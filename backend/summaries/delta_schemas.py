import json

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Resource

from archives.inputs import REGISTRY, ROOT
from records.queries import QueryRejected

PATH = ROOT / 'backend-design/archive-delta.schema.json'
SCHEMA = json.loads(PATH.read_text())
Draft202012Validator.check_schema(SCHEMA)
REGISTRY = REGISTRY.with_resource(PATH.as_uri(), Resource.from_contents({**SCHEMA, '$id': PATH.as_uri()}))
VALIDATORS = {name: Draft202012Validator({**SCHEMA, '$id': PATH.as_uri(), '$ref': f'#/$defs/{name}'}, registry=REGISTRY,
    format_checker=FormatChecker()) for name in SCHEMA['$defs']}

def checked_delta(name, value):
    if next(VALIDATORS[name].iter_errors(value), None) is not None:
        raise QueryRejected('ARCHIVE_DELTA_INPUT_INVALID')
    return value
