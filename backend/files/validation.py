"""Intent-bound local upload validation with frozen originals and runtime budgets."""
import re
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from assessment.provenance import entity_reference
from authorization.policy import check_principal_session, evaluate
from configurations.evidence import deployed_artifact_checksum
from configurations.service import compute_checksum
from family_content.models import EnvironmentGate
from family_content.gate_locks import read_environment_gate_shared
from files.errors import FileRejected
from files.models import FileGroup, FileObject, FilePage, UploadPart
from files.purpose import checked_bytes, lock_original, lock_use_authorization
from files.source_pages import PARSER_VERSION, inspect_source
from identity.models import Patient, Principal
from requests.models import WriteIntent, WriteRequest
from requests.service import commit_intent, compute_digest, prepare_intent
from tasks.models import ConsumerReceipt, Task, TaskAttempt
from tasks.outbox import emit_event, record_receipt
from tasks.service import acquire_lease, cancel_task, complete_task, create_attempt, register_task

OPERATION = 'file_group_complete'
SCHEMA = 'file-validation-task-1'
EVENT = 'file_validation.requested'


class ValidationDeferred(Exception):
    def __init__(self, seconds, max_retries):
        self.seconds, self.max_retries = seconds, max_retries
        super().__init__('FILE_VALIDATION_CONCURRENCY_WAIT')


def canonical_uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError()
        return UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise FileRejected('FILE_VALIDATION_INPUT_INVALID') from None


def checked_payload(value):
    if (not isinstance(value, dict) or set(value) != {'expected_version', 'file_ids'}
            or not isinstance(value['expected_version'], str)
            or not re.fullmatch(r'[1-9][0-9]{0,18}', value['expected_version'])
            or int(value['expected_version']) > 9223372036854775807
            or not isinstance(value['file_ids'], list) or not value['file_ids']):
        raise FileRejected('FILE_VALIDATION_INPUT_INVALID')
    for value_id in value['file_ids']:
        canonical_uuid(value_id)
    if len(set(value['file_ids'])) != len(value['file_ids']):
        raise FileRejected('FILE_VALIDATION_INPUT_INVALID')
    return value


def target_group(target):
    match = re.fullmatch(r'/api/(?:v1/)?uploads/([0-9a-f-]{36})/complete', target) if isinstance(target, str) else None
    if match is None:
        raise FileRejected('FILE_VALIDATION_TARGET_INVALID')
    return FileGroup.objects.get(id=canonical_uuid(match[1]))


def prefix(actor_id, patient_id, *, lock=True):
    principals = Principal.objects.select_for_update(no_key=True) if lock else Principal.objects.all()
    patients = Patient.objects.select_for_update(no_key=True) if lock else Patient.objects.all()
    gate = (read_environment_gate_shared(settings.SJIA_ENVIRONMENT) if lock
        else EnvironmentGate.objects.get(environment=settings.SJIA_ENVIRONMENT))
    actor = principals.get(id=actor_id)
    patient = patients.get(id=patient_id)
    if not gate.enabled or patient.status != 'active':
        raise FileRejected('FILE_VALIDATION_SCOPE_UNAVAILABLE')
    return gate, actor, patient


