import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connections, transaction
from family_content.gate_locks import read_environment_gate_shared
from family_content.models import EnvironmentGate


def database_identity():
    database = settings.DATABASES['default']
    if (database['NAME'] != 'sjia_simulation' or settings.SJIA_ENVIRONMENT != 'simulation'
            or database['HOST'] not in ('127.0.0.1', 'localhost')):
        raise ImproperlyConfigured('Competition check is restricted to local sjia_simulation.')
    with connections['default'].cursor() as cursor:
        cursor.execute('SELECT current_database(), current_schema(), current_setting(%s)', ['search_path'])
        identity = cursor.fetchone()
        cursor.execute('SHOW server_version')
        version = cursor.fetchone()[0]
    if identity != ('sjia_simulation', 'sjia', 'sjia,public'):
        raise ImproperlyConfigured('PostgreSQL database or schema identity does not match.')
    return version


def gate_state(environment):
    row = EnvironmentGate.objects.filter(environment=environment).values(
        'environment', 'enabled', 'runtime_config_id', 'active_release_id',
        'publication_epoch', 'version', 'updated_at').get()
    return {key: value.isoformat() if hasattr(value, 'isoformat') else str(value)
        for key, value in row.items()}


def hold_shared_reader(name, environment, ready, release):
    connections.close_all()
    try:
        with transaction.atomic():
            gate = read_environment_gate_shared(environment)
            with connections['default'].cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                pid = cursor.fetchone()[0]
            ready[name].update({'pid': pid, 'gate_id': gate.environment, 'version': gate.version})
            ready[name]['ready'].set()
            if not release.wait(15):
                raise TimeoutError(f'{name} did not acquire its shared row lock in time.')
            transaction.set_rollback(True)
        return {'name': name, 'pid': pid, 'transaction_rolled_back': True}
    finally:
        connections.close_all()


def acquire_writer(environment, started, acquired, release):
    connections.close_all()
    try:
        with transaction.atomic():
            with connections['default'].cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                pid = cursor.fetchone()[0]
                cursor.execute('SELECT set_config(%s, %s, true)', ['lock_timeout', '5s'])
            started.update({'pid': pid, 'time': time.perf_counter()})
            started['ready'].set()
            with connections['default'].cursor() as cursor:
                cursor.execute(
                    'UPDATE "sjia"."environment_gate" SET version = version '
                    'WHERE environment = %s RETURNING environment, version',
                    [environment],
                )
                row = cursor.fetchone()
            acquired.update({'environment': row[0], 'version': row[1], 'time': time.perf_counter()})
            acquired['ready'].set()
            if not release.wait(10):
                raise TimeoutError('Writer lock was not released in time.')
            transaction.set_rollback(True)
        return {'pid': pid, 'transaction_rolled_back': True}
    finally:
        connections.close_all()


def main():
    report_path = ROOT / 'docs/testing/T31-environment-gate-lock-simulation-report.json'
    environment = settings.SJIA_ENVIRONMENT
    postgres_version = database_identity()
    before = gate_state(environment)
    reader_release = threading.Event()
    writer_release = threading.Event()
    reader_ready = {name: {'ready': threading.Event()} for name in ('reader_a', 'reader_b')}
    writer_started = {'ready': threading.Event()}
    writer_acquired = {'ready': threading.Event()}
    executor = ThreadPoolExecutor(max_workers=3)
    readers = []
    writer = None
    wait_state = None
    try:
        readers = [executor.submit(hold_shared_reader, name, environment, reader_ready, reader_release)
            for name in ('reader_a', 'reader_b')]
        if not all(reader_ready[name]['ready'].wait(10) for name in ('reader_a', 'reader_b')):
            raise TimeoutError('Two shared readers did not acquire the gate row concurrently.')
        writer = executor.submit(acquire_writer, environment, writer_started, writer_acquired, writer_release)
        if not writer_started['ready'].wait(10):
            raise TimeoutError('Writer PostgreSQL session did not start.')
        writer_pid = writer_started['pid']
        deadline = time.perf_counter() + 3
        while time.perf_counter() < deadline:
            with connections['default'].cursor() as cursor:
                cursor.execute(
                    'SELECT wait_event_type, wait_event FROM pg_stat_activity WHERE pid = %s',
                    [writer_pid],
                )
                wait_state = cursor.fetchone()
            if wait_state and wait_state[0] == 'Lock':
                break
            if writer_acquired['ready'].is_set():
                raise AssertionError('Writer acquired FOR UPDATE while shared readers still held their locks.')
            time.sleep(0.02)
        if not wait_state or wait_state[0] != 'Lock':
            raise AssertionError('PostgreSQL did not report the writer waiting on row locks.')
        reader_release.set()
        if not writer_acquired['ready'].wait(10):
            raise TimeoutError('Writer did not acquire FOR UPDATE after reader transactions ended.')
        lock_wait_ms = (writer_acquired['time'] - writer_started['time']) * 1000
        writer_release.set()
        reader_results = [future.result(timeout=10) for future in readers]
        writer_result = writer.result(timeout=10)
        after = gate_state(environment)
        if before != after:
            raise AssertionError('Gate row changed during the rollback-only lock test.')
        if len({reader['pid'] for reader in reader_results}) != 2:
            raise AssertionError('Shared reader checks did not use two PostgreSQL sessions.')
        report = {
            'task': 'T31',
            'classification': 'SIMULATION_POSTGRESQL_ENVIRONMENT_GATE_LOCK_COMPETITION',
            'measured_at': datetime.now(timezone.utc).isoformat(),
            'runtime_versions': {'python': sys.version.split()[0], 'django': django.get_version(),
                'postgresql': postgres_version},
            'target': {'database': 'sjia_simulation', 'schema': 'sjia', 'environment': environment,
                'gate_id': before['environment'], 'simulation_only': True},
            'test': {'shared_reader_sessions': len(reader_results), 'shared_readers_acquired_concurrently': True,
                'production_helper': 'family_content.gate_locks.read_environment_gate_shared',
                'writer_operation': 'UPDATE environment_gate SET version = version RETURNING environment, version',
                'writer_wait_event': list(wait_state),
                'writer_blocked_while_shared_readers_held': True,
                'writer_acquired_after_reader_transactions_ended': True,
                'writer_lock_wait_ms': max(0, lock_wait_ms),
                'all_transactions_rolled_back': all(reader['transaction_rolled_back'] for reader in reader_results)
                    and writer_result['transaction_rolled_back'],
                'gate_state_unchanged': before == after, 'gate_dml_executed_and_rolled_back': True,
                'queue_consumption': 0, 'external_requests': 0},
            'gate_before': before,
            'gate_after': after,
            'scope': {'shared_environment_gate': 'READ_ONLY', 'production_capacity': 'NOT_RUN'},
            'source_sha256': {
                't31_environment_gate_competition.py': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'backend/family_content/gate_locks.py': hashlib.sha256(
                    (ROOT / 'backend/family_content/gate_locks.py').read_bytes()).hexdigest(),
            },
            'saved_and_reread': True,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        if json.loads(report_path.read_text()) != report:
            raise RuntimeError('Lock competition report did not match its saved contents.')
        print(json.dumps(report, ensure_ascii=False))
    finally:
        reader_release.set()
        writer_release.set()
        executor.shutdown(wait=True, cancel_futures=True)


if __name__ == '__main__':
    main()
