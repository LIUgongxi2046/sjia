from recognition.invocation import contract_snapshot
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import F
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from api.base import ApiError
from archives.batch_creation import batch_write_identity, locked_grants, receiving_quota
from archives.batches import batch_status, page_sources, page_record_scope
from archives.inputs import PROTOCOL, REGISTRY
from archives.models import ArchiveBatch, ArchivePage, ArchivePageAttempt
from archives.budget import INFLIGHT, batch_budget_status, budget_cost
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from files.model_input import model_copy_ref
from identity.models import Patient
from family_content.gate_locks import read_environment_gate_shared
from recognition.provider_budget import check_provider_budget
from tasks.models import Task
from tasks.outbox import emit_event
from tasks.service import register_task


OPERATIONS = {'archive_batch_process': 'BatchProcessInput', 'archive_page_retry': 'PageActionInput',
    'archive_page_cancel': 'PageActionInput'}


def action_input(operation, payload):
    validator = Draft202012Validator({**PROTOCOL, '$ref': f'#/$defs/{OPERATIONS[operation]}'},
        registry=REGISTRY, format_checker=FormatChecker())
    if not validator.is_valid(payload):
        raise ApiError('ARCHIVE_PAGE_ACTION_INPUT_INVALID', 422)
    return payload


def action_scope(actor, epoch, operation, identifier, payload, *, mutating):
    action_input(operation, payload)
    if operation == 'archive_batch_process':
        seed = ArchiveBatch.objects.get(id=identifier)
    else:
        seed = ArchiveBatch.objects.get(id=ArchivePage.objects.get(id=identifier).batch_id)
    actor, patient, gate = batch_write_identity(actor, epoch, seed.patient_id)
    batch = ArchiveBatch.objects.select_for_update().get(id=seed.id, patient_id=patient.id)
    pages = list(ArchivePage.objects.select_for_update().filter(batch_id=batch.id,
        patient_id=patient.id).order_by('ordinal'))
    selected_ids = payload['page_ids'] if operation == 'archive_batch_process' else [str(identifier)]
    selected = [page for page in pages if str(page.id) in selected_ids]
    if len(selected) != len(selected_ids) or not selected:
        raise ApiError('ARCHIVE_PAGE_ACTION_SCOPE_INVALID', 409)
    proofs = locked_grants(actor, patient, 'write_record', author_id=batch.submitted_by)
    sources, copies = [], {}
    for page in selected:
        _, source, group, _ = page_sources(page, patient)
        if source.status != 'available' or group.status != 'available':
            raise ApiError('ARCHIVE_PAGE_SOURCE_UNAVAILABLE', 409)
        record_id = page_record_scope(page, patient)
        proofs.extend(locked_grants(actor, patient, 'read_raw', author_id=batch.submitted_by, record_id=record_id))
        proofs.extend(locked_grants(actor, patient, 'read_file', author_id=group.owner_id, record_id=record_id))
        sources.append(source)
    if batch.intake_mode == 'manual':
        raise ApiError('ARCHIVE_MANUAL_BATCH_ACTION_UNSUPPORTED', 409)
    quota, runtime, qualification = receiving_quota(gate, sources, selected)
    if mutating:
        if operation == 'archive_batch_process' and str(batch.version) != payload['expected_version']:
            raise ApiError('VERSION_CONFLICT', 409)
        if operation != 'archive_batch_process' and str(selected[0].status_version) != payload['expected_status_version']:
            raise ApiError('VERSION_CONFLICT', 409)
        for page in selected:
            if operation == 'archive_batch_process' and page.status != 'uploaded':
                raise ApiError('ARCHIVE_PAGE_NOT_PROCESSABLE', 409)
            if operation == 'archive_page_retry' and page.status != 'failed':
                raise ApiError('ARCHIVE_PAGE_NOT_RETRYABLE', 409)
            if operation == 'archive_page_cancel' and page.status not in ('uploaded', 'queued', 'processing'):
                raise ApiError('ARCHIVE_PAGE_NOT_CANCELLABLE', 409)
        if operation != 'archive_page_cancel':
            provider = ConfigVersion.objects.get(id=quota.payload['provider_config_id'])
            target = provider.payload['endpoint'].rstrip('/')
            if target != settings.ARK_BASE_URL.rstrip('/') or not settings.ARK_API_KEY:
                raise ApiError('ARCHIVE_PROVIDER_NOT_CONFIGURED', 503)
            field_config = ConfigVersion.objects.get(id=provider.payload['field_config_id'], kind='content')
            if configuration_qualification_reason(field_config, gate.environment) is not None:
                raise ApiError('ARCHIVE_FIELD_CONFIGURATION_PENDING', 503)
            for page in selected:
                copies[str(page.id)] = model_copy_ref(original_file_id=page.file_id, file_page_id=page.file_page_id,
                    actor=actor, auth_epoch=epoch, target=target)
            check_budget(quota, batch, selected)
    context = {'environment': gate.environment, 'gate_version': str(gate.version),
        'auth_epoch': str(actor.auth_epoch), 'patient_id': str(patient.id), 'configuration': qualification,
        'grants': sorted(proofs, key=lambda value: (value['grant_id'], value['author_id'], value['record_id'] or '')),
        'sources': [{'page_id': str(page.id), 'version': str(page.version), 'file_id': str(page.file_id),
            'file_object_version': page.file_object_version, 'checksum': page.checksum} for page in selected]}
    if copies:
        context['purpose_copies'] = copies
    return actor, patient, batch, selected, quota, runtime, context