def scope(*, actor, auth_epoch, audience, group_id, payload, lock=True):
    from archives.batches import batch_runtime
    from sources.models import SourceRegistry, SourceRevision
    payload = checked_payload(payload)
    initial = FileGroup.objects.get(id=group_id)
    gate, actor, patient = prefix(actor.id, initial.patient_id, lock=lock)
    check_principal_session(actor, auth_epoch)
    if audience != 'family':
        raise FileRejected('FAMILY_AUDIENCE_REQUIRED')
    grant = evaluate(principal=actor, patient=patient, action='write_record', purpose='family_observation',
        environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=auth_epoch,
        author_id=actor.id, whole_resource=True, lock=lock).grant
    groups = FileGroup.objects.select_for_update() if lock else FileGroup.objects.all()
    group = groups.get(id=group_id, patient=patient)
    if group.owner_id != actor.id or str(group.version) != payload['expected_version']:
        raise FileRejected('FILE_VALIDATION_GROUP_MISMATCH')
    if group.status not in ('initiated', 'uploaded', 'validating', 'available', 'failed'):
        raise FileRejected('FILE_VALIDATION_GROUP_UNAVAILABLE')
    runtime = batch_runtime()
    policies = [item for item in runtime.payload['tasks'] if item['task_type'] == 'file_validation']
    if len(policies) != 1:
        raise FileRejected('FILE_VALIDATION_POLICY_REQUIRED')
    policy, limits = policies[0], runtime.payload['limits']
    uploads = UploadPart.objects.select_for_update() if lock else UploadPart.objects.all()
    parts = list(uploads.filter(file_group_id=group.id, patient_id=patient.id).order_by('ordinal'))
    if (not parts or any(part.status != 'stored' for part in parts)
            or [str(part.file_id) for part in parts] != payload['file_ids']):
        raise FileRejected('FILE_VALIDATION_GROUP_INCOMPLETE')
    if set(FileObject.objects.filter(file_group_id=group.id, patient=patient, purpose='original').values_list('id', flat=True)) != {part.file_id for part in parts}:
        raise FileRejected('FILE_VALIDATION_GROUP_INCOMPLETE')
    originals, pages, auth = [], [], None
    for part in parts:
        original = lock_original(part.file_id, lock=lock)
        if (original.patient_id != patient.id or original.file_group_id != group.id
                or original.object_version != part.object_version or original.object_key != part.object_key
                or original.checksum != part.declared_checksum or original.size_bytes != part.declared_size_bytes
                or original.media_type != part.declared_media_type or original.media_type not in limits['media_types']
                or original.size_bytes <= 0 or original.size_bytes > limits['max_upload_bytes']):
            raise FileRejected('FILE_VALIDATION_ORIGINAL_CHANGED')
        auth = lock_use_authorization(group.use_authorization_id, original, 'family_observation', settings.SJIA_ENVIRONMENT, lock=lock)
        originals.append(original)
        file_pages = FilePage.objects.select_for_update() if lock else FilePage.objects.all()
        pages.extend(file_pages.filter(file_id=original.id, file_group_id=group.id,
            patient=patient).order_by('ordinal'))
    if not pages or len(pages) > limits['max_pages'] or [page.ordinal for page in pages] != list(range(1, len(pages) + 1)):
        raise FileRejected('FILE_VALIDATION_PAGE_MISMATCH')
    revisions = SourceRevision.objects.select_for_update() if lock else SourceRevision.objects.all()
    sources = SourceRegistry.objects.select_for_update() if lock else SourceRegistry.objects.all()
    revision = revisions.get(id=group.source_revision_id)
    source = sources.get(id=revision.source_id)
    context = {'environment': settings.SJIA_ENVIRONMENT, 'auth_epoch': str(auth_epoch), 'audience': audience,
        'patient_id': str(patient.id), 'actor_id': str(actor.id), 'group_id': str(group.id), 'group_version': str(group.version),
        'actor': entity_reference(actor), 'grant': entity_reference(grant), 'authorization': entity_reference(auth),
        'source_revision': entity_reference(revision), 'source': entity_reference(source),
        'parts': [entity_reference(part) for part in parts], 'originals': [entity_reference(item) for item in originals],
        'pages': [entity_reference(page) for page in pages], 'runtime_id': str(runtime.id), 'runtime_checksum': runtime.checksum,
        'implementation_sha256': deployed_artifact_checksum(runtime), 'parser_version': PARSER_VERSION,
        'task_policy': policy, 'poll_after_ms': runtime.payload['api']['poll_after_ms']}
    return SimpleNamespace(actor=actor, patient=patient, group=group, context=context, originals=originals, pages=pages, limits=limits, runtime=runtime)


