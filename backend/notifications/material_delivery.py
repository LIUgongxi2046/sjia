import json
import uuid
from datetime import timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from assessment.provenance import entity_reference
from assessment.qualified_facts import lock_case_permissions
from audit.service import write_audit
from authorization.policy import check_service_grant
from archives.models import MaterialReminder, MaterialReminderNotice
from configurations.evidence import inspect_approved_evidence
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from notifications.availability import notification_unavailability_reason
from notifications.errors import NotificationRejected
from notifications.native import PROVIDER, native_configuration, sign, verify
from notifications.policies import MaterialReminderChannelPolicy
from notifications.reminder_schedule import next_permitted_time
from notifications.reminders import current_scope, scope_binding, target_snapshot
from requests.material_reminders import reminder_receipt, source_retention, authorization_context
from requests.models import WriteIntent
from tasks.models import ConsumerReceipt, Task, TaskAttempt
from tasks.outbox import emit_event, record_receipt


TASK_SCHEMA = 'material-reminder-notification-task-2'
CONSUMER = 'material_reminder_notifications'


def material_configuration(gate, *, lock=False):
    runtime, provider, native, retention = native_configuration(gate, lock=lock)
    if native.material_reminder_policy_id is None:
        return None, 'REMINDER_CHANNEL_POLICY_UNAVAILABLE'
    rows = ConfigVersion.objects.select_for_update() if lock else ConfigVersion.objects
    config = rows.filter(id=native.material_reminder_policy_id,
        kind='notification', schema_version='material-reminder-channel-2').first()
    if config is None:
        return None, 'REMINDER_CHANNEL_POLICY_UNAVAILABLE'
    reason = configuration_qualification_reason(config, gate.environment, audience='support')
    if reason is not None:
        return None, reason
    policy = MaterialReminderChannelPolicy.model_validate_json(json.dumps(config.payload))
    if (policy.environment != gate.environment or policy.runtime_config_key != runtime.config_key
            or policy.provider_config_key != provider.config_key or policy.purpose != native.purpose):
        return None, 'REMINDER_CHANNEL_POLICY_SCOPE_MISMATCH'
    for reference in policy.evidence_refs:
        _, _, reason = inspect_approved_evidence(reference, purpose='material_reminder_policy', lock=lock)
        if reason is not None:
            return None, reason
    return (runtime, provider, native, retention, config, policy), None


def material_unavailability_reason():
    reason = notification_unavailability_reason()
    if reason is not None:
        return reason
    gate = EnvironmentGate.objects.get(environment=settings.SJIA_ENVIRONMENT)
    _, reason = material_configuration(gate)
    return reason


def require_material_channel():
    gate = EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
    configured, reason = material_configuration(gate, lock=True)
    if reason is not None:
        raise NotificationRejected(reason)
    return configured


def current_setting_intent(reminder):
    rows = list(WriteIntent.objects.filter(actor_id=reminder.owner_id, patient_id=reminder.patient_id,
        operation_id__in=['material_reminder_create', 'material_reminder_change'], status='committed',
        write_request__resource_type='material_reminder', write_request__resource_id=reminder.id,
        write_request__expected_version=reminder.version).order_by('id')[:2])
    if len(rows) != 1:
        raise NotificationRejected('REMINDER_RECEIPT_INVALID')
    reminder_receipt(rows[0])
    return rows[0]


