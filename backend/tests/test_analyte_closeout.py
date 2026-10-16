"""Synthetic unit projections and record-scoped trend admission, no clinical policy."""
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from configurations.models import ConfigApproval
from records.models import Record, RecordField, RecordVersion
from records.queries import analyte_search
from tests.synthetic_fixtures import SyntheticEvidence
from tests.test_record_access_boundaries import RecordAccessBoundaryTest


@override_settings(SJIA_ENVIRONMENT='test')
class AnalyteCloseoutTests(TestCase):
    setUp = RecordAccessBoundaryTest.setUp
    _grant = RecordAccessBoundaryTest._grant
    _record = RecordAccessBoundaryTest._record
    _permissions = RecordAccessBoundaryTest._permissions
    _time = RecordAccessBoundaryTest._time

    def _measurement(self, day, value, unit, *, actor=None, source=None, method='synthetic-method'):
        time = self._time(day)
        time.timezone_name = 'UTC'
        time.save(update_fields=['timezone_name'])
        record = self._record(actor=actor, times=[time])
        RecordVersion.objects.filter(record_id=record.id).update(source_revision=source or self.synthetic.source)
        field = RecordField.objects.get(record_version_id=RecordVersion.objects.get(record_id=record.id).id)
        field.value_type, field.raw_numeric, field.raw_unit = 'decimal', Decimal(value), unit
        field.method, field.qualifier = method, 'eq'
        field.save()
        return record, field

    def _mapping(self, *, multiplier='10'):
        return self.synthetic.configuration('mapping', {'schema_version': 'unit-mapping-2',
            'mappings': [{'mapping_id': str(uuid4()), 'concept_code': 'allowed-concept',
                'source_revision_id': str(self.synthetic.source.id), 'raw_unit': unit,
                'normalized_unit': 'synthetic-small-unit', 'method_scope': 'synthetic-method',
                'multiplier': multiplier if unit == 'synthetic-large-unit' else '1',
                'additive': '0', 'exact_values_only': True}
                for unit in ['synthetic-large-unit', 'synthetic-small-unit']],
            'evidence_refs': [self.synthetic.reference]})

    def _pair(self):
        self.synthetic = SyntheticEvidence(self, self.principal)
        self.config = self._mapping()
        self.before = self._measurement(1, '2', 'synthetic-large-unit')[1]
        self.after = self._measurement(2, '25', 'synthetic-small-unit')[1]

    def _read(self, scoped=False):
        return analyte_search(patient=self.patient, concept_code='allowed-concept',
            principal=self.principal if scoped else None,
            permission_scopes=self._permissions() if scoped else None)

    def test_exact_mapping_preserves_raw_and_exposes_frozen_provenance(self):
        self._pair()
        result = self._read()
        item = result['trend'][1]
        self.assertEqual(item['direction'], 'up')
        self.assertEqual([v['raw_numeric'] for v in result['trend']], ['2', '25'])
        self.assertEqual(item['comparison']['previous_value'], '20')
        self.assertEqual(item['comparison']['current_value'], '25')
        self.assertEqual(item['comparison']['config_checksum'], self.config.checksum)
        self.assertEqual(item['comparison']['previous_field_id'], str(self.before.id))
        self.assertEqual(item['comparison']['current_source_revision_id'], str(self.synthetic.source.id))
        self.before.refresh_from_db()
        self.assertEqual(self.before.raw_numeric, Decimal('2'))
        from records.models import NormalizedMeasurement
        from dictionaries.models import UnitMapping
        self.assertFalse(NormalizedMeasurement.objects.exists())
        self.assertFalse(UnitMapping.objects.exists())
        schema = json.loads((Path(__file__).resolve().parents[1] / 'records/search.schema.json').read_text())
        Draft202012Validator({'$ref': '#/$defs/AnalyteTrendView', '$defs': schema['$defs']},
            format_checker=FormatChecker()).validate(result)

    def test_revocation_disables_cross_unit_direction(self):
        self._pair()
        ConfigApproval.objects.filter(config=self.config).update(revoked_at=timezone.now())
        item = self._read()['trend'][1]
        self.assertIsNone(item['direction'])
        self.assertIn('unit_mismatch', item['comparison_reasons'])

    def test_ambiguous_qualified_mappings_are_not_selected_by_latest(self):
        self._pair()
        self._mapping(multiplier='11')
        self.assertIsNone(self._read()['trend'][1]['direction'])

    def test_source_revision_binding_and_unknown_method_fail_closed(self):
        self._pair()
        RecordVersion.objects.filter(id=self.before.record_version_id).update(source_revision=self.use_auth.source_revision)
        self.assertIsNone(self._read()['trend'][1]['direction'])
        RecordVersion.objects.filter(id=self.before.record_version_id).update(source_revision=self.synthetic.source)
        self.after.method = None
        self.after.save(update_fields=['method'])
        self.assertIsNone(self._read()['trend'][1]['direction'])

    def test_qualified_value_never_becomes_exact_comparison(self):
        self._pair()
        RecordField.objects.filter(id=self.before.id).update(qualifier='lt')
        self.assertIsNone(self._read()['trend'][1]['direction'])

    def test_own_and_assigned_grants_filter_before_comparison(self):
        self._pair()
        other_record, other_field = self._measurement(3, '40', 'synthetic-small-unit', actor=self.other)
        self._grant(scope='own')
        result = self._read(scoped=True)
        self.assertEqual(len(result['trend']), 2)
        self.assertNotIn(str(other_field.id), [v['field_id'] for v in result['trend']])
        self._grant(scope='assigned', assigned=other_record.id)
        self.assertEqual(len(self._read(scoped=True)['trend']), 3)
        self.relation.status = 'revoked'
        self.relation.save(update_fields=['status'])
        self.assertEqual(self._read(scoped=True)['trend'], [])

    def test_http_uses_all_current_grants_and_rejects_unknown_body(self):
        self._pair()
        self._grant(scope='own')
        self._measurement(3, '40', 'synthetic-small-unit', actor=self.other)
        response = self.client.post(f'/api/patients/{self.patient.id}/analytes/allowed-concept/search', {}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()['trend']), 2)
        response = self.client.post(f'/api/patients/{self.patient.id}/analytes/allowed-concept/search', {'arbitrary': True}, content_type='application/json')
        self.assertEqual(response.status_code, 400)


