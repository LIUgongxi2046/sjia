import argparse
import hashlib
import http.client
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from tests.verify_v2_migration import command, database_args, migration_connection, object_manifest, require, restore_objects, sha256, snapshot
from django.conf import settings
from django.apps import apps
from django.db import connections
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.state import ProjectState
from django.core.management.base import no_translations
from django.urls import resolve
from files.models import FileObject
from files.purpose_schemas import PurposeCaseGrantInput, PurposeConfigApprovalInput, PurposeConfigEnableInput, PurposeConfigScope, PurposeConfigValidationInput, PurposeCopyInput, PurposeDispositionApprovalContext, PurposeDispositionApprovalInput, PurposeDispositionExecuteInput, PurposeDispositionFileGrant, PurposeDispositionInput, PurposeDispositionObjectProof, PurposeDispositionReceiptInput, PurposeDispositionRetainInput, PurposeDispositionRetryInput, PurposeDispositionScope, PurposeDispositionTarget, PurposeDispositionTaskRefs, PurposeIntentInput, PurposeLocalDispositionRule, PurposeOptionsInput, PurposeProcessingPlan, PurposeReviewInput, PurposeRevokeInput, PurposeRetryInput, PurposeServiceGrantInput, PurposeUseAuthorizationInput, PurposeWriteHeaders
from files.service import FileRejected, object_store
from recognition.ark import recognize_report_with_evidence, recognize_therapy_image
from tasks.purpose_runtime import app
from retention.jobs import reconcile_local_dispositions
from tests.verify_disposition_queue import main as verify_current_disposition_queue


@no_translations
def migrate_purpose(connection):
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    plan = executor.migration_plan(targets)
    names = [(migration.app_label, migration.name) for migration, backwards in plan]
    allowed = {('tasks', '0004_purpose_copy'), ('archives', '0003_review_unknown'), ('requests', '0003_authorization_context'),
        ('contacts', '0003_contact_verification')}
    require(len(names) == len(set(names)) and set(names) <= allowed, '存在未经用途任务核验的迁移')
    executor.migrate(targets)
    executor = MigrationExecutor(connection)
    require(not executor.migration_plan(executor.loader.graph.leaf_nodes()), '仍有未执行迁移')
    changes = MigrationAutodetector(executor.loader.project_state(), ProjectState.from_apps(apps)).changes(
        graph=executor.loader.graph)
    require(not changes, '运行模型与 MigrationState 不一致')
    return [f'{app}.{name}' for app, name in names]


