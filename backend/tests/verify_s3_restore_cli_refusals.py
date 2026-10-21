import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest import TestCase

import django

django.setup()

from django.conf import settings
from django.db import connection, connections, transaction
from psycopg import sql
from deploy.recovery import admit_restored_runtime
from files.s3_storage import S3StorageConfiguration
from files.storage_factory import storage_selection
from retention.content_journal import ContentJournalRejected
from retention.s3_backup import verify_package
from retention.s3_lineage_schemas import S3LineageBackupPackage


parser = argparse.ArgumentParser(description='在真实已恢复目标上核验当前CLI拒绝行为和数据保持')
parser.add_argument('--admission', required=True, type=Path)
parser.add_argument('--output', required=True, type=Path)
arguments = parser.parse_args()
ROOT = arguments.admission.parent.parent
TARGET = Path(settings.SJIA_RESTORE_ROOT)
CASES = ROOT / 'current-cli-refusals'
CLI = Path(__file__).resolve().parents[2] / 'deploy/recovery.py'
base = json.loads(arguments.admission.read_bytes())
requirement = json.loads((ROOT / 'runtime-verified.json').read_bytes())
os.umask(0o077)


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


require(not arguments.output.exists() and not CASES.exists() and requirement['result'] == 'PASS'
    and str(TARGET) == base['destination'] and base['operation'] == 'restore'
    and datetime.fromisoformat(base['expires_at']) > datetime.now(timezone.utc),
    'S3_CLI_REFUSALS_CURRENT_COMPLETED_TARGET_AND_VALID_APPROVAL_REQUIRED')
package = verify_package(base['source'])
require(package.completed_at < datetime.now(timezone.utc), 'S3_CLI_REFUSALS_REAL_PAST_COMPLETION_REQUIRED')
CASES.mkdir(mode=0o700)


def database_snapshot():
    result = []
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION READ ONLY')
        cursor.execute("SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' "
            "AND n.nspname NOT LIKE 'pg_temp_%' AND c.relkind IN ('r','p') ORDER BY n.nspname,c.relname")
        tables = cursor.fetchall()
        for schema, table in tables:
            cursor.execute(sql.SQL('SELECT to_jsonb(item)::text FROM {}.{} item ORDER BY to_jsonb(item)::text').format(
                sql.Identifier(schema), sql.Identifier(table)))
            result.append({'schema': schema, 'table': table, 'rows': cursor.fetchall()})
    connections.close_all()
    return digest(result)


def control_snapshot():
    return {path.relative_to(TARGET).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(TARGET.rglob('*')) if path.is_file()}


def native_snapshot():
    store = storage_selection.store
    inventory = store.driver.client.list_object_versions(Bucket=store.driver.configuration.bucket,
        Prefix=store.driver.configuration.prefix + '/', MaxKeys=1000)
    require(not inventory.get('IsTruncated'), 'S3_CLI_REFUSALS_COMPLETE_NATIVE_INVENTORY_REQUIRED')
    return digest({'versions': sorted(inventory.get('Versions', []), key=lambda item: (item['Key'], item['VersionId'])),
        'delete_markers': sorted(inventory.get('DeleteMarkers', []), key=lambda item: (item['Key'], item['VersionId']))})


before = {'database': database_snapshot(), 'control': control_snapshot(), 'native': native_snapshot()}
results = []


def run_case(label, document, expected, *, environment=None, source=base['source'], storage='s3', operation='restore'):
    filename = CASES / (label + '.json')
    with filename.open('x') as output:
        json.dump(document, output, indent=2)
    command = [sys.executable, str(CLI), operation, '--execute', '--admission', str(filename),
        '--destination', document['destination']]
    if operation == 'restore':
        command.extend(('--storage-kind', storage, '--source', source))
    connections.close_all()
    result = subprocess.run(command, env=environment, capture_output=True, text=True)
    observation = {'case': label, 'returncode': result.returncode, 'expected_reason': expected,
        'actual_reason': result.stderr.strip(), 'stdout_sha256': hashlib.sha256(result.stdout.encode()).hexdigest()}
    with (CASES / (label + '-observed.json')).open('x') as output:
        json.dump(observation, output, indent=2)
    require(result.returncode == 2 and result.stderr.strip() == expected, 'S3_CLI_REFUSALS_UNEXPECTED_RESULT:' + label)
    results.append(observation)
    print(json.dumps({'case': label, 'returncode': result.returncode, 'reason': expected}), flush=True)


run_case('existing_restore_root', base, 'EMPTY_PRIVATE_S3_RESTORE_ROOT_REQUIRED')
run_case('source_database', {**base, 'database': package.source_snapshot.database}, 'DATABASE_IDENTITY_MISMATCH')
run_case('source_cluster', {**base, 'cluster_id': package.source_snapshot.cluster_id}, 'DATABASE_IDENTITY_MISMATCH')
run_case('source_binding', {**base, 'storage_binding_sha256': package.source_snapshot.binding_sha256},
    'EXACT_S3_RUNTIME_ADMISSION_REQUIRED')
run_case('trust_root_as_control', {**base, 'control_root': '/var/lib/sjia-trust/journal'}, 'EXACT_S3_RUNTIME_ADMISSION_REQUIRED')
run_case('expired_admission', {**base, 'expires_at': package.completed_at.isoformat()}, 'ADMISSION_EXPIRED_OR_INVALID')
run_case('operator_not_approved', {**base, 'operator_approved': False}, 'EXACT_OPERATION_APPROVAL_REQUIRED')
run_case('wrong_operation', {**base, 'operation': 'backup'}, 'EXACT_OPERATION_APPROVAL_REQUIRED')
run_case('outbound_not_confirmed', {**base, 'outbound_blocked': False}, 'ISOLATED_RESTORE_ADMISSION_REQUIRED')
run_case('database_digest_as_manifest', {**base, 'backup_manifest_sha256': package.database_sha256},
    'EXACT_BACKUP_MANIFEST_ADMISSION_REQUIRED')
