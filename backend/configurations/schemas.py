import json
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from assessment.policies import MappingPolicy, QualificationPolicy, StaticContextPolicy, StaticRulePolicy
from alerting.policies import DynamicInputPolicy, LifecyclePolicy, DynamicSchedulePolicy, weighted_trigger_reason
from alerting.preset_policies import PatientPresetAssignment
from assessment.hlh_contracts import HLHRulePolicy, HLHInputPolicy
from archives.quota import SCHEMA as ARCHIVE_QUOTA, quota_reason
from archives.provider_policy import ArchiveProviderPolicy
from configurations.service import ConfigRejected
from contacts.approval import ContactApprovalPayload
from files.purpose_schemas import ConfirmedTextProcessingPlan, PurposeProcessingPlan
from family_content.policies import ContentPolicy, VisibilityPolicy, NotificationPolicy, ValidationPolicy, SummaryPolicy
from notifications.policies import NativeNotificationPolicy, MaterialReminderChannelPolicy
from records.period_policies import SamplingFrequencyPolicy
from dictionaries.policies import DictionaryPolicy, MappingPolicyV1


ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = json.loads((ROOT / 'backend-design/protocol.schema.json').read_text())
RUNTIME = json.loads((ROOT / 'backend-design/runtime.schema.json').read_text())
ALERT_POLICY = json.loads((ROOT / 'alert-design/policy.schema.json').read_text())
SCHEMAS = {
    ('dictionary', 'dictionary-1'): DictionaryPolicy.model_json_schema(),
    ('mapping', 'mapping-1'): MappingPolicyV1.model_json_schema(),
    ('content', 'field-config-1'): {**PROTOCOL, '$ref': '#/$defs/FieldConfigDefinition'},
    ('content', 'contact-1'): ContactApprovalPayload.model_json_schema(),
    ('provider', 'backend-runtime-1'): RUNTIME,
    ('provider', 'purpose-processing-2'): PurposeProcessingPlan.model_json_schema(),
    ('provider', 'purpose-processing-text-1'): ConfirmedTextProcessingPlan.model_json_schema(),
    ('provider', 'archive-quota-1'): ARCHIVE_QUOTA,
    ('provider', 'provider-1'): ArchiveProviderPolicy.model_json_schema(),
    ('window', 'alert-window-1'): {**ALERT_POLICY, 'oneOf': [{'$ref': '#/$defs/WindowPolicy'}]},
    ('window', 'sampling-frequency-1'): SamplingFrequencyPolicy.model_json_schema(),
    ('rule', 'alert-trigger-1'): {**ALERT_POLICY, 'oneOf': [{'$ref': '#/$defs/TriggerPolicy'}]},
    ('rule', 'hlh-2004-rule-1'): HLHRulePolicy.model_json_schema(),
    ('rule', 'hlh-input-policy-1'): HLHInputPolicy.model_json_schema(),
    ('rule', 'alert-trigger-2'): {**ALERT_POLICY, 'oneOf': [{'$ref': '#/$defs/TriggerPolicyV2'}]},
    ('rule', 'assessment-rule-2'): StaticRulePolicy.model_json_schema(),
    ('rule', 'assessment-context-policy-2'): StaticContextPolicy.model_json_schema(),
    ('verification', 'qualification-policy-2'): QualificationPolicy.model_json_schema(),
    ('mapping', 'unit-mapping-2'): MappingPolicy.model_json_schema(),
    ('rule', 'dynamic-input-policy-2'): DynamicInputPolicy.model_json_schema(),
    ('rule', 'alert-lifecycle-2'): LifecyclePolicy.model_json_schema(),
    ('provider', 'dynamic-schedule-2'): DynamicSchedulePolicy.model_json_schema(),
    ('provider', 'patient-alert-presets-1'): PatientPresetAssignment.model_json_schema(),
    ('provider', 'notification-in-app-2'): NativeNotificationPolicy.model_json_schema(),
    ('notification', 'material-reminder-channel-2'): MaterialReminderChannelPolicy.model_json_schema(),
    ('content', 'content-1'): ContentPolicy.model_json_schema(),
    ('visibility', 'visibility-1'): VisibilityPolicy.model_json_schema(),
    ('notification', 'notification-1'): NotificationPolicy.model_json_schema(),
    ('validation', 'validation-1'): ValidationPolicy.model_json_schema(),
    ('summary', 'summary-1'): SummaryPolicy.model_json_schema(),
}
VALIDATORS = {key: Draft202012Validator(schema, format_checker=FormatChecker()) for key, schema in SCHEMAS.items()}


