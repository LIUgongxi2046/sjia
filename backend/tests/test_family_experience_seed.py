"""Bounded fictional data seeding; no credentials, external services or medicine."""
import ast
import io
import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.core.management import call_command
from django.core.management.base import CommandError
from django.conf import settings
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from core.demo_experience import KEY, NAMESPACE, identity, require_test_context, seed_family_experience
from identity.models import Patient, Principal, PrincipalIdentity
from tests.synthetic_fixtures import SyntheticEvidence


class FamilyExperienceSeedStaticTests(SimpleTestCase):
    def test_command_does_not_reference_credential_or_session_models(self):
        core = Path(__file__).parents[1] / 'core'
        paths = [*core.glob('demo_*.py'),
            core / 'management' / 'commands' / 'seed_family_experience.py']
        for path in paths:
            tree = ast.parse(path.read_text())
            identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            self.assertFalse({'PrincipalCredential', 'Session', 'register_account', 'make_password',
                'authenticate_account', 'establish_session'} & identifiers)

    def test_no_production_or_unacknowledged_execution(self):
        with override_settings(DEBUG=False, SJIA_ENVIRONMENT='production'):
            with self.assertRaisesMessage(CommandError, 'DEMO_REQUIRES_EXPLICIT_DEBUG_TEST_DATABASE'):
                require_test_context(acknowledge_test_only=True)
        with override_settings(DEBUG=True, SJIA_ENVIRONMENT='test'):
            with self.assertRaisesMessage(CommandError, 'DEMO_REQUIRES_EXPLICIT_DEBUG_TEST_DATABASE'):
                require_test_context(acknowledge_test_only=False)


