from decimal import Decimal, localcontext
from uuid import UUID

from django.conf import settings
from django.db.models import F
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from api.base import ApiError
from archives.batches import batch_runtime, file_page_record_scope, source_grants
from archives.inputs import PROTOCOL, REGISTRY
from archives.models import Archive, ArchiveBatch, ArchivePage
from authorization.models import AccessGrant
from authorization.policy import check_principal_session
from configurations.evidence import inspect_approved_evidence
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from family_content.models import EnvironmentGate
from files.models import FileGroup, FileObject, FilePage, UploadPart
from files.purpose import lock_original, lock_use_authorization
from identity.models import Patient, Principal
from relations.models import Relation
from retention.models import RetentionPolicy
from sources.models import SourceRevision


BATCH_INPUT = Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/BatchInput'},
    registry=REGISTRY, format_checker=FormatChecker())


def batch_input(payload):
    if not BATCH_INPUT.is_valid(payload):
        raise ApiError('ARCHIVE_BATCH_INPUT_INVALID', 422)
    identifiers = [UUID(value) for value in payload['file_group_ids']]
    if len(set(identifiers)) != len(identifiers) or any(str(identifier) != value
            for identifier, value in zip(identifiers, payload['file_group_ids'], strict=True)):
        raise ApiError('ARCHIVE_BATCH_INPUT_INVALID', 422)
    return payload


def batch_write_identity(actor, epoch, patient_id):
    if not settings.SJIA_ENVIRONMENT:
        raise ApiError('ENVIRONMENT_CONFIGURATION_REQUIRED', 503)
    gate = EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
    if not gate.enabled:
        raise ApiError('ENVIRONMENT_DISABLED', 403)
    patient = Patient.objects.select_for_update().get(id=patient_id)
    actor = Principal.objects.select_for_update().get(id=actor.id)
    check_principal_session(actor, epoch)
    if patient.status != 'active':
        raise ApiError('PATIENT_NOT_AVAILABLE', 403)
    return actor, patient, gate


def locked_grants(actor, patient, action, *, author_id, record_id=None):
    grants = source_grants(actor, patient, action, author_id=author_id, record_id=record_id)
    if not grants:
        raise ApiError('CASE_ACTION_DENIED', 403)
    list(AccessGrant.objects.select_for_update().filter(id__in=[grant.id for grant in grants]).order_by('id'))
    relations = list(Relation.objects.select_for_update().filter(
        id__in=[grant.relation_id for grant in grants], patient_id=patient.id, guardian_id=actor.id).order_by('id'))
    current = source_grants(actor, patient, action, author_id=author_id, record_id=record_id)
    versions = {relation.id: str(relation.version) for relation in relations}
    if not current or any(grant.relation_id not in versions for grant in current):
        raise ApiError('CASE_ACTION_DENIED', 403)
    return [{'grant_id': str(grant.id), 'relation_id': str(grant.relation_id),
        'relation_version': versions[grant.relation_id], 'action': action,
        'record_id': str(record_id) if record_id is not None else None,
        'author_id': str(author_id)} for grant in current]


