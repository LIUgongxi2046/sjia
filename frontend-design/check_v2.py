import csv
import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator
from markdown_it import MarkdownIt
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def object_fields(schema, resolver):
    if '$ref' in schema:
        resolved = resolver.lookup(schema['$ref'])
        return object_fields(resolved.contents, resolved.resolver)
    properties = {}
    required = set()
    for parent in schema.get('allOf', []):
        if '$ref' in parent or 'properties' in parent:
            inherited, keys = object_fields(parent, resolver)
            properties.update(inherited)
            required.update(keys)
    properties.update({name: describe(value, resolver) for name, value in schema.get('properties', {}).items()})
    required.update(schema.get('required', []))
    return properties, required


def describe(schema, resolver):
    if '$ref' in schema:
        resolved = resolver.lookup(schema['$ref'])
        return describe(resolved.contents, resolved.resolver)
    if 'enum' in schema:
        return {'kind': 'enum', 'values': schema['enum']}
    if 'const' in schema:
        return {'kind': 'enum', 'values': [schema['const']]}
    if 'type' in schema:
        return {'kind': 'types', 'values': schema['type'] if isinstance(schema['type'], list) else [schema['type']]}
    if 'oneOf' in schema:
        shapes = [describe(child, resolver) for child in schema['oneOf']]
        require(all(shape['kind'] == 'types' for shape in shapes), '联合字段类别不明确')
        return {'kind': 'types', 'values': sorted({item for shape in shapes for item in shape['values']})}
    if 'allOf' in schema:
        shapes = [describe(child, resolver) for child in schema['allOf'] if '$ref' in child or 'type' in child]
        if shapes:
            require(all(shape['kind'] == 'types' for shape in shapes), '交集类别不明确')
            return {'kind': 'types', 'values': sorted({item for shape in shapes for item in shape['values']})}
    if 'properties' in schema:
        return {'kind': 'types', 'values': ['object']}
    raise ValueError('Schema 字段缺少类型')


def main():
    names = ['frontend-design/check_v2.py', 'frontend-design/check-v2-types.mjs', 'frontend-design/v2-routes.csv',
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
        require(row['parent_id'] in routes and row['implementation_task'] in task_ids, '页面父级或任务不存在')
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

    registry = Registry()
    schemas = {}
    for name in ['backend-design/v2-protocol.schema.json', 'backend-design/protocol.schema.json', 'data-design/dataset.schema.json', 'data-design/case-extension.schema.json']:
        path = ROOT / name
        sources.add(path)
        schema = json.loads(path.read_text())
        Draft202012Validator.check_schema(schema)
        schemas[name] = schema
        registry = registry.with_resource(path.as_uri(), Resource.from_contents(schema))
    protocol = schemas['backend-design/v2-protocol.schema.json']
    resolver = registry.resolver((ROOT / 'backend-design/v2-protocol.schema.json').as_uri())
    object_descriptions = {}
    for name, schema in protocol['$defs'].items():
        properties, required = object_fields(schema, resolver)
        if properties and name not in {'FactContextFields', 'TaskView', 'FieldInput', 'FamilyField', 'Error', 'MedicalTime', 'MedicalTimeInput', 'SourceReference'}:
            object_descriptions[name] = {'properties': properties, 'required': sorted(required)}
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
                        require(link.is_file() or link in pending, f'本地引用不存在：{link.name}')
    process = subprocess.run(['node', str(ROOT / 'frontend-design/check-v2-types.mjs')], cwd=ROOT, env=os.environ.copy(), text=True, capture_output=True, check=True)
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
