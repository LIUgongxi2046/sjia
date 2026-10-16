from collections import Counter
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from api.base import ApiError
from archives.models import Archive, ArchiveBatch, ArchiveFact, ArchiveFactVersion, ArchivePage, ArchivePageAttempt
from authorization.models import AccessGrant
from authorization.policy import matching_case_grants
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from family_content.models import EnvironmentGate
from files.models import FileGroup, FileObject, FilePage
from recognition.models import CandidateSet, SourceAnchor
from records.models import RecordField, RecordVersion
from tasks.models import Task, TaskAttempt


PAGE_STATES = ('declared', 'receiving', 'uploaded', 'queued', 'processing', 'awaiting_confirmation',
    'confirmed', 'archived', 'failed', 'uncertain', 'cancel_requested', 'cancelled', 'superseded')


def batch_runtime():
    if not settings.SJIA_ENVIRONMENT:
        raise ApiError('ENVIRONMENT_CONFIGURATION_REQUIRED', 503)
    gate = EnvironmentGate.objects.filter(environment=settings.SJIA_ENVIRONMENT).first()
    if gate is None or not gate.enabled:
        raise ApiError('ENVIRONMENT_DISABLED', 403)
    runtime = ConfigVersion.objects.filter(id=gate.runtime_config_id).first()
    if (runtime is None or runtime.kind != 'provider' or runtime.schema_version != 'backend-runtime-1'
            or configuration_qualification_reason(runtime, settings.SJIA_ENVIRONMENT) is not None):
        raise ApiError('RUNTIME_CONFIGURATION_PENDING', 503)
    return runtime


def page_sources(page, patient):
    if page.patient_id != patient.id:
        raise ApiError('ARCHIVE_PAGE_SOURCE_INVALID', 409)
    batch = ArchiveBatch.objects.get(id=page.batch_id, patient_id=patient.id)
    Archive.objects.get(id=batch.archive_id, patient_id=patient.id)
    file_page = FilePage.objects.get(id=page.file_page_id, patient=patient, file_id=page.file_id)
    source, group = file_page_source(file_page, patient)
    if source.object_version != page.file_object_version or source.checksum != page.checksum:
        raise ApiError('ARCHIVE_PAGE_SOURCE_INVALID', 409)
    return batch, source, group, file_page


def file_page_source(file_page, patient):
    if file_page.patient_id != patient.id:
        raise ApiError('ARCHIVE_PAGE_SOURCE_INVALID', 409)
    source = FileObject.objects.get(id=file_page.file_id, patient=patient)
    group = FileGroup.objects.get(id=file_page.file_group_id, patient=patient)
    if source.file_group_id != group.id or source.purpose != 'original':
        raise ApiError('ARCHIVE_PAGE_SOURCE_INVALID', 409)
    return source, group


def file_page_record_scope(file_page_id, patient):
    anchor_ids = SourceAnchor.objects.filter(page_id=file_page_id, patient=patient).values('id')
    version_ids = RecordField.objects.filter(anchor_id__in=anchor_ids, patient=patient).values('record_version_id')
    record_ids = set(RecordVersion.objects.filter(id__in=version_ids, patient=patient).values_list('record_id', flat=True))
    return next(iter(record_ids)) if len(record_ids) == 1 else None


def page_record_scope(page, patient):
    return file_page_record_scope(page.file_page_id, patient)


def source_grants(principal, patient, action, *, author_id, record_id=None):
    return [grant for grant in matching_case_grants(principal, patient, action, timezone.now(),
        author_id=author_id, record_id=record_id, require_relation=True, whole_resource=True)
        if grant.purpose == 'family_observation' and grant.evidence_ref.strip()]


def directory_grants(principal, patient):
    records = set(AccessGrant.objects.filter(principal=principal, patient=patient,
        action='read_raw', purpose='family_observation').values_list('assigned_record_id', flat=True))
    records.add(None)
    grants = {}
    for record_id in records:
        for grant in source_grants(principal, patient, 'read_raw', author_id=principal.id, record_id=record_id):
            grants[grant.id] = grant
    return list(grants.values())


def page_grants(principal, patient, page):
    batch, _, group, _ = page_sources(page, patient)
    record_id = page_record_scope(page, patient)
    raw = source_grants(principal, patient, 'read_raw', author_id=batch.submitted_by, record_id=record_id)
    files = source_grants(principal, patient, 'read_file', author_id=group.owner_id, record_id=record_id)
    return raw + files if raw and files else []


