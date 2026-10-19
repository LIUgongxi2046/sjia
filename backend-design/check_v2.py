import argparse
import ast
import csv
import hashlib
import json
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
import os
import platform
import subprocess
import sys
from string import Formatter
from urllib.parse import unquote, urljoin, urlsplit
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource
from referencing.jsonschema import DRAFT202012


ROOT = Path(__file__).resolve().parents[1]
# 准确别名来自originalSourceSchema.ts的实际登记。
LOCAL_SCHEMA_ALIASES = {'backend/files/record-search.schema.json': 'backend/records/search.schema.json'}


def catalog_schema(name, definitions):
    if name in definitions:
        return ROOT / 'backend-design/v2-protocol.schema.json', definitions[name]
    reference = name
    if '.schema.json' not in reference:
        namespace, definition = reference.rsplit('.', 1)
        reference = namespace + '.schema.json#/$defs/' + definition
    filename, _, fragment = reference.partition('#')
    path = ((ROOT if '/' in filename else ROOT / 'backend-design') / filename).resolve()
    if not path.is_relative_to(ROOT) or not filename.endswith('.schema.json'):
        raise ValueError('接口 Schema 必须引用仓库内文件')
    document = json.loads(path.read_text())
    Draft202012Validator.check_schema(document)
    registry = Registry().with_resource(path.as_uri(), DRAFT202012.create_resource(document))
    schema = registry.resolver(path.as_uri()).lookup('#' + fragment).contents if fragment else document
    return path, schema


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def scoped_refs(value, base_uri):
    resource = DRAFT202012.create_resource(value)
    current = urljoin(base_uri, resource.id() or '')
    if isinstance(resource.contents, dict):
        for keyword in ('$ref', '$dynamicRef'):
            if keyword in resource.contents:
                yield current, resource.contents[keyword]
    for child in resource.subresources():
        yield from scoped_refs(child.contents, current)


def local_schema_registry(paths, *, root=None, runtime_aliases=()):
    root = (ROOT if root is None else root).resolve()
    schemas, aliases = {}, {}
    pending = list(paths)
    registered_paths = {}
    for binding in runtime_aliases:
        target = Path(binding['path']).resolve(strict=True)
        require(target.is_relative_to(root), '实际Schema来源需要位于项目目录')
        for parent in {Path(item).resolve().parent for item in paths}:
            identifier = urljoin(parent.as_uri() + '/', binding['alias'])
            require(identifier not in registered_paths or registered_paths[identifier] == target, '实际Schema登记存在冲突')
            registered_paths[identifier] = target

    def local_path(uri):
        target = urlsplit(uri)
        require(target.scheme == 'file' and not target.netloc and not target.query,
            f'Schema 引用不是受控本地文件：{uri}')
        path = Path(unquote(target.path)).resolve()
        require(path.is_relative_to(root) and path.name.endswith('.schema.json'),
            f'Schema 引用超出仓库或不是 Schema 文件：{uri}')
        if path.as_uri() in registered_paths:
            return registered_paths[path.as_uri()]
        relative = str(path.relative_to(root))
        if relative in LOCAL_SCHEMA_ALIASES:
            path = (root / LOCAL_SCHEMA_ALIASES[relative]).resolve()
            require(path.is_relative_to(root) and path.name.endswith('.schema.json'),
                f'Schema 固定别名超出仓库：{uri}')
        return path

    while pending:
        path = local_path(Path(pending.pop()).absolute().as_uri())
        if path in schemas:
            continue
        schema = json.loads(path.read_text())
        Draft202012Validator.check_schema(schema)
        schemas[path] = schema
        identifiers = {path.as_uri()}
        identifiers.update((root / alias).as_uri() for alias, target in LOCAL_SCHEMA_ALIASES.items()
            if (root / target).resolve() == path)
        if isinstance(schema, dict) and '$id' in schema:
            identifiers.add(urljoin(path.as_uri(), schema['$id']))
        for identifier in identifiers:
            require(identifier not in aliases or aliases[identifier] == path,
                f'Schema 标识有多个本地来源：{identifier}')
            aliases[identifier] = path
        for base_uri, reference in scoped_refs(schema, path.as_uri()):
            target = urlsplit(urljoin(base_uri, reference))
            if target.scheme == 'file':
                dependency = local_path(target._replace(fragment='').geturl())
                if dependency not in schemas:
                    pending.append(dependency)
    for binding in runtime_aliases:
        path = Path(binding['path']).resolve(strict=True)
        require(path in schemas, '实际Schema登记需要准确本地来源')
        for parent in {item.parent for item in schemas}:
            identifier = urljoin(parent.as_uri() + '/', binding['alias'])
            require(identifier not in aliases or aliases[identifier] == path, '实际Schema别名引用冲突')
            aliases[identifier] = path

    def no_external_retrieval(uri):
        raise NoSuchResource(ref=uri)

    registry = Registry(retrieve=no_external_retrieval)
    for identifier, path in aliases.items():
        registry = registry.with_resource(identifier,
            Resource.from_contents(schemas[path], default_specification=DRAFT202012))
    registry = registry.crawl()
    resolved = 0
    def resolve_resource(resource, resolver):
        count = 0
        if isinstance(resource.contents, dict):
            for keyword in ('$ref', '$dynamicRef'):
                if keyword in resource.contents:
                    resolver.lookup(resource.contents[keyword])
                    count += 1
        for child in resource.subresources():
            count += resolve_resource(child, resolver.in_subresource(child))
        return count

    for path, schema in schemas.items():
        resource = DRAFT202012.create_resource(schema)
        resolved += resolve_resource(resource, registry.resolver(path.as_uri()).in_subresource(resource))
    return registry, schemas, resolved


