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


def references(value):
    if isinstance(value, dict):
        if '$ref' in value:
            yield value['$ref']
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def main():
    sources = {Path(__file__).resolve()}
    manifest_path = ROOT / 'alert-design/design-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    sources.add(manifest_path)
    expected_nodes = [f'N{index:02}' for index in range(12)]
    require(manifest['nodes'] == expected_nodes, '节点登记不完整')
    require(manifest['production_agent'] is False and manifest['model_token_budget'] == 0, '确定性评估边界不正确')
    require(manifest['recognition']['provider'] == 'Ark', '模型来源与授权不一致')
    schemas = {}
    registry = Registry()
    schema_names = manifest['schemas'] + [
        'backend-design/v2-protocol.schema.json', 'backend-design/protocol.schema.json',
        'data-design/dataset.schema.json', 'data-design/case-extension.schema.json',
        'backend-design/runtime.schema.json',
    ]
    for name in schema_names:
        path = ROOT / name
        sources.add(path)
        schema = json.loads(path.read_text())
        Draft202012Validator.check_schema(schema)
        schemas[name] = schema
        resource = Resource.from_contents(schema)
        registry = registry.with_resource(path.as_uri(), resource)
        if '$id' in schema:
            registry = registry.with_resource(schema['$id'], resource)
    reference_count = 0
    for name, schema in schemas.items():
        resolver = registry.resolver((ROOT / name).as_uri())
        for reference in references(schema):
            resolver.lookup(reference)
            reference_count += 1

    recognition = schemas['alert-design/v2-recognition.schema.json']
    protected = {'patient_id', 'field_id', 'verification_id', 'available_at', 'concept_code', 'release_id', 'rule_id'}
    for definition in [recognition, *recognition['$defs'].values()]:
        if definition.get('type') == 'object':
            require(definition.get('additionalProperties') is False, '模型对象必须具有明确字段范围')
            require(not protected.intersection(definition['properties']), '模型输出包含服务端资格或身份')
            require(set(definition['properties']) == set(definition['required']), '模型字段空值语义不完整')
    context = schemas['alert-design/v2-context.schema.json']['$defs']
    require(set(context['InputRef']['properties']['role']['enum']) == {'baseline', 'current', 'series', 'context'}, '输入角色不完整')
    require(set(context['EvaluationContextV2']['properties']['mode']['enum']) == {'current', 'replay'}, '执行模式不完整')
    require({'available_at', 'source_anchor_id', 'verification_id', 'fact_version_id', 'relation_version_ids'} <= set(context['InputRef']['required']), '准确出处和资格引用缺少')

    catalog_path = ROOT / manifest['node_catalog']
    sources.add(catalog_path)
    with catalog_path.open(encoding='utf-8-sig', newline='') as stream:
        nodes = list(csv.DictReader(stream))
    require([row['node_id'] for row in nodes] == expected_nodes, '节点目录缺少或重复')
    ledger = json.loads((ROOT / 'planning/delivery-ledger.json').read_text())
    task_ids = {task['id'] for task in ledger['tasks']}
    for row in nodes:
        require(all(row.values()), '节点责任或行为缺少')
        require(row['implementation_task'] in task_ids, '节点实施任务不存在')

    parser = MarkdownIt('commonmark').enable('table')
    pending = {ROOT / 'docs/evidence/CHECK-T37/receipt.json', ROOT / 'docs/evidence/CHECK-T37/output.log'}
    document_names = [manifest['document'], manifest['v2_specification'], 'docs/development/T37.md']
    documents = {}
    local_links = set()
    for name in document_names:
        path = ROOT / name
        sources.add(path)
        source = path.read_text()
        documents[name] = source
        for token in parser.parse(source):
            for child in token.children or []:
                if child.type == 'link_open':
                    target = urlsplit(child.attrGet('href'))
                    if not target.scheme:
                        link = (path.parent / unquote(target.path)).resolve()
                        require(link.is_file() or link in pending, f'本地引用不存在：{link.name}')
                        local_links.add(str(link.relative_to(ROOT)))
    spec = documents[manifest['v2_specification']]
    require('ALERT V2.0' in documents[manifest['document']], '编排版本缺少')
    for index in range(1, 11):
        require(f'| BR-D{index:02} |' in spec, '业务规则责任缺少')

    inventory = {}
    runtime_names = [
        'backend/recognition/ark.py', 'backend/recognition/schemas.py',
        'backend/assessment/classification.py', 'backend/assessment/ms.py',
        'backend/alerting/signals.py', 'backend/alerting/episodes.py',
        'backend/api/views_support2.py',
    ]
    for name in runtime_names:
        path = ROOT / name
        sources.add(path)
        tree = ast.parse(path.read_text())
        inventory[name] = [node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    report = {
        'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        'status': 'PASS', 'capability': 'DESIGN',
        'counts': {'nodes': len(nodes), 'business_rules': 10, 'schemas': len(schemas), 'resolved_schema_refs': reference_count},
        'runtime_inventory': inventory, 'local_links': sorted(local_links),
        'provider_called': False, 'clinical_rows_inserted': 0,
        'business_validation': 'NOT_RUN', 'clinical_validation': 'NOT_RUN', 'visual_validation': 'NOT_RUN',
        'source_sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(sources)},
    }
    output = ROOT / '.planning-work/t37-orchestration.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
