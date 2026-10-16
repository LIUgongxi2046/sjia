"""Synthetic-only additive batch contracts; isolated real-service integration."""
import ast
import io
import json
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from PIL import Image

from enrichment_plan import (BATCH_KEY, BATCH_NAMESPACE, PATIENT_KEYS, anchor_day,
    draft_plan, record_plan, report_rows, visit_plan)
from enrich_family_experience import EnrichmentRejected, enrich_family_experience, inspect_enrichment
from core.demo_experience import identity, seed_family_experience
from core.demo_files_summary import render_demo_report_png
from core.demo_visit_files import render_demo_visit_png
from records.input_validation import validate_family_record_input

AS_OF = datetime(2026, 10, 12, 7, 0, tzinfo=dt_timezone.utc)


class EnrichmentPlanTests(SimpleTestCase):
    def test_bounded_wire_valid_unique_complete_inputs(self):
        rows = record_plan(AS_OF)
        self.assertEqual(Counter(row['kind'] for row in rows), {'observation': 56, 'report': 6, 'therapy': 4})
        self.assertEqual(len({row['key'] for row in rows}), 66)
        self.assertEqual(Counter(row['patient_key'] for row in rows), {'child_a': 33, 'child_b': 33})
        for row in rows:
            payload = {'kind': row['kind'], 'capture_method': 'manual', 'field_config_version': str(uuid4()),
                'fields': row['fields'], 'source_organization': row['source_organization']}
            if row['kind'] == 'report':
                payload['report'] = row['report_metadata']
            validate_family_record_input(payload)
            self.assertIsNone(row['source_organization'])
            for field in row['fields']:
                self.assertNotIn('verification_status', field)
                self.assertNotIn('confirmation_id', field)
        for child in PATIENT_KEYS:
            observations = [row for row in rows if row['patient_key'] == child and row['kind'] == 'observation']
            self.assertEqual(len({row['fields'][0]['medical_time']['local_datetime'][:10] for row in observations}), 14)
            for row in observations:
                temp = row['fields'][0]
                self.assertEqual(temp['qualifier'], 'eq')
                self.assertEqual(temp['medical_time']['timezone_name'], 'Asia/Shanghai')
                self.assertEqual(temp['medical_time']['precision'], 'minute')
                self.assertEqual(temp['medical_time']['resolution_status'], 'local_only')
                self.assertNotIn('instant', temp['medical_time'])
                self.assertLess(row['recorded_at'], AS_OF)

    def test_every_new_original_matches_exact_plan(self):
        for row in record_plan(AS_OF):
            if row['kind'] != 'report':
                continue
            sample = datetime.fromisoformat(row['report_metadata']['sample_time']['local_date']).date()
            content = render_demo_report_png(report_key=row['key'].replace('/', '-'),
                report_date=sample, rows=report_rows(row))
            with Image.open(io.BytesIO(content)) as image:
                saved = json.loads(image.info['demo-content'])
                self.assertEqual(saved['report_date'], sample.isoformat())
                self.assertEqual(saved['rows'], [list(value) for value in report_rows(row)])
                self.assertTrue(saved['synthetic'])
                self.assertFalse(saved['clinical_document'])
                image.verify()
        self.assertEqual(len(visit_plan(AS_OF)), 4)
        for note in visit_plan(AS_OF):
            with Image.open(io.BytesIO(render_demo_visit_png(note))) as image:
                saved = json.loads(image.info['demo-content'])
                self.assertEqual(saved['confirmed_text'], note['confirmed_text'])
                self.assertEqual(saved['proposed_episodes'], note['proposed_episodes'])
                self.assertFalse(saved['ocr_performed'])
                self.assertIn('未经专业核对', saved['confirmed_text'])
                image.verify()

    def test_drafts_are_distinct_restorable_normal_form_payloads(self):
        rows = draft_plan(AS_OF)
        fixture = Path(__file__).parents[3] / 'guardian-h5/scripts/fixtures/enrichment-drafts.synthetic.json'
        self.assertEqual(rows, json.loads(fixture.read_text()))
        self.assertEqual(Counter(row['form_type'] for row in rows), {'report': 2, 'therapy': 2})
        self.assertEqual(len({row['key'] for row in rows}), 4)
        for row in rows:
            self.assertEqual(row['payload']['photos'], [])
            if row['form_type'] == 'report':
                self.assertIsNone(row['payload']['candidates'][0]['value'])
            else:
                self.assertEqual(row['payload']['status'], '无法确定')
                self.assertIsNone(row['payload']['selectedSource'])

    def test_plan_is_deterministic_and_sources_contain_no_identity_mutators(self):
        self.assertEqual(record_plan(AS_OF), record_plan(AS_OF))
        first = record_plan(AS_OF)
        first[0]['fields'][0]['raw_numeric'] = '100'
        self.assertEqual(record_plan(AS_OF)[0]['fields'][0]['raw_numeric'], '36.8')
        with self.assertRaisesRegex(ValueError, 'AWARE'):
            anchor_day(datetime(2026, 10, 12))
        forbidden = {'PrincipalCredential', 'Session', 'register_account', 'make_password',
            'authenticate_account', 'establish_session', 'seed_family_experience'}
        for path in Path(__file__).parents[1].glob('*.py'):
            identifiers = {node.id for node in ast.walk(ast.parse(path.read_text())) if isinstance(node, ast.Name)}
            self.assertFalse(identifiers & forbidden, str(path))

    @override_settings(DEBUG=False, SJIA_ENVIRONMENT='production')
    def test_production_rejected_before_connection(self):
        with patch('django.db.backends.base.base.BaseDatabaseWrapper.ensure_connection',
                side_effect=AssertionError('No production DB connection')):
            with self.assertRaisesMessage(CommandError, 'DEMO_REQUIRES_EXPLICIT_DEBUG_TEST_DATABASE'):
                enrich_family_experience(username='exact', principal_id=uuid4(), acknowledge_test_only=True)


