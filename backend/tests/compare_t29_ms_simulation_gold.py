import hashlib
import json
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN_TAG = 'current-release-20261009-4'
TASK_ID = '4d05e522-4adf-4462-9072-afd5f649aa9d'
API_READBACK = ROOT / '.planning-work' / 'parallel' / 'window1' / f't29-static-task-{RUN_TAG}-api-readback.json'
FIXTURE = ROOT / '.planning-work' / 'parallel' / 'window1' / 't29-simulation-fixture.json'
VALIDATION = ROOT / '.planning-work' / 'parallel' / 'window1' / 't29-simulation-fixture-validation.json'
OUTPUT = ROOT / '.planning-work' / 'parallel' / 'window1' / f't29-ms-simulation-gold-{RUN_TAG}.json'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    path.write_text(encoded, encoding='utf-8')
    require(json.loads(path.read_text(encoding='utf-8')) == json.loads(encoded),
        f'Saved report readback differs: {path.name}.')


def main():
    api_report = json.loads(API_READBACK.read_text(encoding='utf-8'))
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    validation = json.loads(VALIDATION.read_text(encoding='utf-8'))
    require(api_report['task_id'] == 'T29' and api_report['kind'] == 'ms'
        and api_report['task_api_readback']['status'] == 'completed'
        and api_report['result_api_readback']['assessment_id'] == '84c18be2-0921-4d0a-a338-25bcbf1c1896',
        'API readback does not contain the exact current MS result.')
    require(validation['status'] == 'PASS_SHARED_SCHEMA_AND_FIXED_FORMULAE'
        and fixture['mode'] == 'SIMULATION_ONLY' and fixture['not_persisted'] is True,
        'Independent formula reference must remain an unpersisted simulation fixture.')
    actual_inputs = {row['variable_code']: row['input_snapshot'] for row in api_report['result_api_readback']['inputs']}
    expected_facts = {row['variable_code']: row for row in fixture['fact_candidates']}
    category_values = {'present': '1', 'absent': '0'}
    compared_inputs = {}
    formula_codes = ('cns', 'ldh', 'ferritin', 'fibrinogen', 'platelet_count',
        'active_arthritis', 'hemorrhagic_manifestations')
    for code in formula_codes:
        expected = expected_facts[code]
        actual = actual_inputs[code]
        expected_value = category_values.get(expected['value'], expected['value'])
        require(actual['value'] == expected_value and actual['unit'] == expected.get('unit'),
            f'Current source snapshot differs from the simulation reference for {code}.')
        compared_inputs[code] = {'fixture_value': expected['value'], 'api_value': actual['value'],
            'fixture_unit': expected.get('unit'), 'api_unit': actual['unit'],
            'fixture_provenance': expected['verification_status']}

    expected = fixture['expected_fixed_formula_outputs']['ms']
    output = api_report['result_api_readback']['output']
    expected_contributions = {
        'cns': expected['cns'], 'ldh': expected['ldh_contribution'],
        'ferritin': expected['ferritin_contribution'], 'fibrinogen': expected['fibrinogen_contribution'],
        'platelet_count': expected['platelet_count_contribution'],
        'active_arthritis': expected['active_arthritis'],
        'hemorrhagic_manifestations': expected['hemorrhagic_manifestations'],
    }
    require(set(output['ms_contributions']) == set(expected_contributions)
        and all(Decimal(output['ms_contributions'][code]) == Decimal(expected_contributions[code])
            for code in expected_contributions),
        'Current MS contribution vector differs from the independent simulation reference.')
    expected_total = sum((Decimal(value) for value in expected_contributions.values()), Decimal('0'))
    require(expected_total == Decimal(expected['unrounded_total'])
        and Decimal(output['ms_total']) == expected_total,
        'Current MS unrounded total differs from the independent Decimal sum.')
    threshold_met = expected_total >= Decimal(expected['threshold'])
    require(output['ms_threshold_result'] is threshold_met is expected['threshold_met'],
        'Current MS threshold decision differs from the independent simulation reference.')
    report = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY', 'status': 'MS_RESULT_MATCHES_SIMULATION_GOLD',
        'run_tag': RUN_TAG, 'task': {'id': TASK_ID, 'result_id': api_report['result_api_readback']['assessment_id'],
            'input_sha256': api_report['task_input_sha256']},
        'gold_scope': 'synthetic candidate formula reference only; clinical gold standard NOT_RUN',
        'api_readback_sha256': hashlib.sha256(API_READBACK.read_bytes()).hexdigest(),
        'fixture_sha256': hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        'formula_validation_sha256': hashlib.sha256(VALIDATION.read_bytes()).hexdigest(),
        'compared_inputs': compared_inputs, 'api_contributions': output['ms_contributions'],
        'gold_contributions': expected_contributions, 'api_total': output['ms_total'],
        'gold_unrounded_total': expected['unrounded_total'], 'threshold': expected['threshold'],
        'api_threshold_result': output['ms_threshold_result'], 'gold_threshold_met': threshold_met,
        'model_calls': 0, 'external_calls': 0}
    save_json(OUTPUT, report)
    print(json.dumps({'status': report['status'], 'inputs_compared': len(compared_inputs),
        'total': report['api_total'], 'threshold_met': report['api_threshold_result'],
        'gold_scope': report['gold_scope'], 'saved_and_read': True,
        'report_path': str(OUTPUT)}, sort_keys=True))


if __name__ == '__main__':
    main()