@override_settings(DEBUG=True, SJIA_ENVIRONMENT='test')
class FamilyExperienceSeedTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.operator = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        PrincipalIdentity.objects.create(principal=self.actor, provider='self', provider_subject='family-experience', verified_at=now)
        self.fixture = SyntheticEvidence(self, self.operator)
        self.fixture.create(__import__('retention.models', fromlist=['RetentionPolicy']).RetentionPolicy,
            policy_key='experience-test-audit', version=1, data_class='audit',
            rules={'retention_days': None, 'disposition': 'retain'}, schema_version='retention-1', status='active',
            approved_by=self.operator, approved_at=now, evidence_ref=self.fixture.reference)
        self.fixture.runtime()

    def seed(self, **options):
        return seed_family_experience(username='family-experience', principal_id=self.actor.id,
            acknowledge_test_only=True, **options)

    def test_non_test_database_rejected_without_writes(self):
        count = Patient.objects.count()
        with patch.dict(connection.settings_dict, {'NAME': 'sjia_dev'}):
            with self.assertRaisesMessage(CommandError, 'DEMO_REQUIRES_EXPLICIT_DEBUG_TEST_DATABASE'):
                self.seed()
        self.assertEqual(Patient.objects.count(), count)

    def test_identity_mismatch_or_missing_account_does_not_write_dataset(self):
        from sources.models import SourceRegistry
        for username, identifier in [('other-account', self.actor.id), ('family-experience', uuid4())]:
            with self.assertRaisesMessage(CommandError, 'DEMO_ACCOUNT_IDENTITY_MISMATCH'):
                seed_family_experience(username=username, principal_id=identifier, acknowledge_test_only=True)
        self.assertFalse(Patient.objects.exists())
        self.assertFalse(SourceRegistry.objects.filter(source_key=KEY).exists())

    def test_support_account_refused_without_removing_its_roles(self):
        from authorization.models import ServiceRoleGrant
        grant = self.fixture.create(ServiceRoleGrant, principal=self.actor, environment=self.fixture.gate,
            action='manage_configs', granted_by=self.operator, evidence_ref=self.fixture.reference, valid_from=timezone.now())
        with self.assertRaisesMessage(CommandError, 'DEMO_REQUIRES_FAMILY_ONLY_ACCOUNT'):
            self.seed()
        self.assertTrue(ServiceRoleGrant.objects.filter(id=grant.id).exists())
        self.assertFalse(Patient.objects.exists())

    def test_other_patient_identity_rolls_back_all_new_database_writes(self):
        from sources.models import SourceRegistry
        patient = Patient.objects.create(id=identity('patient:child_a'), display_id='Existing unrelated child',
            status='active', created_at=timezone.now())
        with self.assertRaisesMessage(CommandError, 'DEMO_PATIENT_CONFLICT'):
            self.seed()
        self.assertFalse(SourceRegistry.objects.filter(source_key=KEY).exists())
        self.assertEqual(list(Patient.objects.values_list('id', flat=True)), [patient.id])

    def test_invalid_configuration_rolls_back_dataset_and_preserves_runtime(self):
        from configurations.models import ConfigVersion
        from sources.models import SourceRegistry
        old_runtime = self.fixture.gate.runtime_config_id
        unrelated = self.fixture.configuration('content', {'schema_version': 'field-config-1', 'kind': 'observation',
            'fields': [{'field_code': 'temp', 'label': 'Temperature', 'value_type': 'decimal', 'required': False,
                'writable': True, 'allowed_values': [], 'allowed_units': ['C'], 'dictionary_category': None,
                'minimum': None, 'maximum': None, 'max_length': None, 'max_occurrences': 1, 'time_required': False}],
            'watch_field_codes': []})
        with self.assertRaisesMessage(CommandError, 'DEMO_REFUSES_OTHER_FIELD_CONFIGURATION:observation'):
            self.seed()
        self.fixture.gate.refresh_from_db()
        self.assertEqual(self.fixture.gate.runtime_config_id, old_runtime)
        self.assertFalse(SourceRegistry.objects.filter(source_key=KEY).exists())
        self.assertTrue(ConfigVersion.objects.filter(id=unrelated.id, status='enabled').exists())

    def test_complete_dataset_real_readback_and_idempotence(self):
        from alerting.models import AlertEvaluation, AlertEvent
        from archives.models import ArchiveBatch, ArchiveDocument, ArchiveDocumentVersion, ArchivePage
        from case_details.models import ClinicalEpisode
        from assessment.models import Assessment
        from authorization.models import AccessGrant, ServiceRoleGrant
        from drafts.models import Draft
        from files.models import FileObject
        from identity.models import PrincipalCredential
        from records.models import Record, RecordVersion, RecordAttachment, RecordField
        from sources.models import SourceRevision
        from summaries.models import Summary, SummaryItem
        from summaries.service import read_summary
        from tasks.models import Task
        from django.contrib.sessions.models import Session
        credentials, sessions = PrincipalCredential.objects.count(), Session.objects.count()
        locked_tables = []
        def capture_lock_order(execute, sql, params, many, context):
            if 'FOR UPDATE' in sql:
                locked_tables.append(sql)
            return execute(sql, params, many, context)
        with connection.execute_wrapper(capture_lock_order):
            result = self.seed()
        gate_index = next(index for index, sql in enumerate(locked_tables) if 'FROM "sjia"."environment_gate"' in sql)
        actor_index = next(index for index, sql in enumerate(locked_tables) if 'FROM "sjia"."principal"' in sql)
        self.assertLess(gate_index, actor_index)
        self.assertEqual(result['environment'], settings.SJIA_ENVIRONMENT)
        self.assertEqual(result['report_files'], 5)
        self.assertEqual(Patient.objects.count(), 2)
        self.assertEqual(Record.objects.count(), 22)
        self.assertEqual(RecordVersion.objects.count(), 23)
        self.assertEqual(Draft.objects.count(), 2)
        self.assertEqual(FileObject.objects.filter(purpose='original', status='available').count(), 7)
        self.assertEqual(result['visit_note_files'], 2)
        self.assertEqual(ArchiveDocument.objects.count(), 2)
        self.assertEqual(ArchiveBatch.objects.filter(intake_mode='manual', quota_policy_id=None).count(), 2)
        self.assertFalse(ArchivePage.objects.exclude(current_task_id=None).exists())
        self.assertFalse(ClinicalEpisode.objects.exists())
        for document in ArchiveDocumentVersion.objects.all():
            self.assertEqual(document.grouping_status, 'pending')
            self.assertEqual(len(document.proposed_metadata['episodes']), 2)
            self.assertIn('未经专业核对', document.confirmed_text)
        # The displayed originals contain every value, unit, reference interval
        # and flag that the seeded transcription attributes to them.
        from PIL import Image
        from files.service import read_object_bytes
        labels = {'血小板计数': 'Platelets', '铁蛋白': 'Ferritin', 'C反应蛋白': 'CRP'}
        for record in Record.objects.filter(kind='report'):
            version = RecordVersion.objects.get(record_id=record.id, version=record.current_version)
            attachment = RecordAttachment.objects.get(record_version_id=version.id)
            with Image.open(io.BytesIO(read_object_bytes(attachment.file_id))) as image:
                rows = json.loads(image.info['demo-content'])['rows']
            fields = list(RecordField.objects.filter(record_version_id=version.id).order_by('ordinal'))
            self.assertEqual(len(rows), len(fields))
            for row, field in zip(rows, fields, strict=True):
                interval = field.reference_interval.replace('（示例范围）', '').replace('–', '-')
                self.assertEqual(row[0], f'{labels[field.original_name]} (ref {interval})')
                self.assertEqual(row[2], field.raw_unit)
                raw = field.raw_text if field.value_state == 'known' else 'Not provided'
                self.assertEqual(row[1], raw + (' High' if field.report_flag == '↑' else ''))
        self.assertEqual(Summary.objects.count(), 2)
        self.assertGreater(SummaryItem.objects.count(), 0)
        self.assertTrue(all(row == 'completed' for row in Task.objects.values_list('status', flat=True)))
        self.assertEqual(PrincipalCredential.objects.count(), credentials)
        self.assertEqual(Session.objects.count(), sessions)
        self.assertFalse(ServiceRoleGrant.objects.filter(principal=self.actor).exists())
        self.assertEqual(set(AccessGrant.objects.filter(principal=self.actor).values_list('patient_id', flat=True)),
            {identity('patient:child_a'), identity('patient:child_b')})
        self.assertFalse(AlertEvaluation.objects.exists())
        self.assertFalse(AlertEvent.objects.exists())
        self.assertFalse(Assessment.objects.exists())
        for summary in Summary.objects.select_related('patient'):
            current = read_summary(summary.id, summary.patient)
            self.assertFalse(current['needs_regeneration'])
            self.assertTrue(current['items'])
        revision = SourceRevision.objects.get(id=identity('source-revision'))
        evidence = json.loads(Path(revision.source.location).read_text())
        self.assertFalse(evidence['clinical_validation'])
        self.assertFalse(evidence['medical_approval'])
        self.assertEqual(evidence['environment'], settings.SJIA_ENVIRONMENT)
        counts = {model: model.objects.count() for model in (Record, RecordVersion, Draft, FileObject, Summary, Task, AccessGrant, ArchiveDocument, ArchiveDocumentVersion, ArchiveBatch, ArchivePage)}
        repeated = self.seed()
        self.assertEqual(repeated['summary_ids'], result['summary_ids'])
        self.assertEqual({model: model.objects.count() for model in counts}, counts)

    def test_command_requires_explicit_identity_arguments(self):
        with self.assertRaises(CommandError):
            call_command('seed_family_experience', stdout=io.StringIO())


@override_settings(DEBUG=True, SJIA_ENVIRONMENT='synthetic-browser-demo')
class BrowserDemoEnvironmentSeedTests(FamilyExperienceSeedTests):
    """The same complete real-service contract in the retained browser environment."""
