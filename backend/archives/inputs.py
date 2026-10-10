import json
from datetime import datetime
from pathlib import Path
from zoneinfo import available_timezones

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from pydantic import TypeAdapter, ValidationError

from records.queries import QueryRejected


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATHS = ('backend-design/v2-protocol.schema.json', 'backend-design/protocol.schema.json',
    'data-design/dataset.schema.json', 'alert-design/node.schema.json', 'backend-design/archive-delta.schema.json',
    'backend-design/observation-period.schema.json', 'alert-design/v2-recognition.schema.json')
SCHEMAS = { (ROOT / name).as_uri(): json.loads((ROOT / name).read_text()) for name in SCHEMA_PATHS }
for uri, schema in SCHEMAS.items():
    schema['$id'] = uri
    Draft202012Validator.check_schema(schema)
REGISTRY = Registry().with_resources((uri, Resource.from_contents(schema)) for uri, schema in SCHEMAS.items())
PROTOCOL = SCHEMAS[(ROOT / SCHEMA_PATHS[0]).as_uri()]
VALIDATORS = {name: Draft202012Validator({**PROTOCOL, '$ref': f'#/$defs/{name}'},
    registry=REGISTRY, format_checker=FormatChecker()) for name in ('DocumentInput', 'GroupingInput')}
TIME_FORMATS = FormatChecker()
LOCAL_DATETIME = TypeAdapter(datetime)
TIMEZONES = available_timezones()


@TIME_FORMATS.checks('local-medical-datetime', raises=ValidationError)
def local_datetime_valid(value):
    return LOCAL_DATETIME.validate_python(value).utcoffset() is None


def check_medical_time(value):
    if value is None:
        return
    if value['precision'] == 'month_range' and (value['local_year'], value['local_month']) > (value['end_year'], value['end_month']):
        raise QueryRejected('DOCUMENT_TIME_INVALID')
    if value['precision'] == 'relative' and not value['relative_text'].strip():
        raise QueryRejected('DOCUMENT_TIME_INVALID')
    if value['local_datetime'] is not None and not TIME_FORMATS.conforms(value['local_datetime'], 'local-medical-datetime'):
        raise QueryRejected('DOCUMENT_TIME_INVALID')
    if value['timezone_name'] is not None and value['timezone_name'] not in TIMEZONES:
        raise QueryRejected('DOCUMENT_TIME_INVALID')


def document_input(name, payload):
    if next(VALIDATORS[name].iter_errors(payload), None) is not None:
        raise QueryRejected('DOCUMENT_INPUT_INVALID')
    if name == 'GroupingInput' and (int(payload['expected_version']) > 9223372036854775807
            or not payload['reason'].strip()):
        raise QueryRejected('DOCUMENT_INPUT_INVALID')
    check_medical_time(payload['documented_time'])
    proposed = payload.get('proposed_metadata')
    if proposed is not None:
        for episode in proposed['episodes']:
            check_medical_time(episode['start_time'])
            check_medical_time(episode['end_time'])
    return payload
