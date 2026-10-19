import argparse
import csv
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from markdown_it import MarkdownIt

from check_reception_ownership import check_ownership

ROOT = Path(__file__).resolve().parent.parent


def require(condition, message):
    if not condition:
        raise ValueError(message)


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def probe_runtime(source, python, settings, paths, *, api_url=None, frontend_url=None):
    command = [str(python), str(ROOT / 'planning/probe_reception_runtime.py'),
        '--runtime-source', str(source), '--settings-module', settings]
    for path in paths:
        command.extend(['--pythonpath', str(path)])
    if api_url:
        command.extend(['--api-url', api_url, '--frontend-url', frontend_url])
    return json.loads(subprocess.check_output(command, text=True))


def main():
    arguments = argparse.ArgumentParser()
    arguments.add_argument('--api-url', default='http://127.0.0.1:8000')
    arguments.add_argument('--frontend-url', default='http://127.0.0.1:5173/')
    arguments.add_argument('--report-path', type=Path, default=ROOT / '.planning-work/t34-reception.json')
    arguments.add_argument('--node-modules', type=Path, required=True)
    arguments.add_argument('--runtime-source', type=Path, required=True)
    arguments.add_argument('--runtime-python', type=Path, required=True)
    arguments.add_argument('--runtime-settings', required=True)
    arguments.add_argument('--runtime-pythonpath', type=Path, action='append', default=[])
    arguments.add_argument('--candidate-settings', required=True)
    arguments.add_argument('--candidate-pythonpath', type=Path, action='append', default=[])
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
    ownership = check_ownership(ROOT, rows, tasks, options.node_modules.resolve(strict=True))
    sources.update(ROOT / name for name in ownership['source_sha256'])
    sources.add(ROOT / 'planning/probe_reception_runtime.py')

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
                    if not target.scheme and target.path:
                        link = (path.parent / unquote(target.path)).resolve()
                        # 收据和输出由调用检查的证据工具在执行结束时创建。
                        pending = {ROOT / 'docs/evidence/CHECK-T34/receipt.json', ROOT / 'docs/evidence/CHECK-T34/output.log'}
                        require(link.is_file() or link in pending, f'引用文件不存在：{link}')

    require(options.runtime_python.is_file(), '运行Python不存在')
    runtime = probe_runtime(options.runtime_source.resolve(strict=True), options.runtime_python.absolute(),
        options.runtime_settings, options.runtime_pythonpath, api_url=options.api_url, frontend_url=options.frontend_url)
    candidate = probe_runtime(ROOT, Path(sys.executable), options.candidate_settings, options.candidate_pythonpath)
    require(candidate['database']['name'] != runtime['database']['name'], '候选验证需要明确的独立数据库')

    for name in ('planning/environment.json', 'guardian-h5/package.json', 'guardian-h5/package-lock.json', 'backend/requirements.lock.txt', 'ui-delivery/v2/screen-design-map.csv', 'ui-delivery/v2/tokens.json', 'ui-delivery/v2/asset-manifest.csv', 'sJIA合并MAS预警平台PRD.md'):
        sources.add(ROOT / name)
    report = {
        'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        'status': 'PASS', 'capability': 'DESIGN', 'scope': '当前功能职责、未知输入责任、完整设计清单及分别核验的运行身份',
        'counts': {'requirements': len(rows), 'pages': len(pages), 'states': 328, 'support_pages': 6},
        'checker': {'python': platform.python_version(),
            'node': subprocess.check_output(['node', '--version'], text=True).strip()},
        'runtime': runtime, 'candidate_schema': candidate,
        'candidate_artifact_matches_runtime': runtime['backend_artifact_sha256'] == candidate['backend_artifact_sha256'],
        'ownership_validation': ownership, 'business_validation': 'NOT_RUN',
        'visual_validation': 'NOT_RUN', 'real_devices': 'NOT_RUN',
        'task_responsibility': [{key: task[key] for key in ('id', 'acceptance_ids', 'depends_on', 'allowed_paths')}
            for task in ledger['tasks']],
        'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sources)},
    }
    options.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'capability': report['capability'], 'counts': report['counts'],
        'ownership': ownership['counts'], 'runtime_migrations': runtime['inventory']['migrations'],
        'candidate_migrations': candidate['inventory']['migrations'],
        'candidate_artifact_matches_runtime': report['candidate_artifact_matches_runtime'],
        'report_path': str(options.report_path)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