def delivery_scope(reminder_id, *, check_due=True):
    gate = EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
    configured, reason = material_configuration(gate, lock=True)
    if reason is not None:
        raise NotificationRejected(reason)
    runtime, provider, native, retention, config, policy = configured
    initial = MaterialReminder.objects.get(id=reminder_id)
    patient = Patient.objects.select_for_update().get(id=initial.patient_id)
    owner = Principal.objects.select_for_update().get(id=initial.owner_id)
    intent = current_setting_intent(initial)
    scope = current_scope(owner=owner, patient=patient, auth_epoch=int(intent.authorization_context['auth_epoch']), lock=True)
    target, state = target_snapshot(scope, initial.target_type, initial.target_ref, initial.target_version, lock=True)
    reminder = MaterialReminder.objects.select_for_update().get(id=initial.id, owner_id=owner.id, patient_id=patient.id)
    if reminder.version != initial.version or reminder.status != 'enabled' or state != 'pending':
        raise NotificationRejected('REMINDER_TARGET_NOT_PENDING')
    target_policy = source_retention(target, reminder.target_type)
    if (target_policy.id != intent.retention_policy_id or target_policy.status != 'active'
            or target_policy.approved_at is None or target_policy.approved_by_id is None
            or not target_policy.evidence_ref or not target_policy.evidence_ref.strip()):
        raise NotificationRejected('REMINDER_RETENTION_UNAVAILABLE')
    executor = Principal.objects.select_for_update().get(id=native.executor_id)
    if executor.account_status != 'active':
        raise NotificationRejected('NOTIFICATION_PRINCIPAL_UNAVAILABLE')
    now = timezone.now()
    role = check_service_grant(executor, 'manage_notifications', gate.environment, now)
    grants = lock_case_permissions(executor, patient, 'read_case', policy.purpose, now, whole_resource=True)
    due = next_permitted_time(reminder.schedule_at, reminder.timezone_name, reminder.quiet_start, reminder.quiet_end)
    permitted_now = next_permitted_time(now, reminder.timezone_name, reminder.quiet_start, reminder.quiet_end)
    if check_due and (due > now or permitted_now > now):
        raise NotificationRejected('REMINDER_NOT_DUE')
    context = {'authorization': authorization_context(scope, reminder, target, target_policy, runtime),
        'native_config': entity_reference(provider), 'reminder_policy': entity_reference(config),
        'executor': entity_reference(executor), 'role': entity_reference(role),
        'sender_grants': [entity_reference(row) for row in grants], 'scope': scope_binding(scope)}
    return reminder, scope, configured, executor, context, due


def checked_notice(notice, *, lock=False, verify_permit=True):
    rows = Task.objects.select_for_update() if lock else Task.objects
    task = rows.get(id=notice.permit_refs['task_id'], patient_id=notice.patient_id,
        task_type='notification', schema_version=TASK_SCHEMA)
    refs = task.input_refs
    reminder = MaterialReminder.objects.get(id=notice.reminder_id, patient_id=notice.patient_id)
    if (compute_checksum(refs) != task.input_sha256 or notice.permit_refs != {
            'schema_version': TASK_SCHEMA, 'task_id': str(task.id), 'input_sha256': task.input_sha256}
            or refs['task_id'] != str(task.id) or refs['notice_id'] != str(notice.id)
            or refs['reminder_id'] != str(notice.reminder_id) or refs['reminder_version'] != str(notice.reminder_version)
            or refs['scheduled_at'] != notice.scheduled_at.isoformat() or refs['channel'] != notice.channel
            or refs['deduplication_key'] != notice.deduplication_key or refs['executor_id'] != str(task.actor_id)
            or refs['patient_id'] != str(notice.patient_id) or refs['owner_id'] != str(reminder.owner_id)
            or task.created_at.isoformat() != refs['authorized_at'] or task.deadline_at.isoformat() != refs['expires_at']):
        raise NotificationRejected('REMINDER_NOTICE_PROOF_MISMATCH')
    if verify_permit or notice.receipt_refs:
        value = verify(refs['permit_proof'], refs['signing_secret_ref'], 'material-reminder-permit')
        if value != {key: item for key, item in refs.items() if key != 'permit_proof'}:
            raise NotificationRejected('REMINDER_NOTICE_PROOF_MISMATCH')
    attempts = TaskAttempt.objects.select_for_update() if lock else TaskAttempt.objects
    completed = list(attempts.filter(task=task, status='completed'))
    if notice.receipt_refs:
        if (len(completed) != 1 or completed[0].provider_request_id != refs['provider_request_id']
                or notice.provider_request_id != refs['provider_request_id']):
            raise NotificationRejected('REMINDER_RECEIPT_PROOF_MISMATCH')
        evidence = completed[0].usage_evidence
        if evidence['provider'] != PROVIDER or evidence['mailbox_sha256'] != compute_checksum(refs['body']):
            raise NotificationRejected('REMINDER_RECEIPT_PROOF_MISMATCH')
        seen = set()
        for receipt in notice.receipt_refs:
            if (receipt['status'] not in ('accepted', 'delivered')
                    or receipt['provider_event_id'] != f'{PROVIDER}:material:{notice.id}:{receipt["status"]}'):
                raise NotificationRejected('REMINDER_RECEIPT_PROOF_MISMATCH')
            token = evidence['receipt_proofs'].get(receipt['receipt_id'])
            if token is None or sha256(token.encode()).hexdigest() != receipt['evidence_sha256'] or receipt['status'] in seen:
                raise NotificationRejected('REMINDER_RECEIPT_PROOF_MISMATCH')
            seen.add(receipt['status'])
            signed = verify(token, refs['signing_secret_ref'], 'material-reminder-receipt')
            expected = {**{key: value for key, value in receipt.items() if key != 'evidence_sha256'},
                'notice_id': str(notice.id), 'provider_request_id': refs['provider_request_id'],
                'delivery_proof': signed.get('delivery_proof')}
            if signed != expected or receipt['provider'] != PROVIDER:
                raise NotificationRejected('REMINDER_RECEIPT_PROOF_MISMATCH')
            if receipt['status'] == 'accepted' and signed['delivery_proof'] is not None:
                raise NotificationRejected('REMINDER_RECEIPT_PROOF_MISMATCH')
            if receipt['status'] == 'delivered':
                ticket = verify(signed['delivery_proof'], refs['signing_secret_ref'], 'material-reminder-delivery')
                instant = parse_datetime(receipt['occurred_at'])
                if (ticket['notice_id'] != str(notice.id) or ticket['owner_id'] != refs['owner_id']
                        or ticket['input_sha256'] != task.input_sha256 or instant is None
                        or not ticket['issued_at'] <= instant.timestamp() <= ticket['expires_at']):
                    raise NotificationRejected('REMINDER_RECEIPT_PROOF_MISMATCH')
        if 'accepted' not in seen or (notice.status == 'delivered' and 'delivered' not in seen):
            raise NotificationRejected('REMINDER_RECEIPT_PROOF_MISMATCH')
        delivered = next((row for row in notice.receipt_refs if row['status'] == 'delivered'), None)
        if (task.status != 'completed' or notice.status not in ('accepted', 'delivered')
                or (notice.delivered_at is None) != (delivered is None)
                or delivered is not None and notice.delivered_at != parse_datetime(delivered['occurred_at'])
                or notice.read_at is not None and (notice.delivered_at is None or notice.read_at < notice.delivered_at)):
            raise NotificationRejected('REMINDER_RECEIPT_PROOF_MISMATCH')
    elif notice.status in ('accepted', 'delivered'):
        raise NotificationRejected('REMINDER_RECEIPT_PROOF_MISMATCH')
    return task