@override_settings(DEBUG=True, SJIA_ENVIRONMENT='test')
class EnrichmentServiceTests(TestCase):
    def setUp(self):
        from tests.test_family_experience_seed import FamilyExperienceSeedTests
        FamilyExperienceSeedTests.setUp(self)
        self.baseline = seed_family_experience(username='family-experience', principal_id=self.actor.id,
            acknowledge_test_only=True)

    def enrich(self, **changes):
        return enrich_family_experience(**{'username': 'family-experience', 'principal_id': self.actor.id,
            'acknowledge_test_only': True, **changes})

    def snapshot(self):
        from records.models import Record, RecordVersion, RecordField
        from drafts.models import Draft
        from archives.models import ArchiveDocument, ArchiveDocumentVersion
        from summaries.models import Summary, SummaryItem
        return {model: {str(row['pk']): row for row in model.objects.values('pk',
            *[field.attname for field in model._meta.fields if not field.primary_key])}
            for model in (Record, RecordVersion, RecordField, Draft, ArchiveDocument,
                ArchiveDocumentVersion, Summary, SummaryItem)}

    def assert_preserved(self, before):
        for model, rows in before.items():
            current = {str(row['pk']): row for row in model.objects.filter(pk__in=rows).values('pk',
                *[field.attname for field in model._meta.fields if not field.primary_key])}
            self.assertEqual(current, rows, model._meta.label)

    def test_real_batch_readback_and_idempotence_preserve_legacy_and_authority(self):
        from alerting.models import AlertEvaluation, AlertEvent
        from assessment.models import Assessment
        from archives.models import ArchiveDocument
        from authorization.models import AccessGrant, ServiceRoleGrant
        from configurations.models import ConfigVersion
        from drafts.models import Draft
        from files.models import FileObject, FilePage
        from files.service import read_object_bytes
        from identity.models import Principal
        from medical_time.models import MedicalTime
        from recognition.models import SourceAnchor
        from records.models import Record, RecordVersion, RecordField, RecordAttachment
        from summaries.models import Summary, SummaryItem
        from summaries.service import read_summary
        from tasks.models import Task
        from verification.models import Verification
        before = self.snapshot()
        authority = {model: list(model.objects.order_by('pk').values()) for model in (AccessGrant, ServiceRoleGrant, ConfigVersion)}
        epochs = dict(Principal.objects.values_list('id', 'auth_epoch'))
        preview = inspect_enrichment(username='family-experience', principal_id=self.actor.id, acknowledge_test_only=True)
        self.assertEqual(preview['writes'], 0)
        self.assertEqual(preview['existing_record_receipts'], 0)
        self.assertEqual(self.snapshot(), before)
        result = self.enrich()
        self.assertEqual(result['created'], {'records': 66, 'originals': 10, 'drafts': 4, 'archives': 4, 'summaries': 2})
        self.assert_preserved(before)
        self.assertEqual(Record.objects.count(), 88)
        self.assertEqual(Draft.objects.count(), 6)
        self.assertEqual(ArchiveDocument.objects.count(), 6)
        self.assertEqual(FileObject.objects.filter(purpose='original').count(), 17)
        self.assertEqual(Summary.objects.count(), 4)
        self.assertEqual(dict(Principal.objects.values_list('id', 'auth_epoch')), epochs)
        for model, expected in authority.items():
            self.assertEqual(list(model.objects.order_by('pk').values()), expected)
        for model in (AlertEvaluation, AlertEvent, Assessment, Verification):
            self.assertFalse(model.objects.exists())
        self.assertFalse(Task.objects.exclude(status='completed').exists())
        planned = {row['key']: row for row in record_plan(datetime.fromisoformat(result['anchor']))}
        for key, receipt in result['records'].items():
            record = Record.objects.get(id=receipt['record_id'])
            spec = planned[key]
            self.assertEqual(record.patient_id, identity(f'patient:{spec["patient_key"]}'))
            version = RecordVersion.objects.get(id=receipt['record_version_id'])
            self.assertEqual(version.author_id, self.actor.id)
            self.assertEqual(version.quality_status, 'valid')
            fields = list(RecordField.objects.filter(record_version_id=version.id).order_by('ordinal'))
            if record.kind == 'observation':
                temp = fields[0]
                medical = MedicalTime.objects.get(id=temp.medical_time_id, patient=record.patient)
                self.assertEqual(medical.resolution_status, 'resolved')
                self.assertEqual(medical.utc_offset_minutes, 480)
                self.assertEqual(medical.instant, spec['recorded_at'] - timedelta(minutes=5))
                self.assertEqual(temp.qualifier, 'eq')
                self.assertIsNone(version.source_organization)
            if record.kind == 'report':
                attachment = RecordAttachment.objects.get(record_version_id=version.id)
                with Image.open(io.BytesIO(read_object_bytes(attachment.file_id))) as image:
                    original = json.loads(image.info['demo-content'])
                self.assertEqual(original['rows'], [list(row) for row in report_rows(spec)])
                self.assertEqual(original['report_date'], spec['report_metadata']['sample_time']['local_date'])
                self.assertEqual(len(fields), 8)
                for field in fields:
                    anchor_row = SourceAnchor.objects.get(id=field.anchor_id, patient=record.patient)
                    self.assertIn('EXAMPLE REPORT', anchor_row.original_text)
                    self.assertTrue(FilePage.objects.filter(id=anchor_row.page_id,
                        file_id=attachment.file_id, patient=record.patient).exists())
        for id in result['summary_ids']:
            summary = Summary.objects.select_related('patient').get(id=id)
            view = read_summary(summary.id, summary.patient)
            self.assertFalse(view['needs_regeneration'])
            self.assertTrue(view['items'])
            self.assertFalse(SummaryItem.objects.filter(summary_id=summary.id).exclude(patient_id=summary.patient_id).exists())
            self.assertTrue(all(RecordVersion.objects.filter(id=item['record_version_id'],
                patient_id=summary.patient_id).exists() for item in view['items']))
            temp = next(group for group in view['trend_projection']['series'] if group['field_code'] == 'temp')
            self.assertEqual(sum(point['plottable'] for point in temp['points']), 28)
            self.assertTrue(all(not segment['connect'] for segment in temp['segments']))
        for id in self.baseline['summary_ids']:
            summary = Summary.objects.select_related('patient').get(id=id)
            self.assertTrue(read_summary(summary.id, summary.patient)['needs_regeneration'])
        after = self.snapshot()
        again = self.enrich()
        self.assertEqual(again['created'], dict.fromkeys(result['created'], 0))
        self.assertEqual(again['records'], result['records'])
        self.assertEqual(again['summary_ids'], result['summary_ids'])
        self.assert_preserved(after)

    def test_rerun_keeps_user_corrections_new_records_edited_abandoned_drafts_and_stale_summary(self):
        from drafts.models import Draft
        from drafts.service import update_draft, abandon_draft
        from records.correction import correct_record
        from records.models import Record
        from records.service import create_record
        from sources.models import DataUseAuthorization, SourceRevision
        from retention.models import RetentionPolicy
        first = self.enrich()
        record = Record.objects.get(id=first['records']['child_a/observation-01-morning']['record_id'])
        fields = deepcopy(record_plan(datetime.fromisoformat(first['anchor']))[0]['fields'])
        fields[0]['raw_numeric'] = '36.6'
        policy = RetentionPolicy.objects.get(id=identity('policy:observation'))
        use = DataUseAuthorization.objects.get(id=identity('use:child_a'))
        revision = SourceRevision.objects.get(id=identity('source-revision'))
        correct_record(record=record, expected_version=1, actor=self.actor, reason='本人体验后补充更正',
            fields=fields, use_authorization_id=use.id, retention_policy=policy, source_revision=revision)
        create_record(patient=record.patient, actor=self.actor, kind='observation', capture_method='manual',
            origin_type='self_report', retention_policy=policy, use_authorization_id=use.id, source_revision=revision,
            fields=[{'field_code': 'observation_note', 'value_type': 'text', 'value_state': 'known',
                'raw_text': '本人另外保存的记录', 'sample_time_origin': 'observation'}])
        edited = Draft.objects.get(id=first['drafts']['child_a/report-draft']['id'])
        update_draft(draft=edited, expected_version=1, payload={**edited.payload, 'reportRef': '本人已改的标识'})
        abandoned = Draft.objects.get(id=first['drafts']['child_b/therapy-draft']['id'])
        abandon_draft(draft=abandoned, expected_version=1)
        legacy = Draft.objects.get(id=self.baseline['records']['drafts']['child_a']['draft_id'])
        update_draft(draft=legacy, expected_version=1, payload={**legacy.payload, 'description': '本人原有草稿'})
        before = self.snapshot()
        repeated = self.enrich()
        self.assertEqual(repeated['created'], dict.fromkeys(first['created'], 0))
        self.assertEqual(repeated['summary_ids'], first['summary_ids'])
        self.assert_preserved(before)

    def test_midway_failure_rolls_back_and_same_batch_can_retry(self):
        import enrich_family_experience as module
        before = self.snapshot()
        original = module._record
        count = 0
        def fail(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 59:
                raise RuntimeError('synthetic mid-batch interruption')
            return original(*args, **kwargs)
        with patch.object(module, '_record', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'mid-batch'):
                self.enrich()
        self.assertEqual(self.snapshot(), before)
        result = self.enrich()
        self.assertEqual(result['created']['records'], 66)
        self.assertEqual(result['created']['originals'], 10)

    def test_changed_immutable_batch_input_fails_closed_without_overwriting(self):
        first = self.enrich()
        before = self.snapshot()
        rows = record_plan(datetime.fromisoformat(first['anchor']))
        rows[0]['fields'][0]['raw_numeric'] = '40.0'
        with patch('enrich_family_experience.record_plan', return_value=rows):
            with self.assertRaisesMessage(EnrichmentRejected, 'ENRICHMENT_RECORD_INPUT_CONFLICT'):
                self.enrich()
        self.assertEqual(self.snapshot(), before)

    def test_disposed_draft_or_wrong_identity_never_recreates_content(self):
        from drafts.models import Draft
        from django.utils import timezone
        from retention.models import ContentAccessRevocation
        from retention.disposition import DispositionRejected
        first = self.enrich()
        draft_id = first['drafts']['child_a/report-draft']['id']
        ContentAccessRevocation.objects.create(plan_id=uuid4(), patient_id=identity('patient:child_a'),
            adapter='drafts.draft', object_id=draft_id, object_version='1', revoked_at=timezone.now(), proof_sha256='a' * 64)
        before = self.snapshot()
        with self.assertRaisesMessage(DispositionRejected, 'CONTENT_ACCESS_REVOKED'):
            self.enrich()
        self.assertEqual(self.snapshot(), before)
        with self.assertRaisesMessage(CommandError, 'DEMO_ACCOUNT_IDENTITY_MISMATCH'):
            self.enrich(username='other-account')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(Draft.all_objects.filter(id=draft_id).count(), 1)


@override_settings(DEBUG=True, SJIA_ENVIRONMENT='synthetic-browser-demo')
class EnrichmentIndependentProcessTests(TransactionTestCase):
    """Actual CLI admits a fresh process; durable originals survive its exit."""
    def setUp(self):
        from tests.isolated_rows import IsolatedSchemaRows
        rows = IsolatedSchemaRows(require_empty=True)
        self.addCleanup(rows.cleanup)
        from tests.test_family_experience_seed import FamilyExperienceSeedTests
        FamilyExperienceSeedTests.setUp(self)
        from files.storage import LocalFileStore
        store = LocalFileStore(settings.FILE_OBJECT_ROOT)
        for name in ('files.service.object_store', 'files.purpose.object_store'):
            replacement = patch(name, store)
            replacement.start()
            self.addCleanup(replacement.stop)
        import os
        self.repo = Path(__file__).resolve().parents[3]
        self.env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.settings.development',
            'SJIA_ENVIRONMENT': settings.SJIA_ENVIRONMENT, 'SJIA_DEBUG': '1',
            'SJIA_DB_NAME': connection.settings_dict['NAME'],
            'SJIA_CONFIG_EVIDENCE_ROOT': str(settings.CONFIG_EVIDENCE_ROOT),
            'SJIA_CONFIG_EVIDENCE_MAX_BYTES': str(settings.CONFIG_EVIDENCE_MAX_BYTES),
            'SJIA_FILE_OBJECT_ROOT': str(settings.FILE_OBJECT_ROOT)}
        self.run_cli([str(self.repo / 'backend/manage.py'), 'seed_family_experience'])

    def run_cli(self, args, *, extra=()):
        import subprocess
        import sys
        result = subprocess.run([sys.executable, *args, '--username', 'family-experience',
            '--principal-id', str(self.actor.id), '--acknowledge-test-only', *extra],
            cwd=self.repo / 'backend', env=self.env, capture_output=True, text=True, timeout=240, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_actual_admitted_cli_preview_append_and_readback_after_exit(self):
        from records.models import Record
        from files.models import FileObject
        from files.service import read_object_bytes
        from summaries.models import Summary
        from summaries.service import read_summary
        from configurations.models import ConfigVersion
        from configurations.qualification import configuration_qualification_reason
        from family_content.models import EnvironmentGate
        script = [str(self.repo / 'tools/demo/run_enrichment.py')]
        preview = self.run_cli(script, extra=('--check-only',))
        self.assertEqual(preview['writes'], 0)
        self.assertEqual(preview['existing_record_receipts'], 0)
        self.assertEqual(Record.objects.count(), 22)
        result = self.run_cli(script)
        self.assertEqual(result['created'], {'records': 66, 'originals': 10, 'drafts': 4, 'archives': 4, 'summaries': 2})
        self.assertEqual(Record.objects.count(), 88)
        runtime = ConfigVersion.objects.get(id=EnvironmentGate.objects.get(environment=settings.SJIA_ENVIRONMENT).runtime_config_id)
        self.assertIsNone(configuration_qualification_reason(runtime, settings.SJIA_ENVIRONMENT))
        self.assertEqual(runtime.payload['providers'], [])
        for file in FileObject.objects.filter(purpose='original', status='available'):
            with Image.open(io.BytesIO(read_object_bytes(file.id))) as image:
                payload = json.loads(image.info['demo-content'])
                self.assertTrue(payload['synthetic'])
                self.assertFalse(payload['clinical_document'])
                image.verify()
        for id in result['summary_ids']:
            summary = Summary.objects.select_related('patient').get(id=id)
            view = read_summary(summary.id, summary.patient)
            self.assertFalse(view['needs_regeneration'])
            self.assertTrue(view['items'])
        repeated = self.run_cli(script)
        self.assertEqual(repeated['created'], dict.fromkeys(result['created'], 0))
        self.assertEqual(repeated['records'], result['records'])
        self.assertEqual(repeated['summary_ids'], result['summary_ids'])
