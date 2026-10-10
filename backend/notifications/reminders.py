from dataclasses import dataclass

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from archives.models import ArchiveBatch, MaterialReminder, MaterialReminderNotice, PersonalRecordTask
from authorization.models import AccessGrant
from configurations.service import compute_checksum
from drafts.models import Draft
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from notifications.errors import NotificationRejected
from notifications.reminder_schemas import checked_quiet_hours, checked_schedule, checked_version, reminder_input
from notifications.reminder_schedule import next_permitted_time, reminder_next_due_at
from relations.models import Relation


@dataclass(frozen=True)
class ReminderScope:
    owner: Principal
    patient: Patient
    gate: EnvironmentGate
    grants: tuple
    relations: tuple


def current_scope(*, owner, patient, auth_epoch, lock=False):
    if auth_epoch is None:
        raise NotificationRejected('REMINDER_SCOPE_DENIED')
    scope, reason = inspect_scope(owner=owner, patient=patient, auth_epoch=auth_epoch, lock=lock)
    if reason is not None:
        raise NotificationRejected(reason)
    return scope


def inspect_scope(*, owner, patient, auth_epoch=None, lock=False):
    gates, patients, people = EnvironmentGate.objects, Patient.objects, Principal.objects
    relations, grants = Relation.objects, AccessGrant.objects
    if lock:
        gates, patients, people = gates.select_for_update(), patients.select_for_update(), people.select_for_update()
        relations, grants = relations.select_for_update(), grants.select_for_update()
    gate = gates.filter(environment=settings.SJIA_ENVIRONMENT).first()
    if gate is None or not gate.enabled:
        return None, 'REMINDER_ENVIRONMENT_UNAVAILABLE'
    patient = patients.get(id=patient.id)
    owner = people.get(id=owner.id)
    if (patient.status != 'active' or owner.account_status != 'active'
            or (auth_epoch is not None and owner.auth_epoch != auth_epoch)):
        return ReminderScope(owner, patient, gate, (), ()), 'REMINDER_SCOPE_DENIED'
    now = timezone.now()
    relations = tuple(relations.filter(patient=patient, guardian=owner, status='active', valid_from__lte=now)
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now))
        .filter(Q(revoked_at__isnull=True) | Q(revoked_at__gt=now)).order_by('id'))
    grants = tuple(grants.filter(patient=patient, principal=owner, action='manage_draft',
        purpose='family_observation', relation_id__in=[row.id for row in relations], valid_from__lte=now)
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now))
        .filter(Q(revoked_at__isnull=True) | Q(revoked_at__gt=now)).exclude(evidence_ref='').order_by('id'))
    grants = tuple(grant for grant in grants if grant.evidence_ref.strip())
    scope = ReminderScope(owner, patient, gate, grants, relations)
    return scope, None if grants else 'REMINDER_SCOPE_DENIED'


def draft_fields(draft):
    payload = draft.payload
    if not isinstance(payload, dict):
        return None
    keys = {'observation': {'temp', 'tempUnit', 'description', 'measuredTime', 'observedTime', 'method', 'symptoms', 'voiceApplied'},
        'report': {'candidates', 'photos', 'organization', 'reportRef', 'sampleTime', 'resultTime', 'reportStatus'},
        'therapy': {'medication', 'status', 'note', 'startTime', 'endTime', 'photos', 'source', 'sourceReference', 'candidates', 'selectedSource'},
        'correction': {'fields', 'kind', 'reason', 'recordId', 'version'}}
    if draft.form_type not in keys or set(payload) - keys[draft.form_type]:
        return None
    if draft.form_type == 'observation':
        symptoms = payload.get('symptoms', {})
        if not isinstance(symptoms, dict):
            return None
        fields = set(symptoms)
        if payload.get('temp') not in (None, ''):
            fields.add('temp')
        if payload.get('description') not in (None, ''):
            fields.add('observation_note')
        return fields
    if draft.form_type == 'report':
        return {'report_item'}
    if draft.form_type == 'therapy':
        return {'medication_name', 'therapy_status', 'therapy_source', 'therapy_note', 'therapy_start', 'therapy_end'}
    if draft.form_type == 'correction':
        fields = payload.get('fields')
        if not isinstance(fields, list) or any(not isinstance(item, dict)
                or not isinstance(item.get('field_code'), str) for item in fields):
            return None
        return {item['field_code'] for item in fields}
    return None


