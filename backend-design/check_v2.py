import ast
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource
from referencing.jsonschema import DRAFT202012


ROOT = Path(__file__).resolve().parents[1]
# Exact alias registered by guardian-h5/src/api/originalSourceSchema.ts.
# No basename search or fallback to an arbitrary same-named file is allowed.
LOCAL_SCHEMA_ALIASES = {'backend/files/record-search.schema.json': 'backend/records/search.schema.json'}


def catalog_schema(name, definitions):
    """Resolve documented schema forms locally, without network retrieval."""
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
    schema = document
    if fragment:
        if not fragment.startswith('/'):
            raise ValueError('接口 Schema 必须使用 JSON Pointer')
        for key in fragment[1:].split('/'):
            schema = schema[key.replace('~1', '/').replace('~0', '~')]
    return path, schema


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


def scoped_refs(value, base_uri):
    """Keep nested schema $id bases instead of flattening relative references."""
    if isinstance(value, dict):
        current = urljoin(base_uri, value.get('$id', ''))
        if '$ref' in value:
            yield current, value['$ref']
        for child in value.values():
            yield from scoped_refs(child, current)
    elif isinstance(value, list):
        for child in value:
            yield from scoped_refs(child, base_uri)


def local_schema_registry(paths, *, root=None):
    """Load the finite local transitive schema graph; never retrieve a network URI."""
    root = (ROOT if root is None else root).resolve()
    schemas, aliases = {}, {}
    pending = list(paths)

    def local_path(uri):
        target = urlsplit(uri)
        require(target.scheme == 'file' and not target.netloc and not target.query,
            f'Schema 引用不是受控本地文件：{uri}')
        path = Path(unquote(target.path)).resolve()
        require(path.is_relative_to(root) and path.name.endswith('.schema.json'),
            f'Schema 引用超出仓库或不是 Schema 文件：{uri}')
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
            # Non-file IDs may refer only to already registered local resources.
            # Resolution below must fail for unknown external IDs, without fetch.

    def no_external_retrieval(uri):
        raise NoSuchResource(ref=uri)

    registry = Registry(retrieve=no_external_retrieval)
    for identifier, path in aliases.items():
        registry = registry.with_resource(identifier,
            Resource.from_contents(schemas[path], default_specification=DRAFT202012))
    registry = registry.crawl()
    resolved = 0
    for path, schema in schemas.items():
        for base_uri, reference in scoped_refs(schema, path.as_uri()):
            registry.resolver(base_uri).lookup(reference)
            resolved += 1
    return registry, schemas, resolved


def catalog_actions(value, registered):
    actions = value.split(';')
    require(all(actions) and len(actions) == len(set(actions)) and set(actions) <= registered,
        f'授权动作没有登记责任：{value}')
    return set(actions)


def field_mapping_schema(value, definitions):
    # Existing V2 names stay valid; independent schema references use an explicit
    # separator so dots in filenames cannot be mistaken for a property path.
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

    schema_files = ('backend-design/v2-protocol.schema.json', 'backend-design/protocol.schema.json', 'data-design/dataset.schema.json', 'data-design/case-extension.schema.json', 'backend-design/runtime.schema.json', 'alert-design/node.schema.json', 'backend-design/notification.schema.json', 'backend-design/archive-relations.schema.json')
    schema_paths = {ROOT / name for name in schema_files}
    defs = json.loads((ROOT / schema_files[0]).read_text())['$defs']
    for row in catalog:
        for name in ('input_schema', 'output_schema'):
            schema_path, _ = catalog_schema(row[name], defs)
            schema_paths.add(schema_path)
    field_map_path = ROOT / 'backend-design/v2-field-map.csv'
    mapping = rows(field_map_path)
    schema_paths.update(field_mapping_schema(row['api_field'], defs)[0] for row in mapping)
    registry, schemas, resolved_refs = local_schema_registry(schema_paths)
    sources.update(schemas)
    behavior_path = ROOT / 'backend-design/v2-service-behavior.md'
    behavior = behavior_path.read_text()
    write_count = 0
    for row in catalog:
        require(all(row.values()), f"接口元数据缺少：{row['operation_id']}")
        for schema_name in (row['input_schema'], row['output_schema']):
            schema_path, _ = catalog_schema(schema_name, defs)
            sources.add(schema_path)
        catalog_actions(row['auth'], registered_actions | planned_actions |
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
