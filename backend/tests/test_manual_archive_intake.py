"""Real manual intake HTTP, originals, permissions and request receipts.

Only local synthetic PNGs and software-qualified fixtures. No provider, fake
task outcome, clinical episode or clinical verification is provisioned.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from unittest.mock import patch
from uuid import uuid4

from django.db import connection, connections, transaction
from django.test import Client, SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from api.base import ApiError
from archives.batch_creation import batch_input, batch_write_identity
from archives.models import ArchiveBatch, ArchiveDocument, ArchiveDocumentVersion, ArchivePage
from audit.models import AuditEvent
from authorization.models import AccessGrant, ServiceRoleGrant
from case_details.models import ClinicalEpisode
from core.demo_files_summary import ensure_demo_report_attachment
from files.models import FileObject
from identity.models import Patient, Principal
from recognition.models import CandidateSet
from relations.models import Relation, RelationApplication
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from tasks.models import Task
from tests.isolated_rows import IsolatedSchemaRows
from tests.synthetic_fixtures import SyntheticEvidence, ensure_synthetic_audit_policy
from verification.models import Verification


class ManualIntakeSchemaTests(SimpleTestCase):
    def test_manual_is_explicit_and_legacy_input_is_unchanged(self):
        base = {'file_group_ids': [str(uuid4())]}
        self.assertEqual(batch_input(base), base)
        for mode in ('manual', 'ocr'):
            self.assertEqual(batch_input({**base, 'intake_mode': mode})['intake_mode'], mode)
        for mode in ('completed', '', None, False):
            with self.subTest(mode=mode), self.assertRaises(ApiError):
                batch_input({**base, 'intake_mode': mode})


@override_settings(DEBUG=True, SJIA_ENVIRONMENT='test')
class ManualArchiveIntakeTests(TransactionTestCase):
    def setUp(self):
        rows = IsolatedSchemaRows(require_empty=True)
        self.addCleanup(rows.cleanup)
        with transaction.atomic():
            now = timezone.now()
            self.operator = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
            self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
            self.patient = Patient.objects.create(display_id='手工资料（示例）', status='active', created_at=now)
            self.fixture = SyntheticEvidence(self, self.operator)
            from files.storage import LocalFileStore
            self.store = LocalFileStore(self.fixture.root / 'objects')
            for name in ('files.service.object_store', 'files.purpose.object_store'):
                replacement = patch(name, self.store)
                replacement.start()
                self.addCleanup(replacement.stop)
            self.runtime = self.fixture.runtime()
            ensure_synthetic_audit_policy(self.operator)
            application = RelationApplication.objects.create(version=1, applicant=self.actor,
                patient=self.patient, evidence_ref=self.fixture.reference, status='approved',
                checked_by=self.operator, checked_at=now, created_at=now)
            self.relation = Relation.objects.create(patient=self.patient, guardian=self.actor,
                application=application, status='active', version=1, checked_by=self.operator,
                checked_at=now, valid_from=now)
            for action in ('write_record', 'read_raw', 'read_file', 'read_history'):
                AccessGrant.objects.create(patient=self.patient, principal=self.actor,
                    relation_id=self.relation.id, action=action, field_codes=[], record_scope='all',
                    purpose='family_observation', granted_by=self.operator,
                    evidence_ref=self.fixture.reference, valid_from=now)
            self.use = DataUseAuthorization.objects.create(patient=self.patient,
                source_revision=self.fixture.source, purpose='family_observation', environment='test',
                evidence_ref=self.fixture.reference, approved_by=self.operator, approved_at=now)
            policy = RetentionPolicy.objects.get(id__in=self.runtime.payload['retention_policy_ids'], data_class='file')
            self.group = ensure_demo_report_attachment(actor=self.actor, patient=self.patient,
                source_revision=self.fixture.source, use_authorization=self.use, retention_policy=policy,
                request_retention_policy=self.fixture.request_retention, namespace=uuid4(),
                report_key='manual-intake-fixture', report_date=date(2026, 10, 10),
                rows=[('Family-reported visit', 'Outpatient', '2026-10-01'),
                    ('Family-reported visit', 'Outpatient', '2026-10-10')])
            self.original = FileObject.objects.get(file_group_id=self.group.id, purpose='original')
        session = self.client.session
        session['_sjia_principal_id'] = str(self.actor.id)
        session['_sjia_auth_epoch'] = self.actor.auth_epoch
        session['_sjia_audience'] = 'family'
        session.save()
        from django.contrib.sessions.models import Session
        key = session.session_key
        self.addCleanup(lambda: Session.objects.filter(session_key=key).delete())

    def prepare(self, operation, target, payload, *, request_id=None, expected=201):
        identity = request_id or str(uuid4())
        response = self.client.post('/api/v2/write-intents', {'operation_id': operation,
            'target_key': target, 'request_id': identity, 'schema_version': '2.0.0', 'payload': payload},
            content_type='application/json')
        self.assertEqual(response.status_code, expected, response.content)
        if expected != 201:
            return response.json()
        data = response.json()
        return data, {'HTTP_X_WRITE_INTENT': data['intent_id'],
            'HTTP_X_INTENT_GENERATION': data['generation'], 'HTTP_IDEMPOTENCY_KEY': identity}

    def batch_payload(self):
        return {'file_group_ids': [str(self.group.id)], 'intake_mode': 'manual'}

    def receive(self):
        target, payload = f'/patients/{self.patient.id}/archive-batches', self.batch_payload()
        prepared = self.prepare('archive_batch_create', target, payload)
        response = self.client.post('/api/v2' + target, payload,
            content_type='application/json', **prepared[1])
        self.assertEqual(response.status_code, 201, response.content)
        return ArchiveBatch.objects.get(id=response.json()['resource_id']), response.json(), prepared

    def document_payload(self, batch):
        return {'batch_id': str(batch.id),
            'page_ids': [str(page.id) for page in ArchivePage.objects.filter(batch_id=batch.id).order_by('ordinal')],
            'document_type': 'outpatient', 'organization': '示例来源（家庭补充）',
            'documented_time': None, 'episode_ids': [],
            'confirmed_text': 'Family-reported visit | Outpatient | 2026-10-01\nFamily-reported visit | Outpatient | 2026-10-10',
            'proposed_metadata': {'episodes': [
                {'kind': 'outpatient', 'start_time': None, 'end_time': None, 'description': f'示例家庭自报就诊 {index}'}
                for index in (1, 2)], 'grouping_note': '家庭补充，待核对；不是专业就诊记录。'}}

    def save_document(self, batch):
        target, payload = f'/patients/{self.patient.id}/archive-documents', self.document_payload(batch)
        prepared = self.prepare('archive_document_create', target, payload)
        response = self.client.post('/api/v2' + target, payload,
            content_type='application/json', **prepared[1])
        self.assertEqual(response.status_code, 201, response.content)
        return ArchiveDocument.objects.get(id=response.json()['resource_id']), response.json(), prepared

    def test_real_provider_free_receive_document_read_and_original_chain(self):
        batch, receipt, _ = self.receive()
        self.assertEqual((batch.intake_mode, batch.quota_policy_id, batch.status, batch.budget_status),
            ('manual', None, 'receiving', 'unknown'))
        page = ArchivePage.objects.get(batch_id=batch.id)
        self.assertEqual((page.status, page.current_task_id, page.checksum, page.file_object_version),
            ('uploaded', None, self.original.checksum, self.original.object_version))
        response = self.client.get(f'/api/v2/archive-batches/{batch.id}')
        self.assertEqual(response.status_code, 200, response.content)
        view = response.json()
        self.assertEqual(view['intake_mode'], 'manual')
        self.assertEqual(view['allowed_actions'], [])
        self.assertEqual(view['pages'][0]['allowed_actions'], [])
        self.assertEqual(view['pages'][0]['attempts'], [])
        document, saved, _ = self.save_document(batch)
        response = self.client.get(f'/api/v2/archive-documents/{document.id}')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['grouping_status'], 'pending')
        self.assertEqual(response.json()['episode_ids'], [])
        self.assertEqual(len(response.json()['proposed_metadata']['episodes']), 2)
        response = self.client.get(f'/api/files/{self.original.id}/content')
        self.assertEqual(response.status_code, 200, response.content if not response.streaming else '')
        content = b''.join(response.streaming_content) if response.streaming else response.content
        self.assertTrue(content.startswith(b'\x89PNG'))
        response.close()
        self.assertTrue(AuditEvent.objects.filter(action='archive_batch_create', request_id=receipt['request_id']).exists())
        self.assertTrue(AuditEvent.objects.filter(action='archive_document_create', request_id=saved['request_id']).exists())
        self.assertFalse(Task.objects.filter(task_type='ocr').exists())
        self.assertFalse(CandidateSet.objects.exists())
        self.assertFalse(ClinicalEpisode.objects.exists())
        self.assertFalse(Verification.objects.exists())
        self.assertFalse(ServiceRoleGrant.objects.filter(principal=self.actor).exists())

    def test_native_batch_and_document_receipts_replay_without_new_versions(self):
        batch, receipt, prepared = self.receive()
        response = self.client.post(f'/api/v2/patients/{self.patient.id}/archive-batches', self.batch_payload(),
            content_type='application/json', **prepared[1])
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json(), receipt)
        document, saved, prepared = self.save_document(batch)
        response = self.client.post(f'/api/v2/patients/{self.patient.id}/archive-documents', self.document_payload(batch),
            content_type='application/json', **prepared[1])
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json(), saved)
        self.assertEqual(ArchiveBatch.objects.count(), 1)
        self.assertEqual(ArchiveDocumentVersion.objects.filter(document_id=document.id).count(), 1)

    def test_same_original_cannot_enter_a_second_batch(self):
        self.receive()
        result = self.prepare('archive_batch_create', f'/patients/{self.patient.id}/archive-batches',
            self.batch_payload(), expected=409)
        self.assertEqual(result['code'], 'ARCHIVE_SOURCE_ALREADY_RECEIVED')

    def test_default_ocr_path_still_requires_qualified_quota(self):
        result = self.prepare('archive_batch_create', f'/patients/{self.patient.id}/archive-batches',
            {'file_group_ids': [str(self.group.id)]}, expected=503)
        self.assertEqual(result['code'], 'ARCHIVE_QUOTA_CONFIGURATION_PENDING')
        self.assertFalse(ArchiveBatch.objects.exists())

    def test_manual_batch_cannot_run_or_retry_ocr_even_with_explicit_request(self):
        batch, _, _ = self.receive()
        page = ArchivePage.objects.get(batch_id=batch.id)
        cases = [('archive_batch_process', f'/archive-batches/{batch.id}/process',
            {'expected_version': '1', 'page_ids': [str(page.id)]}),
            ('archive_page_retry', f'/archive-pages/{page.id}/retry',
                {'expected_status_version': '1', 'reason': 'explicit test'}),
            ('archive_page_cancel', f'/archive-pages/{page.id}/cancel',
                {'expected_status_version': '1', 'reason': 'explicit test'})]
        for operation, target, payload in cases:
            result = self.prepare(operation, target, payload, expected=409)
            self.assertEqual(result['code'], 'ARCHIVE_MANUAL_BATCH_ACTION_UNSUPPORTED')
        self.assertFalse(Task.objects.filter(task_type='ocr').exists())

    def test_revoked_source_use_between_prepare_and_submit_rolls_back(self):
        target, payload = f'/patients/{self.patient.id}/archive-batches', self.batch_payload()
        prepared = self.prepare('archive_batch_create', target, payload)
        self.use.revoked_at = timezone.now()
        self.use.save(update_fields=['revoked_at'])
        response = self.client.post('/api/v2' + target, payload, content_type='application/json', **prepared[1])
        self.assertGreaterEqual(response.status_code, 400, response.content)
        self.assertLess(response.status_code, 500, response.content)
        self.assertFalse(ArchiveBatch.objects.exists())

    def test_missing_whole_original_read_permission_cannot_receive_or_group(self):
        AccessGrant.objects.filter(principal=self.actor, action='read_file').update(field_codes=['unrelated'])
        result = self.prepare('archive_batch_create', f'/patients/{self.patient.id}/archive-batches',
            self.batch_payload(), expected=403)
        self.assertEqual(result['code'], 'CASE_ACTION_DENIED')
        self.assertFalse(ArchiveBatch.objects.exists())

    def test_changed_intake_mode_cannot_reuse_manual_receipt(self):
        _, _, prepared = self.receive()
        response = self.client.post(f'/api/v2/patients/{self.patient.id}/archive-batches',
            {**self.batch_payload(), 'intake_mode': 'ocr'}, content_type='application/json', **prepared[1])
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['code'], 'ARCHIVE_BATCH_INTENT_MISMATCH')

    def test_original_byte_integrity_is_checked_before_receiving(self):
        (self.store.root / self.original.object_key).write_bytes(b'changed synthetic bytes')
        target, payload = f'/patients/{self.patient.id}/archive-batches', self.batch_payload()
        prepared = self.prepare('archive_batch_create', target, payload)
        response = self.client.post('/api/v2' + target, payload, content_type='application/json', **prepared[1])
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(response.json()['code'], 'PURPOSE_CHECKSUM_MISMATCH')
        self.assertFalse(ArchiveBatch.objects.exists())

    def test_document_replay_preserves_later_manual_correction(self):
        batch, _, _ = self.receive()
        document, original_receipt, prepared = self.save_document(batch)
        target = f'/archive-documents/{document.id}/grouping'
        changed = {**self.document_payload(batch), 'expected_version': '1',
            'reason': '本人补充说明', 'confirmed_text': '本人后来补充的说明'}
        correction = self.prepare('archive_document_group', target, changed)
        response = self.client.post('/api/v2' + target, changed, content_type='application/json', **correction[1])
        self.assertEqual(response.status_code, 200, response.content)
        replay = self.client.post(f'/api/v2/patients/{self.patient.id}/archive-documents',
            self.document_payload(batch), content_type='application/json', **prepared[1])
        self.assertEqual(replay.status_code, 201, replay.content)
        self.assertEqual(replay.json(), original_receipt)
        document.refresh_from_db()
        self.assertEqual(document.current_version, 2)
        self.assertEqual(ArchiveDocumentVersion.objects.get(document_id=document.id, version=2).confirmed_text,
            '本人后来补充的说明')

    def test_revoked_relation_after_receipt_blocks_readback(self):
        batch, _, _ = self.receive()
        self.relation.revoked_at = timezone.now()
        self.relation.save(update_fields=['revoked_at'])
        response = self.client.get(f'/api/v2/archive-batches/{batch.id}')
        self.assertEqual(response.status_code, 403, response.content)

    def test_foreign_patient_originals_cannot_be_received(self):
        other = Patient.objects.create(display_id='OTHER-SYNTHETIC', status='active', created_at=timezone.now())
        result = self.prepare('archive_batch_create', f'/patients/{other.id}/archive-batches',
            self.batch_payload(), expected=403)
        self.assertEqual(result['code'], 'CASE_ACTION_DENIED')
        self.assertFalse(ArchiveBatch.objects.exists())

    def test_authority_locks_have_gate_actor_patient_order(self):
        statements = []
        def capture(execute, sql, params, many, context):
            if 'FOR UPDATE' in sql:
                statements.append(sql)
            return execute(sql, params, many, context)
        with transaction.atomic(), connection.execute_wrapper(capture):
            batch_write_identity(self.actor, self.actor.auth_epoch, self.patient.id)
        positions = [next(index for index, sql in enumerate(statements) if f'FROM "sjia"."{name}"' in sql)
            for name in ('environment_gate', 'principal', 'patient')]
        self.assertEqual(positions, sorted(positions))

    def test_concurrent_same_submission_has_one_real_receipt(self):
        target, payload = f'/patients/{self.patient.id}/archive-batches', self.batch_payload()
        prepared = self.prepare('archive_batch_create', target, payload)
        cookies = deepcopy(self.client.cookies)
        def submit():
            try:
                client = Client()
                client.cookies = deepcopy(cookies)
                response = client.post('/api/v2' + target, payload,
                    content_type='application/json', **prepared[1])
                return response.status_code, response.json()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: submit(), range(2)))
        self.assertEqual([status for status, _ in results], [201, 201], results)
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(ArchiveBatch.objects.count(), 1)
        self.assertEqual(ArchivePage.objects.count(), 1)
