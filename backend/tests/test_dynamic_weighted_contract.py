"""Weighted aggregation tests use invented software-only weights and thresholds.

These cases establish deterministic software behavior, not clinical parameters,
clinical validation, or permission to enable a patient-facing preset.
"""
from copy import deepcopy
from decimal import localcontext
import json
from itertools import product
from uuid import uuid4

from django.test import SimpleTestCase
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from alerting.computation import combination_result
from alerting.policies import SensitivityPreset, WeightedCombination, weighted_trigger_reason
from archives.inputs import PROTOCOL, REGISTRY
from assessment.qualified_facts import QualificationRejected
from configurations.schemas import alert_subschema_validator, validate_payload
from configurations.service import ConfigRejected


def weighted_combination(**changes):
    return {'schema_version': 'weighted-combination-1', 'operator': 'weighted',
        'members': [{'signal_ref': 'core', 'classification': 'core', 'weight': '2'},
            {'signal_ref': 'general', 'classification': 'general', 'weight': '3'}],
        'denominator': 'all_configured_weights', 'core_gate': {'operator': 'all', 'minimum_met': None},
        'threshold_fraction': '0.6', 'minimum_known_fraction': '1',
        'unknown_policy': 'bounded_no_imputation', 'evidence_refs': ['synthetic-test-only'], **changes}


def sensitivity_preset(**changes):
    return {'schema_version': 'dynamic-sensitivity-preset-1', 'preset_key': 'synthetic-only',
        'display_name': 'SYNTHETIC SOFTWARE PRESET',
        'description': 'Invented test values. No clinical approval.', **changes}


def weighted_trigger(original=None, **changes):
    if original is None:
        uid = str(uuid4())
        scope = {'population_config_id': uid, 'therapy_policy_id': uid, 'evidence_refs': ['synthetic-test-only']}
        original = {'rule_key': 'synthetic-only', 'scope': scope, 'window_policy_id': uid,
            'conditions': [{'signal_id': code, 'kind': 'professional_finding', 'field_code': code,
                'required_state': 'present', 'verification_policy_id': uid, 'evidence_refs': ['synthetic-test-only']}
                for code in ('core', 'general')],
            'publication': {'level': 'attention', 'priority': 1, 'content_config_id': uid,
                'visibility_config_id': uid, 'contact_version_id': uid, 'notification_config_id': uid, 'valid_for_seconds': 1},
            'episode': {'rule_family_key': 'synthetic-only', 'repeat_policy': 'once_until_reset',
                'escalation_bypasses_cooldown': False, 'reset_policy_id': uid, 'retrigger_policy_id': uid},
            'unknown_policy': 'no_event_from_unknown', 'replay_policy': 'no_publication',
            'evidence_refs': ['synthetic-test-only']}
    return {**deepcopy(original), 'schema_version': 'alert-trigger-2',
        'sensitivity_preset': sensitivity_preset(), 'combination': weighted_combination(), **changes}


