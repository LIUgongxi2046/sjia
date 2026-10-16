import json
from datetime import timedelta
from decimal import Decimal, localcontext
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from api.base import ApiError
from archives.batches import batch_status, page_sources, page_task
from archives.models import ArchiveBatch, ArchivePage, ArchivePageAttempt
from archives.page_actions import action_scope
from archives.page_locks import lock_task_page
from archives.budget import batch_budget_status
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from files.model_input import model_copy_input, model_copy_ref
from files.models import FileObject
from files.purpose import checked_bytes
from files.service import object_store
from files.storage import build_object_key, digest_bytes
from recognition.archive_extraction import SCHEMA_VERSION, parse_archive_result, recognize_archive_with_evidence
from recognition.service import create_candidate_set
from recognition.invocation import (contract_snapshot, provider_timeout_seconds,
    actual_cost as invocation_cost, failure_state)
from recognition.invocation_runtime import provider_marker, lock_receipt_identity
from tasks.lease import lease_heartbeat
from archives.fact_candidates import save_extraction
from tasks.models import ConsumerReceipt, Task, TaskAttempt
from tasks.outbox import record_receipt
from tasks.service import acquire_lease, create_attempt, finish_attempt


def execution_scope(task, page):
    refs = task.input_refs
    if refs.get('invocation_contract') != contract_snapshot('archive'):
        raise ApiError('MODEL_INVOCATION_CONTRACT_CHANGED', 409)
    if refs['environment'] != settings.SJIA_ENVIRONMENT or refs['auth_epoch'] != task.actor.auth_epoch:
        raise ApiError('ARCHIVE_PAGE_AUTHORIZATION_CHANGED', 403)
    _, patient, batch, selected, quota, runtime, context = action_scope(task.actor, refs['auth_epoch'],
        'archive_batch_process', page.batch_id,
        {'expected_version': '1', 'page_ids': [str(page.id)]}, mutating=False)
    if (quota.id != ConfigVersion.objects.get(id=refs['quota_id']).id
            or context['configuration'] != refs['scope']['configuration']
            or context['gate_version'] != refs['scope']['gate_version']):
        raise ApiError('ARCHIVE_PAGE_CONFIGURATION_CHANGED', 409)
    expected = next(value for value in refs['scope']['sources'] if value['page_id'] == str(page.id))
    if context['sources'] != [expected]:
        raise ApiError('ARCHIVE_PAGE_SOURCE_CHANGED', 409)
    saved_grants = refs['scope']['grants']
    if any(value not in saved_grants for value in context['grants']):
        raise ApiError('ARCHIVE_PAGE_AUTHORIZATION_CHANGED', 403)
    provider = ConfigVersion.objects.get(id=refs['provider_config_id'])
    entries = [entry for entry in runtime.payload['providers']
        if entry['kind'] == 'ocr' and entry['enabled'] and entry['config_id'] == str(provider.id)]
    if len(entries) != 1 or entries[0] != refs.get('provider_entry'):
        raise ApiError('ARCHIVE_PROVIDER_RUNTIME_CHANGED', 409)
    field_config = ConfigVersion.objects.get(id=provider.payload['field_config_id'], kind='content')
    if configuration_qualification_reason(field_config, settings.SJIA_ENVIRONMENT) is not None:
        raise ApiError('ARCHIVE_FIELD_CONFIGURATION_PENDING', 503)
    if (provider.payload['model_version'] != refs['model_version']
            or quota.payload['model_version'] != refs['model_version']
            or provider.payload['currency'] != quota.payload['currency']):
        raise ApiError('ARCHIVE_PAGE_MODEL_CHANGED', 409)
    current_copy = model_copy_ref(original_file_id=page.file_id, file_page_id=page.file_page_id, actor=task.actor,
        auth_epoch=refs['auth_epoch'], target=provider.payload['endpoint'].rstrip('/'))
    if current_copy != refs['purpose_copy']:
        raise ApiError('ARCHIVE_PAGE_PURPOSE_CHANGED', 409)
    return quota, provider


