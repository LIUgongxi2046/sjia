"""Synthetic evaluation arithmetic/protocol tests, not clinical validation."""
from copy import deepcopy

from django.test import SimpleTestCase

from case_details.clinical_metrics import EvaluationInputError, evaluate


def synthetic_capture():
    plan = {'schema_version': 'clinical-measurement-plan-1', 'plan_ref': 'synthetic:plan-v1',
        'matching_policy_ref': 'synthetic:independent-pairings-v1',
        'matching_mode': 'independent_explicit_pairings', 'confidence_interval_protocol_ref': None,
        'subgroup_dimensions': ['treatment', 'age', 'infection', 'completeness'],
        'fixed_before': '2026-01-01T00:00:00Z', 'criteria_refs': ['synthetic:test-criteria']}
    result = {'execution_ref': 'synthetic:execution', 'task_id': 'synthetic:task',
        'rule_config_id': 'rule-one', 'input_sha256': 'a' * 64, 'status': 'completed', 'prompts': []}
    periods = []
    for index, state in enumerate(('positive', 'negative', 'unknown')):
        day = index + 1
        period = {'id': f'period-{index}', 'patient_id': 'patient-a' if index < 2 else 'patient-b',
            'label_id': f'label-{index}', 'label_state': state,
            'reference_answer_ref': f'synthetic:independent-answer-{index}' if state != 'unknown' else None,
            'start': f'2026-01-{day:02}T00:00:00Z', 'end': f'2026-01-{day:02}T02:00:00Z',
            'known_at': f'2026-01-{day:02}T02:00:00Z',
            'subgroups': {'treatment': None, 'age': 'not-clinical', 'infection': None, 'completeness': 'synthetic'},
            'facts': [{'fact_ref': f'fact-{index}', 'source_ref': 'synthetic:source',
                'available_at': f'2026-01-{day:02}T00:00:00Z', 'verified_at': f'2026-01-{day:02}T00:00:00Z', 'used': True}],
            'static': deepcopy(result), 'dynamic': deepcopy(result)}
        for mode in ('static', 'dynamic'):
            period[mode]['task_id'] = f'synthetic:{mode}-task-{index}'
            period[mode]['execution_ref'] = f'synthetic:{mode}-execution-{index}'
        periods.append(period)
    event = {'id': 'event-1', 'patient_id': 'patient-a', 'period_id': 'period-0', 'label_state': 'positive',
        'reference_answer_ref': 'synthetic:independent-event', 'reference_at': '2026-01-01T01:00:00Z',
        'valid_alert_start': '2026-01-01T00:00:00Z', 'valid_alert_end': '2026-01-01T01:59:59Z'}
    prompt = {'id': 'prompt-1', 'at': '2026-01-01T00:50:00Z', 'outcome': 'positive',
        'outcome_ref': 'synthetic:independent-prompt-label', 'matched_event_id': 'event-1', 'occurrence': 'first'}
    periods[0]['dynamic']['prompts'] = [deepcopy(prompt), {**prompt, 'id': 'prompt-2',
        'at': '2026-01-01T01:05:00Z', 'occurrence': 'repeat'}]
    periods[0]['static']['prompts'] = [{**prompt, 'at': '2026-01-01T01:10:00Z'}]
    periods[1]['static']['prompts'] = [{'id': 'negative-prompt', 'at': '2026-01-02T01:00:00Z',
        'outcome': 'negative', 'outcome_ref': 'synthetic:negative-answer', 'matched_event_id': None, 'occurrence': 'first'}]
    capture = {'schema_version': 'clinical-result-capture-1', 'data_mode': 'synthetic', 'mode': 'replay',
        'dataset_version_id': 'dataset-version-arbitrary-17', 'dataset_checksum': 'b' * 64,
        'implementation_sha256': 'c' * 64, 'configuration_checksums': {'rule-one': 'd' * 64},
        'measurement_plan_ref': plan['plan_ref'], 'captured_at': '2026-01-04T00:00:00Z',
        'published_count': 0, 'notified_count': 0, 'current_cycle_mutations': 0,
        'patients': [{'patient_id': 'patient-a', 'partition': 'development'},
            {'patient_id': 'patient-b', 'partition': 'validation'}], 'periods': periods, 'events': [event]}
    return plan, capture