def add_receipt(notice, task, attempt, status, *, delivery_proof=None):
    current = next((row for row in notice.receipt_refs if row['status'] == status), None)
    if current is not None:
        checked_notice(notice, lock=True)
        return current
    now = timezone.now()
    receipt = {'receipt_id': str(uuid.uuid4()), 'provider': PROVIDER,
        'provider_event_id': f'{PROVIDER}:material:{notice.id}:{status}', 'status': status,
        'occurred_at': now.isoformat(), 'received_at': now.isoformat()}
    proof = sign({**receipt, 'notice_id': str(notice.id), 'provider_request_id': task.input_refs['provider_request_id'],
        'delivery_proof': delivery_proof}, task.input_refs['signing_secret_ref'], 'material-reminder-receipt')
    receipt['evidence_sha256'] = sha256(proof.encode()).hexdigest()
    attempt.usage_evidence['receipt_proofs'][receipt['receipt_id']] = proof
    attempt.save(update_fields=['usage_evidence'])
    notice.receipt_refs = [*notice.receipt_refs, receipt]
    notice.save(update_fields=['receipt_refs'])
    return receipt


@transaction.atomic
def authorize_due(reminder_id):
    reminder, scope, configured, executor, context, due = delivery_scope(reminder_id)
    runtime, provider, native, retention, config, policy = configured
    key = compute_checksum({'reminder': str(reminder.id), 'version': str(reminder.version),
        'schedule_at': reminder.schedule_at.isoformat(), 'channel': policy.channel})
    existing = MaterialReminderNotice.objects.select_for_update().filter(deduplication_key=key).first()
    if existing is not None:
        checked_notice(existing, lock=True)
        return existing
    now = timezone.now()
    start = now.astimezone(ZoneInfo(native.timezone)).replace(hour=0, minute=0, second=0, microsecond=0)
    jobs = Task.objects.filter(task_type='notification', input_refs__provider_config_id=str(provider.id))
    task_policy = next(item for item in runtime.payload['tasks'] if item['task_type'] == 'notification')
    if jobs.filter(created_at__gte=start).count() >= native.max_daily_messages:
        raise NotificationRejected('NOTIFICATION_DAILY_BUDGET_EXCEEDED')
    if jobs.filter(status__in=['accepted', 'queued', 'running']).count() >= task_policy['max_concurrency']:
        raise NotificationRejected('NOTIFICATION_CONCURRENCY_BUDGET_EXCEEDED')
    notice_id, task_id = uuid.uuid4(), uuid.uuid4()
    expiry = now + timedelta(seconds=native.permit_seconds)
    refs = {'schema_version': TASK_SCHEMA, 'task_id': str(task_id), 'notice_id': str(notice_id),
        'patient_id': str(scope.patient.id), 'owner_id': str(scope.owner.id), 'executor_id': str(executor.id),
        'reminder_id': str(reminder.id), 'reminder_version': str(reminder.version), 'channel': policy.channel,
        'scheduled_at': due.isoformat(), 'deduplication_key': key, 'provider_config_id': str(provider.id),
        'provider_request_id': f'{PROVIDER}:material:{notice_id}', 'signing_secret_ref': native.signing_secret_ref,
        'retention_policy_id': str(retention.id), 'authorized_at': now.isoformat(), 'expires_at': expiry.isoformat(),
        'body': {'approved_text': policy.notification_text, 'deep_link': f'/account/reminders?reminder={reminder.id}',
            'content_id': str(config.id), 'content_version': str(config.version)}, 'context': context}
    refs['permit_proof'] = sign(refs, native.signing_secret_ref, 'material-reminder-permit')
    if len(json.dumps(refs, separators=(',', ':')).encode()) > runtime.payload['api']['max_json_bytes']:
        raise NotificationRejected('REMINDER_PERMIT_LIMIT_EXCEEDED')
    task = Task.objects.create(id=task_id, patient=scope.patient, actor=executor, task_type='notification',
        schema_version=TASK_SCHEMA, input_refs=refs, input_sha256=compute_checksum(refs), status='accepted',
        status_version=1, created_at=now, deadline_at=expiry)
    notice = MaterialReminderNotice.objects.create(id=notice_id, patient_id=scope.patient.id, reminder_id=reminder.id,
        reminder_version=reminder.version, scheduled_at=due, channel=policy.channel, deduplication_key=key,
        status='pending', permit_refs={'schema_version': TASK_SCHEMA, 'task_id': str(task.id),
            'input_sha256': task.input_sha256}, receipt_refs=[], created_at=now)
    write_audit(actor=executor, patient=scope.patient, action='material_reminder_authorize',
        object_type='material_reminder_notice', object_id=notice.id, object_version=str(reminder.version),
        outcome='authorized', retention_policy=retention, occurred_at=now)
    emit_event(event_type='notification.material_requested', schema_version=TASK_SCHEMA, aggregate_id=task.id,
        aggregate_version=1, correlation_id=task.id, resource_refs={'notice_id': str(notice.id)}, task=task)
    return notice


