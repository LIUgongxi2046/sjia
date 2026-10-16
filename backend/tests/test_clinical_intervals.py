"""Pure synthetic interval tests: no database, network, or clinical approval."""
from copy import deepcopy
from itertools import product
import json
import unittest
from unittest.mock import patch

from case_details.clinical_intervals import (
    IntervalProtocol, MetricSpec, evaluate_intervals, interval_engine,
    interval_from_draws, Contribution, METRICS,
)
from case_details.clinical_metrics import ClinicalCapture, MeasurementPlan, evaluate
from tests.test_clinical_metrics_closeout import synthetic_capture


def protocol_payload(plan_ref='synthetic:plan-v1', protocol_ref='synthetic:intervals'):
    return {'schema_version': 'clinical-interval-protocol-1', 'protocol_ref': protocol_ref,
        'measurement_plan_ref': plan_ref, 'method': 'patient_cluster_percentile_v1',
        'algorithm_version': '1', 'confidence_level': 0.5, 'alternative': 'two_sided',
        'quantile_method': 'linear', 'cluster_key': 'patient_id',
        'sampling': 'whole_cluster_with_replacement',
        'cluster_frame': 'all_frozen_patients_in_selected_partition', 'partition_policy': 'separate_only',
        'n_resamples': 200, 'rng_algorithm': 'PCG64', 'seed': 17,
        'patient_order': 'patient_id_lexicographic', 'numpy_version': interval_engine()['numpy_version'],
        'implementation_sha256': interval_engine()['implementation_sha256'],
        'invalid_resample_policy': 'fail_interval', 'degenerate_policy': 'not_computed',
        'minimum_clusters': 2, 'minimum_contributing_clusters': 2, 'coverage': 'pointwise',
        'multiplicity_adjustment': 'none', 'metric_specs': [metric_spec('alerts_per_observation_person_hour')]}


def metric_spec(metric, mode='dynamic', partition='development', subgroup=None, required=True):
    aggregation, unit, denominator = METRICS[metric]
    return {'spec_id': metric + '-' + mode, 'metric_id': metric, 'mode': mode,
        'scope': {'partition': partition, 'subgroup': subgroup}, 'aggregation': aggregation,
        'unit': unit, 'denominator_policy': denominator, 'required': required}


def interval_fixture():
    plan, capture = synthetic_capture()
    plan['confidence_interval_protocol_ref'] = 'synthetic:intervals'
    capture['patients'][1]['partition'] = 'development'
    # A contributes two alerts over 4 hours; B zero over 2 hours.
    return plan, capture, protocol_payload()


