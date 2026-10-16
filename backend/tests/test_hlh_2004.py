"""Synthetic offline reference-contract tests, not clinical validation or approval.

Source-native threshold numbers are literature facts; all persons, evidence
identities, windows, binding concepts and test measurements below are synthetic.
Run: PYTHONPATH=backend python -m unittest tests.test_hlh_2004
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import product
import json
from pathlib import Path
from unittest import TestCase
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from assessment.hlh import compare_hlh_threshold, evaluate_hlh_2004
from assessment.hlh_contracts import (
    FACT_CODES, HLHInputPolicy, HLHInputs, HLHMedicalInterval, HLHResult,
    HLHRulePolicy, hlh_variable, validate_hlh_policy_pair,
)


NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def synthetic_policy():
    def threshold(operator, value, unit):
        return {'operator': operator, 'value': value, 'unit': unit}
    return {'schema_version': 'hlh-2004-rule-1', 'kind': 'hlh_2004_reference',
        'source_table': 'histiocyte-society-2004-table-2', 'source_revision_id': str(uuid4()),
        'evidence_refs': ['synthetic:source-table-reference-not-approval'], 'policy_revision_id': str(uuid4()),
        'semantics_version': 'hlh-2004-bounds-1', 'count_bounds_policy': 'met_lower_met_plus_unknown_upper',
        'unknown_logic': 'strong_kleene_compounds', 'applicability_precedence': 'not_applicable_then_unknown_then_paths',
        'semantics_evidence_refs': ['synthetic:software-policy-not-clinical-approval'],
        'applicability_policy_ref': 'synthetic:applicability', 'clinical_assertion_policy_ref': 'synthetic:professional-assertions',
        'infant_age_days_exclusive': 28, 'cytopenia_lineages_required': 2, 'criteria_required': 5, 'criteria_total': 8,
        'molecular_path': 'professionally_confirmed_consistent_diagnosis',
        'fever_definition': 'professionally_confirmed_source_table_fever',
        'malignancy_qualifier': 'required_for_hemophagocytosis_only',
        'unit_policy': 'exact_native_unit_no_implicit_conversion',
        'thresholds': {'hemoglobin': threshold('lt', '90', 'g/L'), 'infant_hemoglobin': threshold('lt', '100', 'g/L'),
            'platelet_count': threshold('lt', '100', '10^9/L'), 'neutrophil_count': threshold('lt', '1.0', '10^9/L'),
            'triglycerides': threshold('ge', '3.0', 'mmol/L'), 'fibrinogen': threshold('le', '1.5', 'g/L'),
            'ferritin': threshold('ge', '500', 'ug/L'), 'soluble_cd25': threshold('ge', '2400', 'U/mL')},
        'temporal_policy': {'maximum_age_seconds': {code: 86400 for code in FACT_CODES},
            'maximum_cross_laboratory_span_seconds': 3600, 'require_available_and_verified_by_known_at': True,
            'cross_laboratory_conflict': 'all_numeric_criteria_unknown',
            'interval_policy': 'whole_interval_within_inclusive_window'}}


def synthetic_proof():
    return {'fact_version_id': str(uuid4()), 'source_revision_id': str(uuid4()),
        'verification_id': str(uuid4()), 'verified_by': str(uuid4()), 'evidence_ref': 'synthetic:test-only-proof',
        'medical_interval': {'lower': NOW.isoformat(), 'upper': NOW.isoformat(), 'upper_inclusive': True, 'precision': 'second'},
        'available_at': NOW.isoformat(), 'verified_at': NOW.isoformat(), 'valid_until': None, 'sample_id': None}


def synthetic_assertion(value):
    return {'qualification': 'qualified', 'proof': synthetic_proof(), 'value': value}


def synthetic_numeric(value, unit):
    return {'qualification': 'qualified', 'proof': synthetic_proof(), 'value': value, 'qualifier': 'eq', 'unit': unit}


def synthetic_inputs():
    value = {'schema_version': 'hlh-2004-inputs-1', 'assessment_at': NOW.isoformat(), 'known_at': NOW.isoformat(),
        'applicability': synthetic_assertion(True),
        'molecular': {**synthetic_assertion(False), 'assertion_type': 'molecular_diagnosis_consistent_with_hlh'},
        'fever': synthetic_assertion(False), 'splenomegaly': synthetic_assertion(False),
        'hemoglobin': synthetic_numeric('110', 'g/L'), 'platelet_count': synthetic_numeric('110', '10^9/L'),
        'neutrophil_count': synthetic_numeric('1.1', '10^9/L'), 'triglycerides': synthetic_numeric('2.9', 'mmol/L'),
        'triglycerides_fasting': synthetic_assertion(True), 'fibrinogen': synthetic_numeric('1.6', 'g/L'),
        'hemophagocytosis': {**synthetic_assertion(False), 'specimen_site': 'bone_marrow'},
        'no_malignancy_evidence': synthetic_assertion(True),
        'nk_activity': {**synthetic_assertion(False), 'laboratory_reference': 'synthetic local lab reference'},
        'ferritin': synthetic_numeric('499', 'ug/L'), 'soluble_cd25': synthetic_numeric('2399', 'U/mL'),
        'age_at_hemoglobin': {'qualification': 'qualified', 'proof': synthetic_proof(), 'age_days': 28,
            'hemoglobin_fact_version_id': None}}
    value['age_at_hemoglobin']['hemoglobin_fact_version_id'] = value['hemoglobin']['proof']['fact_version_id']
    sample = str(uuid4())
    value['triglycerides']['proof']['sample_id'] = sample
    value['triglycerides_fasting']['proof']['sample_id'] = sample
    return value


def synthetic_bindings():
    units = {'hemoglobin': 'g/L', 'platelet_count': '10^9/L', 'neutrophil_count': '10^9/L',
        'triglycerides': 'mmol/L', 'fibrinogen': 'g/L', 'ferritin': 'ug/L', 'soluble_cd25': 'U/mL',
        'age_at_hemoglobin': 'day'}
    variables = []
    for code in FACT_CODES:
        variables.append({'variable_code': code, 'concept_code': f'synthetic:hlh:{code}',
            'value_type': 'decimal' if code in units else 'categorical', 'normalized_unit': units.get(code),
            'mapping_config_id': str(uuid4()) if code in units else None,
            'categorical_values': [] if code in units else [{'value': str(value), 'encoded': value,
                'assertion_state': 'present' if value else 'absent',
                'specimen_site': 'bone_marrow' if code == 'hemophagocytosis' else None} for value in (0, 1)],
            'accepted_context_types': ['current'], 'accepted_methods': ['synthetic-method'] if code in units else [None]})
    return {'schema_version': 'hlh-input-policy-1', 'purpose': 'software_test', 'variables': variables,
        'metadata_bindings': {'age_sample_binding': 'same_record_version_and_exact_medical_interval',
            'fasting_sample_binding': 'same_report_and_exact_sample_interval',
            'nk_laboratory_reference': 'verified_record_field_reference_interval',
            'hemophagocytosis_specimen': 'approved_categorical_value_mapping',
            'molecular_assertion': 'qualified_concept_not_raw_variant_or_family_history'},
        'evidence_refs': ['synthetic:binding-evidence-not-approval']}


def unknown(observation, reason='missing'):
    observation['qualification'] = reason
    observation['proof'] = None
    if 'value' in observation:
        observation['value'] = None


def parse(model, data):
    return model.model_validate_json(json.dumps(data))


def evaluate(inputs=None, policy=None):
    return evaluate_hlh_2004(parse(HLHRulePolicy, policy or synthetic_policy()), parse(HLHInputs, inputs or synthetic_inputs()))


def condition(result, code):
    return next(item for item in result.criteria if item.code == code)


class HLHThresholdTest(TestCase):
    def test_source_equalities_and_neighbor_values(self):
        for code, criterion, threshold in (
            ('platelet_count', 'cytopenias', Decimal('100')), ('neutrophil_count', 'cytopenias', Decimal('1')),
            ('hemoglobin', 'cytopenias', Decimal('90')), ('triglycerides', 'lipids_or_fibrinogen', Decimal('3')),
            ('fibrinogen', 'lipids_or_fibrinogen', Decimal('1.5')), ('ferritin', 'ferritin', Decimal('500')),
            ('soluble_cd25', 'soluble_cd25', Decimal('2400')),
        ):
            rule = synthetic_policy()['thresholds'][code]
            expected = {'lt': ['met', 'not_met', 'not_met'], 'le': ['met', 'met', 'not_met'],
                'ge': ['not_met', 'met', 'met']}[rule['operator']]
            for delta, state in zip((Decimal('-0.01'), Decimal('0'), Decimal('0.01')), expected):
                inputs = synthetic_inputs(); inputs[code]['value'] = str(threshold + delta)
                result = condition(evaluate(inputs), criterion)
                actual = result.state if code == criterion else result.subconditions[
                    'triglycerides_numeric' if code == 'triglycerides' else code].state
                self.assertEqual(actual, state, (code, delta))

    def test_all_comparators_censored_bounds_against_interval_oracle(self):
        predicates = {'lt': lambda x: x < 10, 'le': lambda x: x <= 10,
            'gt': lambda x: x > 10, 'ge': lambda x: x >= 10}
        grid = [Decimal(x) / 10 for x in range(-1000, 2001)]
        for qualifier, operator, value in product(('eq', 'lt', 'le', 'gt', 'ge'), predicates, (Decimal('9'), Decimal('10'), Decimal('11'))):
            predicate = {'eq': lambda x: x == value, 'lt': lambda x: x < value,
                'le': lambda x: x <= value, 'gt': lambda x: x > value, 'ge': lambda x: x >= value}[qualifier]
            truth = {predicates[operator](x) for x in grid if predicate(x)}
            expected = 'met' if truth == {True} else 'not_met' if truth == {False} else 'unknown'
            self.assertEqual(compare_hlh_threshold(value, qualifier, operator, Decimal('10')), expected,
                (qualifier, operator, value))

    def test_nonfinite_and_untyped_comparison_rejected(self):
        for value in ('10', Decimal('NaN'), Decimal('Infinity')):
            with self.assertRaises(ValueError):
                compare_hlh_threshold(value, 'eq', 'ge', Decimal('10'))

    def test_native_tg_alternative_and_no_implicit_unit_conversions(self):
        policy = synthetic_policy(); policy['thresholds']['triglycerides'].update(value='265', unit='mg/dL')
        inputs = synthetic_inputs(); inputs['triglycerides'].update(value='265', unit='mg/dL')
        self.assertEqual(condition(evaluate(inputs, policy), 'lipids_or_fibrinogen').state, 'met')
        inputs['triglycerides'].update(value='3', unit='mmol/L')
        self.assertEqual(condition(evaluate(inputs, policy), 'lipids_or_fibrinogen').state, 'unknown')
        for code, unit in (('ferritin', 'ng/mL'), ('fibrinogen', 'mg/dL'), ('soluble_cd25', 'pg/mL')):
            inputs = synthetic_inputs(); inputs[code]['unit'] = unit
            result = evaluate(inputs)
            criterion = condition(result, 'lipids_or_fibrinogen' if code == 'fibrinogen' else code)
            self.assertEqual(criterion.state, 'unknown')
            self.assertIn('HLH_NATIVE_UNIT_MISMATCH', criterion.reason_codes)


class HLHCompoundTest(TestCase):
    def test_all_eight_unknown_and_unknown_molecular_do_not_become_negative(self):
        inputs = synthetic_inputs()
        for code in FACT_CODES:
            if code != 'applicability': unknown(inputs[code])
        result = evaluate(inputs)
        self.assertEqual((result.criteria_met_lower_bound, result.criteria_met_upper_bound), (0, 8))
        self.assertEqual(result.reference_result, 'insufficient')

    def test_all_eight_known_negative_requires_known_molecular_negative(self):
        inputs = synthetic_inputs(); result = evaluate(inputs)
        self.assertEqual((result.criteria_met_lower_bound, result.criteria_met_upper_bound), (0, 0))
        self.assertEqual(result.reference_result, 'criteria_not_supported')
        unknown(inputs['molecular'])
        self.assertEqual(evaluate(inputs).reference_result, 'insufficient')

    def test_five_known_criteria_suffice_with_other_unknowns(self):
        inputs = synthetic_inputs()
        for code in ('fever', 'splenomegaly', 'nk_activity'): inputs[code]['value'] = True
        inputs['ferritin']['value'] = '500'; inputs['soluble_cd25']['value'] = '2400'
        for code in ('hemoglobin', 'platelet_count', 'neutrophil_count', 'hemophagocytosis',
                'triglycerides', 'fibrinogen', 'molecular'): unknown(inputs[code])
        result = evaluate(inputs)
        self.assertEqual((result.criteria_met_lower_bound, result.criteria_met_upper_bound), (5, 8))
        self.assertEqual(result.reference_result, 'criteria_supported')
        self.assertEqual(result.supported_paths, ['five_of_eight'])

    def test_four_met_one_unknown_is_insufficient_and_four_met_no_unknown_not_supported(self):
        inputs = synthetic_inputs()
        for code in ('fever', 'splenomegaly', 'nk_activity'): inputs[code]['value'] = True
        inputs['ferritin']['value'] = '500'; unknown(inputs['soluble_cd25'])
        result = evaluate(inputs)
        self.assertEqual((result.criteria_met_lower_bound, result.criteria_met_upper_bound), (4, 5))
        self.assertEqual(result.reference_result, 'insufficient')
        inputs['soluble_cd25'] = synthetic_numeric('2300', 'U/mL')
        self.assertEqual(evaluate(inputs).reference_result, 'criteria_not_supported')

    def test_molecular_path_is_independent_of_five_of_eight(self):
        inputs = synthetic_inputs()
        for code in FACT_CODES:
            if code not in ('applicability', 'molecular'): unknown(inputs[code])
        inputs['molecular']['value'] = True
        result = evaluate(inputs)
        self.assertEqual(result.criteria_path, 'unknown')
        self.assertEqual(result.reference_result, 'criteria_supported')
        self.assertEqual(result.supported_paths, ['molecular'])

    def test_applicability_precedes_both_paths(self):
        inputs = synthetic_inputs(); inputs['molecular']['value'] = True
        inputs['applicability']['value'] = False
        result = evaluate(inputs)
        self.assertEqual(result.reference_result, 'not_applicable')
        self.assertEqual(result.supported_paths, [])
        unknown(inputs['applicability'])
        self.assertEqual(evaluate(inputs).reference_result, 'insufficient')

    def test_cytopenia_exhaustive_three_valued_two_of_three(self):
        for states in product(('met', 'not_met', 'unknown'), repeat=3):
            inputs = synthetic_inputs()
            for code, state, low in zip(('hemoglobin', 'platelet_count', 'neutrophil_count'), states, ('89', '99', '0.9')):
                if state == 'met': inputs[code]['value'] = low
                elif state == 'unknown': unknown(inputs[code])
            result = condition(evaluate(inputs), 'cytopenias')
            lower = states.count('met'); upper = 3 - states.count('not_met')
            self.assertEqual(result.state, 'met' if lower >= 2 else 'not_met' if upper < 2 else 'unknown', states)

    def test_lipid_or_exhaustive_three_valued_logic_and_counts_once(self):
        for states in product(('met', 'not_met', 'unknown'), repeat=2):
            inputs = synthetic_inputs()
            for code, state, met_value in zip(('triglycerides', 'fibrinogen'), states, ('3', '1.5')):
                if state == 'met': inputs[code]['value'] = met_value
                elif state == 'unknown': unknown(inputs[code])
            result = evaluate(inputs); criterion = condition(result, 'lipids_or_fibrinogen')
            expected = 'met' if 'met' in states else 'unknown' if 'unknown' in states else 'not_met'
            self.assertEqual(criterion.state, expected, states)
            self.assertEqual(result.criteria_met_lower_bound, int(expected == 'met'))

    def test_nonfasting_and_unknown_fasting_cannot_establish_negative_tg(self):
        for fasting in (False, None):
            inputs = synthetic_inputs()
            if fasting is None: unknown(inputs['triglycerides_fasting'])
            else: inputs['triglycerides_fasting']['value'] = fasting
            self.assertEqual(condition(evaluate(inputs), 'lipids_or_fibrinogen').state, 'unknown')
            inputs['fibrinogen']['value'] = '1.5'
            self.assertEqual(condition(evaluate(inputs), 'lipids_or_fibrinogen').state, 'met')

    def test_no_malignancy_qualifier_affects_histology_only(self):
        inputs = synthetic_inputs(); inputs['hemophagocytosis']['value'] = True
        inputs['no_malignancy_evidence']['value'] = False; inputs['molecular']['value'] = True
        result = evaluate(inputs)
        self.assertEqual(condition(result, 'hemophagocytosis').state, 'not_met')
        self.assertEqual(result.reference_result, 'criteria_supported')
        unknown(inputs['no_malignancy_evidence'])
        self.assertEqual(condition(evaluate(inputs), 'hemophagocytosis').state, 'unknown')


class HLHProvenanceTest(TestCase):
    def test_age_boundary_27_and_28_and_unknown_age(self):
        for age, expected in ((27, 'met'), (28, 'not_met')):
            inputs = synthetic_inputs(); inputs['hemoglobin']['value'] = '95'; inputs['age_at_hemoglobin']['age_days'] = age
            result = condition(evaluate(inputs), 'cytopenias').subconditions['hemoglobin']
            self.assertEqual(result.state, expected)
        inputs = synthetic_inputs(); inputs['hemoglobin']['value'] = '80'; unknown(inputs['age_at_hemoglobin'])
        self.assertEqual(condition(evaluate(inputs), 'cytopenias').subconditions['hemoglobin'].state, 'unknown')
        inputs['platelet_count']['value'] = '90'; inputs['neutrophil_count']['value'] = '0.5'
        self.assertEqual(condition(evaluate(inputs), 'cytopenias').state, 'met')

    def test_missing_or_mismatched_metadata_fails_unknown(self):
        checks = [
            ('age_at_hemoglobin', 'hemoglobin_fact_version_id', str(uuid4()), 'cytopenias', 'hemoglobin'),
            ('hemophagocytosis', 'specimen_site', 'other', 'hemophagocytosis', None),
            ('nk_activity', 'laboratory_reference', None, 'nk_activity', None),
        ]
        for code, field, value, criterion, sub in checks:
            inputs = synthetic_inputs(); inputs[code][field] = value
            if code == 'hemophagocytosis': inputs[code]['value'] = True
            result = condition(evaluate(inputs), criterion)
            self.assertEqual(result.subconditions[sub].state if sub else result.state, 'unknown', code)
        inputs = synthetic_inputs(); inputs['triglycerides_fasting']['proof']['sample_id'] = str(uuid4())
        self.assertEqual(condition(evaluate(inputs), 'lipids_or_fibrinogen').state, 'unknown')

    def test_unverified_conflict_and_missing_facts_are_unknown_not_negative(self):
        for reason in ('missing', 'unverified', 'conflict', 'unknown_unit', 'invalid_time', 'invalid_context'):
            inputs = synthetic_inputs(); unknown(inputs['ferritin'], reason)
            result = condition(evaluate(inputs), 'ferritin')
            self.assertEqual(result.state, 'unknown')
            self.assertIn(f'HLH_{reason.upper()}', result.reason_codes)

    def test_future_cutoffs_expired_proof_and_verification_order(self):
        checks = [
            ('available_at', (NOW + timedelta(seconds=1)).isoformat()),
            ('verified_at', (NOW + timedelta(seconds=1)).isoformat()),
            ('valid_until', NOW.isoformat()),
            ('verified_at', (NOW - timedelta(seconds=1)).isoformat()),
        ]
        for key, value in checks:
            inputs = synthetic_inputs(); inputs['ferritin']['proof'][key] = value
            self.assertEqual(condition(evaluate(inputs), 'ferritin').state, 'unknown', key)

    def test_exact_window_inclusive_edges_and_cross_laboratory_conflict(self):
        policy = synthetic_policy(); policy['temporal_policy']['maximum_age_seconds']['ferritin'] = 3600
        inputs = synthetic_inputs()
        for age, state in ((3600, 'not_met'), (3601, 'unknown')):
            instant = NOW - timedelta(seconds=age)
            inputs['ferritin']['proof']['medical_interval'].update(lower=instant.isoformat(), upper=instant.isoformat())
            self.assertEqual(condition(evaluate(inputs, policy), 'ferritin').state, state)
        policy = synthetic_policy(); policy['temporal_policy']['maximum_cross_laboratory_span_seconds'] = 3599
        inputs['ferritin']['proof']['medical_interval'].update(lower=(NOW-timedelta(hours=1)).isoformat(), upper=(NOW-timedelta(hours=1)).isoformat())
        inputs['molecular']['value'] = True
        result = evaluate(inputs, policy)
        self.assertIn('HLH_CROSS_LABORATORY_WINDOW_CONFLICT', result.limitation_codes)
        self.assertEqual(condition(result, 'soluble_cd25').state, 'unknown')
        self.assertEqual(result.reference_result, 'criteria_supported')

    def test_date_intervals_are_not_collapsed_and_partly_future_is_unknown(self):
        inputs = synthetic_inputs()
        inputs['ferritin']['proof']['medical_interval'] = {'lower': '2026-01-01T00:00:00+00:00',
            'upper': '2026-01-02T00:00:00+00:00', 'upper_inclusive': False, 'precision': 'date'}
        self.assertEqual(condition(evaluate(inputs), 'ferritin').state, 'unknown')
        with self.assertRaises(ValidationError):
            parse(HLHMedicalInterval, {'lower': NOW.isoformat(), 'upper': NOW.isoformat(), 'upper_inclusive': True, 'precision': 'date'})

    def test_ambiguous_sample_times_cannot_bind_age_or_fasting(self):
        inputs = synthetic_inputs(); policy = synthetic_policy()
        policy['temporal_policy']['maximum_cross_laboratory_span_seconds'] = 86400
        interval = {'lower': '2025-12-31T12:00:00+00:00', 'upper': NOW.isoformat(), 'upper_inclusive': False, 'precision': 'date'}
        for code in ('hemoglobin', 'age_at_hemoglobin', 'triglycerides', 'triglycerides_fasting'):
            inputs[code]['proof']['medical_interval'] = interval
        result = evaluate(inputs, policy)
        self.assertEqual(condition(result, 'cytopenias').subconditions['hemoglobin'].state, 'unknown')
        self.assertEqual(condition(result, 'lipids_or_fibrinogen').state, 'unknown')

    def test_duplicate_fact_ids_cannot_inflate_criteria(self):
        inputs = synthetic_inputs(); inputs['ferritin']['proof']['fact_version_id'] = inputs['soluble_cd25']['proof']['fact_version_id']
        with self.assertRaises(ValidationError): parse(HLHInputs, inputs)

    def test_every_qualified_fact_requires_typed_proof_and_no_naive_times(self):
        for code in FACT_CODES:
            inputs = synthetic_inputs(); inputs[code]['proof'] = None
            with self.subTest(code=code), self.assertRaises(ValidationError): parse(HLHInputs, inputs)
        inputs = synthetic_inputs(); inputs['known_at'] = '2026-01-01T12:00:00'
        with self.assertRaises(ValidationError): parse(HLHInputs, inputs)


class HLHContractTest(TestCase):
    def test_no_implicit_policy_or_input_defaults(self):
        for model, payload in ((HLHRulePolicy, synthetic_policy()), (HLHInputs, synthetic_inputs()), (HLHInputPolicy, synthetic_bindings())):
            for key in payload:
                value = deepcopy(payload); del value[key]
                with self.subTest(model=model.__name__, missing=key), self.assertRaises(ValidationError): parse(model, value)

    def test_source_threshold_comparators_units_counts_and_unknown_policy_cannot_be_rewritten(self):
        for code in synthetic_policy()['thresholds']:
            for key, value in (('value', '1234'), ('operator', 'gt'), ('unit', 'arbitrary')):
                policy = synthetic_policy(); policy['thresholds'][code][key] = value
                with self.subTest(code=code, key=key), self.assertRaises(ValidationError): parse(HLHRulePolicy, policy)
        for key, value in (('criteria_required', 4), ('criteria_total', 9), ('infant_age_days_exclusive', 30),
                ('cytopenia_lineages_required', 1), ('unknown_logic', 'unknown_is_false'), ('fever_definition', '38.5C_for_week')):
            policy = synthetic_policy(); policy[key] = value
            with self.subTest(key=key), self.assertRaises(ValidationError): parse(HLHRulePolicy, policy)

    def test_family_history_or_raw_variant_cannot_substitute_for_molecular_diagnosis(self):
        for key in ('family_history', 'gene_variant', 'confirmed_by_guardian'):
            inputs = synthetic_inputs(); inputs['molecular'][key] = True
            with self.assertRaises(ValidationError): parse(HLHInputs, inputs)
        inputs = synthetic_inputs(); inputs['molecular']['assertion_type'] = 'pathogenic_variant'
        with self.assertRaises(ValidationError): parse(HLHInputs, inputs)

    def test_invalid_numeric_values_booleans_and_extra_fields_are_rejected(self):
        for value in ('NaN', 'Infinity', '-1', '1e2', '', ' ' , 500, True):
            inputs = synthetic_inputs(); inputs['ferritin']['value'] = value
            with self.subTest(value=value), self.assertRaises(ValidationError): parse(HLHInputs, inputs)
        for field, value in (('qualification', 'assumed'), ('diagnosis', True), ('approved', True)):
            inputs = synthetic_inputs(); inputs['ferritin'][field] = value
            with self.assertRaises(ValidationError): parse(HLHInputs, inputs)

    def test_bypassed_pydantic_construction_is_revalidated_at_evaluation(self):
        policy = parse(HLHRulePolicy, synthetic_policy()).model_copy(update={'criteria_required': 1})
        with self.assertRaises(ValidationError): evaluate_hlh_2004(policy, parse(HLHInputs, synthetic_inputs()))
        with self.assertRaises(TypeError): evaluate_hlh_2004(synthetic_policy(), synthetic_inputs())

    def test_each_fact_requires_its_own_explicit_window(self):
        policy = synthetic_policy(); del policy['temporal_policy']['maximum_age_seconds']['molecular']
        with self.assertRaises(ValidationError): parse(HLHRulePolicy, policy)

    def test_bindings_cover_all_fields_and_keep_native_hlh_units(self):
        binding = parse(HLHInputPolicy, synthetic_bindings())
        rule = parse(HLHRulePolicy, synthetic_policy()); validate_hlh_policy_pair(rule, binding)
        self.assertEqual(hlh_variable(binding, 'ferritin').normalized_unit, 'ug/L')
        for code, key, value in (('ferritin', 'normalized_unit', 'ng/mL'), ('hemoglobin', 'mapping_config_id', None),
                ('hemoglobin', 'accepted_methods', [None]), ('hemophagocytosis', 'categorical_values', [])):
            payload = synthetic_bindings(); item = next(item for item in payload['variables'] if item['variable_code'] == code); item[key] = value
            with self.subTest(code=code, key=key), self.assertRaises(ValidationError): parse(HLHInputPolicy, payload)
        payload = synthetic_bindings(); payload['variables'][1]['concept_code'] = payload['variables'][0]['concept_code']
        with self.assertRaises(ValidationError): parse(HLHInputPolicy, payload)

    def test_policy_pair_rejects_tg_unit_mismatch(self):
        payload = synthetic_bindings(); next(item for item in payload['variables'] if item['variable_code'] == 'triglycerides')['normalized_unit'] = 'mg/dL'
        with self.assertRaises(ValueError): validate_hlh_policy_pair(parse(HLHRulePolicy, synthetic_policy()), parse(HLHInputPolicy, payload))

    def test_exported_schemas_match_models_and_reject_rewritten_source_thresholds(self):
        root = Path(__file__).resolve().parents[2] / 'backend-design'
        for model, filename in ((HLHRulePolicy, 'hlh-2004-rule'), (HLHInputPolicy, 'hlh-input-policy'),
                (HLHInputs, 'hlh-2004-inputs'), (HLHResult, 'hlh-2004-result')):
            saved = json.loads((root / (filename + '.schema.json')).read_text())
            self.assertEqual(saved, {'$schema': 'https://json-schema.org/draft/2020-12/schema', **model.model_json_schema()})
        validator = Draft202012Validator(HLHRulePolicy.model_json_schema(), format_checker=FormatChecker())
        policy = synthetic_policy()
        self.assertTrue(validator.is_valid(policy))
        for key, value in (('operator', 'gt'), ('value', '3.01'), ('unit', 'mg/mL')):
            changed = deepcopy(policy); changed['thresholds']['triglycerides'][key] = value
            self.assertFalse(validator.is_valid(changed))

    def test_result_is_deterministic_traceable_and_not_a_clinical_approval(self):
        inputs = synthetic_inputs(); policy = synthetic_policy(); first = evaluate(inputs, policy); second = evaluate(inputs, policy)
        self.assertEqual(first, second)
        self.assertFalse(first.clinical_use_authorized)
        self.assertEqual(first.use_boundary, 'offline_reference_support_only')
        self.assertIn('HLH_NOT_SUPPORTED_DOES_NOT_EXCLUDE_DISEASE', first.limitation_codes)
        inputs['ferritin']['value'] = '500'
        self.assertNotEqual(first.input_checksum, evaluate(inputs, policy).input_checksum)
        policy['semantics_evidence_refs'] = ['synthetic:changed-policy-reference']
        self.assertNotEqual(first.policy_checksum, evaluate(inputs, policy).policy_checksum)
        validator = Draft202012Validator(HLHResult.model_json_schema(), format_checker=FormatChecker())
        self.assertTrue(validator.is_valid(first.model_dump(mode='json')))
