"""Descriptive summary plots: pure frozen-input contract, never clinical inference."""
from copy import deepcopy
from datetime import date
from uuid import uuid4

from django.test import SimpleTestCase, TestCase, TransactionTestCase

from summaries.trends import build_summary_trends


def sample(value='36.8', instant='2026-10-10T01:00:00+00:00', **changes):
    return {'field_id': str(uuid4()), 'record_id': str(uuid4()), 'record_version_id': str(uuid4()),
        'field_code': 'synthetic_temperature', 'concept_code': 'software-only:temperature',
        'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': value,
        'qualifier': 'eq', 'raw_unit': 'Cel', 'method': 'SYNTHETIC IDENTICAL METHOD',
        'source_definition_revision_id': 'c102d6ba-d3b9-4619-91c5-eaa801ce5ec1',
        'origin_type': 'self_report', 'source_organization': 'SYNTHETIC SOFTWARE SOURCE',
        'sample_time_origin': 'observation', 'medical_time': {'precision': 'second',
            'resolution_status': 'resolved', 'instant': instant}, **changes}


class SummarySnapshotTrendTests(SimpleTestCase):
    def projection(self, rows):
        before = deepcopy(rows)
        result = build_summary_trends(rows, 'a' * 64)
        self.assertEqual(rows, before)
        self.assertFalse(result['clinical_interpretation'])
        self.assertEqual(result['snapshot_sha256'], 'a' * 64)
        return result['series'][0]

    def test_distinct_record_documents_share_source_definition_and_connect_in_medical_order(self):
        before, after = sample(), sample('36.9', '2026-10-10T02:00:00+00:00')
        self.assertNotEqual(before['record_version_id'], after['record_version_id'])
        series = self.projection([after, before])
        self.assertEqual([point['field_id'] for point in series['points']], [before['field_id'], after['field_id']])
        self.assertEqual(series['segments'], [{'from_field_id': before['field_id'], 'to_field_id': after['field_id'],
            'connect': True, 'reason_codes': []}])

    def test_single_point_does_not_create_curve_or_second_measurement(self):
        series = self.projection([sample()])
        self.assertEqual(len(series['points']), 1)
        self.assertTrue(series['points'][0]['plottable'])
        self.assertEqual(series['segments'], [])

    def test_unknown_censored_and_invalid_value_break_adjacent_segments(self):
        for changes in ({'value_state': 'unknown', 'raw_numeric': None}, {'qualifier': 'gt'},
                {'raw_numeric': 'NaN'}, {'raw_numeric': 'Infinity'}):
            with self.subTest(changes=changes):
                gap = sample('36.9', '2026-10-10T02:00:00+00:00', **changes)
                series = self.projection([sample(), gap, sample('37', '2026-10-10T03:00:00+00:00')])
                self.assertFalse(series['points'][1]['plottable'])
                self.assertTrue(all(not segment['connect'] for segment in series['segments']))

    def test_unknown_partial_or_unresolved_time_never_uses_save_order(self):
        for medical in (None, {'precision': 'month', 'local_year': 2026, 'local_month': 10},
                {'precision': 'second', 'resolution_status': 'local_only', 'instant': None}):
            with self.subTest(medical=medical):
                rows = [sample(), sample('37', '2026-10-10T03:00:00+00:00'), sample(medical_time=medical)]
                series = self.projection(rows)
                self.assertIsNone(series['points'][-1]['plot_at'])
                self.assertTrue(all(not segment['connect'] for segment in series['segments']))
                self.assertTrue(all('UNPOSITIONED_TIME_GAP' in segment['reason_codes'] for segment in series['segments']))

    def test_same_time_duplicates_are_not_ordered_by_record_or_uuid(self):
        series = self.projection([sample(), sample('37'), sample('37.2', '2026-10-10T02:00:00+00:00')])
        self.assertTrue(all(not segment['connect'] for segment in series['segments']))
        self.assertTrue(all('DUPLICATE_MEDICAL_TIME' in segment['reason_codes'] for segment in series['segments']))

    def test_metadata_mismatch_and_legacy_source_absence_break_connection_without_conversion(self):
        for changes, reason in (({'raw_unit': 'F'}, 'UNIT_MISMATCH'), ({'method': None}, 'METHOD_UNKNOWN'),
                ({'method': 'OTHER'}, 'METHOD_MISMATCH'), ({'source_definition_revision_id': str(uuid4())}, 'SOURCE_DEFINITION_MISMATCH'),
                ({'source_definition_revision_id': None}, 'SOURCE_DEFINITION_UNKNOWN'),
                ({'source_organization': None}, 'SOURCE_ORGANIZATION_UNKNOWN'),
                ({'origin_type': 'authorized_clinical'}, 'SOURCE_TYPE_MISMATCH')):
            with self.subTest(changes=changes):
                rows = [sample(), sample('37', '2026-10-10T02:00:00+00:00', **changes)]
                series = self.projection(rows)
                self.assertFalse(series['segments'][0]['connect'])
                self.assertIn(reason, series['segments'][0]['reason_codes'])
                self.assertEqual(series['points'][1]['raw_value'], '37')

    def test_concepts_and_field_codes_remain_separate_and_text_never_becomes_numeric(self):
        rows = [sample(), sample(concept_code='software-only:other'), sample(field_code='other_code'),
            sample(value_type='text', raw_numeric=None)]
        result = build_summary_trends(rows, 'a' * 64)
        self.assertEqual(len(result['series']), 3)
        self.assertTrue(all(not group['segments'] for group in result['series']))