def anonymous_http_checks(file):
    results = []
    paths = [('POST', f'/api/v2/support/files/{file.id}/purpose-copies', 401, 'AUTH_REQUIRED'),
        ('GET', f'/api/v2/support/files/{file.id}/purposes/external_recognition/options', 401, 'AUTH_REQUIRED'),
        ('GET', f'/api/v2/support/purpose-copies/{file.id}', 401, 'AUTH_REQUIRED'),
        ('GET', f'/api/v2/support/purpose-copies/{file.id}/evidence', 401, 'AUTH_REQUIRED'),
        ('GET', f'/api/v2/support/purpose-copies/{file.id}/review-content', 401, 'AUTH_REQUIRED'),
        ('POST', '/api/v2/write-intents', 401, 'AUTH_REQUIRED'),
        ('GET', f'/api/v2/tasks/{file.id}', 401, 'AUTH_REQUIRED'),
        ('GET', '/api/v2/support/session', 401, 'AUTH_REQUIRED'),
        ('POST', '/api/v2/support/session/login', 403, 'CSRF_FAILED'),
        ('POST', f'/api/v1/support/patients/{file.patient_id}/use-authorizations', 401, 'AUTH_REQUIRED'),
        ('POST', f'/api/v1/patients/{file.patient_id}/dispositions', 401, 'AUTH_REQUIRED'),
        ('GET', f'/api/v1/dispositions/{file.id}', 401, 'AUTH_REQUIRED'),
        ('POST', f'/api/v1/support/dispositions/{file.id}/approve', 401, 'AUTH_REQUIRED'),
        ('POST', f'/api/v1/support/dispositions/{file.id}/execute', 401, 'AUTH_REQUIRED'),
        ('GET', f'/api/v2/support/dispositions/{file.id}', 401, 'AUTH_REQUIRED'),
        ('POST', f'/api/v2/support/dispositions/{file.id}/approve', 401, 'AUTH_REQUIRED'),
        ('POST', f'/api/v2/support/dispositions/{file.id}/items/{file.id}/execute', 401, 'AUTH_REQUIRED'),
        ('POST', f'/api/v2/support/dispositions/{file.id}/items/{file.id}/retain', 401, 'AUTH_REQUIRED'),
        ('POST', f'/api/v2/support/disposition-tasks/{file.id}/retry', 401, 'AUTH_REQUIRED'),
        ('POST', '/api/v1/support/access-grants', 401, 'AUTH_REQUIRED'),
        ('POST', '/api/v1/support/role-grants', 401, 'AUTH_REQUIRED')]
    for method, path, expected_status, expected_code in paths:
        connection = http.client.HTTPConnection('127.0.0.1', 8000, timeout=10)
        connection.request(method, path, body=b'{}' if method == 'POST' else None,
            headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
        response = connection.getresponse()
        payload = json.loads(response.read())
        require(response.status == expected_status and payload['code'] == expected_code, '匿名接口或登录 CSRF 检查未拒绝读取及提交')
        require(response.getheader('Cache-Control') == 'no-store, private', '受控接口缺少禁止缓存设置')
        connection.close()
        results.append({'method': method, 'path': path, 'status': response.status, 'code': payload['code']})
    return results


def purpose_worker_checks():
    inspector = app.control.inspect(destination=['purpose@local'], timeout=5)
    ping = inspector.ping()
    require(ping == {'purpose@local': {'ok': 'pong'}}, '用途 Worker 未响应实际检查')
    queues = inspector.active_queues()
    require(queues is not None and set(queues) == {'purpose@local'}, '用途 Worker 队列无法读取')
    require([queue['name'] for queue in queues['purpose@local']] == ['purpose'], '用途 Worker 队列范围不一致')
    registered = inspector.registered()
    required = {'tasks.dispatcher.dispatch_pending', 'tasks.worker.consume', 'retention.jobs.reconcile_local_dispositions'}
    require(registered is not None and required <= set(registered['purpose@local']), '用途 Worker 缺少正式任务登记')
    schedule = app.conf.beat_schedule
    require(set(schedule) == {'dispatch-purpose-outbox', 'reconcile-local-dispositions'}, '用途调度范围不一致')
    entry = schedule['dispatch-purpose-outbox']
    require(entry['kwargs']['event_types'] == ['purpose_copy.requested', 'disposition.local_requested', 'disposition.provider_requested'] and entry['options']['queue'] == 'purpose',
        '用途调度事件或队列范围不一致')
    require(schedule['reconcile-local-dispositions']['task'] == 'retention.jobs.reconcile_local_dispositions'
        and schedule['reconcile-local-dispositions']['options']['queue'] == 'purpose', '处置租约检查登记不一致')
    reconciliation_count = reconcile_local_dispositions.run()
    return {'worker_ping': ping, 'queue_names': ['purpose'], 'registered_tasks': sorted(registered['purpose@local']),
        'beat_schedule_names': sorted(schedule), 'event_types': entry['kwargs']['event_types'],
        'reconciliation_count': reconciliation_count,
        'positive_processing_validation': 'NOT_RUN'}


def preserved_snapshot(connection, columns):
    report = {}
    for table, names in columns.items():
        digest = hashlib.sha256()
        count = 0
        query = sql.SQL('SELECT row_to_json(t)::text FROM (SELECT {} FROM {}) t ORDER BY row_to_json(t)::text').format(
            sql.SQL(', ').join(sql.Identifier(name) for name in names), sql.Identifier('sjia', table))
        for row in connection.execute(query):
            data = row[0].encode('utf-8')
            digest.update(len(data).to_bytes(8, 'big'))
            digest.update(data)
            count += 1
        report[table] = {'count': count, 'sha256': digest.hexdigest()}
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--worker-validation', choices=['required', 'not-run'], default='required')
    arguments = parser.parse_args()
    name = settings.DATABASES['default']['NAME']
    require(name == 'sjia_dev', '只允许已接收的本地运行数据库')
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_%f')
    work = ROOT / '.planning-work' / f't40_{stamp}'
    work.mkdir(mode=0o700)
    restored_name = f'sjia_purpose_verify_{stamp}'
    database = settings.DATABASES['default']
    options = ['--host', database['HOST'], '--port', str(database['PORT']), '--username', database['USER']]
    dump_path = work / 'database.dump'
    zip_path = work / 'objects.zip'
    commands = []
    with psycopg.connect(**database_args(name)) as source:
        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        before = snapshot(source)
        columns = {}
        for table, column in source.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='sjia' ORDER BY table_name, ordinal_position"):
            if table in before:
                columns.setdefault(table, []).append(column)
        require(preserved_snapshot(source, columns) == before, '原有字段检查与完整资料检查不一致')
        exported = source.execute('SELECT pg_export_snapshot()').fetchone()[0]
        objects = object_manifest(source, settings.FILE_OBJECT_ROOT, zip_path)
        command(['pg_dump', *options, '--format', 'custom', '--snapshot', exported, '--file', str(dump_path), name], commands)
    dump_path.chmod(0o600)
    zip_path.chmod(0o600)
    with psycopg.connect(**database_args('postgres'), autocommit=True) as administrator:
        administrator.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(restored_name)))
    command(['pg_restore', *options, '--dbname', restored_name, '--no-owner', '--exit-on-error', str(dump_path)], commands)
    object_root = work / 'restored-objects'
    restore_objects(zip_path, objects, object_root)
    with psycopg.connect(**database_args(restored_name)) as restored:
        require(snapshot(restored) == before and object_manifest(restored, object_root) == objects, '备份恢复内容不一致')
    restored_plan = migrate_purpose(migration_connection(restored_name, 'purpose_restore'))
    with psycopg.connect(**database_args(restored_name)) as restored:
        require(preserved_snapshot(restored, columns) == before, '用途任务登记改变现有业务资料')
        if 'authorization_context' not in columns['write_intent']:
            require(restored.execute('SELECT count(*) FROM sjia.write_intent WHERE authorization_context IS NOT NULL').fetchone()[0] == 0,
                '既有请求被添加未经提交的授权范围')
        check = restored.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='sjia.task'::regclass AND conname='task_task_type_check'").fetchone()[0]
        require('purpose_copy' in check, '数据库未登记用途任务类型')
        review_check = restored.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='sjia.purpose_copy_review'::regclass AND conname='purpose_copy_review_clinical_result_check'").fetchone()[0]
        require('unknown' in review_check, '数据库未保留尚未确认的临床检查状态')
    source_plan = None
    if arguments.apply:
        with psycopg.connect(**database_args(name)) as source:
            require(snapshot(source) == before and object_manifest(source, settings.FILE_OBJECT_ROOT) == objects, '运行资料已变化，需要重新核查')
        source_plan = migrate_purpose(connections['default'])
    file = FileObject.objects.filter(purpose='original', status='available').order_by('created_at', 'id').first()
    require(file is not None, '缺少已保存原件，无法执行实际读取检查')
    data = object_store.get(file.object_key)
    require(len(data) == file.size_bytes and sha256(settings.FILE_OBJECT_ROOT / file.object_key) == file.checksum, '实际原件字节不一致')
    assertions = unittest.TestCase()
    adapter_checks = []
    for recognizer in (recognize_report_with_evidence, recognize_therapy_image):
        with assertions.assertRaises(FileRejected) as raised:
            recognizer(data, file.media_type)
        require(raised.exception.reason_code == 'PURPOSE_MODEL_INPUT_DENIED', '缺少用途证明时模型调用未拒绝')
        adapter_checks.append({'identifier': recognizer.__name__, 'reason_code': raised.exception.reason_code})
    routes = []
    for path in (f'/api/v2/support/files/{file.id}/purpose-copies',
            f'/api/v2/support/files/{file.id}/purposes/external_recognition/options', f'/api/v2/support/purpose-copies/{file.id}',
            f'/api/v2/support/purpose-copies/{file.id}/reviews', f'/api/v2/support/purpose-copies/{file.id}/revoke',
            f'/api/v2/support/purpose-copies/{file.id}/content', f'/api/v2/support/purpose-copies/{file.id}/evidence',
            f'/api/v2/support/purpose-copies/{file.id}/review-content', '/api/v2/write-intents', f'/api/v2/tasks/{file.id}',
            f'/api/v2/support/dispositions/{file.id}', f'/api/v2/support/dispositions/{file.id}/approve',
            f'/api/v2/support/dispositions/{file.id}/items/{file.id}/execute',
            f'/api/v2/support/dispositions/{file.id}/items/{file.id}/retain',
            f'/api/v2/support/disposition-tasks/{file.id}/retry'):
        route = resolve(path)
        routes.append({'path': path, 'view': route.func.cls.__name__})
    http_checks = anonymous_http_checks(file)
    worker_checks = purpose_worker_checks() if arguments.worker_validation == 'required' else {
        'status': 'NOT_RUN', 'reason': 'AWAITING_CONSUMER_AUTHORIZATION',
        'positive_processing_validation': 'NOT_RUN'}
    reconciliation_count = reconcile_local_dispositions.run()
    if arguments.worker_validation == 'not-run':
        verify_current_disposition_queue()
    schemas = {model.__name__: model.model_json_schema() for model in (PurposeCopyInput, PurposeIntentInput, PurposeOptionsInput,
        PurposeProcessingPlan, PurposeReviewInput, PurposeRevokeInput, PurposeRetryInput, PurposeWriteHeaders,
        PurposeConfigApprovalInput, PurposeConfigEnableInput, PurposeConfigValidationInput, PurposeConfigScope,
        PurposeCaseGrantInput, PurposeServiceGrantInput, PurposeUseAuthorizationInput, PurposeDispositionInput,
        PurposeDispositionReceiptInput, PurposeDispositionScope, PurposeDispositionTarget,
        PurposeDispositionApprovalContext, PurposeDispositionApprovalInput, PurposeDispositionFileGrant,
        PurposeDispositionObjectProof, PurposeLocalDispositionRule, PurposeDispositionExecuteInput,
        PurposeDispositionRetainInput, PurposeDispositionRetryInput, PurposeDispositionTaskRefs)}
    with psycopg.connect(**database_args(name)) as source:
        require(preserved_snapshot(source, columns) == before and object_manifest(source, settings.FILE_OBJECT_ROOT) == objects,
            '检查改变已有资料或原件')
        inventory = {'purpose_authorizations': source.execute("SELECT count(*) FROM sjia.data_use_authorization WHERE purpose IN ('external_recognition','format_evaluation','clinical_evaluation','competition_display','export') AND revoked_at IS NULL").fetchone()[0],
            'processing_configs': source.execute("SELECT count(*) FROM sjia.config_version WHERE schema_version='purpose-processing-2' AND status='enabled'").fetchone()[0],
            'purpose_copies': source.execute('SELECT count(*) FROM sjia.purpose_copy').fetchone()[0],
            'purpose_reviews': source.execute('SELECT count(*) FROM sjia.purpose_copy_review').fetchone()[0]}
    manifest = {'source_database': name, 'restored_database': restored_name, 'tables': before, 'objects': objects,
        'database_sha256': sha256(dump_path), 'objects_sha256': sha256(zip_path), 'preserved_columns': columns, 'commands': commands}
    manifest_path = work / 'backup-manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    manifest_path.chmod(0o600)
    current_manifest_path = ROOT / '.planning-work/t40-backup-manifest.json'
    current_manifest_path.write_bytes(manifest_path.read_bytes())
    current_manifest_path.chmod(0o600)
    report = {'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'status': 'PASS', 'scope': 'STRUCTURE_AND_DENIAL',
        'restored_migrations': restored_plan, 'source_migrations': source_plan, 'preserved_tables': len(before),
        'preserved_objects': len(objects), 'routes': routes, 'http_checks': http_checks, 'adapter_checks': adapter_checks,
        'worker_checks': worker_checks,
        'local_reconciliation_count': reconciliation_count,
        'original_probe': {'file_id': str(file.id), 'version': file.object_version, 'checksum': file.checksum},
        'existing_inventory': inventory, 'schema_names': sorted(schemas), 'backup_manifest': str(manifest_path.relative_to(ROOT)),
        'backup_manifest_sha256': sha256(manifest_path), 'provider_called': False, 'clinical_rows_inserted': 0,
        'processing_business_validation': 'NOT_RUN', 'human_review_validation': 'NOT_RUN',
        'external_validation': 'NOT_RUN', 'visual_validation': 'NOT_RUN'}
    (ROOT / '.planning-work/t40-runtime.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
