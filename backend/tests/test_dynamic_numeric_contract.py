"""Synthetic dynamic numeric contracts and the current dynamic-2 computation.

The invented blood-cell concept, unit and cutoffs below are software test data,
not a clinical configuration or a clinical approval.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase
from pydantic import ValidationError

from alerting.computation import trend_signal
from alerting.policies import DynamicInputPolicy, DynamicNumericVariable
from assessment.medical_intervals import MedicalInterval
from assessment.policies import DECIMAL_UNITS, StaticVariable
from assessment.qualified_facts import QualifiedFact, QualificationRejected
from assessment.selection import SamplingRelation
from configurations.evidence import actual_artifact_checksum
from configurations.schemas import SCHEMAS, alert_subschema_validator, validate_payload


CODE = 'synthetic_blood_cell_count'
CONCEPT = 'synthetic:separately-approved-blood-cell-count'
UNIT = 'SYNTHETIC-count/L'


def numeric_variable(mapping_id=None, **changes):
    return {'variable_code': CODE, 'concept_code': CONCEPT, 'value_type': 'decimal',
        'normalized_unit': UNIT, 'mapping_config_id': str(mapping_id or uuid4()),
        'categorical_values': [], 'accepted_context_types': ['current'], **changes}


def numeric_policy(mapping_id=None):
    return {'schema_version': 'dynamic-input-policy-2', 'purpose': 'software_test',
        'variables': [numeric_variable(mapping_id)],
        'comparisons': [{'variable_code': CODE, 'allow_negative_baseline': False,
            'relative_decimal_places': 2, 'rounding': 'ROUND_HALF_EVEN',
            'methods': [{'baseline_method': 'synthetic', 'current_method': 'synthetic'}]}],
        'max_combination_depth': 4, 'max_combination_operands': 10,
        'evidence_refs': ['synthetic-test-only']}


def numeric_condition(**changes):
    return {'signal_id': 'synthetic_numeric', 'kind': 'trend', 'variable_code': CODE,
        'metric': 'absolute_delta', 'operator': 'gte', 'threshold': '2', 'unit': UNIT,
        'series': {'sample_count': 2, 'max_gap_seconds': 3600, 'comparison_scope': 'each_adjacent_pair',
            'equal_value_breaks_series': True, 'direction': 'increasing'},
        'evidence_refs': ['synthetic-test-only'], **changes}


class DynamicNumericContractTest(SimpleTestCase):
    def test_explicit_numeric_contract_and_registered_schema_accept_new_concept(self):
        payload = numeric_policy()
        result = DynamicInputPolicy.model_validate_json(json.dumps(payload))
        self.assertIsInstance(result.variables[0], DynamicNumericVariable)
        self.assertEqual(result.model_dump(mode='json'), payload)
        self.assertEqual(validate_payload('rule', 'dynamic-input-policy-2', payload), payload)
        schema = SCHEMAS[('rule', 'dynamic-input-policy-2')]
        self.assertIn('DynamicNumericVariable', schema['$defs'])
        self.assertTrue(alert_subschema_validator('#/$defs/TrendCondition').is_valid(numeric_condition()))

    def test_required_identity_unit_mapping_and_no_extra_configuration(self):
        for key in ('variable_code', 'concept_code', 'normalized_unit', 'mapping_config_id',
                'accepted_context_types', 'categorical_values'):
            payload = numeric_policy()
            del payload['variables'][0][key]
            with self.subTest(missing=key), self.assertRaises(ValidationError):
                DynamicInputPolicy.model_validate_json(json.dumps(payload))
        for changes in ({'mapping_config_id': None}, {'mapping_config_id': 'unapproved-text'},
                {'normalized_unit': '  '}, {'concept_code': '\t'}, {'variable_code': ''},
                {'accepted_context_types': []}, {'accepted_context_types': ['current', 'current']},
                {'value_type': 'categorical'}, {'allow_all_report_fields': True}):
            payload = numeric_policy()
            payload['variables'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                DynamicInputPolicy.model_validate_json(json.dumps(payload))

    def test_fixed_numeric_codes_keep_prior_units_and_static_contract_stays_closed(self):
        for code, unit in DECIMAL_UNITS.items():
            variable = numeric_variable(variable_code=code, concept_code=code, normalized_unit=unit)
            old = StaticVariable.model_validate_json(json.dumps(variable))
            new = DynamicNumericVariable.model_validate_json(json.dumps(variable))
            self.assertEqual(old.model_dump(mode='json'), new.model_dump(mode='json'))
            payload = numeric_policy()
            payload['variables'] = [{**variable, 'normalized_unit': UNIT}]
            payload['comparisons'][0]['variable_code'] = code
            with self.subTest(code=code), self.assertRaises(ValidationError):
                DynamicInputPolicy.model_validate_json(json.dumps(payload))
        with self.assertRaises(ValidationError):
            StaticVariable.model_validate_json(json.dumps(numeric_variable()))

    def test_reserved_categorical_and_premise_codes_cannot_be_numeric(self):
        for code in ('cns', 'hemorrhagic_manifestations', 'active_arthritis', 'sjia', 'fever'):
            payload = numeric_policy()
            payload['variables'][0]['variable_code'] = code
            payload['comparisons'][0]['variable_code'] = code
            with self.subTest(code=code), self.assertRaises(ValidationError):
                DynamicInputPolicy.model_validate_json(json.dumps(payload))

    def test_duplicate_identity_or_missing_comparison_never_enables_variable(self):
        for change in ('code', 'concept', 'comparison_missing', 'comparison_unknown'):
            payload = numeric_policy()
            if change in ('code', 'concept'):
                payload['variables'].append(numeric_variable(variable_code=CODE if change == 'code' else 'other',
                    concept_code=CONCEPT if change == 'concept' else 'other'))
            elif change == 'comparison_missing':
                payload['comparisons'] = []
            else:
                payload['comparisons'][0]['variable_code'] = 'unconfigured_report_field'
            with self.subTest(change=change), self.assertRaises(ValidationError):
                DynamicInputPolicy.model_validate_json(json.dumps(payload))

    def test_numeric_and_categorical_variables_cannot_share_code_or_concept(self):
        for collision in ('variable_code', 'concept_code'):
            payload = numeric_policy()
            categorical = {'variable_code': 'synthetic_other_finding', 'concept_code': 'synthetic:other-finding',
                'value_type': 'categorical', 'normalized_unit': None, 'mapping_config_id': None,
                'accepted_context_types': ['current'], 'categorical_values': [
                    {'value': 'yes', 'encoded': 1, 'assertion_state': 'present'},
                    {'value': 'no', 'encoded': 0, 'assertion_state': 'absent'}]}
            categorical[collision] = payload['variables'][0][collision]
            payload['variables'].append(categorical)
            with self.subTest(collision=collision), self.assertRaisesMessage(
                    ValidationError, '动态变量、项目身份与比较定义需要准确对应'):
                DynamicInputPolicy.model_validate_json(json.dumps(payload))

    def test_trend_condition_still_requires_explicit_threshold_and_has_no_extra_fields(self):
        validator = alert_subschema_validator('#/$defs/TrendCondition')
        for changes in ({'variable_code': ' '}, {'threshold': None}, {'unit': None}, {'autoenable': True}):
            self.assertFalse(validator.is_valid(numeric_condition(**changes)), changes)
        condition = numeric_condition()
        del condition['threshold']
        self.assertFalse(validator.is_valid(condition))

    def test_numeric_policy_and_alert_schema_bytes_are_in_implementation_fingerprint(self):
        config = SimpleNamespace(schema_version='dynamic-input-policy-2')
        before = actual_artifact_checksum(config)
        root = Path(__file__).resolve().parents[2]
        original = Path.read_bytes
        for relative in ('backend/alerting/policies.py', 'alert-design/policy.schema.json'):
            target = root / relative
            def changed_bytes(path):
                data = original(path)
                return data + b'\nSYNTHETIC-CHANGED-IMPLEMENTATION' if path == target else data
            with self.subTest(path=relative), patch.object(Path, 'read_bytes', changed_bytes):
                self.assertNotEqual(actual_artifact_checksum(config), before)
        self.assertEqual(actual_artifact_checksum(config), before)


class DynamicNumericComputationTest(SimpleTestCase):
    def setUp(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.samples = []
        for index, value in enumerate(('10', '12', '14')):
            point = now + timedelta(minutes=index)
            self.samples.append(QualifiedFact({'fact_version_id': str(uuid4()), 'field_id': str(uuid4()),
                'qualifier': 'eq', 'method': 'synthetic', 'normalized_unit': UNIT,
                'medical_precision': 'second'}, MedicalInterval(point, point, True), Decimal(value), ()))
        self.relations = [SamplingRelation({}, left.reference['fact_version_id'], right.reference['fact_version_id'],
            'independent_sampling', ()) for index, left in enumerate(self.samples) for right in self.samples[index + 1:]]
        self.comparison = DynamicInputPolicy.model_validate_json(json.dumps(numeric_policy())).comparisons[0]

    def compute(self, condition=None, samples=None, relations=None):
        samples = self.samples if samples is None else samples
        return trend_signal(condition or numeric_condition(), samples, samples[0] if samples else None,
            samples[-1] if samples else None, self.comparison, {'minimum_separation_seconds': 1},
            self.relations if relations is None else relations, 30)

    def test_current_engine_computes_approved_custom_numeric_adjacent_and_relative_series(self):
        absolute = self.compute()
        self.assertEqual(absolute['state'], 'met')
        self.assertEqual([row['absolute_delta'] for row in absolute['pair_results']], ['2', '2'])
        condition = numeric_condition(metric='relative_delta', unit='%', threshold='15')
        relative = self.compute(condition)
        self.assertEqual(relative['state'], 'met')
        self.assertEqual([row['relative_delta'] for row in relative['pair_results']], ['20.00', '16.67'])
        condition['series']['comparison_scope'] = 'baseline_to_current'
        self.assertEqual(self.compute(condition)['baseline_current_result']['relative_delta'], '40.00')

    def test_missing_samples_are_unknown_without_numeric_defaults(self):
        self.assertEqual(self.compute(samples=[])['state'], 'unknown')
        condition = numeric_condition()
        condition['series']['sample_count'] = 4
        result = self.compute(condition)
        self.assertEqual(result['state'], 'unknown')
        self.assertIn('DYNAMIC_SERIES_SAMPLE_COUNT_INSUFFICIENT', result['reason_codes'])

    def test_unverified_or_nonexact_values_cannot_be_computed(self):
        for changes in ({'reasons': ('FIELD_VERIFICATION_REQUIRED',)},
                {'reference': {**self.samples[0].reference, 'qualifier': 'lt'}}):
            samples = [replace(self.samples[0], **changes), *self.samples[1:]]
            with self.subTest(changes=changes), self.assertRaisesMessage(QualificationRejected, 'DYNAMIC_QUALIFIED_INPUT_INCOMPLETE'):
                self.compute(samples=samples)

    def test_unit_method_and_independent_sampling_remain_required(self):
        for attribute, value, reason in (('method', 'other', 'DYNAMIC_METHOD_NOT_COMPARABLE'),
                ('normalized_unit', 'other', 'DYNAMIC_UNIT_NOT_COMPARABLE')):
            samples = [replace(self.samples[0], reference={**self.samples[0].reference, attribute: value}), *self.samples[1:]]
            result = self.compute(samples=samples)
            self.assertEqual(result['state'], 'unknown')
            self.assertIn(reason, result['reason_codes'])
        self.assertIn('DYNAMIC_INDEPENDENT_SAMPLING_REQUIRED', self.compute(relations=[])['reason_codes'])

    def test_threshold_unit_must_match_metric(self):
        for condition in (numeric_condition(unit='wrong'), numeric_condition(metric='relative_delta', unit=UNIT)):
            with self.assertRaisesMessage(QualificationRejected, 'DYNAMIC_THRESHOLD_UNIT_MISMATCH'):
                self.compute(condition)

    def test_zero_baseline_never_creates_relative_change(self):
        samples = [replace(self.samples[0], value=Decimal('0')), *self.samples[1:]]
        result = self.compute(numeric_condition(metric='relative_delta', unit='%', threshold='1'), samples=samples)
        self.assertEqual(result['state'], 'unknown')
        self.assertIn('DYNAMIC_BASELINE_ZERO', result['reason_codes'])