def reserved_cost(task):
    return budget_cost(task)


def check_budget(quota, batch, pages):
    provider = ConfigVersion.objects.get(id=quota.payload['provider_config_id'])
    gate = read_environment_gate_shared(settings.SJIA_ENVIRONMENT)
    runtime = ConfigVersion.objects.select_for_update().get(id=gate.runtime_config_id,
        kind='provider', schema_version='backend-runtime-1', status='enabled')
    entries = [item for item in runtime.payload['providers'] if item['enabled']
        and item['kind'] == 'ocr' and item['config_id'] == str(provider.id)]
    if len(entries) != 1 or configuration_qualification_reason(runtime,
            settings.SJIA_ENVIRONMENT) is not None:
        raise ApiError('ARCHIVE_PROVIDER_RUNTIME_UNAVAILABLE', 409)
    entry = entries[0]
    if (entry['currency'] != quota.payload['currency']
            or Decimal(entry['daily_budget']) != Decimal(quota.payload['daily_budget'])):
        raise ApiError('ARCHIVE_BUDGET_CURRENCY_CHANGED', 409)
    task_policy = next(item for item in runtime.payload['tasks'] if item['task_type'] == 'ocr')
    check_provider_budget(runtime=runtime, billing_scope_key=entry['billing_scope_key'],
        currency=entry['currency'], daily_budget=entry['daily_budget'],
        reserved_cost=str(Decimal(quota.payload['per_page_reservation']) * len(pages)),
        budget_timezone=quota.payload['budget_timezone'], max_concurrency=task_policy['max_concurrency'],
        additional_tasks=len(pages), reject=lambda code: ApiError(code, 409))
    provider_versions = list(ConfigVersion.objects.filter(config_key=provider.config_key, kind=provider.kind))
    provider_ids = [str(row.id) for row in provider_versions]
    if any(row.payload.get('currency') != quota.payload['currency'] for row in provider_versions):
        raise ApiError('ARCHIVE_BUDGET_CURRENCY_CHANGED', 409)
    tasks = list(Task.objects.filter(schema_version='archive-page-1',
        input_refs__provider_config_id__in=provider_ids).order_by('id'))
    provider_tasks = [task for task in tasks if task.input_refs['environment'] == settings.SJIA_ENVIRONMENT]
    inflight = [task for task in provider_tasks if task.status in INFLIGHT]
    if len(inflight) + len(pages) > quota.payload['max_inflight_pages']:
        raise ApiError('ARCHIVE_PAGE_CONCURRENCY_EXCEEDED', 409)
    zone = ZoneInfo(quota.payload['budget_timezone'])
    today = timezone.now().astimezone(zone).date()
    with localcontext() as arithmetic:
        arithmetic.prec = 80
        additional = Decimal(quota.payload['per_page_reservation']) * len(pages)
        batch_cost = sum((reserved_cost(task) for task in provider_tasks
            if task.input_refs['batch_id'] == str(batch.id)), Decimal('0'))
        daily_cost = sum((reserved_cost(task) for task in provider_tasks
            if task.created_at.astimezone(zone).date() == today or task in inflight), Decimal('0'))
        if batch_cost + additional > Decimal(quota.payload['per_batch_budget']):
            raise ApiError('ARCHIVE_BATCH_BUDGET_EXCEEDED', 409)
        if daily_cost + additional > Decimal(quota.payload['daily_budget']):
            raise ApiError('ARCHIVE_DAILY_BUDGET_EXCEEDED', 409)
    for page in pages:
        if ArchivePageAttempt.objects.filter(page_id=page.id).count() >= quota.payload['max_attempts']:
            raise ApiError('ARCHIVE_PAGE_ATTEMPTS_EXCEEDED', 409)