def batch_grants(principal, patient, batch, pages):
    if batch.patient_id != patient.id:
        raise ApiError('ARCHIVE_BATCH_SOURCE_INVALID', 409)
    Archive.objects.get(id=batch.archive_id, patient_id=patient.id)
    records = {page_record_scope(page, patient) for page in pages}
    record_id = next(iter(records)) if len(records) == 1 and None not in records else None
    grants = source_grants(principal, patient, 'read_raw', author_id=batch.submitted_by, record_id=record_id)
    if not grants:
        return []
    for page in pages:
        if page.batch_id != batch.id:
            raise ApiError('ARCHIVE_BATCH_SOURCE_INVALID', 409)
        proofs = page_grants(principal, patient, page)
        if not proofs:
            return []
        grants.extend(proofs)
    return grants


def page_task(page, patient, task_id):
    task = Task.objects.select_related('request_row').get(id=task_id, patient=patient)
    expected = {'archive_page_id': str(page.id), 'archive_page_version': str(page.version),
        'batch_id': str(page.batch_id), 'file_id': str(page.file_id),
        'file_object_version': page.file_object_version, 'original_checksum': page.checksum}
    if (task.task_type != 'ocr' or task.schema_version != 'archive-page-1'
            or any(task.input_refs.get(name) != value for name, value in expected.items())
            or task.input_sha256 != compute_checksum(task.input_refs)):
        raise ApiError('ARCHIVE_PAGE_TASK_INVALID', 409)
    return task


def page_attempt_read(attempt, page, patient):
    if attempt.patient_id != patient.id or attempt.page_id != page.id or attempt.page_version > page.version:
        raise ApiError('ARCHIVE_PAGE_ATTEMPT_INVALID', 409)
    task = Task.objects.select_related('request_row').get(id=attempt.task_id, patient=patient)
    task_attempt = TaskAttempt.objects.get(id=attempt.task_attempt_id, task=task)
    expected = {'archive_page_id': str(page.id), 'archive_page_version': str(attempt.page_version),
        'batch_id': str(page.batch_id)}
    if (task.task_type != 'ocr' or task.schema_version != 'archive-page-1'
            or any(task.input_refs.get(name) != value for name, value in expected.items())
            or attempt.input_sha256 != task.input_sha256 or compute_checksum(task.input_refs) != task.input_sha256
            or task.input_refs.get('model_version') != attempt.model_version
            or attempt.started_at != task_attempt.started_at
            or attempt.provider_request_id != task_attempt.provider_request_id):
        raise ApiError('ARCHIVE_PAGE_ATTEMPT_INVALID', 409)
    if attempt.cost is not None and (not Decimal(attempt.cost).is_finite() or attempt.cost < 0
            or not attempt.currency or not attempt.usage_evidence):
        raise ApiError('ARCHIVE_PAGE_COST_INVALID', 409)
    if attempt.candidate_set_id is not None:
        candidate = CandidateSet.objects.get(id=attempt.candidate_set_id, patient=patient, task_id=task.id)
        if (str(candidate.file_group_id) != task.input_refs.get('file_group_id')
                or str(candidate.file_group_version) != task.input_refs.get('file_group_version')
                or candidate.model_version != attempt.model_version
                or candidate.provider_request_id != attempt.provider_request_id):
            raise ApiError('ARCHIVE_PAGE_CANDIDATE_INVALID', 409)
    return {'attempt_id': str(attempt.id), 'page_version': str(attempt.page_version),
        'task_id': str(task.id), 'task_attempt_id': str(task_attempt.id),
        'request_id': str(task.request_row.request_id) if task.request_row_id is not None else None,
        'attempt_number': attempt.attempt_number, 'input_sha256': attempt.input_sha256,
        'model_version': attempt.model_version,
        'candidate_set_id': str(attempt.candidate_set_id) if attempt.candidate_set_id is not None else None,
        'provider_request_id': attempt.provider_request_id, 'started_at': attempt.started_at.isoformat(),
        'finished_at': attempt.finished_at.isoformat() if attempt.finished_at is not None else None,
        'duration_ms': attempt.duration_ms, 'cost': format(attempt.cost, 'f') if attempt.cost is not None else None,
        'currency': attempt.currency,
        'pricing_scope': 'simulation' if task.input_refs.get('environment') == 'simulation' else 'unknown',
        'usage_evidence_sha256': compute_checksum(attempt.usage_evidence) if attempt.usage_evidence is not None else None,
        'status': attempt.status, 'error_code': attempt.error_code}