run_case('local_selection', base, 'EXACT_STORAGE_KIND_ADMISSION_REQUIRED', storage='local')
run_case('target_as_source', base, 'EXACT_PATH_ADMISSION_REQUIRED', source=str(TARGET))
run_case('target_not_approved', {**base, 'empty_target_approved': False}, 'ISOLATED_RESTORE_ADMISSION_REQUIRED')
run_case('backup_not_approved', {**base, 'backup_trusted_and_reviewed': False}, 'ISOLATED_RESTORE_ADMISSION_REQUIRED')

if isinstance(package, S3LineageBackupPackage):
    require(base['restore_lineage_sha256'] != base['backup_manifest_sha256'], 'S3_CLI_REFUSALS_DISTINCT_ACTUAL_DIGESTS_REQUIRED')
    run_case('missing_lineage_digest', {key: value for key, value in base.items() if key != 'restore_lineage_sha256'},
        'EXACT_S3_RESTORE_LINEAGE_ADMISSION_REQUIRED')
    run_case('wrong_lineage_digest', {**base, 'restore_lineage_sha256': base['backup_manifest_sha256']},
        'EXACT_S3_RESTORE_LINEAGE_ADMISSION_REQUIRED')
    run_case('wrong_lineage_format', {**base, 'backup_format': 's3-consistent-backup-1'},
        'EXACT_S3_RESTORE_LINEAGE_ADMISSION_REQUIRED')

for label in ('nonempty_s3_prefix', 'nonempty_database'):
    context = ROOT / ('current-' + label)
    context.mkdir(mode=0o700)
    (context / 's3-control').mkdir(mode=0o700)
    configuration = json.loads((ROOT / 'config/target-storage.json').read_bytes())
    configuration['control_directory'] = str(context / 's3-control')
    if label == 'nonempty_database':
        configuration['prefix'] = 'qa38-refusal-empty-' + requirement['target']['restore_id']
    configuration_path = CASES / (label + '-storage.json')
    with configuration_path.open('x') as output:
        json.dump(configuration, output, indent=2)
    driver_configuration = S3StorageConfiguration(provider_id=configuration['provider_id'],
        endpoint_url=configuration['endpoint_url'], region=configuration['region'], bucket=configuration['bucket'],
        prefix=configuration['prefix'], owner_id=configuration['owner_id'], access_key=configuration['access_key'],
        secret_key=configuration['secret_key'], ca_bundle=Path(configuration['ca_bundle']))
    document = {**base, 'destination': str(context), 'control_root': str(context / 's3-control'),
        'storage_binding_sha256': driver_configuration.binding_sha256}
    environment = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'qa40_full_cli_refusal_settings',
        'SJIA_QA38_REFUSAL_ROOT': str(context), 'SJIA_S3_STORAGE_CONFIGURATION_FILE': str(configuration_path)}
    reason = 'RESTORE_S3_PREFIX_NOT_EMPTY' if label == 'nonempty_s3_prefix' else 'RESTORE_DATABASE_NOT_EMPTY'
    run_case(label, document, reason, environment=environment)
    require({path.name for path in context.iterdir()} == {'s3-control'} and not any((context / 's3-control').iterdir()),
        'S3_CLI_REFUSALS_TEMPORARY_EMPTY_CONTEXT_MUST_REMAIN')

adoption = {**base, 'operation': 'admit-restored-runtime', 'target': requirement['target']}
with TestCase().assertRaises(ContentJournalRejected) as rejected:
    admit_restored_runtime(adoption, TARGET)
require(rejected.exception.reason_code == 'RESTORE_SEPARATE_RELEASE_APPROVAL_REQUIRED',
    'S3_CLI_REFUSALS_EXACT_SEPARATE_RELEASE_REQUIRED')
run_case('missing_separate_release', adoption,
    'RECOVERY_FAILED; NO_AUTOMATIC_RESTART; OPERATOR_REVIEW_REQUIRED', operation='admit-restored-runtime')
after = {'database': database_snapshot(), 'control': control_snapshot(), 'native': native_snapshot()}
require(before == after and not (TARGET / 'ADOPTION_ADMITTED.json').exists(),
    'S3_CLI_REFUSALS_RESTORED_STATE_AND_ADOPTION_GUARD_MUST_REMAIN')
report = {'result': 'PASS', 'measured_at': datetime.now(timezone.utc).isoformat(), 'cases': results,
    'backup_format': package.schema_version, 'generations': requirement['generations'],
    'exact_adoption_refusal': rejected.exception.reason_code, 'database_sha256': after['database'],
    'native_inventory_sha256': after['native'], 'control_sha256': after['control'],
    'database_unchanged': True, 'native_inventory_unchanged': True, 'controls_unchanged': True,
    'cli_source_sha256': hashlib.sha256(CLI.read_bytes()).hexdigest(),
    'verification_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    'runtime_admission': 'BLOCKED', 'new_model_calls': 0}
with arguments.output.open('x') as output:
    json.dump(report, output, indent=2)
    output.write('\n')
connections.close_all()
print(json.dumps({'result': 'PASS', 'cli_refusals': len(results), 'restored_state_unchanged': True,
    'runtime_admission': 'BLOCKED', 'new_model_calls': 0}))
