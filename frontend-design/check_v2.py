import csv
import hashlib
import importlib.util
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from markdown_it import MarkdownIt


ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_task_references(value, task_ids):
    """Validate every semicolon-separated owner, never treat a list as one ID."""
    require(isinstance(value, str), '页面任务引用必须是字符串')
    identifiers = [item.strip() for item in value.split(';')]
    require(all(item and item in task_ids for item in identifiers), '页面任务不存在')
    require(len(identifiers) == len(set(identifiers)), '页面任务引用重复')
    return identifiers


def local_schema_registry(paths, *, root=None):
    """Reuse the strict finite local resolver; external or missing refs fail."""
    path = ROOT / 'backend-design/check_v2.py'
    spec = importlib.util.spec_from_file_location('frontend_local_schema_registry', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.local_schema_registry(paths, root=ROOT if root is None else root)


def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def schema_description_module():
    path = ROOT / 'frontend-design/describe-schema.py'
    spec = importlib.util.spec_from_file_location('frontend_schema_description', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SCHEMA_DESCRIPTION = schema_description_module()
describe = _SCHEMA_DESCRIPTION.describe


def object_fields(schema, resolver):
    result = _SCHEMA_DESCRIPTION.object_definition(schema, resolver,
        allow_empty=True)
    return result['properties'], set(result['required'])


def object_union_description(schema, resolver, name):
    require(not ('oneOf' in schema and 'anyOf' in schema),
        f'同级对象联合组合需要显式交集绑定：{name}')
    result = None
    for keyword in ('oneOf', 'anyOf'):
        if keyword not in schema:
            continue
        shapes = [describe(branch, resolver) for branch in schema[keyword]]
        if all(shape == {'kind': 'types', 'values': ['object']} for shape in shapes):
            require(result is None, f'同级对象联合组合需要显式交集绑定：{name}')
            require(all(set(branch) == {'$ref'} and branch['$ref'].startswith('#/$defs/')
                for branch in schema[keyword]), f'对象联合需要显式本地分支绑定：{name}')
            result = {'union_refs': [branch['$ref'].removeprefix('#/$defs/')
                for branch in schema[keyword]], 'keyword': keyword}
    return result


def main():
    names = ['frontend-design/check_v2.py', 'frontend-design/describe-schema.py', 'frontend-design/check-v2-types.mjs', 'frontend-design/v2-routes.csv',
             'frontend-design/v2-state-adoption.csv', 'frontend-design/v2-token-map.json', 'frontend-design/v2-types.ts',
             'frontend-design/tsconfig.json', 'frontend-design/v2-interaction.md', 'docs/development/T38.md',
             'sJIA合并MAS预警平台前端LLD.md', 'sJIA合并MAS预警平台页面流程.md',
             'ui-delivery/v2/screen-design-map.csv', 'ui-delivery/v2/src/main.jsx', 'ui-delivery/v2/src/styles.css',
             'ui-delivery/v2/src/tokens.css', 'ui-delivery/v2/tokens.json', 'ui-delivery/v2/asset-manifest.csv',
             'ui-delivery/v2/page-asset-map.csv', 'backend-design/v2-api-catalog.csv', 'backend-design/api-catalog.csv']
    sources = {ROOT / name for name in names}
    route_rows = rows(ROOT / 'frontend-design/v2-routes.csv')
    designs = {row['screen_id']: row for row in rows(ROOT / 'ui-delivery/v2/screen-design-map.csv')}
    routes = {row['screen_id']: row for row in route_rows}
    require(len(route_rows) == len(routes) == len(designs) == 39, '页面分母不完整')
    require(routes.keys() == designs.keys(), '页面身份不一致')
    require(len({row['path'] for row in route_rows}) == 39, '页面路由重复')
    api_catalog = rows(ROOT / 'backend-design/api-catalog.csv') + rows(ROOT / 'backend-design/v2-api-catalog.csv')
    operations = {row['operation_id']: row for row in api_catalog}
    ledger = json.loads((ROOT / 'planning/delivery-ledger.json').read_text())
    task_ids = {task['id'] for task in ledger['tasks']}
    states = rows(ROOT / 'frontend-design/v2-state-adoption.csv')
    state_ids = {row['state'] for row in states}
    require(len(states) == len(state_ids) == 13, '状态维护范围不完整')
    state_count = 0
    for row in route_rows:
        require(all(row.values()), '路由责任缺少')
        design = designs[row['screen_id']]
        require(row['path'] == design['source_id'], '路由与原稿不一致')
        require(row['parent_id'] in routes, '页面父级不存在')
        validate_task_references(row['implementation_task'], task_ids)
        expected_audience = 'support' if row['screen_id'] >= 'UI2-34' else 'family'
        require(row['audience'] == expected_audience, '支持角色与家庭角色混用')
        require((row['layout'] == 'primary') == (row['screen_id'] in {'UI2-01', 'UI2-05', 'UI2-15'}), '主导航范围不正确')
        page_states = set(design['required_states'].split(';'))
        require(page_states <= state_ids, '页面状态未维护')
        state_count += len(page_states)
        for key in ('read_operations', 'write_operations'):
            for operation in row[key].split(';'):
                if operation != 'none':
                    require(operation in operations, f'页面接口未登记：{operation}')
                    require(not (row['audience'] == 'family' and operations[operation]['caller'] == 'support'), '家庭调用支持接口')
    require(state_count == 328, '适用状态分母改变')

    token_map = json.loads((ROOT / 'frontend-design/v2-token-map.json').read_text())
    tokens = json.loads((ROOT / token_map['source']).read_text())
    require(token_map['sourceVersion'] == tokens['version'], 'Token 版本不一致')
    for mapping in token_map['tokens']:
        value = tokens
        for key in mapping['source'].split('.'):
            value = value[key]
        require(isinstance(value, (str, int, float)), 'Token 类型不正确')
    assets = rows(ROOT / token_map['assetManifest'])
    asset_ids = {row['asset_id'] for row in assets}
    for row in assets:
        path = (ROOT / 'ui-delivery/v2' / row['file_path']).resolve()
        sources.add(path)
        require(hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256'], '资产字节摘要与设计不一致')
    asset_map = rows(ROOT / token_map['pageAssetMap'])
    require({row['screen_id'] for row in asset_map} == designs.keys(), '页面资产映射不完整')
    require({row['asset_id'] for row in asset_map} <= asset_ids, '页面资产不存在')

    schema_names = ['backend-design/v2-protocol.schema.json', 'backend-design/protocol.schema.json',
        'data-design/dataset.schema.json', 'data-design/case-extension.schema.json']
    registry, local_schemas, _ = local_schema_registry([ROOT / name for name in schema_names])
    sources.update(local_schemas)
    schemas = {name: local_schemas[ROOT / name] for name in schema_names}
    protocol = schemas['backend-design/v2-protocol.schema.json']
    resolver = registry.resolver((ROOT / 'backend-design/v2-protocol.schema.json').as_uri())
    object_descriptions = {}
    for name, schema in protocol['$defs'].items():
        if name in {'FactContextFields', 'TaskView', 'FieldInput', 'FamilyField', 'Error', 'MedicalTime', 'MedicalTimeInput', 'SourceReference'}:
            continue
        try:
            properties, required = object_fields(schema, resolver)
        except ValueError as error:
            raise ValueError(f'Schema 定义无法检查：{name}：{error}') from error
        if properties:
            object_descriptions[name] = {'properties': properties, 'required': sorted(required)}
        elif 'oneOf' in schema or 'anyOf' in schema:
            union = object_union_description(schema, resolver, name)
            if union is not None:
                object_descriptions[name] = union
    description_path = ROOT / '.planning-work/t38-types-description.json'
    description_path.write_text(json.dumps(object_descriptions, ensure_ascii=False, indent=2) + '\n')
    parser = MarkdownIt('commonmark').enable('table')
    pending = {ROOT / 'docs/evidence/CHECK-T38/receipt.json', ROOT / 'docs/evidence/CHECK-T38/output.log'}
    for name in ['sJIA合并MAS预警平台前端LLD.md', 'sJIA合并MAS预警平台页面流程.md', 'frontend-design/v2-interaction.md', 'docs/development/T38.md']:
        path = ROOT / name
        for token in parser.parse(path.read_text()):
            for child in token.children or []:
                if child.type == 'link_open':
                    target = urlsplit(child.attrGet('href'))
                    if not target.scheme:
                        link = (path.parent / unquote(target.path)).resolve()
                        require(link.is_file() or link in pending, f'本地引用不存在：{path.relative_to(ROOT)} -> {link.relative_to(ROOT) if link.is_relative_to(ROOT) else link}')
    process = subprocess.run(['node', str(ROOT / 'frontend-design/check-v2-types.mjs')], cwd=ROOT, env=os.environ.copy(), text=True, capture_output=True, check=False)
    if process.returncode:
        raise ValueError('TypeScript 契约检查未通过：\n' + process.stdout + process.stderr)
    types_report = json.loads(process.stdout)
    report = {
        'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'status': 'PASS', 'capability': 'DESIGN',
        'counts': {'pages': len(routes), 'page_states': state_count, 'states': len(states), 'assets': len(assets), 'page_asset_refs': len(asset_map)},
        'types_and_css': types_report, 'provider_called': False, 'clinical_rows_inserted': 0,
        'business_validation': 'NOT_RUN', 'visual_validation': 'NOT_RUN', 'device_validation': 'NOT_RUN',
        'source_sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(sources)},
    }
    (ROOT / '.planning-work/t38-frontend.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
