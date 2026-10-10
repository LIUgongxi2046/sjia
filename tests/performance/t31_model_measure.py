import argparse
import getpass
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend'
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / 'tests' / 'operations'))

from config.settings import simulation as source_settings

WINDOW_PARENT = ROOT / '.planning-work' / 'parallel' / 'window4'


def run(command, *, env=None):
    return subprocess.run(command, check=True, env=env, capture_output=True, text=True)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-name', default='isolated-model-20261009')
    parser.add_argument('--database-port', type=int, default=55478)
    parser.add_argument('--broker-port', type=int, default=16386)
    parser.add_argument('--reuse-seeded-run', action='store_true')
    parser.add_argument('--inspect-latest-task', action='store_true')
    parser.add_argument('--report', default=str(ROOT / 'docs/testing/T31-model-simulation-report.json'))
    args = parser.parse_args()
    if (not args.run_name.startswith('isolated-model-') or '/' in args.run_name
            or args.run_name in ('.', '..')):
        raise ValueError('模拟模型运行名必须以 isolated-model- 开头且不得包含路径。')
    if (args.database_port == args.broker_port or any(port < 10000 or port > 65535
            for port in (args.database_port, args.broker_port))):
        raise ValueError('隔离数据库与队列端口必须不同且位于 10000 至 65535。')
    if (source_settings.DATABASES['default']['NAME'] != 'sjia_simulation'
            or source_settings.SJIA_ENVIRONMENT != 'simulation'):
        raise RuntimeError('模型测量只能从 sjia_simulation 模拟配置启动。')
    if args.inspect_latest_task and not args.reuse_seeded_run:
        raise RuntimeError('读取既有模拟任务必须使用已建立的隔离运行目录。')
    run_root = WINDOW_PARENT / args.run_name
    data_root = run_root / 'postgres'
    if run_root.parent.resolve() != WINDOW_PARENT.resolve():
        raise RuntimeError('模拟模型运行目录必须位于窗口四专用目录内。')
    if args.reuse_seeded_run:
        if not run_root.exists() or not (data_root / 'PG_VERSION').is_file() or not (run_root / 't42-fixture.json').is_file():
            raise RuntimeError('待继续的模拟运行缺少已建立数据库或模拟种子。')
    elif run_root.exists():
        raise RuntimeError('模拟模型运行目录必须是窗口四下的全新独立目录。')
    else:
        run_root.mkdir()
        data_root.mkdir()
    owner = getpass.getuser()
    database_user = source_settings.DATABASES['default']['USER']
    connection = ['--host', '127.0.0.1', '--port', str(args.database_port), '--username', owner]
    postgres_log = run_root / 'postgres.log'
    postgres_started = False
    try:
        if not args.reuse_seeded_run:
            run(['initdb', '--pgdata', str(data_root), '--username', owner, '--auth-local=trust',
                '--auth-host=trust', '--no-locale', '--encoding=UTF8'])
        run(['pg_ctl', '--pgdata', str(data_root), '-o',
            f'-h 127.0.0.1 -p {args.database_port} -c listen_addresses=127.0.0.1 '
            '-c max_connections=80 -c unix_socket_directories=', '-l', str(postgres_log), '-w', 'start'])
        postgres_started = True
        if not args.reuse_seeded_run:
            run(['createuser', *connection, '--superuser', database_user])
            run(['createdb', *connection, '--owner', database_user, '--encoding=UTF8', 'sjia_simulation'])
            run(['psql', *connection, '--dbname', 'sjia_simulation', '--command',
                f'CREATE SCHEMA sjia AUTHORIZATION {database_user}; ALTER DATABASE sjia_simulation SET search_path TO sjia,public;'])

        isolated_env = dict(os.environ)
        isolated_env.update({'DJANGO_SETTINGS_MODULE': 'window4_simulation_settings',
            'PYTHONPATH': os.pathsep.join([str(ROOT / 'tests' / 'operations'), str(BACKEND)]),
            'WINDOW4_SIMULATION_ROOT': str(run_root),
            'WINDOW4_SIMULATION_DB_PORT': str(args.database_port),
            'WINDOW4_SIMULATION_BROKER_PORT': str(args.broker_port),
            'WINDOW4_SOURCE_CONFIG_EVIDENCE_ROOT': str(Path(source_settings.CONFIG_EVIDENCE_ROOT).resolve())})
        os.environ.update(isolated_env)

        import django
        django.setup()

        from django.conf import settings
        from django.core.management import call_command
        from django.db import connection as django_connection, transaction
        from archives.batch_creation import prepare_sources, save_received_batch
        from archives.models import ArchiveBatch, ArchivePage, ArchivePageAttempt
        from archives.page_actions import action_scope, save_action
        from configurations.models import ConfigVersion
        from configurations.evidence import deployed_artifact_checksum
        from configurations.qualification import configuration_qualification_reason
        from core.management.commands.seed_simulation import configuration, qualify_simulation_configuration
        from files.models import FileGroup, FileObject
        from files.service import object_store
        from files.storage import digest_bytes
        from family_content.models import EnvironmentGate
        from identity.models import PrincipalCredential
        from retention.models import RetentionPolicy
        from tasks.models import ConsumerReceipt, OutboxEvent, Task, TaskAttempt
        from tasks.worker import consume
        import recognition.ark as ark

        if (settings.DATABASES['default']['NAME'] != 'sjia_simulation'
                or settings.SJIA_ENVIRONMENT != 'simulation'
                or Path(settings.SIMULATION_ROOT).resolve() != run_root.resolve()):
            raise RuntimeError('隔离模型数据库、环境或文件目录身份核验失败。')
        if not settings.ARK_API_KEY or not settings.ARK_VISION_MODEL:
            raise RuntimeError('模拟模型配置缺少实际调用所需的凭据或模型名称。')

        if args.inspect_latest_task:
            with django_connection.cursor() as cursor:
                cursor.execute('SELECT current_database(), current_schema(), current_setting(%s)', ['search_path'])
                database_identity = cursor.fetchone()
                cursor.execute('SHOW server_version')
                postgres_version = cursor.fetchone()[0]
            if (database_identity[0:2] != ('sjia_simulation', 'sjia')
                    or database_identity[2].replace(' ', '') != 'sjia,public'):
                raise RuntimeError('既有模拟任务不在指定隔离数据库。')
            batch = ArchiveBatch.objects.order_by('-received_at', '-id').first()
            pages = list(ArchivePage.objects.filter(batch_id=batch.id).order_by('ordinal'))
            if len(pages) != 1:
                raise RuntimeError('最新隔离模拟批次的页数不是一页。')
            selected_page = pages[0]
            task = Task.objects.get(id=selected_page.current_task_id)
            attempts = list(TaskAttempt.objects.filter(task=task).order_by('attempt_number'))
            if len(attempts) != 1:
                raise RuntimeError('最新隔离模拟 OCR 任务的尝试数不是一条。')
            attempt = attempts[0]
            page_attempt = ArchivePageAttempt.objects.get(page_id=selected_page.id,
                task_attempt_id=attempt.id)
            usage_evidence = page_attempt.usage_evidence
            response_file = FileObject.objects.get(id=usage_evidence['raw_response_file_id'],
                patient_id=task.patient_id, purpose='ocr_response', status='available')
            response_bytes = object_store.get(response_file.object_key)
            original_file = FileObject.objects.get(id=task.input_refs['file_id'],
                patient_id=task.patient_id, purpose='original', status='available')
            provider = ConfigVersion.objects.get(id=task.input_refs['provider_config_id'])
            quota = ConfigVersion.objects.get(id=task.input_refs['quota_id'])
            event = OutboxEvent.objects.get(task=task, event_type='archive_page.requested')
            receipt_count = ConsumerReceipt.objects.filter(consumer='archive_page', event=event).count()
            from recognition.models import CandidateSet

            candidate_set = CandidateSet.objects.get(id=page_attempt.candidate_set_id, task_id=task.id,
                patient=task.patient_id)
            usage = usage_evidence['usage']
            input_tokens, output_tokens = usage.get('prompt_tokens'), usage.get('completion_tokens')
            estimated_cost = None
            if input_tokens is not None and output_tokens is not None:
                estimated_cost = str((Decimal(input_tokens) * Decimal(provider.payload['input_per_million']) +
                    Decimal(output_tokens) * Decimal(provider.payload['output_per_million'])) / Decimal('1000000'))
            if (task.status != 'awaiting_confirmation' or selected_page.status != 'awaiting_confirmation'
                    or attempt.status != TaskAttempt.Status.COMPLETED or not attempt.provider_request_id
                    or page_attempt.status != 'completed' or receipt_count != 1
                    or len(response_bytes) != response_file.size_bytes
                    or digest_bytes(response_bytes) != response_file.checksum
                    or estimated_cost != str(page_attempt.cost)):
                raise RuntimeError('既有模拟 OCR 任务、用量、费用或响应文件回读校验失败。')
            report = {'task': 'T31', 'classification': 'SIMULATION_ARCHIVE_PAGE_ONE_ACTUAL_MODEL_REQUEST',
                'measured_at': datetime.now(timezone.utc).isoformat(),
                'runtime_versions': {'python': sys.version.split()[0], 'django': django.get_version(),
                    'postgresql': postgres_version},
                'environment': {'database': database_identity[0], 'schema': database_identity[1],
                    'search_path': database_identity[2], 'runtime': 'simulation',
                    'isolated_root': str(run_root), 'database_port': args.database_port,
                    'broker': 'not_started', 'global_worker_or_beat_started': False,
                    'queue_consumption': 0},
                'input': {'source': 'seeded_simulation_fixture', 'mode': 'SIMULATION',
                    'file_group_id': task.input_refs['file_group_id'], 'file_id': str(original_file.id),
                    'media_type': original_file.media_type, 'bytes': original_file.size_bytes,
                    'sha256': original_file.checksum, 'archive_batch_id': str(batch.id),
                    'archive_page_id': str(selected_page.id), 'pages': 1},
                'provider': {'name': provider.payload['provider'], 'endpoint': provider.payload['endpoint'],
                    'model': usage_evidence['model'], 'config_id': str(provider.id),
                    'configuration_environment': provider.payload['environment'],
                    'actual_external_model_requests': 1, 'request_id': attempt.provider_request_id,
                    'input_tokens': input_tokens, 'output_tokens': output_tokens,
                    'total_tokens': usage.get('total_tokens'),
                    'model_request_elapsed_ms': 'NOT_CAPTURED_AFTER_PROCESS_EXIT',
                    'archive_page_attempt_duration_ms': page_attempt.duration_ms,
                    'configured_simulation_price': {'input_per_million': str(provider.payload['input_per_million']),
                        'output_per_million': str(provider.payload['output_per_million']),
                        'currency': provider.payload['currency']},
                    'simulation_cost_estimate': estimated_cost,
                    'simulation_cost_status': 'SIMULATION_PRICE_ESTIMATE',
                    'recorded_page_cost': str(page_attempt.cost), 'currency': page_attempt.currency,
                    'quota_config_id': str(quota.id),
                    'per_page_reservation': str(quota.payload['per_page_reservation']),
                    'actual_invoice_cost': 'NOT_VERIFIED'},
                'task_result': {'event_id': str(event.id), 'task_id': str(task.id),
                    'task_status': task.status, 'attempt_id': str(attempt.id),
                    'attempt_status': attempt.status, 'archive_page_attempt_id': str(page_attempt.id),
                    'archive_page_attempt_status': page_attempt.status,
                    'candidate_set_id': str(candidate_set.id), 'consumer_receipts': receipt_count,
                    'response_file_id': str(response_file.id), 'response_bytes': len(response_bytes),
                    'response_sha256': sha256(response_bytes), 'response_checksum_matches': True,
                    'response_saved_and_reread': True},
                'scope': {'model_requests': 1, 'files_processed': 1, 'notifications_sent': 0,
                    'real_clinical_data': False, 'real_capacity': 'NOT_RUN',
                    'clinical_scale': 'NOT_RUN', 'saved_and_reread': True}}
            report_path = Path(args.report)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            if json.loads(report_path.read_text()) != report:
                raise RuntimeError('模拟模型任务报告保存后回读内容不一致。')
            print(json.dumps(report, ensure_ascii=False))
            return

        if not args.reuse_seeded_run:
            Path(settings.CONFIG_EVIDENCE_ROOT).mkdir(parents=True)
            call_command('migrate', interactive=False, verbosity=0)
            call_command('seed_simulation', verbosity=0)

        fixture = json.loads((run_root / 't42-fixture.json').read_text())
        if fixture['mode'] != 'SIMULATION' or fixture['pricing_scope'] != 'SIMULATION':
            raise RuntimeError('模型输入没有明确标记为模拟资料。')
        group = FileGroup.objects.get(id=fixture['file_group_ids'][8], patient_id=fixture['patient_id'])
        files = list(FileObject.objects.filter(file_group_id=group.id, status='available', purpose='original').order_by('created_at'))
        if len(files) != 1 or files[0].media_type != 'image/png':
            raise RuntimeError('单次模拟模型测量必须只处理一张 PNG 原件。')
        owner_principal = PrincipalCredential.objects.get(username='simulation-family').principal
        quota = ConfigVersion.objects.filter(kind='provider', schema_version='archive-quota-1',
            status='enabled', payload__environment='simulation').order_by('-created_at', '-id').first()
        provider = ConfigVersion.objects.get(id=quota.payload['provider_config_id'], kind='provider',
            schema_version='provider-1', status='enabled', payload__provider='ark')
        gate = EnvironmentGate.objects.get(environment='simulation', enabled=True)
        runtime = ConfigVersion.objects.get(id=gate.runtime_config_id, kind='provider',
            schema_version='backend-runtime-1', status='enabled')
        previous_field_config = ConfigVersion.objects.get(id=provider.payload['field_config_id'], kind='content')
        reviewer = PrincipalCredential.objects.get(username='simulation-support').principal
        source = provider.source_revision
        reference = provider.payload['pricing_evidence_ref']
        retention = RetentionPolicy.objects.get(policy_key='simulation-configuration_evidence', version=1)
        field_config = configuration(key='window4-simulation-archive-ocr-fields', kind='content',
            actor=reviewer, source=source, payload=previous_field_config.payload)
        qualify_simulation_configuration(field_config, actor=reviewer, gate=gate, source=source,
            reference=reference, retention=retention)
        provider_payload = dict(provider.payload)
        provider_payload['field_config_id'] = str(field_config.id)
        provider = configuration(key='window4-simulation-archive-ocr', kind='provider',
            actor=reviewer, source=source, payload=provider_payload)
        qualify_simulation_configuration(provider, actor=reviewer, gate=gate, source=source,
            reference=reference, retention=retention)
        runtime_payload = dict(runtime.payload)
        runtime_payload['providers'] = [dict(entry) for entry in runtime.payload['providers']]
        ocr_entries = [entry for entry in runtime_payload['providers'] if entry['kind'] == 'ocr' and entry['enabled']]
        if len(ocr_entries) != 1:
            raise RuntimeError('模拟 runtime 配置中的 OCR provider 数量不为一。')
        ocr_entries[0]['config_id'] = str(provider.id)
        runtime = configuration(key='window4-simulation-runtime', kind='provider',
            actor=reviewer, source=runtime.source_revision, payload=runtime_payload)
        qualify_simulation_configuration(runtime, actor=reviewer, gate=gate, source=source,
            reference=reference, retention=retention)
        quota_payload = dict(quota.payload)
        quota_payload['runtime_config_id'] = str(runtime.id)
        quota_payload['provider_config_id'] = str(provider.id)
        quota = configuration(key='window4-simulation-archive-quota', kind='provider',
            actor=reviewer, source=source, payload=quota_payload)
        qualify_simulation_configuration(quota, actor=reviewer, gate=gate, source=source,
            reference=reference, retention=retention)
        gate.runtime_config = runtime
        gate.save(update_fields=['runtime_config'])
        provider_qualification = configuration_qualification_reason(provider, 'simulation')
        quota_qualification = configuration_qualification_reason(quota, 'simulation')
        runtime_qualification = configuration_qualification_reason(runtime, 'simulation')
        field_qualification = configuration_qualification_reason(field_config, 'simulation')
        if any(value is not None for value in (provider_qualification, quota_qualification,
                runtime_qualification, field_qualification)):
            with django_connection.cursor() as cursor:
                cursor.execute('SELECT current_database(), current_schema(), current_setting(%s)', ['search_path'])
                blocked_database = cursor.fetchone()
                cursor.execute('SHOW server_version')
                blocked_postgres_version = cursor.fetchone()[0]
            blocked_report = {
                'task': 'T31',
                'classification': 'SIMULATION_MODEL_PREFLIGHT_BLOCKED',
                'measured_at': datetime.now(timezone.utc).isoformat(),
                'status': 'BLOCKED_CONFIGURATION_QUALIFICATION',
                'runtime_versions': {'python': sys.version.split()[0],
                    'django': django.get_version(), 'postgresql': blocked_postgres_version},
                'environment': {'database': 'sjia_simulation', 'runtime': 'simulation',
                    'isolated_root': str(run_root), 'database_port': args.database_port,
                    'schema': blocked_database[1], 'search_path': blocked_database[2],
                    'broker': 'not_started', 'global_worker_or_beat_started': False,
                    'queue_consumption': 0},
                'input': {'source': 'seeded_simulation_fixture', 'mode': fixture['mode'],
                    'file_group_id': fixture['file_group_ids'][8], 'media_type': 'image/png',
                    'pages_intended': 1},
                'configuration': {'provider_config_id': str(provider.id),
                    'provider_qualification': provider_qualification,
                    'quota_config_id': str(quota.id), 'quota_qualification': quota_qualification,
                    'runtime_config_id': str(runtime.id), 'runtime_qualification': runtime_qualification,
                    'field_config_id': str(field_config.id), 'field_config_key': field_config.config_key,
                    'field_config_version': field_config.version,
                    'field_config_status': field_config.status,
                    'field_config_qualification': field_qualification,
                    'current_application_sha256': deployed_artifact_checksum(provider)},
                'model': {'actual_external_model_requests': 0, 'status': 'NOT_CALLED'},
                'task_result': {'task_submitted': False,
                    'reason': 'archive_batch_process stopped before task submission because one or more simulation configurations are not currently qualified'},
                'budget': {'provider_calls': 0, 'cost': '0', 'currency': 'CNY',
                    'per_page_reservation': str(quota.payload['per_page_reservation'])},
                'scope': {'real_clinical_data': False, 'real_capacity': 'NOT_RUN',
                    'clinical_scale': 'NOT_RUN', 'saved_and_reread': True},
            }
            report_path = Path(args.report)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(blocked_report, ensure_ascii=False, indent=2) + '\n')
            if json.loads(report_path.read_text()) != blocked_report:
                raise RuntimeError('模拟模型阻断报告保存后回读内容不一致。')
            print(json.dumps(blocked_report, ensure_ascii=False))
            return
        input_price = Decimal(provider.payload['input_per_million'])
        output_price = Decimal(provider.payload['output_per_million'])
        page_reservation = Decimal(quota.payload['per_page_reservation'])
        if (provider.payload['currency'] != quota.payload['currency']
                or page_reservation > Decimal(quota.payload['per_batch_budget'])):
            raise RuntimeError('模拟 Provider 计价与单页预算配置不一致。')

        with django_connection.cursor() as cursor:
            cursor.execute('SELECT current_database(), current_schema(), current_setting(%s)', ['search_path'])
            database_identity = cursor.fetchone()
            cursor.execute('SHOW server_version')
            postgres_version = cursor.fetchone()[0]
        if (database_identity[0:2] != ('sjia_simulation', 'sjia')
                or database_identity[2].replace(' ', '') != 'sjia,public'):
            raise RuntimeError('模拟模型测量数据库身份不符。')

        with transaction.atomic():
            actor, patient, _, pages, quota, _, _ = prepare_sources(
                owner_principal, owner_principal.auth_epoch, fixture['patient_id'],
                {'file_group_ids': [str(group.id)]})
            if len(pages) != 1 or pages[0].file_id != files[0].id:
                raise RuntimeError('模拟模型任务超出单页运行预算。')
            batch = save_received_batch(actor=actor, patient=patient, request_id=uuid4(), pages=pages, quota=quota)
            selected_page = ArchivePage.objects.get(batch_id=batch.id, ordinal=1)
            actor, patient, batch, selected_pages, quota, _, action_context = action_scope(
                actor, owner_principal.auth_epoch, 'archive_batch_process', batch.id,
                {'expected_version': '1', 'page_ids': [str(selected_page.id)]}, mutating=True)
            save_action(actor=actor, patient=patient, batch=batch, pages=selected_pages, quota=quota,
                context=action_context, request_row=None, operation='archive_batch_process')
        selected_page.refresh_from_db()
        task = Task.objects.get(id=selected_page.current_task_id)
        event = OutboxEvent.objects.get(task=task, event_type='archive_page.requested')
        model_call_started = []
        model_call_elapsed = []
        model_call_code = ark._completion.__code__

        def measure_model_call(frame, event_name, _argument):
            if frame.f_code is model_call_code:
                if event_name == 'call':
                    model_call_started.append(perf_counter())
                elif event_name == 'return':
                    model_call_elapsed.append((perf_counter() - model_call_started.pop()) * 1000)

        started = perf_counter()
        sys.setprofile(measure_model_call)
        try:
            worker_result = consume.apply(kwargs={'event_id': str(event.id)}, task_id=str(uuid4())).get(propagate=True)
        finally:
            sys.setprofile(None)
        worker_elapsed_ms = (perf_counter() - started) * 1000
        task.refresh_from_db()
        attempt = TaskAttempt.objects.get(task=task)
        selected_page.refresh_from_db()
        page_attempt = ArchivePageAttempt.objects.get(page=selected_page, task_attempt=attempt)
        usage_evidence = page_attempt.usage_evidence
        response_file = FileObject.objects.get(id=usage_evidence['raw_response_file_id'],
            patient_id=task.patient_id, purpose='ocr_response', status='available')
        response_bytes = object_store.get(response_file.object_key)
        usage = usage_evidence['usage']
        if (task.status != 'awaiting_confirmation' or selected_page.status != 'awaiting_confirmation'
                or attempt.status != TaskAttempt.Status.COMPLETED or len(model_call_elapsed) != 1
                or not attempt.provider_request_id or len(response_bytes) != response_file.size_bytes
                or digest_bytes(response_bytes) != response_file.checksum):
            raise RuntimeError('实际模拟档案页 OCR 调用、任务保存或回读校验失败。')
        input_tokens = usage.get('prompt_tokens')
        output_tokens = usage.get('completion_tokens')
        estimated_cost = None
        cost_status = 'INDETERMINATE'
        if input_tokens is not None and output_tokens is not None:
            estimated_cost = str((Decimal(input_tokens) * input_price +
                Decimal(output_tokens) * output_price) / Decimal('1000000'))
            cost_status = 'SIMULATION_PRICE_ESTIMATE'
            if Decimal(estimated_cost) > page_reservation:
                raise RuntimeError('模拟模型计价结果高于单页预算预留。')

        receipt_count = ConsumerReceipt.objects.filter(consumer='archive_page', event=event).count()
        if receipt_count != 1:
            raise RuntimeError('模拟档案页 OCR 任务消费回执数量不为一。')
        report = {
            'task': 'T31',
            'classification': 'SIMULATION_ARCHIVE_PAGE_ONE_ACTUAL_MODEL_REQUEST',
            'measured_at': datetime.now(timezone.utc).isoformat(),
            'environment': {'database': database_identity[0], 'schema': database_identity[1],
                'search_path': database_identity[2], 'postgresql': postgres_version,
                'runtime': 'simulation', 'isolated_root': str(run_root),
                'database_port': args.database_port, 'broker': 'not_started',
                'global_worker_or_beat_started': False, 'queue_consumption': 0},
            'input': {'source': 'seeded_simulation_fixture', 'mode': fixture['mode'],
                'file_group_id': str(group.id), 'original_file_id': str(files[0].id),
                'media_type': files[0].media_type, 'bytes': files[0].size_bytes,
                'sha256': files[0].checksum, 'archive_batch_id': str(batch.id),
                'archive_page_id': str(selected_page.id), 'pages': len(selected_pages)},
            'provider': {'name': provider.payload['provider'], 'endpoint': provider.payload['endpoint'],
                'model': settings.ARK_VISION_MODEL, 'config_id': str(provider.id),
                'configuration_environment': provider.payload['environment'],
                'actual_external_model_requests': len(model_call_elapsed),
                'request_id': page_attempt.provider_request_id, 'response_model': usage_evidence['model'],
                'input_tokens': input_tokens, 'output_tokens': output_tokens,
                'total_tokens': usage.get('total_tokens'),
                'model_request_elapsed_ms': model_call_elapsed[0],
                'configured_simulation_price': {'input_per_million': str(input_price),
                    'output_per_million': str(output_price), 'currency': provider.payload['currency']},
                'simulation_cost_estimate': estimated_cost, 'simulation_cost_status': cost_status,
                'per_page_reservation': str(page_reservation),
                'actual_invoice_cost': 'NOT_VERIFIED'},
            'task_result': {'event_id': str(event.id), 'task_id': str(task.id),
                'task_status': task.status, 'attempt_id': str(attempt.id),
                'attempt_status': attempt.status, 'task_worker_elapsed_ms': worker_elapsed_ms,
                'worker_result': worker_result, 'provider_request_id_saved': bool(attempt.provider_request_id),
                'consumer_receipts': receipt_count, 'archive_page_attempt_id': str(page_attempt.id),
                'archive_page_attempt_status': page_attempt.status,
                'recorded_page_cost': str(page_attempt.cost), 'recorded_currency': page_attempt.currency,
                'candidate_set_id': str(page_attempt.candidate_set_id),
                'response_file_id': str(response_file.id), 'response_bytes': len(response_bytes),
                'response_sha256': sha256(response_bytes),
                'response_checksum_matches': digest_bytes(response_bytes) == response_file.checksum,
                'response_saved_and_reread': True},
            'scope': {'model_requests': 1, 'files_processed': 1, 'notifications_sent': 0,
                'real_clinical_data': False, 'real_capacity': 'NOT_RUN',
                'clinical_scale': 'NOT_RUN', 'saved_and_reread': True},
        }
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        if json.loads(report_path.read_text()) != report:
            raise RuntimeError('模拟模型报告保存后回读内容不一致。')
        print(json.dumps(report, ensure_ascii=False))
    finally:
        if postgres_started:
            run(['pg_ctl', '--pgdata', str(data_root), '-m', 'fast', '-w', 'stop'])


if __name__ == '__main__':
    main()
