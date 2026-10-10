import argparse
import hashlib
import json
import os
import re
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]
API_BASE = 'http://127.0.0.1:8001'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + '\n'
    path.write_text(encoded, encoding='utf-8')
    require(json.loads(path.read_text(encoding='utf-8')) == json.loads(encoded),
        f'Saved report readback differs: {path.name}.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-tag', required=True)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--kind', required=True, choices=('classification', 'ms'))
    args = parser.parse_args()
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{2,47}', args.run_tag) is not None,
        'Run tag must be a unique lowercase alphanumeric label with optional hyphens.')
    state_path = ROOT / '.planning-work' / 'parallel' / 'window1' / f't29-static-task-{args.run_tag}-state.json'
    db_readback_path = ROOT / '.planning-work' / 'parallel' / 'window1' / f't29-static-task-{args.run_tag}-readback.json'
    parent_report = ROOT / '.planning-work' / 'coordination' / 'root' / f'task-{args.task_id}.json'
    state = json.loads(state_path.read_text(encoding='utf-8'))
    db_readback = json.loads(db_readback_path.read_text(encoding='utf-8'))
    execution = json.loads(parent_report.read_text(encoding='utf-8'))
    require(execution['task_id'] == args.task_id and execution['database'] == 'sjia_simulation'
        and execution['status'] == 'PASS' and execution['task_status'] == 'completed',
        'Single-event execution report does not confirm this completed simulation Task.')
    if args.kind == 'classification':
        saved_task = db_readback['tasks']['classification']
        saved_task_id = saved_task['task_id']
        saved_input_sha256 = saved_task['task_input_sha256']
    else:
        saved_task = db_readback['tasks']['ms']
        saved_task_id = saved_task['task_id']
        saved_input_sha256 = saved_task['task_input_sha256']
    require(saved_task_id == args.task_id and execution['input_sha256'] == saved_input_sha256,
        'Execution report input digest differs from the saved static Task state.')
    with httpx.Client(base_url=API_BASE, timeout=45, trust_env=False) as client:
        bootstrap = client.get('/api/auth/bootstrap')
        require(bootstrap.status_code == 200, f'Simulation API bootstrap returned HTTP {bootstrap.status_code}.')
        client.headers['X-CSRFToken'] = bootstrap.json()['csrf_token']
        login = client.post('/api/v2/support/session/login', json={
            'username': 'simulation-support', 'password': os.environ['SJIA_SIMULATION_PASSWORD']})
        require(login.status_code == 200 and login.json().get('audience') == 'support',
            f'Support login returned HTTP {login.status_code}: {login.text}')
        client.headers['X-CSRFToken'] = login.json()['csrf_token']
        task_response = client.get(f'/api/v2/support/assessment-tasks/{args.task_id}')
        if task_response.status_code != 200:
            report = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY',
                'status': 'ASSESSMENT_TASK_API_REJECTED',
                'database': {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433},
                'api_base': API_BASE, 'run_tag': args.run_tag, 'known_at': state['known_at'],
                'kind': args.kind, 'request_id': state['tasks'][args.kind]['request_id'],
                'task_id_value': args.task_id,
                'task_request_path': f'/api/v2/support/assessment-tasks/{args.task_id}',
                'task_http_status': task_response.status_code,
                'task_response': task_response.json(),
                'execution_report_sha256': hashlib.sha256(parent_report.read_bytes()).hexdigest(),
                'model_calls': 0, 'external_calls': 0}
            output_path = ROOT / '.planning-work' / 'parallel' / 'window1' / f't29-static-task-{args.run_tag}-api-readback.json'
            save_json(output_path, report)
            print(json.dumps({'status': report['status'], 'task_id': args.task_id,
                'http_status': task_response.status_code, 'response': report['task_response'],
                'saved_and_read': True, 'report_path': str(output_path)}, sort_keys=True))
            return
        task = task_response.json()
        require(task['task_id'] == args.task_id and task['status'] == 'completed'
            and task['result_type'] == 'assessment', 'Task API read did not confirm the completed result.')
        result_response = client.get(f'/api/v2/support/assessments/{task["result_ref"]}')
        if result_response.status_code != 200:
            report = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY',
                'status': 'ASSESSMENT_RESULT_API_REJECTED',
                'database': {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433},
                'api_base': API_BASE, 'run_tag': args.run_tag, 'known_at': state['known_at'],
                'kind': args.kind, 'request_id': state['tasks'][args.kind]['request_id'],
                'task_id_value': args.task_id, 'task_api_readback': task,
                'result_request_path': f'/api/v2/support/assessments/{task["result_ref"]}',
                'result_http_status': result_response.status_code,
                'result_response': result_response.json(),
                'execution_report_sha256': hashlib.sha256(parent_report.read_bytes()).hexdigest(),
                'model_calls': 0, 'external_calls': 0}
            output_path = ROOT / '.planning-work' / 'parallel' / 'window1' / f't29-static-task-{args.run_tag}-api-readback.json'
            save_json(output_path, report)
            print(json.dumps({'status': report['status'], 'task_id': args.task_id,
                'http_status': result_response.status_code, 'response': report['result_response'],
                'saved_and_read': True, 'report_path': str(output_path)}, sort_keys=True))
            return
        result = result_response.json()
        require(result['assessment_id'] == task['result_ref'], 'Assessment API read returned another result identity.')
        if args.kind == 'classification':
            require(result['output']['classification_result'] == execution['output']['classification_result'],
                'Classification API result differs from the exact single-event execution result.')
        else:
            require(result['output']['ms_contributions'] == execution['output']['ms_contributions']
                and result['output']['ms_total'] == execution['output']['ms_total']
                and result['output']['ms_threshold_result'] == execution['output']['ms_threshold_result'],
                'MS API contributions, total, or threshold differ from the exact single-event execution result.')
        report = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY', 'status': 'STATIC_TASK_AND_RESULT_API_READBACK',
            'database': {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433},
            'api_base': API_BASE, 'run_tag': args.run_tag, 'known_at': state['known_at'],
            'kind': args.kind, 'request_id': state['tasks'][args.kind]['request_id'],
            'task_input_sha256': execution['input_sha256'], 'event_id': execution['event_id'],
            'task_api_readback': task, 'result_api_readback': result,
            'execution_report_sha256': hashlib.sha256(parent_report.read_bytes()).hexdigest(),
            'model_calls': 0, 'external_calls': 0}
    output_path = ROOT / '.planning-work' / 'parallel' / 'window1' / f't29-static-task-{args.run_tag}-api-readback.json'
    save_json(output_path, report)
    print(json.dumps({'status': report['status'], 'task_id': args.task_id,
        'result_id': report['result_api_readback']['assessment_id'],
        'kind': args.kind, 'result': report['result_api_readback']['output'],
        'saved_and_read': True, 'report_path': str(output_path)}, sort_keys=True))


if __name__ == '__main__':
    main()
