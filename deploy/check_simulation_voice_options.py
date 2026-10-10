import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django
import httpx
from jsonschema import Draft202012Validator

django.setup()

from django.db import connection
from core.management.commands.seed_simulation import require_simulation

require_simulation()
parser = argparse.ArgumentParser()
parser.add_argument('--voice-id')
args = parser.parse_args()
with connection.cursor() as cursor:
    cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
    if cursor.fetchone() != ('sjia_simulation', 'sjia', 5433):
        raise RuntimeError('模拟语音检查数据库身份不匹配')
fixture = json.loads((ROOT / '.simulation-work/t55-voice-fixture.json').read_text())
schema = json.loads((ROOT / 'backend-design/voice-input.schema.json').read_text())
with httpx.Client(base_url='http://127.0.0.1:8001', timeout=30, trust_env=False) as client:
    bootstrap = client.get('/api/auth/bootstrap')
    bootstrap.raise_for_status()
    client.headers['X-CSRFToken'] = bootstrap.json()['csrf_token']
    login = client.post('/api/auth/login', json={'username': fixture['family_username'],
        'password': os.environ['SJIA_SIMULATION_PASSWORD']})
    login.raise_for_status()
    client.headers['X-CSRFToken'] = login.json()['csrf_token']
    response = client.get(f'/api/v2/patients/{fixture["patient_id"]}/voice-input-options')
    target = ROOT / '.planning-work/coordination/root/voice-options-body.json'
    target.write_bytes(response.content)
    if target.read_bytes() != response.content:
        raise RuntimeError('实际选项响应保存重读不一致')
    response.raise_for_status()
    value = response.json()
    Draft202012Validator({**schema, '$ref': '#/$defs/OptionsView'}).validate(value)
    if value['state'] != 'available' or value['patient_id'] != fixture['patient_id']:
        raise RuntimeError(f'当前语音选项不可用：{value["reason_code"]}')
    originals = [item for item in value['original_options']
        if item['original_file_id'] == fixture['original_file_id']]
    if len(originals) != 1 or originals[0]['confirmed_text'] != fixture['confirmed_text']:
        raise RuntimeError('语音原件选项与实际确认全文不一致')
    original = originals[0]
    if original['state'] != 'available' or original['purpose_copy_id'] != fixture['purpose_copy_id']:
        raise RuntimeError('语音用途副本选项与实际批准结果不一致')
    if value['field_config_id'] != fixture['field_config_id'] or value['provider_config_id'] != fixture['provider_config_id']:
        raise RuntimeError('实际语音配置引用不一致')
    if args.voice_id:
        voice_response = client.get(f'/api/v2/patients/{fixture["patient_id"]}/voice-inputs/{args.voice_id}')
        voice_response.raise_for_status()
        voice = voice_response.json()
        Draft202012Validator({**schema, '$ref': '#/$defs/VoiceInputView'}).validate(voice)
        if voice['id'] != args.voice_id or voice['transcript_sha256'] != fixture['transcript_sha256']:
            raise RuntimeError('已创建语音输入读取身份或原文摘要不一致')
        voice_target = ROOT / '.planning-work/coordination/root/voice-created-read.json'
        voice_target.write_bytes(voice_response.content)
        if voice_target.read_bytes() != voice_response.content:
            raise RuntimeError('已创建语音输入响应保存重读不一致')
    print(json.dumps({'status': 'PASS', 'scope': 'SIMULATION_VOICE_OPTIONS_HTTP',
        'http_status': response.status_code, 'patient_id': value['patient_id'],
        'purpose_copy_id': original['purpose_copy_id'], 'strict_schema': 'PASS',
        'saved_reread': True, 'body_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
        'voice_read': 'PASS' if args.voice_id else 'NOT_RUN',
        'model_call': 'NOT_RUN', 'audio_transcription': 'NOT_RUN', 'visual': 'NOT_RUN'}))