@transaction.atomic
def submit_notice(notice_id):
    initial = MaterialReminderNotice.objects.get(id=notice_id)
    if initial.status in ('accepted', 'delivered', 'cancelled'):
        EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
        Patient.objects.select_for_update().get(id=initial.patient_id)
        notice = MaterialReminderNotice.objects.select_for_update().get(id=initial.id)
        checked_notice(notice, lock=True)
        return notice
    reminder, scope, configured, executor, context, due = delivery_scope(initial.reminder_id)
    notice = MaterialReminderNotice.objects.select_for_update().get(id=initial.id, patient_id=scope.patient.id)
    task = checked_notice(notice, lock=True)
    now = timezone.now()
    if notice.status == 'uncertain' or task.status not in ('accepted', 'queued', 'failed'):
        raise NotificationRejected('REMINDER_ORIGINAL_RESULT_REQUIRED')
    expiry = parse_datetime(task.input_refs['expires_at'])
    if (expiry is None or expiry <= now or context != task.input_refs['context']
            or notice.reminder_version != reminder.version or notice.scheduled_at != due):
        raise NotificationRejected('REMINDER_SEND_AUTHORIZATION_CHANGED')
    previous = list(TaskAttempt.objects.select_for_update().filter(task=task).order_by('attempt_number'))
    if any(row.status != 'failed' or row.provider_request_id != task.input_refs['provider_request_id'] for row in previous):
        raise NotificationRejected('REMINDER_ORIGINAL_RESULT_REQUIRED')
    task_policy = next(item for item in configured[0].payload['tasks'] if item['task_type'] == 'notification')
    if len(previous) >= task_policy['max_attempts'] or previous and (
            previous[-1].finished_at is None or now < previous[-1].finished_at + timedelta(
                seconds=task_policy['retry_delays_seconds'][len(previous) - 1])):
        raise NotificationRejected('REMINDER_RETRY_UNAVAILABLE')
    attempt = TaskAttempt.objects.create(task=task, attempt_number=len(previous) + 1, lease_generation=0,
        provider_request_id=task.input_refs['provider_request_id'], started_at=now, finished_at=now, status='completed',
        usage_evidence={'provider': PROVIDER, 'mailbox_sha256': compute_checksum(task.input_refs['body']), 'receipt_proofs': {}})
    notice.provider_request_id, notice.status = task.input_refs['provider_request_id'], 'accepted'
    notice.save(update_fields=['provider_request_id', 'status'])
    add_receipt(notice, task, attempt, 'accepted')
    task.status, task.status_version = 'completed', task.status_version + 1
    task.save(update_fields=['status', 'status_version'])
    write_audit(actor=executor, patient=scope.patient, action='material_reminder_accept',
        object_type='material_reminder_notice', object_id=notice.id, object_version=str(notice.reminder_version),
        outcome='accepted', retention_policy=configured[3], occurred_at=now)
    return notice


