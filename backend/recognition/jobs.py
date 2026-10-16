import json
import uuid
from typing import Annotated
from datetime import timedelta

from pydantic import Field, RootModel, TypeAdapter

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from authorization.policy import evaluate
from configurations.models import ConfigVersion
from files.models import FileGroup, FileObject, FilePage
from files.service import object_store
from files.storage import build_object_key, digest_bytes
from files.model_input import model_copy_input, model_copy_ref
from recognition.ark import recognize_report_with_evidence
from recognition.models import CandidateSet
from recognition.schemas import REPORT_SCHEMA_VERSION, ReportNumeric, ReportCandidates
from recognition.report_times import report_candidate_model, report_schema_version
from recognition.invocation import checked_candidates, provider_timeout_seconds, failure_state
from recognition.invocation_runtime import (admit_invocation, validate_invocation, provider_marker,
    persist_call_receipt, record_invocation_failure, require_authority, lock_receipt_identity)
from family_content.models import EnvironmentGate
from tasks.lease import lease_heartbeat
from recognition.service import RecognitionRejected, add_candidate_field, add_source_anchor, create_candidate_set
from tasks.models import ConsumerReceipt, Task, TaskAttempt
from tasks.outbox import emit_event, record_receipt
from tasks.service import acquire_lease, create_attempt, finish_attempt, register_task


REPORT_VALUE = TypeAdapter(Annotated[ReportNumeric | RootModel[str], Field(union_mode='left_to_right')])


def _files(group, version):
    if group.status != FileGroup.Status.AVAILABLE:
        raise RecognitionRejected('FILE_GROUP_NOT_AVAILABLE')
    if str(group.version) != str(version):
        raise RecognitionRejected('VERSION_CONFLICT')
    files = {str(item.id): item for item in FileObject.objects.filter(
        file_group_id=group.id, patient=group.patient, purpose=FileObject.Purpose.ORIGINAL)}
    pages = list(FilePage.objects.filter(file_group_id=group.id, patient=group.patient).order_by('ordinal'))
    if not pages or set(files) != {str(page.file_id) for page in pages}:
        raise RecognitionRejected('FILE_GROUP_INCOMPLETE')
    if any(item.status != FileObject.Status.AVAILABLE for item in files.values()):
        raise RecognitionRejected('FILE_NOT_AVAILABLE')
    return [{'file_id': str(page.file_id), 'page_id': str(page.id), 'checksum': files[str(page.file_id)].checksum,
             'ordinal': page.ordinal} for page in pages]


def _authorize(task):
    task.actor.refresh_from_db()
    task.patient.refresh_from_db()
    for action in ('write_record', 'read_file'):
        evaluate(principal=task.actor, patient=task.patient, action=action, purpose='family_observation',
                 environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=task.input_refs['auth_epoch'])


@transaction.atomic
def submit_recognition(*, group, actor, version, retry_of=None):
    EnvironmentGate.objects.select_for_update().filter(environment=settings.SJIA_ENVIRONMENT).first()
    group = FileGroup.objects.select_for_update().get(id=group.id)
    files = _files(group, version)
    existing = Task.objects.filter(actor=actor, task_type=Task.TaskType.OCR, schema_version='recognition-1',
        input_refs__file_group_id=str(group.id), input_refs__file_group_version=str(group.version))
    existing = existing.filter(input_refs__retry_of=str(retry_of.id)) if retry_of else existing.filter(Q(input_refs__retry_of=None) | Q(input_refs__retry_of__isnull=True))
    existing = existing.order_by('created_at').first()
    if existing is not None:
        return existing
    provider, governance = admit_invocation(kind='report', field_kind='report', call_count=len(files))
    copies = {source['file_id']: model_copy_ref(original_file_id=source['file_id'], actor=actor,
        auth_epoch=actor.auth_epoch, target=provider.payload['endpoint'].rstrip('/')) for source in files}
    task = register_task(actor=actor, patient=group.patient, task_type=Task.TaskType.OCR,
        schema_version='recognition-1', deadline_seconds=governance['task_config']['queue_wait_seconds'] + governance['task_config']['execution_seconds'], input_refs={**governance,
            'file_group_id': str(group.id), 'file_group_version': str(group.version), 'files': files,
            'model': provider.payload['model_version'], 'auth_epoch': actor.auth_epoch,
            'retry_of': str(retry_of.id) if retry_of else None,
            'purpose_copies': copies,
        })
    emit_event(event_type='recognition.requested', schema_version='recognition-1', aggregate_id=group.id,
        aggregate_version=group.version, correlation_id=task.id, resource_refs={'task_id': str(task.id)}, task=task)
    return task


def execute_recognition(event, owner):
    try:
        return _execute_recognition(event, owner)
    except Exception as exc:
        record_invocation_failure(event.task_id, owner, getattr(exc, 'reason_code', 'RECOGNITION_FAILED'))
        with transaction.atomic():
            if not ConsumerReceipt.objects.filter(consumer='recognition', event=event).exists():
                record_receipt('recognition', event, event.task_id)
        raise


