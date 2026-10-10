import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WINDOW = ROOT / '.planning-work' / 'parallel' / 'window1'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_json(value):
    path = (ROOT / value).resolve()
    require(path.is_relative_to(ROOT), f'Input path is outside the project: {value}')
    raw = path.read_bytes()
    return path, json.loads(raw), hashlib.sha256(raw).hexdigest()


def same_timestamp(left, right):
    return datetime.fromisoformat(left.replace('Z', '+00:00')) == datetime.fromisoformat(right.replace('Z', '+00:00'))


def main():
    parser = argparse.ArgumentParser(description='核验T56指定SIMULATION共同范围的Task与保存回读结果')
    parser.add_argument('--run-tag', required=True)
    parser.add_argument('--scope-file', required=True)
    parser.add_argument('--fixture-file', required=True)
    parser.add_argument('--qualification-file', required=True)
    parser.add_argument('--classification-plan', required=True)
    parser.add_argument('--classification-execution', required=True)
    parser.add_argument('--ms-plan', required=True)
    parser.add_argument('--ms-execution', required=True)
    parser.add_argument('--dynamic-plan', required=True)
    parser.add_argument('--dynamic-readback', required=True)
    parser.add_argument('--dynamic-execution', required=True)
    parser.add_argument('--notification-creation', required=True)
    parser.add_argument('--notification-execution', required=True)
    parser.add_argument('--notification-readback', required=True)
    parser.add_argument('--summary-readback', required=True)
    args = parser.parse_args()
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{2,47}', args.run_tag) is not None,
        'Run tag must be a lowercase alphanumeric label with optional hyphens.')

    inputs = {
        'scope': read_json(args.scope_file),
        'fixture': read_json(args.fixture_file),
        'qualification': read_json(args.qualification_file),
        'classification_plan': read_json(args.classification_plan),
        'classification_execution': read_json(args.classification_execution),
        'ms_plan': read_json(args.ms_plan),
        'ms_execution': read_json(args.ms_execution),
        'dynamic_plan': read_json(args.dynamic_plan),
        'dynamic_readback': read_json(args.dynamic_readback),
        'dynamic_execution': read_json(args.dynamic_execution),
        'notification_creation': read_json(args.notification_creation),
        'notification_execution': read_json(args.notification_execution),
        'notification_readback': read_json(args.notification_readback),
        'summary_readback': read_json(args.summary_readback),
    }
    values = {key: item[1] for key, item in inputs.items()}
    hashes = {key: item[2] for key, item in inputs.items()}
    scope = values['scope']
    fixture = values['fixture']
    patient_id = fixture['patient_id']
    scope_sha256 = hashes['scope']
    fixture_sha256 = hashes['fixture']

    require(scope['mode'] == 'current'
        and scope['known_at'] == scope['assessment_at'] == scope['source_cutoff_at']
        and scope['patient_id'] == patient_id
        and scope['fixture_sha256'] == fixture_sha256,
        'Scope does not match the saved fixture, patient, or shared evaluation time.')
    require(fixture['mode'] == 'SIMULATION_ONLY'
        and fixture['scenario'] == 'INDEPENDENT_FIRST_ALERT'
        and fixture['clinical_approval'] == 'NOT_RUN'
        and fixture['patient_id'] == patient_id
        and fixture['family_username'],
        'Fixture is incomplete or outside its SIMULATION boundary.')
    qualification = values['qualification']
    active_release = qualification['active_release']
    require(qualification['mode'] == 'SIMULATION_ONLY'
        and qualification['status'] == 'CURRENT_QUALIFICATION_READBACK'
        and qualification['database'] == {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433}
        and active_release['id'] == scope['release_id']
        and active_release['publication_epoch'] == scope['publication_epoch']
        and active_release['validation_config']['id'] == scope['validation_id'],
        'Qualification and active release do not match the common scope.')

    static_results = []
    for kind in ('classification', 'ms'):
        plan = values[f'{kind}_plan']
        execution = values[f'{kind}_execution']
        require(plan['mode'] == 'SIMULATION_ONLY'
            and plan['status'] == 'STATIC_TASKS_CREATED_AND_READ'
            and plan['database'] == {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433}
            and plan['patient_id'] == patient_id
            and plan['scope_sha256'] == scope_sha256
            and plan['business_fixture_sha256'] == fixture_sha256
            and plan['known_at'] == scope['known_at']
            and plan['assessment_at'] == scope['assessment_at'],
            f'{kind} plan is outside the common scope.')
        task_plan = plan['tasks'][kind]
        result = execution['result']
        attempt = execution['attempts']
        require(execution['mode'] == 'SIMULATION_ONLY'
            and execution['status'] == 'PASS'
            and execution['database'] == 'sjia_simulation'
            and execution['queue_consumption'] == 'EXACT_SINGLE_EVENT'
            and execution['task_status'] == 'completed'
            and execution['patient_id'] == patient_id
            and execution['business_fixture_sha256'] == fixture_sha256
            and execution['task_id'] == task_plan['task_id']
            and execution['event_id'] == task_plan['outbox_event']['id']
            and execution['input_sha256'] == task_plan['task_input_sha256']
            and result['task_id'] == task_plan['task_id']
            and result['patient'] == patient_id
            and result['kind'] == kind
            and result['validity'] == 'valid'
            and result['missing_items'] == []
            and execution['consumer_receipt']['event'] == execution['event_id']
            and execution['consumer_receipt']['effect_resource_id'] == result['id']
            and same_timestamp(result['assessment_at'], scope['assessment_at'])
            and len(attempt) == 1
            and attempt[0]['status'] == 'completed'
            and attempt[0]['usage_evidence']['input_references'],
            f'{kind} Task, exact Event, Attempt, inputs, or result do not match the scope.')
        require(execution['clinical_approval'] == 'NOT_RUN'
            and execution['external_model_calls'] == 0,
            f'{kind} execution exceeds the simulation boundary.')
        static_results.append({'kind': kind, 'task_id': execution['task_id'],
            'event_id': execution['event_id'], 'result_id': result['id'],
            'input_count': len(attempt[0]['usage_evidence']['input_references']),
            'status': result['computation_status'], 'validity': result['validity']})

    classification = values['classification_execution']
    ms = values['ms_execution']
    require(classification['output']['classification_result'] == 'meets'
        and ms['output']['ms_total'] == '-1.0500'
        and ms['output']['ms_threshold_result'] is True
        and len(ms['output']['ms_contributions']) == 7,
        'Static outputs do not contain the expected simulated classification and seven-part MS result.')

    dynamic_plan = values['dynamic_plan']
    dynamic_readback = values['dynamic_readback']
    dynamic = values['dynamic_execution']
    dynamic_task = dynamic_readback['task']
    evaluation = dynamic['result']
    require(dynamic_plan['mode'] == 'SIMULATION_ONLY'
        and dynamic_plan['patient_id'] == patient_id
        and dynamic_plan['scope_sha256'] == scope_sha256
        and dynamic_plan['business_fixture_sha256'] == fixture_sha256
        and dynamic_plan['request_id'] == dynamic_readback['request_id']
        and dynamic_plan['task_id_value'] == dynamic_readback['task']['task_id']
        and dynamic_plan['known_at'] == scope['known_at']
        and dynamic_plan['assessment_at'] == scope['assessment_at']
        and dynamic_plan['task_read']['task_id'] == dynamic_plan['task_id_value']
        and dynamic_plan['task_read']['result_ref'] == dynamic_readback['task']['evaluation_api_readback']['evaluation_id']
        and dynamic_plan['evaluation_read']['evaluation_id'] == dynamic_plan['task_read']['result_ref'],
        'Dynamic plan is outside the common scope.')
    require(dynamic['mode'] == dynamic_readback['mode'] == 'SIMULATION_ONLY'
        and dynamic['status'] == 'PASS'
        and dynamic_readback['status'] == 'DYNAMIC_TASK_READBACK_COMPLETE'
        and dynamic['database'] == 'sjia_simulation'
        and dynamic['queue_consumption'] == 'EXACT_SINGLE_EVENT'
        and dynamic['external_model_calls'] == 0
        and dynamic['patient_id'] == patient_id
        and dynamic['business_fixture_sha256'] == fixture_sha256
        and dynamic['task_id'] == dynamic_plan['task_id_value'] == dynamic_task['task_id']
        and dynamic['event_id'] == dynamic_task['outbox_event']['id']
        and dynamic['input_sha256'] == dynamic_task['task_input_sha256']
        and dynamic_task['task_status'] == 'completed'
        and dynamic['consumer_receipt']['consumer'] == 'alert_evaluation'
        and dynamic['consumer_receipt']['event'] == dynamic['event_id']
        and dynamic['consumer_receipt']['effect_resource_id'] == dynamic['result']['id']
        and evaluation['id'] == dynamic_task['evaluation_api_readback']['evaluation_id']
        and evaluation['patient'] == patient_id
        and evaluation['status'] == 'completed'
        and evaluation['decision'] == 'met'
        and evaluation['validity'] == 'valid'
        and evaluation['publication_epoch'] == scope['publication_epoch']
        and evaluation['missing_items'] == []
        and same_timestamp(evaluation['known_at'], scope['known_at'])
        and same_timestamp(evaluation['assessment_at'], scope['assessment_at'])
        and dynamic_task['evaluation_api_readback']['input_sha256'] == dynamic['input_sha256']
        and dynamic_task['evaluation_api_readback']['validity'] == 'valid',
        'Dynamic Task or Evaluation does not match the exact common-scope result.')
    lifecycle = dynamic['output']['lifecycle']
    alert_id = lifecycle['event_id']
    require(lifecycle['action'] == 'create_event'
        and lifecycle['state'] == 'current'
        and lifecycle['episode_id']
        and len(dynamic_task['alert_events']) == 1
        and dynamic_task['alert_events'][0]['id'] == alert_id
        and dynamic_task['alert_events'][0]['validity'] == 'valid'
        and dynamic_task['alert_events'][0]['release_id'] == scope['release_id']
        and dynamic_task['alert_event_outbox'][0]['resource_refs']['alert_id'] == alert_id
        and dynamic_task['alert_event_outbox'][0]['resource_refs']['evaluation_id'] == evaluation['id']
        and dynamic_task['alert_event_outbox'][0]['resource_refs']['episode_id'] == lifecycle['episode_id']
        and dynamic_task['alert_event_outbox'][0]['resource_refs']['release_id'] == scope['release_id'],
        'Dynamic lifecycle did not save and expose one current AlertEvent.')

    notification_creation = values['notification_creation']
    notification_execution = values['notification_execution']
    notification_readback = values['notification_readback']
    require(notification_creation['mode'] == notification_execution['mode'] == 'SIMULATION_ONLY'
        and notification_creation['status'] == notification_execution['status'] == 'PASS'
        and notification_creation['database'] == notification_execution['database'] == 'sjia_simulation'
        and notification_creation['alert_id'] == notification_execution['alert_id'] == alert_id
        and notification_creation['event_id'] == dynamic_task['alert_event_outbox'][0]['id']
        and notification_creation['consumer_receipt']['consumer'] == 'native_notifications'
        and notification_creation['consumer_receipt']['event'] == notification_creation['event_id']
        and notification_creation['consumer_receipt']['effect_resource_id'] == alert_id
        and notification_creation['queue_consumption'] == 'EXACT_SINGLE_EVENT'
        and notification_creation['clinical_approval'] == 'NOT_RUN'
        and notification_creation['external_calls'] == 0
        and len(notification_creation['recipients']) == len(notification_creation['attempts']) == 1
        and len(notification_creation['receipts']) == 1,
        'AlertEvent notification creation does not match the current event or exact simulated execution.')
    require(notification_execution['mode'] == 'SIMULATION_ONLY'
        and notification_execution['status'] == 'PASS'
        and notification_execution['database'] == 'sjia_simulation'
        and notification_execution['alert_id'] == alert_id
        and notification_execution['external_calls'] == 0
        and notification_execution['clinical_approval'] == 'NOT_RUN'
        and notification_execution['queue_consumption'] == 'EXACT_SINGLE_EVENT'
        and notification_execution['event_id'] == notification_execution['requested_events'][0]['id']
        and notification_execution['requested_events'][0]['event_type'] == 'notification.native_requested'
        and notification_execution['consumer_receipt']['consumer'] == 'native_notifications'
        and notification_execution['consumer_receipt']['event'] == notification_execution['event_id']
        and notification_execution['consumer_receipt']['effect_resource_id'] == notification_execution['attempts'][0]['send_permit_id']
        and notification_execution['requested_events'][0]['task'] == notification_execution['consumer_receipt']['effect_resource_id']
        and len(notification_execution['requested_events']) == 1
        and len(notification_execution['recipients']) == 1,
        'Notification creation does not match the current AlertEvent or exact simulated event run.')
    recipient = notification_execution['recipients'][0]
    created_recipient = notification_creation['recipients'][0]
    created_attempt = notification_creation['attempts'][0]
    require(recipient['id'] == notification_execution['attempts'][0]['recipient_id']
        and recipient['id'] == created_recipient['id'] == created_attempt['recipient_id']
        and notification_execution['attempts'][0]['id'] == created_attempt['id']
        and created_attempt['publication_epoch'] == scope['publication_epoch']
        and notification_execution['receipts'][0]['id'] == notification_creation['receipts'][0]['id']
        and recipient['patient_id'] == patient_id
        and recipient['alert_id'] == alert_id
        and recipient['delivery_status'] == 'accepted'
        and notification_execution['attempts'][0]['status'] == 'accepted'
        and len(notification_execution['receipts']) == 1
        and notification_execution['receipts'][0]['status'] == 'accepted',
        'Accepted simulated notification recipient, attempt, or receipt is incomplete.')
    require(notification_readback['mode'] == 'SIMULATION_ONLY'
        and notification_readback['status'] == 'NATIVE_SEND_DELIVERED_AND_READ'
        and notification_readback['patient_id'] == patient_id
        and notification_readback['alert_id'] == alert_id
        and notification_readback['recipient_id'] == recipient['id']
        and notification_readback['fixture_sha256'] == fixture_sha256
        and notification_readback['external_calls'] == 0
        and notification_readback['model_calls'] == 0
        and notification_readback['family_api_record']['delivery_status'] == 'delivered'
        and notification_readback['family_api_record']['current_validity'] == 'valid'
        and notification_readback['family_api_record']['read_at']
        and len(notification_readback['family_api_record']['attempts']) == 1
        and {item['status'] for item in notification_readback['family_api_record']['attempts'][0]['receipts']}
            == {'accepted', 'delivered'},
        'Family API readback does not confirm accepted and delivered receipts plus read state.')
    notification_db = notification_readback['database_readback']
    family_record = notification_readback['family_api_record']
    support_record = notification_readback['support_api_record']
    read_at = notification_db['recipient']['read_at']
    require(notification_db['database'] == {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433}
        and notification_db['recipient']['id'] == recipient['id']
        and notification_db['recipient']['patient_id'] == patient_id
        and notification_db['recipient']['alert_id'] == alert_id
        and notification_db['recipient']['delivery_status'] == 'delivered'
        and read_at
        and family_record['read_at'] == read_at
        and family_record['delivery_status'] == 'delivered'
        and support_record['recipient_id'] == recipient['id']
        and support_record['patient_id'] == patient_id
        and support_record['alert_id'] == alert_id
        and support_record['delivery_status'] == 'accepted'
        and family_record['attempts'][0]['attempt_id'] == support_record['attempts'][0]['attempt_id'] == created_attempt['id']
        and {item['status'] for item in notification_db['receipts']} == {'accepted', 'delivered'}
        and {item['id'] for item in notification_db['receipts']}
            == {item['receipt_id'] for item in family_record['attempts'][0]['receipts']}
        and notification_db['attempt']['id'] == created_attempt['id']
        and notification_db['attempt']['send_permit_id'] == created_attempt['send_permit_id']
        and len(notification_db['receipts']) == 2
        and notification_db['task']['status'] == 'completed'
        and any(row['event_id'] == notification_execution['event_id'] for row in notification_db['notification_consumer_receipts'])
        and any(row['event_id'] == notification_creation['event_id'] for row in notification_db['alert_event_consumer_receipts']),
        'Persisted notification state or required ConsumerReceipt rows are incomplete.')

    summary = values['summary_readback']
    require(summary['mode'] == 'SIMULATION_ONLY'
        and summary['status'] == 'SUMMARY_CREATED_AND_READ'
        and summary['database'] == {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433}
        and summary['patient_id'] == patient_id
        and summary['business_fixture_sha256'] == fixture_sha256
        and summary['external_calls'] == 0
        and summary['model_calls'] == 0
        and summary['summary']['task_status'] == 'completed'
        and summary['summary']['task_id'] == summary['summary_task_api_readback']['task_id']
        and summary['summary']['summary_id'] == summary['summary_task_api_readback']['result_ref']
        and summary['summary']['summary_id'] == summary['summary_api_readback']['summary_id']
        and summary['summary_task_api_readback']['request_id'] == summary['request_id']
        and summary['summary']['summary_item_count'] == summary['summary']['api_visible_item_count']
        and summary['summary']['summary_needs_regeneration'] is False
        and set(summary['summary']['source_record_ids']) <= set(summary['source_record_ids']),
        'Summary API, database, patient, source, or saved readback is incomplete.')

    report = {'task_id': 'T56', 'mode': 'SIMULATION_ONLY',
        'status': 'SIMULATION_POSITIVE_PATH_SAVED_AND_READ', 'run_tag': args.run_tag,
        'patient_id': patient_id, 'scope_sha256': scope_sha256, 'fixture_sha256': fixture_sha256,
        'release_id': scope['release_id'], 'publication_epoch': scope['publication_epoch'],
        'known_at': scope['known_at'], 'assessment_at': scope['assessment_at'],
        'source_cutoff_at': scope['source_cutoff_at'],
        'result_refs': {'classification': classification['result']['id'],
            'ms': ms['result']['id'], 'evaluation': evaluation['id'],
            'alert': alert_id, 'summary': summary['summary']['summary_id']},
        'static_results': static_results,
        'dynamic': {'task_id': dynamic['task_id'], 'event_id': dynamic['event_id'],
            'evaluation_id': evaluation['id'], 'decision': evaluation['decision'],
            'validity': evaluation['validity'], 'alert_id': alert_id, 'episode_id': lifecycle['episode_id'],
            'input_count': len(dynamic['output']['rows']['inputs'])},
        'notification': {'alert_event_event_id': notification_creation['event_id'],
            'request_event_id': notification_execution['event_id'],
            'recipient_id': recipient['id'], 'attempt_id': created_attempt['id'],
            'send_permit_id': created_attempt['send_permit_id'],
            'receipt_ids': sorted(item['id'] for item in notification_db['receipts']),
            'delivery_status': 'delivered',
            'read_at': read_at,
            'receipt_statuses': sorted({item['status'] for item in notification_db['receipts']})},
        'summary': {'summary_id': summary['summary']['summary_id'],
            'task_id': summary['summary']['task_id'], 'request_id': summary['request_id'],
            'item_count': summary['summary']['summary_item_count'],
            'source_record_ids': summary['summary']['source_record_ids'],
            'source_version_ids': summary['summary']['source_version_ids'],
            'needs_regeneration': summary['summary']['summary_needs_regeneration']},
        'source_hashes': [{'label': label, 'path': str(item[0].relative_to(ROOT)), 'sha256': item[2]}
            for label, item in inputs.items()],
        'clinical_approval': 'NOT_RUN', 'business_acceptance': 'NOT_RUN',
        'device': 'NOT_RUN', 'wechat': 'NOT_RUN', 'visual': 'NOT_RUN', 'production': 'NOT_RUN',
        'external_calls': 0, 'model_calls': 0}
    output_path = WINDOW / f't56-current-scope-{args.run_tag}.json'
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    output_path.write_text(encoded, encoding='utf-8')
    require(json.loads(output_path.read_text(encoding='utf-8')) == report,
        'Saved current-scope T56 report differs from its source evidence.')
    print(json.dumps({'status': report['status'], 'run_tag': args.run_tag,
        'output': str(output_path.relative_to(ROOT)), 'saved_and_read': True,
        'source_count': len(inputs), 'patient_id': patient_id, 'alert_id': alert_id},
        ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