def page_read(page, patient, principal=None):
    _, source, _, _ = page_sources(page, patient)
    attempts = [page_attempt_read(row, page, patient) for row in
        ArchivePageAttempt.objects.filter(page_id=page.id, patient_id=patient.id).order_by('page_version', 'attempt_number', 'id')]
    current = [item for item in attempts if item['page_version'] == str(page.version)
        and item['task_id'] == str(page.current_task_id)]
    latest = current[-1] if current else None
    candidate_id = (latest['candidate_set_id'] if latest is not None and latest['status'] == 'completed'
        and page.status in ('awaiting_confirmation', 'confirmed', 'archived') else None)
    task_view = None
    if page.current_task_id is not None:
        task = page_task(page, patient, page.current_task_id)
        terminal = task.status in ('completed', 'failed', 'cancelled', 'superseded', 'awaiting_confirmation')
        task_view = {'task_id': str(task.id), 'task_type': task.task_type,
            'request_id': str(task.request_row.request_id) if task.request_row_id is not None else None,
            'accepted_at': task.created_at.isoformat(), 'status': task.status, 'status_version': str(task.status_version),
            'poll_after_ms': None if terminal else batch_runtime().payload['api']['poll_after_ms'],
            'allowed_actions': [], 'result_type': 'candidate_set' if candidate_id is not None else None,
            'result_ref': candidate_id, 'error_code': latest['error_code'] if latest is not None else None}
    if page.status == 'awaiting_confirmation' and page.current_task_id is not None and candidate_id is None:
        raise ApiError('ARCHIVE_PAGE_CANDIDATE_INVALID', 409)
    actions = []
    batch = ArchiveBatch.objects.get(id=page.batch_id)
    if batch.intake_mode != 'manual' and principal is not None and source_grants(principal, patient, 'write_record', author_id=batch.submitted_by):
        if page.status in ('uploaded', 'queued', 'processing'):
            actions.append('archive_page_cancel')
        if page.status == 'failed':
            actions.append('archive_page_retry')
    return {'page_id': str(page.id), 'batch_id': str(page.batch_id), 'file_page_id': str(page.file_page_id),
        'file_id': str(page.file_id), 'file_object_version': page.file_object_version, 'checksum': page.checksum,
        'file_status': source.status, 'version': str(page.version), 'status_version': str(page.status_version),
        'status': page.status, 'task': task_view, 'candidate_set_id': candidate_id,
        'error_code': latest['error_code'] if latest is not None else None, 'attempts': attempts, 'allowed_actions': actions}


def batch_status(pages):
    states = {page.status for page in pages}
    if not states or states <= {'declared', 'receiving', 'uploaded'}:
        return 'receiving'
    if states <= {'queued', 'processing', 'cancel_requested'}:
        return 'processing'
    if states <= {'awaiting_confirmation', 'confirmed'}:
        return 'awaiting_confirmation'
    if states == {'archived'}:
        return 'archived'
    if states == {'cancelled'}:
        return 'cancelled'
    if states == {'failed'}:
        return 'failed'
    return 'partial'


def batch_read(batch, patient, pages, principal=None):
    if [page.ordinal for page in pages] != list(range(1, len(pages) + 1)):
        raise ApiError('ARCHIVE_PAGE_ORDER_INVALID', 409)
    statuses = Counter(page.status for page in pages)
    if set(statuses) - set(PAGE_STATES):
        raise ApiError('ARCHIVE_PAGE_STATE_INVALID', 409)
    page_ids = [page.id for page in pages]
    current_facts = ArchiveFact.objects.filter(patient_id=patient.id)
    facts = sum(ArchiveFactVersion.objects.filter(fact_id=fact.id, patient_id=patient.id,
        version=fact.current_version, page_id__in=page_ids).count() for fact in current_facts)
    counts = {state: statuses[state] for state in PAGE_STATES}
    counts.update(originals=len({page.file_id for page in pages}), pages=len(pages), facts=facts)
    return {'batch_id': str(batch.id), 'patient_id': str(patient.id), 'version': str(batch.version),
        'intake_mode': batch.intake_mode,
        'status': batch_status(pages), 'counts': counts, 'received_at': batch.received_at.isoformat(),
        'budget_status': batch.budget_status, 'pages': [page_read(page, patient, principal) for page in pages],
        'allowed_actions': ['archive_batch_process'] if batch.intake_mode != 'manual' and principal is not None
            and any(page.status == 'uploaded' for page in pages)
            and source_grants(principal, patient, 'write_record', author_id=batch.submitted_by) else []}