def _execute_recognition(event, owner):
    with transaction.atomic():
        task = Task.objects.select_for_update().get(id=event.task_id)
        if ConsumerReceipt.objects.filter(consumer='recognition', event=event).exists():
            return
        if task.status in (Task.Status.AWAITING_CONFIRMATION, Task.Status.COMPLETED, Task.Status.FAILED, Task.Status.CANCELLED, Task.Status.SUPERSEDED, Task.Status.UNCERTAIN, Task.Status.AWAITING_RESOLUTION):
            record_receipt('recognition', event, task.id)
            return
        if task.deadline_at <= timezone.now():
            raise RecognitionRejected('TASK_DEADLINE_EXCEEDED')
        policy = task.input_refs.get('task_config')
        if policy is None:
            raise RecognitionRejected('MODEL_GOVERNED_INVOCATION_REQUIRED')
        if not acquire_lease(task.id, owner, min(task.deadline_at,
                timezone.now() + timedelta(seconds=policy['lease_seconds']))):
            return
        task.refresh_from_db()
        attempt = create_attempt(task, task.lease_generation)
        attempt.usage_evidence = {'phase': 'pre_provider', 'provider_called': False, 'provider_calls': []}
        attempt.save(update_fields=['usage_evidence'])
    if task.deadline_at <= timezone.now():
        raise RecognitionRejected('TASK_DEADLINE_EXCEEDED')
    provider = validate_invocation(task, 'report')
    execution_until = min(task.deadline_at, attempt.started_at + timedelta(seconds=policy['execution_seconds']))
    if task.created_at + timedelta(seconds=policy['queue_wait_seconds']) <= timezone.now():
        raise RecognitionRejected('MODEL_QUEUE_WAIT_EXCEEDED')
    _authorize(task)
    group = FileGroup.objects.get(id=task.input_refs['file_group_id'], patient=task.patient)
    if _files(group, task.input_refs['file_group_version']) != task.input_refs['files']:
        raise RecognitionRejected('FILE_INPUT_CHANGED')
    outputs = []
    for call_index, source in enumerate(task.input_refs['files']):
        task.refresh_from_db()
        attempt.refresh_from_db()
        require_authority(task, attempt, owner, execution_until)
        provider = validate_invocation(task, 'report')
        _authorize(task)
        file = FileObject.objects.get(id=source['file_id'], patient=task.patient)
        if 'purpose_copies' not in task.input_refs or source['file_id'] not in task.input_refs['purpose_copies']:
            raise RecognitionRejected('PURPOSE_REVIEW_REQUIRED')
        with model_copy_input(original_file_id=file.id, actor=task.actor, auth_epoch=task.input_refs['auth_epoch'],
                reference=task.input_refs['purpose_copies'][source['file_id']]) as (data, media_type):
            with lease_heartbeat(task.id, attempt.lease_generation, policy, execution_until):
                result = recognize_report_with_evidence(data, media_type, task.input_refs['model'],
                    provider=provider,
                    before_provider_call=provider_marker(task, attempt, owner, execution_until, call_index=call_index),
                    timeout_seconds=provider_timeout_seconds(task, execution_until, task.input_refs['provider_entry']))
                persist_call_receipt(task=task, attempt=attempt, original=file,
                    result=result, provider=provider, call_index=call_index)
        result['candidates'] = checked_candidates(result, provider, report_candidate_model(provider))
        outputs.append((source, result, file))
    if not any(result['candidates']['items'] for _, result, _ in outputs):
        raise RecognitionRejected('OCR_NO_ITEMS')
    with transaction.atomic():
        lock_receipt_identity(task)
        group = FileGroup.objects.select_for_update().get(id=group.id)
        task = Task.objects.select_for_update().get(id=task.id)
        if task.status != Task.Status.RUNNING or task.lease_generation != attempt.lease_generation:
            finish_attempt(attempt, TaskAttempt.Status.SUPERSEDED if task.status == Task.Status.SUPERSEDED else TaskAttempt.Status.CANCELLED)
            record_receipt('recognition', event, task.id)
            return
        if group.status != FileGroup.Status.AVAILABLE or str(group.version) != task.input_refs['file_group_version']:
            task.status = Task.Status.SUPERSEDED
            task.status_version += 1
            task.save(update_fields=['status', 'status_version'])
            finish_attempt(attempt, TaskAttempt.Status.SUPERSEDED)
            record_receipt('recognition', event, task.id)
            return
        _authorize(task)
        for source in task.input_refs['files']:
            current_copy = model_copy_ref(original_file_id=source['file_id'], actor=task.actor,
                auth_epoch=task.input_refs['auth_epoch'], target=provider.payload['endpoint'].rstrip('/'))
            if current_copy != task.input_refs['purpose_copies'][source['file_id']]:
                raise RecognitionRejected('PURPOSE_INPUT_CHANGED')
        attempt.refresh_from_db()
        require_authority(task, attempt, owner, execution_until)
        validate_invocation(task, 'report')
        if _files(group, group.version) != task.input_refs['files']:
            raise RecognitionRejected('FILE_INPUT_CHANGED')
        field_config = ConfigVersion.objects.get(id=task.input_refs['field_config_id'], status='enabled')
        raw = json.dumps([result['raw_response'].decode('utf-8') if isinstance(result['raw_response'], bytes) else result['raw_response'] for _, result, _ in outputs], ensure_ascii=False).encode('utf-8')
        response_id = uuid.uuid4()
        object_key = build_object_key(task.patient_id, response_id, 'ocr_response')
        object_store.put_immutable(object_key, raw)
        response_file = FileObject.objects.create(id=response_id, patient=task.patient, file_group_id=group.id,
            object_key=object_key, object_version=digest_bytes(raw), checksum=digest_bytes(raw), size_bytes=len(raw),
            media_type='application/json', purpose=FileObject.Purpose.OCR_RESPONSE, status=FileObject.Status.AVAILABLE,
            retention_policy=outputs[0][2].retention_policy, created_at=timezone.now())
        request_ids = [result['provider_request_id'] for _, result, _ in outputs]
        models = sorted({result['model'] for _, result, _ in outputs})
        candidate_set = create_candidate_set(patient=task.patient, file_group_id=group.id,
            file_group_version=group.version, task_id=task.id, provider='ark', model_version=json.dumps(models),
            provider_request_id=json.dumps(request_ids), schema_version=report_schema_version(provider), raw_response_file_id=response_file.id)
        index = 0
        for source, result, _ in outputs:
            for item in result['candidates']['items']:
                index += 1
                original = item['source_text']
                anchor = add_source_anchor(patient=task.patient, page_id=source['page_id'], original_text=original)
                numeric = item['value'] is not None and isinstance(REPORT_VALUE.validate_python(item['value']), ReportNumeric)
                proposed = {'field_code': 'report_item', 'original_name': item['name'],
                    'value_type': 'decimal' if numeric else 'text', 'value_state': 'unknown' if item['value'] is None else 'known',
                    'raw_text': None if item['value'] is None else str(item['value']),
                    'raw_numeric': str(item['value']) if numeric else None, 'raw_unit': item['unit'],
                    'qualifier': {'=': 'eq', '<': 'lt', '<=': 'le', '≤': 'le', '>': 'gt', '>=': 'ge', '≥': 'ge', None: None}[item['comparator']],
                    'reference_interval': item['reference'], 'method': item['method'], 'sample_time_origin': 'unknown'}
                if report_schema_version(provider) == 'report-recognition-3':
                    # Review-only metadata. Confirmation strips this container;
                    # it never becomes a formal medical_time automatically.
                    proposed['recognition_time_candidates'] = {
                        'item_sample_time': item['sample_time'],
                        'report_sample_time': result['candidates']['sample_time'],
                        'report_result_time': result['candidates']['result_time'],
                        'report_source_text': result['candidates']['source_text']}
                add_candidate_field(candidate_set=candidate_set, field_path=f'/fields/{index}', field_config_version=field_config,
                    recognition_status='uncertain' if item['value'] is None else 'recognized', raw_text=original,
                    proposed_field=proposed, anchor_id=anchor.id)
        task.status = Task.Status.AWAITING_CONFIRMATION
        task.status_version += 1
        task.lease_until = timezone.now()
        task.save(update_fields=['status', 'status_version', 'lease_until'])
        attempt.provider_request_id = json.dumps(request_ids)
        attempt.save(update_fields=['provider_request_id'])
        finish_attempt(attempt, TaskAttempt.Status.COMPLETED, usage_evidence=attempt.usage_evidence)
        record_receipt('recognition', event, candidate_set.id)