class WeightedDynamicContractTest(SimpleTestCase):
    def test_valid_contract_and_registered_v2_schema(self):
        value = weighted_trigger()
        self.assertEqual(WeightedCombination.model_validate(value['combination']).model_dump(), value['combination'])
        self.assertEqual(SensitivityPreset.model_validate(value['sensitivity_preset']).model_dump(), value['sensitivity_preset'])
        self.assertEqual(validate_payload('rule', 'alert-trigger-2', value), value)
        self.assertTrue(alert_subschema_validator('#/$defs/TriggerPolicyV2').is_valid(value))

    def test_no_implicit_weights_gates_denominator_thresholds_or_names(self):
        for section, model in ((weighted_combination(), WeightedCombination), (sensitivity_preset(), SensitivityPreset)):
            for key in section:
                value = deepcopy(section)
                del value[key]
                with self.subTest(missing=key), self.assertRaises(ValidationError):
                    model.model_validate(value)
        value = weighted_combination()
        del value['members'][0]['weight']
        with self.assertRaises(ValidationError):
            WeightedCombination.model_validate(value)

    def test_weights_fractions_and_classes_are_strict_and_positive(self):
        for weight in ('0', '-1', 'NaN', 'Infinity', '1e2', '', ' 1', 1, 1.0, True):
            value = weighted_combination()
            value['members'][0]['weight'] = weight
            with self.subTest(weight=weight), self.assertRaises(ValidationError):
                WeightedCombination.model_validate(value)
        for key, bad in product(('threshold_fraction', 'minimum_known_fraction'), ('0', '1.01', '-1', True, 0.6)):
            with self.subTest(key=key, bad=bad), self.assertRaises(ValidationError):
                WeightedCombination.model_validate(weighted_combination(**{key: bad}))
        value = weighted_combination()
        value['members'][0]['classification'] = 'important'
        with self.assertRaises(ValidationError):
            WeightedCombination.model_validate(value)

    def test_duplicate_members_no_core_unknown_fields_and_bad_gates_rejected(self):
        original = weighted_combination()
        bad = [dict(original, members=[original['members'][0]] * 2),
            dict(original, members=[original['members'][1]]), dict(original, guardian_override=True)]
        bad += [weighted_combination(core_gate=gate) for gate in (
            {'operator': 'all', 'minimum_met': 1}, {'operator': 'at_least', 'minimum_met': None},
            {'operator': 'at_least', 'minimum_met': 0}, {'operator': 'at_least', 'minimum_met': 2})]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                WeightedCombination.model_validate(value)

    def test_weighted_members_cover_every_condition_exactly_once(self):
        for scope in ('missing', 'extra', 'duplicate'):
            value = weighted_trigger()
            if scope == 'missing':
                value['conditions'].append({**value['conditions'][0], 'signal_id': 'third'})
            elif scope == 'extra':
                value['combination']['members'][1]['signal_ref'] = 'other'
            else:
                value['conditions'].append(deepcopy(value['conditions'][0]))
            self.assertIsNotNone(weighted_trigger_reason(value))
            with self.subTest(scope=scope), self.assertRaises(ConfigRejected):
                validate_payload('rule', 'alert-trigger-2', value)

    def test_v1_stays_valid_and_cannot_silently_adopt_v2_fields(self):
        value = weighted_trigger()
        value.pop('sensitivity_preset')
        value.update(schema_version='alert-trigger-1', combination={'operator': 'all', 'signal_refs': ['core', 'general']})
        self.assertEqual(validate_payload('rule', 'alert-trigger-1', value), value)
        for key, change in (('sensitivity_preset', sensitivity_preset()), ('combination', weighted_combination())):
            with self.subTest(key=key), self.assertRaises(ConfigRejected):
                validate_payload('rule', 'alert-trigger-1', {**value, key: change})


