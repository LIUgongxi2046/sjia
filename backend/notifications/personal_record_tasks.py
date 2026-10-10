import json
from pathlib import Path

from django.core import signing
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Resource

from api.base import ApiError
from archives.inputs import REGISTRY
from archives.models import MaterialReminder, PersonalRecordTask
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from notifications.reminder_schemas import checked_version
from notifications.reminders import scope_binding
from retention.models import RetentionPolicy


SCHEMA_PATH = Path(__file__).resolve().parents[2] / 'backend-design/personal-record-task.schema.json'
SCHEMA = json.loads(SCHEMA_PATH.read_text())
SCHEMA['$id'] = SCHEMA_PATH.as_uri()
REGISTRY = REGISTRY.with_resource(SCHEMA['$id'], Resource.from_contents(SCHEMA))
VALIDATORS = {name: Draft202012Validator({**SCHEMA, '$ref': f'#/$defs/{name}'},
    registry=REGISTRY, format_checker=FormatChecker()) for name in SCHEMA['$defs']}


def task_input(payload, *, change=False):
    name = 'ChangeInput' if change else 'CreateInput'
    if next(VALIDATORS[name].iter_errors(payload), None) is not None:
        raise ApiError('PERSONAL_RECORD_TASK_INPUT_INVALID', 422)
    if change:
        checked_version(payload['expected_version'])
    elif not payload['title'].strip() or payload['description'] is not None and not payload['description'].strip():
        raise ApiError('PERSONAL_RECORD_TASK_INPUT_INVALID', 422)
    return payload


def require_personal_scope(scope):
    if not any(grant.record_scope in ('all', 'own') for grant in scope.grants):
        raise ApiError('PERSONAL_RECORD_TASK_SCOPE_DENIED', 403)


def personal_tasks(scope, *, lock=False):
    require_personal_scope(scope)
    rows = PersonalRecordTask.objects.filter(owner_id=scope.owner.id, patient_id=scope.patient.id)
    return rows.select_for_update() if lock else rows


def task_runtime(scope):
    runtime = ConfigVersion.objects.filter(id=scope.gate.runtime_config_id,
        kind='provider', schema_version='backend-runtime-1').first()
    if runtime is None or configuration_qualification_reason(runtime, scope.gate.environment, audience='support'):
        raise ApiError('RUNTIME_CONFIGURATION_PENDING', 503)
    return runtime


def approved_retention(policy):
    return (policy.status == 'active' and policy.data_class == 'personal_record_task'
        and policy.approved_at is not None and policy.approved_by_id is not None
        and isinstance(policy.evidence_ref, str) and bool(policy.evidence_ref.strip()))


def task_retention(scope, policy_id, *, lock=False):
    runtime = task_runtime(scope)
    rows = RetentionPolicy.objects.select_for_update() if lock else RetentionPolicy.objects
    policy = rows.filter(id=policy_id).first()
    if (policy is None or not approved_retention(policy)
            or str(policy.id) not in runtime.payload['retention_policy_ids']):
        raise ApiError('PERSONAL_RECORD_TASK_RETENTION_UNAVAILABLE', 409)
    return policy, runtime


def retention_options(scope):
    require_personal_scope(scope)
    runtime = task_runtime(scope)
    policies = RetentionPolicy.objects.filter(id__in=runtime.payload['retention_policy_ids'],
        data_class='personal_record_task').order_by('policy_key', 'version', 'id')
    items = [{'policy_id': str(row.id), 'policy_key': row.policy_key, 'version': str(row.version)}
        for row in policies if approved_retention(row)]
    return {'items': items, 'capability_state': 'available' if items else 'unavailable',
        'capability_reason': None if items else 'PERSONAL_RECORD_TASK_RETENTION_UNAVAILABLE'}


def task_reference(task):
    return {'id': str(task.id), 'patient_id': str(task.patient_id), 'owner_id': str(task.owner_id),
        'version': str(task.version), 'title': task.title, 'description': task.description, 'status': task.status,
        'retention_policy_id': str(task.retention_policy_id), 'created_at': task.created_at.isoformat(),
        'updated_at': task.updated_at.isoformat(), 'finished_at': task.finished_at.isoformat() if task.finished_at else None}


