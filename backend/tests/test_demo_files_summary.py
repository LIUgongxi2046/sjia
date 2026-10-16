"""Isolated synthetic fixtures; no account/password/session or provider setup."""
import io
import json
from datetime import date
from unittest.mock import patch
from uuid import UUID

from django.conf import settings
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from authorization.models import AccessGrant
from authorization.policy import Denied
from core.demo_files_summary import (
    ensure_demo_report_attachment, ensure_demo_summary, render_demo_report_png,
)
from files.errors import FileRejected
from files.models import FileGroup, FileObject, FilePage, UploadPart
from files.service import read_object_bytes
from files.storage import LocalFileStore, digest_bytes
from identity.models import Patient, Principal, PrincipalCredential, PrincipalIdentity
from records.models import RecordVersion
from records.service import create_record
from requests.models import WriteIntent, WriteRequest
from requests.service import IdempotencyConflict
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from summaries.models import Summary, SummaryItem
from summaries.service import SummaryRejected, read_summary
from tasks.models import ConsumerReceipt, OutboxEvent, Task, TaskAttempt
from tests.integration.helpers import activate_summary_configs
from tests.synthetic_fixtures import SyntheticEvidence


NAMESPACE = UUID('91752ecc-4d74-5c77-a976-f0108cf0fce4')
REPORT = {'report_key': 'example-labs-01', 'report_date': date(2026, 10, 10),
    'rows': [('Ferritin', '420', 'ng/mL'), ('Platelets', '210', '10^9/L')]}


