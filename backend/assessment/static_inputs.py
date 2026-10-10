import json
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from archives.models import ArchiveFactVersion
from assessment.policies import QualificationPolicy, StaticContextPolicy, StaticRulePolicy, static_variable
from assessment.qualified_facts import QualificationRejected, QualifiedFact, lock_case_permissions, qualified_configuration, qualify_fact
from assessment.selection import cross_analyte_reason, qualified_relations, select_current
from assessment.clinical_selection import qualified_clinical_selection, selection_index
from configurations.service import compute_checksum
from authorization.policy import check_principal_session, check_service_grant
from family_content.gate_locks import read_environment_gate_shared
from identity.models import Patient, Principal
from records.models import RecordField


@dataclass(frozen=True)
class StaticInputs:
    rule: object
    window: object
    verification_policy: object
    selections: dict
    candidate_refs: tuple[dict, ...]
    exclusions: tuple[dict, ...]
    relation_refs: tuple[dict, ...]
    context_reasons: tuple[dict, ...]
    context_selections: dict
    context_policies: tuple
    context_states: dict
    limitations: tuple[dict, ...]
    applicability: str
    sha256: str
    clinical_selections: tuple[dict, ...]


@transaction.atomic
def collect_static_inputs(*, patient, actor, auth_epoch, rule_id, window_policy_id, verification_policy_id,
        environment, known_at, assessment_at, mode, maximum_bytes, observation_scope=None, selection_payload=None):
    if (mode not in ('current', 'replay') or known_at.utcoffset() is None or assessment_at.utcoffset() is None
            or assessment_at > known_at or known_at > timezone.now()):
        raise QualificationRejected('ASSESSMENT_CONTEXT_INVALID')
    gate = read_environment_gate_shared(environment)
    patient = Patient.objects.select_for_update().get(id=patient.id)
    actor = Principal.objects.select_for_update().get(id=actor.id)
    if not gate.enabled or patient.status != 'active':
        raise QualificationRejected('ASSESSMENT_ENVIRONMENT_UNAVAILABLE')
    check_principal_session(actor, auth_epoch)
    check_service_grant(actor, 'execute_assessment', environment, timezone.now())
    rule_config = qualified_configuration(rule_id, kind='rule', schema_version='assessment-rule-2',
        environment=environment, known_at=known_at, mode=mode)
    rule = StaticRulePolicy.model_validate_json(json.dumps(rule_config.payload))
    window_config = qualified_configuration(window_policy_id, kind='window', schema_version='alert-window-1',
        environment=environment, known_at=known_at, mode=mode)
    verification_config = qualified_configuration(verification_policy_id, kind='verification', schema_version='qualification-policy-2',
        environment=environment, known_at=known_at, mode=mode)
    verification_policy = QualificationPolicy.model_validate_json(json.dumps(verification_config.payload))
    lock_case_permissions(actor, patient, 'execute_assessment', verification_policy.purpose, timezone.now(), whole_resource=True)
    codes = [item.variable_code for item in rule.variables] + ['sjia', 'fever']
    variables = {code: static_variable(rule, code) for code in codes}
    context_ids = {'population': window_config.payload['scope']['population_config_id'],
        'therapy': window_config.payload['scope']['therapy_policy_id'],
        'clinical_finding': window_config.payload['clinical_finding_validity_policy_id'],
        'late_result': window_config.payload['late_result_policy_id']}
    contexts = {}
    context_configs = []
    variable_contexts = {code: None for code in codes}
    for role, config_id in context_ids.items():
        config = qualified_configuration(config_id, kind='rule', schema_version='assessment-context-policy-2',
            environment=environment, known_at=known_at, mode=mode)
        policy = StaticContextPolicy.model_validate_json(json.dumps(config.payload))
        if policy.role != role or policy.purpose != verification_policy.purpose:
            raise QualificationRejected('ASSESSMENT_CONTEXT_POLICY_MISMATCH')
        contexts[role] = policy
        context_configs.append(config)
        for variable in policy.conditions:
            if variable.variable_code in variables or variable.concept_code in {item.concept_code for item in variables.values()}:
                raise QualificationRejected('ASSESSMENT_CONTEXT_VARIABLE_DUPLICATE')
            variables[variable.variable_code] = variable
            variable_contexts[variable.variable_code] = config.id
    windows = {item['variable_code']: item for item in window_config.payload['variables']}
    if not variables.keys() <= windows.keys():
        raise QualificationRejected('ASSESSMENT_VARIABLE_WINDOW_REQUIRED')
    fields = RecordField.objects.filter(patient_id=patient.id,
        concept_code__in=[item.concept_code for item in variables.values()])
    facts = ArchiveFactVersion.objects.select_for_update().filter(patient_id=patient.id,
        field_id__in=fields.values('id')).order_by('id')
    candidates = {code: [] for code in variables}
    variable_by_concept = {item.concept_code: code for code, item in variables.items()}
    consumed = 0
    candidate_refs = []
    for fact in facts.iterator():
        field = RecordField.objects.select_for_update().get(id=fact.field_id, patient_id=patient.id)
        code = variable_by_concept[field.concept_code]
        value = qualify_fact(fact_id=fact.id, patient=patient, actor=actor, auth_epoch=auth_epoch, rule_id=rule_id,
            window_policy_id=window_policy_id, verification_policy_id=verification_policy_id, variable_code=code,
            environment=environment, known_at=known_at, assessment_at=assessment_at, mode=mode,
            context_policy_id=variable_contexts[code])
        if value.eligible and observation_scope is not None:
            interval = value.interval
            if (interval.lower < observation_scope['lower'] or interval.upper > observation_scope['upper']
                    or interval.upper == observation_scope['upper'] and interval.upper_inclusive and not observation_scope['upper_inclusive']):
                value = QualifiedFact(value.reference, interval, value.value, ('FACT_OUTSIDE_OBSERVATION_PERIOD',))
        reference = {'variable_code': code, 'reference': value.reference, 'reason_codes': list(value.reasons)}
        consumed += len(json.dumps(reference, separators=(',', ':')).encode('utf-8'))
        if consumed > maximum_bytes:
            raise QualificationRejected('ASSESSMENT_CONTEXT_LIMIT_EXCEEDED')
        candidate_refs.append(reference)
        candidates[code].append(value)
    selections = {}
    choices = selection_index([] if selection_payload is None else selection_payload.get('clinical_selections', []), variables, windows)
    selection_proofs = []
    relation_refs = {}
    exclusions = []
    for code, values in candidates.items():
        relations = qualified_relations(facts=values, patient=patient, policy_config=verification_config, policy=verification_policy,
            environment=environment, known_at=known_at, mode=mode, maximum_bytes=maximum_bytes)
        choice = choices.get(code)
        if choice is not None:
            selection_proofs.append(qualified_clinical_selection(choice=choice, facts=values, patient=patient, actor=actor,
                policy=verification_policy, environment=environment, known_at=known_at, payload=selection_payload))
        selected = select_current(values, relations, windows[code], explicit_fact_id=None if choice is None else choice['fact_version_id'])
        selections[code] = selected
        exclusions.extend(selected.exclusions)
        for reference in selected.relations:
            relation_refs[reference['id']] = reference
    reason = cross_analyte_reason({code: value for code, value in selections.items()
        if variables[code].value_type == 'decimal'}, window_config.payload['max_cross_analyte_gap_seconds'])
    context_reasons = [] if reason is None else [{'variable_code': 'context', 'reason_codes': [reason]}]
    context_states = {}
    limitations = []
    applicability = 'applicable'
    for role, policy in contexts.items():
        selected = [selections[item.variable_code].selected for item in policy.conditions]
        state = ('unknown' if role == 'therapy' and not selected else 'not_required_by_policy' if not selected
            else 'not_met' if any(item is not None and item.value == 0 for item in selected)
            else 'unknown' if any(item is None for item in selected) else 'met')
        context_states[role] = state
        if state not in ('unknown', 'not_met'):
            continue
        action = policy.unknown_action if state == 'unknown' else policy.not_met_action
        item = {'variable_code': role, 'reason_codes': [f'ASSESSMENT_{role.upper()}_{state.upper()}']}
        if action == 'record_limitation':
            limitations.append(item)
        elif action == 'not_applicable':
            applicability = 'not_applicable'
        else:
            context_reasons.append(item)
    content = {'rule_id': str(rule_config.id), 'rule_checksum': rule_config.checksum,
        'window_policy_id': str(window_config.id), 'window_checksum': window_config.checksum,
        'verification_policy_id': str(verification_config.id), 'verification_checksum': verification_config.checksum,
        'context_policies': [{'id': str(item.id), 'checksum': item.checksum} for item in context_configs],
        'candidate_refs': candidate_refs, 'exclusions': exclusions, 'relation_refs': list(relation_refs.values()),
        'selected': {code: value.selected.reference['fact_version_id'] if value.selected is not None else None
            for code, value in selections.items()}, 'context_reasons': context_reasons,
        'context_states': context_states, 'limitations': limitations, 'applicability': applicability,
        'clinical_selections': selection_proofs}
    if len(json.dumps(content, separators=(',', ':')).encode('utf-8')) > maximum_bytes:
        raise QualificationRejected('ASSESSMENT_CONTEXT_LIMIT_EXCEEDED')
    return StaticInputs(rule_config, window_config, verification_config, {code: selections[code] for code in codes}, tuple(candidate_refs),
        tuple(exclusions), tuple(relation_refs.values()), tuple(context_reasons),
        {code: value for code, value in selections.items() if code not in codes}, tuple(context_configs),
        context_states, tuple(limitations), applicability, compute_checksum(content), tuple(selection_proofs))