def alert_subschema_validator(reference):
    return Draft202012Validator({'$schema': ALERT_POLICY['$schema'], '$defs': ALERT_POLICY['$defs'],
        '$ref': reference}, format_checker=FormatChecker())


def validate_operation_input(name, data):
    validator = Draft202012Validator({**PROTOCOL, '$ref': f'#/$defs/{name}'}, format_checker=FormatChecker())
    if next(validator.iter_errors(data), None) is not None:
        raise ConfigRejected('CONFIG_INPUT_INVALID')
    return data


def field_definition_reason(payload):
    fields = payload['fields']
    codes = [field['field_code'] for field in fields]
    if len(codes) != len(set(codes)) or not set(payload['watch_field_codes']) <= set(codes):
        return 'CONFIG_FIELD_CODE_INVALID'
    for field in fields:
        kind = field['value_type']
        if kind == 'categorical' and not field['allowed_values']:
            return 'CONFIG_CATEGORICAL_VALUES_REQUIRED'
        if kind != 'categorical' and field['allowed_values']:
            return 'CONFIG_FIELD_TYPE_INVALID'
        if kind != 'decimal' and (field['allowed_units'] or field['minimum'] is not None or field['maximum'] is not None):
            return 'CONFIG_FIELD_TYPE_INVALID'
        if kind == 'decimal' and field['minimum'] is not None and field['maximum'] is not None:
            if Decimal(field['minimum']) > Decimal(field['maximum']):
                return 'CONFIG_DECIMAL_BOUNDS_INVALID'
    return None


def runtime_reason(payload):
    if payload['session']['idle_ttl_seconds'] > payload['session']['absolute_ttl_seconds']:
        return 'CONFIG_SESSION_LIMIT_INVALID'
    for collection, key in ((payload['tasks'], 'task_type'), (payload['providers'], 'kind'),
            (payload['limits']['rate_limits'], 'operation_id')):
        values = [item[key] for item in collection]
        if len(values) != len(set(values)):
            return 'CONFIG_RUNTIME_ENTRY_DUPLICATE'
    for task in payload['tasks']:
        if (task['heartbeat_seconds'] * 2 >= task['lease_seconds']
                or task['visibility_timeout_seconds'] <= task['execution_seconds']
                or len(task['retry_delays_seconds']) != task['max_attempts'] - 1):
            return 'CONFIG_TASK_LIMIT_INVALID'
    for provider in payload['providers']:
        if (provider['connect_timeout_ms'] > provider['total_timeout_ms']
                or provider['read_timeout_ms'] > provider['total_timeout_ms']
                or len(provider['query_delays_seconds']) != provider['query_attempts']):
            return 'CONFIG_PROVIDER_LIMIT_INVALID'
        if provider['enabled'] and provider['kind'] in ('ocr', 'voice'):
            if (not provider.get('billing_scope_key') or provider['daily_budget'] is None
                    or not provider['currency'] or 'reserved_cost' not in provider):
                return 'CONFIG_PROVIDER_BUDGET_REQUIRED'
    scopes = {}
    for provider in payload['providers']:
        if provider['enabled'] and provider['kind'] in ('ocr', 'voice'):
            scope = provider['billing_scope_key']
            budget = (provider['currency'], Decimal(provider['daily_budget']))
            if scope in scopes and scopes[scope] != budget:
                return 'CONFIG_PROVIDER_BUDGET_INCONSISTENT'
            scopes[scope] = budget
    return None


