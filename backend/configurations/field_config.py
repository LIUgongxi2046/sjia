from django.conf import settings

from configurations.models import ConfigVersion
from django.db.models import Q
from configurations.qualification import configuration_qualification_reason
from configurations.schemas import payload_reason, validate_operation_input
from decimal import Decimal
from collections import Counter


class FieldConfigInvalid(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


VALUE_TYPES = frozenset({"decimal", "categorical", "text", "time"})
KINDS = frozenset({"observation", "report", "therapy"})


def validate_definition(payload: dict) -> dict:
    reason = payload_reason('content', 'field-config-1', payload)
    if reason:
        raise FieldConfigInvalid(reason)
    return payload


def validate_record_configuration(config_id, kind, fields):
    config = ConfigVersion.objects.select_for_update().filter(id=config_id, kind='content',
        schema_version='field-config-1', payload__kind=kind).first()
    if config is None:
        raise FieldConfigInvalid('CONFIG_NOT_AVAILABLE')
    reason = configuration_qualification_reason(config, settings.SJIA_ENVIRONMENT)
    if reason:
        raise FieldConfigInvalid(reason)
    definitions = {item['field_code']: item for item in config.payload['fields']}
    counts = Counter(item['field_code'] for item in fields)
    if any(item['required'] and code not in counts for code, item in definitions.items()):
        raise FieldConfigInvalid('CONFIG_REQUIRED_FIELD_MISSING')
    for field in fields:
        definition = definitions.get(field['field_code'])
        if (definition is None or not definition['writable'] or field.get('value_type') != definition['value_type']
                or counts[field['field_code']] > definition['max_occurrences']):
            raise FieldConfigInvalid('CONFIG_FIELD_NOT_WRITABLE')
        if field.get('value_state') == 'known':
            if definition['value_type'] == 'categorical' and field.get('categorical_value') not in definition['allowed_values']:
                raise FieldConfigInvalid('CONFIG_FIELD_VALUE_INVALID')
            if definition['value_type'] == 'decimal':
                if definition['allowed_units'] and field.get('raw_unit') not in definition['allowed_units']:
                    raise FieldConfigInvalid('CONFIG_FIELD_UNIT_INVALID')
                validate_operation_input('Decimal', field.get('raw_numeric'))
                value = Decimal(field['raw_numeric'])
                if not value.is_finite() or (definition['minimum'] is not None and value < Decimal(definition['minimum'])) or (
                        definition['maximum'] is not None and value > Decimal(definition['maximum'])):
                    raise FieldConfigInvalid('CONFIG_FIELD_VALUE_INVALID')
        if definition['max_length'] is not None and any(isinstance(field.get(key), str) and len(field[key]) > definition['max_length']
                for key in ('raw_text', 'original_name', 'reference_interval')):
            raise FieldConfigInvalid('CONFIG_FIELD_TEXT_TOO_LONG')
        if definition['time_required'] and (not field.get('medical_time') or field['medical_time'].get('precision') == 'unknown'):
            raise FieldConfigInvalid('CONFIG_FIELD_TIME_REQUIRED')
        if definition['dictionary_category'] and field.get('concept_code') and not field.get('dictionary_entry_id'):
            raise FieldConfigInvalid('CONFIG_DICTIONARY_SELECTION_REQUIRED')
        if field.get('dictionary_entry_id'):
            from dictionaries.service import validate_entry

            entry = validate_entry(field['dictionary_entry_id'], field.get('concept_code'))
            if definition['dictionary_category'] != entry.category:
                raise FieldConfigInvalid('CONFIG_DICTIONARY_CATEGORY_INVALID')
    return config


def read_field_config(patient, kind, version=None, *, readable_field_codes=None, writable_field_codes=()) -> dict:
    if kind not in KINDS:
        raise FieldConfigInvalid("CONFIG_KIND_INVALID")
    base = dict(
        patient_id=str(patient.id),
        kind=kind,
        config_version=None,
        checksum=None,
        state="disabled",
        reason_code="CONFIG_NOT_AVAILABLE",
        fields=[],
        watch_field_codes=[],
    )
    query = ConfigVersion.objects.filter(kind="content", schema_version="field-config-1", payload__kind=kind)
    if version is not None:
        query = query.filter(id=version)
        config = query.first()
    else:
        qualified = [item for item in query.filter(status='enabled').filter(
            Q(payload__input_context='family_record') | Q(payload__input_context__isnull=True)).order_by('-created_at', '-version')
            if configuration_qualification_reason(item, settings.SJIA_ENVIRONMENT) is None]
        if len(qualified) != 1:
            return base
        config = qualified[0]
    if config is None:
        return base
    reason = configuration_qualification_reason(config, settings.SJIA_ENVIRONMENT, allow_historical=version is not None)
    if reason:
        return dict(
            base,
            state="read_only",
            reason_code=reason,
            config_version=str(config.id),
            checksum=config.checksum,
        )
    payload = validate_definition(config.payload)
    fields = [field for field in payload['fields']
        if readable_field_codes is None or field['field_code'] in readable_field_codes]
    if not fields:
        return base
    current = config.status == 'enabled'
    fields = [{**field, 'writable': current and field['writable'] and field['field_code'] in writable_field_codes}
        for field in fields]
    codes = {field['field_code'] for field in fields}
    return dict(
        base,
        state="available" if current else 'read_only',
        reason_code=None if current else 'CONFIG_NOT_CURRENT',
        config_version=str(config.id),
        checksum=config.checksum,
        fields=fields,
        watch_field_codes=[code for code in payload['watch_field_codes'] if code in codes],
    )