class IntervalContractTests(unittest.TestCase):
    def test_every_decision_is_required(self):
        payload = protocol_payload()
        for key in payload:
            with self.subTest(key=key), self.assertRaises(ValueError):
                IntervalProtocol.model_validate({k: v for k, v in payload.items() if k != key})

    def test_invalid_and_unknown_decisions_rejected(self):
        for key, value in [('confidence_level', 0), ('confidence_level', 1), ('confidence_level', True),
                ('confidence_level', float('nan')), ('n_resamples', True), ('n_resamples', 1),
                ('seed', -1), ('seed', 1.5), ('method', 'wilson'), ('cluster_key', 'event_id'),
                ('minimum_clusters', 1), ('minimum_contributing_clusters', 1),
                ('coverage', 'simultaneous'), ('quantile_method', 'nearest')]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                IntervalProtocol.model_validate({**protocol_payload(), key: value})
        for key, value in [('aggregation', 'mean_of_ratios'), ('unit', 'events'),
                ('denominator_policy', 'all_prompts'), ('scope', {'partition': 'pooled', 'subgroup': None})]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                MetricSpec.model_validate({**metric_spec('sensitivity'), key: value})

    def test_hand_enumerated_ratio_oracle_cluster_multiplicity(self):
        # Ordered draws AA, AB, BA, BB. Ratios: 1, 1/4, 1/4, 0.
        # q=.25/.75 linear interpolation gives 3/16 and 7/16, not mean patient ratios.
        rows = [Contribution(1, 1), Contribution(0, 3)]
        result = interval_from_draws(rows, list(product(range(2), repeat=2)),
            MetricSpec.model_validate(metric_spec('sensitivity')), IntervalProtocol.model_validate(protocol_payload()))
        self.assertEqual(result['estimate'], 0.25)
        self.assertEqual((result['lower'], result['upper']), (3/16, 7/16))
        self.assertEqual((result['draws_valid'], result['draws_invalid']), (4, 0))
        self.assertEqual(result['status'], 'CALCULATED')

    def test_mean_median_and_missed_fraction_oracles(self):
        draws = list(product(range(2), repeat=2))
        protocol = IntervalProtocol.model_validate(protocol_payload())
        for metric, expected in [('first_alert_difference_mean_seconds', (-5, 2.5)),
                                 ('first_alert_difference_median_seconds', (-5, 2.5))]:
            # AA [-20,-20], AB/BA [-20,0,20], BB [0,20,0,20].
            result = interval_from_draws([Contribution(-20, 1, (-20,)), Contribution(20, 2, (0,20))],
                draws, MetricSpec.model_validate(metric_spec(metric)), protocol)
            self.assertEqual((result['lower'], result['upper']), expected)
        result = interval_from_draws([Contribution(1,1), Contribution(0,3)], draws,
            MetricSpec.model_validate(metric_spec('missed_event_fraction')), protocol)
        self.assertEqual((result['lower'], result['upper']), (9/16, 13/16))

    def test_failed_and_degenerate_distributions(self):
        protocol = IntervalProtocol.model_validate(protocol_payload())
        spec = MetricSpec.model_validate(metric_spec('sensitivity'))
        cases = [([], [], 'ZERO_DENOMINATOR'), ([Contribution(0,0)]*2, [[0,1]], 'ZERO_DENOMINATOR'),
            ([Contribution(1,1)], [[0]], 'INSUFFICIENT_CLUSTERS'),
            ([Contribution(1,1), Contribution(0,0)], [[0,1]], 'INSUFFICIENT_CLUSTERS'),
            ([Contribution(1,1)]*2, [[0,0],[0,1],[1,1]], 'DEGENERATE_DISTRIBUTION'),
            ([Contribution(0,1)]*2, [[0,0],[0,1],[1,1]], 'DEGENERATE_DISTRIBUTION')]
        for rows, draws, status in cases:
            with self.subTest(status=status):
                result = interval_from_draws(rows, draws, spec, protocol)
                self.assertEqual(result['status'], status)
                self.assertIsNone(result['lower'])
                json.dumps(result, allow_nan=False)
        result = interval_from_draws([Contribution(1,1),Contribution(0,2),Contribution(0,0)],
            [[0,1,2],[2,2,2],[1,1,1]], spec, protocol)
        self.assertEqual((result['status'],result['draws_invalid'],result['draws_valid']), ('INVALID_RESAMPLE',1,2))

    def test_nonnull_positive_path_and_no_database_network(self):
        plan, capture, protocol = interval_fixture()
        with patch('socket.socket', side_effect=AssertionError('No network')):
            report = evaluate(plan, capture, interval_protocol=protocol)
        section = report['confidence_intervals']
        item = section['results'][0]
        self.assertEqual(item['status'], 'CALCULATED')
        self.assertEqual((item['estimate'],item['lower'],item['upper']), (1/3, 0, .5))
        self.assertFalse(report['clinical_validation'])
        self.assertEqual(report['evidence_qualification'], 'NOT_VERIFIED_BY_PURE_EVALUATOR')
        self.assertEqual(section['status'], 'COMPLETE')

    def test_permutation_invariant_paired_draws_and_separate_domains(self):
        plan, capture, protocol = interval_fixture()
        protocol['metric_specs'].append(metric_spec('alerts_per_observation_person_hour', 'static'))
        first = evaluate(plan,capture,interval_protocol=protocol)['confidence_intervals']
        capture['patients'].reverse(); capture['periods'].reverse(); capture['events'].reverse()
        second = evaluate(plan,capture,interval_protocol=protocol)['confidence_intervals']
        self.assertEqual(first, second)
        self.assertEqual(first['results'][0]['draw_sequence_sha256'], first['results'][1]['draw_sequence_sha256'])
        self.assertEqual(first['results'][0]['denominator'], 6)
        protocol['seed'] += 1
        changed = evaluate(plan,capture,interval_protocol=protocol)['confidence_intervals']
        self.assertNotEqual(first['protocol_sha256'], changed['protocol_sha256'])
        self.assertNotEqual(first['results'][0]['draw_sequence_sha256'], changed['results'][0]['draw_sequence_sha256'])

    def test_absent_unknown_mismatched_and_resource_protocols(self):
        plan, capture, protocol = interval_fixture()
        for value, status in [(None, 'NOT_CONFIGURED'), ({**protocol,'method':'bca'},'UNSUPPORTED_METHOD'),
                ({**protocol,'schema_version':'future'},'UNSUPPORTED_PROTOCOL'),
                ({**protocol,'n_resamples':100000000}, 'RESOURCE_LIMIT'),
                ({**protocol,'numpy_version':'0.0'},'UNSUPPORTED_PROTOCOL')]:
            with self.subTest(status=status):
                self.assertEqual(evaluate(plan,capture,interval_protocol=value)['confidence_intervals']['status'], status)
        for key in ('protocol_ref', 'measurement_plan_ref', 'implementation_sha256'):
            changed = {**protocol,key:'f'*64 if key.endswith('sha256') else 'different'}
            with self.subTest(key=key), self.assertRaises(ValueError):
                evaluate(plan,capture,interval_protocol=changed)

