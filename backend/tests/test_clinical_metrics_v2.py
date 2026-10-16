"""Deterministic software-only scheduled-replay metric contracts; no database."""
from copy import deepcopy
from django.test import SimpleTestCase
from pydantic import ValidationError
from case_details.clinical_metrics import evaluate, EvaluationInputError
from case_details.clinical_metrics_v2 import ClinicalCaptureV2, corpus_digest
from tests.test_clinical_metrics_closeout import synthetic_capture


def synthetic_capture_v2():
    plan, capture = synthetic_capture()
    plan.update(schema_version='clinical-measurement-plan-2', static_criterion='classification_meets',
        dynamic_criterion='decision_met', assessability='all_scheduled_steps_completed',
        occurrence_policy='first_positive_then_repeat', reset_policy='period_boundary_only', schedule=[])
    capture['schema_version'] = 'clinical-result-capture-2'
    for name in ('published_count', 'notified_count', 'current_cycle_mutations'):
        capture.pop(name)
    for period in capture['periods']:
        old = {mode: period.pop(mode) for mode in ('static', 'dynamic')}
        facts = period.pop('facts')
        for fact in facts:
            fact.pop('used')
            fact.update(field_id='field-' + fact['fact_ref'], record_version_id='version-' + fact['fact_ref'],
                verification_id='verification-' + fact['fact_ref'], reference_sha256='e' * 64)
        point = period.pop('known_at')
        times = sorted({prompt['at'] for value in old.values() for prompt in value['prompts']}) or [point.replace('02:00:00', '01:00:00')]
        period['steps'] = []
        for index, at in enumerate(times):
            step = dict(id=f'step-{index}', assessment_at=at, known_at=at, corpus_sha256='a' * 64, facts=deepcopy(facts))
            for mode in ('static', 'dynamic'):
                step[mode] = {**old[mode], 'task_id': f'{old[mode]["task_id"]}-{index}',
                    'execution_ref': f'{old[mode]["execution_ref"]}-{index}',
                    'execution_input_sha256': ('a' if mode == 'static' else 'b') * 64, 'result_sha256': 'f' * 64,
                    'used_fact_refs': [fact['fact_ref'] for fact in facts],
                    'prompts': [prompt for prompt in old[mode]['prompts'] if prompt['at'] == at]}
                step[mode].pop('input_sha256')
            period['steps'].append(step)
    rehash(plan, capture)
    return plan, capture


def rehash(plan, capture):
    for index, key in enumerate(('static-window', 'dynamic-window', 'verification-policy', 'dynamic-input', 'runtime-config')):
        capture['configuration_checksums'].setdefault(key, str(index + 1) * 64)
    typed = ClinicalCaptureV2.model_validate(capture)
    plan['schedule'] = []
    for period, parsed in zip(capture['periods'], typed.periods):
        for step, parsed_step in zip(period['steps'], parsed.steps):
            step['corpus_sha256'] = corpus_digest(typed, parsed, parsed_step)
            plan['schedule'].append({key: step[key] for key in ('assessment_at', 'known_at', 'corpus_sha256')} |
                {'period_id': period['id'], 'step_id': step['id'],
                    'static_rule_config_id': step['static']['rule_config_id'],
                    'dynamic_rule_config_id': step['dynamic']['rule_config_id'],
                    'static_policies': {'runtime_config_id': 'runtime-config', 'window_policy_id': 'static-window', 'verification_policy_id': 'verification-policy'},
                    'dynamic_policies': {'runtime_config_id': 'runtime-config', 'input_rule_id': 'dynamic-input', 'window_policy_id': 'dynamic-window',
                        'verification_policy_id': 'verification-policy'}})