class DemoReportImageTests(SimpleTestCase):
    def test_png_is_real_deterministic_and_explicitly_synthetic(self):
        content = render_demo_report_png(**REPORT)
        self.assertEqual(content, render_demo_report_png(**REPORT))
        with Image.open(io.BytesIO(content)) as image:
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(image.size, (1120, 568))
            self.assertIn('Not a medical report', image.info['Description'])
            payload = json.loads(image.info['demo-content'])
            self.assertTrue(payload['synthetic'])
            self.assertFalse(payload['clinical_document'])
            self.assertEqual(payload['rows'], [list(row) for row in REPORT['rows']])
            image.verify()

    def test_render_rejects_unbounded_and_unrenderable_input(self):
        for changes in ({'rows': []}, {'rows': [('体温', '37', 'C')]},
                {'rows': [('Ferritin', '4\n20', 'ng/mL')]},
                {'report_key': '../not-a-report'}, {'rows': [('x' * 33, '1', '')]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                render_demo_report_png(**{**REPORT, **changes})

    @override_settings(SJIA_ENVIRONMENT='production')
    def test_helpers_fail_before_accessing_production(self):
        with self.assertRaisesMessage(ValueError, 'DEMO_TEST_ENVIRONMENT_REQUIRED'):
            ensure_demo_report_attachment(actor=None, patient=None, source_revision=None,
                use_authorization=None, retention_policy=None, request_retention_policy=None,
                namespace=NAMESPACE, **REPORT)
        with self.assertRaisesMessage(ValueError, 'DEMO_TEST_ENVIRONMENT_REQUIRED'):
            ensure_demo_summary(actor=None, patient=None, retention_policy=None, namespace=NAMESPACE,
                from_date=date(2026, 10, 1), to_date=date(2026, 10, 12))

    @override_settings(SJIA_ENVIRONMENT='test', DEBUG=False)
    def test_helpers_require_explicit_debug_environment(self):
        with self.assertRaisesMessage(ValueError, 'DEMO_TEST_ENVIRONMENT_REQUIRED'):
            ensure_demo_summary(actor=None, patient=None, retention_policy=None, namespace=NAMESPACE,
                from_date=date(2026, 10, 1), to_date=date(2026, 10, 12))

    @override_settings(SJIA_ENVIRONMENT='test', DEBUG=True)
    def test_helpers_reject_non_test_database_without_connecting(self):
        with patch.dict(connection.settings_dict, NAME='sjia_real'), self.assertRaisesMessage(
                ValueError, 'DEMO_TEST_DATABASE_REQUIRED'):
            ensure_demo_summary(actor=None, patient=None, retention_policy=None, namespace=NAMESPACE,
                from_date=date(2026, 10, 1), to_date=date(2026, 10, 12))


@override_settings(DEBUG=True, SJIA_ENVIRONMENT='test')
class DemoFilesSummaryTests(TestCase):
    def setUp(self):
        if not str(connection.settings_dict['NAME']).startswith('test_'):
            raise RuntimeError('Demo file tests refuse non-test databases')
        now = timezone.now()
        self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id='SYNTHETIC-DEMO-HELPER', status='active', created_at=now)
        self.synthetic = SyntheticEvidence(self, self.actor)
        self.runtime = self.synthetic.runtime()
        self.file_policy = RetentionPolicy.objects.get(id__in=self.runtime.payload['retention_policy_ids'], data_class='file')
        self.authorization = DataUseAuthorization.objects.create(patient=self.patient,
            source_revision=self.synthetic.source, purpose='family_observation', environment=settings.SJIA_ENVIRONMENT,
            evidence_ref=self.synthetic.reference, approved_by=self.actor, approved_at=now)
        for action, fields in (('write_record', []), ('read_file', []),
                ('create_summary', ['temp']), ('read_summary', ['temp'])):
            AccessGrant.objects.create(patient=self.patient, principal=self.actor, action=action,
                field_codes=fields, record_scope='all', purpose='family_observation', granted_by=self.actor,
                evidence_ref=self.synthetic.reference, valid_from=now)
        self.store = LocalFileStore(self.synthetic.root / 'demo-originals')
        for name in ('files.service.object_store', 'files.purpose.object_store'):
            replacement = patch(name, self.store)
            replacement.start()
            self.addCleanup(replacement.stop)

    def attachment(self, **changes):
        return ensure_demo_report_attachment(actor=self.actor, patient=self.patient,
            source_revision=self.synthetic.source, use_authorization=self.authorization,
            retention_policy=self.file_policy, request_retention_policy=self.synthetic.request_retention,
            namespace=NAMESPACE, **{**REPORT, **changes})

    def record(self, value='36.9'):
        return create_record(patient=self.patient, actor=self.actor, kind='observation', capture_method='manual',
            origin_type='self_report', retention_policy=self.synthetic.retention,
            use_authorization_id=self.authorization.id, source_revision=self.synthetic.source,
            source_organization='Synthetic demonstration', fields=[{'field_code': 'temp', 'value_type': 'decimal',
                'value_state': 'known', 'raw_numeric': value, 'raw_unit': 'C', 'sample_time_origin': 'observation',
                'medical_time': {'precision': 'date', 'local_date': '2026-10-10', 'resolution_status': 'local_only'}}])

    def summary(self, **changes):
        return ensure_demo_summary(actor=self.actor, patient=self.patient,
            retention_policy=self.synthetic.request_retention, namespace=NAMESPACE,
            **{'from_date': date(2026, 10, 1), 'to_date': date(2026, 10, 12), **changes})

    def test_attachment_is_openable_immutable_and_idempotent_with_real_validation(self):
        group = self.attachment()
        original = FileObject.objects.get(file_group_id=group.id, purpose='original')
        content = read_object_bytes(original.id)
        self.assertEqual(digest_bytes(content), original.checksum)
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
        again = self.attachment()
        self.assertEqual(group.id, again.id)
        self.assertEqual(again.status, 'available')
        self.assertEqual(FileGroup.objects.count(), 1)
        self.assertEqual(FileObject.objects.count(), 1)
        self.assertEqual(FilePage.objects.count(), 1)
        self.assertEqual(UploadPart.objects.count(), 1)
        task = Task.objects.get(task_type='file_validation')
        self.assertEqual(task.status, 'completed')
        self.assertEqual(TaskAttempt.objects.filter(task=task, status='completed').count(), 1)
        self.assertEqual(ConsumerReceipt.objects.filter(consumer='file_validation').count(), 1)
        self.assertEqual(OutboxEvent.objects.filter(task=task).count(), 1)
        self.assertEqual(WriteIntent.objects.filter(operation_id='file_group_complete').count(), 1)
        self.assertEqual(WriteRequest.objects.filter(action='file_group_complete').count(), 1)
        self.assertFalse(Task.objects.filter(task_type='ocr').exists())
        self.assertFalse(PrincipalCredential.objects.exists())
        self.assertFalse(PrincipalIdentity.objects.exists())

    def test_changed_report_does_not_overwrite_original_bytes(self):
        group = self.attachment()
        original = FileObject.objects.get(file_group_id=group.id)
        before = read_object_bytes(original.id)
        with self.assertRaisesMessage(FileRejected, 'DEMO_ATTACHMENT_INPUT_CHANGED'):
            self.attachment(rows=[('Ferritin', '999', 'ng/mL')])
        self.assertEqual(read_object_bytes(original.id), before)
        self.assertEqual(FileGroup.objects.count(), 1)

    def test_non_synthetic_source_and_revoked_grant_leave_no_upload(self):
        source = self.synthetic.source.source
        source.sensitivity = 'personal'
        source.save(update_fields=['sensitivity'])
        with self.assertRaisesMessage(FileRejected, 'DEMO_SYNTHETIC_SOURCE_REQUIRED'):
            self.attachment()
        self.assertFalse(FileGroup.objects.exists())
        source.sensitivity = 'synthetic'
        source.save(update_fields=['sensitivity'])
        AccessGrant.objects.filter(action='write_record').update(revoked_at=timezone.now())
        with self.assertRaises(Denied):
            self.attachment()
        self.assertFalse(FileObject.objects.exists())

    def test_real_validator_failure_never_becomes_a_completed_attachment(self):
        from files.validation import execute_validation
        def expired(event, owner):
            Task.objects.filter(id=event.task_id).update(deadline_at=timezone.now())
            return execute_validation(event, owner)
        with patch('core.demo_files_summary.execute_validation', side_effect=expired):
            with self.assertRaisesMessage(FileRejected, 'FILE_VALIDATION_DEADLINE_EXCEEDED'):
                self.attachment()
        self.assertEqual(Task.objects.get(task_type='file_validation').status, 'failed')
        self.assertEqual(FileGroup.objects.get().status, 'failed')
        self.assertFalse(TaskAttempt.objects.filter(status='completed').exists())

    def test_summary_uses_real_record_snapshot_and_one_real_worker_attempt(self):
        from alerting.models import AlertEvaluation, AlertEvent
        from assessment.models import Assessment
        from notifications.models import NotificationAttempt
        from recognition.models import CandidateSet
        from verification.models import Verification
        activate_summary_configs(self)
        record = self.record()
        untouched = (Assessment, AlertEvaluation, AlertEvent, NotificationAttempt, CandidateSet, Verification)
        baseline = [model.objects.count() for model in untouched]
        summary = self.summary()
        repeated = self.summary()
        self.assertEqual(summary.id, repeated.id)
        self.patient.refresh_from_db()
        view = read_summary(summary.id, self.patient)
        self.assertFalse(view['needs_regeneration'])
        self.assertEqual(view['items'][0]['snapshot']['field']['raw_numeric'], '36.9')
        version = RecordVersion.objects.get(record_id=record['record_id'])
        self.assertEqual(view['items'][0]['record_version_id'], str(version.id))
        self.assertEqual(summary.execution_snapshot['fields'][0]['record_version_id'], str(version.id))
        self.assertEqual(Summary.objects.count(), 1)
        self.assertEqual(SummaryItem.objects.count(), 1)
        task = Task.objects.get(task_type='summary')
        self.assertEqual((task.status, task.schema_version), ('completed', 'summary-task-2'))
        self.assertEqual(TaskAttempt.objects.filter(task=task, status='completed').count(), 1)
        self.assertEqual(ConsumerReceipt.objects.filter(consumer='summary').count(), 1)
        self.assertEqual(WriteRequest.objects.filter(action='summary_create').count(), 1)
        self.assertFalse(task.input_refs.get('fields'))
        self.assertEqual(self.runtime.payload['providers'], [])
        self.assertEqual([model.objects.count() for model in untouched], baseline)

    def test_changed_scope_and_later_records_do_not_silently_replace_summary(self):
        activate_summary_configs(self)
        self.record()
        summary = self.summary()
        with self.assertRaises(IdempotencyConflict):
            self.summary(to_date=date(2026, 10, 11))
        self.record('37.1')
        with self.assertRaisesMessage(SummaryRejected, 'DEMO_SUMMARY_NOT_READABLE'):
            self.summary()
        self.assertEqual(list(Summary.objects.values_list('id', flat=True)), [summary.id])

    def test_non_synthetic_records_cannot_enter_demo_summary(self):
        activate_summary_configs(self)
        self.record()
        # A legacy version without a synthetic source must not be swept in.
        RecordVersion.objects.update(source_revision=None)
        with self.assertRaisesMessage(SummaryRejected, 'DEMO_SYNTHETIC_RECORDS_REQUIRED'):
            self.summary()
        self.assertFalse(Summary.objects.exists())
        self.assertFalse(Task.objects.filter(task_type='summary').exists())
