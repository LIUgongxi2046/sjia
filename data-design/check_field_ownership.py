import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from django.apps import apps
from django.db import connection


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'planning'))

from check_reception_ownership import python_symbols, source_reference


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_fields(metadata_path, node_modules):
    path = ROOT / 'data-design/field-ownership.json'
    specification = json.loads(path.read_text())
    source_path = ROOT / specification['requirement_ownership_ref']
    ownership = {item['requirement']: item for item in json.loads(source_path.read_text())['requirements']}
    metadata = json.loads(metadata_path.read_text())
    require(metadata['read_only'] is True and metadata['migrations'] == 105 and metadata['server_version'] == '17.11',
        '字段核验需要明确的当前真实升级副本元数据')
    require(specification['schema_version'] == 1, '字段责任格式不正确')
    entries = specification['field_groups']
    require(len(entries) == 71 and {item['id'] for item in entries} == {f'FD-{number:02}' for number in range(1, 72)},
        '71个字段组需要各自维护唯一责任')
    roles = specification['roles']
    require({item['role'] for item in entries} == roles.keys(), '存在未接收的字段责任')
    with (ROOT / 'planning/reception-v2.csv').open(newline='') as stream:
        reception = {item['requirement']: item for item in csv.DictReader(stream)}
    sources = {path, source_path, Path(__file__).resolve(), ROOT / 'planning/check_reception_ownership.py',
        ROOT / 'planning/reception_frontend_ast.mjs', ROOT / 'planning/reception-v2.csv',
        ROOT / 'guardian-h5/package-lock.json'}
    references = set()
    role_results = {}
    for name, role in roles.items():
        require(role['requirement_refs'] and set(role['requirement_refs']) <= ownership.keys(), '字段引用未知功能')
        require(role['entry_refs'] and role['read_trigger'].strip() and role['invalidation'].strip(), '查询和失效责任不完整')
        consumers = set(role.get('extra_consumers', []))
        producers, tasks, acceptances = set(), set(), set()
        for requirement in role['requirement_refs']:
            consumers.update(ownership[requirement]['consumer'])
            producers.update(ownership[requirement]['producer'])
            tasks.update(reception[requirement]['implementation_task'].split(';'))
            acceptances.update(reception[requirement]['acceptance'].split(';'))
        references.update(consumers | producers | set(role['entry_refs']))
        role_results[name] = {'producer_refs': sorted(producers), 'entry_refs': role['entry_refs'],
            'consumer_refs': sorted(consumers), 'tasks': sorted(tasks), 'acceptance_ids': sorted(acceptances),
            'read_trigger': role['read_trigger'], 'invalidation': role['invalidation']}
    resolved = {reference: source_reference(ROOT, reference) for reference in references}
    frontend = sorted({str(path.relative_to(ROOT)) for path, _ in resolved.values() if path.suffix == '.tsx'})
    parsed = json.loads(subprocess.check_output(['node', str(ROOT / 'planning/reception_frontend_ast.mjs')],
        input=json.dumps({'root': str(ROOT), 'node_modules': str(node_modules.resolve(strict=True)), 'files': frontend}), text=True))
    symbols = {path: python_symbols(path) for path, _ in resolved.values() if path.suffix == '.py'}
    symbols.update({ROOT / name: item['symbols'] for name, item in parsed['files'].items()})
    for reference, (path, symbol) in resolved.items():
        require(path in symbols and symbol in symbols[path], f'字段使用入口不存在：{reference}')
        sources.add(path)
    field_results, required_domains = {}, set()
    for item in entries:
        require(item['model_fields'], '字段组缺少持久位置')
        fields = []
        for label, names in item['model_fields'].items():
            model = apps.get_model(label)
            module = Path(sys.modules[model.__module__].__file__).resolve()
            observed = metadata['models'][model.__name__]
            require(observed['app'] == model._meta.app_label
                and observed['module_sha256'] == hashlib.sha256(module.read_bytes()).hexdigest(), 'ORM来源与元数据不一致')
            require(names and len(names) == len(set(names)), '持久字段缺少或重复')
            sources.add(module)
            for name in names:
                field = model._meta.get_field(name)
                actual = observed['fields'][field.attname]
                require(actual['column'] == field.column and actual['nullable'] == field.null
                    and actual['sql_declaration'] == field.db_type(connection) and actual['exact_type_oid'] is True,
                    f'字段声明与真实物理类型不一致：{label}.{name}')
                if actual['domain_name'] is not None:
                    required_domains.add(actual['domain_name'])
                fields.append({'model': label, 'attribute': field.attname, 'table': observed['table'], **actual})
        field_results[item['id']] = {'role': item['role'], 'fields': fields}
    return {'status': 'PASS', 'capability': 'DESIGN', 'field_groups': len(entries),
        'unique_fields': len({(field['model'], field['attribute']) for item in field_results.values() for field in item['fields']}),
        'roles': role_results, 'fields': field_results, 'required_domains': {
            name: metadata['domains'][name] for name in sorted(required_domains)},
        'metadata_sha256': hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
        'metadata_models': len(metadata['models']), 'runtime_symbol_count': len(references),
        'business_validation': 'NOT_RUN', 'full_interface_propagation': 'T36及各项业务验收',
        'source_sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(sources)}}