@transaction.atomic
def consume_material(event):
    if (event.event_type != 'notification.material_requested' or event.schema_version != TASK_SCHEMA
            or event.task_id is None or event.resource_refs.keys() != {'notice_id'}):
        raise NotificationRejected('REMINDER_EVENT_SCOPE_MISMATCH')
    EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
    if ConsumerReceipt.objects.filter(consumer=CONSUMER, event=event).exists():
        return
    notice = MaterialReminderNotice.objects.get(id=event.resource_refs['notice_id'])
    task = checked_notice(notice)
    if task.id != event.task_id or task.id != event.aggregate_id or event.aggregate_version != 1:
        raise NotificationRejected('REMINDER_EVENT_SCOPE_MISMATCH')
    submit_notice(notice.id)
    record_receipt(CONSUMER, event, notice.id)


def owner_notice_scope(*, actor, epoch, notice_id, lock=False):
    from notifications.reminders import scoped_reminders

    initial = MaterialReminderNotice.objects.filter(id=notice_id).first()
    if initial is None:
        raise NotificationRejected('REMINDER_NOTICE_NOT_FOUND')
    scope = current_scope(owner=actor, patient=Patient.objects.get(id=initial.patient_id), auth_epoch=epoch, lock=lock)
    if not scoped_reminders(scope).filter(id=initial.reminder_id).exists():
        raise NotificationRejected('REMINDER_NOTICE_NOT_FOUND')
    rows = MaterialReminderNotice.objects.select_for_update() if lock else MaterialReminderNotice.objects
    return scope, rows.get(id=initial.id, patient_id=scope.patient.id)


def notice_view(notice, scope):
    reminder = MaterialReminder.objects.get(id=notice.reminder_id, owner_id=scope.owner.id, patient_id=scope.patient.id)
    checked_notice(notice, verify_permit=False)
    _, state = target_snapshot(scope, reminder.target_type, reminder.target_ref, reminder.target_version)
    value = {'notice_id': str(notice.id), 'patient_id': str(notice.patient_id), 'reminder_id': str(reminder.id),
        'reminder_version': str(notice.reminder_version), 'target_type': reminder.target_type,
        'target_id': str(reminder.target_ref), 'target_state': state, 'status': notice.status,
        'channel': notice.channel, 'scheduled_at': notice.scheduled_at.isoformat(), 'created_at': notice.created_at.isoformat(),
        'delivered_at': notice.delivered_at.isoformat() if notice.delivered_at else None,
        'read_at': notice.read_at.isoformat() if notice.read_at else None, 'receipts': notice.receipt_refs,
        'current_setting': notice.reminder_version == reminder.version and reminder.status == 'enabled' and state == 'pending'}
    return {**value, 'snapshot_version': compute_checksum(value)}