def task_view(task, scope):
    if not personal_tasks(scope).filter(id=task.id).exists():
        raise ApiError('PERSONAL_RECORD_TASK_NOT_FOUND', 404)
    task_retention(scope, task.retention_policy_id)
    return task_projection(task)


def task_projection(task):
    return {'task_id': str(task.id), 'patient_id': str(task.patient_id), 'version': str(task.version),
        'title': task.title, 'description': task.description, 'status': task.status,
        'retention_policy_id': str(task.retention_policy_id), 'created_at': task.created_at.isoformat(),
        'updated_at': task.updated_at.isoformat(), 'finished_at': task.finished_at.isoformat() if task.finished_at else None,
        'allowed_actions': ['complete', 'cancel'] if task.status == 'pending' else []}


def task_list(*, scope, limit, expires_at, cursor=None):
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ApiError('PERSONAL_RECORD_TASK_QUERY_INVALID', 422)
    now = timezone.now()
    if expires_at <= now:
        raise ApiError('CURSOR_STALE', 409)
    runtime = task_runtime(scope)
    rows = list(personal_tasks(scope).select_related('retention_policy').order_by('-updated_at', 'id'))
    for row in rows:
        if not approved_retention(row.retention_policy) or str(row.retention_policy_id) not in runtime.payload['retention_policy_ids']:
            raise ApiError('PERSONAL_RECORD_TASK_RETENTION_UNAVAILABLE', 409)
    values = [task_projection(row) for row in rows]
    bound = {**scope_binding(scope), 'items_sha256': compute_checksum({'items': values}),
        'runtime_checksum': runtime.checksum, 'limit': limit}
    offset, expiry = 0, expires_at.timestamp()
    if cursor is not None:
        saved = signing.loads(cursor, salt='personal-record-task-list')
        if saved['scope'] != bound or saved['expires_at'] <= now.timestamp():
            raise ApiError('CURSOR_STALE', 409)
        offset, expiry = saved['offset'], saved['expires_at']
    next_offset = offset + limit
    next_cursor = signing.dumps({'scope': bound, 'offset': next_offset, 'expires_at': expiry},
        salt='personal-record-task-list') if next_offset < len(values) else None
    return {'items': values[offset:next_offset], 'next_cursor': next_cursor, 'directory_version': compute_checksum(bound)}


def save_personal_task(*, scope, payload, task=None):
    require_personal_scope(scope)
    value = task_input(payload, change=task is not None)
    policy_id = value['retention_policy_id'] if task is None else task.retention_policy_id
    policy, _ = task_retention(scope, policy_id, lock=True)
    now = timezone.now()
    if task is None:
        return PersonalRecordTask.objects.create(patient_id=scope.patient.id, owner_id=scope.owner.id,
            version=1, title=value['title'], description=value['description'], status='pending',
            retention_policy=policy, created_at=now, updated_at=now, finished_at=None)
    current = personal_tasks(scope, lock=True).get(id=task.id)
    if current.version != checked_version(value['expected_version']) or current.version == 9223372036854775807:
        raise ApiError('VERSION_CONFLICT', 409)
    if current.status != 'pending':
        raise ApiError('PERSONAL_RECORD_TASK_STATE_CONFLICT', 409)
    current.status = {'complete': 'completed', 'cancel': 'cancelled'}[value['action']]
    current.version += 1
    current.updated_at, current.finished_at = now, now
    current.save(update_fields=['status', 'version', 'updated_at', 'finished_at'])
    return current


def reconcile_task_reminders(task):
    from notifications.reminder_lifecycle import reconcile_reminder

    identifiers = list(MaterialReminder.objects.filter(patient_id=task.patient_id, owner_id=task.owner_id,
        target_type='record_task', target_ref=task.id, status__in=['disabled', 'enabled', 'paused'])
        .order_by('id').values_list('id', flat=True))
    return [reconcile_reminder(identifier) for identifier in identifiers]