def draft_accessible(scope, draft):
    fields = draft_fields(draft)
    if fields is None:
        return False
    for grant in scope.grants:
        if grant.record_scope == 'assigned' and (grant.assigned_record_id is None
                or grant.assigned_record_id != draft.correction_record_id):
            continue
        if grant.record_scope not in ('own', 'all', 'assigned'):
            continue
        if fields <= set(grant.field_codes) or not grant.field_codes:
            return True
    return False


def target_queries(scope, *, lock=False):
    drafts = Draft.objects.filter(owner=scope.owner, patient=scope.patient)
    batches = ArchiveBatch.objects.filter(submitted_by=scope.owner.id, patient_id=scope.patient.id)
    identifiers = [row.id for row in drafts.only('id', 'payload', 'form_type', 'correction_record_id').iterator()
        if draft_accessible(scope, row)]
    drafts = drafts.filter(id__in=identifiers)
    if not any(grant.record_scope in ('all', 'own') and not grant.field_codes for grant in scope.grants):
        batches = batches.none()
    if lock:
        drafts, batches = drafts.select_for_update(), batches.select_for_update()
    return drafts, batches


def record_task_query(scope, *, lock=False):
    rows = PersonalRecordTask.objects.filter(owner_id=scope.owner.id, patient_id=scope.patient.id)
    if not any(grant.record_scope in ('all', 'own') for grant in scope.grants):
        rows = rows.none()
    return rows.select_for_update() if lock else rows


def target_snapshot(scope, target_type, target_id, expected_version=None, *, lock=False):
    target, state = inspect_target(scope, target_type, target_id, expected_version, lock=lock)
    if state == 'unsupported':
        raise NotificationRejected('REMINDER_TARGET_TYPE_UNAVAILABLE')
    if target is None:
        raise NotificationRejected('REMINDER_TARGET_UNAVAILABLE')
    return target, state


def inspect_target(scope, target_type, target_id, expected_version=None, *, lock=False):
    drafts, batches = target_queries(scope, lock=lock)
    if target_type == 'draft':
        target = drafts.filter(id=target_id).first()
        if target is None or not draft_accessible(scope, target):
            return None, 'unavailable'
    elif target_type == 'batch':
        target = batches.filter(id=target_id).first()
        if target is None:
            return None, 'unavailable'
    elif target_type == 'record_task':
        target = record_task_query(scope, lock=lock).select_related('retention_policy').filter(id=target_id).first()
        if target is None:
            return None, 'unavailable'
    else:
        return None, 'unsupported'
    state = target_current_state(target, target_type, expected_version)
    return (None, 'unavailable') if state is None else (target, state)


def target_current_state(target, target_type, expected_version=None):
    if target_type == 'draft':
        if target.status == 'submitted':
            state = 'completed'
        elif target.status in ('abandoned', 'expired') or (target.expires_at and target.expires_at <= timezone.now()):
            state = 'cancelled'
        elif target.status == 'editing':
            state = 'pending'
        else:
            return None
        if target.retention_policy.status != 'active':
            state = 'unavailable'
    elif target_type == 'batch':
        state = {'archived': 'completed', 'cancelled': 'cancelled', 'receiving': 'pending',
            'processing': 'pending', 'awaiting_confirmation': 'pending', 'partial': 'pending',
            'failed': 'pending'}.get(target.status)
        if state is None:
            return None
    elif target_type == 'record_task':
        from notifications.personal_record_tasks import approved_retention

        state = {'pending': 'pending', 'completed': 'completed', 'cancelled': 'cancelled'}.get(target.status)
        if state is None:
            return None
        if not approved_retention(target.retention_policy):
            state = 'unavailable'
    else:
        raise NotificationRejected('REMINDER_TARGET_TYPE_UNAVAILABLE')
    if state == 'pending' and expected_version is not None and target.version != expected_version:
        state = 'changed'
    return state


