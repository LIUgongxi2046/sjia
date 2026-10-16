"""Synthetic-only archive regrouping persistence/protocol regressions.

Lead runs this label on PostgreSQL. No external provider, patient files or real
approval records. TestCase rollback isolates every generated fixture.
"""
import copy
import hashlib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import transaction
from django.test import SimpleTestCase, TestCase, override_settings, RequestFactory
from django.utils import timezone

from api.base import ApiError
from archives.documents import document_read, document_versions
from archives.fact_candidates import current_document
from archives.models import (Archive, ArchiveBatch, ArchiveDocument, ArchiveDocumentVersion,
    ArchiveDocumentPage, ArchiveDocumentEpisode, ArchivePage, ArchiveFact, ArchiveFactVersion, ArchiveRegrouping)
from archives.record_facts import create_record_facts
from archives.regrouping import regroup_input, validate_partition, regroup_scope, save_regrouping
from assessment.provenance import document_provenance
from authorization.models import AccessGrant
from case_details.models import ClinicalEpisode
from dependencies.models import DependencyNode
from files.models import FileGroup, FileObject, FilePage
from files.purpose_dependencies import resource_node, connect
from files.storage import LocalFileStore
from identity.models import Patient
from medical_time.models import MedicalTime
from recognition.models import SourceAnchor
from records.models import Record, RecordField, RecordVersion, RecordAttachment
from records.queries import QueryRejected
from requests.models import WriteIntent
from sources.models import DataUseAuthorization
from tests import test_record_access_boundaries as boundaries
from tests.synthetic_fixtures import SyntheticEvidence


def output(batch, pages, document=None, episodes=()):
    return {'document_id': str(document.id) if document else None, 'batch_id': str(batch.id),
        'page_ids': [str(page.id) for page in pages], 'document_type': 'laboratory',
        'organization': 'SYNTHETIC SOFTWARE FIXTURE', 'documented_time': None,
        'episode_ids': [str(value.id) for value in episodes], 'confirmed_text': 'Synthetic boundary confirmation',
        'proposed_metadata': None, 'metadata_confirmed': True}


class RegroupInputTests(SimpleTestCase):
    def setUp(self):
        self.batch, self.doc, self.page = [SimpleNamespace(id=uuid4()) for _ in range(3)]
        self.payload = {'batch_id': str(self.batch.id), 'sources': [{'document_id': str(self.doc.id), 'expected_version': '1'}],
            'outputs': [output(self.batch, [self.page], self.doc)], 'unassigned_page_ids': [], 'reason': 'Synthetic split'}

    def test_exact_partition_and_metadata_confirmation(self):
        regroup_input(self.payload); validate_partition(self.payload, [self.page.id])
        self.payload['outputs'][0]['metadata_confirmed'] = False
        with self.assertRaises(ApiError): regroup_input(self.payload)

    def test_duplicate_missing_foreign_page_or_reused_identity_rejected(self):
        for mutation in ('duplicate', 'missing', 'foreign', 'identity', 'batch', 'reason'):
            data = copy.deepcopy(self.payload)
            if mutation == 'duplicate': data['unassigned_page_ids'] = [str(self.page.id)]
            if mutation == 'missing': data['outputs'] = []
            if mutation == 'foreign': data['outputs'][0]['page_ids'] = [str(uuid4())]
            if mutation == 'identity': data['outputs'][0]['document_id'] = str(uuid4())
            if mutation == 'batch': data['outputs'][0]['batch_id'] = str(uuid4())
            if mutation == 'reason': data['reason'] = '  '
            with self.subTest(mutation=mutation), self.assertRaises(ApiError):
                regroup_input(data); validate_partition(data, [self.page.id])


