"""Bounded example documents through real manual intake and document writers.

No login/session, model task, clinical episode or verification is created. The
caller supplies actual, validated example originals and self-reported text.
"""
import io
import json
import re
from functools import wraps
from uuid import uuid5

from django.conf import settings
from django.db import connection, transaction
from PIL import Image

from archives.batch_creation import batch_write_identity, prepare_sources, save_received_batch
from archives.documents import document_read, document_sources
from archives.grouping import save_document
from archives.models import ArchiveBatch, ArchiveDocument, ArchiveDocumentVersion, ArchivePage
from audit.service import write_audit
from authorization.models import ServiceRoleGrant
from authorization.policy import evaluate
from core.demo_records import DEMO_NAMESPACE, PATIENT_KEYS, demo_identity
from files.models import FileGroup, FileObject
from files.purpose import checked_bytes, lock_original, lock_use_authorization
from requests.archive_batches import authorized_request, batch_receipt
from requests.archive_documents import document_receipt, target_scope
from requests.models import WriteIntent, WriteRequest
from requests.service import compute_digest, prepare_intent, commit_intent
from sources.models import SourceRevision


class DemoArchivesRejected(ValueError):
    pass


def _require_test_context():
    if (settings.DEBUG is not True or settings.SJIA_ENVIRONMENT not in ('test', 'synthetic-browser-demo')
            or connection.vendor != 'postgresql'
            or not str(connection.settings_dict['NAME']).startswith('test_')):
        raise DemoArchivesRejected('DEMO_ARCHIVE_TEST_CONTEXT_REQUIRED')


def _test_only(function):
    @wraps(function)
    def checked(*args, **kwargs):
        # Guard before transaction.atomic can open a database connection.
        _require_test_context()
        return function(*args, **kwargs)
    return checked


def _context(actor, patient, file_group):
    _require_test_context()
    if patient.id not in {demo_identity(f'patient:{key}') for key in PATIENT_KEYS}:
        raise DemoArchivesRejected('DEMO_ARCHIVE_PATIENT_REFUSED')
    # Acquire the same gate -> actor -> patient order as normal intake.
    actor, patient, _ = batch_write_identity(actor, actor.auth_epoch, patient.id)
    if '示例' not in patient.display_id or ServiceRoleGrant.objects.filter(
            principal=actor, revoked_at__isnull=True).exists():
        raise DemoArchivesRejected('DEMO_ARCHIVE_FAMILY_SCOPE_REQUIRED')
    group = FileGroup.objects.select_for_update().get(id=file_group.id)
    revision = SourceRevision.objects.select_related('source').get(id=group.source_revision_id)
    if (group.patient_id != patient.id or group.owner_id != actor.id or group.status != 'available'
            or revision.id != demo_identity('source-revision') or revision.source_id != demo_identity('source')
            or revision.source.sensitivity != 'synthetic' or revision.source.status != 'approved'
            or revision.status != 'approved' or not revision.rights_evidence_ref):
        raise DemoArchivesRejected('DEMO_ARCHIVE_SOURCE_REFUSED')
    for action in ('write_record', 'read_raw', 'read_file'):
        evaluate(principal=actor, patient=patient, action=action, purpose='family_observation',
            environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=actor.auth_epoch,
            author_id=actor.id, require_relation=True, whole_resource=True)
    return actor, patient, group