def scope_binding(scope):
    return {'owner': str(scope.owner.id), 'auth_epoch': str(scope.owner.auth_epoch),
        'patient': str(scope.patient.id), 'data_epoch': str(scope.patient.data_epoch), 'gate_version': str(scope.gate.version),
        'runtime_config_id': str(scope.gate.runtime_config_id),
        'grants': [{'id': str(grant.id), 'scope': grant.record_scope, 'record': str(grant.assigned_record_id),
            'fields': sorted(grant.field_codes), 'relation': str(grant.relation_id),
            'valid_from': grant.valid_from.isoformat(), 'valid_until': grant.valid_until.isoformat() if grant.valid_until else None,
            'revoked_at': grant.revoked_at.isoformat() if grant.revoked_at else None} for grant in scope.grants],
        'relations': [[str(row.id), row.version, row.auth_epoch,
            row.valid_until.isoformat() if row.valid_until else None,
            row.revoked_at.isoformat() if row.revoked_at else None] for row in scope.relations]}


def reminder_target_list(*, scope, target_type, limit, expires_at, cursor=None):
    if target_type not in ('draft', 'batch', 'record_task') or isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise NotificationRejected('REMINDER_QUERY_INVALID')
    now = timezone.now()
    if expires_at <= now:
        raise NotificationRejected('CURSOR_STALE')
    items = []
    reason = None
    if target_type == 'record_task' and not any(grant.record_scope in ('all', 'own') for grant in scope.grants):
        reason = 'REMINDER_SCOPE_DENIED'
    if target_type == 'batch' and not any(grant.record_scope in ('all', 'own')
            and not grant.field_codes for grant in scope.grants):
        reason = 'REMINDER_SCOPE_DENIED'
    if reason is None:
        drafts, batches = target_queries(scope)
        if target_type == 'draft':
            rows = drafts.select_related('retention_policy').order_by('-saved_at', 'id')
        elif target_type == 'batch':
            rows = batches.order_by('-received_at', 'id')
        else:
            rows = record_task_query(scope).select_related('retention_policy').order_by('-updated_at', 'id')
        for target in rows:
            state = target_current_state(target, target_type)
            if state is None:
                raise NotificationRejected('REMINDER_TARGET_UNAVAILABLE')
            items.append({'target_id': str(target.id), 'patient_id': str(scope.patient.id), 'target_type': target_type,
                'target_version': str(target.version), 'target_state': state,
                'saved_at': (target.saved_at if target_type == 'draft' else
                    target.updated_at if target_type == 'record_task' else target.received_at).isoformat(),
                'form_type': target.form_type if target_type == 'draft' else None})
    bound = {**scope_binding(scope), 'target_type': target_type, 'items_sha256': compute_checksum({'items': items}),
        'limit': limit, 'capability_reason': reason}
    offset, expiry = 0, expires_at.timestamp()
    if cursor is not None:
        saved = signing.loads(cursor, salt='material-reminder-target-list')
        if saved['scope'] != bound or saved['expires_at'] <= now.timestamp():
            raise NotificationRejected('CURSOR_STALE')
        offset, expiry = saved['offset'], saved['expires_at']
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise NotificationRejected('REMINDER_QUERY_INVALID')
    return {'items': items[offset:offset + limit], 'snapshot_version': compute_checksum(bound),
        'next_cursor': signing.dumps({'scope': bound, 'offset': offset + limit, 'expires_at': expiry},
            salt='material-reminder-target-list') if len(items) > offset + limit else None,
        'capability_state': 'unavailable' if reason else 'available', 'capability_reason': reason}


def reminder_transition(status, target_state, scope_reason=None):
    if status not in ('disabled', 'enabled', 'paused', 'completed', 'cancelled'):
        raise NotificationRejected('REMINDER_STATE_CONFLICT')
    if status in ('completed', 'cancelled') or scope_reason == 'REMINDER_ENVIRONMENT_UNAVAILABLE':
        return status
    if scope_reason == 'REMINDER_SCOPE_DENIED':
        return 'cancelled'
    if scope_reason is not None:
        raise NotificationRejected(scope_reason)
    if target_state in ('completed', 'cancelled'):
        return target_state
    if target_state in ('changed', 'unavailable', 'unsupported'):
        return 'paused' if status == 'enabled' else status
    if target_state != 'pending':
        raise NotificationRejected('REMINDER_TARGET_UNAVAILABLE')
    return status


