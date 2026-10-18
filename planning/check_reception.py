import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from markdown_it import MarkdownIt


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django

django.setup()

from django.db import connection, transaction
from django.db.migrations.loader import MigrationLoader
import redis


def require(condition, message):
    if not condition:
        raise ValueError(message)


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def main():
    arguments = argparse.ArgumentParser()
    arguments.add_argument('--api-url', default='http://127.0.0.1:8000')
    arguments.add_argument('--frontend-url', default='http://127.0.0.1:5173/')
    arguments.add_argument('--report-path', type=Path, default=ROOT / '.planning-work/t34-reception.json')
    options = arguments.parse_args()
    for value in (options.api_url, options.frontend_url):
        address = urlsplit(value)
        require(address.scheme == 'http' and address.hostname in {'127.0.0.1', 'localhost'}
            and not address.username and not address.password and not address.query and not address.fragment
            and address.path in {'', '/'}, '接收检查需要明确的本地服务地址')
    rows = csv_rows(ROOT / 'planning/reception-v2.csv')
    require(len(rows) == 22, '功能接收清单数量必须为 22')
    require({row['requirement'] for row in rows} == {f'FR-{n:02}' for n in range(1, 23)}, '功能接收编号不完整')
    ledger = json.loads((ROOT / 'planning/delivery-ledger.json').read_text())
    tasks = {task['id'] for task in ledger['tasks']}
    prd = (ROOT / 'sJIA合并MAS预警平台PRD.md').read_text()
    sources = {ROOT / 'planning/reception-v2.csv', Path(__file__).resolve()}
    for row in rows:
        require(all(row.values()), f"接收内容为空：{row['requirement']}")
        require(set(row['implementation_task'].split(';')) <= tasks, '接收任务不存在')
        for acceptance in row['acceptance'].split(';'):
            require(acceptance in prd, f'验收编号不存在：{acceptance}')
        for key in ('producer', 'storage', 'consumer'):
            for name in row[key].split(';'):
                path = ROOT / name
                require(path.is_file(), f'接收源码不存在：{name}')
                sources.add(path)

    pages = csv_rows(ROOT / 'ui-delivery/v2/screen-design-map.csv')
    require(len(pages) == 39 and {p['screen_id'] for p in pages} == {f'UI2-{n:02}' for n in range(1, 40)}, '39 页清单不完整')
    require(sum(len(p['required_states'].split(';')) for p in pages) == 328, '适用状态清单不完整')
    require(sum(p['source_type'] == 'INFERRED' for p in pages) == 6, '受控支持页面接收不完整')
    for page in pages:
        require(set(page['requirement_refs'].split(';')) <= {r['requirement'] for r in rows}, '页面引用未知功能')

    parser = MarkdownIt('commonmark').enable('table')
    hld_path = ROOT / 'sJIA合并MAS预警平台HLD.md'
    hld = hld_path.read_text()
    for number in range(1, 19):
        require(f'NFR-{number:02}' in hld, 'HLD 缺少非功能要求')
    for number in range(1, 11):
        require(f'OPT-{number:02}' in hld, 'HLD 缺少优化责任')
        require(f'D{number:02}' in hld, 'HLD 缺少决定责任')
    require('HLD V2.0' in hld, 'HLD 版本未更新')
    for path in (hld_path, ROOT / 'docs/development/T34.md', ROOT / 'docs/knowledge/prd2-delivery.md'):
        sources.add(path)
        for token in parser.parse(path.read_text()):
            for child in token.children or []:
                if child.type == 'link_open':
                    target = urlsplit(child.attrGet('href'))
                    if not target.scheme:
                        link = (path.parent / unquote(target.path)).resolve()
                        # 收据和输出由调用检查的证据工具在执行结束时创建。
                        pending = {ROOT / 'docs/evidence/CHECK-T34/receipt.json', ROOT / 'docs/evidence/CHECK-T34/output.log'}
                        require(link.is_file() or link in pending, f'引用文件不存在：{link}')

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
            cursor.execute('SELECT version(), current_database(), current_user, inet_server_port()')
            database_version, database_name, database_user, database_port = cursor.fetchone()
            cursor.execute('SELECT count(*) FROM sjia.record')
            record_count = cursor.fetchone()[0]
            cursor.execute('SELECT count(*) FROM sjia.record_version')
            version_count = cursor.fetchone()[0]
            cursor.execute('SELECT count(*) FROM sjia.file_object')
            file_count = cursor.fetchone()[0]
        loader = MigrationLoader(connection)
        pending_migrations = sorted(set(loader.disk_migrations) - set(loader.applied_migrations))
    require(not pending_migrations, f'已有数据库存在未执行迁移：{pending_migrations}')
    broker = redis.Redis(host='127.0.0.1', port=6379, socket_connect_timeout=3, socket_timeout=3)
    require(broker.ping(), 'Redis 读取失败')
    broker_version = broker.info('server')['redis_version']
    with urlopen(urljoin(options.api_url, '/internal/readiness'), timeout=10) as response:
        readiness = json.load(response)
    require(readiness['status'] == 'ready', '后端依赖未就绪')
    with urlopen(options.frontend_url, timeout=10) as response:
        frontend_status = response.status
        frontend_cache = response.headers.get('Cache-Control')
    require(frontend_status == 200 and frontend_cache == 'no-store', '正式前端未正常提供禁止缓存响应')

    for name in ('planning/environment.json', 'guardian-h5/package.json', 'guardian-h5/package-lock.json', 'backend/requirements.lock.txt', 'ui-delivery/v2/screen-design-map.csv', 'ui-delivery/v2/tokens.json', 'ui-delivery/v2/asset-manifest.csv', 'sJIA合并MAS预警平台PRD.md'):
        sources.add(ROOT / name)
    report = {
        'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        'status': 'PASS', 'capability': 'DESIGN', 'scope': '接收清单结构、引用与实际运行身份',
        'counts': {'requirements': len(rows), 'pages': len(pages), 'states': 328, 'support_pages': 6},
        'runtime': {'python': platform.python_version(), 'node': subprocess.check_output(['node', '--version'], text=True).strip(),
                    'django': django.get_version(), 'celery': importlib.metadata.version('celery'),
                    'database': {'version': database_version, 'name': database_name, 'user': database_user, 'port': database_port},
                    'redis': broker_version, 'readiness': readiness, 'frontend_http': frontend_status, 'frontend_cache': frontend_cache,
                    'api_url': options.api_url, 'frontend_url': options.frontend_url},
        'existing_inventory': {'records': record_count, 'record_versions': version_count, 'files': file_count, 'pending_migrations': pending_migrations},
        'semantic_ownership_validation': 'NOT_RUN', 'business_validation': 'NOT_RUN',
        'visual_validation': 'NOT_RUN', 'real_devices': 'NOT_RUN',
        'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sources)},
    }
    options.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
