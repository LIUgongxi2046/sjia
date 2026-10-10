import hashlib
import json
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WINDOW = ROOT / '.planning-work' / 'parallel' / 'window1'
OUTPUT = WINDOW / 't56-common-scope-composition.json'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_json(relative_path):
    path = ROOT / relative_path
    raw = path.read_bytes()
    value = json.loads(raw)
    return value, hashlib.sha256(raw).hexdigest()


def same_timestamp(left, right):
    return datetime.fromisoformat(left.replace('Z', '+00:00')) == datetime.fromisoformat(right.replace('Z', '+00:00'))


def matches_expected(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and matches_expected(actual[key], value)
            for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            matches_expected(actual_value, expected_value)
            for actual_value, expected_value in zip(actual, expected))
    return actual == expected


def main():
    first_alert_fixture_path = '.simulation-work/t29-first-alert-business-fixture.json'
    first_alert_fixture, first_alert_fixture_sha256 = read_json(first_alert_fixture_path)
    require(first_alert_fixture['mode'] == 'SIMULATION_ONLY'
        and first_alert_fixture['scenario'] == 'INDEPENDENT_FIRST_ALERT'
        and first_alert_fixture['patient_id'] == 'dff4286d-b457-43b1-b53d-64bc40b224a2'
        and first_alert_fixture['family_username'] == 'simulation-family-first-alert'
        and first_alert_fixture['clinical_independence'] == 'NOT_CLAIMED'
        and first_alert_fixture['clinical_approval'] == 'NOT_RUN'
        and sum(len(group) for group in first_alert_fixture['fact_ids']) == 15
        and len(first_alert_fixture['records']) == 2 and len(first_alert_fixture['verification_ids']) == 2,
        'New first-alert input is not a complete, explicitly simulated fixture.')
    scope_path = '.planning-work/parallel/window1/t29-common-scope-current-20261009-065526.json'
    scope, scope_sha256 = read_json(scope_path)
    classification_plan_path = '.planning-work/parallel/window1/t29-static-task-current-20261009-3-readback.json'
    ms_plan_path = '.planning-work/parallel/window1/t29-static-task-current-20261009-4-readback.json'
    classification_plan, classification_plan_sha256 = read_json(classification_plan_path)
    ms_plan, ms_plan_sha256 = read_json(ms_plan_path)
    classification_path = '.planning-work/coordination/root/task-36fe2110-577c-43a1-b955-40c3a5b3e350.json'
    ms_path = '.planning-work/coordination/root/task-a6bf735b-5044-4898-8f59-ac52fe241ba4.json'
    dynamic_path = '.planning-work/coordination/root/task-d17d89e4-345e-46c9-bb35-7d042d6a6988.json'
    classification, classification_sha256 = read_json(classification_path)
    ms, ms_sha256 = read_json(ms_path)
    dynamic, dynamic_sha256 = read_json(dynamic_path)
    dynamic_readback_path = '.planning-work/parallel/window1/t29-dynamic-task-current-20261009-5-readback.json'
    dynamic_readback, dynamic_readback_sha256 = read_json(dynamic_readback_path)
    summary_path = '.planning-work/parallel/window1/t29-summary-current-release-20261009-6-readback.json'
    summary, summary_sha256 = read_json(summary_path)
    static_result_api_path = '.planning-work/parallel/window1/t29-static-result-api-block.json'
    static_result_api, static_result_api_sha256 = read_json(static_result_api_path)
    latest_static_api_path = '.planning-work/parallel/window1/t29-static-task-current-20261009-3-api-readback.json'
    latest_static_api, latest_static_api_sha256 = read_json(latest_static_api_path)
    notification_path = '.planning-work/parallel/window1/t23-native-delivery-readback.json'
    notification, notification_sha256 = read_json(notification_path)
    qualification_path = '.planning-work/parallel/window1/current-simulation-qualification.json'
    qualification, qualification_sha256 = read_json(qualification_path)
    latest_scope_path = '.planning-work/parallel/window1/t29-common-scope-current-20261009-081917.json'
    latest_scope, latest_scope_sha256 = read_json(latest_scope_path)
    latest_classification_path = '.planning-work/coordination/root/task-f6917a16-a589-4b6e-9ca5-d4958ded0144.json'
    latest_classification, latest_classification_sha256 = read_json(latest_classification_path)
    latest_classification_plan_path = '.planning-work/parallel/window1/t29-static-task-final-20261009-081917-readback.json'
    latest_classification_plan, latest_classification_plan_sha256 = read_json(latest_classification_plan_path)
    latest_ms_intent_path = '.planning-work/parallel/window1/t29-static-intent-final-ms-20261009-081917-readback.json'
    latest_ms_intent, latest_ms_intent_sha256 = read_json(latest_ms_intent_path)
    release3_scope_path = '.planning-work/parallel/window1/t29-common-scope-current-20261009-082724.json'
    release3_scope, release3_scope_sha256 = read_json(release3_scope_path)
    release3_classification_plan_path = '.planning-work/parallel/window1/t29-static-task-release3-20261009-082724-readback.json'
    release3_classification_plan, release3_classification_plan_sha256 = read_json(release3_classification_plan_path)
    release3_ms_plan_path = '.planning-work/parallel/window1/t29-static-task-release3-ms-20261009-082724-readback.json'
    release3_ms_plan, release3_ms_plan_sha256 = read_json(release3_ms_plan_path)
    release3_classification_path = '.planning-work/coordination/root/task-acedcaad-5c3e-4685-bd46-0c0bc1eac079.json'
    release3_classification, release3_classification_sha256 = read_json(release3_classification_path)
    release3_ms_path = '.planning-work/coordination/root/task-5f0e9381-d884-4ff9-9234-cf871d8ba2f8.json'
    release3_ms, release3_ms_sha256 = read_json(release3_ms_path)
    release3_dynamic_state_path = '.planning-work/parallel/window1/t29-dynamic-task-release3-dynamic-20261009-082724-state.json'
    release3_dynamic_state, release3_dynamic_state_sha256 = read_json(release3_dynamic_state_path)
    release3_dynamic_readback_path = '.planning-work/parallel/window1/t29-dynamic-intent-release3-dynamic-20261009-082724-readback.json'
    release3_dynamic_readback, release3_dynamic_readback_sha256 = read_json(release3_dynamic_readback_path)
    t40_path = '.planning-work/parallel/window1/t40-positive-provider-disposition-readback.json'
    t40_readback, t40_sha256 = read_json(t40_path)
    t60_demo_path = '.planning-work/parallel/window1/t60-simulation-demo-current-fields-20261009.json'
    t60_demo, t60_demo_sha256 = read_json(t60_demo_path)
    scope04_path = '.planning-work/parallel/window1/t56-current-scope-first-alert-main-04.json'
    scope04, scope04_sha256 = read_json(scope04_path)
    t60_positive_manifest_path = '.planning-work/parallel/window1/t60-first-alert-positive-manifest.json'
    t60_positive_manifest, t60_positive_manifest_sha256 = read_json(t60_positive_manifest_path)
    t60_positive_path = '.planning-work/parallel/window1/t60-first-alert-positive-verification.json'
    t60_positive, t60_positive_sha256 = read_json(t60_positive_path)
    t55_correction_path = '.planning-work/parallel/window3/T55-correction-browser-roundtrip.json'
    t55_correction, t55_correction_sha256 = read_json(t55_correction_path)
    t56_home_path = '.planning-work/coordination/root/current-home-response.json'
    t56_home, t56_home_sha256 = read_json(t56_home_path)
    current_scope_path = '.planning-work/parallel/window1/t29-common-scope-final-20261009T091845Z.json'
    current_scope, current_scope_sha256 = read_json(current_scope_path)
    current_classification_plan_path = '.planning-work/parallel/window1/t29-static-task-final-20261009-091845-readback.json'
    current_classification_plan, current_classification_plan_sha256 = read_json(current_classification_plan_path)
    current_classification_path = '.planning-work/coordination/root/task-12481825-d23c-4f1a-bf8b-f94929e56400.json'
    current_classification, current_classification_sha256 = read_json(current_classification_path)
    current_ms_plan_path = '.planning-work/parallel/window1/t29-static-task-final-20261009-091845-ms-readback.json'
    current_ms_plan, current_ms_plan_sha256 = read_json(current_ms_plan_path)
    current_ms_path = '.planning-work/coordination/root/task-7d063afd-e9c1-4eeb-b19b-5ee7cf3ff796.json'
    current_ms, current_ms_sha256 = read_json(current_ms_path)
    current_dynamic_readback_path = '.planning-work/parallel/window1/t29-dynamic-task-final-20261009-091845-dyn-readback.json'
    current_dynamic_readback, current_dynamic_readback_sha256 = read_json(current_dynamic_readback_path)
    current_dynamic_path = '.planning-work/coordination/root/task-7f5783b6-a11e-4335-9735-711d5d290a2d.json'
    current_dynamic, current_dynamic_sha256 = read_json(current_dynamic_path)
    current_notification_path = '.planning-work/parallel/window1/t23-native-delivery-final-20261009-091845-readback.json'
    current_notification, current_notification_sha256 = read_json(current_notification_path)
    current_summary_path = '.planning-work/parallel/window1/t29-summary-final-20261009-091845-summary-readback.json'
    current_summary, current_summary_sha256 = read_json(current_summary_path)
    cross_window_specs = [
        ('T29_SCOPE04', scope04_path,
            {'mode': 'SIMULATION_ONLY', 'status': 'SIMULATION_POSITIVE_PATH_SAVED_AND_READ',
                'run_tag': 'first-alert-main-04',
                'result_refs': {'classification': '8a1f3f49-16a7-412d-9783-c6b991da4bd6',
                    'ms': 'b9d56a39-07b7-44b3-96f8-7ecbea5239c0',
                    'evaluation': '43fd490e-835f-49e7-b93d-0e280634d125',
                    'alert': 'a69b91f5-f5db-470b-981a-5ec740305111',
                    'summary': 'e1a02aa3-feac-4b87-b0b5-39396d40315e'},
                'notification': {'delivery_status': 'delivered',
                    'read_at': '2026-10-09T10:22:58.621600+00:00'}}),
        ('T60_POSITIVE_READBACK', t60_positive_path,
            {'mode': 'SIMULATION_ONLY', 'status': 'PASS', 'patient_id': t60_positive_manifest['patient_id'],
                'task': {'task_id': t60_positive_manifest['task_id'], 'status': 'completed',
                    'result_ref': t60_positive_manifest['evaluation_id']},
                'evaluation': {'evaluation_id': t60_positive_manifest['evaluation_id'],
                    'status': 'completed', 'validity': 'valid'},
                'alert': {'alert_id': t60_positive_manifest['alert_id'],
                    'validity': 'valid', 'content_state': 'available'},
                'notification': {'recipient_id': t60_positive_manifest['recipient_id'],
                    'delivery_status': 'delivered',
                    'receipt_ids': t60_positive['notification']['receipt_ids']},
                'summary': {'summary_id': t60_positive_manifest['summary_id'],
                    'validity': 'valid', 'needs_regeneration': False},
                'business_writes': 0, 'model_calls': 0, 'external_calls': 0}),
        ('T55_BROWSER_CORRECTION', t55_correction_path,
            {'data_mode': 'SIMULATION', 'status': 'SIMULATION_VOICE_CORRECTION_ROUNDTRIP_PASS',
                'old_version_readable': True, 'unknown_and_excluded_fields_preserved': True,
                'record_count_before': 1, 'record_count_after': 1, 'browser_errors': []}),
        ('T56_HOME_API_READBACK', t56_home_path,
            {'mode': 'SIMULATION_ONLY', 'status': 'PASS',
                'patient_id': 'dff4286d-b457-43b1-b53d-64bc40b224a2',
                'http_status': 200, 'saved_and_read': True}),
        ('T14', '.planning-work/parallel/window2/evidence/t14-upload-group-read.json',
            {'mode': 'SIMULATION_ONLY', 'status': 'available', 'version': '1'}),
        ('T30', '.planning-work/parallel/window2/evidence/business-state-device-check-2026-10-09T08-13-53.899Z.json',
            {'mode': 'SIMULATION_ONLY', 'status': 'PASS', 'totals': {'pass': 15, 'not_run': 2, 'fail': 0, 'page_errors': 0}}),
        ('T58', '.planning-work/parallel/window2/evidence/business-state-correction-continuation-2026-10-09T06-15-20.564Z.json',
            {'mode': 'SIMULATION_ONLY', 'status': 'PASS', 'totals': {'pass': 2, 'not_run': 2, 'fail': 0, 'page_errors': 0}}),
        ('T31', 'docs/testing/T31-authorization-integrated-simulation-report.json',
            {'classification': 'SIMULATION_MEASUREMENT_ONLY', 'saved_and_reread': True, 'real_capacity': 'NOT_RUN'}),
        ('T57', 'docs/testing/T57-isolated-simulation-report.json',
            {'classification': 'SIMULATION_RECOVERY_CHECK_ONLY', 'saved_and_reread': True,
                'real_backup_restore': 'NOT_RUN', 'RTO_RPO_target': 'NOT_PROVIDED'}),
        ('T32', '.planning-work/parallel/window3/T32-simulation-evaluation.json',
            {'status': 'SIMULATION_PREPARATION', 'real_clinical_validity': 'NOT_RUN',
                'real_family_understanding': 'NOT_RUN'}),
        ('T55', '.planning-work/parallel/window3/T55-voice-simulation-evaluation.json',
            {'data_mode': 'SIMULATION', 'evidence_scope': 'SIMULATION_TEXT_ONLY',
                'status': 'SIMULATION_EVALUATED', 'clinical_validity': 'NOT_RUN',
                'family_understanding': 'NOT_RUN'}),
        ('T59', '.planning-work/parallel/window3/T59-simulation-evaluation.json',
            {'status': 'SIMULATION_DENOMINATORS_ONLY',
                'dataset': {'clinical_qualification': 'not_evaluated'},
                'metrics': {'status': 'NOT_MEASURED'}}),
    ]
    cross_window_reports = []
    for task_id, path, expected in cross_window_specs:
        report, checksum = read_json(path)
        for key, value in expected.items():
            require(matches_expected(report.get(key), value),
                f'{task_id} handoff evidence differs from its recorded simulation scope: {path}')
        cross_window_reports.append({'task_id': task_id, 'path': path, 'sha256': checksum,
            'status': report.get('status'), 'mode': report.get('mode') or report.get('data_mode'),
            'acceptance_boundary': expected})

    require(t40_readback['mode'] == 'SIMULATION' and t40_readback['status'] == 'PASS'
        and t40_readback['real_provider'] == 'NOT_RUN' and t40_readback['clinical'] == 'NOT_RUN',
        'T40 provider disposition readback is outside its recorded simulation boundary.')
    require(t60_demo['mode'] == 'SIMULATION_ONLY' and t60_demo['status'] == 'PARTIAL_SIMULATION_DEMO_COMPLETED'
        and t60_demo['database'] == {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433}
        and t60_demo['business_scope'] == {'clinical_result_created': False,
            'alert_or_notification_created': False, 'summary_created': False,
            'external_service_called': False, 'model_called': False}
        and len(t60_demo['steps']) == 8,
        'Latest T60 family simulation report is incomplete or exceeds its recorded scope.')
    require(scope04['patient_id'] == t60_positive_manifest['patient_id']
        and scope04['dynamic']['evaluation_id'] == t60_positive_manifest['evaluation_id']
        and scope04['dynamic']['alert_id'] == t60_positive_manifest['alert_id']
        and scope04['notification']['recipient_id'] == t60_positive_manifest['recipient_id']
        and set(scope04['notification']['receipt_ids']) == set(t60_positive_manifest['receipt_ids'])
        and scope04['summary']['summary_id'] == t60_positive_manifest['summary_id']
        and t60_positive['manifest'] == t60_positive_manifest_path
        and len(t60_positive['source_versions']) == len(t60_positive_manifest['source_versions'])
        and all(readback['is_current'] and readback['record_id'] == manifest_item['record_id']
            and readback['record_version_id'] == manifest_item['record_version_id']
            for readback, manifest_item in zip(t60_positive['source_versions'],
                t60_positive_manifest['source_versions'])),
        'T60 positive API readback does not match its scope04 report and saved manifest.')
    require(t55_correction['old_version_readable']
        and t55_correction['unknown_and_excluded_fields_preserved']
        and not t55_correction['browser_errors'],
        'T55 browser correction readback does not preserve its declared simulation boundary.')

    current_patient_id = current_classification_plan['patient_id']
    require(current_scope['schema_version'] == 't29-common-scope-1'
        and current_scope['known_at'] == current_scope['assessment_at'] == current_scope['source_cutoff_at']
        and current_scope['release_id'] == '8b44a175-5809-4566-a859-a5d62ca83e6e'
        and current_scope['validation_id'] == '71719f27-ea0d-47c8-8032-4358d8fa91c9'
        and current_classification_plan['scope_sha256'] == current_ms_plan['scope_sha256'] == current_scope_sha256
        and current_classification_plan['patient_id'] == current_ms_plan['patient_id'] == current_patient_id
        and current_classification_plan['known_at'] == current_ms_plan['known_at'] == current_scope['known_at']
        and current_classification_plan['assessment_at'] == current_ms_plan['assessment_at'] == current_scope['assessment_at'],
        'Latest current-scope classification and MS plans do not share the saved patient, scope, and times.')
    for result, plan, kind in (
        (current_classification, current_classification_plan, 'classification'),
        (current_ms, current_ms_plan, 'ms'),
    ):
        task_plan = plan['tasks'][kind]
        require(result['mode'] == 'SIMULATION_ONLY' and result['status'] == 'PASS'
            and result['database'] == 'sjia_simulation' and result['task_status'] == 'completed'
            and result['queue_consumption'] == 'EXACT_SINGLE_EVENT'
            and result['task_id'] == task_plan['task_id']
            and result['event_id'] == task_plan['outbox_event']['id']
            and result['input_sha256'] == task_plan['task_input_sha256']
            and result['result']['patient'] == current_patient_id
            and same_timestamp(result['result']['assessment_at'], current_scope['assessment_at'])
            and result['result']['validity'] == 'valid' and result['result']['missing_items'] == [],
            f'Latest {kind} Task execution does not match its exact common-scope plan.')
    require(current_classification['output']['classification_result'] == 'meets'
        and current_ms['output']['ms_total'] == '-1.0500'
        and current_ms['output']['ms_threshold_result'] is True
        and len(current_ms['output']['ms_contributions']) == 7,
        'Latest common-scope classification or MS output is incomplete.')

    current_dynamic_task = current_dynamic_readback['task']
    current_dynamic_eval = current_dynamic['result']
    require(current_dynamic['mode'] == current_dynamic_readback['mode'] == 'SIMULATION_ONLY'
        and current_dynamic['status'] == 'PASS'
        and current_dynamic['database'] == 'sjia_simulation'
        and current_dynamic['queue_consumption'] == 'EXACT_SINGLE_EVENT'
        and current_dynamic['task_id'] == current_dynamic_task['task_id']
        and current_dynamic['event_id'] == current_dynamic_task['outbox_event']['id']
        and current_dynamic['input_sha256'] == current_dynamic_task['task_input_sha256']
        and current_dynamic_eval['patient'] == current_patient_id
        and same_timestamp(current_dynamic_eval['known_at'], current_scope['known_at'])
        and same_timestamp(current_dynamic_eval['assessment_at'], current_scope['assessment_at'])
        and current_dynamic_eval['decision'] == 'met' and current_dynamic_eval['validity'] == 'valid'
        and current_dynamic_eval['missing_items'] == []
        and current_dynamic['output']['lifecycle']['action'] == 'continue_episode'
        and current_dynamic['output']['lifecycle']['event_id'] is None
        and current_dynamic_task['alert_events'] == []
        and current_dynamic_task['alert_recipients'] == [],
        'Latest dynamic Task readback does not match its exact run or lifecycle result.')

    require(current_notification['mode'] == 'SIMULATION_ONLY'
        and current_notification['status'] == 'FAMILY_CONTENT_UNAVAILABLE_CURRENT_CONFIGURATION'
        and current_notification['family_message_response']['http_status'] == 409
        and current_notification['family_message_response']['response']['code'] == 'CONFIG_VALIDATION_UNAVAILABLE'
        and current_notification['delivery'] == current_notification['read'] == 'NOT_RUN'
        and current_notification['alert_id'] == 'db547811-b132-416d-87dd-ebed5879c7a5'
        and current_notification['family_api_record']['current_validity'] == 'invalid',
        'Latest family notification readback does not preserve its exact rejection and NOT_RUN states.')
    require(current_summary['mode'] == 'SIMULATION_ONLY'
        and current_summary['status'] == 'SUMMARY_CREATED_AND_READ'
        and current_summary['database'] == {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433}
        and current_summary['patient_id'] == current_patient_id
        and current_summary['summary']['task_status'] == 'completed'
        and current_summary['summary']['summary_item_count'] == 30
        and current_summary['summary']['api_visible_item_count'] == 30
        and current_summary['summary']['summary_needs_regeneration'] is False
        and set(current_summary['summary']['source_record_ids']) <= set(current_summary['source_record_ids']),
        'Latest Summary API/database/source readback is incomplete or out of scope.')

    latest_task = latest_classification_plan['tasks']['classification']
    require(latest_scope['schema_version'] == 't29-common-scope-1'
        and latest_scope['release_id'] == '80546222-76e6-456c-8f79-0d4d38a4db39'
        and latest_scope['known_at'] == latest_scope['assessment_at'] == latest_scope['source_cutoff_at']
        and latest_classification_plan['mode'] == 'SIMULATION_ONLY'
        and latest_classification_plan['status'] == 'STATIC_TASKS_CREATED_AND_READ'
        and latest_classification_plan['scope_sha256'] == latest_scope_sha256
        and latest_task['task_id'] == latest_classification['task_id']
        and latest_task['outbox_event']['id'] == latest_classification['event_id']
        and latest_classification['mode'] == 'SIMULATION_ONLY'
        and latest_classification['status'] == 'PASS'
        and latest_classification['database'] == 'sjia_simulation'
        and latest_classification['task_status'] == 'completed'
        and latest_classification['queue_consumption'] == 'EXACT_SINGLE_EVENT'
        and latest_classification['result']['computation_status'] == 'completed'
        and latest_classification['result']['validity'] == 'valid'
        and latest_classification['result']['missing_items'] == []
        and latest_classification['clinical_approval'] == 'NOT_RUN',
        'Latest current-scope classification execution does not match its saved Task and scope.')
    require(latest_ms_intent['mode'] == 'SIMULATION_ONLY'
        and latest_ms_intent['status'] == 'STATIC_INTENT_PREPARED_NO_REQUEST'
        and latest_ms_intent['database'] == {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433}
        and latest_ms_intent['intent_status'] == 'prepared'
        and latest_ms_intent['matching_request_count'] == 0
        and latest_ms_intent['matching_task_count'] == 0
        and latest_ms_intent['matching_event_count'] == 0
        and latest_ms_intent['read_only'] is True,
        'Latest MS intent report does not prove that no Task request or event was created.')

    release3_classification_task = release3_classification_plan['tasks']['classification']
    release3_ms_task = release3_ms_plan['tasks']['ms']
    release3_patient_id = release3_classification_plan['patient_id']
    require(release3_scope['schema_version'] == 't29-common-scope-1'
        and release3_scope['release_id'] == '21930c86-1ae1-41e5-aa38-7f1ec3c74f33'
        and release3_scope['known_at'] == release3_scope['assessment_at'] == release3_scope['source_cutoff_at']
        and release3_classification_plan['mode'] == release3_ms_plan['mode'] == 'SIMULATION_ONLY'
        and release3_classification_plan['status'] == release3_ms_plan['status'] == 'STATIC_TASKS_CREATED_AND_READ'
        and release3_classification_plan['scope_sha256'] == release3_ms_plan['scope_sha256'] == release3_scope_sha256
        and release3_classification_plan['patient_id'] == release3_ms_plan['patient_id']
        and release3_classification_plan['known_at'] == release3_ms_plan['known_at'] == release3_scope['known_at']
        and release3_classification_plan['assessment_at'] == release3_ms_plan['assessment_at'] == release3_scope['assessment_at'],
        'Latest static Task preparation reports do not share the recorded patient, release, and evaluation times.')
    for report, plan, task in (
        (release3_classification, release3_classification_plan, release3_classification_task),
        (release3_ms, release3_ms_plan, release3_ms_task),
    ):
        require(report['mode'] == 'SIMULATION_ONLY' and report['status'] == 'PASS'
            and report['database'] == 'sjia_simulation' and report['task_status'] == 'completed'
            and report['queue_consumption'] == 'EXACT_SINGLE_EVENT'
            and report['task_id'] == task['task_id'] and report['event_id'] == task['outbox_event']['id']
            and report['input_sha256'] == task['task_input_sha256']
            and report['result']['patient'] == release3_patient_id
            and same_timestamp(report['result']['assessment_at'], release3_scope['assessment_at'])
            and report['result']['validity'] == 'valid' and report['result']['missing_items'] == [],
            f"{task['task_id']} execution does not match its exact latest-scope Task and event.")
    require(release3_classification['result']['kind'] == 'classification'
        and release3_classification['output']['classification_result'] == 'meets'
        and release3_ms['result']['kind'] == 'ms'
        and release3_ms['output']['ms_total'] == '-1.0500'
        and release3_ms['output']['ms_threshold_result'] is True
        and len(release3_ms['output']['ms_contributions']) == 7,
        'Latest common-scope classification or MS output is incomplete.')
    require(release3_dynamic_state['mode'] == release3_dynamic_readback['mode'] == 'SIMULATION_ONLY'
        and release3_dynamic_state['scope_sha256'] == release3_scope_sha256
        and release3_dynamic_state['patient_id'] == release3_patient_id
        and release3_dynamic_state['intent_id'] == release3_dynamic_readback['intent_id']
        and release3_dynamic_state['request_id'] == release3_dynamic_readback['request_id']
        and release3_dynamic_readback['status'] == 'DYNAMIC_INTENT_PREPARED_NO_REQUEST'
        and release3_dynamic_readback['clean_no_request_task_event'] is True
        and release3_dynamic_readback['intent_status'] == 'prepared'
        and release3_dynamic_readback['matching_request_count'] == 0
        and release3_dynamic_readback['matching_task_count'] == 0
        and release3_dynamic_readback['matching_event_count'] == 0,
        'Latest dynamic intent readback does not establish its prepared no-request state.')

    expected_scope = scope_sha256
    patient_id = classification_plan['patient_id']
    require(patient_id == ms_plan['patient_id'], 'Classification and MS Tasks target different simulation patients.')
    for label, plan in (('classification', classification_plan), ('ms', ms_plan)):
        require(plan['mode'] == 'SIMULATION_ONLY' and plan['status'] == 'STATIC_TASKS_CREATED_AND_READ'
            and plan['database'] == {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433}
            and plan['scope_sha256'] == expected_scope
            and plan['known_at'] == plan['assessment_at'] == scope['known_at'] == scope['assessment_at'],
            f'{label} Task preparation report is outside the saved common scope.')

    expected_static = (
        (classification, classification_plan['tasks']['classification']['task_id'],
            classification_plan['tasks']['classification']['task_input_sha256'], 'classification'),
        (ms, ms_plan['tasks']['ms']['task_id'], ms_plan['tasks']['ms']['task_input_sha256'], 'ms'),
    )
    for report, task_id, input_sha256, kind in expected_static:
        require(report['mode'] == 'SIMULATION_ONLY' and report['status'] == 'PASS'
            and report['database'] == 'sjia_simulation' and report['task_status'] == 'completed'
            and report['queue_consumption'] == 'EXACT_SINGLE_EVENT'
            and report['task_id'] == task_id and report['input_sha256'] == input_sha256
            and report['result']['patient'] == patient_id
            and same_timestamp(report['result']['assessment_at'], scope['assessment_at'])
            and report['result']['kind'] == kind and report['result']['validity'] == 'valid'
            and report['result']['missing_items'] == [],
            f'{kind} execution report does not match its exact common-scope Task.')
    require(classification['output']['classification_result'] == 'meets',
        'Current simulation classification did not meet its configured synthetic conditions.')
    require(ms['output']['ms_total'] == '-1.0500' and ms['output']['ms_threshold_result'] is True
        and len(ms['output']['ms_contributions']) == 7,
        'Current simulation MS result lacks the seven-contribution total and threshold.')

    dynamic_task = dynamic_readback['task']
    dynamic_eval = dynamic['result']
    require(dynamic_readback['mode'] == dynamic['mode'] == 'SIMULATION_ONLY'
        and dynamic_readback['status'] == 'DYNAMIC_TASK_READBACK_COMPLETE'
        and dynamic['status'] == 'PASS' and dynamic['database'] == 'sjia_simulation'
        and dynamic['queue_consumption'] == 'EXACT_SINGLE_EVENT'
        and dynamic_task['task_status'] == dynamic['task_status'] == 'completed'
        and dynamic_task['task_input_sha256'] == dynamic['input_sha256']
        and dynamic_eval['patient'] == patient_id
        and same_timestamp(dynamic_eval['assessment_at'], scope['assessment_at'])
        and same_timestamp(dynamic_eval['known_at'], scope['known_at'])
        and dynamic_eval['decision'] == 'met' and dynamic_eval['validity'] == 'valid'
        and dynamic_eval['missing_items'] == []
        and dynamic_task['evaluation_api_readback']['input_sha256'] == dynamic['input_sha256'],
        'Dynamic Task and Evaluation API/database readback do not match the common scope.')
    require(len(dynamic_task['alert_events']) == 1
        and dynamic_task['alert_events'][0]['id'] == dynamic['output']['lifecycle']['event_id']
        and dynamic_task['alert_events'][0]['validity'] == 'valid'
        and len(dynamic_task['alert_event_outbox']) == 1
        and dynamic_task['alert_event_outbox'][0]['published_at'] is None
        and len(dynamic_task['alert_recipients']) == 0,
        'Dynamic AlertEvent, pending Outbox, or current recipient inventory changed.')

    active_release = qualification['active_release']
    event_release_id = dynamic_task['alert_events'][0]['release_id']
    active_release_matches_event = active_release is not None and active_release['id'] == event_release_id
    require(qualification['mode'] == 'SIMULATION_ONLY'
        and qualification['status'] == 'CURRENT_QUALIFICATION_READBACK'
        and qualification['database'] == {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433}
        and qualification['gate']['enabled'] is True
        and active_release is not None
        and active_release['publication_epoch'] == active_release['gate_publication_epoch']
        and len(active_release['components']) == 7
        and all(component['config_status'] == 'enabled' for component in active_release['components'])
        and active_release['qualification_reason'] is None
        and qualification['gate']['runtime_config_reason'] is None
        and all(provider['config_qualification_reason'] is None for provider in qualification['runtime_providers'])
        and len(qualification['runtime_providers']) >= 1,
        'Current simulation release, runtime configuration, or provider qualification is incomplete.')

    require(summary['mode'] == 'SIMULATION_ONLY' and summary['status'] == 'SUMMARY_CREATED_AND_READ'
        and summary['database'] == {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433}
        and summary['summary']['summary_item_count'] == 30
        and summary['summary']['api_visible_item_count'] == 30
        and summary['summary']['summary_needs_regeneration'] is False
        and summary['summary']['task_status'] == 'completed',
        'Current Summary API and database readback is incomplete.')
    require(static_result_api['mode'] == 'SIMULATION_ONLY'
        and static_result_api['status'] == 'CLASSIFICATION_RESULT_API_REJECTED'
        and static_result_api['kind'] == 'classification' and static_result_api['http_status'] == 409
        and static_result_api['error_code'] == 'CONFIG_VALIDATION_UNAVAILABLE'
        and static_result_api['task_id_value'] == classification_plan['tasks']['classification']['task_id'],
        'Static result API limitation does not identify the exact observed simulation rejection.')
    require(latest_static_api['mode'] == 'SIMULATION_ONLY'
        and latest_static_api['status'] == 'ASSESSMENT_TASK_API_REJECTED'
        and latest_static_api['task_id_value'] == classification_plan['tasks']['classification']['task_id']
        and latest_static_api['http_status'] == 422
        and latest_static_api['response']['code'] == 'CONFIG_PAYLOAD_INVALID'
        and latest_static_api['queue_consumption'] == 'NONE',
        'Latest classification Task API observation is missing or does not identify the exact blocked read.')
    require(notification['mode'] == 'SIMULATION_ONLY'
        and notification['status'] == 'FAMILY_CONTENT_UNAVAILABLE_CURRENT_CONFIGURATION'
        and notification['recipient_id'] == '1f8bef18-2839-43fd-8c55-549f66dc5174'
        and notification['family_message_response']['http_status'] == 503
        and notification['family_message_response']['response']['code'] == 'NOTIFICATION_CONFIGURATION_UNAVAILABLE'
        and notification['delivery'] == notification['read'] == 'NOT_RUN',
        'Notification acceptance and current family content limit are not recorded accurately.')
    notification_db = notification['database_readback']
    support_attempt = notification['support_api_record']['attempts']
    family_record = notification['family_api_record']
    require(notification_db['database'] == {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433}
        and notification_db['recipient_id'] == notification['recipient_id']
        and len(support_attempt) == 1 and support_attempt[0]['attempt_id'] == notification_db['attempt']['id']
        and notification_db['attempt']['status'] == support_attempt[0]['status'] == 'accepted'
        and notification_db['task']['status'] == 'completed'
        and notification_db['task']['id'] == notification_db['attempt']['send_permit_id']
        and len(notification_db['receipts']) == len(support_attempt[0]['receipts']) == 1
        and notification_db['receipts'][0]['status'] == support_attempt[0]['receipts'][0]['status'] == 'accepted'
        and notification_db['receipts'][0]['id'] == support_attempt[0]['receipts'][0]['receipt_id']
        and notification_db['outbox_events'][0]['id'] == notification_db['notification_consumer_receipts'][0]['event_id']
        and notification_db['outbox_events'][0]['resource_refs'] == {
            'attempt_id': notification_db['attempt']['id'], 'recipient_id': notification['recipient_id']}
        and len(notification_db['outbox_events']) == len(notification_db['notification_consumer_receipts']) == 1
        and len(notification_db['alert_event_consumer_receipts']) == 1
        and notification_db['alert_event_consumer_receipts'][0]['effect_resource_id'] == notification['alert_id']
        and family_record['delivery_status'] == notification_db['attempt']['status']
        and family_record['read_at'] is None and family_record['recipient_id'] == notification['recipient_id'],
        'Native accepted attempt, signed receipt, Task, Outbox and consumer receipts differ across API and database readbacks.')

    sources = [
        {'path': path, 'sha256': checksum} for path, checksum in (
            (scope_path, scope_sha256),
            (classification_plan_path, classification_plan_sha256),
            (ms_plan_path, ms_plan_sha256),
            (classification_path, classification_sha256),
            (ms_path, ms_sha256),
            (dynamic_path, dynamic_sha256),
            (dynamic_readback_path, dynamic_readback_sha256),
            (summary_path, summary_sha256),
            (static_result_api_path, static_result_api_sha256),
            (latest_static_api_path, latest_static_api_sha256),
            (notification_path, notification_sha256),
            (qualification_path, qualification_sha256),
            (latest_scope_path, latest_scope_sha256),
            (latest_classification_path, latest_classification_sha256),
            (latest_classification_plan_path, latest_classification_plan_sha256),
            (latest_ms_intent_path, latest_ms_intent_sha256),
            (release3_scope_path, release3_scope_sha256),
            (release3_classification_plan_path, release3_classification_plan_sha256),
            (release3_ms_plan_path, release3_ms_plan_sha256),
            (release3_classification_path, release3_classification_sha256),
            (release3_ms_path, release3_ms_sha256),
            (release3_dynamic_state_path, release3_dynamic_state_sha256),
            (release3_dynamic_readback_path, release3_dynamic_readback_sha256),
            (t40_path, t40_sha256),
            (t60_demo_path, t60_demo_sha256),
            (scope04_path, scope04_sha256),
            (t60_positive_manifest_path, t60_positive_manifest_sha256),
            (t60_positive_path, t60_positive_sha256),
            (t55_correction_path, t55_correction_sha256),
            (t56_home_path, t56_home_sha256),
            (current_scope_path, current_scope_sha256),
            (current_classification_plan_path, current_classification_plan_sha256),
            (current_classification_path, current_classification_sha256),
            (current_ms_plan_path, current_ms_plan_sha256),
            (current_ms_path, current_ms_sha256),
            (current_dynamic_readback_path, current_dynamic_readback_sha256),
            (current_dynamic_path, current_dynamic_sha256),
            (current_notification_path, current_notification_sha256),
            (current_summary_path, current_summary_sha256),
            (first_alert_fixture_path, first_alert_fixture_sha256),
            *((item['path'], item['sha256']) for item in cross_window_reports),
        )]
    report = {
        'task_id': 'T56',
        'mode': 'SIMULATION_ONLY',
        'status': 'PARTIAL_SIMULATION_COMPONENT_COMPOSITION',
        'database': {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433},
        'scope': {'path': scope_path, 'sha256': scope_sha256, 'patient_id': patient_id,
            'known_at': scope['known_at'], 'assessment_at': scope['assessment_at']},
        'classification': {'task_id': classification['task_id'], 'result_id': classification['result']['id'],
            'status': 'completed', 'result': classification['output']['classification_result'],
            'input_sha256': classification['input_sha256']},
        'ms': {'task_id': ms['task_id'], 'result_id': ms['result']['id'], 'status': 'completed',
            'contribution_count': len(ms['output']['ms_contributions']), 'total': ms['output']['ms_total'],
            'threshold_result': ms['output']['ms_threshold_result'], 'input_sha256': ms['input_sha256']},
        'dynamic': {'task_id': dynamic['task_id'], 'evaluation_id': dynamic_eval['id'],
            'decision': dynamic_eval['decision'], 'validity': dynamic_eval['validity'],
            'alert_id': dynamic['output']['lifecycle']['event_id'],
            'alert_release_id': event_release_id,
            'current_release_match': active_release_matches_event,
            'alert_event_outbox_id': dynamic_task['alert_event_outbox'][0]['id'],
            'alert_outbox_pending': dynamic_task['alert_event_outbox'][0]['published_at'] is None,
            'recipient_count': len(dynamic_task['alert_recipients']), 'input_sha256': dynamic['input_sha256']},
        'summary': {'summary_id': summary['summary']['summary_id'],
            'api_visible_item_count': summary['summary']['api_visible_item_count'],
            'summary_item_count': summary['summary']['summary_item_count'],
            'needs_regeneration': summary['summary']['summary_needs_regeneration']},
        'current_qualification': {'gate_version': qualification['gate']['version'],
            'publication_epoch': qualification['gate']['publication_epoch'],
            'runtime_config_id': qualification['gate']['runtime_config_id'],
            'runtime_config_reason': qualification['gate']['runtime_config_reason'],
            'active_release_id': active_release['id'],
            'release_qualification_reason': active_release['qualification_reason'],
            'common_scope_alert_release_id': event_release_id,
            'common_scope_alert_release_matches_current': active_release_matches_event,
            'provider_qualification_reasons': [{'kind': provider['kind'],
                'config_id': provider['config_id'], 'reason': provider['config_qualification_reason']}
                for provider in qualification['runtime_providers']]},
        'supplemental_current_scope': {'scope_path': latest_scope_path,
            'scope_sha256': latest_scope_sha256, 'release_id': latest_scope['release_id'],
            'validation_id': latest_scope['validation_id'], 'known_at': latest_scope['known_at'],
            'classification_task_id': latest_classification['task_id'],
            'classification_result_id': latest_classification['result']['id'],
            'classification_result_status': latest_classification['result']['validity'],
            'classification_event_id': latest_classification['event_id'],
            'ms_intent_id': latest_ms_intent['intent_id'], 'ms_request_id': latest_ms_intent['request_id'],
            'ms_intent_state': latest_ms_intent['intent_status'],
            'ms_request_count': latest_ms_intent['matching_request_count'],
            'ms_task_count': latest_ms_intent['matching_task_count'],
            'ms_event_count': latest_ms_intent['matching_event_count']},
        'latest_scope_execution': {'scope_path': release3_scope_path,
            'scope_sha256': release3_scope_sha256, 'release_id': release3_scope['release_id'],
            'validation_id': release3_scope['validation_id'], 'known_at': release3_scope['known_at'],
            'patient_id': release3_patient_id,
            'classification_task_id': release3_classification['task_id'],
            'classification_event_id': release3_classification['event_id'],
            'classification_result_id': release3_classification['result']['id'],
            'classification_result': release3_classification['output']['classification_result'],
            'ms_task_id': release3_ms['task_id'], 'ms_event_id': release3_ms['event_id'],
            'ms_result_id': release3_ms['result']['id'], 'ms_total': release3_ms['output']['ms_total'],
            'ms_contribution_count': len(release3_ms['output']['ms_contributions']),
            'dynamic_intent_id': release3_dynamic_readback['intent_id'],
            'dynamic_request_id': release3_dynamic_readback['request_id'],
            'dynamic_intent_state': release3_dynamic_readback['intent_status'],
            'dynamic_request_count': release3_dynamic_readback['matching_request_count'],
            'dynamic_task_count': release3_dynamic_readback['matching_task_count'],
            'dynamic_event_count': release3_dynamic_readback['matching_event_count']},
        'latest_authorized_run': {'scope_path': current_scope_path, 'scope_sha256': current_scope_sha256,
            'release_id': current_scope['release_id'], 'validation_id': current_scope['validation_id'],
            'patient_id': current_patient_id, 'known_at': current_scope['known_at'],
            'classification_task_id': current_classification['task_id'],
            'classification_event_id': current_classification['event_id'],
            'classification_result_id': current_classification['result']['id'],
            'classification_result': current_classification['output']['classification_result'],
            'ms_task_id': current_ms['task_id'], 'ms_event_id': current_ms['event_id'],
            'ms_result_id': current_ms['result']['id'], 'ms_total': current_ms['output']['ms_total'],
            'dynamic_task_id': current_dynamic['task_id'], 'dynamic_event_id': current_dynamic['event_id'],
            'dynamic_evaluation_id': current_dynamic_eval['id'],
            'dynamic_lifecycle_action': current_dynamic['output']['lifecycle']['action'],
            'dynamic_alert_event_created': current_dynamic['output']['lifecycle']['event_id'] is not None,
            'notification_http_status': current_notification['family_message_response']['http_status'],
            'notification_error_code': current_notification['family_message_response']['response']['code'],
            'notification_delivery': current_notification['delivery'], 'notification_read': current_notification['read'],
            'summary_id': current_summary['summary']['summary_id'],
            'summary_item_count': current_summary['summary']['summary_item_count'],
            'summary_source_record_count': len(current_summary['summary']['source_record_ids'])},
        'first_alert_case': {'fixture_path': first_alert_fixture_path,
            'fixture_sha256': first_alert_fixture_sha256, 'patient_id': first_alert_fixture['patient_id'],
            'family_username': first_alert_fixture['family_username'],
            'source_record_count': len(first_alert_fixture['records']),
            'verified_fact_count': sum(len(group) for group in first_alert_fixture['fact_ids']),
            'professional_check_count': len(first_alert_fixture['verification_ids']),
            'release_id': '8b44a175-5809-4566-a859-a5d62ca83e6e',
            'publication_epoch': 38, 'release_evidence_source': 'MAIN_WINDOW_HANDOFF',
            'clinical_independence': 'NOT_CLAIMED',
            'clinical_approval': 'NOT_RUN', 'task_execution': 'NOT_RUN',
            'alert_event_and_recipient': 'NOT_RUN', 'notification_delivery_and_read': 'NOT_RUN',
            'summary_roundtrip': 'NOT_RUN'},
        'coverage': {'same_patient_and_evaluation_times_for_classification_ms_dynamic': 'PASS',
            'task_attempt_consumer_receipt_and_result_readbacks': 'PASS',
            'summary_saved_and_current_api_database_readback': 'PASS',
            'classification_result_api_readback': 'BLOCKED_CONFIG_PAYLOAD_INVALID_HTTP_422',
            'earlier_classification_result_api_readback': 'BLOCKED_CONFIG_VALIDATION_UNAVAILABLE_HTTP_409',
            'ms_result_api_readback': 'NOT_RUN_AFTER_SHARED_CONFIGURATION_BLOCK',
            'common_scope_alert_release_currentness': 'PASS' if active_release_matches_event else 'HISTORICAL_RELEASE_SUPERSEDED',
            'notification_native_acceptance_and_signed_accepted_receipt': 'PASS',
            'notification_database_task_outbox_and_consumer_readback': 'PASS',
            'notification_family_delivery_and_read': 'NOT_RUN_HTTP_503_CONFIGURATION_UNAVAILABLE',
            'current_runtime_and_release_qualification': 'PASS_CURRENT_RELEASE_AND_RUNTIME',
            'supplemental_current_scope_classification': 'PASS_EXACT_SINGLE_EVENT_SAVED_READ',
            'supplemental_current_scope_ms': 'PREPARED_INTENT_WITHOUT_REQUEST_TASK_OR_EVENT',
            'supplemental_current_scope_dynamic': 'PREPARED_INTENT_NO_REQUEST_TASK_OR_EVENT_CONFIRMED_READ_ONLY',
            'latest_scope_classification_and_ms': 'PASS_EXACT_SINGLE_EVENT_SAVED_READ',
            'latest_scope_dynamic': 'PREPARED_INTENT_NO_REQUEST_TASK_OR_EVENT_CONFIRMED_READ_ONLY',
            'latest_authorized_classification_and_ms': 'PASS_EXACT_SINGLE_EVENT_SAVED_READ',
            'latest_authorized_dynamic': 'PASS_EXACT_SINGLE_EVENT_CONTINUE_EPISODE_NO_NEW_ALERT_EVENT',
            'latest_authorized_notification_delivery_and_read': 'NOT_RUN_HTTP_409_CONFIG_VALIDATION_UNAVAILABLE',
            'latest_authorized_summary': 'PASS_30_ITEMS_API_DATABASE_AND_SOURCE_SAVED_READ',
            'first_alert_case_fixture_input': 'PASS_SIMULATION_FIXTURE_15_FACTS_TWO_RECORDS_TWO_CHECKS',
            'first_alert_case_current_scope_and_full_flow': 'NOT_RUN_SHARED_API_AND_DATABASE_UNAVAILABLE',
            't40_provider_copy_disposition_saved_readback': 'PASS_SIMULATION_EXTERNAL_PROVIDER_NOT_RUN',
            't60_family_demo_saved_correction_and_history_readback': 'PASS_EIGHT_STEP_SIMULATION_DEMO',
            'scope04_first_alert_saved_and_read': 'PASS_SIMULATION_POSITIVE_PATH',
            't60_positive_manifest_api_readback': 'PASS_READ_ONLY_ACCURATE_OBJECTS',
            't55_browser_correction_and_prior_version_readback': 'PASS_SIMULATION_NONVISUAL_PAGE',
            't56_current_home_api_readback': 'PASS_HTTP_200_SAVED_AND_READ',
            'cross_window_t14_t30_t31_t32_t55_t57_t58_t59': 'SAVED_SIMULATION_REPORTS_LINKED_WITH_ACCEPTANCE_LIMITS',
            'full_original_and_prd_2_0_acceptance': 'NOT_RUN',
            'clinical_gold_device_wechat_visual_production': 'NOT_RUN'},
        'source_reports': sources,
        'cross_window_reports': cross_window_reports,
        'external_calls': 0,
        'model_calls': 0,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    saved = json.loads(OUTPUT.read_text(encoding='utf-8'))
    require(saved == report, 'Saved T56 common-scope composition differs from its source evidence.')
    print(json.dumps({'status': report['status'], 'saved_and_read': True,
        'output': str(OUTPUT.relative_to(ROOT)), 'source_count': len(sources),
        'classification': report['classification']['result'], 'ms_total': report['ms']['total'],
        'dynamic_decision': report['dynamic']['decision'], 'recipient_count': report['dynamic']['recipient_count'],
        'runtime_qualification': report['current_qualification']['runtime_config_reason'],
        'release_qualification': report['current_qualification']['release_qualification_reason'],
        'common_scope_release_current': report['current_qualification']['common_scope_alert_release_matches_current'],
        't40_disposition_readback': t40_readback['status'], 't60_demo': t60_demo['status']},
        ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
