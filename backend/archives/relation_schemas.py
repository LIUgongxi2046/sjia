import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Resource

from archives.inputs import REGISTRY, ROOT
from records.queries import QueryRejected


PATH = ROOT / 'backend-design/archive-relations.schema.json'
SCHEMA = json.loads(PATH.read_text())
Draft202012Validator.check_schema(SCHEMA)
REGISTRY = REGISTRY.with_resource(PATH.as_uri(), Resource.from_contents({**SCHEMA, '$id': PATH.as_uri()}))
VALIDATORS = {name: Draft202012Validator({**SCHEMA, '$id': PATH.as_uri(), '$ref': f'#/$defs/{name}'},
    registry=REGISTRY, format_checker=FormatChecker()) for name in SCHEMA['$defs']}
OPERATIONS = {'archive_relation_create': 'RelationInput', 'archive_relation_update': 'RelationChangeInput',
    'archive_relation_verify': 'RelationVerificationInput'}


def checked_relation(name, value):
    if next(VALIDATORS[name].iter_errors(value), None) is not None:
        raise QueryRejected('ARCHIVE_RELATION_INPUT_INVALID')
    if isinstance(value, dict):
        if 'expected_version' in value and int(value['expected_version']) > 9223372036854775807:
            raise QueryRejected('ARCHIVE_RELATION_INPUT_INVALID')
        for key in ('evidence_ref', 'reason'):
            if key in value and (not value[key].strip() or len(value[key]) > 16000):
                raise QueryRejected('ARCHIVE_RELATION_INPUT_INVALID')
    return value