def received_sources(actor, patient, gate, payload, *, receive):
    if receive is not True:
        raise ValueError('原件接收必须核验当前来源资格。')
    batch_input(payload)
    proofs = locked_grants(actor, patient, 'write_record', author_id=actor.id)
    groups = {str(group.id): group for group in FileGroup.objects.select_for_update().filter(
        patient=patient, id__in=payload['file_group_ids']).order_by('id')}
    if set(groups) != set(payload['file_group_ids']):
        raise ApiError('ARCHIVE_BATCH_SOURCE_UNAVAILABLE', 409)
    sources, pages, references = [], [], []
    for group_id in payload['file_group_ids']:
        group = groups[group_id]
        if group.status != 'available':
            raise ApiError('FILE_GROUP_NOT_AVAILABLE', 409)
        group_pages = list(FilePage.objects.select_for_update().filter(
            patient=patient, file_group_id=group.id).order_by('ordinal'))
        originals = list(FileObject.objects.filter(patient=patient, file_group_id=group.id,
            purpose='original').order_by('id'))
        if not group_pages or not originals or {page.file_id for page in group_pages} != {source.id for source in originals}:
            raise ApiError('ARCHIVE_BATCH_PAGES_INCOMPLETE', 409)
        if [page.ordinal for page in group_pages] != list(range(1, len(group_pages) + 1)):
            raise ApiError('ARCHIVE_PAGE_ORDER_INVALID', 409)
        for file_page in group_pages:
            proofs.extend(locked_grants(actor, patient, 'read_file', author_id=group.owner_id,
                record_id=file_page_record_scope(file_page.id, patient)))
        use_refs, file_refs = [], []
        for source in originals:
            if receive:
                source = lock_original(source.id)
                source.retention_policy = RetentionPolicy.objects.select_for_update().get(id=source.retention_policy_id)
                policy_approver = Principal.objects.select_for_update().get(id=source.retention_policy.approved_by_id)
                if source.retention_policy.approved_at > timezone.now() or policy_approver.account_status != 'active':
                    raise ApiError('ARCHIVE_RETENTION_APPROVAL_UNAVAILABLE', 409)
                authorization = lock_use_authorization(group.use_authorization_id, source,
                    'family_observation', gate.environment)
                revision = SourceRevision.objects.get(id=authorization.source_revision_id)
                revision_policy = RetentionPolicy.objects.select_for_update().filter(id=revision.retention_policy_id).first()
                if (not revision.license.strip() or not revision.rights_evidence_ref.strip()
                        or revision.captured_at > timezone.now() or revision_policy is None or revision_policy.status != 'active'):
                    raise ApiError('ARCHIVE_SOURCE_APPROVAL_UNAVAILABLE', 409)
                parts = list(UploadPart.objects.select_for_update().filter(patient_id=patient.id,
                    file_group_id=group.id, file_id=source.id))
                if len(parts) != 1 or (parts[0].status != 'stored'
                        or parts[0].object_version != source.object_version
                        or parts[0].object_key != source.object_key
                        or parts[0].declared_checksum != source.checksum
                        or parts[0].declared_size_bytes != source.size_bytes
                        or parts[0].declared_media_type != source.media_type):
                    raise ApiError('ARCHIVE_UPLOAD_RECEIPT_INVALID', 409)
                use_refs.append({'id': str(authorization.id), 'source_revision_id': str(authorization.source_revision_id),
                    'source_checksum': revision.checksum, 'source_captured_at': revision.captured_at.isoformat(),
                    'source_retention_policy_id': str(revision_policy.id), 'source_retention_policy_version': str(revision_policy.version),
                    'rights_evidence_ref': revision.rights_evidence_ref,
                    'approved_at': authorization.approved_at.isoformat(), 'valid_until':
                        authorization.valid_until.isoformat() if authorization.valid_until is not None else None,
                    'evidence_ref': authorization.evidence_ref})
            file_refs.append({'id': str(source.id), 'object_version': source.object_version,
                'checksum': source.checksum, 'size_bytes': source.size_bytes,
                'media_type': source.media_type, 'created_at': source.created_at.isoformat(),
                'retention_policy_id': str(source.retention_policy_id),
                'retention_policy_version': str(source.retention_policy.version)})
            sources.append(source)
        if receive and UploadPart.objects.filter(patient_id=patient.id, file_group_id=group.id).exclude(status='stored').exists():
            raise ApiError('FILE_GROUP_INCOMPLETE', 409)
        pages.extend(group_pages)
        references.append({'group_id': str(group.id), 'group_version': str(group.version),
            'use_authorization_id': str(group.use_authorization_id), 'files': file_refs,
            'page_ids': [str(page.id) for page in group_pages], 'use_refs': use_refs})
    context = {'environment': gate.environment, 'gate_version': str(gate.version),
        'runtime_config_id': str(gate.runtime_config_id), 'patient_id': str(patient.id),
        'auth_epoch': str(actor.auth_epoch), 'source_refs': references,
        'grants': sorted(proofs, key=lambda proof: (proof['grant_id'], proof['record_id'] or '', proof['author_id']))}
    return sources, pages, context


