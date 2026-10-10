import ast
import csv
import hashlib
import json
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


def refs(value):
    if isinstance(value, dict):
        if '$ref' in value:
            yield value['$ref']
        for child in value.values():
            yield from refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from refs(child)


def main():
    sources = {Path(__file__).resolve()}
    catalog_path = ROOT / 'backend-design/v2-api-catalog.csv'
    catalog = rows(catalog_path)
    sources.add(catalog_path)
    operations = {row['operation_id']: row for row in catalog}
    require(len(operations) == len(catalog), '接口 operation_id 重复')
    require(len({(row['method'], row['path']) for row in catalog}) == len(catalog), '接口路径重复')
    ledger = json.loads((ROOT / 'planning/delivery-ledger.json').read_text())
    task_ids = {task['id'] for task in ledger['tasks']}
    pages = {row['screen_id'] for row in rows(ROOT / 'ui-delivery/v2/screen-design-map.csv')}
    require({f'FR-{n:02}' for n in range(13, 23)} <= {row['requirement'] for row in catalog}, '新增功能接口不完整')
    runtime_sources = ('backend/api/urls.py', 'backend/api/base.py', 'backend/authorization/actions.py', 'backend/authorization/policy.py', 'backend/requests/service.py', 'backend/tasks/service.py', 'backend/notifications/service.py')
    for name in runtime_sources:
        sources.add(ROOT / name)
    action_tree = ast.parse((ROOT / 'backend/authorization/actions.py').read_text())
    registered_actions = {node.value for node in ast.walk(action_tree) if isinstance(node, ast.Constant) and isinstance(node.value, str) and '_' in node.value}
    planned_actions = {'manage_data_use', 'manage_notifications'}

    schema_files = ('backend-design/v2-protocol.schema.json', 'backend-design/protocol.schema.json', 'data-design/dataset.schema.json', 'data-design/case-extension.schema.json', 'backend-design/runtime.schema.json', 'alert-design/node.schema.json')
    registry = Registry()
    schemas = {}
    for name in schema_files:
        path = ROOT / name
        sources.add(path)
        schema = json.loads(path.read_text())
        Draft202012Validator.check_schema(schema)
        schemas[path] = schema
        resource = Resource.from_contents(schema)
        registry = registry.with_resource(path.as_uri(), resource)
        if '$id' in schema:
            registry = registry.with_resource(schema['$id'], resource)
    resolved_refs = 0
    for path, schema in schemas.items():
        resolver = registry.resolver(path.as_uri())
        for reference in refs(schema):
            resolver.lookup(reference)
            resolved_refs += 1
    defs = schemas[ROOT / schema_files[0]]['$defs']
    behavior_path = ROOT / 'backend-design/v2-service-behavior.md'
    behavior = behavior_path.read_text()
    write_count = 0
    for row in catalog:
        require(all(row.values()), f"接口元数据缺少：{row['operation_id']}")
        require(row['input_schema'] in defs and row['output_schema'] in defs, '接口 Schema 不存在')
        require(row['auth'] in registered_actions | planned_actions | {'provider_signature', 'anonymous_credentials', 'support_session'}, '授权动作没有登记责任')
        require(set(row['screen_refs'].split(';')) <= pages, '接口页面引用不存在')
        require(row['implementation_task'] in task_ids, '接口实施任务不存在')
        read = operations[row['read_operation']]
        require(read['idempotency'] == 'none', '重新读取入口具有业务副作用')
        require(row['transaction'] == 'read_snapshot' or row['transaction'] in behavior, '事务未定义')
        if row['idempotency'] != 'none':
            write_count += 1
            require(row['method'] == 'POST', '写操作 method 不正确')
            require(row['idempotency'] in {'intent', 'provider_event'}, '写操作幂等身份缺少')
        require(row['task_type'] in {'none', 'purpose_copy', 'ocr', 'summary', 'disposition', 'assessment', 'alert_evaluation', 'reconcile'}, '任务种类未定义')

    field_map_path = ROOT / 'backend-design/v2-field-map.csv'
    mapping = rows(field_map_path)
    require(len({row['api_field'] for row in mapping}) == len(mapping), '字段映射重复')
    for row in mapping:
        require(all(row.values()) and row['owner'] in task_ids, '字段映射缺少维护责任')
        definition, field = row['api_field'].split('.', 1)
        require(definition in defs, '字段映射 Schema 不存在')
        properties = defs[definition].get('properties', {})
        require(field.split('.')[0] in properties, '字段映射没有对应属性')

    parser = MarkdownIt('commonmark').enable('table')
    local_links = set()
    for name in ('sJIA合并MAS预警平台后端LLD.md', 'backend-design/v2-service-behavior.md', 'docs/development/T36.md'):
        path = ROOT / name
        sources.add(path)
        for token in parser.parse(path.read_text()):
            for child in token.children or []:
                if child.type == 'link_open':
                    target = urlsplit(child.attrGet('href'))
                    if not target.scheme:
                        link = (path.parent / unquote(target.path)).resolve()
                        pending = {ROOT / 'docs/evidence/CHECK-T36/receipt.json', ROOT / 'docs/evidence/CHECK-T36/output.log'}
                        require(link.is_file() or link in pending, f'本地引用不存在：{link.name}')
                        local_links.add(str(link.relative_to(ROOT)))
    require('BACK V2.0' in (ROOT / 'sJIA合并MAS预警平台后端LLD.md').read_text(), '后端版本缺少')
    sources.update({behavior_path, field_map_path, ROOT / 'ui-delivery/v2/screen-design-map.csv', ROOT / 'data-design/v2-schema.sql'})
    url_tree = ast.parse((ROOT / 'backend/api/urls.py').read_text())
    existing_routes = sum(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'path' for node in ast.walk(url_tree))
    report = {
        'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'status': 'PASS', 'capability': 'DESIGN',
        'counts': {'v2_operations': len(catalog), 'write_operations': write_count, 'schema_definitions': len(defs), 'resolved_schema_refs': resolved_refs, 'field_maps': len(mapping), 'existing_runtime_routes': existing_routes},
        'planned_service_actions': sorted(planned_actions), 'local_links': sorted(local_links),
        'provider_called': False, 'clinical_rows_inserted': 0, 'business_validation': 'NOT_RUN', 'visual_validation': 'NOT_RUN', 'device_validation': 'NOT_RUN',
        'source_sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(sources)},
    }
    output = ROOT / '.planning-work/t36-backend.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
