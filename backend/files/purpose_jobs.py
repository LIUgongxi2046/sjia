import uuid

from django.db import transaction
from django.utils import timezone

from archives.models import PurposeCopy
from files.models import FileObject
from files.purpose import audit_copy, checked_bytes, lock_file_access, lock_original, lock_prepared_image, lock_processing_config, lock_use_authorization, processing_page, runtime_environment
from files.purpose_dependencies import register_copy
from files.purpose_processing import process_image
from files.purpose_plan import plan_processing_version
from files.purpose_text import process_confirmed_text
from files.service import FileRejected, object_store
from files.storage import build_object_key, digest_bytes
from tasks.models import ConsumerReceipt, Task, TaskAttempt
from tasks.outbox import emit_event, record_receipt
from tasks.service import acquire_lease, create_attempt, finish_attempt, input_digest, register_task
from archives.batches import batch_runtime


@transaction.atomic
def submit_copy(*, file_id, actor, auth_epoch, payload):
    original = FileObject.objects.get(id=file_id)
    actor, patient, gate, group = lock_file_access(original, actor, auth_epoch,
        purpose=payload.purpose, service_action='manage_data_use')
    original = lock_original(original.id)
    auth = lock_use_authorization(payload.authorization_id, original, payload.purpose, gate.environment)
    config, plan, approvals, validations = lock_processing_config(payload.processing_config_id, original,
        payload.purpose, payload.target, gate.environment)
    refs = {'original_file_id': str(original.id), 'original_version': original.object_version,
        'original_checksum': original.checksum, 'original_group_version': str(group.version),
        'authorization_id': str(auth.id), 'purpose': payload.purpose, 'environment': gate.environment,
        'target': payload.target, 'config_id': str(config.id), 'config_checksum': config.checksum,
        'config_version': str(config.version), 'auth_epoch': auth_epoch,
        'approval_ids': [str(item.id) for item in approvals], 'validation_ids': [str(item.id) for item in validations]}
    task = register_task(actor=actor, patient=patient, task_type=Task.TaskType.PURPOSE_COPY,
        schema_version='purpose-copy-2', input_refs=refs)
    emit_event(event_type='purpose_copy.requested', schema_version='purpose-copy-2', aggregate_id=original.id,
        aggregate_version=group.version, correlation_id=task.id, resource_refs={'task_id': str(task.id)}, task=task)
    return task


