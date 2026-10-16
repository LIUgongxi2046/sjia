"""PRD 8.19.3: manual document correction over intact failed OCR originals.

Isolated PostgreSQL synthetic fixtures only. The lead runs this label against a
frozen test snapshot. HTTP document writes/read/replay use real authorization,
qualification and storage checks. Retry tests exercise native retry persistence
with an explicit synthetic admission context, without invoking a provider or
claiming to cover external model/purpose-copy admission.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.db import connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from archives.fact_candidates import candidate_items, current_document, field_input, unknown_context
from archives.fact_writes import confirm_facts
from archives.grouping import document_materials, save_document
from archives.models import (Archive, ArchiveBatch, ArchiveDocument, ArchiveDocumentPage,
    ArchiveDocumentVersion, ArchiveFact, ArchiveFactVersion, ArchivePage, ArchivePageAttempt)
from archives.page_actions import save_action
from authorization.models import AccessGrant
from configurations.models import ConfigVersion
from configurations.service import compute_checksum
from dependencies.models import DependencyEdge, DependencyNode
from files.models import FileGroup, FileObject, FilePage
from files.storage import LocalFileStore, digest_bytes
from identity.models import Patient
from recognition.archive_extraction import SCHEMA_VERSION
from recognition.models import CandidateConfirmation, CandidateField, CandidateSet, SourceAnchor
from records.models import Record, RecordField, RecordVersion
from records.queries import QueryRejected
from requests.models import WriteIntent
from sources.models import DataUseAuthorization
from tasks.models import OutboxEvent, Task, TaskAttempt
from tests import test_record_access_boundaries as boundaries
from tests.isolated_rows import IsolatedSchemaRows
from tests.synthetic_fixtures import SyntheticEvidence, synthetic_png
from verification.models import Verification, FieldVerificationState


@override_settings(SJIA_ENVIRONMENT='test')
class FailedArchiveCorrectionTests(TransactionTestCase):
    _grant = boundaries.RecordAccessBoundaryTest._grant

    def setUp(self):
        rows = IsolatedSchemaRows(require_empty=True)
        self.addCleanup(rows.cleanup)
        # Keep fixture construction atomic, including any future Record/current
        # version deferred-FK pairs introduced by the shared synthetic fixtures.
        with transaction.atomic():
            boundaries.RecordAccessBoundaryTest.setUp(self)
            self.actor = self.principal
            self.fixture = SyntheticEvidence(self, self.actor)
            self.runtime = self.fixture.runtime()
            for action in ('read_raw', 'read_file', 'write_record', 'read_history'):
                self._grant(action=action, codes=())
            self.use = DataUseAuthorization.objects.create(patient=self.patient,
                source_revision=self.fixture.source, purpose='family_observation', environment='test',
                evidence_ref='synthetic-test-only', approved_by=self.actor, approved_at=self.now)
            self.group = FileGroup.objects.create(patient=self.patient, owner=self.actor, version=1,
                status='available', source_revision=self.fixture.source,
                use_authorization_id=self.use.id, created_at=self.now)
            # A valid blank image deliberately contains no OCR-recoverable text.
            # Any confirmed_text below is explicitly supplied by the human actor.
            self.original_bytes = synthetic_png()
            self.file = FileObject.objects.create(patient=self.patient, file_group_id=self.group.id,
                object_key=f'synthetic-failed-original-{uuid4()}', object_version='1',
                checksum=digest_bytes(self.original_bytes), size_bytes=len(self.original_bytes),
                media_type='image/png', purpose='original', status='available',
                retention_policy=self.fixture.retention, created_at=self.now)
            self.file_page = FilePage.objects.create(patient=self.patient, file_group_id=self.group.id,
                file_id=self.file.id, ordinal=1, width=12, height=18, rotation=0)
            archive = Archive.objects.create(patient_id=self.patient.id, created_at=self.now)
            self.batch = ArchiveBatch.objects.create(patient_id=self.patient.id, archive_id=archive.id,
                submitted_by=self.actor.id, logical_request_id=uuid4(), version=3, received_at=self.now,
                quota_policy_id=self.runtime.id, budget_status='released', status='failed')
            self.page = ArchivePage.objects.create(patient_id=self.patient.id, batch_id=self.batch.id,
                file_page_id=self.file_page.id, file_id=self.file.id, file_object_version='1',
                checksum=self.file.checksum, ordinal=1, version=1, status='failed', status_version=3,
                available_at=self.now)
            refs = {'archive_page_id': str(self.page.id), 'archive_page_version': '1',
                'batch_id': str(self.batch.id), 'file_id': str(self.file.id),
                'file_page_id': str(self.file_page.id), 'file_object_version': '1',
                'original_checksum': self.file.checksum, 'model_version': 'synthetic-no-provider',
                'file_group_id': str(self.group.id), 'file_group_version': '1', 'environment': 'test'}
            self.task = Task.objects.create(actor=self.actor, patient=self.patient, task_type='ocr',
                schema_version='archive-page-1', input_refs=refs, input_sha256=compute_checksum(refs),
                status='failed', status_version=3, lease_generation=1, lease_owner='synthetic-finished',
                lease_until=self.now, created_at=self.now - timedelta(seconds=2), deadline_at=self.now)
            self.page.current_task_id = self.task.id
            self.page.save(update_fields=['current_task_id'])
            self.attempt = TaskAttempt.objects.create(task=self.task, attempt_number=1, lease_generation=1,
                started_at=self.now - timedelta(seconds=1), finished_at=self.now, status='failed',
                error_code='SYNTHETIC_UNREADABLE_ORIGINAL', usage_evidence={'phase': 'pre_provider'})
            self.page_attempt = ArchivePageAttempt.objects.create(patient_id=self.patient.id,
                page_id=self.page.id, page_version=1, task_id=self.task.id, task_attempt_id=self.attempt.id,
                attempt_number=1, input_sha256=self.task.input_sha256, model_version='synthetic-no-provider',
                started_at=self.attempt.started_at, finished_at=self.now, duration_ms=1000,
                status='failed', error_code=self.attempt.error_code, usage_evidence=self.attempt.usage_evidence)
        self.store = LocalFileStore(settings.FILE_OBJECT_ROOT)
        self.store.put(self.file.object_key, self.original_bytes)
        storage = patch('files.purpose.object_store', self.store)
        storage.start()
        self.addCleanup(storage.stop)
        self.target = f'/patients/{self.patient.id}/archive-documents'

    def payload(self, **changes):
        value = {'batch_id': str(self.batch.id), 'page_ids': [str(self.page.id)],
            'document_type': 'laboratory', 'organization': 'SYNTHETIC HUMAN PROVIDED ORGANIZATION',
            'documented_time': None, 'episode_ids': [], 'proposed_metadata': None,
            'confirmed_text': 'Human-provided manual text; blank original remains unreadable. Synthetic test only.'}
        value.update(changes)
        return value

    def prepare(self, payload=None, *, target=None, operation='archive_document_create', expected=201):
        payload = self.payload() if payload is None else payload
        identity = str(uuid4())
        response = self.client.post('/api/v2/write-intents', {'operation_id': operation,
            'target_key': target or self.target, 'request_id': identity, 'schema_version': '2.0.0',
            'payload': payload}, content_type='application/json')
        self.assertEqual(response.status_code, expected, response.content)
        if expected != 201:
            return response.json()
        value = response.json()
        return value, {'HTTP_X_WRITE_INTENT': value['intent_id'],
            'HTTP_X_INTENT_GENERATION': value['generation'], 'HTTP_IDEMPOTENCY_KEY': identity}

    def submit(self, payload=None, *, target=None, prepared=None, operation='archive_document_create', expected=None):
        payload = self.payload() if payload is None else payload
        target = target or self.target
        prepared = prepared or self.prepare(payload, target=target, operation=operation)
        response = self.client.post('/api/v2' + target, payload, content_type='application/json', **prepared[1])
        self.assertEqual(response.status_code, expected or (201 if operation == 'archive_document_create' else 200), response.content)
        return response.json(), prepared

    def clinical_counts(self):
        return {model.__name__: model.objects.filter(patient_id=self.patient.id).count() for model in
            (Record, RecordVersion, RecordField, ArchiveFact, ArchiveFactVersion, CandidateSet,
             CandidateField, CandidateConfirmation, SourceAnchor, Verification, FieldVerificationState)}

    def execution_snapshot(self):
        return {model.__name__: list(model.objects.filter(patient_id=self.patient.id).order_by('id').values())
            for model in (ArchiveBatch, ArchivePage, Task, ArchivePageAttempt)} | {
            'TaskAttempt': list(TaskAttempt.objects.filter(task__patient=self.patient).order_by('id').values())}

    def assert_no_document(self):
        self.assertFalse(ArchiveDocument.objects.filter(patient_id=self.patient.id).exists())
        self.assertFalse(any(self.clinical_counts().values()))

    def test_http_create_read_update_replay_preserves_failed_execution_and_human_provenance(self):
        before = self.execution_snapshot()
        payload = self.payload()
        first, prepared = self.submit(payload)
        document = ArchiveDocument.objects.get(id=first['resource_id'])
        original = ArchiveDocumentVersion.objects.get(document_id=document.id, version=1)
        self.assertEqual((original.confirmed_text, original.confirmed_by), (payload['confirmed_text'], self.actor.id))
        self.assertIsNotNone(original.confirmed_at)
        self.assertIsNone(original.proposed_metadata)
        self.assertEqual(original.grouping_status, 'pending')
        response = self.client.get(f'/api/v2/archive-documents/{document.id}')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['confirmed_text'], payload['confirmed_text'])
        target = f'/archive-documents/{document.id}/grouping'
        update = self.payload(expected_version='1', reason='Human corrected own manual entry',
            confirmed_text='Corrected human-provided text. Still no recovered OCR or clinical verification.',
            organization='SYNTHETIC HUMAN CORRECTION')
        second, second_intent = self.submit(update, target=target, operation='archive_document_group')
        latest = ArchiveDocumentVersion.objects.get(document_id=document.id, version=2)
        self.assertEqual((latest.supersedes_version, latest.reason, latest.confirmed_by),
            (1, update['reason'], self.actor.id))
        self.assertEqual(latest.confirmed_text, update['confirmed_text'])
        self.assertIsNotNone(latest.confirmed_at)
        original.refresh_from_db()
        self.assertEqual(original.confirmed_text, payload['confirmed_text'])
        for body, route, intent, operation, receipt in ((payload, self.target, prepared,
                'archive_document_create', first), (update, target, second_intent, 'archive_document_group', second)):
            replay, _ = self.submit(body, target=route, prepared=intent, operation=operation)
            self.assertEqual(replay, receipt)
            recovered = self.client.get(f'/api/v2/requests/{intent[0]["intent_id"]}')
            self.assertEqual(recovered.status_code, 200, recovered.content)
            self.assertEqual(recovered.json()['receipt'], receipt)
            self.assertEqual(WriteIntent.objects.get(id=intent[0]['intent_id']).status, 'committed')
        current = self.client.get(f'/api/v2/archive-documents/{document.id}')
        self.assertEqual(current.status_code, 200, current.content)
        self.assertEqual(current.json()['version'], '2')
        self.assertEqual(current.json()['confirmed_text'], update['confirmed_text'])
        self.assertEqual(ArchiveDocumentVersion.objects.filter(document_id=document.id).count(), 2)
        self.assertEqual(before, self.execution_snapshot())
        self.assertFalse(any(self.clinical_counts().values()))
        self.assertEqual(self.store.get(self.file.object_key), self.original_bytes)
        node = DependencyNode.objects.get(resource_type='archive_document', resource_id=document.id, resource_version='2')
        sources = set(DependencyEdge.objects.filter(dependent=node).values_list(
            'prerequisite__resource_type', 'prerequisite__resource_id', 'prerequisite__resource_version'))
        self.assertIn(('file_object', self.file.id, '1'), sources)
        self.assertIn(('archive_page', self.page.id, '1'), sources)
        self.assertEqual(DependencyNode.objects.get(resource_type='archive_document', resource_id=document.id,
            resource_version='1').validity, 'invalid')

    def test_metadata_only_does_not_invent_confirmed_text(self):
        payload = self.payload()
        del payload['confirmed_text']
        receipt, _ = self.submit(payload)
        self.assertIsNone(ArchiveDocumentVersion.objects.get(document_id=receipt['resource_id']).confirmed_text)
        self.assertFalse(any(self.clinical_counts().values()))

    def test_no_source_and_corrupt_source_are_distinct_failures(self):
        path = self.store.root / self.file.object_key
        path.unlink()
        result = self.prepare(expected=409)
        self.assertEqual(result['code'], 'FILE_OBJECT_UNAVAILABLE')
        self.assert_no_document()
        self.store.put(self.file.object_key, b'SYNTHETIC CORRUPT ORIGINAL')
        result = self.prepare(expected=422)
        self.assertEqual(result['code'], 'PURPOSE_CHECKSUM_MISMATCH')
        self.assert_no_document()

    def test_original_availability_and_current_version_are_required(self):
        for status in ('quarantined', 'failed', 'disposed'):
            FileObject.objects.filter(id=self.file.id).update(status=status)
            with self.subTest(status=status):
                self.assertEqual(self.prepare(expected=422)['code'], 'ORIGINAL_UNAVAILABLE')
        FileObject.objects.filter(id=self.file.id).update(status='available', object_version='2')
        self.assertEqual(self.prepare(expected=422)['code'], 'DOCUMENT_SOURCE_INVALID')
        self.assert_no_document()

    def test_receiving_and_cancelled_batches_remain_blocked_including_all_uploaded(self):
        for batch_status, page_status in (('receiving', 'uploaded'), ('receiving', 'failed'), ('cancelled', 'failed')):
            ArchiveBatch.objects.filter(id=self.batch.id).update(status=batch_status)
            ArchivePage.objects.filter(id=self.page.id).update(status=page_status)
            with self.subTest(batch=batch_status, page=page_status):
                self.assertEqual(self.prepare(expected=409)['code'], 'DOCUMENT_BATCH_UNAVAILABLE')
        self.assert_no_document()

    def test_active_or_uncertain_page_states_never_admit_manual_correction(self):
        for state in ('declared', 'receiving', 'queued', 'processing', 'cancel_requested', 'uncertain', 'cancelled', 'superseded'):
            ArchivePage.objects.filter(id=self.page.id).update(status=state)
            with self.subTest(state=state):
                self.assertEqual(self.prepare(expected=409)['code'], 'DOCUMENT_PAGE_UNAVAILABLE')
        self.assert_no_document()

    def test_failed_page_cannot_hide_nonterminal_task_or_live_lease(self):
        for state in ('accepted', 'blocked', 'queued', 'running', 'cancel_requested', 'uncertain',
                'awaiting_resolution', 'awaiting_confirmation', 'completed', 'cancelled', 'superseded'):
            Task.objects.filter(id=self.task.id).update(status=state)
            with self.subTest(state=state):
                self.assertEqual(self.prepare(expected=409)['code'], 'DOCUMENT_PAGE_UNAVAILABLE')
        Task.objects.filter(id=self.task.id).update(status='failed', lease_until=timezone.now() + timedelta(minutes=1))
        self.assertEqual(self.prepare(expected=409)['code'], 'DOCUMENT_PAGE_UNAVAILABLE')
        self.assert_no_document()

    def test_missing_current_task_and_corrupt_task_provenance_fail_closed(self):
        ArchivePage.objects.filter(id=self.page.id).update(current_task_id=None)
        self.assertEqual(self.prepare(expected=409)['code'], 'DOCUMENT_PAGE_UNAVAILABLE')
        ArchivePage.objects.filter(id=self.page.id).update(current_task_id=self.task.id)
        Task.objects.filter(id=self.task.id).update(input_sha256='0' * 64)
        self.assertEqual(self.prepare(expected=409)['code'], 'ARCHIVE_PAGE_TASK_INVALID')
        self.assert_no_document()

    def test_failed_task_cannot_hide_running_uncertain_or_unfinished_attempts(self):
        for model, row in ((ArchivePageAttempt, self.page_attempt), (TaskAttempt, self.attempt)):
            for state, finished in (('running', None), ('uncertain', self.now), ('failed', None)):
                model.objects.filter(id=row.id).update(status=state, finished_at=finished)
                with self.subTest(model=model.__name__, state=state, finished=finished):
                    self.assertEqual(self.prepare(expected=409)['code'], 'DOCUMENT_PAGE_UNAVAILABLE')
            model.objects.filter(id=row.id).update(status='failed', finished_at=self.now)
        self.assert_no_document()

    def test_historical_uncertain_task_cannot_be_hidden_by_current_failed_pointer(self):
        refs = deepcopy(self.task.input_refs)
        Task.objects.create(actor=self.actor, patient=self.patient, task_type='ocr', schema_version='archive-page-1',
            input_refs=refs, input_sha256=compute_checksum(refs), status='uncertain', status_version=1,
            created_at=self.now, deadline_at=self.now)
        self.assertEqual(self.prepare(expected=409)['code'], 'DOCUMENT_PAGE_UNAVAILABLE')
        self.assert_no_document()

    def test_revoked_grant_and_use_authorization_after_intent_are_rechecked(self):
        prepared = self.prepare()
        for action in ('write_record', 'read_raw', 'read_file'):
            grants = AccessGrant.objects.filter(principal=self.actor, patient=self.patient, action=action)
            grants.update(revoked_at=timezone.now())
            with self.subTest(action=action):
                result, _ = self.submit(prepared=prepared, expected=403)
                self.assertEqual(result['code'], 'CASE_ACTION_DENIED')
            grants.update(revoked_at=None)
        DataUseAuthorization.objects.filter(id=self.use.id).update(revoked_at=timezone.now())
        result, _ = self.submit(prepared=prepared, expected=403)
        self.assertEqual(result['code'], 'PURPOSE_AUTHORIZATION_UNAVAILABLE')
        self.assert_no_document()

    def test_cross_patient_page_is_rejected_without_creating_document(self):
        with transaction.atomic():
            other = Patient.objects.create(display_id=f'SYNTHETIC-OTHER-{uuid4()}', status='active', created_at=self.now)
            archive = Archive.objects.create(patient_id=other.id, created_at=self.now)
            batch = ArchiveBatch.objects.create(patient_id=other.id, archive_id=archive.id, submitted_by=self.actor.id,
                logical_request_id=uuid4(), version=1, received_at=self.now, quota_policy_id=self.runtime.id,
                budget_status='released', status='failed')
            use = DataUseAuthorization.objects.create(patient=other, source_revision=self.fixture.source,
                purpose='family_observation', environment='test', evidence_ref='synthetic-test-only',
                approved_by=self.actor, approved_at=self.now)
            group = FileGroup.objects.create(patient=other, owner=self.actor, version=1, status='available',
                source_revision=self.fixture.source, use_authorization_id=use.id, created_at=self.now)
            file = FileObject.objects.create(patient=other, file_group_id=group.id, object_key=f'synthetic-foreign-{uuid4()}',
                object_version='1', checksum=self.file.checksum, size_bytes=self.file.size_bytes, media_type='image/png',
                purpose='original', status='available', retention_policy=self.fixture.retention, created_at=self.now)
            file_page = FilePage.objects.create(patient=other, file_group_id=group.id, file_id=file.id,
                ordinal=1, width=12, height=18, rotation=0)
            page = ArchivePage.objects.create(patient_id=other.id, batch_id=batch.id, file_page_id=file_page.id,
                file_id=file.id, file_object_version='1', checksum=file.checksum, ordinal=1, version=1,
                status='failed', status_version=1, available_at=self.now)
        self.assertEqual(self.prepare(self.payload(page_ids=[str(page.id)]), expected=422)['code'], 'DOCUMENT_PAGE_INVALID')
        self.assert_no_document()

    def test_stale_document_version_and_source_change_after_intent_are_rechecked(self):
        first, _ = self.submit()
        target = f'/archive-documents/{first["resource_id"]}/grouping'
        payload = self.payload(expected_version='1', reason='Human correction')
        prepared = self.prepare(payload, target=target, operation='archive_document_group')
        self.submit(payload, target=target, operation='archive_document_group')
        stale, _ = self.submit(payload, target=target, prepared=prepared, operation='archive_document_group', expected=409)
        self.assertEqual(stale['code'], 'VERSION_CONFLICT')
        payload['expected_version'] = '2'
        prepared = self.prepare(payload, target=target, operation='archive_document_group')
        self.store.put(self.file.object_key, b'SYNTHETIC CORRUPTED AFTER INTENT')
        rejected, _ = self.submit(payload, target=target, prepared=prepared, operation='archive_document_group', expected=422)
        self.assertEqual(rejected['code'], 'PURPOSE_CHECKSUM_MISMATCH')
        self.assertEqual(ArchiveDocumentVersion.objects.filter(document_id=first['resource_id']).count(), 2)

    def retry_fixture(self):
        # Native retry persistence accepts an already-admitted context. This
        # explicit synthetic context never claims a real provider/copy approval.
        provider = ConfigVersion.objects.create(config_key=f'synthetic-retry-{uuid4()}', kind='provider',
            version=1, schema_version='test-1', payload={}, checksum=compute_checksum({}),
            status='draft', created_by=self.actor, created_at=self.now)
        runtime = ConfigVersion.objects.create(config_key=f'synthetic-retry-runtime-{uuid4()}', kind='provider',
            version=1, schema_version='test-1', payload={'providers': [{'kind': 'ocr', 'enabled': True,
                'config_id': str(provider.id)}]}, checksum='a' * 64, status='draft', created_by=self.actor, created_at=self.now)
        quota = SimpleNamespace(id=self.runtime.id, payload={'provider_config_id': str(provider.id),
            'model_version': 'synthetic-no-provider', 'per_page_reservation': '0', 'deadline_seconds': 60})
        context = {'configuration': {'runtime_id': str(runtime.id)},
            'purpose_copies': {str(self.page.id): {'synthetic_persistence_boundary_only': True}}}
        return quota, context

    def native_retry(self, config):
        with transaction.atomic():
            batch = ArchiveBatch.objects.select_for_update().get(id=self.batch.id)
            page = ArchivePage.objects.select_for_update().get(id=self.page.id)
            self.assertEqual(page.status, 'failed')
            save_action(actor=self.actor, patient=self.patient, batch=batch, pages=[page], quota=config[0],
                context=config[1], request_row=None, operation='archive_page_retry')
        self.page.refresh_from_db()
        return Task.objects.get(id=self.page.current_task_id)

    def test_native_retry_preserves_manual_membership_and_receipt_while_new_writes_block(self):
        receipt, prepared = self.submit()
        original_page = (self.page.id, self.page.version, self.page.file_id, self.page.file_page_id,
            self.page.file_object_version, self.page.checksum)
        document = current_document(self.page)
        new_task = self.native_retry(self.retry_fixture())
        self.assertNotEqual(new_task.id, self.task.id)
        self.assertEqual(self.page.status, 'queued')
        self.assertEqual((self.page.id, self.page.version, self.page.file_id, self.page.file_page_id,
            self.page.file_object_version, self.page.checksum), original_page)
        self.assertEqual(current_document(self.page).id, document.id)
        self.assertTrue(OutboxEvent.objects.filter(task=new_task).exists())
        self.assertEqual(self.prepare(expected=409)['code'], 'DOCUMENT_PAGE_UNAVAILABLE')
        replay, _ = self.submit(prepared=prepared)
        self.assertEqual(replay, receipt)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'failed')
        self.assertFalse(any(self.clinical_counts().values()))

    def test_pending_document_intent_rejects_retry_queued_and_running(self):
        prepared = self.prepare()
        task = self.native_retry(self.retry_fixture())
        for page_status, task_status in (('queued', 'queued'), ('processing', 'running')):
            ArchivePage.objects.filter(id=self.page.id).update(status=page_status)
            Task.objects.filter(id=task.id).update(status=task_status)
            with self.subTest(page=page_status, task=task_status):
                rejected, _ = self.submit(prepared=prepared, expected=409)
                self.assertEqual(rejected['code'], 'DOCUMENT_PAGE_UNAVAILABLE')
        self.assert_no_document()

    def unassign_payload(self, document_id):
        return {'batch_id': str(self.batch.id),
            'sources': [{'document_id': document_id, 'expected_version': '1'}],
            'outputs': [], 'unassigned_page_ids': [str(self.page.id)],
            'reason': 'Synthetic human correction of document boundaries'}

    def test_all_unassigned_regroup_checks_batch_page_task_and_attempt_states(self):
        receipt, _ = self.submit()
        payload = self.unassign_payload(receipt['resource_id'])
        target = f'/patients/{self.patient.id}/archive-document-regroupings'
        for state in ('receiving', 'cancelled'):
            ArchiveBatch.objects.filter(id=self.batch.id).update(status=state)
            with self.subTest(batch=state):
                self.assertEqual(self.prepare(payload, target=target, operation='archive_documents_regroup',
                    expected=409)['code'], 'DOCUMENT_BATCH_UNAVAILABLE')
        ArchiveBatch.objects.filter(id=self.batch.id).update(status='failed')
        for state in ('queued', 'processing', 'cancel_requested', 'uncertain', 'cancelled'):
            ArchivePage.objects.filter(id=self.page.id).update(status=state)
            with self.subTest(page=state):
                self.assertEqual(self.prepare(payload, target=target, operation='archive_documents_regroup',
                    expected=409)['code'], 'DOCUMENT_PAGE_UNAVAILABLE')
        ArchivePage.objects.filter(id=self.page.id).update(status='failed')
        Task.objects.filter(id=self.task.id).update(status='uncertain')
        self.assertEqual(self.prepare(payload, target=target, operation='archive_documents_regroup',
            expected=409)['code'], 'DOCUMENT_PAGE_UNAVAILABLE')
        Task.objects.filter(id=self.task.id).update(status='failed')
        ArchivePageAttempt.objects.filter(id=self.page_attempt.id).update(status='running', finished_at=None)
        self.assertEqual(self.prepare(payload, target=target, operation='archive_documents_regroup',
            expected=409)['code'], 'DOCUMENT_PAGE_UNAVAILABLE')
        self.assertIsNone(ArchiveDocument.objects.get(id=receipt['resource_id']).retired_at)
        self.assertEqual(ArchiveDocumentVersion.objects.filter(document_id=receipt['resource_id']).count(), 1)

    def test_partial_output_cannot_hide_an_active_unassigned_source_page(self):
        first, _ = self.submit()
        with transaction.atomic():
            file = FileObject.objects.create(patient=self.patient, file_group_id=self.group.id,
                object_key=f'synthetic-second-original-{uuid4()}', object_version='1', checksum=self.file.checksum,
                size_bytes=self.file.size_bytes, media_type='image/png', purpose='original', status='available',
                retention_policy=self.fixture.retention, created_at=self.now)
            file_page = FilePage.objects.create(patient=self.patient, file_group_id=self.group.id, file_id=file.id,
                ordinal=2, width=12, height=18, rotation=0)
            page = ArchivePage.objects.create(patient_id=self.patient.id, batch_id=self.batch.id,
                file_page_id=file_page.id, file_id=file.id, file_object_version='1', checksum=file.checksum,
                ordinal=2, version=1, status='uploaded', status_version=1, available_at=self.now)
            ArchiveBatch.objects.filter(id=self.batch.id).update(status='partial')
        self.store.put(file.object_key, self.original_bytes)
        second, _ = self.submit(self.payload(page_ids=[str(page.id)]))
        payload = self.unassign_payload(first['resource_id'])
        payload['sources'].append({'document_id': second['resource_id'], 'expected_version': '1'})
        payload['outputs'] = [{**self.payload(page_ids=[str(page.id)]),
            'document_id': second['resource_id'], 'metadata_confirmed': True}]
        target = f'/patients/{self.patient.id}/archive-document-regroupings'
        for state in ('queued', 'processing', 'uncertain'):
            ArchivePage.objects.filter(id=self.page.id).update(status=state)
            with self.subTest(unassigned_page=state):
                self.assertEqual(self.prepare(payload, target=target, operation='archive_documents_regroup',
                    expected=409)['code'], 'DOCUMENT_PAGE_UNAVAILABLE')
        self.assertFalse(ArchiveDocument.objects.filter(patient_id=self.patient.id, retired_at__isnull=False).exists())

    def test_failed_page_unassignment_replays_exactly_after_later_retry(self):
        created, _ = self.submit()
        payload = self.unassign_payload(created['resource_id'])
        target = f'/patients/{self.patient.id}/archive-document-regroupings'
        receipt, prepared = self.submit(payload, target=target, operation='archive_documents_regroup')
        self.assertEqual(receipt['result']['outputs'], [])
        self.assertEqual(receipt['result']['retired_document_ids'], [created['resource_id']])
        self.assertIsNotNone(ArchiveDocument.objects.get(id=created['resource_id']).retired_at)
        self.native_retry(self.retry_fixture())
        replay, _ = self.submit(payload, target=target, prepared=prepared, operation='archive_documents_regroup')
        self.assertEqual(replay, receipt)
        recovered = self.client.get(f'/api/v2/requests/{prepared[0]["intent_id"]}')
        self.assertEqual(recovered.status_code, 200, recovered.content)
        self.assertEqual(recovered.json()['receipt'], receipt)
        self.assertFalse(any(self.clinical_counts().values()))

    def test_pending_unassignment_intent_cannot_retire_document_after_retry(self):
        created, _ = self.submit()
        payload = self.unassign_payload(created['resource_id'])
        target = f'/patients/{self.patient.id}/archive-document-regroupings'
        prepared = self.prepare(payload, target=target, operation='archive_documents_regroup')
        self.native_retry(self.retry_fixture())
        rejected, _ = self.submit(payload, target=target, prepared=prepared,
            operation='archive_documents_regroup', expected=409)
        self.assertEqual(rejected['code'], 'DOCUMENT_PAGE_UNAVAILABLE')
        self.assertIsNone(ArchiveDocument.objects.get(id=created['resource_id']).retired_at)

    def test_later_synthetic_candidate_adoption_resolves_current_manual_document(self):
        # Explicit synthetic candidate boundary verifies native later adoption;
        # manual document creation above does not make any candidate or fact.
        first, _ = self.submit()
        target = f'/archive-documents/{first["resource_id"]}/grouping'
        self.submit(self.payload(expected_version='1', reason='Human metadata correction'),
            target=target, operation='archive_document_group')
        current = current_document(self.page)
        self.assertEqual(current.version, 2)
        task = self.native_retry(self.retry_fixture())
        self.assertFalse(any(self.clinical_counts().values()))
        with transaction.atomic():
            candidate = CandidateSet.objects.create(patient=self.patient, file_group_id=self.group.id,
                file_group_version=1, task_id=task.id, provider='synthetic', model_version='synthetic-no-provider',
                provider_request_id='synthetic-result', schema_version=SCHEMA_VERSION,
                status='pending_confirmation', created_at=timezone.now())
            anchor = SourceAnchor.objects.create(patient=self.patient, page_id=self.file_page.id,
                anchor_kind='page', coordinate_system='decoded_pixel', original_text='Synthetic later candidate quote')
            field = CandidateField.objects.create(patient=self.patient, candidate_set_id=candidate.id,
                field_path='/items/1', field_config_version=self.runtime, anchor_id=anchor.id,
                recognition_status='recognized', raw_text=anchor.original_text)
            proposed = field_input(field.id, anchor.id, {'field_code': 'archive_text', 'value_type': 'text',
                'value_state': 'known', 'raw_text': 'Synthetic later candidate', 'sample_time_origin': 'unknown'})
            context = unknown_context()
            field.proposed_field = {'field': proposed, 'context': context, 'recognition_status': 'recognized'}
            field.save(update_fields=['proposed_field'])
            items = candidate_items(self.page, self.patient, candidate, [field])
            self.assertEqual(items[0]['document_version_id'], str(current.id))
            self.page.status, task.status = 'awaiting_confirmation', 'awaiting_confirmation'
            self.page.save(update_fields=['status'])
            task.save(update_fields=['status'])
            decision = {'candidate_id': str(field.id), 'decision': 'accepted', 'field': proposed,
                'context': context, 'reason': None}
            confirm_facts(self.actor, self.patient, {'items': [decision]},
                (self.page, candidate, [field], task, self.file, self.group, {str(field.id): items[0]}))
        fact = ArchiveFactVersion.objects.get(patient_id=self.patient.id)
        self.assertEqual((fact.document_version_id, fact.page_id, fact.source_anchor_id),
            (current.id, self.page.id, anchor.id))
        self.assertFalse(Verification.objects.filter(patient=self.patient).exists())
        self.assertFalse(FieldVerificationState.objects.filter(patient_id=self.patient.id).exists())
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'failed')

    def wait_blocked(self, ready, pids):
        self.assertTrue(ready.wait(10))
        deadline = monotonic() + 10
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute('SELECT cardinality(pg_blocking_pids(%s))', [pids[0]])
                if cursor.fetchone()[0] > 0:
                    return
            sleep(0.01)
        self.fail('Separate PostgreSQL connection never contended on the shared batch lock')

    def test_retry_wins_batch_lock_then_waiting_correction_rejects_queued_page(self):
        config = self.retry_fixture()
        ready, pids = Event(), []
        def correction():
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pids.append(cursor.fetchone()[0])
                ready.set()
                with transaction.atomic():
                    try:
                        document_materials(self.patient, self.payload())
                        return 'unexpected_success'
                    except QueryRejected as error:
                        return error.reason_code
            finally:
                connections['default'].close()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                ArchiveBatch.objects.select_for_update().get(id=self.batch.id)
                future = pool.submit(correction)
                self.wait_blocked(ready, pids)
                self.native_retry(config)
            self.assertEqual(future.result(timeout=15), 'DOCUMENT_PAGE_UNAVAILABLE')
        self.assert_no_document()
        self.page.refresh_from_db()
        self.assertEqual(self.page.status, 'queued')

    def test_correction_wins_batch_lock_then_waiting_retry_preserves_manual_version(self):
        config = self.retry_fixture()
        ready, pids = Event(), []
        def retry():
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pids.append(cursor.fetchone()[0])
                ready.set()
                return self.native_retry(config).id
            finally:
                connections['default'].close()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                document, version = save_document(patient=self.patient, actor=self.actor, payload=self.payload())
                future = pool.submit(retry)
                self.wait_blocked(ready, pids)
            new_task_id = future.result(timeout=15)
        self.page.refresh_from_db()
        self.assertEqual((self.page.status, self.page.current_task_id), ('queued', new_task_id))
        self.assertEqual(current_document(self.page).id, version.id)
        self.assertEqual(ArchiveDocumentPage.objects.filter(document_version_id=version.id, page_id=self.page.id).count(), 1)
        self.assertFalse(any(self.clinical_counts().values()))