def catalog_actions(value, registered):
    actions = value.split(';')
    require(all(actions) and len(actions) == len(set(actions)) and set(actions) <= registered,
        f'授权动作没有登记责任：{value}')
    return set(actions)


def field_mapping_schema(value, definitions):
    # 独立Schema使用明确分隔符，文件名称和属性路径各自解析。
    if '::' in value:
        name, field = value.split('::', 1)
    else:
        name, field = value.split('.', 1)
    path, schema = catalog_schema(name, definitions)
    fields = field.split('.')
    require(all(fields), f'字段路径为空：{value}')
    return path, schema, fields


def schema_has_field(schema, fields, resolver, *, seen=frozenset(), enter=True):
    if not isinstance(schema, dict):
        return False
    identity = (id(schema), tuple(fields))
    if identity in seen:
        return False
    seen = seen | {identity}
    if enter:
        resolver = resolver.in_subresource(Resource.from_contents(schema, default_specification=DRAFT202012))
    if '$ref' in schema:
        resolved = resolver.lookup(schema['$ref'])
        if schema_has_field(resolved.contents, fields, resolved.resolver, seen=seen, enter=False):
            return True
    properties = schema.get('properties', {})
    if fields[0] in properties:
        if len(fields) == 1:
            return properties[fields[0]] is not False
        if schema_has_field(properties[fields[0]], fields[1:], resolver, seen=seen):
            return True
    return any(schema_has_field(child, fields, resolver, seen=seen)
        for keyword in ('allOf', 'anyOf', 'oneOf') for child in schema.get(keyword, []))


