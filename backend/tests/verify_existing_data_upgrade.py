import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform

import django
from psycopg import sql

django.setup()

from django.conf import settings
from django.apps import apps
from django.core.management.base import no_translations
from django.db import connection, transaction
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.state import ProjectState
from configurations.evidence import backend_artifact_manifest
from configurations.service import compute_checksum
from archives.models import ArchiveFact, ArchiveFactVersion


parser = argparse.ArgumentParser(description='核验真实资料恢复、正式升级及全部原有表内容保持')
parser.add_argument('--snapshot', required=True, type=Path)
parser.add_argument('--snapshot-sha256', required=True)
parser.add_argument('--framework', required=True, type=Path)
parser.add_argument('--target-database', required=True)
parser.add_argument('--major', required=True, choices=('18', '17'))
parser.add_argument('--phase', required=True, choices=('restored', 'migrated'))
parser.add_argument('--output', required=True, type=Path)
arguments = parser.parse_args()
os.umask(0o077)


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def snapshot_table(cursor, schema, name):
    require(schema in ('public', 'sjia'), 'EXISTING_UPGRADE_TABLE_SCOPE_INVALID')
    query = sql.SQL('SELECT row_to_json(item)::text FROM {} item ORDER BY row_to_json(item)::text COLLATE "C"').format(sql.Identifier(schema, name))
    cursor.execute(query)
    measured = hashlib.sha256()
    rows = []
    for (row,) in cursor.fetchall():
        content = row.encode()
        measured.update(len(content).to_bytes(8, 'big'))
        measured.update(content)
        rows.append(row)
    return {'schema': schema, 'table': name, 'count': len(rows), 'sha256': measured.hexdigest()}, rows


@no_translations
def verify_current_model_state():
    executor = MigrationExecutor(connection)
    require(not executor.migration_plan(executor.loader.graph.leaf_nodes()), 'EXISTING_UPGRADE_PENDING_MIGRATIONS')
    require(not MigrationAutodetector(executor.loader.project_state(), ProjectState.from_apps(apps)).changes(graph=executor.loader.graph),
        'EXISTING_UPGRADE_ORM_STATE_MISMATCH')


require(not arguments.output.exists(), 'EXISTING_UPGRADE_REPORT_ALREADY_EXISTS')
require(hashlib.sha256(arguments.snapshot.read_bytes()).hexdigest() == arguments.snapshot_sha256,
    'EXISTING_UPGRADE_EXACT_SOURCE_SNAPSHOT_REQUIRED')
baseline = json.loads(arguments.snapshot.read_bytes())
framework = json.loads(arguments.framework.read_bytes())
require(baseline['state'] == 'CAPTURED' and framework['result'] == 'PASS'
    and framework['snapshot_sha256'] == arguments.snapshot_sha256, 'EXISTING_UPGRADE_CAPTURED_INPUTS_REQUIRED')
expected_migrations = {('files', '0003_s3storedobject'), ('retention', '0010_s3contentrestorestate'),
    ('retention', '0011_s3_restore_generations')}
