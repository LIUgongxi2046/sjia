import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from unittest import TestCase

import django
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource


def deny_network(event, values):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.sendto'}:
        raise RuntimeError('RECOVERY_SCHEMA_NETWORK_FORBIDDEN')


sys.addaudithook(deny_network)
django.setup()
from files.s3_storage import S3StorageRejected, canonical_key
from retention.s3_recovery_schemas import (S3BackupPackage, S3BackupSource, S3MappedAbsenceFence,
    S3RestoreFence, S3RestoreMapping, S3RestoreTarget)

parser = argparse.ArgumentParser(description='使用实际备份与恢复文档验证正式Schema')
parser.add_argument('--mapping', required=True, type=Path)
parser.add_argument('--backup', required=True, type=Path)
parser.add_argument('--target-marker', required=True, type=Path)
parser.add_argument('--control-root', required=True, type=Path)
parser.add_argument('--output', required=True, type=Path)
arguments = parser.parse_args()
assert not arguments.output.exists()
root = Path(__file__).resolve().parents[2]
paths = [root / 'backend/files/s3-object-reference.schema.json', root / 'backend/files/s3-stored-object.schema.json',
    root / 'data-design/s3-backup-source.schema.json', root / 'data-design/s3-object-absence.schema.json',
    root / 'data-design/s3-restore-mapping.schema.json', root / 'backend-design/s3-restore-target.schema.json',
    root / 'data-design/s3-backup-package.schema.json', root / 'data-design/s3-mapped-absence-fence.schema.json',
    root / 'data-design/s3-restore-fence.schema.json']
schemas = [json.loads(path.read_bytes()) for path in paths]
for schema in schemas:
    Draft202012Validator.check_schema(schema)
registry = Registry().with_resources((schema['$id'], Resource.from_contents(schema)) for schema in schemas)
checker = FormatChecker()
if 'date-time' not in checker.checkers:
    raise RuntimeError('RECOVERY_SCHEMA_FORMAT_DEPENDENCIES_REQUIRED')


@checker.checks('sjia-canonical-object-key', raises=S3StorageRejected)
def object_key(value):
    return isinstance(value, str) and canonical_key(value) == value


@checker.checks('sjia-absolute-control-root')
def control_root(value):
    return isinstance(value, str) and Path(value).is_absolute() and Path(value).as_posix() == value and '..' not in Path(value).parts


validators = {schema['$id']: Draft202012Validator(schema, registry=registry, format_checker=checker) for schema in schemas}


def validate(kind, document):
    validators['urn:sjia:schema:' + kind].validate(document)


mapping_bytes = arguments.mapping.read_bytes()
mapping = json.loads(mapping_bytes)
package_bytes = (arguments.backup / 'manifest.json').read_bytes()
completion = json.loads((arguments.backup / 'COMPLETE.json').read_bytes())
assert completion['manifest_sha256'] == hashlib.sha256(package_bytes).hexdigest()
package = json.loads(package_bytes)
marker = json.loads(arguments.target_marker.read_bytes())
target = marker['target']
validate('s3-backup-source-1', mapping['source_snapshot'])
validate('s3-restore-mapping-1', mapping)
validate('s3-consistent-backup-1', package)
validate('s3-restore-target-1', target)
S3BackupSource.model_validate_json(json.dumps(mapping['source_snapshot']))
S3RestoreMapping.model_validate_json(mapping_bytes)
S3BackupPackage.model_validate_json(package_bytes)
S3RestoreTarget.model_validate_json(json.dumps(target))
fences = []
for path in sorted(arguments.control_root.rglob('journal.json')):
    document = json.loads(path.read_bytes())
    kind = document.get('schema_version')
    if kind in {'s3-mapped-absence-fence-1', 's3-restore-fence-1'}:
        validate(kind, document)
        model = S3MappedAbsenceFence if kind == 's3-mapped-absence-fence-1' else S3RestoreFence
        model.model_validate_json(json.dumps(document))
        fences.append(document)
assert len(fences) == 6
absence = next(item['target_absence'] for item in mapping['objects'] if item['kind'] == 'absent')
for fence in fences:
    validate('s3-object-absence-1', fence['target_absence'])
negative = []


def refuse(name, kind, document):
    with TestCase().assertRaises(ValidationError):
        validate(kind, document)
    negative.append(name)


changed = copy.deepcopy(mapping)
changed['objects'][0].pop('kind')
refuse('missing-discriminator', 's3-restore-mapping-1', changed)
refuse('null-mapped-digest', 's3-restore-target-1', {**target, 'mapping_sha256': None})
refuse('relative-control-root', 's3-restore-target-1', {**target, 'control_root': target['control_root'].lstrip('/')})
refuse('nonempty-native-inventory', 's3-object-absence-1', {**absence, 'versions': 1})
refuse('boolean-native-inventory', 's3-object-absence-1', {**absence, 'delete_markers': True})
refuse('missing-provider-request', 's3-object-absence-1', {**absence, 'provider_request_id': ''})
refuse('automatic-restart', 's3-consistent-backup-1', {**package, 'automatic_restart': True})
changed = copy.deepcopy(package)
changed['content_checkpoint']['sequence'] = 0
refuse('nonzero-initial-digest', 's3-consistent-backup-1', changed)
changed = copy.deepcopy(mapping['source_snapshot'])
changed['file_references'][0]['object_key'] += '/..'
refuse('noncanonical-object-key', 's3-backup-source-1', changed)
mapped_fence = next(item for item in fences if item['schema_version'] == 's3-mapped-absence-fence-1')
refuse('missing-fence-source', 's3-mapped-absence-fence-1', {**mapped_fence,
    'source_registration_id': None, 'source_reference': None, 'source_file': None})
unseen_fence = next(item for item in fences if item['schema_version'] == 's3-restore-fence-1')
refuse('local-fence-with-native-reference', 's3-restore-fence-1', {**unseen_fence, 'source_kind': 'local_object'})
refuse('extra-target-property', 's3-restore-target-1', {**target, 'source_reference': unseen_fence['source_reference']})
refuse('invalid-target-uuid', 's3-restore-target-1', {**target, 'restore_id': mapping['backup_manifest_sha256']})
refuse('naive-observation-time', 's3-object-absence-1', {**absence, 'verified_at': absence['verified_at'][:19]})
report = {'result': 'PASS', 'measured_at': datetime.now(timezone.utc).isoformat(),
    'schemas': {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
    'actual_mapping_sha256': hashlib.sha256(mapping_bytes).hexdigest(),
    'actual_package_sha256': hashlib.sha256(package_bytes).hexdigest(),
    'actual_fences': len(fences), 'negative': negative, 'network_calls': 0,
    'database_writes': 0, 'new_model_calls': 0}
with arguments.output.open('x') as stream:
    json.dump(report, stream, indent=2)
print(json.dumps({'result': 'PASS', 'schemas': len(schemas), 'actual_fences': len(fences), 'negative': len(negative)}))
