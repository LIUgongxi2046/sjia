import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.db import connection, transaction
from django.utils import timezone
from alerting.models import AlertEpisode
from core.management.commands.seed_simulation import require_simulation
from family_content.models import EnvironmentGate
from identity.models import Patient
from prepare_simulation_assessment import prepare, read_saved

require_simulation()
with connection.cursor() as cursor:
    cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
    if cursor.fetchone() != ('sjia_simulation', 'sjia', 5433):
        raise RuntimeError('首次提示场景仅允许准确模拟数据库')
target = ROOT / '.simulation-work/t29-first-alert-business-fixture.json'
source_path = ROOT / '.simulation-work/t29-business-fixture.json'
source = json.loads(source_path.read_text())
before_gate = dict(EnvironmentGate.objects.filter(environment='simulation').values().get())
before_episode = dict(AlertEpisode.objects.filter(id='8c39107e-7bf3-4787-98f8-f02e95685d07').values().get())
if target.exists():
    saved = json.loads(target.read_text())
    patient = Patient.objects.get(id=saved['patient_id'], display_id='SIMULATION独立首次提示患儿C', status='active')
    if saved['family_username'] != 'simulation-family-first-alert' or saved['scenario'] != 'INDEPENDENT_FIRST_ALERT':
        raise RuntimeError('首次提示场景目录身份不匹配')
else:
    with transaction.atomic():
        patient, _ = Patient.objects.get_or_create(display_id='SIMULATION独立首次提示患儿C',
            defaults={'status': 'active', 'created_at': timezone.now()})
        saved = prepare(patient_id=patient.id, family_username='simulation-family-first-alert',
            target=target, reuse_case=source)
if AlertEpisode.objects.filter(patient=patient).exists():
    raise RuntimeError('首次提示场景已进入周期，禁止再次初始化')
read_saved(saved, patient.id)
saved['release_id'] = str(before_gate['active_release_id'])
saved['publication_epoch'] = before_gate['publication_epoch']
target.write_text(json.dumps(saved, ensure_ascii=False, indent=2))
if json.loads(target.read_text()) != saved:
    raise RuntimeError('首次提示业务资料清单保存重读不一致')
if (dict(EnvironmentGate.objects.filter(environment='simulation').values().get()) != before_gate
        or dict(AlertEpisode.objects.filter(id=before_episode['id']).values().get()) != before_episode):
    raise RuntimeError('首次提示初始化改变既有发布或周期')
report = {'status': 'PASS', 'mode': 'SIMULATION_ONLY', 'scenario': 'INDEPENDENT_FIRST_ALERT',
    'checked_at': timezone.now().isoformat(), 'patient_id': str(patient.id),
    'family_username': saved['family_username'], 'business_fixture_path': str(target.relative_to(ROOT)),
    'business_fixture_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
    'template_sha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
    'original_file_ids': saved['file_ids'], 'record_versions': saved['records'],
    'verification_ids': saved['verification_ids'], 'field_count': sum(map(len, saved['fact_ids'])),
    'current_release_id': str(before_gate['active_release_id']),
    'current_publication_epoch': before_gate['publication_epoch'],
    'existing_episode_unchanged': True, 'publication_unchanged': True,
    'clinical_independence': 'NOT_CLAIMED', 'clinical': 'NOT_RUN', 'visual': 'NOT_RUN'}
output = ROOT / '.planning-work/coordination/root/first-alert-case-preparation.json'
output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
if json.loads(output.read_text()) != report:
    raise RuntimeError('首次提示准备报告保存重读不一致')
print(json.dumps(report, ensure_ascii=False))
