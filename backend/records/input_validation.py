"""Admission for the existing family record-create wire format.

Server-owned confirmation/anchor/author/verification fields must never be
accepted from a family request. Trusted candidate preparation adds them later.
This deliberately retains the deployed file_ids/decisions wire names.
"""
from decimal import Decimal, InvalidOperation
from uuid import UUID
import re

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
from records.medical_times import time_values

from records.service import RecordRejected


TOP_LEVEL = frozenset({'kind', 'capture_method', 'origin_type', 'field_config_version',
    'fields', 'file_ids', 'source_organization', 'report', 'draft_id', 'draft_version',
    'candidate_set_id', 'decisions', 'family_confirmation'})
FIELD_KEYS = frozenset({'field_code', 'concept_code', 'dictionary_entry_id', 'original_name',
    'value_type', 'value_state', 'raw_text', 'raw_numeric', 'categorical_value', 'qualifier',
    'raw_unit', 'method', 'reference_interval', 'report_flag', 'medical_time',
    'sample_time_origin', 'source_file_id', 'source_text', 'candidate_field_id'})
REPORT_KEYS = frozenset({'external_ref', 'report_status', 'sample_time', 'result_time'})


def _uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (TypeError, ValueError, AttributeError):
        return False


def validate_medical_input(value):
    if value is None:
        return
    if not isinstance(value, dict):
        raise RecordRejected('MEDICAL_TIME_INPUT_INVALID')
    # Legacy family forms may omit nullable slots; do not mutate their digest.
    normalized = {key: None for key in ('raw_text', 'local_date', 'local_datetime',
        'timezone_name', 'utc_offset_minutes', 'instant', 'local_year', 'local_month',
        'end_year', 'end_month', 'relative_text')}
    normalized.update(value)
    from archives.inputs import check_medical_time, SCHEMAS, ROOT, REGISTRY
    from api.base import ApiError
    from records.queries import QueryRejected
    protocol = SCHEMAS[(ROOT / 'backend-design/protocol.schema.json').as_uri()]
    validator = Draft202012Validator({**protocol, '$ref': '#/$defs/MedicalTimeInput'},
        registry=REGISTRY, format_checker=FormatChecker())
    if not validator.is_valid(normalized):
        raise RecordRejected('MEDICAL_TIME_INPUT_INVALID')
    try:
        check_medical_time(normalized)
        time_values(normalized)
    except (ApiError, QueryRejected, ValidationError, ValueError) as exc:
        raise RecordRejected('MEDICAL_TIME_INPUT_INVALID') from exc



