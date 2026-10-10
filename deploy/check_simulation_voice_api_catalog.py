import csv
import hashlib
import json
from pathlib import Path

from prepare_simulation_voice import guard
from django.urls import resolve
from api import patient_selection, views_voice_inputs
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
guard()
paths = {'voice-input': ROOT / 'backend-design/voice-input.schema.json',
    'protocol': ROOT / 'backend-design/protocol.schema.json',
    'family-record': ROOT / 'backend/records/search.schema.json'}
schemas = {name: json.loads(path.read_text()) for name, path in paths.items()}
registry = Registry().with_resources((f'{name}.schema.json', Resource.from_contents(schema))
    for name, schema in schemas.items())
for schema in schemas.values():
    Draft202012Validator.check_schema(schema)
with (ROOT / 'backend-design/voice-api-catalog.csv').open(newline='') as stream:
    catalog = list(csv.DictReader(stream))
if len({row['operation_id'] for row in catalog}) != len(catalog):
    raise RuntimeError('语音接口目录操作标识重复')
checks = []
for row in catalog:
    if not all(row.values()):
        raise RuntimeError('语音接口目录存在空字段')
    path = row['path'].format(patient_id='e91ce806-49b2-4e9a-97ed-ade303a27cd2',
        voice_id='6a85134c-3bdd-4d87-b161-5455d038bc0a')
    match = resolve(path)
    module = patient_selection if row['operation_id'] == 'patient_selection_save' else views_voice_inputs
    if match.func is not getattr(module, row['view']):
        raise RuntimeError('语音目录路径与实际 Django 入口不一致')
    for key in ('input_schema', 'output_schema'):
        value = row[key]
        if value in ('None', 'request_id'):
            continue
        namespace, definition = value.split('.', maxsplit=1)
        resolver = registry.resolver(f'{namespace}.schema.json')
        resolved = resolver.lookup(f'#/$defs/{definition}')
        Draft202012Validator(resolved.contents, registry=registry)
    checks.append({'operation_id': row['operation_id'], 'path': path, 'view': row['view'],
        'method': row['method'], 'status': 'PASS'})
report = {'status': 'PASS', 'mode': 'SIMULATION_INTERFACE_DEFINITIONS', 'checks': checks,
    'catalog_sha256': hashlib.sha256((ROOT / 'backend-design/voice-api-catalog.csv').read_bytes()).hexdigest(),
    'business_validation': 'NOT_RUN', 'visual': 'NOT_RUN'}
target = ROOT / '.planning-work/coordination/root/voice-api-catalog.json'
data = json.dumps(report, ensure_ascii=False, indent=2).encode()
target.write_bytes(data)
if target.read_bytes() != data:
    raise RuntimeError('语音接口目录检查保存重读不一致')
print(json.dumps({'status': 'PASS', 'operations': len(checks),
    'report_sha256': hashlib.sha256(data).hexdigest()}, ensure_ascii=False))
