import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings
from django.db import connection, transaction


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', default=str(ROOT / 'docs/testing/T31-connection-budget-simulation-report.json'))
    args = parser.parse_args()
    database = settings.DATABASES['default']
    if (database['NAME'] != 'sjia_simulation'
            or settings.SJIA_ENVIRONMENT != 'simulation'
            or database['HOST'] not in ('127.0.0.1', 'localhost')):
        raise RuntimeError('连接预算检查只允许本机 sjia_simulation 模拟数据库。')
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION READ ONLY')
        cursor.execute("SELECT current_database(), current_schema(), current_setting('search_path'), current_setting('server_version')")
        identity = cursor.fetchone()
        if identity[:2] != ('sjia_simulation', 'sjia') or identity[2].replace(' ', '') != 'sjia,public':
            raise RuntimeError('连接预算检查数据库身份不符。')
        cursor.execute("SELECT current_setting('max_connections')::integer, current_setting('superuser_reserved_connections')::integer, current_setting('reserved_connections')::integer")
        limits = cursor.fetchone()
        cursor.execute("SELECT count(*) FILTER (WHERE datname = current_database()), count(*) FILTER (WHERE datname = current_database() AND state = 'active'), count(*) FILTER (WHERE datname = current_database() AND state = 'idle'), count(*) FILTER (WHERE datname = current_database() AND state = 'idle in transaction'), count(*) FILTER (WHERE datname = current_database() AND state IS NULL), count(*) FROM pg_stat_activity")
        counts = cursor.fetchone()
        cursor.execute("SELECT coalesce(application_name, ''), coalesce(state, 'unknown'), count(*) FROM pg_stat_activity WHERE datname = current_database() GROUP BY application_name, state ORDER BY application_name, state")
        groups = cursor.fetchall()
        cursor.execute("SELECT pg_database_size(current_database())")
        database_size = cursor.fetchone()[0]
    usable_connections = limits[0] - limits[1] - limits[2]
    report = {
        'task': 'T31',
        'classification': 'SIMULATION_READ_ONLY_CONNECTION_BUDGET',
        'measured_at': datetime.now(timezone.utc).isoformat(),
        'runtime': {'django': django.get_version(), 'postgresql': identity[3]},
        'database': {'name': identity[0], 'schema': identity[1], 'search_path': identity[2], 'port': database['PORT'], 'size_bytes': database_size},
        'limits': {'max_connections': limits[0], 'superuser_reserved_connections': limits[1], 'reserved_connections': limits[2], 'usable_non_reserved_connections': usable_connections},
        'observed_sessions': {'database_total': counts[0], 'database_active': counts[1], 'database_idle': counts[2], 'database_idle_in_transaction': counts[3], 'database_without_state': counts[4], 'server_total': counts[5], 'by_application_and_state': [{'application_name': row[0], 'state': row[1], 'count': row[2]} for row in groups]},
        'application_budget': {'django_conn_max_age_seconds': database['CONN_MAX_AGE'], 'persistent_connections_enabled': database['CONN_MAX_AGE'] > 0, 'api_workers': 'NOT_PROVIDED', 'worker_processes': 'NOT_STARTED', 'beat_processes': 'NOT_STARTED', 'pool_max_size': 'NOT_CONFIGURED'},
        'capacity_assessment': {'simulation_only': True, 'observed_sessions_are_snapshot': True, 'approved_process_counts': 'NOT_PROVIDED', 'production_capacity': 'NOT_RUN', 'capacity_pass': 'NOT_DETERMINED'},
        'database_operations': {'transactions_written': 0, 'services_started_or_stopped': 0, 'worker_or_beat_started': False, 'queue_consumption': 0},
        'saved_and_reread': True,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if json.loads(report_path.read_text()) != report:
        raise RuntimeError('连接预算报告保存后回读不一致。')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
