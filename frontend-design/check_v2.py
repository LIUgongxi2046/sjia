import argparse
import ast
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
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_task_references(value, task_ids):
    require(isinstance(value, str), '页面任务引用必须是字符串')
    identifiers = [item.strip() for item in value.split(';')]
    require(all(item and item in task_ids for item in identifiers), '页面任务不存在')
    require(len(identifiers) == len(set(identifiers)), '页面任务引用重复')
    return identifiers


def local_schema_registry(paths, *, root=None):
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
    arguments = argparse.ArgumentParser()
    arguments.add_argument('--report-path', type=Path, default=ROOT / '.planning-work/t38-frontend.json')
    options = arguments.parse_args()
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
    runtime_api_path = ROOT / 'frontend-design/runtime-api-map.csv'
    runtime_api = rows(runtime_api_path)
    api_catalog = rows(ROOT / 'backend-design/api-catalog.csv') + rows(ROOT / 'backend-design/v2-api-catalog.csv') + runtime_api
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
    native_script = ROOT / 'frontend-design/read-route-ownership.mjs'
    frontend_sources = sorted(str(path.relative_to(ROOT)) for path in (ROOT / 'guardian-h5/src').rglob('*')
        if path.suffix in ('.ts', '.tsx') and not any(value in path.name for value in ('.test.', '.spec.')))
    native = json.loads(subprocess.check_output(['node', str(native_script)], cwd=ROOT,
        input=json.dumps({'root': str(ROOT), 'node_modules': str(ROOT / 'guardian-h5/node_modules'),
            'sources': frontend_sources}), text=True))
    sources.update({native_script, ROOT / 'guardian-h5/package-lock.json', ROOT / 'guardian-h5/package.json',
        ROOT / 'guardian-h5/vite.config.ts', ROOT / 'guardian-h5/src/main.tsx',
        ROOT / 'backend-design/check_v2.py', *(ROOT / name for name in native['source_files'])})
    native_routes = {audience: native[audience + '_routes'] for audience in ('family', 'support')}
    url_path = ROOT / 'backend/api/urls.py'
    url_tree = ast.parse(url_path.read_text())
    aliases = {item.asname or item.name: statement.module + '.' + item.name for statement in url_tree.body
        if isinstance(statement, ast.ImportFrom) and statement.module is not None for item in statement.names}
    for row in runtime_api:
        require(all(row.values()) and row['owner'] in task_ids, '实际补充接口需要明确维护责任')
        filename, symbol = row['backend_view'].split('#')
        path = ROOT / filename
        tree = ast.parse(path.read_text())
        definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == symbol]
        require(len(definitions) == 1, '补充接口需要准确后端函数')
        declared = [node.args[0] for node in definitions[0].decorator_list if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == 'api_view']
        require(len(declared) == 1 and row['method'] in ast.literal_eval(declared[0]), '补充接口HTTP方法不一致')
        module = '.'.join(path.relative_to(ROOT / 'backend').with_suffix('').parts)
        bindings = [node for node in ast.walk(url_tree) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == 'path' and len(node.args) >= 2
            and isinstance(node.args[0], ast.Constant) and node.args[0].value == row['path'].lstrip('/')
            and isinstance(node.args[1], ast.Attribute) and isinstance(node.args[1].value, ast.Name)
            and aliases.get(node.args[1].value.id) == module and node.args[1].attr == symbol]
        require(len(bindings) == 1, '补充接口需要准确实际URL登记')
        frontend, function = row['frontend_function'].split('#')
        require(function in native['source_symbols'][frontend], '补充接口需要准确前端函数')
        sources.update({path, ROOT / frontend})
    sources.update({runtime_api_path, url_path})
    accepted_routes = set()
    page_ownership = {}
    for row in route_rows:
        selected = [entry for entry in native_routes[row['audience']] if entry['path'] == row['path']]
        require(len(selected) == 1, '设计页面需要唯一实际路由：' + row['screen_id'])
        entry = selected[0]
        require(len(entry['components']) == 1 and entry['components'][0]['symbol'] == row['container'],
            '页面容器需要准确实际导出：' + row['screen_id'])
        accepted_routes.add((row['audience'], row['path']))
        page_ownership[row['screen_id']] = entry
    entry_path = ROOT / 'frontend-design/runtime-entry-map.csv'
    additional = rows(entry_path)
    require(len(additional) == len({(row['audience'], row['path']) for row in additional}), '附加入口身份重复')
    for row in additional:
        require(all(row.values()) and row['related_screen_id'] in routes and row['owner'] in task_ids,
            '附加入口需要明确页面及维护任务')
        selected = [entry for entry in native_routes[row['audience']] if entry['path'] == row['path']]
        require(len(selected) == 1 and len(selected[0]['components']) == 1
            and selected[0]['components'][0]['symbol'] == row['container'], '附加入口没有准确实际组件')
        identity = (row['audience'], row['path'])
        require(identity not in accepted_routes, '附加入口与设计页面重复')
        accepted_routes.add(identity)
    require(accepted_routes == {(audience, entry['path']) for audience, entries in native_routes.items() for entry in entries},
        '实际路由需要完整维护责任')
    sources.add(entry_path)

    asset_catalog = {}
    for name in ('ui-delivery/asset-manifest.csv', 'ui-delivery/identity/asset-manifest.csv', 'ui-delivery/v2/asset-manifest.csv'):
        manifest = ROOT / name
        sources.add(manifest)
        for row in rows(manifest):
            path = (manifest.parent / row['file_path']).resolve()
            require(path.is_relative_to(ROOT), '资产清单引用超出项目')
            asset_catalog.setdefault(path, []).append({'manifest': name, **row})
    current_assets = []
    for item in [*native['asset_imports'], *native['public_assets']]:
        path = (ROOT / item['path']).resolve(strict=True)
        source = (ROOT / item['design_source']).resolve(strict=True) if 'design_source' in item else path
        require(path.is_relative_to(ROOT) and source.is_relative_to(ROOT) and source in asset_catalog,
            '当前图片需要明确设计来源')
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, '公开资产与准确设计来源不一致')
        with Image.open(source) as picture:
            width, height, format_name, mode = *picture.size, picture.format, picture.mode
        for expected in asset_catalog[source]:
            require((width, height) == (int(expected['width']), int(expected['height']))
                and format_name == expected['format'].upper(), '当前图片元数据与设计清单不一致')
            if expected.get('sha256'):
                require(expected['sha256'] == digest, '当前图片与准确清单摘要不一致')
        current_assets.append({'path': item['path'], 'design_source': str(source.relative_to(ROOT)),
            'sha256': digest, 'width': width, 'height': height, 'format': format_name, 'mode': mode,
            'manifests': sorted({row['manifest'] for row in asset_catalog[source]}),
            'manifest_sha256_available': any(row.get('sha256') for row in asset_catalog[source])})
        sources.update({ROOT / item['path'], source})
    frame_styles = ['ui-delivery/v2/src/styles.css', 'guardian-h5/src/styles/rich-ui.css',
        'guardian-h5/src/styles/rich-pages.css', 'guardian-h5/src/styles/rich-registration.css',
        'guardian-h5/src/styles/rich-theme.css']
    require(native['frame_style_order'] == frame_styles, '正式Frame需要准确CSS来源顺序')
    companion = [item for sheet in native['css_sources'] for item in sheet['companion']]
    require(len(companion) == 1 and companion[0]['selector'] == '.home-twin'
        and companion[0]['value'] == 'clamp(232px,64vw,300px)', '首页形象尺寸需要准确样式依据')
    sources.update(ROOT / item['path'] for item in native['css_sources'])

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
        properties, required = object_fields(schema, resolver)
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
                        if link not in pending and link != ROOT / 'planning/delivery-ledger.json':
                            sources.add(link)
    process = subprocess.run(['node', str(ROOT / 'frontend-design/check-v2-types.mjs')], cwd=ROOT, env=os.environ.copy(), text=True, capture_output=True, check=False)
    if process.returncode:
        raise ValueError('TypeScript类型检查未通过：\n' + process.stdout + process.stderr)
    types_report = json.loads(process.stdout)
    report = {
        'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'status': 'PASS', 'capability': 'DESIGN',
        'counts': {'pages': len(routes), 'page_states': state_count, 'states': len(states), 'assets': len(assets), 'page_asset_refs': len(asset_map)},
        'runtime_ownership': {'pages': page_ownership, 'additional_entries': additional,
            'runtime_api_bindings': runtime_api,
            'family_route_count': len(native['family_routes']), 'support_route_count': len(native['support_routes']),
            'parser': native['parser'], 'parser_version': native['version']},
        'current_assets': current_assets, 'current_css': native['css_sources'], 'frame_style_order': native['frame_style_order'],
        'companion_height': companion[0], 'postcss_version': native['postcss_version'],
        'types_and_css': types_report, 'provider_called': False, 'clinical_rows_inserted': 0,
        'business_validation': 'NOT_RUN', 'visual_validation': 'NOT_RUN', 'device_validation': 'NOT_RUN',
        'source_sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(sources)},
    }
    options.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
