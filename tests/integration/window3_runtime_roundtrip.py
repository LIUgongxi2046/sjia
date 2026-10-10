import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import django
from django.conf import settings
from django.db import connection, transaction


django.setup()

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIRECTORY = ROOT / '.planning-work' / 'parallel' / 'window3'
API_ROOT = 'http://127.0.0.1:8013/api/v1'


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest_json(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


if settings.SJIA_ENVIRONMENT != 'window3_simulation':
    raise RuntimeError('本程序仅允许本窗口模拟环境')

with connection.cursor() as cursor:
    cursor.execute('SELECT current_database(), inet_server_port(), current_user')
    database_name, database_port, database_user = cursor.fetchone()
if (database_name, database_port, database_user) != ('sjia_simulation', 5545, 'window3_owner'):
    raise RuntimeError('本程序拒绝连接预期之外的数据库')

account_name = f'window3_sim_{uuid.uuid4().hex}'
account_password = secrets.token_urlsafe(32)
with httpx.Client(base_url=API_ROOT, timeout=20.0) as client:
    bootstrap_response = client.get('/auth/bootstrap')
    bootstrap_response.raise_for_status()
    csrf_token = bootstrap_response.json()['csrf_token']
    register_response = client.post('/auth/register', json={
        'username': account_name,
        'password': account_password,
    }, headers={'X-CSRFToken': csrf_token})
    register_response.raise_for_status()
    registered = register_response.json()
    session_response = client.get('/session')
    session_response.raise_for_status()
    session = session_response.json()
if register_response.status_code != 201:
    raise RuntimeError('模拟账号注册没有返回创建状态')
if registered['principal_id'] != session['principal_id']:
    raise RuntimeError('API 会话重新读取主体与注册回执不一致')

report_names = (
    'T55-simulation-evaluation.json',
    'T59-simulation-evaluation.json',
    'T32-simulation-evaluation.json',
)
report_rows = []
for report_name in report_names:
    report_path = REPORT_DIRECTORY / report_name
    payload = json.loads(report_path.read_text(encoding='utf-8'))
    artifact_id = uuid.uuid4()
    payload_sha256 = digest_json(payload)
    report_rows.append((artifact_id, report_name, payload_sha256, payload))

with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('CREATE SCHEMA IF NOT EXISTS window3')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS window3.evaluation_artifact (
                artifact_id uuid PRIMARY KEY,
                report_name text NOT NULL,
                payload_sha256 char(64) NOT NULL,
                payload jsonb NOT NULL,
                saved_at timestamptz NOT NULL
            )
        ''')
        for artifact_id, report_name, payload_sha256, payload in report_rows:
            cursor.execute(
                'INSERT INTO window3.evaluation_artifact '
                '(artifact_id, report_name, payload_sha256, payload, saved_at) '
                'VALUES (%s, %s, %s, %s, %s)',
                (artifact_id, report_name, payload_sha256, json.dumps(payload), datetime.now(timezone.utc)),
            )

saved_and_read = []
with connection.cursor() as cursor:
    for artifact_id, report_name, expected_sha256, expected_payload in report_rows:
        cursor.execute(
            'SELECT report_name, payload_sha256, payload FROM window3.evaluation_artifact '
            'WHERE artifact_id = %s',
            (artifact_id,),
        )
        reread = cursor.fetchone()
        if reread is None:
            raise RuntimeError(f'数据库没有重新读取已保存报告：{report_name}')
        actual_name, actual_sha256, actual_payload = reread
        if actual_name != report_name or actual_sha256 != expected_sha256:
            raise RuntimeError(f'数据库重新读取的报告标识或摘要不一致：{report_name}')
        actual_payload = json.loads(actual_payload)
        if actual_payload != expected_payload or digest_json(actual_payload) != expected_sha256:
            raise RuntimeError(f'数据库重新读取的报告内容不一致：{report_name}')
        saved_and_read.append({
            'artifact_id': str(artifact_id),
            'report_name': report_name,
            'payload_sha256': actual_sha256,
            'saved_and_reread': True,
        })

evidence = {
    'schema_version': 'window3-runtime-roundtrip-1',
    'data_mode': 'SIMULATION',
    'database': database_name,
    'database_port': database_port,
    'database_user': database_user,
    'api_base': API_ROOT,
    'api_bootstrap_status': bootstrap_response.status_code,
    'api_registration_status': register_response.status_code,
    'api_session_read_status': session_response.status_code,
    'simulated_principal_id': session['principal_id'],
    'account_password_recorded': False,
    'database_artifacts': saved_and_read,
    'generated_at_utc': datetime.now(timezone.utc).isoformat(),
    'scope_limitations': [
        '账号会话通过独立 API 注册并重新读取；评价报告通过独立 PostgreSQL 表保存并重新读取。',
        'T55 报告候选的采用、WriteIntent 保存、RecordVersion 重读另见共享模拟环境的 T55 API 与只读数据库证据。',
        '本次没有执行 Redis 任务，也没有发起新模型调用。',
    ],
}
output_path = REPORT_DIRECTORY / 'runtime-roundtrip-evidence.json'
output_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(evidence, ensure_ascii=False))