def update_batch(batch_id):
    batch = ArchiveBatch.objects.select_for_update().get(id=batch_id)
    pages = list(ArchivePage.objects.filter(batch_id=batch.id))
    batch.status = batch_status(pages)
    attempts = list(ArchivePageAttempt.objects.filter(page_id__in=[page.id for page in pages]))
    batch.budget_status = batch_budget_status(pages, attempts)
    batch.version += 1
    batch.save(update_fields=['status', 'budget_status', 'version'])


def actual_cost(result, provider):
    return invocation_cost(result, provider)


def lock_receipt_attempts(task_id, attempt_id, page_attempt_id):
    # FileObject inserts need the patient FK lock. Match cancel's complete prefix,
    # without reauthorizing: even revoked/cancelled calls need their receipt saved.
    seed = Task.objects.get(id=task_id)
    lock_receipt_identity(seed)
    lock_task_page(seed)
    Task.objects.select_for_update().get(id=task_id)
    attempt = TaskAttempt.objects.select_for_update().get(id=attempt_id, task_id=task_id)
    page_attempt = ArchivePageAttempt.objects.select_for_update().get(
        id=page_attempt_id, task_id=task_id, task_attempt_id=attempt_id)
    return attempt, page_attempt


def execute_page(event, owner):
    with transaction.atomic():
        seed = Task.objects.get(id=event.task_id, schema_version='archive-page-1')
        lock_receipt_identity(seed)
        page = lock_task_page(seed)
        task = Task.objects.select_for_update().get(id=seed.id)
        page_task(page, task.patient, task.id)
        if ConsumerReceipt.objects.filter(consumer='archive_page', event=event).exists():
            return
        if task.status in ('awaiting_confirmation', 'completed', 'failed', 'uncertain', 'cancelled', 'superseded'):
            record_receipt('archive_page', event, page.id)
            return
        if page.current_task_id != task.id or page.status != 'queued':
            raise ApiError('ARCHIVE_PAGE_EXECUTION_STATE_INVALID', 409)
        quota = ConfigVersion.objects.get(id=task.input_refs['quota_id'])
        if not acquire_lease(task.id, owner, min(task.deadline_at,
                timezone.now() + timedelta(seconds=quota.payload['lease_seconds']))):
            return
        task.refresh_from_db()
        attempt = create_attempt(task, task.lease_generation)
        attempt.usage_evidence = {'phase': 'pre_provider', 'provider_called': False}
        attempt.save(update_fields=['usage_evidence'])
        number = ArchivePageAttempt.objects.filter(page_id=page.id, page_version=page.version).count() + 1
        page_attempt = ArchivePageAttempt.objects.create(patient_id=task.patient_id, page_id=page.id,
            page_version=page.version, task_id=task.id, task_attempt_id=attempt.id, attempt_number=number,
            input_sha256=task.input_sha256, model_version=task.input_refs['model_version'], started_at=attempt.started_at,
            status='running')
        page.status, page.status_version = 'processing', page.status_version + 1
        page.save(update_fields=['status', 'status_version'])
    with transaction.atomic():
        if task.deadline_at <= timezone.now():
            raise ApiError('ARCHIVE_PAGE_DEADLINE_EXCEEDED', 409)
        if task.created_at + timedelta(seconds=quota.payload['queue_wait_seconds']) <= timezone.now():
            raise ApiError('ARCHIVE_PAGE_QUEUE_WAIT_EXCEEDED', 409)
        quota, provider = execution_scope(task, page)
        _, source, group, _ = page_sources(page, task.patient)
        checked_bytes(source)
    execution_until = min(task.deadline_at,
        attempt.started_at + timedelta(seconds=quota.payload['execution_seconds']))
    with lease_heartbeat(task.id, attempt.lease_generation, quota.payload, execution_until):
        with model_copy_input(original_file_id=page.file_id, actor=task.actor,
                auth_epoch=task.input_refs['auth_epoch'], reference=task.input_refs['purpose_copy']) as (data, media_type):
            result = recognize_archive_with_evidence(data, media_type, task.input_refs['model_version'],
                provider=provider, before_provider_call=provider_marker(task, attempt, owner, execution_until),
                timeout_seconds=provider_timeout_seconds(task, execution_until, task.input_refs['provider_entry']))
        # 外部回执在重新采用候选前保存，取消及撤权仍保留请求与费用。
        with transaction.atomic():
            attempt, page_attempt = lock_receipt_attempts(task.id, attempt.id, page_attempt.id)
            cost = None
            raw = result['raw_response']
            raw = raw.encode('utf-8') if isinstance(raw, str) else raw
            response_id = uuid4()
            key = build_object_key(task.patient_id, response_id, 'ocr_response')
            object_store.put_immutable(key, raw)
            response = FileObject.objects.create(id=response_id, patient=task.patient, file_group_id=group.id,
                object_key=key, object_version=digest_bytes(raw), checksum=digest_bytes(raw), size_bytes=len(raw),
                media_type='application/json', purpose='ocr_response', status='available',
                retention_policy=source.retention_policy, created_at=timezone.now())
            usage = {**result['invocation'], 'phase': 'provider_finished', 'provider_request_id': result['provider_request_id'],
                'model': result['model'], 'usage': result['usage'], 'pricing_evidence_ref': provider.payload['pricing_evidence_ref'],
                'provider_config_id': str(provider.id), 'provider_checksum': provider.checksum,
                'raw_response_file_id': str(response.id), 'raw_response_sha256': response.checksum,
                'currency': provider.payload['currency'], 'actual_cost': None, 'cost_status': 'not_measured'}
            now = timezone.now()
            attempt.provider_request_id, attempt.usage_evidence = result['provider_request_id'], usage
            attempt.save(update_fields=['provider_request_id', 'usage_evidence'])
            page_attempt.provider_request_id, page_attempt.usage_evidence = result['provider_request_id'], usage
            page_attempt.cost, page_attempt.currency = cost, provider.payload['currency'] if cost is not None else None
            page_attempt.finished_at = now
            page_attempt.duration_ms = int((now - page_attempt.started_at).total_seconds() * 1000)
            page_attempt.save(update_fields=['provider_request_id', 'usage_evidence', 'cost', 'currency', 'finished_at', 'duration_ms'])
        cost = actual_cost(result, provider)
        if cost is not None:
            usage.update(actual_cost=str(cost), cost_status='measured')
            with transaction.atomic():
                TaskAttempt.objects.filter(id=attempt.id).update(usage_evidence=usage)
                ArchivePageAttempt.objects.filter(id=page_attempt.id).update(
                    cost=cost, currency=provider.payload['currency'], usage_evidence=usage)
    result['candidates'] = parse_archive_result(result, provider)
    with transaction.atomic():
        execution_scope(task, page)
        page = ArchivePage.objects.select_for_update().get(id=page.id)
        task = Task.objects.select_for_update().get(id=task.id)
        if task.status == 'cancel_requested' or page.status == 'cancel_requested':
            page.status, task.status = 'cancelled', 'cancelled'
            page.status_version, task.status_version = page.status_version + 1, task.status_version + 1
            page.save(update_fields=['status', 'status_version'])
            task.save(update_fields=['status', 'status_version'])
            finish_attempt(attempt, 'cancelled', usage_evidence=usage)
            page_attempt.status = 'cancelled'
            page_attempt.save(update_fields=['status'])
            update_batch(page.batch_id)
            record_receipt('archive_page', event, page.id)
            return
        if (page.current_task_id != task.id or page.status != 'processing'
                or task.status != 'running' or task.lease_generation != attempt.lease_generation
                or task.lease_owner != owner or task.lease_until <= timezone.now() or task.deadline_at <= timezone.now()):
            raise ApiError('ARCHIVE_PAGE_EXECUTION_EXPIRED', 409)
        if attempt.started_at + timedelta(seconds=quota.payload['execution_seconds']) <= timezone.now():
            raise ApiError('ARCHIVE_PAGE_EXECUTION_TIMEOUT', 409)
        candidate = create_candidate_set(patient=task.patient, file_group_id=group.id, file_group_version=group.version,
            task_id=task.id, provider='ark', model_version=task.input_refs['model_version'],
            provider_request_id=result['provider_request_id'], schema_version=SCHEMA_VERSION,
            raw_response_file_id=response.id)
        field_config = ConfigVersion.objects.get(id=provider.payload['field_config_id'])
        save_extraction(candidate, page, result['candidates'], field_config)
        page.status, page.status_version = 'awaiting_confirmation', page.status_version + 1
        task.status, task.status_version, task.lease_until = 'awaiting_confirmation', task.status_version + 1, timezone.now()
        page.save(update_fields=['status', 'status_version'])
        task.save(update_fields=['status', 'status_version', 'lease_until'])
        page_attempt.candidate_set_id, page_attempt.status = candidate.id, 'completed'
        page_attempt.save(update_fields=['candidate_set_id', 'status'])
        finish_attempt(attempt, 'completed', usage_evidence=usage)
        update_batch(page.batch_id)
        record_receipt('archive_page', event, candidate.id)