def validate_family_record_input(payload):
    if not isinstance(payload, dict) or set(payload) - TOP_LEVEL:
        raise RecordRejected('RECORD_INPUT_INVALID')
    fields = payload.get('fields')
    if not isinstance(fields, list) or not fields or any(not isinstance(field, dict) for field in fields):
        raise RecordRejected('RECORD_FIELDS_REQUIRED')
    for field in fields:
        if set(field) - FIELD_KEYS:
            raise RecordRejected('RECORD_FIELD_INPUT_INVALID')
        if not isinstance(field.get('field_code'), str) or not field['field_code'].strip():
            raise RecordRejected('RECORD_FIELD_INPUT_INVALID')
        for key in ('concept_code', 'original_name', 'raw_text', 'categorical_value',
                'raw_unit', 'method', 'reference_interval', 'report_flag', 'source_text'):
            if field.get(key) is not None and not isinstance(field[key], str):
                raise RecordRejected('RECORD_FIELD_INPUT_INVALID')
        if field.get('medical_time') is not None and not isinstance(field['medical_time'], dict):
            raise RecordRejected('RECORD_FIELD_INPUT_INVALID')
        validate_medical_input(field.get('medical_time'))
        if field.get('value_type') not in ('decimal', 'categorical', 'text', 'time') or field.get('value_state') not in ('known', 'unknown', 'invalid'):
            raise RecordRejected('RECORD_FIELD_INPUT_INVALID')
        if field.get('qualifier') not in (None, 'eq', 'lt', 'le', 'gt', 'ge'):
            raise RecordRejected('RECORD_FIELD_INPUT_INVALID')
        if field.get('sample_time_origin', 'unknown') not in ('report', 'item', 'observation', 'therapy', 'unknown'):
            raise RecordRejected('RECORD_FIELD_INPUT_INVALID')
        if 'candidate_field_id' in field and not payload.get('candidate_set_id'):
            raise RecordRejected('CANDIDATE_SET_REQUIRED')
        for key in ('source_file_id', 'dictionary_entry_id', 'candidate_field_id'):
            if field.get(key) is not None and not _uuid(field[key]):
                raise RecordRejected('RECORD_FIELD_INPUT_INVALID')
        if field.get('raw_numeric') is not None:
            try:
                if (not isinstance(field['raw_numeric'], str) or re.fullmatch(r'-?(0|[1-9][0-9]*)(\.[0-9]+)?', field['raw_numeric']) is None
                        or not Decimal(field['raw_numeric']).is_finite()):
                    raise InvalidOperation()
            except InvalidOperation:
                raise RecordRejected('FIELD_DECIMAL_INVALID') from None
    for key in ('draft_id', 'candidate_set_id', 'field_config_version'):
        if payload.get(key) is not None and not _uuid(payload[key]):
            raise RecordRejected('RECORD_INPUT_INVALID')
    if payload.get('source_organization') is not None and not isinstance(payload['source_organization'], str):
        raise RecordRejected('RECORD_INPUT_INVALID')
    if payload.get('kind') not in ('observation', 'report', 'therapy') or payload.get('capture_method') not in ('manual', 'ocr_confirmed', 'voice_confirmed'):
        raise RecordRejected('RECORD_INPUT_INVALID')
    if payload.get('origin_type') not in (None, 'self_report', 'report_transcription'):
        raise RecordRejected('RECORD_SOURCE_INVALID')
    if 'family_confirmation' in payload and type(payload['family_confirmation']) is not bool:
        raise RecordRejected('RECORD_INPUT_INVALID')
    if 'decisions' in payload and not isinstance(payload['decisions'], list):
        raise RecordRejected('RECORD_INPUT_INVALID')
    files = payload.get('file_ids', [])
    if not isinstance(files, list) or len(files) > 10 or any(not _uuid(value) for value in files) or len(set(files)) != len(files):
        raise RecordRejected('ATTACHMENTS_INVALID')
    if payload.get('report') is not None and (not isinstance(payload['report'], dict) or set(payload['report']) - REPORT_KEYS):
        raise RecordRejected('REPORT_INPUT_INVALID')
    if payload.get('report') is not None:
        report = payload['report']
        if (report.get('external_ref') is not None and not isinstance(report['external_ref'], str)
                or report.get('report_status', 'unknown') not in ('unknown', 'preliminary', 'final', 'corrected')
                or any(report.get(key) is not None and not isinstance(report[key], dict) for key in ('sample_time', 'result_time'))):
            raise RecordRejected('REPORT_INPUT_INVALID')
        for key in ('sample_time', 'result_time'):
            validate_medical_input(report.get(key))
    if payload.get('draft_id') is not None:
        version = payload.get('draft_version')
        if (not isinstance(version, (int, str)) or isinstance(version, bool)
                or len(str(version)) > 19 or not str(version).isascii() or not str(version).isdigit()
                or not 0 < int(version) <= 9223372036854775807):
            raise RecordRejected('DRAFT_VERSION_INVALID')
    elif payload.get('draft_version') is not None:
        raise RecordRejected('DRAFT_VERSION_INVALID')
    if payload.get('decisions') and not payload.get('candidate_set_id'):
        raise RecordRejected('CANDIDATE_SET_REQUIRED')
    return payload
