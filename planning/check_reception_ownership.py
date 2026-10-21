import ast
import hashlib
import json
from pathlib import Path
import subprocess
from urllib.parse import unquote, urlsplit


def require(condition, message):
    if not condition:
        raise ValueError(message)


def source_reference(root, reference):
    address = urlsplit(reference)
    require(not address.scheme and not address.netloc and not address.query and address.fragment,
        f'源码引用需要路径和symbol：{reference}')
    path = (root / unquote(address.path)).resolve(strict=True)
    require(path.is_relative_to(root) and path.is_file(), f'源码引用超出项目：{reference}')
    return path, unquote(address.fragment)


def python_symbols(path):
    program = ast.parse(path.read_text(), filename=str(path))
    symbols = {}
    for node in program.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols[node.name] = {'line': node.lineno, 'kind': type(node).__name__}
            if isinstance(node, ast.ClassDef):
                for statement in node.body:
                    targets = statement.targets if isinstance(statement, ast.Assign) else (
                        [statement.target] if isinstance(statement, ast.AnnAssign) else [])
                    for target in targets:
                        if isinstance(target, ast.Name):
                            symbols[node.name + '.' + target.id] = {
                                'line': statement.lineno, 'kind': 'model_field'}
    return symbols


def check_ownership(root, rows, tasks, node_modules):
    ownership_path = root / 'planning/reception-ownership.json'
    decisions_path = root / 'planning/reception-decisions.json'
    ownership = json.loads(ownership_path.read_text())
    decisions = json.loads(decisions_path.read_text())
    require(ownership['schema_version'] == 1 and decisions['schema_version'] == 1,
        '接收责任格式版本不正确')
    requirements = {row['requirement']: row for row in rows}
    entries = {item['requirement']: item for item in ownership['requirements']}
    require(len(entries) == len(ownership['requirements']) and entries.keys() == requirements.keys(),
        '22项功能需要各自维护唯一责任')
    decision_entries = {item['decision_id']: item for item in decisions['decisions']}
    require(len(decision_entries) == len(decisions['decisions']) == 10
        and decision_entries.keys() == {f'D{number:02}' for number in range(1, 11)},
        'D01至D10需要各自维护唯一责任')
    sources = {ownership_path, decisions_path, Path(__file__).resolve(),
        root / 'planning/reception_frontend_ast.mjs'}
    for item in decision_entries.values():
        require(item['status'] == 'NEEDS_INPUT' and item['source_type'] == 'EXPLICIT',
            '当前未知输入需要保持明确状态')
        require(all(isinstance(item[key], str) and item[key].strip()
            for key in ('confirmed', 'pending', 'blocked_actions', 'owner')), '决定责任不完整')
        require(item['tasks'] and set(item['tasks']) <= tasks, '决定的后续任务不存在')
        require(item['evidence_refs'], '决定需要当前依据')
        for reference in item['evidence_refs']:
            path = (root / reference).resolve(strict=True)
            require(path.is_relative_to(root) and path.is_file(), '决定依据需要项目文件')
            sources.add(path)
    references = {reference for item in entries.values()
        for role in ('producer', 'storage', 'consumer', 'fields') for reference in item[role]}
    resolved = {reference: source_reference(root, reference) for reference in references}
    frontend_files = sorted({str(path.relative_to(root)) for path, _ in resolved.values()
        if path.suffix == '.tsx'})
    frontend = json.loads(subprocess.check_output(['node', str(root / 'planning/reception_frontend_ast.mjs')],
        input=json.dumps({'root': str(root), 'node_modules': str(node_modules), 'files': frontend_files}),
        text=True))
    tables = {path: python_symbols(path) for path, _ in resolved.values() if path.suffix == '.py'}
    for name, value in frontend['files'].items():
        tables[root / name] = value['symbols']
    verified = {}
    for reference, (path, symbol) in sorted(resolved.items()):
        require(path in tables and symbol in tables[path], f'当前symbol不存在：{reference}')
        verified[reference] = tables[path][symbol]
        sources.add(path)
    for requirement, item in entries.items():
        row = requirements[requirement]
        require(item['flow'].strip() and item['fields'] and item['decisions'], '功能责任不完整')
        require(set(item['decisions']) <= decision_entries.keys(), '功能引用未知决定')
        for role in ('producer', 'storage', 'consumer'):
            require(item[role], f'功能缺少{role}：{requirement}')
            actual = {str(resolved[reference][0].relative_to(root)) for reference in item[role]}
            require(actual == set(row[role].split(';')), f'清单与symbol引用不一致：{requirement}/{role}')
        for reference in item['fields']:
            require(verified[reference]['kind'] == 'model_field', f'字段需要明确持久模型：{reference}')
    return {
        'status': 'PASS', 'scope': '22项功能的实际函数、持久模型字段、消费组件及未知输入维护责任',
        'counts': {'requirements': len(entries), 'decisions': len(decision_entries),
            'symbols': len(verified), 'model_fields': sum(value['kind'] == 'model_field' for value in verified.values())},
        'python_parser': 'stdlib.ast', 'frontend_parser': {'name': frontend['parser'], 'version': frontend['version']},
        'symbol_references': verified,
        'frontend_imports': {name: value['imports'] for name, value in frontend['files'].items()},
        'business_behavior': 'NOT_RUN', 'complete_field_propagation': 'T35-T38及各业务任务验收',
        'source_sha256': {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(sources)},
    }
