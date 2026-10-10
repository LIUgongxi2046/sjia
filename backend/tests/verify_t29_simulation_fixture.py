import hashlib
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

import django

django.setup()

from configurations.schemas import payload_reason, validate_payload


FIXTURE = ROOT / '.planning-work' / 'parallel' / 'window1' / 't29-simulation-fixture.json'
REPORT = ROOT / '.planning-work' / 'parallel' / 'window1' / 't29-simulation-fixture-validation.json'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def fact_value(facts, code):
    rows = [item for item in facts if item['variable_code'] == code]
    require(len(rows) == 1, f'Expected one candidate fact for {code}.')
    return rows[0]['value']


def main():
    fixture_bytes = FIXTURE.read_bytes()
    fixture = json.loads(fixture_bytes)
    require(fixture['mode'] == 'SIMULATION_ONLY', 'Fixture mode must be SIMULATION_ONLY.')
    require(fixture['use_state'] == 'CANDIDATE_FOR_PARENT_REVIEW', 'Fixture must remain pending parent review.')
    require(fixture['not_persisted'] is True, 'Candidate facts must remain unpersisted.')
    require(fixture['execution_state']['parent_approval_required'] is True, 'Parent approval gate is required.')

    validated = []
    for candidate in fixture['candidate_payloads']:
        shared_reason = payload_reason(candidate['kind'], candidate['schema_version'], candidate['payload'])
        if shared_reason is not None:
            raise RuntimeError(f'Candidate payload rejected by shared validator: {candidate["config_key"]}: {shared_reason}.')
        validate_payload(candidate['kind'], candidate['schema_version'], candidate['payload'])
        validated.append({'config_key': candidate['config_key'], 'kind': candidate['kind'],
            'schema_version': candidate['schema_version'], 'result': 'VALID',
            'shared_payload_reason': None, 'validation_path': 'validate_payload'})

    refs = fixture['candidate_configuration_refs']
    window = next(item['payload'] for item in fixture['candidate_payloads'] if item['schema_version'] == 'alert-window-1')
    decimal_codes = {'ferritin', 'platelet_count', 'ast', 'triglycerides', 'fibrinogen', 'ldh'}
    categorical_codes = {'sjia', 'fever', 'cns', 'hemorrhagic_manifestations', 'active_arthritis',
        'current_therapy', 'inpatient_status', 'late_result_flag'}
    require({item['variable_code'] for item in window['variables']} == decimal_codes | categorical_codes,
        'Window variable set must match configured static and context variables.')
    for item in window['variables']:
        expected_id = refs['unit_mapping'] if item['variable_code'] in decimal_codes else refs['qualification']
        require(item['comparison_policy_id'] == expected_id,
            f'Window comparison policy reference is invalid for {item["variable_code"]}.')
    require(window['scope']['population_config_id'] == refs['population_context'], 'Population context reference mismatch.')
    require(window['scope']['therapy_policy_id'] == refs['therapy_context'], 'Therapy context reference mismatch.')
    require(window['clinical_finding_validity_policy_id'] == refs['finding_context'], 'Clinical finding context reference mismatch.')
    require(window['late_result_policy_id'] == refs['late_result_context'], 'Late-result context reference mismatch.')

    facts = fixture['fact_candidates']
    require(all(item['verification_status'] == 'candidate_only' for item in facts), 'Every synthetic fact must remain a candidate.')
    require(all(item['source_text'].startswith('SIMULATION t29 fixture:') for item in facts), 'Synthetic source labels are required.')
    require(all(item['medical_time'] <= item['available_at'] <= item['recorded_at'] for item in facts),
        'Candidate timestamps must preserve medical, availability, and record order.')

    values = {code: Decimal(fact_value(facts, code)) for code in decimal_codes}
    classification_support = sum((
        values['platelet_count'] <= Decimal('181'),
        values['ast'] > Decimal('48'),
        values['triglycerides'] > Decimal('156'),
        values['fibrinogen'] <= Decimal('360'),
    ))
    classification_met = (fact_value(facts, 'sjia') == 'present' and fact_value(facts, 'fever') == 'present'
        and values['ferritin'] > Decimal('684') and classification_support >= 2)
    ms_total = (Decimal('2.44') * int(fact_value(facts, 'cns') == 'present')
        + Decimal('1.54') * int(fact_value(facts, 'hemorrhagic_manifestations') == 'present')
        - Decimal('1.30') * int(fact_value(facts, 'active_arthritis') == 'present')
        - Decimal('0.003') * values['platelet_count'] + Decimal('0.001') * values['ldh']
        - Decimal('0.004') * values['fibrinogen'] + Decimal('0.0001') * values['ferritin'])
    points = fixture['comparison_points']
    baseline = Decimal(next(item['value'] for item in points if item['medical_time'].startswith('2026-10-06')))
    current = Decimal(next(item['value'] for item in points if item['medical_time'].startswith('2026-10-08')))
    expected = fixture['expected_fixed_formula_outputs']
    require(classification_met and classification_support == expected['classification']['supporting_conditions_met'],
        'Classification fixed-rule result mismatch.')
    require(ms_total == Decimal(expected['ms']['unrounded_total']), 'MS unrounded Decimal result mismatch.')
    require(ms_total >= Decimal(expected['ms']['threshold']), 'MS threshold result mismatch.')
    require(abs(current - baseline) == Decimal(expected['dynamic']['ferritin_baseline_to_current_absolute_delta']),
        'Dynamic ferritin delta mismatch.')

    report = {
        'task_id': 'T29',
        'mode': 'SIMULATION_ONLY',
        'status': 'PASS_SHARED_SCHEMA_AND_FIXED_FORMULAE',
        'fixture_sha256': hashlib.sha256(fixture_bytes).hexdigest(),
        'source_sha256': {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in (
            'backend/configurations/schemas.py', 'backend/assessment/policies.py',
            'backend/alerting/policies.py', 'backend/tests/verify_t29_simulation_fixture.py')},
        'candidate_payloads': validated,
        'shared_lifecycle_validator': 'PASS',
        'shared_validate_payload_pass_count': sum(item['validation_path'] == 'validate_payload' for item in validated),
        'shared_validate_payload_rejected_count': sum(item['shared_payload_reason'] is not None for item in validated),
        'candidate_references_checked': len(window['variables']) + 4,
        'candidate_facts_checked': len(facts),
        'classification_supporting_conditions': classification_support,
        'classification_expected': 'meets' if classification_met else 'does_not_meet',
        'ms_unrounded_decimal': str(ms_total),
        'ms_threshold_met': ms_total >= Decimal(expected['ms']['threshold']),
        'ferritin_absolute_delta': str(abs(current - baseline)),
        'business_writes': 0,
        'api_calls': 0,
        'model_calls': 0,
        'external_calls': 0,
        'parent_approval_required': True,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    saved = json.loads(REPORT.read_text(encoding='utf-8'))
    require(saved == report, 'Validation report readback differs from its saved content.')
    print(json.dumps({'status': report['status'], 'schema_payloads': len(validated),
        'facts': len(facts), 'report': str(REPORT), 'saved_and_read': True}, sort_keys=True))


if __name__ == '__main__':
    main()