def _original_metadata(group, patient, expected):
    originals = list(FileObject.objects.select_for_update().filter(file_group_id=group.id,
        patient_id=patient.id, purpose='original'))
    if len(originals) != 1 or originals[0].media_type != 'image/png':
        raise DemoArchivesRejected('DEMO_ARCHIVE_VISIT_ORIGINAL_REQUIRED')
    original = lock_original(originals[0].id)
    lock_use_authorization(group.use_authorization_id, original, 'family_observation', settings.SJIA_ENVIRONMENT)
    with Image.open(io.BytesIO(checked_bytes(original))) as image:
        try:
            metadata = json.loads(image.info['demo-content'])
        except (KeyError, TypeError, ValueError):
            raise DemoArchivesRejected('DEMO_ARCHIVE_VISIT_METADATA_REQUIRED') from None
        image.verify()
    child_key = next(key for key in PATIENT_KEYS if patient.id == demo_identity(f'patient:{key}'))
    if (not isinstance(metadata, dict) or metadata.get('schema_version') != 'example-family-visit-1'
            or metadata.get('synthetic') is not True or metadata.get('clinical_document') is not False
            or metadata.get('ocr_performed') is not False or metadata.get('child_key') != child_key
            or any(metadata.get(key) != value for key, value in expected.items())
            or not isinstance(metadata.get('lines'), list)
            or any(not isinstance(line, str) for line in metadata['lines'])
            or '\n'.join(metadata['lines']) != expected['confirmed_text']):
        raise DemoArchivesRejected('DEMO_ARCHIVE_VISIT_CONTENT_MISMATCH')


def _existing(*, actor, patient, operation, target, payload, request_id):
    rows = list(WriteIntent.all_objects.select_for_update().filter(request_id=request_id))
    if not rows:
        if WriteRequest.all_objects.filter(request_id=request_id).exists():
            raise DemoArchivesRejected('DEMO_ARCHIVE_ORPHAN_RECEIPT')
        return None
    if len(rows) != 1:
        raise DemoArchivesRejected('DEMO_ARCHIVE_RECEIPT_CONFLICT')
    intent = rows[0]
    if (intent.actor_id != actor.id or intent.patient_id != patient.id
            or intent.operation_id != operation or intent.target_key != target
            or intent.payload_sha256 != compute_digest(operation, target, '2.0.0', payload)
            or intent.status != 'committed'):
        raise DemoArchivesRejected('DEMO_ARCHIVE_INPUT_OR_RECEIPT_CHANGED')
    from retention.content_fences import assert_content_available
    assert_content_available('requests.writeintent', intent.id)
    return intent


def _audit(intent, resource, version):
    saved = commit_intent(intent=intent, action=intent.operation_id,
        resource_type=resource, resource_id=version[0], expected_version=version[1])
    write_audit(actor=intent.actor, patient=intent.patient, action=intent.operation_id,
        object_type=resource, object_id=version[0], object_version=str(version[1]),
        request_id=intent.request_id, outcome='committed', retention_policy=intent.retention_policy,
        occurred_at=saved.completed_at)


def _batch(actor, patient, group, key):
    operation, target = 'archive_batch_create', f'/patients/{patient.id}/archive-batches'
    payload = {'file_group_ids': [str(group.id)], 'intake_mode': 'manual'}
    request_id = uuid5(DEMO_NAMESPACE, f'experience-manual-archive-1:{patient.id}:{key}:batch')
    intent = _existing(actor=actor, patient=patient, operation=operation, target=target,
        payload=payload, request_id=request_id)
    if intent is not None:
        authorized_request(actor, actor.auth_epoch, intent)
    else:
        _, patient, sources, pages, quota, runtime, context = prepare_sources(
            actor, actor.auth_epoch, patient.id, payload)
        for source in sources:
            checked_bytes(source)
        intent = prepare_intent(actor=actor, patient=patient, operation_id=operation,
            target_key=target, request_id=request_id, payload=payload, schema_version='2.0.0',
            retention_policy=sources[0].retention_policy, authorization_context=context,
            recovery_payload=payload, ttl_seconds=runtime.payload['api']['intent_ttl_seconds'])
        batch = save_received_batch(actor=actor, patient=patient, request_id=request_id,
            pages=pages, quota=quota, intake_mode='manual')
        _audit(intent, 'archive_batch', (batch.id, 1))
    receipt = batch_receipt(intent)
    return ArchiveBatch.objects.get(id=receipt['resource_id'], patient_id=patient.id)


