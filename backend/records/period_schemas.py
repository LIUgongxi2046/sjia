import json

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Resource

from archives.inputs import REGISTRY, ROOT, check_medical_time
from records.queries import QueryRejected


PATH = ROOT / 'backend-design/observation-period.schema.json'
SCHEMA = json.loads(PATH.read_text())
Draft202012Validator.check_schema(SCHEMA)
REGISTRY = REGISTRY.with_resource(PATH.as_uri(), Resource.from_contents({**SCHEMA, '$id': PATH.as_uri()}))
VALIDATORS = {name: Draft202012Validator({**SCHEMA, '$id': PATH.as_uri(), '$ref': f'#/$defs/{name}'},
    registry=REGISTRY, format_checker=FormatChecker()) for name in SCHEMA['$defs']}


def checked_period(name, value):
    if next(VALIDATORS[name].iter_errors(value), None) is not None:
        raise QueryRejected('OBSERVATION_PERIOD_INPUT_INVALID')
    if name == 'Input':
        if not value['reason'].strip() or value['label'] is not None and not value['label'].strip():
            raise QueryRejected('OBSERVATION_PERIOD_INPUT_INVALID')
        if value['expected_version'] is not None and int(value['expected_version']) > 9223372036854775807:
            raise QueryRejected('OBSERVATION_PERIOD_INPUT_INVALID')
        for key in ('start', 'end'):
            check_medical_time(value[key])
    return value