def save_action(*, actor, patient, batch, pages, quota, context, request_row, operation):
    if batch.intake_mode == 'manual':
        raise ApiError('ARCHIVE_MANUAL_BATCH_ACTION_UNSUPPORTED', 409)
    provider = ConfigVersion.objects.get(id=quota.payload['provider_config_id'])
    provider_entry = None
    if operation != 'archive_page_cancel':
        runtime = ConfigVersion.objects.get(id=context['configuration']['runtime_id'])
        entries = [entry for entry in runtime.payload['providers']
            if entry['kind'] == 'ocr' and entry['enabled'] and entry['config_id'] == str(provider.id)]
        if len(entries) != 1:
            raise ApiError('ARCHIVE_PROVIDER_RUNTIME_UNAVAILABLE', 409)
        provider_entry = entries[0]
    for page in pages:
        if operation == 'archive_page_cancel':
            page.status = 'cancel_requested' if page.status == 'processing' else 'cancelled'
            if page.current_task_id:
                task = Task.objects.select_for_update().get(id=page.current_task_id)
                task.status, task.status_version = page.status, task.status_version + 1
                task.save(update_fields=['status', 'status_version'])
        else:
            _, source, group, _ = page_sources(page, patient)
            refs = {'archive_page_id': str(page.id), 'archive_page_version': str(page.version),
                'batch_id': str(batch.id), 'file_page_id': str(page.file_page_id), 'file_id': str(page.file_id),
                'file_object_version': page.file_object_version, 'original_checksum': page.checksum,
                'file_group_id': str(group.id), 'file_group_version': str(group.version),
                'environment': settings.SJIA_ENVIRONMENT, 'auth_epoch': actor.auth_epoch,
                'provider_config_id': str(provider.id), 'quota_id': str(quota.id),
                'model_version': quota.payload['model_version'], 'reserved_cost': quota.payload['per_page_reservation'],
                'invocation_contract': contract_snapshot('archive'), 'provider_entry': provider_entry,
                'purpose_copy': context['purpose_copies'][str(page.id)], 'scope': context}
            task = register_task(actor=actor, patient=patient, request_row=request_row, task_type='ocr',
                schema_version='archive-page-1', input_refs=refs, deadline_seconds=quota.payload['deadline_seconds'])
            emit_event(event_type='archive_page.requested', schema_version='archive-page-1',
                aggregate_id=page.id, aggregate_version=page.version, correlation_id=task.id,
                resource_refs={'task_id': str(task.id), 'page_id': str(page.id)}, task=task)
            page.current_task_id, page.status = task.id, 'queued'
        page.status_version += 1
        page.save(update_fields=['current_task_id', 'status', 'status_version'])
    batch.version += 1
    saved_pages = list(ArchivePage.objects.filter(batch_id=batch.id))
    batch.status = batch_status(saved_pages)
    attempts = list(ArchivePageAttempt.objects.filter(page_id__in=[page.id for page in saved_pages]))
    batch.budget_status = batch_budget_status(saved_pages, attempts)
    batch.save(update_fields=['version', 'status', 'budget_status'])
    Patient.objects.filter(id=patient.id).update(data_epoch=F('data_epoch') + 1)