def recognition_result(task):
    result = CandidateSet.objects.filter(task_id=task.id, patient=task.patient).first()
    attempt = TaskAttempt.objects.filter(task=task).order_by('-attempt_number').first()
    return {'result_type': 'candidate_set' if result else None,
            'result_ref': str(result.id) if result else None,
            'error_code': attempt.error_code if attempt else None}


@transaction.atomic
def retry_recognition(task, actor):
    if task.schema_version != 'recognition-1':
        raise RecognitionRejected('TASK_NOT_RETRYABLE')
    EnvironmentGate.objects.select_for_update().filter(environment=settings.SJIA_ENVIRONMENT).first()
    group = FileGroup.objects.select_for_update().get(id=task.input_refs['file_group_id'], patient=task.patient)
    task = Task.objects.select_for_update().get(id=task.id, actor=actor)
    if task.status not in (Task.Status.FAILED, Task.Status.CANCELLED, Task.Status.SUPERSEDED):
        raise RecognitionRejected('TASK_NOT_RETRYABLE')
    if any(failure_state(attempt.usage_evidence or {}) == 'uncertain'
            for attempt in TaskAttempt.objects.filter(task=task)):
        raise RecognitionRejected('PROVIDER_RESULT_RECONCILIATION_REQUIRED')
    _authorize(task)
    return submit_recognition(group=group, actor=actor, version=task.input_refs['file_group_version'], retry_of=task)