class WeightedDynamicComputationTest(SimpleTestCase):
    def compute(self, states=('met', 'met'), policy=None, **limits):
        policy = policy or weighted_combination()
        signals = {member['signal_ref']: {'state': state} for member, state in zip(policy['members'], states)}
        result = combination_result(policy, signals, **{'maximum_depth': 4, 'maximum_operands': 10,
            'maximum_digits': 30, **limits})
        validator = Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/DynamicCombination'}, registry=REGISTRY)
        self.assertTrue(validator.is_valid(result), list(validator.iter_errors(result)))
        return result, result['groups'][0]['weighted']

    def test_all_known_exact_equality_is_met_and_explicit_classification_preserved(self):
        value = weighted_combination(members=[{'signal_ref': 'core', 'classification': 'core', 'weight': '3'},
            {'signal_ref': 'general', 'classification': 'general', 'weight': '2'}])
        result, detail = self.compute(('met', 'not_met'), value)
        self.assertEqual(result['state'], 'met')
        self.assertEqual(detail['lower_fraction'], {'numerator': '3', 'denominator': '5'})
        self.assertEqual(detail['lower_fraction'], detail['upper_fraction'])
        self.assertEqual(detail['denominator_weight'], '5')
        self.assertEqual([row['classification'] for row in detail['members']], ['core', 'general'])

    def test_all_nine_state_pairs_match_three_valued_fixed_denominator_contract(self):
        expected = {('met', 'met'): 'met', ('met', 'not_met'): 'not_met', ('met', 'unknown'): 'unknown',
            ('not_met', 'met'): 'not_met', ('not_met', 'not_met'): 'not_met', ('not_met', 'unknown'): 'not_met',
            ('unknown', 'met'): 'unknown', ('unknown', 'not_met'): 'not_met', ('unknown', 'unknown'): 'unknown'}
        for states, state in expected.items():
            with self.subTest(states=states):
                self.assertEqual(self.compute(states)[0]['state'], state)

    def test_unknown_general_stays_in_denominator_without_imputing_a_fact(self):
        result, detail = self.compute(('met', 'unknown'))
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual((detail['denominator_weight'], detail['met_weight'], detail['known_weight'], detail['unknown_weight']), ('5', '2', '2', '3'))
        self.assertEqual(detail['lower_fraction'], {'numerator': '2', 'denominator': '5'})
        self.assertEqual(detail['upper_fraction'], {'numerator': '1', 'denominator': '1'})
        self.assertEqual(detail['members'][1]['state'], 'unknown')

    def test_unknown_core_does_not_satisfy_gate_even_when_lower_bound_meets_threshold(self):
        result, detail = self.compute(('unknown', 'met'), weighted_combination(minimum_known_fraction='0.5'))
        self.assertEqual((detail['threshold_state'], detail['coverage_state'], detail['core_state']), ('met', 'met', 'unknown'))
        self.assertEqual(result['state'], 'unknown')

    def test_approved_coverage_can_allow_unknown_general_with_sufficient_met_lower_bound(self):
        policy = weighted_combination(threshold_fraction='0.4', minimum_known_fraction='0.4')
        result, detail = self.compute(('met', 'unknown'), policy)
        self.assertEqual(result['state'], 'met')
        self.assertEqual(detail['members'][1]['state'], 'unknown')
        result, detail = self.compute(('met', 'unknown'), {**policy, 'minimum_known_fraction': '0.4001'})
        self.assertEqual(result['state'], 'unknown')
        self.assertIn('DYNAMIC_WEIGHTED_KNOWN_WEIGHT_INSUFFICIENT', detail['reason_codes'])

    def test_core_at_least_uses_known_met_count_and_possible_upper_bound(self):
        members = [{'signal_ref': str(i), 'classification': 'core', 'weight': '1'} for i in range(3)]
        policy = weighted_combination(members=members, core_gate={'operator': 'at_least', 'minimum_met': 2},
            minimum_known_fraction='0.5', threshold_fraction='0.5')
        for states, expected in ((('met', 'met', 'unknown'), 'met'), (('met', 'unknown', 'not_met'), 'unknown'),
                (('met', 'not_met', 'not_met'), 'not_met')):
            with self.subTest(states=states):
                self.assertEqual(self.compute(states, policy)[0]['state'], expected)

    def test_fraction_comparison_is_exact_and_independent_of_decimal_context(self):
        policy = weighted_combination(members=[{'signal_ref': 'core', 'classification': 'core', 'weight': '1'},
            {'signal_ref': 'general', 'classification': 'general', 'weight': '2'}],
            threshold_fraction='0.333333333333333333333333333')
        with localcontext() as context:
            context.prec = 2
            result, detail = self.compute(('met', 'not_met'), policy)
        self.assertEqual(result['state'], 'met')
        self.assertEqual(detail['lower_fraction'], {'numerator': '1', 'denominator': '3'})
        policy['threshold_fraction'] = '0.333333333333333333333333334'
        self.assertEqual(self.compute(('met', 'not_met'), policy)[0]['state'], 'not_met')

    def test_decimal_weights_have_exact_sum_and_ratio(self):
        policy = weighted_combination(members=[{'signal_ref': 'core', 'classification': 'core', 'weight': '0.1'},
            {'signal_ref': 'general', 'classification': 'general', 'weight': '0.2'}])
        _, detail = self.compute(policy=policy)
        self.assertEqual(detail['denominator_weight'], '0.3')
        self.assertEqual(detail['lower_fraction'], {'numerator': '1', 'denominator': '1'})

    def test_bounded_runtime_limits_invalid_states_and_bad_scope_are_fail_closed(self):
        for limits in ({'maximum_operands': 1}, {'maximum_depth': 0}, {'maximum_digits': None}, {'maximum_digits': 1}):
            with self.subTest(limits=limits), self.assertRaises(QualificationRejected):
                self.compute(**limits)
        with self.assertRaisesMessage(QualificationRejected, 'DYNAMIC_SIGNAL_STATE_INVALID'):
            self.compute(('normal', 'met'))
        for signals in ({'core': {'state': 'met'}}, {'core': {'state': 'met'}, 'general': {'state': 'met'}, 'extra': {'state': 'met'}}):
            with self.assertRaisesMessage(QualificationRejected, 'DYNAMIC_WEIGHTED_SIGNAL_SCOPE_INVALID'):
                combination_result(weighted_combination(), signals, maximum_depth=4, maximum_operands=10, maximum_digits=30)

    def test_legacy_nested_combination_output_is_unchanged(self):
        group = {'operator': 'all', 'signal_refs': ['a'], 'groups': [{'operator': 'any', 'signal_refs': ['b', 'c']}]}
        result = combination_result(group, {'a': {'state': 'met'}, 'b': {'state': 'not_met'}, 'c': {'state': 'unknown'}}, maximum_depth=4, maximum_operands=10)
        self.assertEqual(result, {'state': 'unknown', 'groups': [
            {'path': [], 'operator': 'all', 'state': 'unknown', 'operands': ['met', 'unknown'], 'signal_refs': ['a'], 'minimum_met': None},
            {'path': [0], 'operator': 'any', 'state': 'unknown', 'operands': ['not_met', 'unknown'], 'signal_refs': ['b', 'c'], 'minimum_met': None}]})