class SummarySnapshotTrendIntegrationTests(TestCase):
    def setUp(self):
        from tests import test_T25
        test_T25.SummaryServiceTest.setUp(self)
        from tests.synthetic_fixtures import SyntheticEvidence
        from tests.integration.helpers import activate_summary_configs
        self.synthetic = SyntheticEvidence(self, self.principal)
        activate_summary_configs(self)

    def test_projection_reads_only_hash_bound_snapshot_and_hides_on_epoch_change(self):
        from records.service import create_record
        from summaries.service import generate_summary, read_summary
        for hour, value in ((1, '36.8'), (2, '36.9')):
            create_record(patient=self.patient, actor=self.principal, kind='observation', capture_method='manual',
                origin_type='self_report', retention_policy=self.retention, use_authorization_id=self.use_auth.id,
                source_revision=self.use_auth.source_revision, source_organization='SYNTHETIC TEST SOURCE',
                fields=[{'field_code': 'temp', 'concept_code': 'software-only:temperature', 'value_type': 'decimal',
                    'value_state': 'known', 'raw_numeric': value, 'raw_unit': 'Cel', 'method': 'synthetic',
                    'qualifier': 'eq', 'sample_time_origin': 'observation', 'medical_time': {'precision': 'second',
                        'local_datetime': f'2026-09-15T0{hour}:00:00', 'timezone_name': 'UTC', 'resolution_status': 'local_only'}}])
        self.patient.refresh_from_db()
        summary = generate_summary(patient=self.patient, requested_by=self.principal, from_date=date(2026, 9, 1),
            to_date=date(2026, 9, 30), scope_timezone='UTC', include_unknown_time=True,
            field_codes=['temp'], environment='test')
        from unittest.mock import patch
        with patch('records.queries.analyte_search', side_effect=AssertionError('No live analyte lookup')):
            view = read_summary(summary.id, self.patient)
        series = view['trend_projection']['series'][0]
        self.assertTrue(series['segments'][0]['connect'])
        self.assertEqual({point['record_version_id'] for point in series['points']},
            {item['record_version_id'] for item in view['items']})
        self.assertEqual({point['source_definition_revision_id'] for point in series['points']},
            {str(self.use_auth.source_revision_id)})
        self.patient.data_epoch += 1
        self.patient.save(update_fields=['data_epoch'])
        stale = read_summary(summary.id, self.patient)
        self.assertIsNone(stale['trend_projection'])
        self.assertEqual(stale['items'], [])


class SummaryTrendPermissionTests(TransactionTestCase):
    def setUp(self):
        from tests.isolated_rows import IsolatedSchemaRows
        self.rows = IsolatedSchemaRows()
        self.addCleanup(self.rows.cleanup)
        from tests import test_summary_async_closeout
        test_summary_async_closeout.AsyncSummaryTests.setUp(self)

    def test_narrowed_field_grant_hides_both_items_and_plot_on_actual_http_read(self):
        from tests import test_summary_async_closeout
        from summaries.jobs import execute_summary
        from summaries.models import Summary
        from authorization.models import AccessGrant
        task, event = test_summary_async_closeout.AsyncSummaryTests.submit(self)
        execute_summary(event, 'synthetic-trend-permission')
        summary = Summary.objects.get(task_id=task.id)
        path = f'/api/summaries/{summary.id}'
        first = self.client.get(path)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertTrue(first.json()['trend_projection']['series'])
        AccessGrant.objects.filter(patient=self.patient, principal=self.principal,
            action='read_summary').update(field_codes=['other_synthetic_field'])
        narrowed = self.client.get(path)
        self.assertEqual(narrowed.status_code, 200, narrowed.content)
        self.assertTrue(narrowed.json()['needs_regeneration'])
        self.assertEqual(narrowed.json()['items'], [])
        self.assertIsNone(narrowed.json()['trend_projection'])
        AccessGrant.objects.filter(patient=self.patient, principal=self.principal,
            action='read_summary').update(field_codes=['temp'])
        from relations.models import Relation
        Relation.objects.filter(patient=self.patient, guardian=self.principal).update(status='revoked')
        revoked = self.client.get(path)
        self.assertEqual(revoked.status_code, 403, revoked.content)
        self.assertNotIn('trend_projection', revoked.json())
        Relation.objects.filter(patient=self.patient, guardian=self.principal).update(status='active')
        from identity.models import Patient
        Patient.objects.filter(id=self.patient.id).update(status='restricted')
        restricted = self.client.get(path)
        self.assertEqual(restricted.status_code, 403, restricted.content)
        self.assertEqual(restricted.json()['code'], 'SUMMARY_PATIENT_SCOPE_DENIED')
        self.assertNotIn('trend_projection', restricted.json())