class ClinicalMetricsV2Test(SimpleTestCase):
    def setUp(self):
        self.plan, self.capture = synthetic_capture_v2()

    def test_distinct_execution_hashes_share_explicit_corpus_and_preserve_metrics(self):
        report = evaluate(self.plan, self.capture)
        self.assertEqual(report['schema_version'], 'clinical-metrics-report-2')
        self.assertEqual(report['dynamic']['prompt_counts'], {'total': 2, 'first': 1, 'repeat': 1})
        self.assertEqual(report['static']['ppv'], 0.5)
        self.assertEqual(report['dynamic']['first_alert_difference_seconds_by_event'], {'event-1': -600})
        self.assertEqual(len(report['execution_steps']), 10)
        self.assertNotEqual(report['execution_steps'][0]['execution_input_sha256'], report['execution_steps'][1]['execution_input_sha256'])
        self.assertEqual(report['execution_steps'][0]['corpus_sha256'], report['execution_steps'][1]['corpus_sha256'])
        self.assertFalse(report['clinical_validation'])
        self.assertIsNone(report['dynamic']['confidence_interval'])

    def test_partial_schedule_is_unassessable_not_a_negative(self):
        last = self.capture['periods'][0]['steps'][-1]['dynamic']
        last.update(status='insufficient', prompts=[])
        report = evaluate(self.plan, self.capture)
        self.assertEqual(report['dynamic']['unassessable_positive_event_ids'], ['event-1'])
        self.assertEqual(report['dynamic']['missed_event_ids'], [])
        self.assertIsNone(report['dynamic']['sensitivity'])

    def test_partial_schedule_does_not_hide_bad_event_pairing(self):
        self.capture['periods'][0]['steps'][-1]['dynamic'].update(status='insufficient', prompts=[])
        self.capture['periods'][0]['steps'][0]['dynamic']['prompts'][0]['matched_event_id'] = 'absent-event'
        with self.assertRaisesRegex(EvaluationInputError, 'PROMPT_EVENT_SCOPE_INVALID'):
            evaluate(self.plan, self.capture)

    def test_corpus_mutation_and_schedule_omission_rejected(self):
        self.capture['periods'][0]['steps'][0]['facts'][0]['reference_sha256'] = '0' * 64
        with self.assertRaisesRegex(EvaluationInputError, 'CORPUS_DIGEST'):
            evaluate(self.plan, self.capture)
        self.plan, self.capture = synthetic_capture_v2()
        self.capture['periods'][0]['steps'].pop()
        with self.assertRaisesRegex(EvaluationInputError, 'SCHEDULE_MISMATCH'):
            evaluate(self.plan, self.capture)

    def test_unavailable_fact_can_be_in_corpus_but_cannot_be_consumed(self):
        step = self.capture['periods'][0]['steps'][0]
        step['facts'][0]['verified_at'] = None
        rehash(self.plan, self.capture)
        with self.assertRaisesRegex(EvaluationInputError, 'FUTURE_OR_UNVERIFIED'):
            evaluate(self.plan, self.capture)
        for mode in ('static', 'dynamic'):
            step[mode]['used_fact_refs'] = []
        self.assertEqual(evaluate(self.plan, self.capture)['future_or_unverified_facts_excluded'], 1)

    def test_used_fact_subset_and_reused_task_rejected(self):
        step = self.capture['periods'][0]['steps'][0]
        step['static']['used_fact_refs'] = ['not-in-corpus']
        with self.assertRaisesRegex(EvaluationInputError, 'USED_FACT_SCOPE'):
            evaluate(self.plan, self.capture)
        step['static']['used_fact_refs'] = []
        step['static']['task_id'] = step['dynamic']['task_id']
        with self.assertRaisesRegex(EvaluationInputError, 'TASK_REUSED'):
            evaluate(self.plan, self.capture)

    def test_occurrence_and_unsupported_reset_policy_cannot_be_invented(self):
        self.capture['periods'][0]['steps'][1]['dynamic']['prompts'][0]['occurrence'] = 'first'
        with self.assertRaisesRegex(EvaluationInputError, 'OCCURRENCE_INVALID'):
            evaluate(self.plan, self.capture)
        self.plan, self.capture = synthetic_capture_v2()
        self.plan['reset_policy'] = 'automatic_live_episode_reset'
        with self.assertRaises(ValidationError):
            evaluate(self.plan, self.capture)

    def test_capture_cannot_declare_its_own_zero_side_effects(self):
        self.capture['published_count'] = 0
        with self.assertRaises(ValidationError):
            evaluate(self.plan, self.capture)

    def test_post_run_choice_among_frozen_rules_is_rejected(self):
        self.capture['configuration_checksums']['other-frozen-rule'] = 'a' * 64
        self.capture['periods'][0]['steps'][0]['static']['rule_config_id'] = 'other-frozen-rule'
        with self.assertRaisesRegex(EvaluationInputError, 'REPLAY_PREDECLARED_RULE_MISMATCH'):
            evaluate(self.plan, self.capture)

    def test_missing_predeclared_policy_role_is_not_defaulted(self):
        self.plan['schedule'][0]['static_policies'].pop('window_policy_id')
        with self.assertRaises(ValidationError):
            evaluate(self.plan, self.capture)
