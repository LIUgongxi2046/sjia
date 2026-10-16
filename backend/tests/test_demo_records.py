"""Synthetic-only experience dataset contracts and real service integration.

Run with an isolated PostgreSQL test database. No browser, provider calls,
credential creation, professional verification or notification delivery occurs.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.sessions.models import Session
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from archives.models import ArchiveFact, ArchiveFactVersion
from authorization.models import AccessGrant, ServiceRoleGrant
from authorization.policy import Denied
from case_details.models import MedicationEvent, MedicationEventItem
from configurations.models import ConfigVersion
from configurations.schemas import validate_payload
from core.demo_records import (DEMO_VERSION, PATIENT_KEYS, DemoRecordsRejected,
    demo_field_config_payloads, demo_identity, demo_record_specs, demo_report_plan, seed_demo_records)
from drafts.models import Draft
from drafts.service import abandon_draft, update_draft
from files.models import FileGroup, FileObject, FilePage
from files.storage import LocalFileStore, digest_bytes
from identity.models import Patient, Principal, PrincipalCredential, PrincipalIdentity
from records.correction import correct_record
from records.input_validation import validate_family_record_input
from records.models import NormalizedMeasurement, Record, RecordAttachment, RecordField, RecordVersion, Report
from records.service import create_record, read_record
from relations.models import Relation, RelationApplication
from requests.models import WriteIntent, WriteRequest
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision
from tests.synthetic_fixtures import SyntheticEvidence, synthetic_png


AS_OF = datetime(2026, 10, 12, 7, 0, tzinfo=datetime_timezone.utc)


class DemoRecordPlanTests(SimpleTestCase):
    def test_exact_editable_definitions_and_service_wire_contracts(self):
        configs = demo_field_config_payloads('synthetic-test-reference')
        self.assertEqual(set(configs), {'observation', 'report', 'therapy'})
        for config in configs.values():
            validate_payload('content', 'field-config-1', config)
            self.assertTrue(all(field['writable'] for field in config['fields']))
        specs = demo_record_specs(AS_OF)
        self.assertEqual(Counter(row['kind'] for row in specs),
            {'observation': 14, 'report': 5, 'therapy': 3})
        self.assertEqual(len({row['key'] for row in specs}), 22)
        for spec in specs:
            value = {'kind': spec['kind'], 'capture_method': 'manual',
                'field_config_version': str(uuid4()), 'fields': spec['fields']}
            if spec['kind'] == 'report':
                value['report'] = spec['report_metadata']
            validate_family_record_input(value)
            definitions = {field['field_code']: field for field in configs[spec['kind']]['fields']}
            for field in spec['fields']:
                self.assertEqual(field['value_type'], definitions[field['field_code']]['value_type'])
                self.assertNotIn('confirmation_id', field)
                self.assertNotIn('anchor_id', field)
                self.assertNotIn('verification_status', field)

    def test_all_raw_states_and_temporal_precision_are_explicit(self):
        specs = demo_record_specs(AS_OF)
        fields = [field for row in specs for field in row['fields']]
        self.assertEqual({field['value_state'] for field in fields}, {'known', 'unknown', 'invalid'})
        invalid = [field for field in fields if field['value_state'] == 'invalid']
        self.assertEqual(len(invalid), 1)
        self.assertIsNone(invalid[0]['raw_numeric'])
        self.assertEqual(invalid[0]['raw_text'], '读数模糊，原值无法辨认')
        for key in PATIENT_KEYS:
            observed = [row for row in specs if row['patient_key'] == key and row['kind'] == 'observation']
            self.assertEqual(len({row['fields'][0]['medical_time']['local_date'] for row in observed}), 7)
        self.assertTrue(any(field.get('report_flag') == '↑' for field in fields))
        for field in fields:
            if field.get('medical_time'):
                self.assertEqual(field['medical_time']['precision'], 'date')
                self.assertNotIn('instant', field['medical_time'])

    def test_report_plan_is_the_same_content_that_the_writer_uses(self):
        expected = [row for row in demo_record_specs(AS_OF) if row['kind'] == 'report']
        self.assertEqual(demo_report_plan(AS_OF), expected)
        self.assertEqual([row['key'] for row in expected],
            ['child_a/report-1', 'child_a/report-2', 'child_a/report-3', 'child_b/report-1', 'child_b/report-2'])
        self.assertTrue(all('示例' in row['report_metadata']['external_ref'] for row in expected))
        self.assertTrue(all('source_file_id' not in field for row in expected for field in row['fields']))

    def test_keys_remain_stable_but_returned_values_are_independent(self):
        first, later = demo_record_specs(AS_OF), demo_record_specs(AS_OF + timedelta(days=5))
        self.assertEqual([row['key'] for row in first], [row['key'] for row in later])
        self.assertNotEqual(first[0]['fields'][0]['medical_time'], later[0]['fields'][0]['medical_time'])
        first[0]['fields'][0]['raw_numeric'] = '123'
        self.assertEqual(demo_record_specs(AS_OF)[0]['fields'][0]['raw_numeric'], '37.0')
        self.assertEqual(demo_identity('patient:child_a').version, 5)
        self.assertNotEqual(demo_identity('patient:child_a'), demo_identity('patient:child_b'))

    def test_naive_or_missing_reference_is_rejected(self):
        with self.assertRaisesRegex(DemoRecordsRejected, 'AWARE'):
            demo_record_specs(datetime(2026, 10, 12))
        with self.assertRaisesRegex(DemoRecordsRejected, 'REFERENCE_REQUIRED'):
            demo_field_config_payloads('')


@override_settings(DEBUG=True, SJIA_ENVIRONMENT='test')
class DemoRecordServiceTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.fixture = SyntheticEvidence(self, self.actor)
        source = SourceRegistry.objects.create(id=demo_identity('source'),
            source_key='synthetic-experience-record-tests', owner='synthetic-software-test',
            location=self.fixture.source.source.location, purpose='family_observation',
            sensitivity='synthetic', update_frequency='never', status='approved')
        self.revision = SourceRevision.objects.create(id=demo_identity('source-revision'), source=source,
            version=DEMO_VERSION, checksum=self.fixture.source.checksum, license='synthetic-test-only',
            allowed_use=['family_observation'], rights_evidence_ref=self.fixture.reference,
            retention_policy=self.fixture.retention, captured_at=now, status='approved')
        self.patients, self.uses = {}, {}
        for key, name in (('child_a', '安安（示例）'), ('child_b', '乐乐（示例）')):
            patient = Patient.objects.create(id=demo_identity(f'patient:{key}'), display_id=name,
                status='active', created_at=now)
            self.patients[key] = patient
            self.uses[key] = DataUseAuthorization.objects.create(patient=patient, source_revision=self.revision,
                purpose='family_observation', environment='test', approved_by=self.actor,
                approved_at=now, evidence_ref=self.fixture.reference)
        self.policies = {kind: RetentionPolicy.objects.create(policy_key=f'synthetic-demo-{kind}', version=1,
            data_class=kind, rules={'retention_days': None, 'disposition': 'retain'}, schema_version='retention-1',
            status='active', approved_by=self.actor, approved_at=now, evidence_ref=self.fixture.reference)
            for kind in ('observation', 'report', 'therapy', 'draft', 'request')}
        self.configs = {kind: self.fixture.configuration('content', payload)
            for kind, payload in demo_field_config_payloads(self.fixture.reference).items()}
        codes = sorted({field['field_code'] for config in self.configs.values() for field in config.payload['fields']})
        for patient in self.patients.values():
            application = RelationApplication.objects.create(version=1, applicant=self.actor,
                patient=patient, evidence_ref=self.fixture.reference, status='approved',
                checked_by=self.actor, checked_at=now, created_at=now)
            relation = Relation.objects.create(patient=patient, guardian=self.actor,
                application=application, status='active', version=1, checked_by=self.actor,
                checked_at=now, valid_from=now)
            for action in ('write_record', 'correct_record', 'manage_draft'):
                AccessGrant.objects.create(patient=patient, principal=self.actor, relation_id=relation.id,
                    action=action, field_codes=codes, record_scope='own', purpose='family_observation',
                    granted_by=self.actor, evidence_ref=self.fixture.reference, valid_from=now)

    def seed(self, **overrides):
        return seed_demo_records(**{'actor': self.actor, 'patients': self.patients,
            'retention_policies': self.policies, 'source_revision': self.revision,
            'use_authorizations': self.uses, 'field_configs': self.configs,
            'as_of': AS_OF, **overrides})

    def counts(self):
        return {model._meta.label: model.objects.count() for model in (
            Record, RecordVersion, RecordField, Report, ArchiveFact, ArchiveFactVersion,
            MedicationEvent, MedicationEventItem, Draft, WriteIntent, WriteRequest)}

    def test_real_services_create_raw_facts_receipts_and_one_correction(self):
        from alerting.models import AlertEvaluation
        from assessment.models import Assessment
        from notifications.models import NotificationAttempt, NotificationReceipt
        from recognition.models import CandidateConfirmation
        from verification.models import Verification, FieldVerificationState
        result = self.seed()
        self.assertEqual((result['created_records'], result['created_corrections'], result['created_drafts']), (22, 1, 2))
        self.assertEqual(Record.objects.count(), 22)
        self.assertEqual(RecordVersion.objects.count(), 23)
        self.assertEqual(WriteIntent.objects.filter(status='committed').count(), 23)
        self.assertEqual(WriteRequest.objects.filter(status='committed').count(), 23)
        # Ordinary writers do not invent a clinical quality explanation for an
        # unknown/invalid value. The real format validator retains that gap.
        self.assertEqual(RecordVersion.objects.filter(quality_status='needs_correction').count(), 8)
        for version in RecordVersion.objects.all():
            incomplete = list(RecordField.objects.filter(record_version_id=version.id,
                value_state__in=('unknown', 'invalid')).order_by('ordinal'))
            self.assertEqual(version.quality_status, 'needs_correction' if incomplete else 'valid')
            self.assertEqual(version.quality_reasons,
                [f'FIELD_FORMAT_INVALID:{field.id}:$.quality_reasons:minItems' for field in incomplete])
        self.assertEqual(RecordVersion.objects.exclude(capture_method='manual').count(), 0)
        self.assertEqual(ArchiveFactVersion.objects.count(), RecordField.objects.count())
        self.assertEqual(MedicationEvent.objects.count(), 3)
        self.assertEqual(MedicationEventItem.objects.count(), 3)
        record = Record.objects.get(id=result['correction']['record_id'])
        self.assertEqual(record.current_version, 2)
        temperatures = list(RecordField.objects.filter(record_version_id__in=RecordVersion.objects.filter(
            record_id=record.id).values('id'), field_code='temp').order_by('raw_numeric').values_list('raw_numeric', flat=True))
        self.assertEqual(temperatures, [Decimal('38.1'), Decimal('38.2')])
        for model in (AlertEvaluation, Assessment, NotificationAttempt, NotificationReceipt,
                CandidateConfirmation, Verification, FieldVerificationState, NormalizedMeasurement):
            self.assertEqual(model.objects.count(), 0, model._meta.label)
        for key, draft_ref in result['drafts'].items():
            draft = Draft.objects.get(id=draft_ref['draft_id'])
            self.assertEqual(draft.patient_id, self.patients[key].id)
            self.assertEqual(draft.payload['tempUnit'], '摄氏度（°C）')
            self.assertEqual(draft.payload['measuredTime'], {'precision': 'unknown', 'value': '', 'timezone': ''})
            self.assertIs(draft.payload['voiceApplied'], False)

    def test_rerun_preserves_ids_dates_counts_and_user_draft_edits(self):
        first = self.seed()
        draft = Draft.objects.get(id=first['drafts']['child_a']['draft_id'])
        changed_payload = {**draft.payload, 'temp': '36.6', 'description': '用户继续填写的示例草稿'}
        update_draft(draft=draft, expected_version=1, payload=changed_payload)
        second_draft = Draft.objects.get(id=first['drafts']['child_b']['draft_id'])
        abandon_draft(draft=second_draft, expected_version=1)
        before = self.counts()
        dates = list(Record.objects.order_by('id').values_list('id', 'created_at'))
        again = self.seed(as_of=AS_OF + timedelta(days=30))
        self.assertEqual(before, self.counts())
        self.assertEqual(dates, list(Record.objects.order_by('id').values_list('id', 'created_at')))
        self.assertEqual(first['records'], again['records'])
        self.assertEqual(first['correction'], again['correction'])
        self.assertEqual((again['created_records'], again['created_corrections'], again['created_drafts']), (0, 0, 0))
        draft.refresh_from_db()
        self.assertEqual(draft.payload, changed_payload)
        self.assertEqual(draft.version, 2)
        self.assertEqual(again['drafts']['child_b']['status'], 'abandoned')

    def test_later_user_correction_and_unrelated_record_are_preserved(self):
        first = self.seed()
        record = Record.objects.get(id=first['correction']['record_id'])
        fields = deepcopy(next(row['fields'] for row in demo_record_specs(AS_OF)
            if row['key'] == 'child_a/observation-3'))
        fields[0]['raw_numeric'] = '37.9'
        correct_record(record=record, expected_version=2, actor=self.actor, reason='体验时继续更正',
            fields=fields, use_authorization_id=self.uses['child_a'].id,
            retention_policy=self.policies['observation'], source_revision=self.revision)
        other = create_record(patient=self.patients['child_a'], actor=self.actor, kind='observation',
            capture_method='manual', origin_type='self_report', retention_policy=self.policies['observation'],
            use_authorization_id=self.uses['child_a'].id, source_revision=self.revision,
            fields=[{'field_code': 'observation_note', 'value_type': 'text', 'value_state': 'known',
                'raw_text': '用户新建的示例记录', 'sample_time_origin': 'observation'}])
        before = self.counts()
        self.seed()
        self.assertEqual(before, self.counts())
        record.refresh_from_db()
        self.assertEqual(record.current_version, 3)
        self.assertEqual(read_record(record.id, self.patients['child_a'])['fields'][0]['raw_numeric'], '37.9')
        self.assertTrue(Record.objects.filter(id=other['record_id']).exists())

    def test_identity_credentials_sessions_and_permissions_are_not_changed(self):
        models = (Principal, PrincipalIdentity, PrincipalCredential, Session, Patient,
            SourceRegistry, SourceRevision, DataUseAuthorization, AccessGrant, ServiceRoleGrant, ConfigVersion)
        before = {model._meta.label: list(model.objects.order_by('pk').values()) for model in models}
        self.seed()
        for model in models:
            after = list(model.objects.order_by('pk').values())
            expected = before[model._meta.label]
            if model is Patient:
                for row in after:
                    row['data_epoch'] = next(item['data_epoch'] for item in expected if item['id'] == row['id'])
            self.assertEqual(after, expected, model._meta.label)

    def test_foreign_patient_or_source_is_rejected_before_writes(self):
        now = timezone.now()
        foreign = Patient.objects.create(display_id='外部已有资料', status='active', created_at=now)
        for override, error in (({'patients': {**self.patients, 'child_a': foreign}}, 'EXTERNAL_PATIENT'),
                ({'source_revision': self.fixture.source}, 'EXTERNAL_SOURCE')):
            with self.subTest(error=error), self.assertRaisesRegex(DemoRecordsRejected, error):
                self.seed(**override)
        self.assertEqual(Record.objects.count(), 0)
        self.assertEqual(WriteIntent.objects.count(), 0)

    def test_revoked_authorization_or_nonsynthetic_source_is_rejected(self):
        DataUseAuthorization.objects.filter(id=self.uses['child_a'].id).update(revoked_at=timezone.now())
        with self.assertRaisesRegex(DemoRecordsRejected, 'AUTHORIZATION_UNAVAILABLE'):
            self.seed()
        DataUseAuthorization.objects.filter(id=self.uses['child_a'].id).update(revoked_at=None)
        SourceRegistry.objects.filter(id=self.revision.source_id).update(sensitivity='patient_data')
        with self.assertRaisesRegex(DemoRecordsRejected, 'SOURCE_UNAVAILABLE'):
            self.seed()
        self.assertEqual(Record.objects.count(), 0)

    def test_missing_family_write_or_correction_permission_rolls_back(self):
        for action in ('write_record', 'correct_record', 'manage_draft'):
            query = AccessGrant.objects.filter(principal=self.actor, action=action)
            query.update(revoked_at=timezone.now())
            with self.subTest(action=action), self.assertRaisesRegex(Denied, 'CASE_ACTION_DENIED'):
                self.seed()
            self.assertEqual(Record.objects.count(), 0)
            self.assertEqual(WriteRequest.objects.count(), 0)
            query.update(revoked_at=None)

    def test_rollback_keeps_failed_run_from_claiming_success_or_partial_records(self):
        original = create_record
        calls = 0
        def fail_second(**values):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('synthetic injected interruption')
            return original(**values)
        with patch('core.demo_records.create_record', side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, 'injected interruption'):
                self.seed()
        self.assertEqual(Record.objects.count(), 0)
        self.assertEqual(RecordField.objects.count(), 0)
        self.assertEqual(WriteRequest.objects.count(), 0)
        self.assertEqual(WriteIntent.objects.count(), 0)
        for patient in self.patients.values():
            patient.refresh_from_db()
            self.assertEqual(patient.data_epoch, 0)

    def test_committed_request_cannot_be_adopted_by_another_actor(self):
        self.seed()
        now = timezone.now()
        foreign = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        before = self.counts()
        with self.assertRaisesRegex(DemoRecordsRejected, 'FOREIGN_OR_INCOMPLETE_INTENT'):
            self.seed(actor=foreign)
        self.assertEqual(before, self.counts())

    def test_attachment_uses_existing_actual_bytes_and_same_patient_source(self):
        patient, use = self.patients['child_a'], self.uses['child_a']
        group = FileGroup.objects.create(patient=patient, owner=self.actor, source_revision=self.revision,
            use_authorization_id=use.id, version=1, status='available', created_at=timezone.now())
        content = synthetic_png()
        object_key = f'synthetic-demo/{uuid4()}.png'
        store = LocalFileStore(self.fixture.root / 'objects')
        store.put_immutable(object_key, content)
        file = FileObject.objects.create(patient=patient, file_group_id=group.id, object_key=object_key,
            object_version='1', checksum=digest_bytes(content), size_bytes=len(content), media_type='image/png',
            purpose='original', status='available', retention_policy=self.policies['report'], created_at=timezone.now())
        FilePage.objects.create(patient=patient, file_group_id=group.id, file_id=file.id, ordinal=1,
            width=12, height=18, rotation=0)
        attachments = {'child_a/report-1': {'file_ids': [str(file.id)], 'source_file_id': str(file.id),
            'source_text': '测试图像中的示例原件说明'}}
        result = self.seed(report_attachments=attachments)
        version_id = result['records']['child_a/report-1']['record_version_id']
        self.assertEqual(RecordAttachment.objects.get(record_version_id=version_id).file_id, file.id)
        self.assertEqual(RecordField.objects.filter(record_version_id=version_id, anchor_id__isnull=False).count(), 3)
        self.assertEqual(store.get(object_key), content)

    def test_missing_and_foreign_attachments_do_not_leave_partial_dataset(self):
        with self.assertRaisesRegex(DemoRecordsRejected, 'FOREIGN_ATTACHMENT'):
            self.seed(report_attachments={'child_a/report-1': {'file_ids': [str(uuid4())]}})
        self.assertEqual(Record.objects.count(), 0)
        self.assertEqual(WriteRequest.objects.count(), 0)
        with self.assertRaisesRegex(DemoRecordsRejected, 'ATTACHMENT_KEY'):
            self.seed(report_attachments={'unrecognized-report': {'file_ids': [str(uuid4())]}})

    def test_existing_draft_collision_is_rejected_without_overwriting(self):
        result = self.seed()
        draft = Draft.objects.get(id=result['drafts']['child_a']['draft_id'])
        now = timezone.now()
        foreign = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        Draft.objects.filter(id=draft.id).update(owner=foreign)
        before = self.counts()
        with self.assertRaisesRegex(DemoRecordsRejected, 'FOREIGN_DRAFT'):
            self.seed()
        self.assertEqual(before, self.counts())
        draft.refresh_from_db()
        self.assertEqual(draft.owner_id, foreign.id)

    @override_settings(DEBUG=False)
    def test_production_is_refused(self):
        with self.assertRaisesRegex(DemoRecordsRejected, 'NONPRODUCTION_ONLY'):
            self.seed()
        self.assertEqual(Record.objects.count(), 0)

    def test_non_test_database_and_simulation_are_refused(self):
        with patch.dict(connection.settings_dict, {'NAME': 'sjia_dev'}):
            with self.assertRaisesRegex(DemoRecordsRejected, 'NONPRODUCTION_ONLY'):
                self.seed()
        with override_settings(SJIA_ENVIRONMENT='simulation'):
            with self.assertRaisesRegex(DemoRecordsRejected, 'NONPRODUCTION_ONLY'):
                self.seed()
        self.assertEqual(Record.objects.count(), 0)
