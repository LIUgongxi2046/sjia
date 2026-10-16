"""Synthetic-only professional creation/correction contract and domain regression."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from assessment.qualified_facts import QualificationRejected
from authorization.models import AccessGrant, ServiceRoleGrant
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from records.models import Record, RecordField, RecordVersion
from records.professional import prepare_professional_intent, professional_payload, submit_professional_record
from records.service import RecordRejected
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision
from verification.models import Verification


class ProfessionalRecordTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id='SYNTHETIC-PROFESSIONAL', status='active', created_at=now)
        self.gate = EnvironmentGate.objects.create(environment=settings.SJIA_ENVIRONMENT, version=1, enabled=True, updated_at=now)
        self.policy = RetentionPolicy.objects.create(policy_key='synthetic-professional', version=1, data_class='clinical',
            rules={'synthetic_only': True}, schema_version='retention-1', status='active', approved_by=self.actor,
            approved_at=now, evidence_ref='synthetic-test-only')
        from tests.synthetic_fixtures import SyntheticEvidence
        self.evidence = SyntheticEvidence(self, self.actor)
        self.policy.rules = {'retention_days': None, 'disposition': 'retain'}
        self.policy.evidence_ref = self.evidence.reference
        self.policy.save(update_fields=['rules', 'evidence_ref'])
        source = SourceRegistry.objects.create(source_key='synthetic-professional', owner='SYNTHETIC ONLY', location='synthetic',
            purpose='clinical', sensitivity='clinical_restricted', update_frequency='never', status='approved')
        self.revision = SourceRevision.objects.create(source=source, version='synthetic-1', checksum='a' * 64,
            license='synthetic-test-only', allowed_use=['clinical'], rights_evidence_ref='synthetic-test-only',
            retention_policy=self.policy, captured_at=now, status='approved')
        self.authorization = DataUseAuthorization.objects.create(patient=self.patient, source_revision=self.revision,
            purpose='clinical', environment=settings.SJIA_ENVIRONMENT, evidence_ref='synthetic-test-only',
            approved_by=self.actor, approved_at=now)
        self.grant = AccessGrant.objects.create(patient=self.patient, principal=self.actor, action='write_professional',
            field_codes=['synthetic_note', 'synthetic_numeric'], record_scope='all', purpose='clinical',
            granted_by=self.actor, evidence_ref='synthetic-test-only', valid_from=now)
        self.target = f'/api/support/patients/{self.patient.id}/records'
        rid, vid, fid = map(str, (uuid4(), uuid4(), uuid4()))
        self.dataset = {'schema_version': '1.0.0', 'dataset_kind': 'clinical', 'dataset_version': 'synthetic-1',
            'environment': settings.SJIA_ENVIRONMENT, 'allowed_use': ['clinical'], 'sources': [{
                'id': str(self.revision.id), 'source_id': str(source.id), 'owner': source.owner, 'location': source.location,
                'purpose': source.purpose, 'license': self.revision.license, 'allowed_use': self.revision.allowed_use,
                'sensitivity': source.sensitivity, 'update_frequency': source.update_frequency, 'version': self.revision.version,
                'checksum': self.revision.checksum, 'retention_policy_id': str(self.policy.id),
                'rights_evidence_ref': self.revision.rights_evidence_ref, 'status': 'approved'}],
            'records': [{'record_id': rid, 'record_version_id': vid, 'patient_id': str(self.patient.id), 'version': 1,
                'kind': 'professional', 'author_id': str(self.actor.id), 'origin_type': 'authorized_clinical',
                'capture_method': 'authorized_import', 'source_revision_id': str(self.revision.id),
                'use_authorization_id': str(self.authorization.id), 'retention_policy_id': str(self.policy.id),
                'recorded_at': now.isoformat(), 'quality_status': 'pending', 'quality_reasons': [],
                'supersedes_version': None, 'correction_reason': None, 'report': None, 'fields': [self.field(fid, vid)]}]}

    def field(self, fid, vid):
        return {'field_id': fid, 'ordinal': 1, 'field_code': 'synthetic_note', 'concept_code': None,
            'dictionary_entry_id': None, 'original_name': 'synthetic note', 'value_type': 'text', 'value_state': 'known',
            'raw_text': 'SYNTHETIC TEST ONLY', 'raw_numeric': None, 'categorical_value': None, 'qualifier': None,
            'raw_unit': None, 'method': None, 'reference_interval': None, 'report_flag': None,
            'medical_time': None, 'sample_time_origin': 'unknown', 'quality_reasons': [],
            'source_reference': {'source_revision_id': str(self.revision.id), 'record_version_id': vid,
                'field_id': fid, 'file_id': None, 'page_id': None, 'anchor_id': None, 'verification_ids': [], 'mapping_id': None}}

    def prepare(self, payload=None, operation='professional_record_create', target=None):
        return prepare_professional_intent(actor=self.actor, auth_epoch=self.actor.auth_epoch, audience='support',
            data={'operation_id': operation, 'target_key': target or self.target, 'request_id': str(uuid4()),
                'schema_version': '1.0.0', 'payload': payload or self.dataset})

    def submit(self, intent, payload=None):
        return submit_professional_record(actor=self.actor, auth_epoch=self.actor.auth_epoch, audience='support',
            operation=intent.operation_id, target=intent.target_key, payload=payload or self.dataset,
            headers={'X-Write-Intent': str(intent.id), 'X-Intent-Generation': str(intent.generation),
                'Idempotency-Key': str(intent.request_id)})

    def test_single_record_creation_maps_ids_and_never_creates_verification(self):
        intent = self.prepare()
        first = self.submit(intent)
        replay = self.submit(intent)
        self.assertEqual(first, replay)
        self.assertEqual(Record.objects.count(), 1)
        version = RecordVersion.objects.get(id=first['resource_id'])
        self.assertEqual((version.origin_type, version.author_id, version.quality_status), ('authorized_clinical', self.actor.id, 'pending'))
        intent.refresh_from_db()
        self.assertEqual(intent.authorization_context['identity_map'][self.dataset['records'][0]['record_id']], str(version.record_id))
        self.assertEqual(Verification.objects.count(), 0)

    def test_global_config_admin_does_not_gain_patient_write(self):
        self.grant.delete()
        ServiceRoleGrant.objects.create(principal=self.actor, environment=self.gate, action='manage_config',
            granted_by=self.actor, evidence_ref='synthetic-test-only', valid_from=timezone.now())
        with self.assertRaises(QualificationRejected):
            self.prepare()
        self.assertFalse(Record.objects.exists())

    def test_source_and_actor_claims_cannot_create_authority(self):
        for path, value in (('author_id', str(uuid4())), ('patient_id', str(uuid4())), ('quality_status', 'valid')):
            payload = copy.deepcopy(self.dataset)
            payload['records'][0][path] = value
            with self.subTest(path=path), self.assertRaises(RecordRejected):
                self.prepare(payload)
        self.dataset['records'][0]['fields'][0]['source_reference']['verification_ids'] = [str(uuid4())]
        with self.assertRaisesRegex(RecordRejected, 'PROFESSIONAL_FIELD_PROVENANCE_INVALID'):
            self.prepare()
        self.assertFalse(Record.objects.exists())

    def test_revocation_between_prepare_and_submit_prevents_write(self):
        intent = self.prepare()
        self.authorization.revoked_at = timezone.now()
        self.authorization.save(update_fields=['revoked_at'])
        with self.assertRaisesRegex(RecordRejected, 'PROFESSIONAL_SOURCE_SCOPE_DENIED'):
            self.submit(intent)
        self.assertFalse(Record.objects.exists())

    def test_correction_requires_exact_version_and_preserves_source(self):
        first = self.submit(self.prepare())
        first['record_id'] = str(RecordVersion.objects.get(id=first['resource_id']).record_id)
        dataset = copy.deepcopy(self.dataset)
        record = dataset['records'][0]
        record.update(record_id=first['record_id'], record_version_id=str(uuid4()), version=2,
            supersedes_version=1, correction_reason='Synthetic correction')
        field = self.field(str(uuid4()), record['record_version_id'])
        field['raw_text'] = 'SYNTHETIC CORRECTED'
        record['fields'] = [field]
        payload = {'expected_version': '1', 'reason': 'Synthetic correction', 'dataset': dataset}
        target = f"/api/support/records/{first['record_id']}/corrections"
        intent = self.prepare(payload, 'professional_record_correct', target)
        receipt = self.submit(intent, payload)
        self.assertEqual(receipt['version'], '2')
        self.assertEqual(self.submit(intent, payload), receipt)
        self.assertEqual(Record.objects.get().current_version, 2)
        self.assertEqual(RecordVersion.objects.get(version=1).origin_type, 'authorized_clinical')
        self.assertEqual(RecordField.objects.get(record_version_id=receipt['resource_id']).raw_text, 'SYNTHETIC CORRECTED')
        with self.assertRaisesRegex(RecordRejected, 'PROFESSIONAL_VERSION_CONFLICT'):
            self.prepare(payload, 'professional_record_correct', target)

    def test_all_eighteen_domains_use_new_ids_and_authorized_original(self):
        from case_details import models
        from files.models import FileGroup, FileObject
        from files.storage import LocalFileStore, digest_bytes
        from tests.synthetic_case_projection import complete_projection
        from tests.synthetic_fixtures import synthetic_png
        initial = self.dataset['records'][0]
        base = {'fields': [{'id': initial['fields'][0]['field_id'], 'patient_id': str(self.patient.id),
            'record_version_id': initial['record_version_id'], 'original_name': 'synthetic_note', 'value_type': 'text',
            'raw_text': 'SYNTHETIC TEST ONLY', 'raw_numeric': None, 'qualifier': None, 'raw_unit': None,
            'report_flag': None, 'reference_interval': None}], 'case_details': {key: [] for key in (
                'profiles','episodes','groups','statements','imaging_studies','image_links','medication_events',
                'procedures','scores','followups','quality_issues')}}
        projection, ids = complete_projection(base)
        self.dataset.update(schema_version='1.1.0', medical_times=projection['medical_times'], case_details=projection['case_details'])
        initial['fields'] = []
        for index, source in enumerate(projection['fields'], 1):
            field = self.field(source['id'], initial['record_version_id'])
            field.update({key: source[key] for key in ('raw_text','raw_numeric','qualifier','raw_unit','report_flag','reference_interval','value_type')})
            field.update(field_code=source['original_name'], ordinal=index, medical_time=projection['medical_times'][0], sample_time_origin='observation')
            initial['fields'].append(field)
        directory = TemporaryDirectory(); self.addCleanup(directory.cleanup)
        store = LocalFileStore(Path(directory.name))
        patcher = patch('files.purpose.object_store', store); patcher.start(); self.addCleanup(patcher.stop)
        content = synthetic_png(); key = f'synthetic/{uuid4()}'; store.put_immutable(key, content)
        group = FileGroup.objects.create(patient=self.patient, owner=self.actor, version=1, status='available',
            source_revision=self.revision, use_authorization_id=self.authorization.id, created_at=timezone.now())
        original = FileObject.objects.create(patient=self.patient, file_group_id=group.id, object_key=key,
            object_version=digest_bytes(content), checksum=digest_bytes(content), size_bytes=len(content),
            media_type='image/png', purpose='original', status='available', retention_policy=self.policy, created_at=timezone.now())
        self.dataset['case_details']['image_links'][0]['file_id'] = str(original.id)
        AccessGrant.objects.create(patient=self.patient, principal=self.actor, action='read_file', field_codes=[],
            record_scope='all', purpose='clinical', granted_by=self.actor, evidence_ref='synthetic-test-only', valid_from=timezone.now())
        intent = self.prepare()
        result = self.submit(intent)
        intent.refresh_from_db()
        for name in ('PatientProfileVersion','ClinicalEpisode','RecordFieldGroup','RecordFieldGroupMember',
                'ClinicalStatement','StatementEvidence','ImagingStudy','ImagingStudySite','ImagingAssetLink',
                'MedicationEvent','MedicationEventItem','ProcedureEvent','ExternalScoreObservation',
                'ExternalScoreComponent','FollowupEncounter','FollowupReference','DataQualityIssue','DataQualityIssueReference'):
            self.assertEqual(getattr(models, name).objects.filter(patient_id=self.patient.id).count(), 1, name)
        self.assertNotEqual(intent.authorization_context['identity_map'][ids['profile']], ids['profile'])
        self.assertEqual(models.ImagingAssetLink.objects.get().file_id, original.id)
        self.assertEqual(models.StatementEvidence.objects.get().evidence_record_version_id, RecordVersion.objects.get().id)
        self.assertEqual(Verification.objects.count(), 0)


class ProfessionalResolvedCorrectionTests(TestCase):
    setUp = ProfessionalRecordTests.setUp
    field = ProfessionalRecordTests.field
    prepare = ProfessionalRecordTests.prepare
    submit = ProfessionalRecordTests.submit

    """Explicit exchange instants are checked and re-derived, never trusted."""
    def test_resolved_time_correction_is_normalized_for_shared_writer(self):
        first = self.submit(self.prepare())
        record_id = str(RecordVersion.objects.get(id=first['resource_id']).record_id)
        dataset = copy.deepcopy(self.dataset)
        record = dataset['records'][0]
        record.update(record_id=record_id, record_version_id=str(uuid4()), version=2,
            supersedes_version=1, correction_reason='Synthetic timestamp correction')
        field = self.field(str(uuid4()), record['record_version_id'])
        field['medical_time'] = {'raw_text': 'SYNTHETIC 2026-01-01 08:00 +08:00', 'precision': 'minute',
            'local_date': None, 'local_datetime': '2026-01-01T08:00', 'timezone_name': 'Asia/Shanghai',
            'utc_offset_minutes': 480, 'instant': '2026-01-01T00:00:00Z', 'resolution_status': 'resolved'}
        field['sample_time_origin'] = 'observation'
        record['fields'] = [field]
        payload = {'expected_version': '1', 'reason': record['correction_reason'], 'dataset': dataset}
        intent = self.prepare(payload, 'professional_record_correct', f'/api/support/records/{record_id}/corrections')
        result = self.submit(intent, payload)
        from medical_time.models import MedicalTime
        saved = RecordField.objects.get(record_version_id=result['resource_id'])
        medical = MedicalTime.objects.get(id=saved.medical_time_id)
        self.assertEqual(medical.instant.isoformat(), '2026-01-01T00:00:00+00:00')
        self.assertEqual(medical.precision, 'minute')
        self.assertEqual(medical.raw_text, field['medical_time']['raw_text'])


class ProfessionalConcurrencyTests(TransactionTestCase):
    def setUp(self):
        from tests.isolated_rows import IsolatedSchemaRows
        rows = IsolatedSchemaRows()
        self.addCleanup(rows.cleanup)
        ProfessionalRecordTests.setUp(self)
    field = ProfessionalRecordTests.field
    prepare = ProfessionalRecordTests.prepare
    submit = ProfessionalRecordTests.submit

    def test_same_correction_intent_waiter_receives_original_receipt(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from django.db import connection, close_old_connections
        from records import professional
        first = self.submit(self.prepare())
        record_id = str(RecordVersion.objects.get(id=first['resource_id']).record_id)
        dataset = copy.deepcopy(self.dataset)
        record = dataset['records'][0]
        record.update(record_id=record_id, record_version_id=str(uuid4()), version=2,
            supersedes_version=1, correction_reason='Synthetic concurrent correction')
        record['fields'] = [self.field(str(uuid4()), record['record_version_id'])]
        payload = {'expected_version': '1', 'reason': record['correction_reason'], 'dataset': dataset}
        intent = self.prepare(payload, 'professional_record_correct', f'/api/support/records/{record_id}/corrections')
        writing, competing = Event(), Event()
        apply = professional._apply
        def held_apply(*args):
            writing.set()
            if not competing.wait(10):
                raise AssertionError('Synthetic second connection did not start')
            return apply(*args)
        def submit(first_writer):
            close_old_connections()
            try:
                if not first_writer and not writing.wait(10):
                    raise AssertionError('Synthetic first writer did not start')
                def observe_gate(execute, sql, params, many, context):
                    # Signal only when connection two is about to contend on the
                    # same gate. Before the fix it has already read stale status.
                    statement = str(sql).lower()
                    if not first_writer and 'environment_gate' in statement and 'for update' in statement:
                        competing.set()
                    return execute(sql, params, many, context)
                with connection.execute_wrapper(observe_gate):
                    return self.submit(intent, payload)
            finally:
                connection.close()
        with patch('records.professional._apply', side_effect=held_apply), ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(submit, True)
            waiter = pool.submit(submit, False)
            first_receipt, second_receipt = writer.result(timeout=20), waiter.result(timeout=20)
        self.assertEqual(first_receipt, second_receipt)
        self.assertEqual(Record.objects.get(id=record_id).current_version, 2)
        self.assertEqual(RecordVersion.objects.filter(record_id=record_id).count(), 2)