class AnalyteTimeResolutionTests(SimpleTestCase):
    def _pair(self, status, *, precision='minute'):
        from types import SimpleNamespace as NS
        from datetime import datetime
        fields = [NS(value_state='known', value_type='decimal', raw_unit='synthetic-unit',
            method='synthetic-method', qualifier='eq', medical_time_id=index, raw_numeric=Decimal(index))
            for index in (1, 2)]
        times = {index: NS(precision=precision, resolution_status=status, instant=None,
            timezone_name='America/New_York', local_datetime=datetime(2026, 11, 1, 1, 15 + (index - 1) * 30),
            local_date=None) for index in (1, 2)}
        return fields, times

    def test_ambiguous_invalid_and_unknown_times_never_imply_direction(self):
        from records.queries import _comparable_direction, comparison_reasons
        for status, reason in [('ambiguous', 'time_ambiguous'), ('invalid', 'time_invalid'),
                ('unknown', 'time_unknown'), ('local_only', 'time_unresolved'), ('resolved', 'time_unresolved')]:
            fields, times = self._pair(status)
            with self.subTest(status=status):
                self.assertIsNone(_comparable_direction(*fields, times))
                self.assertIn(reason, comparison_reasons(*fields, times))

    def test_only_server_resolved_instants_establish_exact_time_order(self):
        from datetime import datetime, timezone
        from records.queries import _comparable_direction
        fields, times = self._pair('resolved')
        # Repeated local hour: local clock order contradicts the actual instants.
        times[1].instant = datetime(2026, 11, 1, 6, 15, tzinfo=timezone.utc)
        times[2].instant = datetime(2026, 11, 1, 5, 45, tzinfo=timezone.utc)
        self.assertIsNone(_comparable_direction(*fields, times))
        times[2].instant = datetime(2026, 11, 1, 6, 45, tzinfo=timezone.utc)
        self.assertEqual(_comparable_direction(*fields, times), 'up')