class ClinicalMetricsCloseoutTest(SimpleTestCase):
    def setUp(self):
        self.plan, self.capture = synthetic_capture()

    def test_metrics_use_actual_denominators_not_historical_six_hour_constant(self):
        report = evaluate(self.plan, self.capture)
        self.assertEqual(report['dynamic']['sensitivity'], 1)
        self.assertEqual(report['static']['ppv'], 0.5)
        self.assertEqual(report['dynamic']['prompt_counts'], {'total': 2, 'first': 1, 'repeat': 1})
        self.assertEqual(report['dynamic']['first_alert_difference_seconds_by_event'], {'event-1': -600})
        self.assertEqual(report['static']['first_alert_difference_seconds_by_event'], {'event-1': 600})
        self.assertEqual(report['static']['alerts_per_negative_observation_person_hour'], 0.5)
        self.capture['periods'][2]['end'] = '2026-01-03T03:00:00Z'
        report = evaluate(self.plan, self.capture)
        self.assertEqual(report['dynamic']['all_observation_person_hours'], '7.0')
        self.assertEqual(report['dynamic']['alerts_per_observation_person_hour'], 2 / 7)

    def test_unknown_labels_and_unassessable_results_are_not_negatives(self):
        self.capture['periods'][0]['static'].update(status='insufficient', prompts=[])
        result = evaluate(self.plan, self.capture)
        self.assertIsNone(result['static']['sensitivity'])
        self.assertEqual(result['static']['missed_event_ids'], [])
        self.assertEqual(result['static']['unassessable_positive_event_ids'], ['event-1'])
        self.assertEqual(result['denominators']['unknown_labels_excluded_from_negative_denominator'], 1)
        self.assertEqual(result['static']['event_unassessable_fraction'], 1)

    def test_no_clinical_validation_or_confidence_interval_is_invented(self):
        for mode in ('synthetic', 'clinical'):
            self.capture['data_mode'] = mode
            result = evaluate(self.plan, self.capture)
            self.assertFalse(result['clinical_validation'])
            self.assertEqual(result['evidence_qualification'], 'NOT_VERIFIED_BY_PURE_EVALUATOR')
            self.assertIsNone(result['dynamic']['confidence_interval'])

    def test_partitions_and_unknown_subgroups_remain_separate(self):
        result = evaluate(self.plan, self.capture)
        self.assertEqual(result['partitions']['development']['periods'], 2)
        self.assertEqual(result['partitions']['validation']['periods'], 1)
        self.assertIsNone(result['partitions']['validation']['dynamic']['sensitivity'])
        self.assertEqual(result['subgroups']['treatment']['unknown']['patients'], 2)

    def test_cross_partition_patient_duplicate_is_rejected(self):
        self.capture['patients'].append({'patient_id': 'patient-a', 'partition': 'validation'})
        with self.assertRaisesRegex(EvaluationInputError, 'PATIENT_PARTITION_DUPLICATE'):
            evaluate(self.plan, self.capture)

    def test_cross_patient_event_pairing_is_rejected(self):
        self.capture['events'][0]['patient_id'] = 'patient-b'
        with self.assertRaisesRegex(EvaluationInputError, 'PROMPT_EVENT_SCOPE_INVALID'):
            evaluate(self.plan, self.capture)

    def test_future_or_unverified_used_facts_are_rejected_not_imputed(self):
        for change in ({'available_at': '2026-01-02T00:00:00Z'}, {'verified_at': None},
                {'verified_at': '2026-01-02T00:00:00Z'}):
            capture = deepcopy(self.capture)
            capture['periods'][0]['facts'][0].update(change)
            with self.subTest(change=change), self.assertRaisesRegex(EvaluationInputError, 'FUTURE_OR_UNVERIFIED_FACT_USED'):
                evaluate(self.plan, capture)
            capture['periods'][0]['facts'][0]['used'] = False
            self.assertEqual(evaluate(self.plan, capture)['future_or_unverified_facts_excluded'], 1)

    def test_empty_or_changed_config_and_dataset_binding_fail_closed(self):
        for binding in ({'dataset_version_id': 'other'}, {'dataset_checksum': 'e' * 64},
                {'implementation_sha256': 'f' * 64}):
            with self.subTest(binding=binding), self.assertRaisesRegex(EvaluationInputError, 'FROZEN_INPUT_BINDING_MISMATCH'):
                evaluate(self.plan, self.capture, expected_binding=binding)
        self.capture['configuration_checksums'] = {}
        with self.assertRaisesRegex(EvaluationInputError, 'FIXED_CONFIGURATIONS_REQUIRED'):
            evaluate(self.plan, self.capture)

    def test_mismatched_static_dynamic_input_fails(self):
        self.capture['periods'][0]['dynamic']['input_sha256'] = 'f' * 64
        with self.assertRaisesRegex(EvaluationInputError, 'STATIC_DYNAMIC_INPUT_MISMATCH'):
            evaluate(self.plan, self.capture)

    def test_overlap_and_out_of_window_prompts_are_rejected(self):
        self.capture['periods'][1]['start'] = '2026-01-01T01:00:00Z'
        with self.assertRaisesRegex(EvaluationInputError, 'OBSERVATION_PERIODS_OVERLAP'):
            evaluate(self.plan, self.capture)
        self.plan, self.capture = synthetic_capture()
        self.capture['periods'][0]['dynamic']['prompts'][0]['at'] = '2026-01-01T02:00:00Z'
        with self.assertRaisesRegex(EvaluationInputError, 'PROMPT_OUTSIDE_OBSERVATION'):
            evaluate(self.plan, self.capture)

    def test_unknown_prompt_outcomes_excluded_from_ppv_denominator(self):
        self.capture['periods'][1]['static']['prompts'][0]['outcome'] = 'unknown'
        result = evaluate(self.plan, self.capture)
        self.assertEqual(result['static']['ppv'], 1)
        self.assertEqual(result['static']['unknown_outcome_prompts'], 1)

    def test_duplicate_events_periods_and_prompt_ids_rejected(self):
        for collection in ('events', 'periods'):
            capture = deepcopy(self.capture)
            capture[collection].append(deepcopy(capture[collection][0]))
            with self.subTest(collection=collection), self.assertRaises(EvaluationInputError):
                evaluate(self.plan, capture)
        self.capture['periods'][0]['dynamic']['prompts'][1]['id'] = 'prompt-1'
        with self.assertRaisesRegex(EvaluationInputError, 'PROMPT_DUPLICATE'):
            evaluate(self.plan, self.capture)

    def test_future_observation_denominator_and_contradictory_negative_label_rejected(self):
        self.capture['periods'][2]['end'] = '2026-01-05T00:00:00Z'
        with self.assertRaisesRegex(EvaluationInputError, 'OBSERVATION_SCOPE_INVALID'):
            evaluate(self.plan, self.capture)
        self.plan, self.capture = synthetic_capture()
        self.capture['periods'][0]['label_state'] = 'negative'
        with self.assertRaisesRegex(EvaluationInputError, 'EVENT_PERIOD_LABEL_CONFLICT'):
            evaluate(self.plan, self.capture)

    def test_period_end_cutoff_cannot_legitimize_earlier_alert_using_later_facts(self):
        self.capture['periods'][0]['facts'][0]['available_at'] = '2026-01-01T01:00:00Z'
        with self.assertRaisesRegex(EvaluationInputError, 'PROMPT_USES_FUTURE_FACT'):
            evaluate(self.plan, self.capture)

    def test_unknown_prompt_cannot_be_silently_counted_as_missed_positive_event(self):
        self.capture['periods'][0]['static']['prompts'][0]['outcome'] = 'unknown'
        with self.assertRaisesRegex(EvaluationInputError, 'PROMPT_EVENT_OUTCOME_CONFLICT'):
            evaluate(self.plan, self.capture)

    def test_reused_execution_and_coerced_side_effects_rejected(self):
        from pydantic import ValidationError

        self.capture['periods'][1]['static']['task_id'] = self.capture['periods'][0]['static']['task_id']
        with self.assertRaisesRegex(EvaluationInputError, 'REPLAY_TASK_REUSED'):
            evaluate(self.plan, self.capture)
        self.plan, self.capture = synthetic_capture()
        self.capture['published_count'] = False
        with self.assertRaises(ValidationError):
            evaluate(self.plan, self.capture)