def validate_registration(actor, patient, schema_version, input_refs):
    from tasks.service import TaskRejected
    if (schema_version != SCHEMA or patient is None or not isinstance(input_refs, dict)
            or set(input_refs) != {'context', 'input'} or not isinstance(input_refs.get('context'), dict)):
        raise TaskRejected('FILE_VALIDATION_POLICY_REQUIRED')
    context = input_refs['context']
    if context.get('actor_id') != str(actor.id) or context.get('patient_id') != str(patient.id):
        raise TaskRejected('FILE_VALIDATION_POLICY_REQUIRED')
    current = scope(actor=actor, auth_epoch=int(context['auth_epoch']), audience=context['audience'],
        group_id=context['group_id'], payload=input_refs['input'])
    if current.context != context:
        raise TaskRejected('FILE_VALIDATION_POLICY_REQUIRED')


def task_view(task):
    completed = task.status == 'completed'
    return {'task_id': str(task.id), 'task_type': 'file_validation',
        'request_id': str(task.request_row.request_id), 'accepted_at': task.created_at.isoformat(),
        'status': task.status, 'status_version': str(task.status_version),
        'poll_after_ms': task.input_refs['context']['poll_after_ms'] if task.status in ('accepted', 'queued', 'running') else None,
        'allowed_actions': ['task_cancel'] if task.status in ('accepted', 'queued', 'running') else [],
        'result_type': 'file_group' if completed else None,
        'result_ref': task.input_refs['context']['group_id'] if completed else None,
        'error_code': TaskAttempt.objects.filter(task=task).order_by('-attempt_number').values_list('error_code', flat=True).first()}


@transaction.atomic
def prepare_validation_intent(*, actor, auth_epoch, audience, data, retention_policy):
    if (not isinstance(data, dict) or set(data) != {'operation_id', 'target_key', 'request_id', 'schema_version', 'payload'}
            or data['operation_id'] != OPERATION or data['schema_version'] != '1.0.0'):
        raise FileRejected('FILE_VALIDATION_INPUT_INVALID')
    request_id = canonical_uuid(data['request_id'])
    group = target_group(data['target_key'])
    admitted = scope(actor=actor, auth_epoch=auth_epoch, audience=audience, group_id=group.id, payload=data['payload'])
    return prepare_intent(actor=admitted.actor, patient=admitted.patient, operation_id=OPERATION,
        target_key=data['target_key'], request_id=request_id, payload=data['payload'], schema_version='1.0.0',
        retention_policy=retention_policy, recovery_payload=data['payload'],
        ttl_seconds=admitted.runtime.payload['api']['intent_ttl_seconds'])


def saved_task(intent):
    saved = intent.write_request
    if (intent.status != 'committed' or saved is None or saved.status != 'committed'
            or saved.action != OPERATION or saved.resource_type != 'task' or saved.resource_id is None
            or saved.actor_id != intent.actor_id or saved.patient_id != intent.patient_id
            or saved.request_id != intent.request_id or saved.target_key != intent.target_key
            or saved.payload_sha256 != intent.payload_sha256 or saved.completed_at is None):
        raise FileRejected('FILE_VALIDATION_RECEIPT_INVALID')
    return Task.objects.get(id=saved.resource_id, request_row=saved, actor_id=intent.actor_id,
        patient_id=intent.patient_id, task_type='file_validation', schema_version=SCHEMA)


