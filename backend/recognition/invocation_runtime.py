"""Durable admission, authority, receipts and reconciliation for fixed OCR routes."""
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from family_content.gate_locks import find_environment_gate_shared
from identity.models import Patient, Principal
from files.models import FileObject
from files.service import object_store
from files.storage import build_object_key, digest_bytes
from recognition.invocation import InvocationRejected, actual_cost, contract_snapshot, failure_state, provider_timeout_seconds
from recognition.provider_budget import check_provider_budget
from tasks.models import Task, TaskAttempt
from tasks.service import finish_attempt


def select_workflow_field_config(provider, field_kind, field_id=None):
    """Select only the provider's explicitly approved workflow/default binding."""
    bound_id = provider.payload.get('workflow_field_config_ids', {}).get(
        field_kind, provider.payload['field_config_id'])
    if field_id is not None and str(field_id) != str(bound_id):
        raise InvocationRejected('MODEL_FIELD_CONFIGURATION_BINDING_CHANGED')
    field = ConfigVersion.objects.filter(id=bound_id, kind='content',
        schema_version='field-config-1', status='enabled', payload__kind=field_kind).first()
    if field is None:
        raise InvocationRejected('MODEL_FIELD_CONFIGURATION_BINDING_REQUIRED')
    if configuration_qualification_reason(field, settings.SJIA_ENVIRONMENT) is not None:
        raise InvocationRejected('MODEL_FIELD_CONFIGURATION_UNQUALIFIED')
    return field


def lock_receipt_identity(task):
    """Lock identity only; revoked permission must not discard a paid receipt."""
    find_environment_gate_shared(task.input_refs.get('environment', settings.SJIA_ENVIRONMENT))
    Patient.objects.select_for_update().get(id=task.patient_id)
    Principal.objects.select_for_update().get(id=task.actor_id)


def _runtime(kind, field_kind, provider_id=None, field_id=None):
    gate = find_environment_gate_shared(settings.SJIA_ENVIRONMENT)
    if gate is None or not gate.enabled or gate.runtime_config_id is None:
        raise InvocationRejected('MODEL_RUNTIME_CONFIGURATION_REQUIRED')
    runtime = ConfigVersion.objects.select_for_update().filter(id=gate.runtime_config_id,
        kind='provider', schema_version='backend-runtime-1', status='enabled').first()
    if runtime is None or configuration_qualification_reason(runtime, settings.SJIA_ENVIRONMENT) is not None:
        raise InvocationRejected('MODEL_RUNTIME_CONFIGURATION_UNQUALIFIED')
    matches = []
    for entry in runtime.payload['providers']:
        if entry['kind'] != kind or not entry['enabled'] or provider_id and entry['config_id'] != str(provider_id):
            continue
        provider = ConfigVersion.objects.select_for_update().filter(id=entry['config_id'],
            kind='provider', schema_version='provider-1', status='enabled').first()
        if provider is None or configuration_qualification_reason(provider, settings.SJIA_ENVIRONMENT) is not None:
            continue
        matches.append((provider, entry))
    if len(matches) != 1:
        raise InvocationRejected('MODEL_PROVIDER_SELECTION_REQUIRED')
    provider, entry = matches[0]
    field = select_workflow_field_config(provider, field_kind, field_id)
    if (entry['endpoint'].rstrip('/') != provider.payload['endpoint'].rstrip('/')
            or provider.payload['endpoint'].rstrip('/') != settings.ARK_BASE_URL.rstrip('/')
            or entry.get('currency') != provider.payload['currency']
            or not entry.get('billing_scope_key') or not entry.get('daily_budget')
            or Decimal(entry['daily_budget']) <= 0 or Decimal(entry.get('reserved_cost', '0')) <= 0):
        raise InvocationRejected('MODEL_PROVIDER_BUDGET_INVALID')
    policies = [item for item in runtime.payload['tasks'] if item['task_type'] == 'ocr']
    if len(policies) != 1:
        raise InvocationRejected('MODEL_TASK_RUNTIME_REQUIRED')
    return provider, runtime, entry, policies[0], field