class OptionalHlhDynamicSignalTest(SimpleTestCase):
    def test_final_hlh_reference_result_is_mapped_without_imputing_missing_or_inapplicable(self):
        from alerting.inputs import static_output_state
        for state, expected in (('criteria_supported', 'met'), ('criteria_not_supported', 'not_met'),
                ('insufficient', 'unknown'), ('not_applicable', 'unknown'), ('unrecognized', 'unknown')):
            with self.subTest(state=state):
                self.assertEqual(static_output_state('hlh_reference_met', {'hlh_result': {
                    'reference_result': state, 'molecular_or_criteria': 'met'}}), expected)
        for output in ({}, {'hlh_result': None}):
            self.assertEqual(static_output_state('hlh_reference_met', output), 'unknown')

    def test_optional_hlh_condition_requires_a_rule_identity_and_does_not_alter_other_conditions(self):
        condition = {'signal_id': 'hlh_reference', 'kind': 'hlh_reference_met',
            'assessment_rule_id': str(uuid4()), 'evidence_refs': ['synthetic-test-only']}
        self.assertTrue(alert_subschema_validator('#/$defs/AssessmentCondition').is_valid(condition))
        del condition['assessment_rule_id']
        self.assertFalse(alert_subschema_validator('#/$defs/AssessmentCondition').is_valid(condition))
        self.assertEqual({row['kind'] for row in weighted_trigger()['conditions']}, {'professional_finding'})

    def test_legacy_static_signal_outcomes_keep_existing_contract(self):
        from alerting.inputs import static_output_state
        for value, expected in (('meets', 'met'), ('not_meets', 'not_met'), ('not_applicable', 'not_met'), ('insufficient', 'unknown')):
            self.assertEqual(static_output_state('classification_met', {'classification_result': value}), expected)
        for value, expected in ((True, 'met'), (False, 'not_met'), (None, 'unknown')):
            self.assertEqual(static_output_state('ms_threshold_met', {'ms_threshold_result': value}), expected)


class OptionalHlhScheduledAssessmentTest(SimpleTestCase):
    def test_hlh_requires_input_rule_and_legacy_plans_remain_unchanged_when_dumping_for_tasks(self):
        from alerting.policies import ScheduledAssessment
        fields = {'rule_id': str(uuid4()), 'window_policy_id': str(uuid4()), 'verification_policy_id': str(uuid4())}
        for kind in ('classification', 'ms'):
            value = {'kind': kind, **fields}
            model = ScheduledAssessment.model_validate_json(json.dumps(value))
            self.assertEqual(model.model_dump(mode='json', exclude_none=True), value)
            for extra in (None, str(uuid4())):
                with self.subTest(kind=kind, extra=extra), self.assertRaises(ValidationError):
                    ScheduledAssessment.model_validate_json(json.dumps({**value, 'input_rule_id': extra}))
        for missing in ({}, {'input_rule_id': None}):
            with self.assertRaises(ValidationError):
                ScheduledAssessment.model_validate_json(json.dumps({'kind': 'hlh', **fields, **missing}))
        value = {'kind': 'hlh', **fields, 'input_rule_id': str(uuid4())}
        self.assertEqual(ScheduledAssessment.model_validate_json(json.dumps(value)).model_dump(mode='json'), value)

    def test_hlh_input_rule_must_be_watched_when_configuration_changes_trigger_assessment(self):
        from alerting.policies import DynamicSchedulePolicy
        keys = ('patient_id', 'actor_id', 'policy_id', 'input_rule_id', 'window_policy_id', 'verification_policy_id', 'use_authorization_id')
        ids = {key: str(uuid4()) for key in keys}
        plan = {'kind': 'hlh', **{key: str(uuid4()) for key in ('rule_id', 'input_rule_id', 'window_policy_id', 'verification_policy_id')}}
        watched = [ids[key] for key in ('policy_id', 'input_rule_id', 'window_policy_id', 'verification_policy_id')]
        watched += [plan[key] for key in ('rule_id', 'window_policy_id', 'verification_policy_id')]
        value = {'schema_version': 'dynamic-schedule-2', 'environment': 'synthetic', 'purpose': 'software_test', **ids,
            'observation_period_id': None, 'static_assessments': [plan], 'event_types': ['configuration.changed'],
            'watch_config_ids': watched, 'interval_seconds': None, 'starts_at': None,
            'trigger_deadline_seconds': 30, 'evidence_refs': ['synthetic-test-only']}
        with self.assertRaises(ValidationError):
            DynamicSchedulePolicy.model_validate_json(json.dumps(value))
        value['watch_config_ids'].append(plan['input_rule_id'])
        self.assertEqual(DynamicSchedulePolicy.model_validate_json(json.dumps(value)).static_assessments[0].kind, 'hlh')