def receiving_quota(gate, sources, pages):
    runtime = batch_runtime()
    candidates = list(ConfigVersion.objects.select_for_update().filter(kind='provider', status='enabled',
        schema_version='archive-quota-1', payload__environment=gate.environment,
        payload__runtime_config_id=str(runtime.id)).order_by('id'))
    if not candidates:
        raise ApiError('ARCHIVE_QUOTA_CONFIGURATION_PENDING', 503)
    if len(candidates) != 1:
        raise ApiError('ARCHIVE_QUOTA_CONFIGURATION_AMBIGUOUS', 409)
    quota = candidates[0]
    if configuration_qualification_reason(quota, gate.environment) is not None:
        raise ApiError('ARCHIVE_QUOTA_CONFIGURATION_PENDING', 503)
    provider_entries = [entry for entry in runtime.payload['providers'] if entry['kind'] == 'ocr' and entry['enabled']]
    if len(provider_entries) != 1 or provider_entries[0]['config_id'] != quota.payload['provider_config_id']:
        raise ApiError('ARCHIVE_PROVIDER_CONFIGURATION_PENDING', 503)
    provider = ConfigVersion.objects.select_for_update().filter(id=quota.payload['provider_config_id'], kind='provider').first()
    if (provider is None or provider.schema_version != 'provider-1'
            or configuration_qualification_reason(provider, gate.environment) is not None):
        raise ApiError('ARCHIVE_PROVIDER_CONFIGURATION_PENDING', 503)
    provider_entry = provider_entries[0]
    if (provider_entry['currency'] != quota.payload['currency'] or provider_entry['daily_budget'] is None
            or Decimal(quota.payload['daily_budget']) > Decimal(provider_entry['daily_budget'])):
        raise ApiError('ARCHIVE_PROVIDER_BUDGET_INVALID', 409)
    evidence = {}
    for field, purpose in (('measurement_evidence_ref', 'archive_measurement'), ('pricing_evidence_ref', 'archive_pricing')):
        revision, _, reason = inspect_approved_evidence(quota.payload[field], purpose=purpose, lock=True)
        if reason:
            raise ApiError(reason, 503)
        evidence[field] = {'source_revision_id': str(revision.id), 'checksum': revision.checksum}
    maximum_pages = min(runtime.payload['limits']['max_pages'], quota.payload['max_pages'])
    maximum_bytes = min(runtime.payload['limits']['max_upload_bytes'], quota.payload['max_bytes'])
    if len(pages) > maximum_pages or sum(source.size_bytes for source in sources) > maximum_bytes:
        raise ApiError('ARCHIVE_BATCH_LIMIT_EXCEEDED', 422)
    with localcontext() as arithmetic:
        arithmetic.prec = len(quota.payload['per_page_reservation']) + len(str(len(pages))) + 1
        if Decimal(quota.payload['per_page_reservation']) * len(pages) > Decimal(quota.payload['per_batch_budget']):
            raise ApiError('ARCHIVE_BATCH_BUDGET_EXCEEDED', 422)
    return quota, runtime, {'quota_id': str(quota.id), 'quota_checksum': quota.checksum,
        'runtime_id': str(runtime.id), 'runtime_checksum': runtime.checksum,
        'provider_id': str(provider.id), 'provider_checksum': provider.checksum, 'evidence': evidence}


def prepare_sources(actor, epoch, patient_id, payload):
    actor, patient, gate = batch_write_identity(actor, epoch, patient_id)
    sources, pages, context = received_sources(actor, patient, gate, payload, receive=True)
    if ArchivePage.objects.filter(patient_id=patient.id, file_page_id__in=[page.id for page in pages]).exists():
        raise ApiError('ARCHIVE_SOURCE_ALREADY_RECEIVED', 409)
    quota, runtime, qualification = receiving_quota(gate, sources, pages)
    context['configuration'] = qualification
    return actor, patient, sources, pages, quota, runtime, context


def save_received_batch(*, actor, patient, request_id, pages, quota):
    if ArchiveBatch.objects.filter(logical_request_id=request_id).exists():
        raise ApiError('ARCHIVE_BATCH_RESULT_UNKNOWN', 409)
    now = timezone.now()
    archive, _ = Archive.objects.get_or_create(patient_id=patient.id, defaults={'created_at': now})
    batch = ArchiveBatch.objects.create(patient_id=patient.id, archive_id=archive.id, submitted_by=actor.id,
        logical_request_id=request_id, version=1, received_at=now, quota_policy_id=quota.id,
        budget_status='available', status='receiving')
    for ordinal, file_page in enumerate(pages, 1):
        source = FileObject.objects.get(id=file_page.file_id, patient=patient)
        ArchivePage.objects.create(patient_id=patient.id, batch_id=batch.id, ordinal=ordinal,
            file_page_id=file_page.id, file_id=source.id, file_object_version=source.object_version,
            checksum=source.checksum, version=1, status='uploaded', status_version=1,
            current_task_id=None, available_at=source.created_at)
    Patient.objects.filter(id=patient.id).update(data_epoch=F('data_epoch') + 1)
    return batch