def notice_list(*, scope, limit, expires_at, reminder_id=None, cursor=None):
    from django.core import signing
    from notifications.reminders import scoped_reminders

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or expires_at <= timezone.now():
        raise NotificationRejected('REMINDER_QUERY_INVALID')
    reminders = scoped_reminders(scope)
    if reminder_id is not None:
        reminders = reminders.filter(id=reminder_id)
        if not reminders.exists():
            raise NotificationRejected('REMINDER_NOT_FOUND')
    rows = MaterialReminderNotice.objects.filter(patient_id=scope.patient.id, reminder_id__in=reminders.values('id')).order_by('-created_at', 'id')
    values = [notice_view(row, scope) for row in rows]
    bound = {**scope_binding(scope), 'items_sha256': compute_checksum({'items': values}),
        'reminder_id': str(reminder_id) if reminder_id else None, 'limit': limit}
    offset, expiry = 0, expires_at.timestamp()
    if cursor is not None:
        saved = signing.loads(cursor, salt='material-reminder-notice-list')
        if saved['scope'] != bound or saved['expires_at'] <= timezone.now().timestamp():
            raise NotificationRejected('CURSOR_STALE')
        offset, expiry = saved['offset'], saved['expires_at']
    following = offset + limit
    return {'items': values[offset:following], 'snapshot_version': compute_checksum(bound),
        'next_cursor': signing.dumps({'scope': bound, 'offset': following, 'expires_at': expiry},
            salt='material-reminder-notice-list') if following < len(values) else None}


@transaction.atomic
def notice_message(*, actor, epoch, notice_id, session_expires_at):
    scope, notice = owner_notice_scope(actor=actor, epoch=epoch, notice_id=notice_id, lock=True)
    if notice.status not in ('accepted', 'delivered'):
        raise NotificationRejected('REMINDER_MESSAGE_UNAVAILABLE')
    reminder, current, configured, _, context, _ = delivery_scope(notice.reminder_id, check_due=False)
    task = checked_notice(notice, lock=True)
    if scope.owner.id != current.owner.id or notice.reminder_version != reminder.version or task.input_refs['context'] != context:
        raise NotificationRejected('REMINDER_SEND_AUTHORIZATION_CHANGED')
    now = timezone.now()
    expiry = min(session_expires_at, now + timedelta(seconds=configured[2].delivery_ticket_seconds))
    if expiry <= now:
        raise NotificationRejected('REMINDER_MESSAGE_UNAVAILABLE')
    ticket = sign({'notice_id': str(notice.id), 'owner_id': str(scope.owner.id),
        'auth_epoch': str(scope.owner.auth_epoch), 'scope_sha256': compute_checksum(scope_binding(scope)),
        'input_sha256': task.input_sha256, 'issued_at': now.timestamp(), 'expires_at': expiry.timestamp()},
        task.input_refs['signing_secret_ref'], 'material-reminder-delivery')
    return {'notice_id': str(notice.id), 'reminder_id': str(reminder.id), 'patient_id': str(scope.patient.id),
        **task.input_refs['body'], 'delivery_ticket': ticket, 'expires_at': expiry.isoformat()}


@transaction.atomic
def acknowledge_notice(*, actor, epoch, notice_id, ticket, session_expires_at):
    scope, notice = owner_notice_scope(actor=actor, epoch=epoch, notice_id=notice_id, lock=True)
    reminder, _, configured, _, context, _ = delivery_scope(notice.reminder_id, check_due=False)
    task = checked_notice(notice, lock=True)
    value = verify(ticket, task.input_refs['signing_secret_ref'], 'material-reminder-delivery')
    now = timezone.now()
    if (notice.status not in ('accepted', 'delivered') or value['notice_id'] != str(notice.id)
            or value['owner_id'] != str(scope.owner.id) or value['auth_epoch'] != str(scope.owner.auth_epoch)
            or value['scope_sha256'] != compute_checksum(scope_binding(scope)) or value['input_sha256'] != task.input_sha256
            or not value['issued_at'] <= now.timestamp() <= value['expires_at']
            or value['expires_at'] > session_expires_at.timestamp()
            or notice.reminder_version != reminder.version or task.input_refs['context'] != context):
        raise NotificationRejected('REMINDER_DELIVERY_TICKET_INVALID')
    if notice.delivered_at is None:
        attempt = TaskAttempt.objects.select_for_update().get(task=task, status='completed')
        receipt = add_receipt(notice, task, attempt, 'delivered', delivery_proof=ticket)
        notice.status, notice.delivered_at = 'delivered', parse_datetime(receipt['occurred_at'])
        notice.save(update_fields=['status', 'delivered_at'])
        write_audit(actor=actor, patient=scope.patient, action='material_reminder_deliver',
            object_type='material_reminder_notice', object_id=notice.id, object_version=str(notice.reminder_version),
            outcome='delivered', retention_policy=configured[3], occurred_at=notice.delivered_at)
    return notice_view(notice, scope)