framework_names = set(framework['rows'])
verified_tables = {}
actual_framework = {}
with transaction.atomic(), connection.cursor() as cursor:
    cursor.execute('SHOW transaction_read_only')
    require(cursor.fetchone() == ('on',), 'EXISTING_UPGRADE_VERIFIER_MUST_BE_READ_ONLY')
    cursor.execute('SELECT current_database(),current_user,system_identifier::text,current_setting(\'server_version\') FROM pg_control_system()')
    identity = cursor.fetchone()
    require(identity[0] == arguments.target_database and identity[0] != baseline['source']
        and identity[2] != baseline['cluster_id'] and identity[3].startswith(arguments.major + '.'),
        'EXISTING_UPGRADE_EXACT_INDEPENDENT_DATABASE_REQUIRED')
    cursor.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user')
    require(cursor.fetchone() == (False, False, False), 'EXISTING_UPGRADE_NON_SUPERUSER_REQUIRED')
    cursor.execute("SET LOCAL TIME ZONE 'UTC'")
    for key, expected in baseline['tables'].items():
        observed, rows = snapshot_table(cursor, expected['schema'], expected['table'])
        if arguments.phase == 'migrated' and key in framework_names:
            require(set(framework['rows'][key]) <= set(rows), 'EXISTING_UPGRADE_ORIGINAL_FRAMEWORK_ROW_CHANGED:' + key)
            actual_framework[key] = [json.loads(row) for row in set(rows) - set(framework['rows'][key])]
        else:
            require(observed == expected, 'EXISTING_UPGRADE_ORIGINAL_TABLE_CHANGED:' + key)
        verified_tables[key] = observed
    cursor.execute("SELECT n.nspname||'.'||c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname IN ('public','sjia') AND c.relkind IN ('r','p')")
    tables = {row[0] for row in cursor.fetchall()}
    extra_tables = tables - set(baseline['tables'])
    if arguments.phase == 'restored':
        require(not extra_tables, 'EXISTING_UPGRADE_RESTORE_MUST_NOT_ADD_TABLES')
    else:
        require(extra_tables == {'sjia.s3_stored_object', 'sjia.s3_content_restore_state'},
            'EXISTING_UPGRADE_ONLY_FORMAL_NEW_TABLES_ALLOWED')
        new_migrations = actual_framework[framework['keys']['django_migrations']]
        require({(row['app'], row['name']) for row in new_migrations} == expected_migrations and len(new_migrations) == 3,
            'EXISTING_UPGRADE_EXACT_THREE_FORMAL_MIGRATIONS_REQUIRED')
        content_types = actual_framework[framework['keys']['django_content_type']]
        labels = {(row['app_label'], row['model']) for row in content_types}
        require(labels == {('files', 's3storedobject'), ('retention', 's3contentrestorestate')} and len(content_types) == 2,
            'EXISTING_UPGRADE_EXACT_NEW_CONTENT_TYPES_REQUIRED')
        expected_permissions = {(row['id'], action + '_' + row['model']) for row in content_types
            for action in ('add', 'change', 'delete', 'view')}
        permissions = actual_framework[framework['keys']['auth_permission']]
        require({(row['content_type_id'], row['codename']) for row in permissions} == expected_permissions and len(permissions) == 8,
            'EXISTING_UPGRADE_EXACT_FRAMEWORK_PERMISSIONS_REQUIRED')
        for name in extra_tables:
            schema, table = name.split('.')
            cursor.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(schema, table)))
            require(cursor.fetchone() == (0,), 'EXISTING_UPGRADE_NEW_STORAGE_TABLES_MUST_REMAIN_EMPTY')
    cursor.execute("SELECT count(*),sum((input_refs->>'reserved_cost')::numeric) FROM sjia.task WHERE task_type='ocr' AND status='uncertain'")
    unknown_count, reserved = cursor.fetchone()
    require(unknown_count == 2 and reserved == 2, 'EXISTING_UPGRADE_UNKNOWN_OCR_AND_COST_MUST_REMAIN')
    require(ArchiveFact.objects.count() == 83 and ArchiveFactVersion.objects.count() == 249,
        'EXISTING_UPGRADE_ACTUAL_ORM_FACTS_REQUIRED')
if arguments.phase == 'migrated':
    verify_current_model_state()
report = {'result': 'PASS', 'measured_at': datetime.now(timezone.utc).isoformat(), 'phase': arguments.phase,
    'database': identity[0], 'role': identity[1], 'cluster_id': identity[2], 'server_version': identity[3],
    'python_version': platform.python_version(), 'original_tables': verified_tables, 'new_tables': sorted(extra_tables),
    'framework_additions': actual_framework, 'archive_facts': 83, 'fact_versions': 249, 'unknown_ocr': unknown_count,
    'uncertain_reserved_cost_cny': str(reserved), 'backend_artifact_sha256': compute_checksum(backend_artifact_manifest()),
    'snapshot_sha256': arguments.snapshot_sha256, 'read_only': True, 'new_model_calls': 0,
    'clinical_qualification': 'NOT_ESTABLISHED'}
with arguments.output.open('x') as output:
    json.dump(report, output, ensure_ascii=False, sort_keys=True, indent=2)
print(json.dumps({'result': 'PASS', 'phase': arguments.phase, 'server_version': identity[3],
    'original_tables': len(verified_tables), 'archive_facts': 83, 'fact_versions': 249, 'unknown_ocr': 2}))
