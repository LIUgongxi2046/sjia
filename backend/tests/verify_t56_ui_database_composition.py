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


def read_json(value, required_parent):
    path = (ROOT / value).resolve()
    require(path.is_relative_to(required_parent), f'输入路径不属于要求目录：{value}')
    raw = path.read_bytes()
    data = json.loads(raw)
    require(isinstance(data, dict), f'输入报告必须为JSON对象：{value}')
    return path, data, hashlib.sha256(raw).hexdigest()


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def main():
    parser = argparse.ArgumentParser(description='组合核验T56模拟页面、数据库保存重读与取消请求报告')
    parser.add_argument('--run-tag', required=True)
    parser.add_argument('--fixture-file', required=True)
    parser.add_argument('--business-report', required=True)
    parser.add_argument('--ui-report', required=True)
    parser.add_argument('--database-report', required=True)
    parser.add_argument('--cancellation-report', required=True)
    args = parser.parse_args()
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{2,47}', args.run_tag) is not None,
        '运行标识须由小写字母、数字和连字符组成。')

    sources = {
        'fixture': read_json(args.fixture_file, ROOT / '.simulation-work'),
        'business': read_json(args.business_report, WINDOW),
        'ui': read_json(args.ui_report, ROOT / '.planning-work' / 'parallel' / 'window2' / 'evidence'),
        'database': read_json(args.database_report, ROOT / '.planning-work' / 'coordination' / 'root'),
        'cancellation': read_json(args.cancellation_report, ROOT / '.planning-work' / 'coordination' / 'root'),
    }
    reports = {key: item[1] for key, item in sources.items()}
    hashes = {key: item[2] for key, item in sources.items()}
    fixture = reports['fixture']
    business = reports['business']
    ui = reports['ui']
    database = reports['database']
    cancellation = reports['cancellation']
    patient_id = business['patient_id']
    alert_id = business['result_refs']['alert']
    recipient_id = business['notification']['recipient_id']
    attempt_id = business['notification']['attempt_id']

    require(fixture['mode'] == 'SIMULATION_ONLY'
        and fixture['scenario'] == 'INDEPENDENT_FIRST_ALERT'
        and fixture['clinical_approval'] == 'NOT_RUN'
        and fixture['patient_id'] == patient_id
        and business['fixture_sha256'] == hashes['fixture'],
        '模拟fixture与业务报告不一致。')
    require(business['task_id'] == 'T56'
        and business['mode'] == 'SIMULATION_ONLY'
        and business['status'] == 'SIMULATION_POSITIVE_PATH_SAVED_AND_READ'
        and business['clinical_approval'] == 'NOT_RUN'
        and business['patient_id'] == ui['patient_id'],
        '既有业务报告与页面报告的模拟范围或患者不一致。')
    require(ui['mode'] == 'SIMULATION_ONLY'
        and ui['task_id'] == 'T56_UI2_NOTIFICATION_SUMMARY_SUPPLEMENT'
        and ui['status'] == 'PASS'
        and ui['origin'] in {'http://127.0.0.1:5183', 'http://127.0.0.1:5184'}
        and ui['account'] == fixture['family_username']
        and ui['patient_id'] == patient_id
        and ui['database_crosscheck'] == 'PENDING_MAIN_WINDOW_READBACK'
        and ui['page_errors'] == []
        and ui['visual'] == ui['actual_device'] == ui['family_observation'] == ui['wechat'] == 'NOT_RUN',
        '页面报告不满足T56模拟范围或未通过条件。')

    required_checks = {
        'UI2-12 正文、来源、联系、阅读保存与刷新重读',
        'UI2-39 授权范围、接收人、回执与刷新重读',
        'UI2-13/14 摘要保存、Task、来源版本与刷新重读',
        '已送达通知重新读取保持原回执且没有重复确认',
    }
    check_results = {item['name']: item['result'] for item in ui['checks']}
    require(required_checks <= set(check_results)
        and all(check_results[name] == 'PASS' for name in required_checks)
        and all(item['result'] == 'PASS' for item in ui['checks']),
        '页面报告缺少必要交互检查或有未通过项。')

    notification = ui['notification_reference']
    family_record = notification['family_api_record']
    support_record = ui['support']['record']
    summary = ui['summary']['summary']
    task = ui['summary']['task']
    request = ui['summary']['request']
    require(notification['patient_id'] == patient_id
        and notification['fixture_sha256'] == hashes['fixture']
        and notification['alert_id'] == alert_id
        and notification['recipient_id'] == recipient_id
        and notification['attempt_id'] == attempt_id
        and family_record['delivery_status'] == business['notification']['delivery_status'] == 'delivered'
        and set(business['notification']['receipt_statuses']) == {'accepted', 'delivered'}
        and family_record['read_at'] == business['notification']['read_at']
        and len(family_record['attempts']) == 1
        and family_record['attempts'][0]['attempt_id'] == attempt_id
        and {item['status'] for item in family_record['attempts'][0]['receipts']} == {'accepted', 'delivered'}
        and {item['receipt_id'] for item in family_record['attempts'][0]['receipts']}
            == set(business['notification']['receipt_ids'])
        and support_record['recipient_id'] == recipient_id
        and support_record['alert_id'] == alert_id
        and support_record['delivery_status'] == 'delivered'
        and len(support_record['attempts']) == 1
        and support_record['attempts'][0]['attempt_id'] == attempt_id
        and support_record['attempts'][0]['status'] == 'accepted'
        and {item['status'] for item in support_record['attempts'][0]['receipts']} == {'accepted', 'delivered'}
        and ui['support']['record_after_refresh'] == support_record
        and ui['alert']['alert_id'] == alert_id
        and ui['alert']['initial']['validity'] == 'valid'
        and ui['alert']['initial']['content_state'] == 'available'
        and ui['alert']['read_after_page']['read_at']
        and ui['alert']['read_after_refresh']['read_at'] == ui['alert']['read_after_page']['read_at']
        and ui['alert']['read_after_refresh']['content']['source_refs']
            == ui['alert']['initial']['content']['source_refs']
        and ui['alert']['notification_after_refresh'] == ui['alert']['notification_before'],
        '页面提示、通知详情、阅读状态与既有业务对象不一致。')
    require(ui['summary']['status'] == 'PASS'
        and summary['summary_id'] == ui['summary']['summary_after_refresh']['summary_id']
        and summary['task_id'] == task['task_id'] == ui['summary']['task_after_refresh']['task_id']
        and task['status'] == 'completed'
        and task['result_ref'] == summary['summary_id']
        and request['request_id'] == task['request_id']
        and ui['summary']['summary_after_refresh'] == summary
        and ui['summary']['task_after_refresh'] == task
        and summary['patient_id'] == patient_id
        and summary['validity'] == 'valid'
        and summary['needs_regeneration'] is False
        and bool(summary['items'])
        and set(ui['summary']['source_version_ids']) == {item['record_version_id'] for item in summary['items']},
        '页面摘要、Task、请求、来源版本或刷新重读不一致。')

    required_responses = {
        ('GET', f'/api/alerts/{alert_id}', 200),
        ('GET', f'/api/v2/alerts/{alert_id}/notification', 200),
        ('GET', '/api/v2/support/notification-scopes', 200),
        ('GET', f'/api/v2/support/patients/{patient_id}/notification-records', 200),
        ('GET', f'/api/v2/support/notification-records/{recipient_id}', 200),
        ('POST', f'/api/patients/{patient_id}/summaries', 202),
        ('GET', f'/api/summaries/{summary["summary_id"]}', 200),
        ('GET', f'/api/tasks/{task["task_id"]}', 200),
    }
    actual_responses = {(item['method'], item['path'], item['status']) for item in ui['page_api_responses']}
    require(required_responses <= actual_responses, '页面API报告缺少实际成功响应。')
    request_reads = [item for item in ui['api_reads']
        if item['method'] == 'GET' and item['path'] == f'/api/requests/{request["intent_id"]}']
    successful_request_reads = [item for item in request_reads if item.get('status') == 200]
    require(bool(successful_request_reads)
        and all(item['body'] == request for item in successful_request_reads),
        '独立请求读取未按intent_id返回页面记录的准确请求内容。')
    require(not any(item['method'] == 'POST'
            and item['path'] == f'/api/v2/notification-records/{recipient_id}/delivered'
            for item in ui['api_mutations']),
        '已送达通知页面重复提交了送达确认。')

    require(database['task_id'] == 'T56'
        and database['mode'] == 'SIMULATION_ONLY'
        and database['status'] == 'PASS'
        and database['database'] == {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433}
        and database['read_only'] is True
        and database['saved_and_read'] is True
        and database['ui_report'] == str(sources['ui'][0].relative_to(ROOT))
        and database['ui_sha256'] == hashes['ui']
        and database['patient_id'] == patient_id
        and database['summary_id'] == summary['summary_id']
        and database['summary_task_id'] == task['task_id']
        and database['request_id'] == request['request_id']
        and database['summary_item_count'] == len(summary['items'])
        and set(database['source_version_ids']) == {item['record_version_id'] for item in summary['items']}
        and database['recipient_id'] == recipient_id
        and database['attempt_id'] == attempt_id
        and set(database['receipt_ids']) == set(business['notification']['receipt_ids'])
        and timestamp(database['read_at']) == timestamp(family_record['read_at']),
        'PostgreSQL只读报告未证明页面所示对象已保存并重新读取。')

    require(cancellation['mode'] == 'SIMULATION_ONLY'
        and cancellation['status'] == 'PASS'
        and cancellation['target'] == f'/api/patients/{patient_id}/home'
        and cancellation['error_name'] == 'AbortError'
        and cancellation['request_count'] == 0
        and cancellation['saved_and_read'] is True
        and cancellation['visual'] == cancellation['actual_device'] == cancellation['wechat'] == 'NOT_RUN',
        '取消请求报告未确认终止前没有发出私有网络请求。')

    output = WINDOW / f't56-ui-database-composition-{args.run_tag}.json'
    require(not output.exists(), f'组合报告已存在：{output.relative_to(ROOT)}')
    report = {
        'task_id': 'T56',
        'mode': 'SIMULATION_ONLY',
        'status': 'SIMULATION_UI_DATABASE_COMPOSITION_PASS',
        'run_tag': args.run_tag,
        'family_username': fixture['family_username'],
        'patient_id': patient_id,
        'alert_id': alert_id,
        'recipient_id': recipient_id,
        'attempt_id': attempt_id,
        'summary_id': summary['summary_id'],
        'summary_task_id': task['task_id'],
        'summary_request_id': request['request_id'],
        'summary_item_count': len(summary['items']),
        'database': database['database'],
        'saved_and_read': True,
        'cancelled_request_count': cancellation['request_count'],
        'page_checks': [{'name': name, 'result': check_results[name]} for name in sorted(required_checks)],
        'task_coverage': [
            {
                'task_id': 'T29',
                'scope': 'scope04 classification, MS, dynamic alert, notification, and summary business chain',
                'evidence_report': str(sources['business'][0].relative_to(ROOT)),
                'evidence_status': business['status'],
                'original_acceptance': 'NOT_RUN',
            },
            {
                'task_id': 'T56',
                'scope': 'UI2-12, UI2-39, UI2-13/14, simulation database readback, and cancelled request',
                'evidence_report': str(sources['ui'][0].relative_to(ROOT)),
                'evidence_status': 'SIMULATION_UI_DATABASE_COMPOSITION_PASS',
                'original_acceptance': 'NOT_RUN',
            },
        ],
        'source_reports': [
            {'label': key, 'path': str(value[0].relative_to(ROOT)), 'sha256': hashes[key]}
            for key, value in sources.items()
        ],
        'clinical': 'NOT_RUN',
        'visual': 'NOT_RUN',
        'actual_device': 'NOT_RUN',
        'wechat': 'NOT_RUN',
        'production': 'NOT_RUN',
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    output.write_text(encoded, encoding='utf-8')
    require(json.loads(output.read_text(encoding='utf-8')) == report,
        'T56组合报告保存后重新读取不一致。')
    print(json.dumps({'status': report['status'], 'run_tag': args.run_tag,
        'output': str(output.relative_to(ROOT)), 'saved_and_read': True,
        'patient_id': patient_id, 'summary_id': summary['summary_id']},
        ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
