import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings
from django.db import connections, transaction
from core.management.commands.seed_simulation import require_simulation
from retention.models import RetentionPolicy
from retention.policy_locks import read_retention_policy_shared

require_simulation()
with connections['default'].cursor() as cursor:
    cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
    if cursor.fetchone() != ('sjia_simulation', 'sjia', 5433):
        raise RuntimeError('保留策略检查仅允许指定模拟数据库')
policy = RetentionPolicy.objects.get(policy_key='simulation-configuration_evidence', status='active')
before = dict(RetentionPolicy.objects.filter(id=policy.id).values().get())
for alias in ('retention_reader', 'retention_writer'):
    settings.DATABASES[alias] = dict(settings.DATABASES['default'])
reader_ready, writer_started, writer_acquired, release_reader = Event(), Event(), Event(), Event()


def reader():
    with transaction.atomic(using='retention_reader'):
        current = read_retention_policy_shared(policy.id, using='retention_reader')
        if current.status != policy.status:
            raise RuntimeError('并发保留策略读取结果不一致')
        reader_ready.set()
        if not release_reader.wait(5):
            raise RuntimeError('保留策略读取事务没有收到结束信号')
    connections['retention_reader'].close()


def writer():
    with transaction.atomic(using='retention_writer'):
        writer_started.set()
        RetentionPolicy.objects.using('retention_writer').select_for_update().get(id=policy.id)
        writer_acquired.set()
    connections['retention_writer'].close()


with ThreadPoolExecutor(max_workers=2) as pool:
    with transaction.atomic():
        read_retention_policy_shared(policy.id)
        future_reader = pool.submit(reader)
        if not reader_ready.wait(2):
            raise RuntimeError('第二个读取事务未能并发取得读取锁')
        future_writer = pool.submit(writer)
        if not writer_started.wait(2) or writer_acquired.wait(0.2):
            raise RuntimeError('保留策略写入锁未等待有效读取事务')
        release_reader.set()
        future_reader.result(timeout=2)
        if writer_acquired.is_set():
            raise RuntimeError('保留策略写入锁提前取得')
    future_writer.result(timeout=3)
if not writer_acquired.is_set() or dict(RetentionPolicy.objects.filter(id=policy.id).values().get()) != before:
    raise RuntimeError('保留策略锁检查或原有记录完整性不一致')
value = {'status': 'PASS', 'database': 'sjia_simulation', 'policy_id': str(policy.id),
    'parallel_readers': 'PASS', 'writer_waits_for_all_readers': 'PASS', 'record_unchanged': True,
    'visual': 'NOT_RUN'}
target = ROOT / '.planning-work/coordination/root/retention-policy-shared.json'
data = json.dumps(value, ensure_ascii=False, indent=2).encode()
target.write_bytes(data)
if target.read_bytes() != data:
    raise RuntimeError('保留策略锁检查报告保存重读不一致')
print(json.dumps({**value, 'report_sha256': hashlib.sha256(data).hexdigest()}, ensure_ascii=False))