@transaction.atomic
def record_page_failure(event, owner, error):
    seed = Task.objects.get(id=event.task_id, schema_version='archive-page-1')
    page = lock_task_page(seed)
    task = Task.objects.select_for_update().get(id=seed.id)
    # A late failure must never overwrite a cancelled page or a newer retry.
    if page.current_task_id != task.id or task.status not in ('running', 'cancel_requested'):
        return
    attempt = TaskAttempt.objects.select_for_update().filter(task=task, lease_generation=task.lease_generation,
        status='running').first()
    if task.lease_owner != owner or attempt is None:
        return
    page_attempt = ArchivePageAttempt.objects.select_for_update().get(task_attempt_id=attempt.id)
    state = failure_state(attempt.usage_evidence or {}, task.status == 'cancel_requested')
    task.status, task.status_version, task.lease_until = state, task.status_version + 1, timezone.now()
    page.status, page.status_version = state, page.status_version + 1
    task.save(update_fields=['status', 'status_version', 'lease_until'])
    page.save(update_fields=['status', 'status_version'])
    finish_attempt(attempt, state, error_code=error, usage_evidence=attempt.usage_evidence)
    page_attempt.status, page_attempt.error_code = state, error
    page_attempt.usage_evidence = attempt.usage_evidence
    if page_attempt.finished_at is None:
        page_attempt.finished_at = attempt.finished_at
        page_attempt.duration_ms = int((attempt.finished_at - attempt.started_at).total_seconds() * 1000)
    page_attempt.save(update_fields=['status', 'error_code', 'finished_at', 'duration_ms', 'usage_evidence'])
    update_batch(page.batch_id)
    record_receipt('archive_page', event, page.id)


def reconcile_expired_pages():
    now = timezone.now()
    expired = Task.objects.filter(schema_version='archive-page-1', status__in=['running', 'cancel_requested'],
        lease_until__lte=now).order_by('id')
    for task in expired:
        event = ConsumerReceipt.objects.filter(event__task_id=task.id, consumer='archive_page').exists()
        if not event:
            from tasks.models import OutboxEvent

            saved = OutboxEvent.objects.get(task=task, event_type='archive_page.requested')
            record_page_failure(saved, task.lease_owner, 'ARCHIVE_PAGE_EXECUTION_LEASE_EXPIRED')
