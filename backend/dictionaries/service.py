import json
from decimal import Decimal, Inexact, Rounded, localcontext

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone

from assessment.policies import MappingPolicy
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from dictionaries.models import DictionaryAlias, DictionaryEntry, UnitMapping
from dictionaries.policies import DictionaryPolicy, MappingPolicyV1, normalize_alias
from sources.models import SourceRevision


class DictionaryRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


class MappingNotFound(DictionaryRejected):
    def __init__(self):
        super().__init__('UNIT_MAPPING_NOT_FOUND')


def qualified_config(config, *, kind):
    current = ConfigVersion.objects.select_for_update().get(id=config.id)
    if current.kind != kind:
        raise DictionaryRejected('DICTIONARY_CONFIG_KIND_INVALID')
    reason = configuration_qualification_reason(current, settings.SJIA_ENVIRONMENT)
    if reason:
        raise DictionaryRejected(reason)
    return current


def approved_source(source_id):
    source = SourceRevision.objects.select_related('source', 'retention_policy').get(id=source_id)
    if (source.status != 'approved' or source.source.status != 'approved' or source.captured_at > timezone.now()
            or not source.rights_evidence_ref or not source.rights_evidence_ref.strip()
            or source.retention_policy_id is None or source.retention_policy.status != 'active'):
        raise DictionaryRejected('DICTIONARY_SOURCE_UNAVAILABLE')
    return source


@transaction.atomic
def materialize_configuration(config):
    if config.kind not in ('dictionary', 'mapping'):
        raise DictionaryRejected('DICTIONARY_CONFIG_KIND_INVALID')
    config = qualified_config(config, kind=config.kind)
    if config.kind == 'dictionary':
        policy = DictionaryPolicy.model_validate_json(json.dumps(config.payload))
        for item in policy.entries:
            values = {'dictionary_version': config, 'concept_code': item.concept_code, 'category': item.category,
                'label': item.label, 'definition_ref': item.definition_ref}
            entry, created = DictionaryEntry.objects.get_or_create(id=item.id, defaults=values)
            if not created and any(getattr(entry, key) != value for key, value in values.items()):
                raise DictionaryRejected('DICTIONARY_CONTENT_CHANGED')
            for alias in item.aliases:
                approved_source(alias.source_revision_id)
                alias_values = {'dictionary_version_id': config.id, 'original_alias': alias.original_alias,
                    'source_revision_id': alias.source_revision_id}
                saved, created = DictionaryAlias.objects.get_or_create(entry_id=entry.id,
                    normalized_alias=normalize_alias(alias.original_alias), defaults=alias_values)
                if not created and any(getattr(saved, key) != value for key, value in alias_values.items()):
                    raise DictionaryRejected('DICTIONARY_CONTENT_CHANGED')
            if DictionaryAlias.objects.filter(entry_id=entry.id).count() != len(item.aliases):
                raise DictionaryRejected('DICTIONARY_CONTENT_CHANGED')
        if DictionaryEntry.objects.filter(dictionary_version=config).count() != len(policy.entries):
            raise DictionaryRejected('DICTIONARY_CONTENT_CHANGED')
    else:
        policy = (MappingPolicy if config.schema_version == 'unit-mapping-2' else MappingPolicyV1).model_validate_json(json.dumps(config.payload))
        for item in policy.mappings:
            approved_source(item.source_revision_id)
            values = {'config': config, 'concept_code': item.concept_code, 'source_revision_id': item.source_revision_id,
                'raw_unit': item.raw_unit, 'normalized_unit': item.normalized_unit, 'method_scope': item.method_scope,
                'multiplier': Decimal(item.multiplier), 'additive': Decimal(item.additive), 'exact_values_only': item.exact_values_only}
            saved, created = UnitMapping.objects.get_or_create(id=item.mapping_id, defaults=values)
            if not created and any(getattr(saved, key) != value for key, value in values.items()):
                raise DictionaryRejected('UNIT_MAPPING_CONTENT_CHANGED')
        if UnitMapping.objects.filter(config=config).count() != len(policy.mappings):
            raise DictionaryRejected('UNIT_MAPPING_CONTENT_CHANGED')
    return config


@transaction.atomic
def search_dictionary(*, actor, category, query, cursor=None, limit=20, config_id=None):
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise DictionaryRejected('DICTIONARY_LIMIT_INVALID')
    candidates = ConfigVersion.objects.filter(kind='dictionary', schema_version='dictionary-1', status='enabled')
    if config_id is not None:
        candidates = candidates.filter(id=config_id)
    qualified = [item for item in candidates if configuration_qualification_reason(item, settings.SJIA_ENVIRONMENT) is None]
    if len(qualified) != 1:
        raise DictionaryRejected('DICTIONARY_CONFIGURATION_UNAVAILABLE')
    config = materialize_configuration(qualified[0])
    normalized = normalize_alias(query)
    scope = {'version': str(config.id), 'checksum': config.checksum, 'category': category, 'query': normalized,
        'principal_id': str(actor.id), 'auth_epoch': str(actor.auth_epoch), 'limit': limit}
    start = 0
    if cursor:
        saved = signing.loads(cursor, salt='sjia.dictionary.search', max_age=3600)
        if saved['scope'] != scope:
            raise DictionaryRejected('CURSOR_STALE')
        start = saved['offset']
    aliases = list(DictionaryAlias.objects.filter(dictionary_version_id=config.id).order_by('normalized_alias', 'id'))
    alias_by_entry = {}
    for alias in aliases:
        alias_by_entry.setdefault(alias.entry_id, []).append(alias.original_alias)
    entries = [entry for entry in DictionaryEntry.objects.filter(dictionary_version=config, category=category).order_by('concept_code', 'id')
        if any(normalized in normalize_alias(value) for value in [entry.label, entry.concept_code, *alias_by_entry.get(entry.id, [])])]
    total = len(entries)
    options = [{'id': str(entry.id), 'concept_code': entry.concept_code, 'label': entry.label,
        'aliases': alias_by_entry.get(entry.id, [])} for entry in entries[start:start + limit]]
    return {'version': str(config.id), 'match_state': 'none' if not total else 'unique' if total == 1 else 'ambiguous',
        'options': options, 'next_cursor': signing.dumps({'scope': scope, 'offset': start + limit}, salt='sjia.dictionary.search')
            if start + limit < total else None}


@transaction.atomic
def validate_entry(entry_id, concept_code):
    entry = DictionaryEntry.objects.select_related('dictionary_version').get(id=entry_id)
    materialize_configuration(entry.dictionary_version)
    if entry.concept_code != concept_code:
        raise DictionaryRejected('DICTIONARY_CONCEPT_MISMATCH')
    return entry


@transaction.atomic
def convert_measurement(*, raw_numeric: Decimal, raw_unit, concept_code, config, source_revision, method_scope, qualifier='eq'):
    materialize_configuration(config)
    mapping = UnitMapping.objects.filter(config=config, concept_code=concept_code, source_revision=source_revision,
        raw_unit=raw_unit, method_scope=method_scope).first()
    if mapping is None:
        raise MappingNotFound()
    if mapping.exact_values_only and qualifier != 'eq':
        raise DictionaryRejected('UNIT_EXACT_VALUE_REQUIRED')
    raw = Decimal(raw_numeric)
    if not raw.is_finite():
        raise DictionaryRejected('UNIT_NUMERIC_INVALID')
    with localcontext() as context:
        context.prec = 100
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        normalized = raw * mapping.multiplier + mapping.additive
    return normalized, mapping.normalized_unit