def payload_reason(kind, schema_version, payload):
    validator = VALIDATORS.get((kind, schema_version))
    if validator is None:
        return 'CONFIG_SCHEMA_UNAVAILABLE'
    if next(validator.iter_errors(payload), None) is not None:
        return 'CONFIG_PAYLOAD_INVALID'
    models = {'hlh-2004-rule-1': HLHRulePolicy, 'hlh-input-policy-1': HLHInputPolicy, 'assessment-rule-2': StaticRulePolicy, 'assessment-context-policy-2': StaticContextPolicy,
        'dictionary-1': DictionaryPolicy, 'mapping-1': MappingPolicyV1,
        'qualification-policy-2': QualificationPolicy, 'unit-mapping-2': MappingPolicy,
        'dynamic-input-policy-2': DynamicInputPolicy, 'alert-lifecycle-2': LifecyclePolicy,
        'dynamic-schedule-2': DynamicSchedulePolicy, 'patient-alert-presets-1': PatientPresetAssignment,
        'content-1': ContentPolicy, 'visibility-1': VisibilityPolicy,
        'notification-1': NotificationPolicy, 'validation-1': ValidationPolicy, 'summary-1': SummaryPolicy,
        'notification-in-app-2': NativeNotificationPolicy, 'material-reminder-channel-2': MaterialReminderChannelPolicy,
        'sampling-frequency-1': SamplingFrequencyPolicy, 'provider-1': ArchiveProviderPolicy}
    if schema_version in models:
        models[schema_version].model_validate_json(json.dumps(payload))
    if schema_version == 'field-config-1':
        return field_definition_reason(payload)
    if schema_version == 'backend-runtime-1':
        return runtime_reason(payload)
    if schema_version == 'archive-quota-1':
        return quota_reason(payload)
    if schema_version == 'alert-window-1':
        codes = [item['variable_code'] for item in payload['variables']]
        if len(codes) != len(set(codes)):
            return 'CONFIG_VARIABLE_DUPLICATE'
    if schema_version == 'alert-trigger-2':
        return weighted_trigger_reason(payload)
    if schema_version == 'alert-trigger-1':
        codes = [item['signal_id'] for item in payload['conditions']]
        if len(codes) != len(set(codes)):
            return 'CONFIG_SIGNAL_DUPLICATE'
        pending = [payload['combination']]
        used = set()
        while pending:
            group = pending.pop()
            references = group.get('signal_refs', [])
            children = group.get('groups', [])
            if not set(references) <= set(codes):
                return 'CONFIG_SIGNAL_REFERENCE_INVALID'
            if used & set(references):
                return 'CONFIG_SIGNAL_REFERENCE_DUPLICATE'
            used.update(references)
            if group['operator'] == 'at_least' and group['minimum_met'] > len(references) + len(children):
                return 'CONFIG_COMBINATION_INVALID'
            pending.extend(children)
    if schema_version == 'alert-lifecycle-2':
        for item in payload['conditions']:
            if not alert_subschema_validator('#/$defs/AssessmentCondition').is_valid(item):
                if not alert_subschema_validator('#/$defs/TrendCondition').is_valid(item):
                    if not alert_subschema_validator('#/$defs/FindingCondition').is_valid(item):
                        return 'CONFIG_LIFECYCLE_CONDITION_INVALID'
        if not alert_subschema_validator('#/$defs/Combination').is_valid(payload['combination']):
            return 'CONFIG_LIFECYCLE_COMBINATION_INVALID'
        codes = [item['signal_id'] for item in payload['conditions']]
        if len(set(codes)) != len(codes):
            return 'CONFIG_SIGNAL_DUPLICATE'
        used, pending = set(), [payload['combination']]
        while pending:
            group = pending.pop()
            refs, children = group.get('signal_refs', []), group.get('groups', [])
            if not set(refs) <= set(codes) or used & set(refs):
                return 'CONFIG_SIGNAL_REFERENCE_INVALID'
            used.update(refs)
            if group['operator'] == 'at_least' and group['minimum_met'] > len(refs) + len(children):
                return 'CONFIG_COMBINATION_INVALID'
            pending.extend(children)
    return None


def validate_payload(kind, schema_version, payload):
    reason = payload_reason(kind, schema_version, payload)
    if reason:
        raise ConfigRejected(reason)
    return payload
