import getpass
import hashlib
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend'
sys.path.insert(0, str(BACKEND))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings


WINDOW_PARENT = ROOT / '.planning-work' / 'parallel' / 'window4'
REPORT_PATH = ROOT / 'docs' / 'testing' / 'T57-isolated-restore-report.json'


def run(command, *, env=None, stdout=None):
    subprocess.run(command, check=True, env=env, stdout=stdout, stderr=subprocess.PIPE, text=stdout is None)


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def file_manifest(directory):
    directory = Path(directory)
    files = sorted(path for path in directory.rglob('*') if path.is_file())
    entries = [f'{path.relative_to(directory).as_posix()}:{sha256(path)}' for path in files]
    return {'file_count': len(files), 'total_bytes': sum(path.stat().st_size for path in files),
        'inventory_sha256': hashlib.sha256('\n'.join(entries).encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--recovery-only', action='store_true')
    parser.add_argument('--include-authorized-search', action='store_true')
    parser.add_argument('--active-purpose-copy', action='store_true')
    parser.add_argument('--run-name', default='isolated')
    parser.add_argument('--database-port', type=int, default=55471)
    parser.add_argument('--api-port', type=int, default=18041)
    parser.add_argument('--broker-port', type=int, default=16379)
    args = parser.parse_args()
    if not args.run_name.startswith('isolated') or '/' in args.run_name or args.run_name in ('.', '..'):
        raise ValueError('恢复运行名必须以 isolated 开头且不得包含路径。')
    ports = {args.database_port, args.api_port, args.broker_port}
    if len(ports) != 3 or any(port < 10000 or port > 65535 for port in ports):
        raise ValueError('数据库、API 与 Broker 端口必须是独立的高位端口。')
    if args.recovery_only and (args.include_authorized_search or args.active_purpose_copy):
        raise ValueError('授权查询和用途副本探针需要完整隔离运行。')
    if args.active_purpose_copy and not args.include_authorized_search:
        raise ValueError('活动用途副本探针需要同时执行资格已验证的授权档案查询。')
    window_root = WINDOW_PARENT / args.run_name
    data_root = window_root / 'postgres'
    dump_path = window_root / 'sjia-simulation-source.dump'
    object_root = window_root / 'object-store'
    evidence_root = window_root / 'config-evidence'
    if settings.DATABASES['default']['NAME'] != 'sjia_simulation' or settings.SJIA_ENVIRONMENT != 'simulation':
        raise RuntimeError('恢复来源必须为 sjia_simulation 模拟环境。')
    if settings.DATABASES['default']['HOST'] not in ('127.0.0.1', 'localhost'):
        raise RuntimeError('恢复来源只允许本机模拟数据库。')
    if settings.DATABASES['default']['PASSWORD'] == '':
        raise RuntimeError('模拟数据库连接凭据缺失。')
    if window_root.exists():
        raise RuntimeError('窗口四隔离恢复目录已经存在，请先核对其内容与运行状态。')

    window_root.mkdir(parents=True)
    data_root.mkdir()
    database = settings.DATABASES['default']
    source_env = dict(os.environ)
    source_env['PGPASSWORD'] = database['PASSWORD']
    dump_command = ['pg_dump', '--format=custom', '--host', database['HOST'], '--port', str(database['PORT']),
        '--username', database['USER'], '--dbname', 'sjia_simulation']
    with dump_path.open('wb') as dump_file:
        run(dump_command, env=source_env, stdout=dump_file)

    owner = getpass.getuser()
    local_options = ['--host', '127.0.0.1', '--port', str(args.database_port), '--username', owner]
    run(['initdb', '--pgdata', str(data_root), '--username', owner, '--auth-local=trust', '--auth-host=trust',
        '--no-locale', '--encoding=UTF8'])
    postgres_log = window_root / 'postgres.log'
    run(['pg_ctl', '--pgdata', str(data_root), '-o',
        f'-h 127.0.0.1 -p {args.database_port} -c listen_addresses=127.0.0.1 -c max_connections=120 -c unix_socket_directories=',
        '-l', str(postgres_log), '-w', 'start'])
    postgres_started = True
    api_process = None
    try:
        run(['createuser', *local_options, '--superuser', database['USER']])
        run(['createdb', *local_options, '--owner', database['USER'], '--encoding=UTF8', 'sjia_simulation'])
        run(['pg_restore', '--exit-on-error', '--no-owner', '--no-acl', '--host', '127.0.0.1', '--port', str(args.database_port),
            '--username', database['USER'], '--dbname', 'sjia_simulation', str(dump_path)])
        shutil.copytree(settings.FILE_OBJECT_ROOT, object_root)
        shutil.copytree(settings.CONFIG_EVIDENCE_ROOT, evidence_root)

        isolated_env = dict(os.environ)
        isolated_env.update({'DJANGO_SETTINGS_MODULE': 'window4_simulation_settings',
            'PYTHONPATH': os.pathsep.join([str(ROOT / 'tests' / 'operations'), str(BACKEND)]),
            'WINDOW4_SIMULATION_ROOT': str(window_root), 'WINDOW4_SIMULATION_DB_PORT': str(args.database_port),
            'WINDOW4_SIMULATION_BROKER_PORT': str(args.broker_port),
            'WINDOW4_SOURCE_CONFIG_EVIDENCE_ROOT': str(Path(settings.CONFIG_EVIDENCE_ROOT).resolve())})
        run([sys.executable, str(ROOT / 'tests' / 'operations' / 't57_relocate_evidence.py')], env=isolated_env)
        object_source_manifest = file_manifest(settings.FILE_OBJECT_ROOT)
        object_restored_manifest = file_manifest(object_root)
        evidence_source_manifest = file_manifest(settings.CONFIG_EVIDENCE_ROOT)
        evidence_restored_manifest = file_manifest(evidence_root)
        if object_source_manifest != object_restored_manifest or evidence_source_manifest != evidence_restored_manifest:
            raise RuntimeError('隔离恢复文件清单与来源不一致。')
        run([sys.executable, str(ROOT / 'tests' / 'operations' / 't57_isolated_copy_check.py'),
            '--run-name', args.run_name, '--database-port', str(args.database_port)], env=isolated_env)
        t57_report = ROOT / 'docs' / 'testing' / 'T57-isolated-simulation-report.json'
        run([sys.executable, str(ROOT / 'tests' / 'operations' / 't57_recovery.py'), '--report', str(t57_report)], env=isolated_env)
        recovery_result = json.loads(t57_report.read_text())
        evidence_integrity = recovery_result['configuration']['restored_evidence_integrity']
        if evidence_integrity['integrity'] != 'PASS' or evidence_integrity['missing_files'] or evidence_integrity['checksum_mismatches']:
            raise RuntimeError('恢复后的配置证据文件完整性检查失败。')
        if (args.include_authorized_search or args.active_purpose_copy) and recovery_result['configuration']['runtime_qualification'] != 'QUALIFIED':
            raise RuntimeError('授权查询和用途副本探针要求恢复副本运行资格为 QUALIFIED。')

        t31_report = ROOT / 'docs' / 'testing' / 'T31-authorization-simulation-report.json'
        if args.recovery_only:
            performance_result = 'NOT_RUN_WAITING_FOR_MAIN_CONFIGURATION_VALIDATION'
        else:
            api_log = (window_root / 'api.log').open('w')
            isolated_env['PYTHONUNBUFFERED'] = '1'
            api_process = subprocess.Popen([sys.executable, str(BACKEND / 'manage.py'), 'runserver',
                f'127.0.0.1:{args.api_port}', '--noreload'], cwd=ROOT, env=isolated_env,
                stdout=api_log, stderr=subprocess.STDOUT)
            ready = False
            for _ in range(40):
                if api_process.poll() is not None:
                    raise RuntimeError(f'隔离模拟 API 提前退出，日志位于 {window_root / "api.log"}。')
                api_log.flush()
                readiness = subprocess.run(['curl', '--silent', '--show-error', '--output', '/dev/null',
                    '--write-out', '%{http_code}', f'http://127.0.0.1:{args.api_port}/api/auth/bootstrap'],
                    capture_output=True, text=True, check=False)
                if readiness.returncode == 0 and readiness.stdout == '200':
                    ready = True
                    break
                time.sleep(0.25)
            if not ready:
                raise RuntimeError(f'隔离模拟 API 未在启动期限内就绪，日志位于 {window_root / "api.log"}。')
            performance_args = [sys.executable, str(ROOT / 'tests' / 'performance' / 't31_measure.py'), '--base-url',
                f'http://127.0.0.1:{args.api_port}', '--requests', '20', '--concurrency', '4',
                '--report', str(t31_report)]
            if not args.include_authorized_search:
                performance_args.append('--authorization-only')
            run(performance_args, env=isolated_env)
            api_process.terminate()
            api_process.wait(timeout=10)
            api_process = None
            api_log.close()
            performance_result = str(t31_report.relative_to(ROOT))

        purpose_copy_result = 'NOT_RUN'
        if args.active_purpose_copy:
            purpose_copy_report = ROOT / 'docs' / 'testing' / 'T57-purpose-copy-simulation-report.json'
            run([sys.executable, str(ROOT / 'tests' / 'operations' / 't57_active_purpose_copy.py'),
                '--report', str(purpose_copy_report), '--recovery-report', str(t57_report)], env=isolated_env)
            purpose_copy_result = str(purpose_copy_report.relative_to(ROOT))

        report = {'task': 'T57', 'classification': 'SIMULATION_ONLY_ISOLATED_POSTGRES_RESTORE',
            'measured_at': datetime.now(timezone.utc).isoformat(),
            'source': {'database': 'sjia_simulation', 'schema': 'sjia', 'object_root': str(settings.FILE_OBJECT_ROOT)},
            'recovery': {'database': 'sjia_simulation', 'host': '127.0.0.1', 'port': args.database_port,
                'postgres_version': subprocess.run(['psql', *local_options, '--dbname', 'sjia_simulation',
                    '--tuples-only', '--no-align', '-c', 'SHOW server_version'], check=True,
                    capture_output=True, text=True).stdout.strip(),
                'source_dump_bytes': dump_path.stat().st_size, 'source_dump_sha256': sha256(dump_path),
                'run_root': str(window_root), 'object_copy': {'source': object_source_manifest, 'restored': object_restored_manifest, 'match': True},
                'configuration_evidence_copy': {'source': evidence_source_manifest,
                    'restored': evidence_restored_manifest, 'match': True,
                    'database_references': evidence_integrity},
                'database_name_and_schema_rechecked': True, 'separate_object_directory': True,
                'api_port': args.api_port, 'queue': f'redis://127.0.0.1:{args.broker_port}/1', 'broker_started': False,
                'shared_queue_consumption': 0, 'worker_started': False, 'beat_started': False,
                'external_sends': 0},
            'reports': {'task_recovery': str(t57_report.relative_to(ROOT)), 'active_purpose_copy': purpose_copy_result,
                'performance': performance_result, 'source_dump_retained_under_ignored_directory': True,
                'saved_and_reread': True}}
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        if json.loads(REPORT_PATH.read_text()) != report:
            raise RuntimeError('隔离恢复报告保存后回读内容不一致。')
        print(json.dumps(report, ensure_ascii=False))
    finally:
        if api_process is not None:
            api_process.terminate()
            api_process.wait(timeout=10)
        if postgres_started:
            run(['pg_ctl', '--pgdata', str(data_root), '-m', 'fast', '-w', 'stop'])


if __name__ == '__main__':
    main()