def scoped_reminders(scope):
    drafts, batches = target_queries(scope)
    allowed = Q(target_type='draft', target_ref__in=drafts.values('id')) | Q(target_type='batch', target_ref__in=batches.values('id'))
    allowed |= Q(target_type='record_task', target_ref__in=record_task_query(scope).values('id'))
    return MaterialReminder.objects.filter(owner_id=scope.owner.id, patient_id=scope.patient.id).filter(allowed)


def reminder_view(*, reminder, scope):
    from notifications.material_delivery import material_unavailability_reason

    return reminder_projection(reminder=reminder, scope=scope, channel_reason=material_unavailability_reason())


def reminder_projection(*, reminder, scope, channel_reason):
    from notifications.material_delivery import checked_notice

    current = scoped_reminders(scope).filter(id=reminder.id).first()
    if current is None:
        raise NotificationRejected('REMINDER_NOT_FOUND')
    _, target_state = target_snapshot(scope, current.target_type, current.target_ref, current.target_version)
    notice = (MaterialReminderNotice.objects.filter(reminder_id=current.id, patient_id=scope.patient.id)
        .order_by('-created_at', '-id').first())
    if notice is not None:
        checked_notice(notice, verify_permit=False)
    reason = channel_reason
    actions = []
    if current.status in ('disabled', 'enabled', 'paused'):
        if current.status != 'disabled':
            actions.append('disable')
        if current.status == 'enabled':
            actions.append('pause')
        if target_state in ('pending', 'changed'):
            actions.append('configure')
        if target_state == 'pending' and reason is None:
            if current.status in ('disabled', 'paused'):
                actions.append('enable')
            if current.status in ('enabled', 'paused'):
                actions.append('snooze')
    has_slot = MaterialReminderNotice.objects.filter(reminder_id=current.id,
        patient_id=current.patient_id, reminder_version=current.version).exists()
    next_due = None if has_slot else reminder_next_due_at(current, target_state, now=timezone.now())
    return {'reminder_id': str(current.id), 'patient_id': str(current.patient_id), 'version': str(current.version),
        'target_type': current.target_type, 'target_id': str(current.target_ref),
        'target_version': str(current.target_version), 'target_state': target_state, 'status': current.status,
        'timezone': current.timezone_name, 'quiet_start': current.quiet_start.isoformat() if current.quiet_start else None,
        'quiet_end': current.quiet_end.isoformat() if current.quiet_end else None,
        'channel': 'in_app' if reason is None else None,
        'channel_state': 'available' if reason is None else 'unavailable', 'channel_reason': reason,
        'due_at': current.schedule_at.isoformat() if current.schedule_at else None,
        'next_due_at': next_due.isoformat() if next_due else None,
        'last_notice_status': notice.status if notice else 'none', 'allowed_actions': actions}


def reminder_list(*, scope, limit, expires_at, cursor=None):
    from notifications.material_delivery import material_unavailability_reason

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise NotificationRejected('REMINDER_QUERY_INVALID')
    rows = scoped_reminders(scope)
    now = timezone.now()
    if expires_at <= now:
        raise NotificationRejected('CURSOR_STALE')
    summary = rows.aggregate(count=Count('id'), versions=Sum('version'), latest=Max('updated_at'))
    channel_reason = material_unavailability_reason()
    notices = MaterialReminderNotice.objects.filter(patient_id=scope.patient.id, reminder_id__in=rows.values('id'))
    notice_summary = list(notices.values('status').annotate(count=Count('id'), latest=Max('created_at'),
        delivered=Max('delivered_at'), read=Max('read_at')).order_by('status'))
    targets = []
    for row in rows.order_by('id'):
        source, state = target_snapshot(scope, row.target_type, row.target_ref, row.target_version)
        targets.append({'reminder_id': str(row.id), 'target_id': str(source.id),
            'target_version': str(source.version), 'target_state': state})
    bound = {**scope_binding(scope), 'targets_sha256': compute_checksum({'targets': targets}),
        'channel_reason': channel_reason,
        'count': summary['count'], 'versions': summary['versions'],
        'latest': summary['latest'].isoformat() if summary['latest'] else None,
        'notices': [{key: value.isoformat() if hasattr(value, 'isoformat') else value for key, value in row.items()}
            for row in notice_summary], 'limit': limit}
    offset, expiry = 0, expires_at.timestamp()
    if cursor is not None:
        saved = signing.loads(cursor, salt='material-reminder-list')
        if saved['scope'] != bound or saved['expires_at'] <= now.timestamp():
            raise NotificationRejected('CURSOR_STALE')
        offset, expiry = saved['offset'], saved['expires_at']
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise NotificationRejected('REMINDER_QUERY_INVALID')
    selected = list(rows.order_by('-updated_at', 'id')[offset:offset + limit + 1])
    return {'items': [reminder_projection(reminder=row, scope=scope, channel_reason=channel_reason) for row in selected[:limit]],
        'snapshot_version': compute_checksum(bound),
        'next_cursor': signing.dumps({'scope': bound, 'offset': offset + limit, 'expires_at': expiry},
            salt='material-reminder-list') if len(selected) > limit else None}