class IntervalDenominatorTests(unittest.TestCase):
    def test_all_metric_registry_ratios_and_time_oracles(self):
        protocol = IntervalProtocol.model_validate(protocol_payload())
        draws = list(product(range(2), repeat=2))
        for metric in METRICS:
            rows = [Contribution(1,1,(1,)), Contribution(0,3,(0,0,0))]
            result = interval_from_draws(rows, draws, MetricSpec.model_validate(metric_spec(metric)), protocol)
            self.assertEqual(result['status'],'CALCULATED',metric)
            if metric == 'first_alert_difference_median_seconds':
                expected = (0, .25)
            elif metric == 'missed_event_fraction':
                expected = (9/16, 13/16)
            else:
                expected = (3/16, 7/16)
            self.assertEqual((result['lower'],result['upper']),expected,metric)

    def test_unknown_and_insufficient_denominators_remain_separate(self):
        plan,capture,protocol = interval_fixture()
        protocol['metric_specs'] = [metric_spec(metric) for metric in METRICS]
        report = evaluate(plan,capture,interval_protocol=protocol)
        results = {item['metric_id']:item for item in report['confidence_intervals']['results']}
        self.assertEqual(results['sensitivity']['denominator'],1)
        self.assertEqual(results['ppv']['denominator'],2)
        self.assertEqual(results['alerts_per_observation_person_hour']['denominator'],6)
        self.assertEqual(results['alerts_per_negative_observation_person_hour']['denominator'],2)
        self.assertEqual(results['first_alert_difference_mean_seconds']['denominator'],1)
        self.assertEqual(results['first_alert_difference_mean_seconds']['diagnostics']['detected_events'],1)
        capture['periods'][0]['dynamic'].update(status='insufficient',prompts=[])
        results = {item['metric_id']:item for item in evaluate(plan,capture,interval_protocol=protocol)['confidence_intervals']['results']}
        self.assertEqual(results['sensitivity']['status'],'ZERO_DENOMINATOR')
        self.assertEqual(results['ppv']['status'],'ZERO_DENOMINATOR')
        self.assertEqual(results['alerts_per_observation_person_hour']['denominator'],4)
        self.assertEqual(results['event_unassessable_fraction']['estimate'],1)
        self.assertEqual(results['first_alert_difference_median_seconds']['diagnostics']['unassessable_positive_events'],1)

    def test_partition_frame_contains_zero_domain_contributors(self):
        plan,capture,protocol = interval_fixture()
        protocol['metric_specs'] = [metric_spec('alerts_per_observation_person_hour', subgroup={'dimension':'treatment','value':'x'})]
        capture['periods'][0]['subgroups']['treatment']='x'
        result=evaluate(plan,capture,interval_protocol=protocol)['confidence_intervals']['results'][0]
        self.assertEqual((result['clusters'],result['contributing_clusters'],result['denominator']),(2,1,2))
        self.assertEqual(result['status'],'INSUFFICIENT_CLUSTERS')
        capture['periods'][2]['subgroups']['treatment']='x'
        result=evaluate(plan,capture,interval_protocol=protocol)['confidence_intervals']['results'][0]
        self.assertEqual((result['clusters'],result['contributing_clusters'],result['denominator']),(2,2,4))
        protocol['metric_specs'][0]['scope']['subgroup']['value']='unknown'
        result=evaluate(plan,capture,interval_protocol=protocol)['confidence_intervals']['results'][0]
        self.assertEqual((result['clusters'],result['contributing_clusters'],result['denominator']),(2,1,2))
        capture['patients'][1]['partition']='validation'
        protocol['metric_specs'][0]['scope']['subgroup']=None
        result=evaluate(plan,capture,interval_protocol=protocol)['confidence_intervals']['results'][0]
        self.assertEqual((result['clusters'],result['denominator']),(1,4))
        protocol['metric_specs'][0]['scope']['subgroup']={'dimension':'new','value':'x'}
        with self.assertRaisesRegex(ValueError,'SUBGROUP_NOT_FROZEN'):
            evaluate(plan,capture,interval_protocol=protocol)

    def test_repeated_patient_keeps_multiple_event_and_prompt_contributions(self):
        from case_details.clinical_intervals import contributions
        plan,capture,protocol=interval_fixture()
        event={**capture['events'][0],'id':'event-2'}
        capture['events'].append(event)
        capture['periods'][0]['dynamic']['prompts'].append({**capture['periods'][0]['dynamic']['prompts'][0],
            'id':'prompt-for-event-2','matched_event_id':'event-2'})
        typed=ClinicalCapture.model_validate(capture)
        spec=MetricSpec.model_validate(metric_spec('sensitivity'))
        rows=contributions(typed,spec,['patient-a','patient-b'])
        self.assertEqual((rows[0].numerator,rows[0].denominator),(2,2))
        self.assertEqual((rows[0].detected,len(rows[0].differences)),(2,2))
        from case_details.clinical_intervals import statistic
        self.assertEqual(statistic(rows,[0,0],spec),(1,4,4))
        ppv=contributions(typed,MetricSpec.model_validate(metric_spec('ppv')),['patient-a','patient-b'])
        self.assertEqual((ppv[0].numerator,ppv[0].denominator),(3,3))

    def test_numeric_and_time_resource_failures_never_return_false_interval(self):
        spec=MetricSpec.model_validate(metric_spec('sensitivity'))
        protocol=IntervalProtocol.model_validate(protocol_payload())
        result=interval_from_draws([Contribution(float('inf'),1),Contribution(0,1)],[[0,1]],spec,protocol)
        self.assertEqual(result['status'],'NUMERIC_FAILURE')
        json.dumps(result,allow_nan=False)
        result=interval_from_draws([Contribution(1,1),Contribution(0,1)],[[0,1]],spec,protocol,deadline=0)
        self.assertEqual(result['status'],'RESOURCE_LIMIT')
        self.assertEqual((result['lower'],result['upper']),(None,None))

    def test_v2_explicit_interval_section_preserves_independent_replay_contract(self):
        from tests.test_clinical_metrics_v2 import synthetic_capture_v2
        plan,capture=synthetic_capture_v2()
        plan['confidence_interval_protocol_ref']='synthetic:intervals'
        capture['patients'][1]['partition']='development'
        report=evaluate(plan,capture,interval_protocol=protocol_payload())
        self.assertEqual(report['schema_version'],'clinical-metrics-report-3')
        self.assertEqual(report['point_report_schema_version'],'clinical-metrics-report-2')
        self.assertEqual(report['confidence_intervals']['results'][0]['status'],'CALCULATED')
        self.assertEqual(len(report['execution_steps']),10)
        capture['periods'][0]['steps'][0]['dynamic']['result_sha256']='not-a-digest'
        with self.assertRaises(ValueError):
            evaluate(plan,capture,interval_protocol=protocol_payload())


    def test_explicit_evidence_binding_is_complete_and_pins_the_candidate_body(self):
        from case_details.clinical_metrics import IntervalEvidenceBinding
        from case_details.clinical_intervals import digest
        plan,capture,protocol=interval_fixture()
        binding={'protocol_sha256':digest(protocol),'approval_evidence_ref':'synthetic:approval',
            'approval_evidence_sha256':'e'*64,'approval_audit_id':'22222222-2222-4222-8222-222222222222',
            'purpose':'clinical_evaluation'}
        for key in binding:
            with self.subTest(key=key),self.assertRaises(ValueError):
                IntervalEvidenceBinding.model_validate({k:v for k,v in binding.items() if k!=key})
        plan['confidence_interval_binding']=binding
        self.assertEqual(evaluate(plan,capture,interval_protocol=protocol)['confidence_intervals']['status'],'COMPLETE')
        with self.assertRaisesRegex(ValueError,'INTERVAL_FROZEN_BODY_MISMATCH'):
            evaluate(plan,capture,interval_protocol={**protocol,'seed':18})

    def test_published_protocol_and_plan_schema_match_live_models(self):
        from pathlib import Path
        from case_details.clinical_intervals import interval_protocol_schema
        from case_details.clinical_metrics_v2 import MeasurementPlanV2
        root=Path(__file__).resolve().parents[2]
        self.assertEqual(json.loads((root/'backend-design/clinical-interval-protocol.schema.json').read_text()),interval_protocol_schema())
        self.assertEqual(json.loads((root/'backend-design/clinical-measurement-plan-v2.schema.json').read_text()),MeasurementPlanV2.model_json_schema())