@transaction.atomic
def submit_validation(*, actor, auth_epoch, audience, target, payload, headers):
    group = target_group(target)
    admitted = scope(actor=actor, auth_epoch=auth_epoch, audience=audience, group_id=group.id, payload=payload)
    intent = WriteIntent.objects.select_for_update().get(id=canonical_uuid(headers.get('X-Write-Intent')),
        actor=actor, operation_id=OPERATION, target_key=target)
    if (intent.patient_id != group.patient_id or intent.recovery_payload != payload
            or str(intent.request_id) != headers.get('Idempotency-Key') or str(intent.generation) != headers.get('X-Intent-Generation')
            or intent.payload_sha256 != compute_digest(OPERATION, target, '1.0.0', payload)):
        raise FileRejected('FILE_VALIDATION_INTENT_MISMATCH')
    if intent.status == 'committed':
        return task_view(saved_task(intent))
    if intent.status != 'prepared' or intent.expires_at <= timezone.now():
        raise FileRejected('FILE_VALIDATION_INTENT_EXPIRED')
    if admitted.group.status not in ('initiated', 'uploaded'):
        raise FileRejected('FILE_VALIDATION_GROUP_UNAVAILABLE')
    policy = admitted.context['task_policy']
    refs = {'context': admitted.context, 'input': payload}
    task = register_task(actor=admitted.actor, patient=admitted.patient, task_type='file_validation', schema_version=SCHEMA,
        input_refs=refs, deadline_seconds=policy['queue_wait_seconds'] + policy['execution_seconds'])
    receipt = commit_intent(intent=intent, action=OPERATION, resource_type='task', resource_id=task.id,
        expected_version=int(payload['expected_version']))
    task.request_row = receipt; task.save(update_fields=['request_row'])
    admitted.group.status = 'validating'; admitted.group.save(update_fields=['status'])
    emit_event(event_type=EVENT, schema_version=SCHEMA, aggregate_id=task.id, aggregate_version=1,
        correlation_id=intent.request_id, resource_refs={'task_id': str(task.id)}, task=task)
    return task_view(task)


@transaction.atomic
def validation_request_view(*, actor, auth_epoch, audience, intent):
    group = target_group(intent.target_key)
    scope(actor=actor, auth_epoch=auth_epoch, audience=audience, group_id=group.id, payload=intent.recovery_payload)
    intent = WriteIntent.objects.select_for_update().get(id=intent.id, actor=actor)
    rows = WriteRequest.objects.filter(actor=actor, action=OPERATION, target_key=intent.target_key, request_id=intent.request_id)
    if intent.status != 'committed' and (intent.write_request_id or rows.exists()):
        raise FileRejected('FILE_VALIDATION_RECEIPT_UNCERTAIN')
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': OPERATION,
        'target_key': intent.target_key, 'patient_id': str(intent.patient_id), 'status': intent.status,
        'generation': str(intent.generation), 'may_retry': intent.status != 'committed' and intent.expires_at <= timezone.now(),
        'task': task_view(saved_task(intent)) if intent.status == 'committed' else None,
        'file_group_input': intent.recovery_payload}


@transaction.atomic
def retry_validation_intent(*, actor, auth_epoch, audience, intent, expected_generation):
    view = validation_request_view(actor=actor, auth_epoch=auth_epoch, audience=audience, intent=intent)
    intent.refresh_from_db()
    if not view['may_retry'] or str(intent.generation) != str(expected_generation) or intent.generation >= 9223372036854775807:
        raise FileRejected('FILE_VALIDATION_RETRY_UNAVAILABLE')
    from archives.batches import batch_runtime
    intent.generation += 1
    intent.status, intent.error_code, intent.completed_at = 'prepared', None, None
    intent.prepared_at = timezone.now()
    intent.expires_at = intent.prepared_at + timedelta(seconds=batch_runtime().payload['api']['intent_ttl_seconds'])
    intent.save(update_fields=['generation', 'status', 'error_code', 'completed_at', 'prepared_at', 'expires_at'])
    return intent