def main():
    from markdown_it import MarkdownIt

    arguments = argparse.ArgumentParser()
    arguments.add_argument('--node-modules', type=Path, default=ROOT / 'guardian-h5/node_modules')
    arguments.add_argument('--report-path', type=Path, default=ROOT / '.planning-work/t36-backend.json')
    options = arguments.parse_args()
    require(os.environ.get('DJANGO_SETTINGS_MODULE'), '接口检查需要明确的独立配置')
    sys.path.insert(0, str(ROOT / 'backend'))
    import django
    django.setup()
    from check_runtime_routes import check_routes
    from authorization.actions import CASE_ACTIONS, SERVICE_ACTIONS

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
    registered_actions = CASE_ACTIONS | SERVICE_ACTIONS
    runtime = check_routes(catalog)
    sources.update(ROOT / name for name in runtime['source_sha256'])
    frontend_sources = sorted(str(path.relative_to(ROOT)) for path in (ROOT / 'guardian-h5/src').rglob('*')
        if path.suffix in {'.ts', '.tsx'} and not any(token in path.name for token in ('.test.', '.spec.')))
    caller_script = ROOT / 'backend-design/read_api_callers.mjs'
    callers = json.loads(subprocess.check_output(['node', str(caller_script)], input=json.dumps({
        'root': str(ROOT), 'node_modules': str(options.node_modules.resolve(strict=True)), 'sources': frontend_sources}), text=True))
    sources.update({caller_script, ROOT / 'guardian-h5/package-lock.json', *(ROOT / name for name in frontend_sources)})
    sources.update(path for path in (ROOT / 'frontend-design').glob('*.ts') if not path.name.startswith('test'))
    registration_script = ROOT / 'alert-design/read_schema_registry.mjs'
    api_sources = sorted(str(path.relative_to(ROOT)) for path in (ROOT / 'guardian-h5/src/api').glob('*.ts'))
    registrations = json.loads(subprocess.check_output(['node', str(registration_script)], input=json.dumps({
        'root': str(ROOT), 'node_modules': str(options.node_modules.resolve(strict=True)), 'sources': api_sources}), text=True))
    sources.add(registration_script)

    schema_files = ('backend-design/v2-protocol.schema.json', 'backend-design/protocol.schema.json', 'data-design/dataset.schema.json', 'data-design/case-extension.schema.json', 'backend-design/runtime.schema.json', 'alert-design/node.schema.json', 'backend-design/notification.schema.json', 'backend-design/archive-relations.schema.json')
    schema_paths = {ROOT / name for name in schema_files}
    schema_paths.update(path for directory in ('alert-design', 'backend-design', 'data-design')
        for path in (ROOT / directory).glob('*.schema.json'))
    schema_paths.update(path for directory in (ROOT / 'backend').iterdir()
        if directory.is_dir() and not directory.name.startswith('.') for path in directory.rglob('*.schema.json'))
    defs = json.loads((ROOT / schema_files[0]).read_text())['$defs']
    for row in catalog:
        for name in ('input_schema', 'output_schema'):
            schema_path, _ = catalog_schema(row[name], defs)
            schema_paths.add(schema_path)
    field_map_path = ROOT / 'backend-design/v2-field-map.csv'
    mapping = rows(field_map_path)
    schema_paths.update(field_mapping_schema(row['api_field'], defs)[0] for row in mapping)
    registry, schemas, resolved_refs = local_schema_registry(schema_paths, runtime_aliases=registrations['aliases'])
    sources.update(schemas)
    behavior_path = ROOT / 'backend-design/v2-service-behavior.md'
    behavior = behavior_path.read_text()
    write_count = 0
    for row in catalog:
        require(all(row.values()), f"接口元数据缺少：{row['operation_id']}")
        for schema_name in (row['input_schema'], row['output_schema']):
            schema_path, _ = catalog_schema(schema_name, defs)
            sources.add(schema_path)
        catalog_actions(row['auth'], registered_actions |
            {'provider_signature', 'anonymous_credentials', 'support_session'})
        require(set(row['screen_refs'].split(';')) <= pages, '接口页面引用不存在')
        require(row['implementation_task'] in task_ids, '接口实施任务不存在')
        read = operations[row['read_operation']]
        require(read['idempotency'] == 'none', '重新读取入口具有业务副作用')
        require(row['transaction'] == 'read_snapshot' or row['transaction'] in behavior,
            f"事务未定义：{row['operation_id']} / {row['transaction']}")
        if row['idempotency'] != 'none':
            write_count += 1
            require(row['method'] == 'POST', '写操作 method 不正确')
            require(row['idempotency'] in {'intent', 'provider_event', 'actor_client_key', 'manifest_source', 'plan_journal', 'review_version_source'}, '写操作幂等身份缺少')
        require(row['task_type'] in {'none', 'purpose_copy', 'ocr', 'summary', 'disposition', 'assessment', 'alert_evaluation', 'reconcile'}, '任务种类未定义')

    require(len({row['api_field'] for row in mapping}) == len(mapping), '字段映射重复')
    for row in mapping:
        require(all(row.values()) and row['owner'] in task_ids, '字段映射缺少维护责任')
        schema_path, schema, fields = field_mapping_schema(row['api_field'], defs)
        require(schema_has_field(schema, fields, registry.resolver(schema_path.as_uri())),
            f"字段映射没有对应属性：{row['api_field']}")

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
                        if link not in pending and link != ROOT / 'planning/delivery-ledger.json':
                            sources.add(link)
    require('BACK V2.0' in (ROOT / 'sJIA合并MAS预警平台后端LLD.md').read_text(), '后端版本缺少')
    def path_shape(value):
        return ''.join(literal + ('{}' if field is not None else '') for literal, field, _, _ in Formatter().parse(value))

    route_shapes = {path_shape('/api/v2' + item['path']) for item in runtime['routes']}
    route_methods = {(method.upper(), path_shape('/api/v2' + item['path']))
        for item in runtime['routes'] for method in item['methods']}
    caller_bindings, dynamic_calls, checked_references = [], [], []
    for name, entry in callers['files'].items():
        for call in entry['calls']:
            if not call['path_shape'].startswith('/api/v2'):
                continue
            shape = call['path_shape'].partition('?')[0]
            item = {'source': name, **call}
            if shape in route_shapes:
                require(call['method'] is None or (call['method'], shape) in route_methods,
                    '调用方HTTP方法与实际路由不一致')
                caller_bindings.append(item)
            else:
                dynamic_calls.append(item)
        for reference in entry['schema_references']:
            if '{}' in reference['reference']:
                continue
            registry.resolver((ROOT / 'backend-design/').as_uri() + '/').lookup(reference['reference'])
            checked_references.append({'source': name, **reference})
    composition_path = ROOT / 'backend-design/composed-callers.json'
    composition = json.loads(composition_path.read_text())
    require(composition['schema_version'] == 1 and composition['capability'] == 'DESIGN', '动态调用责任格式不正确')
    reviews = {item['source']: item for item in composition['callers']}
    require(len(reviews) == len(composition['callers']) and set(reviews) == {
        item['source'] + '#' + item['owner'] for item in dynamic_calls}, '动态调用需要逐项接收完整目标')
    native_methods = {(method.upper(), item['path']) for item in runtime['routes'] for method in item['methods']}
    for item in dynamic_calls:
        review = reviews[item['source'] + '#' + item['owner']]
        require(review['owner'] in task_ids and review['paths'] and len(review['paths']) == len(set(review['paths'])),
            '动态调用维护责任或目标不完整')
        require(item['method'] is None or item['method'] == review['method'], '动态调用HTTP方法不一致')
        require(all((review['method'], path) in native_methods for path in review['paths']), '动态调用没有准确实际路由')
        item['review'] = review
    sources.add(composition_path)
    sources.update({behavior_path, field_map_path, ROOT / 'ui-delivery/v2/screen-design-map.csv', ROOT / 'data-design/v2-schema.sql'})
    url_tree = ast.parse((ROOT / 'backend/api/urls.py').read_text())
    existing_routes = sum(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'path' for node in ast.walk(url_tree))
    report = {
        'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'status': 'PASS', 'capability': 'DESIGN',
        'runtime_python': platform.python_version(), 'dependency_versions': {
            name: version(name) for name in ('Django', 'djangorestframework', 'jsonschema', 'referencing', 'markdown-it-py')},
        'counts': {'v2_operations': len(catalog), 'write_operations': write_count, 'schema_definitions': len(defs),
            'schema_files': len(schemas), 'resolved_schema_refs': resolved_refs, 'field_maps': len(mapping),
            'existing_runtime_routes': existing_routes, 'v2_runtime_routes': runtime['route_count'],
            'v2_runtime_methods': runtime['method_count']},
        'registered_service_actions': sorted(SERVICE_ACTIONS), 'local_links': sorted(local_links),
        'runtime_routes': runtime, 'frontend_callers': {'parser': callers['parser'], 'parser_version': callers['version'],
            'source_files': len(callers['files']), 'literal_route_bindings': caller_bindings,
            'dynamic_or_composed_routes': dynamic_calls, 'resolved_schema_calls': checked_references,
            'scope': '准确字面路径与Schema引用；动态目标及实际传播由原功能任务验收'},
        'schema_registrations': registrations,
        'provider_called': False, 'clinical_rows_inserted': 0, 'business_validation': 'NOT_RUN', 'visual_validation': 'NOT_RUN', 'device_validation': 'NOT_RUN',
        'source_sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(sources)},
    }
    output = options.report_path
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