def execute_copy(event, owner):
    with transaction.atomic():
        task = Task.objects.select_for_update().get(id=event.task_id)
        if task.task_type != Task.TaskType.PURPOSE_COPY:
            raise FileRejected('PURPOSE_TASK_TYPE_MISMATCH')
        if ConsumerReceipt.objects.filter(consumer='purpose_copy', event=event).exists():
            return
        if task.status in ('completed', 'failed', 'cancelled', 'superseded'):
            record_receipt('purpose_copy', event, task.id)
            return
        if not acquire_lease(task.id, owner, task.deadline_at):
            return
        task.refresh_from_db()
        attempt = create_attempt(task, task.lease_generation)
        refs = task.input_refs
    # 每次尝试已经持久保存；异常由 Worker 记录准确失败，不产生替代副本。
    with transaction.atomic():
        original = FileObject.objects.get(id=refs['original_file_id'], patient_id=task.patient_id)
        actor, patient, gate, group = lock_file_access(original, task.actor, refs['auth_epoch'],
            purpose=refs['purpose'], service_action='manage_data_use')
        original = lock_original(original.id)
        task = Task.objects.select_for_update().get(id=task.id)
        if task.status != 'running' or task.lease_generation != attempt.lease_generation or task.lease_owner != owner:
            finish_attempt(attempt, 'superseded')
            return
        if task.deadline_at <= timezone.now():
            raise FileRejected('TASK_DEADLINE_EXCEEDED')
        if (input_digest(task.input_refs) != task.input_sha256 or original.checksum != refs['original_checksum']
                or original.object_version != refs['original_version'] or str(group.version) != refs['original_group_version']
                or gate.environment != refs['environment']):
            raise FileRejected('PURPOSE_INPUT_CHANGED')
        lock_use_authorization(refs['authorization_id'], original, refs['purpose'], gate.environment)
        config, plan, approvals, validations = lock_processing_config(refs['config_id'], original,
            refs['purpose'], refs['target'], gate.environment)
        if config.checksum != refs['config_checksum'] or str(config.version) != refs['config_version']:
            raise FileRejected('PURPOSE_CONFIGURATION_CHANGED')
        original_bytes = checked_bytes(original)
        prepared = lock_prepared_image(plan, original)
        prepared_bytes = checked_bytes(prepared) if prepared is not None else None
        page, page_number = processing_page(plan, original)
        limits = batch_runtime().payload['limits']
        processing_version = plan_processing_version(plan)
        if plan.schema_version == 'purpose-processing-text-1':
            data, automatic_evidence = process_confirmed_text(plan=plan, original_bytes=original_bytes,
                prepared_bytes=prepared_bytes, original_media_type=original.media_type, limits=limits)
            output_media_type = 'text/plain'
        else:
            data, automatic_evidence = process_image(plan=plan, original_bytes=original_bytes, prepared_bytes=prepared_bytes,
                original_media_type=original.media_type, page_number=page_number, limits=limits)
            output_media_type = 'image/png'
        if automatic_evidence['width'] != page.width or automatic_evidence['height'] != page.height:
            raise FileRejected('PURPOSE_PAGE_DIMENSIONS_CHANGED')
        if task.deadline_at <= timezone.now():
            raise FileRejected('TASK_DEADLINE_EXCEEDED')
        derived_id = uuid.uuid4()
        key = build_object_key(patient.id, derived_id, 'deidentified')
        derived = FileObject.objects.create(id=derived_id, patient=patient, file_group_id=group.id,
            derived_from_id=original.id, object_key=key, object_version=digest_bytes(data), checksum=digest_bytes(data),
            size_bytes=len(data), media_type=output_media_type, purpose='deidentified',
            processing_version=processing_version, status='quarantined', retention_policy=original.retention_policy,
            created_at=timezone.now())
        attempt.usage_evidence = {'stage_file_id': str(derived.id), 'processing_version': processing_version,
            'input_bytes': len(original_bytes), 'output_bytes': len(data), 'provider_called': False}
        attempt.save(update_fields=['usage_evidence'])
    object_store.put_immutable(key, data)
    with transaction.atomic():
        original = FileObject.objects.get(id=refs['original_file_id'], patient_id=task.patient_id)
        actor, patient, gate, group = lock_file_access(original, task.actor, refs['auth_epoch'],
            purpose=refs['purpose'], service_action='manage_data_use')
        original = lock_original(original.id)
        task = Task.objects.select_for_update().get(id=task.id)
        derived = FileObject.objects.select_for_update().get(id=derived.id, patient=patient)
        if task.status != 'running' or task.lease_generation != attempt.lease_generation or task.lease_owner != owner:
            finish_attempt(attempt, 'superseded', usage_evidence=attempt.usage_evidence)
            return
        if task.deadline_at <= timezone.now():
            raise FileRejected('TASK_DEADLINE_EXCEEDED')
        if (input_digest(task.input_refs) != task.input_sha256 or original.checksum != refs['original_checksum']
                or original.object_version != refs['original_version'] or str(group.version) != refs['original_group_version']
                or gate.environment != refs['environment']):
            raise FileRejected('PURPOSE_INPUT_CHANGED')
        authorization = lock_use_authorization(refs['authorization_id'], original, refs['purpose'], gate.environment)
        config, plan, approvals, validations = lock_processing_config(refs['config_id'], original,
            refs['purpose'], refs['target'], gate.environment)
        if config.checksum != refs['config_checksum'] or str(config.version) != refs['config_version']:
            raise FileRejected('PURPOSE_CONFIGURATION_CHANGED')
        checked_bytes(original)
        prepared = lock_prepared_image(plan, original)
        saved_bytes = object_store.get(derived.object_key)
        if digest_bytes(saved_bytes) != derived.checksum or len(saved_bytes) != derived.size_bytes:
            raise FileRejected('PURPOSE_CHECKSUM_MISMATCH')
        derived.status = 'available'
        derived.save(update_fields=['status'])
        copy = PurposeCopy.objects.create(id=uuid.uuid4(), patient_id=patient.id, original_file_id=original.id,
            derived_file_id=derived.id, version=1, purpose=refs['purpose'], environment=gate.environment,
            use_authorization_id=refs['authorization_id'], processing_version=processing_version,
            processing_evidence={**automatic_evidence, 'task_id': str(task.id), 'original_version': original.object_version,
                'original_group_version': str(group.version), 'target': refs['target'], 'config_id': str(config.id),
                'config_version': str(config.version), 'config_checksum': config.checksum,
                'required_scopes': plan.required_scopes, 'identity_coverage': [item.model_dump() for item in plan.identity_coverage],
                'clinical_preservation_evidence_ref': plan.clinical_preservation_evidence_ref,
                'approval_ids': [str(item.id) for item in approvals], 'validation_ids': [str(item.id) for item in validations]},
            date_strategy=plan.date_strategy, clinical_integrity_status='pending', status='pending_review',
            created_by=actor.id, created_at=timezone.now())
        register_copy(copy, original, derived, authorization, config, approvals, validations, prepared)
        audit_copy(copy, original, actor, 'purpose_copy_created', task.request_row.request_id if task.request_row_id else None)
        task.status = 'completed'
        task.status_version += 1
        task.lease_until = timezone.now()
        task.save(update_fields=['status', 'status_version', 'lease_until'])
        finish_attempt(attempt, TaskAttempt.Status.COMPLETED, usage_evidence={'provider_called': False,
            'stage_file_id': str(derived.id), 'input_bytes': len(original_bytes), 'output_bytes': len(data), 'processing_version': processing_version})
        record_receipt('purpose_copy', event, copy.id)