def _read_saved_document(actor, patient, intent):
    receipt = document_receipt(intent)
    document = ArchiveDocument.objects.get(id=receipt['resource_id'], patient_id=patient.id)
    version = ArchiveDocumentVersion.objects.get(document_id=document.id, patient_id=patient.id,
        version=int(receipt['version']))
    batch, pages, files, groups, episodes, facts = document_sources(
        document=document, patient=patient, version=version)
    # This helper's immutable initial version never contains clinical claims.
    if (episodes or facts or version.confirmed_by != actor.id
            or batch.intake_mode != 'manual' or batch.submitted_by != actor.id
            or str(batch.id) != intent.recovery_payload['batch_id']
            or [str(page.id) for page in pages] != intent.recovery_payload['page_ids']):
        raise DemoArchivesRejected('DEMO_ARCHIVE_DOCUMENT_SOURCE_CHANGED')
    actions = ['read_raw']
    if document.retired_at is not None or document.current_version != version.version:
        actions.append('read_history')
    for action in actions:
        evaluate(principal=actor, patient=patient, action=action, purpose='family_observation',
            environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=actor.auth_epoch,
            author_id=batch.submitted_by, require_relation=True, whole_resource=True)
    for group in groups:
        evaluate(principal=actor, patient=patient, action='read_file', purpose='family_observation',
            environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=actor.auth_epoch,
            author_id=group.owner_id, require_relation=True, whole_resource=True)
    for source in files:
        group = next(group for group in groups if group.id == source.file_group_id)
        lock_use_authorization(group.use_authorization_id, source, 'family_observation', settings.SJIA_ENVIRONMENT)
        checked_bytes(source)
    document_read(document=document, patient=patient, version=version)
    return document, version


@_test_only
@transaction.atomic
def ensure_demo_manual_archive(*, actor, patient, file_group, proposed_episodes,
        documented_time, organization, confirmed_text, request_key, document_type='outpatient'):
    """Return immutable initial receipt IDs, preserving any later user edits.

    The original must genuinely contain the supplied example visit text. This
    writer records family-supplied descriptions only, never ClinicalEpisode.
    It does not create, authenticate or persist a session.
    """
    actor, patient, group = _context(actor, patient, file_group)
    if (not isinstance(request_key, str) or not re.fullmatch(r'[a-zA-Z0-9_/-]{1,80}', request_key)
            or not isinstance(proposed_episodes, list) or len(proposed_episodes) != 2
            or not isinstance(confirmed_text, str) or not confirmed_text.strip()):
        raise DemoArchivesRejected('DEMO_ARCHIVE_INPUT_INVALID')
    _original_metadata(group, patient, {'confirmed_text': confirmed_text,
        'proposed_episodes': proposed_episodes, 'organization': organization, 'documented_time': documented_time})
    batch = _batch(actor, patient, group, request_key)
    pages = list(ArchivePage.objects.filter(batch_id=batch.id, patient_id=patient.id).order_by('ordinal'))
    payload = {'batch_id': str(batch.id), 'page_ids': [str(page.id) for page in pages],
        'document_type': document_type, 'organization': organization,
        'documented_time': documented_time, 'episode_ids': [], 'confirmed_text': confirmed_text,
        'proposed_metadata': {'episodes': proposed_episodes,
            'grouping_note': '家庭补充的示例就诊说明，尚未经专业核对。'}}
    operation, target = 'archive_document_create', f'/patients/{patient.id}/archive-documents'
    request_id = uuid5(DEMO_NAMESPACE, f'experience-manual-archive-1:{patient.id}:{request_key}:document')
    intent = _existing(actor=actor, patient=patient, operation=operation, target=target,
        payload=payload, request_id=request_id)
    if intent is None:
        patient, _, retention, context = target_scope(None, actor, actor.auth_epoch,
            operation, target, payload)
        intent = prepare_intent(actor=actor, patient=patient, operation_id=operation,
            target_key=target, request_id=request_id, payload=payload, schema_version='2.0.0',
            retention_policy=retention, authorization_context=context, recovery_payload=payload)
        document, version = save_document(patient=patient, actor=actor, payload=payload)
        _audit(intent, 'archive_document', (document.id, version.version))
    document, version = _read_saved_document(actor, patient, intent)
    return {'batch_id': str(batch.id), 'document_id': str(document.id),
        'version': str(version.version), 'current_version': str(document.current_version),
        'grouping_status': version.grouping_status, 'self_reported_visits': len(proposed_episodes),
        'clinical_episode_ids': [], 'ocr': 'not_run'}