def current_task(event):
    seed = Task.objects.get(id=event.task_id, task_type='file_validation', schema_version=SCHEMA)
    prefix(seed.actor_id, seed.patient_id)
    task = Task.objects.select_for_update().get(id=seed.id)
    saved = task.request_row
    if (event.event_type != EVENT or event.schema_version != SCHEMA or event.aggregate_id != task.id
            or event.aggregate_version != 1 or event.resource_refs != {'task_id': str(task.id)}
            or saved is None or saved.status != 'committed' or saved.resource_type != 'task'
            or saved.resource_id != task.id or saved.action != OPERATION or saved.actor_id != task.actor_id
            or saved.patient_id != task.patient_id or event.correlation_id != saved.request_id
            or saved.target_key not in (f"/api/uploads/{task.input_refs.get('context', {}).get('group_id')}/complete",
                f"/api/v1/uploads/{task.input_refs.get('context', {}).get('group_id')}/complete")
            or saved.payload_sha256 != compute_digest(OPERATION, saved.target_key, '1.0.0', task.input_refs.get('input'))
            or task.input_sha256 != compute_checksum(task.input_refs)):
        raise FileRejected('FILE_VALIDATION_TASK_INVALID')
    return task


def check_context(task):
    expected = task.input_refs['context']
    current = scope(actor=task.actor, auth_epoch=int(expected['auth_epoch']), audience=expected['audience'],
        group_id=expected['group_id'], payload=task.input_refs['input'])
    if current.context != expected:
        raise FileRejected('FILE_VALIDATION_INPUT_CHANGED')
    return current


def terminal(task, event):
    if task.status not in ('completed', 'failed', 'cancelled', 'superseded'):
        return False
    effect = UUID(task.input_refs['context']['group_id']) if task.status == 'completed' else task.id
    row, _ = ConsumerReceipt.objects.get_or_create(consumer='file_validation', event=event,
        defaults={'effect_resource_id': effect, 'processed_at': timezone.now()})
    if row.effect_resource_id != effect:
        raise FileRejected('FILE_VALIDATION_RECEIPT_INVALID')
    return True