@transaction.atomic
def admit_invocation(*, kind, field_kind, call_count=1):
    if not isinstance(call_count, int) or isinstance(call_count, bool) or call_count < 1:
        raise InvocationRejected('MODEL_CALL_LIMIT_INVALID')
    if not settings.ARK_API_KEY:
        raise InvocationRejected('ARK_NOT_CONFIGURED')
    provider, runtime, entry, policy, field = _runtime('ocr', field_kind)
    reservation = Decimal(entry['reserved_cost']) * call_count
    budget = check_provider_budget(runtime=runtime, billing_scope_key=entry['billing_scope_key'],
        currency=entry['currency'], daily_budget=entry['daily_budget'], reserved_cost=reservation,
        budget_timezone=settings.TIME_ZONE, max_concurrency=policy['max_concurrency'], reject=InvocationRejected)
    return provider, {'provider_config_id': str(provider.id), 'provider_checksum': provider.checksum,
        'runtime_config_id': str(runtime.id), 'runtime_checksum': runtime.checksum,
        'provider_entry': entry, 'task_config': policy, 'field_config_id': str(field.id),
        'field_config_checksum': field.checksum,
        'field_kind': field_kind, 'environment': settings.SJIA_ENVIRONMENT,
        'reserved_cost': str(reservation), 'per_call_reserved_cost': entry['reserved_cost'],
        'call_limit': call_count, 'budget_evidence': budget, 'invocation_contract': contract_snapshot(kind, provider)}


@transaction.atomic
def validate_invocation(task, kind):
    refs = task.input_refs
    if refs.get('environment') != settings.SJIA_ENVIRONMENT:
        raise InvocationRejected('MODEL_INVOCATION_CONTRACT_CHANGED')
    provider, runtime, entry, policy, field = _runtime('ocr', refs['field_kind'], refs['provider_config_id'], refs['field_config_id'])
    if refs.get('invocation_contract') != contract_snapshot(kind, provider):
        raise InvocationRejected('MODEL_INVOCATION_CONTRACT_CHANGED')
    if (provider.checksum != refs['provider_checksum'] or str(runtime.id) != refs['runtime_config_id']
            or runtime.checksum != refs['runtime_checksum'] or entry != refs['provider_entry']
            or policy != refs['task_config'] or str(field.id) != refs['field_config_id']
            or field.checksum != refs.get('field_config_checksum')):
        raise InvocationRejected('MODEL_RUNTIME_CONFIGURATION_CHANGED')
    return provider


def require_authority(task, attempt, owner, execution_until):
    now = timezone.now()
    if (task.status != 'running' or attempt.status != 'running'
            or task.lease_generation != attempt.lease_generation or task.lease_owner != owner
            or task.lease_until is None or task.lease_until <= now
            or task.deadline_at <= now or execution_until <= now):
        raise InvocationRejected('MODEL_PROVIDER_CALL_AUTHORITY_UNAVAILABLE')


def provider_marker(task, attempt, owner, execution_until, *, call_index=None):
    def mark(evidence):
        with transaction.atomic():
            current = Task.objects.select_for_update().get(id=task.id)
            current_attempt = TaskAttempt.objects.select_for_update().get(id=attempt.id, task=current)
            require_authority(current, current_attempt, owner, execution_until)
            expected_contract = current.input_refs.get('invocation_contract')
            if expected_contract is None or any(evidence.get(key) != value for key, value in expected_contract.items()):
                raise InvocationRejected('MODEL_INVOCATION_CONTRACT_CHANGED')
            if evidence.get('provider_config_id') != current.input_refs.get('provider_config_id'):
                raise InvocationRejected('MODEL_PROVIDER_CONFIGURATION_CHANGED')
            expected_checksum = current.input_refs.get('provider_checksum') or current.input_refs.get('scope', {}).get('configuration', {}).get('provider_checksum')
            if not expected_checksum or evidence.get('provider_checksum') != expected_checksum:
                raise InvocationRejected('MODEL_PROVIDER_CONFIGURATION_CHANGED')
            effective_timeout = provider_timeout_seconds(current, execution_until, current.input_refs.get('provider_entry'))
            if 'timeout_seconds' in evidence:
                effective_timeout = min(effective_timeout, evidence['timeout_seconds'])
            evidence['timeout_seconds'] = effective_timeout
            usage = current_attempt.usage_evidence or {}
            if call_index is None:
                if usage.get('provider_called'):
                    raise InvocationRejected('MODEL_DUPLICATE_PROVIDER_CALL')
                usage = {**usage, **evidence}
            else:
                calls = usage.get('provider_calls', [])
                if call_index != len(calls) or call_index >= current.input_refs['call_limit']:
                    raise InvocationRejected('MODEL_CALL_LIMIT_EXCEEDED')
                calls.append({**evidence, 'call_index': call_index,
                    'reserved_cost': current.input_refs['per_call_reserved_cost']})
                usage = {**usage, 'provider_calls': calls, 'provider_called': True, 'phase': 'provider_started'}
            current_attempt.usage_evidence = usage
            current_attempt.save(update_fields=['usage_evidence'])
        return effective_timeout
    return mark