@override_settings(SJIA_ENVIRONMENT='test')
class ArchiveRegroupingTests(TestCase):
    _grant = boundaries.RecordAccessBoundaryTest._grant

    def setUp(self):
        boundaries.RecordAccessBoundaryTest.setUp(self)
        self.actor = self.principal
        self.fixture = SyntheticEvidence(self, self.actor)
        self.runtime = self.fixture.runtime()
        for action in ('read_raw', 'read_file', 'write_record', 'correct_record', 'read_history'):
            self._grant(action=action, codes=())
            if action != 'read_file':
                self._grant(action=action, codes=('synthetic_report',))
        now = timezone.now()
        self.use = DataUseAuthorization.objects.create(patient=self.patient, source_revision=self.fixture.source,
            purpose='family_observation', environment='test', evidence_ref='synthetic-test-only',
            approved_by=self.actor, approved_at=now)
        group = FileGroup.objects.create(patient=self.patient, owner=self.actor, version=1, status='available',
            source_revision=self.fixture.source, use_authorization_id=self.use.id, created_at=now)
        data = b'SYNTHETIC SOFTWARE FIXTURE ONLY: three source pages'
        self.file = FileObject.objects.create(patient=self.patient, file_group_id=group.id, object_key=f'synthetic-{uuid4()}',
            object_version='1', checksum=hashlib.sha256(data).hexdigest(), size_bytes=len(data), media_type='text/plain',
            purpose='original', status='available', retention_policy=self.fixture.retention, created_at=now)
        from django.conf import settings
        store = LocalFileStore(settings.FILE_OBJECT_ROOT)
        store.put(self.file.object_key, data)
        storage = patch('files.purpose.object_store', store)
        storage.start(); self.addCleanup(storage.stop)
        archive = Archive.objects.create(patient_id=self.patient.id, created_at=now)
        self.batch = ArchiveBatch.objects.create(patient_id=self.patient.id, archive_id=archive.id, submitted_by=self.actor.id,
            logical_request_id=uuid4(), version=1, received_at=now, quota_policy_id=self.runtime.id, budget_status='reserved', status='archived')
        self.pages, self.file_pages, self.anchors = [], [], []
        for ordinal in range(1, 4):
            page = FilePage.objects.create(patient=self.patient, file_group_id=group.id, file_id=self.file.id,
                ordinal=ordinal, width=10, height=10, rotation=0)
            self.file_pages.append(page)
            self.pages.append(ArchivePage.objects.create(patient_id=self.patient.id, batch_id=self.batch.id,
                file_page_id=page.id, file_id=self.file.id, file_object_version='1', checksum=self.file.checksum,
                ordinal=ordinal, version=1, status='confirmed', status_version=1, available_at=now))
            self.anchors.append(SourceAnchor.objects.create(patient=self.patient, page_id=page.id,
                anchor_kind='page', coordinate_system='decoded_pixel', original_text=f'Synthetic page {ordinal}'))
        self.record = Record.objects.create(patient=self.patient, kind='report', current_version=1, status='active', created_at=now)
        self.version = RecordVersion.objects.create(patient=self.patient, record_id=self.record.id, version=1,
            author=self.actor, origin_type='report_transcription', capture_method='manual', source_revision=self.fixture.source,
            use_authorization_id=self.use.id, quality_status='valid', quality_reasons=[], recorded_at=now, retention_policy=self.fixture.retention)
        RecordAttachment.objects.create(patient_id=self.patient.id, record_version_id=self.version.id, file_id=self.file.id, ordinal=1)
        self.time = MedicalTime.objects.create(patient=self.patient, precision='year', local_year=2020,
            raw_text='Synthetic original year', resolution_status='local_only')
        self.fields = [RecordField.objects.create(patient=self.patient, record_version_id=self.version.id, ordinal=index + 1,
            field_code='synthetic_report', concept_code='synthetic', original_name=f'Original {index}', value_type='text', value_state='known',
            raw_text=f'Unchanged {index}', medical_time_id=self.time.id, sample_time_origin='item', anchor_id=anchor.id, quality_reasons=[])
            for index, anchor in enumerate(self.anchors)]
        create_record_facts(self.version)
        self.documents, self.versions = [], []
        for selected in (self.pages[:2], self.pages[2:]):
            document = ArchiveDocument.objects.create(patient_id=self.patient.id, current_version=1, created_at=now)
            version = ArchiveDocumentVersion.objects.create(patient_id=self.patient.id, document_id=document.id,
                version=1, batch_id=self.batch.id, document_type='laboratory', organization='SYNTHETIC', grouping_status='confirmed',
                available_at=now, confirmed_by=self.actor.id, confirmed_at=now, confirmed_text='Original synthetic text')
            for ordinal, page in enumerate(selected, 1):
                ArchiveDocumentPage.objects.create(patient_id=self.patient.id, document_version_id=version.id, page_id=page.id, page_order=ordinal)
                field = self.fields[self.pages.index(page)]
                ArchiveFactVersion.objects.filter(field_id=field.id).update(document_version_id=version.id,
                    page_id=page.id, context_type='current', assertion_state='present')
            self.documents.append(document); self.versions.append(version)
        self.request = RequestFactory().post('/synthetic')
        self.request.session = self.client.session

    def payload(self, outputs=None, sources=None, unassigned=()):
        sources = self.documents if sources is None else sources
        return {'batch_id': str(self.batch.id), 'sources': [{'document_id': str(value.id), 'expected_version': str(value.current_version)} for value in sources],
            'outputs': outputs if outputs is not None else [output(self.batch, self.pages, self.documents[0])],
            'unassigned_page_ids': [str(page.id) for page in unassigned], 'reason': 'Synthetic boundary correction'}

    def scope(self, payload):
        return regroup_scope(self.request, self.actor, self.actor.auth_epoch, self.patient.id, payload)

    def save(self, payload):
        return save_regrouping(actor=self.actor, payload=payload, scope=self.scope(payload), request_id=uuid4(), input_sha256='d' * 64)

    def current_facts(self):
        return [ArchiveFactVersion.objects.get(fact_id=fact.id, version=fact.current_version) for fact in ArchiveFact.objects.filter(patient_id=self.patient.id)]

    def test_split_preserves_originals_and_field_time_anchor_lineage(self):
        before = list(ArchiveFactVersion.objects.filter(patient_id=self.patient.id).order_by('id').values())
        row = self.save(self.payload([output(self.batch, [self.pages[0]], self.documents[0]), output(self.batch, [self.pages[1]])],
            sources=[self.documents[0]]))
        self.assertEqual(len(row.result_refs['outputs']), 2)
        self.assertEqual(list(ArchiveFactVersion.objects.filter(id__in=[value['id'] for value in before]).order_by('id').values()), before)
        self.assertEqual(FileObject.objects.get(id=self.file.id).checksum, self.file.checksum)
        fields = RecordField.objects.filter(record_version_id=RecordVersion.objects.get(record_id=self.record.id, version=2).id).order_by('ordinal')
        self.assertEqual([(value.raw_text, value.medical_time_id, value.anchor_id) for value in fields],
            [(value.raw_text, value.medical_time_id, value.anchor_id) for value in self.fields])
        self.assertEqual({fact.page_id: fact.document_version_id for fact in self.current_facts()}[self.pages[2].id], self.versions[1].id)

    def test_merge_retires_source_without_deleting_history_and_corrects_shared_record_once(self):
        row = self.save(self.payload())
        self.record.refresh_from_db(); self.documents[1].refresh_from_db()
        self.assertEqual(self.record.current_version, 2)
        self.assertEqual(self.documents[1].retirement_id, row.id)
        self.assertEqual(ArchiveDocumentVersion.objects.filter(document_id=self.documents[1].id).count(), 1)
        self.assertEqual(len({fact.record_version_id for fact in self.current_facts()}), 1)
        self.assertEqual(len({fact.document_version_id for fact in self.current_facts()}), 1)
        with self.assertRaisesRegex(QueryRejected, 'DOCUMENT_RETIRED'):
            document_read(document=self.documents[1], patient=self.patient)
        self.assertEqual(document_read(document=self.documents[1], patient=self.patient, version=self.versions[1])['page_ids'], [str(self.pages[2].id)])

    def test_reorder_and_explicit_unassigned_pages(self):
        row = self.save(self.payload([output(self.batch, [self.pages[1], self.pages[0]], self.documents[0])], unassigned=[self.pages[2]]))
        self.assertEqual(row.result_refs['outputs'][0]['page_ids'], [str(self.pages[1].id), str(self.pages[0].id)])
        self.assertIsNone(next(fact for fact in self.current_facts() if fact.page_id == self.pages[2].id).document_version_id)
        self.assertIsNone(current_document(self.pages[2]))

    def test_all_pages_may_be_explicitly_unassigned_without_fabricating_empty_document(self):
        row = self.save(self.payload([], unassigned=self.pages))
        self.assertEqual(row.result_refs['outputs'], [])
        self.assertEqual(len(row.result_refs['retired_document_ids']), 2)
        self.assertTrue(all(fact.document_version_id is None for fact in self.current_facts()))

    def test_multi_episode_assignment_requires_exact_source_page_and_retains_links(self):
        episodes = [ClinicalEpisode.objects.create(patient_id=self.patient.id, record_version_id=self.version.id,
            source_field_id=field.id, episode_type='outpatient', label='Synthetic encounter') for field in self.fields]
        for field, episode in zip(self.fields, episodes):
            ArchiveFactVersion.objects.filter(field_id=field.id).update(episode_id=episode.id)
        payload = self.payload([output(self.batch, self.pages, self.documents[0], episodes)])
        row = self.save(payload)
        self.assertEqual(ArchiveDocumentEpisode.objects.filter(document_version_id=row.result_refs['outputs'][0]['version_id']).count(), 3)

    def test_missing_required_fact_episode_is_rejected(self):
        episode = ClinicalEpisode.objects.create(patient_id=self.patient.id, record_version_id=self.version.id,
            source_field_id=self.fields[0].id, episode_type='outpatient', label='Synthetic encounter')
        ArchiveFactVersion.objects.filter(field_id=self.fields[0].id).update(episode_id=episode.id)
        with self.assertRaisesRegex(ApiError, 'DOCUMENT_FACT_EPISODE_REQUIRED'): self.scope(self.payload())

    def test_existing_active_page_guard_remains_for_unselected_source(self):
        payload = self.payload([output(self.batch, self.pages, self.documents[0])], sources=[self.documents[0]])
        with self.assertRaisesRegex(ApiError, 'DOCUMENT_REGROUP_PAGE_PARTITION_INVALID'): self.scope(payload)
        from archives.grouping import document_materials
        regular = {key: value for key, value in payload['outputs'][0].items() if key not in ('document_id', 'metadata_confirmed')}
        regular.update(expected_version='1', reason='synthetic')
        with self.assertRaisesRegex(QueryRejected, 'DOCUMENT_COPY_CONFIRMATION_REQUIRED'):
            document_materials(self.patient, regular, self.documents[0])

    def test_cross_patient_batch_and_stale_versions_rejected(self):
        for kind in ('patient', 'batch', 'stale'):
            payload = self.payload()
            if kind == 'patient':
                other = Patient.objects.create(display_id=f'SYNTHETIC-OTHER-{uuid4()}', status='active', created_at=timezone.now())
                archive = Archive.objects.create(patient_id=other.id, created_at=timezone.now())
                batch = ArchiveBatch.objects.create(patient_id=other.id, archive_id=archive.id, submitted_by=self.actor.id,
                    logical_request_id=uuid4(), version=1, received_at=timezone.now(), quota_policy_id=self.runtime.id, budget_status='reserved', status='archived')
                document = ArchiveDocument.objects.create(patient_id=other.id, current_version=1, created_at=timezone.now())
                ArchiveDocumentVersion.objects.create(patient_id=other.id, document_id=document.id, version=1,
                    batch_id=batch.id, document_type='unknown', grouping_status='pending', available_at=timezone.now())
                payload['sources'][0]['document_id'] = str(document.id); payload['outputs'][0]['document_id'] = None
            elif kind == 'batch':
                other = ArchiveBatch.objects.create(patient_id=self.patient.id, archive_id=self.batch.archive_id, submitted_by=self.actor.id,
                    logical_request_id=uuid4(), version=1, received_at=timezone.now(), quota_policy_id=self.runtime.id, budget_status='reserved', status='archived')
                payload['batch_id'] = str(other.id); payload['outputs'][0]['batch_id'] = str(other.id)
            else: payload['sources'][0]['expected_version'] = '2'
            with self.subTest(kind=kind), self.assertRaises(ApiError): self.scope(payload)

    def test_correct_record_and_original_read_permissions_are_required(self):
        for action in ('correct_record', 'read_file', 'write_record', 'read_raw'):
            grants = AccessGrant.objects.filter(principal=self.actor, patient=self.patient, action=action)
            grants.update(revoked_at=timezone.now())
            with self.subTest(action=action), self.assertRaises(ApiError): self.scope(self.payload())
            grants.update(revoked_at=None)

    def test_failure_after_output_creation_rolls_back_all_versions_and_retirement(self):
        before = (ArchiveDocument.objects.count(), ArchiveDocumentVersion.objects.count(), ArchiveFactVersion.objects.count())
        with patch('archives.regrouping.rebind_facts', side_effect=RuntimeError('synthetic rollback')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic rollback'): self.save(self.payload())
        self.assertEqual((ArchiveDocument.objects.count(), ArchiveDocumentVersion.objects.count(), ArchiveFactVersion.objects.count()), before)
        self.assertFalse(ArchiveDocument.objects.filter(retired_at__isnull=False).exists())
        self.assertFalse(ArchiveRegrouping.objects.exists())

    def test_exact_request_replay_keeps_receipt_after_later_document_versions(self):
        target = f'/api/v2/patients/{self.patient.id}/archive-document-regroupings'
        payload = self.payload(); request_id = str(uuid4())
        prepared = self.client.post('/api/v2/write-intents', {'operation_id': 'archive_documents_regroup',
            'target_key': target.removeprefix('/api/v2'), 'request_id': request_id, 'schema_version': '2.0.0', 'payload': payload}, content_type='application/json')
        self.assertEqual(prepared.status_code, 201, prepared.content)
        intent = prepared.json()
        headers = {'HTTP_X_WRITE_INTENT': intent['intent_id'], 'HTTP_IDEMPOTENCY_KEY': request_id, 'HTTP_X_INTENT_GENERATION': intent['generation']}
        first = self.client.post(target, payload, content_type='application/json', **headers)
        self.assertEqual(first.status_code, 200, first.content)
        second = self.client.post(target, payload, content_type='application/json', **headers)
        self.assertEqual(second.status_code, 200, second.content); self.assertEqual(first.json(), second.json())
        self.assertEqual(ArchiveRegrouping.objects.count(), 1)
        self.assertEqual(WriteIntent.objects.get(id=intent['intent_id']).status, 'committed')
        # A later valid reorder changes the current document/record versions;
        # the first request still recovers its original exact output versions.
        self.documents[0].refresh_from_db()
        self.save(self.payload([output(self.batch, list(reversed(self.pages)), self.documents[0])], sources=[self.documents[0]]))
        self.assertEqual(ArchiveRegrouping.objects.count(), 2)
        history = AccessGrant.objects.filter(patient=self.patient, principal=self.actor, action='read_history')
        history.update(revoked_at=timezone.now())
        self.assertEqual(self.client.get(f'/api/v2/requests/{intent["intent_id"]}').status_code, 403)
        history.update(revoked_at=None)
        recovery = self.client.get(f'/api/v2/requests/{intent["intent_id"]}')
        self.assertEqual(recovery.status_code, 200, recovery.content)
        self.assertEqual(recovery.json()['receipt'], first.json())
        after_later_change = self.client.post(target, payload, content_type='application/json', **headers)
        self.assertEqual(after_later_change.status_code, 200, after_later_change.content)
        self.assertEqual(after_later_change.json(), first.json())
        changed = copy.deepcopy(payload); changed['reason'] = 'different'
        self.assertEqual(self.client.post(target, changed, content_type='application/json', **headers).status_code, 409)

    def test_prior_document_intent_receipt_recovers_after_its_identity_is_retired(self):
        target = f'/api/v2/archive-documents/{self.documents[0].id}/grouping'
        payload = {key: value for key, value in output(self.batch, self.pages[:2], self.documents[0]).items()
            if key not in ('document_id', 'metadata_confirmed')}
        payload.update(expected_version='1', reason='Synthetic prior grouping')
        request_id = str(uuid4())
        prepared = self.client.post('/api/v2/write-intents', {'operation_id': 'archive_document_group',
            'target_key': target.removeprefix('/api/v2'), 'request_id': request_id, 'schema_version': '2.0.0',
            'payload': payload}, content_type='application/json')
        self.assertEqual(prepared.status_code, 201, prepared.content)
        intent = prepared.json()
        headers = {'HTTP_X_WRITE_INTENT': intent['intent_id'], 'HTTP_IDEMPOTENCY_KEY': request_id,
            'HTTP_X_INTENT_GENERATION': intent['generation']}
        original = self.client.post(target, payload, content_type='application/json', **headers)
        self.assertEqual(original.status_code, 200, original.content)
        self.documents[0].refresh_from_db()
        self.save(self.payload([output(self.batch, self.pages, self.documents[1])]))
        self.documents[0].refresh_from_db(); self.assertIsNotNone(self.documents[0].retired_at)
        recovered = self.client.get(f'/api/v2/requests/{intent["intent_id"]}')
        self.assertEqual(recovered.status_code, 200, recovered.content)
        self.assertEqual(recovered.json()['receipt'], original.json())
        replay = self.client.post(target, payload, content_type='application/json', **headers)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.json(), original.json())

    def test_epoch_history_cursor_and_summary_dependencies_are_invalidated(self):
        node = resource_node('archive_document', self.documents[0].id, '1', self.patient.id)
        summary = resource_node('synthetic_summary', uuid4(), '1', self.patient.id); connect(summary, node, 'source')
        before = self.patient.data_epoch
        self.save(self.payload()); self.patient.refresh_from_db(); self.documents[0].refresh_from_db()
        summary.refresh_from_db(); self.assertEqual(summary.validity, 'invalid')
        self.assertGreater(self.patient.data_epoch, before)
        self.assertEqual(len(document_versions(document=self.documents[0], patient=self.patient, scope={}, limit=1,
            expires_at=timezone.now() + timedelta(minutes=5))['items']), 1)
        first = document_versions(document=self.documents[0], patient=self.patient, scope={}, limit=1,
            expires_at=timezone.now() + timedelta(minutes=5))
        self.patient.data_epoch += 1
        with self.assertRaisesRegex(QueryRejected, 'CURSOR_STALE'):
            document_versions(document=self.documents[0], patient=self.patient, scope={}, limit=1,
                expires_at=timezone.now() + timedelta(minutes=5), cursor=first['next_cursor'])

    def test_retired_document_replay_before_cutoff_keeps_immutable_source(self):
        fact = ArchiveFactVersion.objects.get(field_id=self.fields[2].id)
        self.save(self.payload()); self.documents[1].refresh_from_db()
        cutoff = self.documents[1].retired_at - timedelta(microseconds=1)
        self.assertIsNone(document_provenance(fact, self.file_pages[2], self.file, known_at=cutoff, mode='replay')[1])
        self.assertEqual(document_provenance(fact, self.file_pages[2], self.file, known_at=timezone.now(), mode='current')[1], 'FACT_DOCUMENT_RETIRED')