@transaction.atomic
def save_reminder(*, owner, patient, auth_epoch, payload, reminder_id=None):
    from notifications.material_delivery import require_material_channel

    scope = current_scope(owner=owner, patient=patient, auth_epoch=auth_epoch, lock=True)
    now = timezone.now()
    value = reminder_input(payload, change=reminder_id is not None, now=now)
    if reminder_id is None:
        target, state = target_snapshot(scope, value.target_type, value.target_id,
            checked_version(value.target_version), lock=True)
        if state != 'pending':
            raise NotificationRejected('REMINDER_TARGET_NOT_PENDING')
        if MaterialReminder.objects.filter(patient_id=patient.id, owner_id=owner.id, target_type=value.target_type,
                target_ref=target.id, status__in=['disabled', 'enabled', 'paused']).exists():
            raise NotificationRejected('REMINDER_ALREADY_EXISTS')
        first, last = checked_quiet_hours(value.timezone, value.quiet_start, value.quiet_end)
        if value.enabled:
            require_material_channel()
        return MaterialReminder.objects.create(patient_id=patient.id, owner_id=owner.id, version=1,
            target_type=value.target_type, target_ref=target.id, target_version=target.version,
            schedule_at=value.due_at, timezone_name=value.timezone, quiet_start=first, quiet_end=last,
            status='enabled' if value.enabled else 'disabled', created_at=now, updated_at=now)
    reminder = scoped_reminders(scope).select_for_update().filter(id=reminder_id).first()
    if reminder is None:
        raise NotificationRejected('REMINDER_NOT_FOUND')
    if reminder.version != checked_version(value.expected_version) or reminder.version == 9223372036854775807:
        raise NotificationRejected('VERSION_CONFLICT')
    if reminder.status not in ('disabled', 'enabled', 'paused'):
        raise NotificationRejected('REMINDER_STATE_CONFLICT')
    if value.action in ('disable', 'pause'):
        if value.action == 'pause' and reminder.status != 'enabled':
            raise NotificationRejected('REMINDER_STATE_CONFLICT')
        reminder.status = 'disabled' if value.action == 'disable' else 'paused'
    else:
        expected = checked_version(value.target_version) if value.action == 'configure' else reminder.target_version
        target, state = target_snapshot(scope, reminder.target_type, reminder.target_ref, expected, lock=True)
        if state != 'pending':
            raise NotificationRejected('REMINDER_TARGET_NOT_PENDING')
        if value.action == 'configure':
            reminder.timezone_name = value.timezone
            reminder.quiet_start, reminder.quiet_end = checked_quiet_hours(value.timezone, value.quiet_start, value.quiet_end)
            reminder.target_version = target.version
            reminder.schedule_at = value.due_at
            if value.enabled is not None:
                reminder.status = 'enabled' if value.enabled else 'disabled'
            if reminder.status == 'enabled':
                require_material_channel()
        else:
            if ((value.action == 'enable' and reminder.status not in ('disabled', 'paused'))
                    or (value.action == 'snooze' and reminder.status not in ('enabled', 'paused'))):
                raise NotificationRejected('REMINDER_STATE_CONFLICT')
            checked_schedule(value.due_at or reminder.schedule_at, now)
            require_material_channel()
            reminder.status = 'enabled'
            if value.due_at is not None:
                reminder.schedule_at = value.due_at
    reminder.version += 1
    reminder.updated_at = now
    reminder.save(update_fields=['status', 'version', 'updated_at', 'schedule_at', 'target_version',
        'timezone_name', 'quiet_start', 'quiet_end'])
    return reminder