def persist_call_receipt(*, task, attempt, original, result, provider, call_index):
    raw = result['raw_response']
    raw = raw.encode('utf-8') if isinstance(raw, str) else raw
    response_id = uuid4()
    checksum = digest_bytes(raw)
    key = build_object_key(task.patient_id, response_id, 'ocr_response')
    object_store.put_immutable(key, raw)
    receipt = {**result['invocation'], 'provider_request_id': result['provider_request_id'],
        'model': result['model'], 'usage': result['usage'], 'actual_cost': None,
        'cost_status': 'not_measured', 'currency': provider.payload['currency'],
        'pricing_evidence_ref': provider.payload['pricing_evidence_ref'],
        'input_per_million': provider.payload['input_per_million'],
        'output_per_million': provider.payload['output_per_million'],
        'raw_response_file_id': str(response_id), 'raw_response_sha256': checksum,
        'raw_response_size_bytes': len(raw)}
    with transaction.atomic():
        lock_receipt_identity(task)
        Task.objects.select_for_update().get(id=task.id)
        response = FileObject.objects.create(id=response_id, patient_id=task.patient_id,
            file_group_id=original.file_group_id, object_key=key, object_version=checksum,
            checksum=checksum, size_bytes=len(raw), media_type='application/json',
            purpose='ocr_response', status='available', retention_policy=original.retention_policy,
            created_at=timezone.now())
        current = TaskAttempt.objects.select_for_update().get(id=attempt.id)
        usage = current.usage_evidence or {}
        calls = usage['provider_calls']
        calls[call_index] = {**calls[call_index], **receipt}
        current.provider_request_id = result['provider_request_id']
        current.usage_evidence = {**usage, 'provider_calls': calls, 'phase': 'provider_finished'}
        current.save(update_fields=['provider_request_id', 'usage_evidence'])
    # Persist first even when provider usage is malformed or omitted.
    cost = actual_cost(result, provider)
    if cost is not None:
        with transaction.atomic():
            Task.objects.select_for_update().get(id=task.id)
            current = TaskAttempt.objects.select_for_update().get(id=attempt.id)
            current.usage_evidence['provider_calls'][call_index].update(actual_cost=str(cost), cost_status='measured')
            current.save(update_fields=['usage_evidence'])
    return response


@transaction.atomic
def record_invocation_failure(task_id, owner, reason):
    find_environment_gate_shared(settings.SJIA_ENVIRONMENT)
    task = Task.objects.select_for_update().get(id=task_id)
    if task.status not in ('accepted', 'queued', 'running', 'cancel_requested', 'cancelled'):
        return
    if task.lease_owner and task.lease_owner != owner:
        return
    attempt = TaskAttempt.objects.select_for_update().filter(task=task,
        lease_generation=task.lease_generation).order_by('-attempt_number').first()
    evidence = attempt.usage_evidence or {} if attempt else {}
    state = failure_state(evidence, task.status in ('cancel_requested', 'cancelled'))
    task.status, task.status_version, task.lease_until = state, task.status_version + 1, timezone.now()
    task.save(update_fields=['status', 'status_version', 'lease_until'])
    if attempt is not None:
        finish_attempt(attempt, state, error_code=reason, usage_evidence={**evidence, 'failure_reason': reason})
    else:
        TaskAttempt.objects.create(task=task, attempt_number=TaskAttempt.objects.filter(task=task).count() + 1,
            lease_generation=task.lease_generation, started_at=timezone.now(), finished_at=timezone.now(),
            status=state, error_code=reason, usage_evidence={**evidence, 'failure_reason': reason})
