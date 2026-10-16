"""Read-only, approved unit projections for objective historical comparisons.

Never materializes configurations or overwrites raw records during a read. A
projection requires an exact source/concept/unit/method definition; no wildcard
or universal conversion is inferred. Ambiguity is deliberately not resolved by
"latest" configuration ordering.
"""
import json
from decimal import Decimal, DecimalException, Inexact, Rounded, localcontext

from django.conf import settings
from assessment.policies import MappingPolicy
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from dictionaries.policies import MappingPolicyV1
from dictionaries.service import DictionaryRejected, approved_source
from sources.models import SourceRevision


def approved_projections(fields, versions):
    wanted = {(field.concept_code, versions[field.record_version_id].source_revision_id,
        field.raw_unit, field.method) for field in fields if field.raw_unit and field.method}
    definitions = {}
    for config in ConfigVersion.objects.filter(kind='mapping', status='enabled',
            schema_version__in=['mapping-1', 'unit-mapping-2']):
        if configuration_qualification_reason(config, settings.SJIA_ENVIRONMENT):
            continue
        policy_type = MappingPolicy if config.schema_version == 'unit-mapping-2' else MappingPolicyV1
        policy = policy_type.model_validate_json(json.dumps(config.payload))
        for item in policy.mappings:
            key = (item.concept_code, item.source_revision_id, item.raw_unit, item.method_scope)
            if key not in wanted:
                continue
            try:
                approved_source(item.source_revision_id)
            except (DictionaryRejected, SourceRevision.DoesNotExist):
                continue
            definitions.setdefault(key, []).append((config, item))
    projections = {}
    for field in fields:
        if (field.value_state != 'known' or field.value_type != 'decimal'
                or field.qualifier != 'eq' or field.raw_numeric is None):
            continue
        key = (field.concept_code, versions[field.record_version_id].source_revision_id,
            field.raw_unit, field.method)
        choices = definitions.get(key, [])
        if len(choices) != 1:
            continue
        config, definition = choices[0]
        try:
            raw = Decimal(field.raw_numeric)
            if not raw.is_finite():
                continue
            with localcontext() as context:
                context.prec = 100
                context.traps[Inexact] = True
                context.traps[Rounded] = True
                value = raw * Decimal(definition.multiplier) + Decimal(definition.additive)
            if not value.is_finite():
                continue
        except DecimalException:
            continue
        projections[field.id] = {'value': value, 'unit': definition.normalized_unit,
            'config_id': str(config.id), 'config_checksum': config.checksum,
            'mapping_id': str(definition.mapping_id),
            'source_revision_id': str(definition.source_revision_id)}
    return projections


def comparison_basis(previous, current, projections):
    before, after = projections.get(previous.id), projections.get(current.id)
    if (before and after and before['config_id'] == after['config_id']
            and before['unit'] == after['unit']):
        return {'kind': 'approved_mapping', 'previous_field_id': str(previous.id),
            'previous_value': format(before['value'], 'f'), 'current_value': format(after['value'], 'f'),
            'unit': after['unit'], 'method': current.method,
            'config_id': after['config_id'], 'config_checksum': after['config_checksum'],
            'previous_mapping_id': before['mapping_id'], 'current_mapping_id': after['mapping_id'],
            'previous_source_revision_id': before['source_revision_id'],
            'current_source_revision_id': after['source_revision_id']}
    if previous.raw_unit and previous.raw_unit == current.raw_unit:
        return {'kind': 'raw', 'previous_field_id': str(previous.id),
            'previous_value': str(previous.raw_numeric), 'current_value': str(current.raw_numeric),
            'unit': current.raw_unit, 'method': current.method,
            'config_id': None, 'config_checksum': None, 'previous_mapping_id': None,
            'current_mapping_id': None, 'previous_source_revision_id': None,
            'current_source_revision_id': None}
    return None