@transaction.atomic
def mark_notice_read(*, actor, epoch, notice_id):
    scope, notice = owner_notice_scope(actor=actor, epoch=epoch, notice_id=notice_id, lock=True)
    task = checked_notice(notice, lock=True)
    if notice.delivered_at is None or notice.status != 'delivered':
        raise NotificationRejected('REMINDER_NOT_DELIVERED')
    if notice.read_at is None:
        from retention.models import RetentionPolicy

        policy = RetentionPolicy.objects.select_for_update().get(id=task.input_refs['retention_policy_id'])
        if policy.status != 'active':
            raise NotificationRejected('REMINDER_RETENTION_UNAVAILABLE')
        notice.read_at = timezone.now()
        notice.save(update_fields=['read_at'])
        write_audit(actor=actor, patient=scope.patient, action='material_reminder_read',
            object_type='material_reminder_notice', object_id=notice.id, object_version=str(notice.reminder_version),
            outcome='read', retention_policy=policy, occurred_at=notice.read_at)
    return notice_view(notice, scope)


@transaction.atomic
def retry_original_notice(*, actor, epoch, notice_id):
    scope, notice = owner_notice_scope(actor=actor, epoch=epoch, notice_id=notice_id, lock=True)
    checked_notice(notice, lock=True)
    if notice.status not in ('pending', 'failed', 'accepted', 'delivered'):
        raise NotificationRejected('REMINDER_ORIGINAL_RESULT_REQUIRED')
    return notice_view(submit_notice(notice.id), scope)


@transaction.atomic
def record_material_failure(event, error_code):
    if (event.event_type != 'notification.material_requested' or event.schema_version != TASK_SCHEMA
            or event.task_id is None or event.resource_refs.keys() != {'notice_id'}):
        raise NotificationRejected('REMINDER_EVENT_SCOPE_MISMATCH')
    EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
    task = Task.objects.get(id=event.task_id, schema_version=TASK_SCHEMA)
    if task.id != event.aggregate_id or event.aggregate_version != 1 or task.input_refs['notice_id'] != event.resource_refs['notice_id']:
        raise NotificationRejected('REMINDER_EVENT_SCOPE_MISMATCH')
    Patient.objects.select_for_update().get(id=task.patient_id)
    notice = MaterialReminderNotice.objects.select_for_update().get(id=task.input_refs['notice_id'], patient_id=task.patient_id)
    task = Task.objects.select_for_update().get(id=task.id)
    if notice.status not in ('pending', 'failed') or task.status not in ('accepted', 'queued', 'failed'):
        return
    if notice.receipt_refs or notice.provider_request_id is not None or TaskAttempt.objects.filter(task=task, status__in=['running', 'completed', 'uncertain']).exists():
        raise NotificationRejected('REMINDER_ORIGINAL_RESULT_REQUIRED')
    previous = TaskAttempt.objects.filter(task=task).order_by('-attempt_number').first()
    if previous is not None and task.status == 'failed':
        return
    now = timezone.now()
    task.status, task.status_version = 'failed', task.status_version + 1
    task.save(update_fields=['status', 'status_version'])
    notice.status = 'failed'
    notice.save(update_fields=['status'])
    TaskAttempt.objects.create(task=task, attempt_number=previous.attempt_number + 1 if previous else 1,
        lease_generation=0, provider_request_id=task.input_refs['provider_request_id'], started_at=now,
        finished_at=now, status='failed', error_code=error_code,
        usage_evidence={'event_id': str(event.id), 'error_code': error_code})