def execute_validation(event, owner):
    generation = None
    try:
        with transaction.atomic():
            task = current_task(event)
            if terminal(task, event): return
            admitted = check_context(task)
            now, policy = timezone.now(), task.input_refs['context']['task_policy']
            if task.deadline_at <= now or task.status in ('accepted', 'queued') and task.created_at + timedelta(seconds=policy['queue_wait_seconds']) <= now:
                raise FileRejected('FILE_VALIDATION_DEADLINE_EXCEEDED')
            if task.status == 'running' and task.lease_until and task.lease_until > now: return
            if TaskAttempt.objects.filter(task=task).count() >= policy['max_attempts']:
                raise FileRejected('FILE_VALIDATION_ATTEMPT_LIMIT')
            if Task.objects.filter(task_type='file_validation', schema_version=SCHEMA, status='running', lease_until__gt=now,
                    input_refs__context__runtime_id=admitted.context['runtime_id']).exclude(id=task.id).count() >= policy['max_concurrency']:
                raise ValidationDeferred(policy['heartbeat_seconds'], policy['queue_wait_seconds'] // policy['heartbeat_seconds'] + 1)
            task.deadline_at = min(task.deadline_at, now + timedelta(seconds=policy['execution_seconds']))
            task.save(update_fields=['deadline_at'])
            if not acquire_lease(task.id, owner, now + timedelta(seconds=policy['lease_seconds'])): return
            task.refresh_from_db(); generation = task.lease_generation
            attempt = create_attempt(task, generation)
        # Bytes stay local. Adoption happens only after a second locked scope,
        # current configuration and immutable source/part/page comparison.
        from tasks.lease import lease_heartbeat
        inspected = []
        with lease_heartbeat(task.id, attempt.lease_generation, policy, task.deadline_at):
            for original in admitted.originals:
                parsed = inspect_source(checked_bytes(original), original.media_type, admitted.limits)
                inspected.append((str(original.id), parsed))
        with transaction.atomic():
            task = current_task(event)
            if terminal(task, event): return
            if task.status != 'running' or task.lease_owner != owner or task.lease_generation != attempt.lease_generation: return
            admitted = check_context(task)
            if admitted.group.status != 'validating':
                raise FileRejected('FILE_VALIDATION_GROUP_UNAVAILABLE')
            for original in admitted.originals:
                checked_bytes(original)
            for file_id, parsed in inspected:
                actual = [page for page in admitted.pages if str(page.file_id) == file_id]
                if len(actual) != len(parsed) or any((page.width, page.height, page.rotation) !=
                        (entry['width'], entry['height'], entry['rotation']) for page, entry in zip(actual, parsed, strict=True)):
                    raise FileRejected('FILE_VALIDATION_PAGE_MISMATCH')
            if not complete_task(task.id, attempt.lease_generation, 'file_group', admitted.group.id):
                raise FileRejected('FILE_VALIDATION_LEASE_EXPIRED')
            admitted.group.status = 'available'; admitted.group.save(update_fields=['status'])
            attempt.status, attempt.finished_at = 'completed', timezone.now(); attempt.save(update_fields=['status', 'finished_at'])
            record_receipt('file_validation', event, admitted.group.id)
    except ValidationDeferred: raise
    except Exception as error:
        record_failure(event, owner, getattr(error, 'reason_code', getattr(error, 'code', 'FILE_VALIDATION_FAILED')), generation=generation)
        raise


@transaction.atomic
def record_failure(event, owner, code, generation=None):
    seed = Task.objects.get(id=event.task_id, task_type='file_validation', schema_version=SCHEMA)
    # Failure recording does not require the now-revoked permission or active patient.
    read_environment_gate_shared(settings.SJIA_ENVIRONMENT)
    Principal.objects.select_for_update(no_key=True).get(id=seed.actor_id)
    Patient.objects.select_for_update(no_key=True).get(id=seed.patient_id)
    task = Task.objects.select_for_update().get(id=seed.id)
    if (event.event_type != EVENT or event.schema_version != SCHEMA or event.aggregate_id != task.id
            or event.aggregate_version != 1 or event.resource_refs != {'task_id': str(task.id)}
            or task.request_row is None or task.request_row.resource_id != task.id
            or event.correlation_id != task.request_row.request_id):
        return
    # An owner token may be reused by Celery redelivery. Only the captured
    # attempt generation can fail running work; generic on_failure lacks it.
    if generation is None:
        if task.status not in ('accepted', 'queued') or task.lease_generation != 0: return
    elif task.status != 'running' or task.lease_owner != owner or task.lease_generation != generation:
        return
    task.status, task.status_version, task.lease_until = 'failed', task.status_version + 1, timezone.now()
    task.save(update_fields=['status', 'status_version', 'lease_until'])
    attempts = TaskAttempt.objects.filter(task=task, lease_generation=task.lease_generation, status='running')
    if attempts.exists(): attempts.update(status='failed', finished_at=timezone.now(), error_code=code)
    else: TaskAttempt.objects.create(task=task, attempt_number=TaskAttempt.objects.filter(task=task).count()+1,
        lease_generation=task.lease_generation, started_at=timezone.now(), finished_at=timezone.now(), status='failed', error_code=code)
    original_group = target_group(task.request_row.target_key)
    FileGroup.objects.filter(id=original_group.id, patient_id=task.patient_id, status='validating').update(status='failed')
    if not ConsumerReceipt.objects.filter(event=event, consumer='file_validation').exists(): record_receipt('file_validation', event, task.id)


@transaction.atomic
def read_validation_task(*, task, actor, auth_epoch, audience, lock=True):
    scope(actor=actor, auth_epoch=auth_epoch, audience=audience, group_id=task.input_refs['context']['group_id'], payload=task.input_refs['input'], lock=lock)
    task.refresh_from_db()
    return task_view(task)


@transaction.atomic
def cancel_validation(*, task, actor, auth_epoch, audience):
    scope(actor=actor, auth_epoch=auth_epoch, audience=audience, group_id=task.input_refs['context']['group_id'], payload=task.input_refs['input'])
    if cancel_task(task.id):
        FileGroup.objects.filter(id=task.input_refs['context']['group_id'], status='validating').update(status='failed')
