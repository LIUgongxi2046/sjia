import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Resource

from archives.inputs import REGISTRY, ROOT, check_medical_time
from records.queries import QueryRejected


PATH = ROOT / 'backend-design/archive-search.schema.json'
SCHEMA = json.loads(PATH.read_text())
Draft202012Validator.check_schema(SCHEMA)
REGISTRY = REGISTRY.with_resource(PATH.as_uri(), Resource.from_contents({**SCHEMA, '$id': PATH.as_uri()}))
VALIDATORS = {name: Draft202012Validator({**SCHEMA, '$id': PATH.as_uri(), '$ref': f'#/$defs/{name}'},
    registry=REGISTRY, format_checker=FormatChecker()) for name in SCHEMA['$defs']}


def checked_search(name, value):
    if next(VALIDATORS[name].iter_errors(value), None) is not None:
        raise QueryRejected('ARCHIVE_SEARCH_INPUT_INVALID')
    if name == 'ArchiveSearchInput':
        for key in ('query', 'organization', 'confirmed_text', 'project'):
            if key in value and not value[key].strip():
                raise QueryRejected('ARCHIVE_SEARCH_INPUT_INVALID')
        if value.get('from_date') and value.get('to_date') and value['from_date'] > value['to_date']:
            raise QueryRejected('ARCHIVE_SEARCH_DATE_INVALID')
        if ('from_date' in value and 'from_time' in value) or ('to_date' in value and 'to_time' in value):
            raise QueryRejected('ARCHIVE_SEARCH_DATE_INVALID')
        for key in ('from_time', 'to_time'):
            if key in value:
                check_medical_time(value[key])
                if value[key]['precision'] not in ('date', 'minute', 'second'):
                    raise QueryRejected('ARCHIVE_SEARCH_DATE_INVALID')
    return value
