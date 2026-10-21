import json
from dataclasses import dataclass, replace
from datetime import timezone as datetime_timezone
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from archives.models import ArchiveFactVersion
from assessment.clinical_selection import qualified_clinical_selection
from assessment.configuration_proofs import configuration_proof
from assessment.medical_intervals import window_reason
from assessment.policies import QualificationPolicy, StaticContextPolicy
from assessment.provenance import entity_reference
from assessment.qualified_facts import QualificationRejected, lock_case_permissions, qualify_fact, qualified_configuration
from assessment.selection import independent_samples, qualified_relations, select_current, strictly_before, cross_analyte_reason
from assessment.service import TIMESTAMP, lock_actor, observation_scope, read_assessment
from assessment.models import Assessment
from alerting.computation import combination_result, trend_signal
from alerting.policies import DynamicInputPolicy, LifecyclePolicy
from configurations.evidence import inspect_approved_evidence
from configurations.models import ConfigVersion
from configurations.service import compute_checksum
from dependencies.models import DependencyNode
from identity.models import Principal
from records.models import RecordField
from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision
from retention.models import RetentionPolicy
from tasks.models import Task


@dataclass(frozen=True)
class DynamicInputs:
    patient: object
    actor: object
    runtime: object
    policy: object
    window: object
    use_authorization: object
    configuration_rows: tuple
    signals: tuple
    snapshot: dict
    context: dict


STATIC_SIGNAL_KINDS = {'classification_met': 'classification', 'ms_threshold_met': 'ms',
    'hlh_reference_met': 'hlh'}


def condition_variable(condition):
    return condition['variable_code'] if condition['kind'] == 'trend' else condition['field_code'] if condition['kind'] == 'professional_finding' else None


def qualified_trigger_policy(identifier, *, environment, known_at, mode):
    row = ConfigVersion.objects.select_for_update().filter(id=identifier, kind='rule',
        schema_version__in=('alert-trigger-1', 'alert-trigger-2')).first()
    if row is None:
        raise QualificationRejected('ASSESSMENT_CONFIGURATION_UNAVAILABLE')
    return qualified_configuration(identifier, kind='rule', schema_version=row.schema_version,
        environment=environment, known_at=known_at, mode=mode)


def medical_context(policies, selected):
    states, reasons, limitations = {}, [], []
    applicability = 'applicable'
    for role, policy in policies.items():
        values = [selected[item.variable_code].selected for item in policy.conditions]
        state = ('unknown' if role == 'therapy' and not values else 'not_required_by_policy' if not values
            else 'not_met' if any(item is not None and item.value == 0 for item in values)
            else 'unknown' if any(item is None for item in values) else 'met')
        states[role] = state
        if state not in ('unknown', 'not_met'):
            continue
        action = policy.unknown_action if state == 'unknown' else policy.not_met_action
        item = {'variable_code': role, 'reason_codes': [f'DYNAMIC_{role.upper()}_{state.upper()}']}
        if action == 'record_limitation':
            limitations.append(item)
        elif action == 'not_applicable':
            applicability = 'not_applicable'
        else:
            reasons.append(item)
    return states, reasons, limitations, applicability


def static_output_state(kind, output):
    if kind == 'classification_met':
        return 'met' if output['classification_result'] == 'meets' else 'not_met' if output['classification_result'] in ('not_meets', 'not_applicable') else 'unknown'
    if kind == 'ms_threshold_met':
        return 'unknown' if output['ms_threshold_result'] is None else 'met' if output['ms_threshold_result'] else 'not_met'
    if kind == 'hlh_reference_met':
        # Use the final applicability-qualified reference result, never the raw
        # molecular/criteria count and never a missing or inapplicable negative.
        value = (output.get('hlh_result') or {}).get('reference_result')
        return {'criteria_supported': 'met', 'criteria_not_supported': 'not_met'}.get(value, 'unknown')
    raise QualificationRejected('DYNAMIC_STATIC_CONDITION_INVALID')


def static_signal(condition, patient, actor, epoch, environment, payload):
    query = Assessment.objects.select_for_update().filter(patient=patient, rule_id=condition['assessment_rule_id'],
        kind=STATIC_SIGNAL_KINDS[condition['kind']], validity='valid',
        data_epoch=patient.data_epoch, verification_epoch=patient.verification_epoch,
        assessment_at=TIMESTAMP.validate_python(payload['assessment_at']))
    for row in query.order_by('-computed_at', '-id'):
        task = Task.objects.select_for_update().get(id=row.task_id, task_type='assessment', schema_version='static-assessment-task-2')
        if (task.status != 'completed' or TIMESTAMP.validate_python(task.input_refs['payload']['known_at']) != TIMESTAMP.validate_python(payload['known_at'])
                or task.input_refs['payload']['mode'] != payload['mode']):
            continue
        owner = Principal.objects.select_for_update().get(id=task.actor_id)
        node = DependencyNode.objects.select_for_update().filter(resource_type='assessment', resource_id=row.id, resource_version='1').first()
        if (owner.account_status != 'active' or str(owner.auth_epoch) != task.input_refs['authorization_context']['auth_epoch']
                or node is None or node.validity != 'valid'):
            continue
        value = read_assessment(assessment_id=row.id, actor=actor, auth_epoch=epoch, environment=environment)
        if value['validity'] != 'valid' or value['computation_status'] != 'completed':
            continue
        state = static_output_state(condition['kind'], value['output'])
        return {'state': state, 'reason_codes': ['DYNAMIC_STATIC_RESULT_INSUFFICIENT'] if state == 'unknown' else [],
            'pair_results': [], 'samples': [], 'assessment_reference': entity_reference(row), 'assessment_id': str(row.id), 'assessment_kind': row.kind}
    return {'state': 'unknown', 'reason_codes': ['DYNAMIC_COMPATIBLE_STATIC_RESULT_REQUIRED'], 'pair_results': [], 'samples': []}


@transaction.atomic
def collect_dynamic_inputs(*, patient_id, actor, auth_epoch, environment, payload):
    gate, patient, actor, grant, runtime = lock_actor(patient_id, actor, auth_epoch, environment, 'execute_assessment')
    assessment_at, known_at = TIMESTAMP.validate_python(payload['assessment_at']), TIMESTAMP.validate_python(payload['known_at'])
    if (assessment_at.utcoffset() is None or known_at.utcoffset() is None or assessment_at > known_at
            or known_at > timezone.now() or payload['mode'] not in ('current', 'replay')):
        raise QualificationRejected('DYNAMIC_CONTEXT_INVALID')
    def config(identifier, kind, schema):
        return qualified_configuration(identifier, kind=kind, schema_version=schema, environment=environment,
            known_at=known_at, mode=payload['mode'])
    policy = qualified_trigger_policy(payload['policy_id'], environment=environment,
        known_at=known_at, mode=payload['mode'])
    window = config(payload['window_policy_id'], 'window', 'alert-window-1')
    input_rule = config(payload['input_rule_id'], 'rule', 'dynamic-input-policy-2')
    professional = config(payload['verification_policy_id'], 'verification', 'qualification-policy-2')
    definition = DynamicInputPolicy.model_validate_json(json.dumps(input_rule.payload))
    qualification = QualificationPolicy.model_validate_json(json.dumps(professional.payload))
    if (policy.payload['window_policy_id'] != str(window.id) or policy.payload['scope'] != window.payload['scope']
            or definition.purpose != qualification.purpose
            or definition.max_combination_operands > runtime.payload['limits']['max_record_fields']
            or definition.max_combination_depth > runtime.payload['limits']['max_record_fields']
            or any(item.relative_decimal_places >= runtime.payload['limits']['max_decimal_digits'] for item in definition.comparisons)):
        raise QualificationRejected('DYNAMIC_CONFIGURATION_SCOPE_MISMATCH')
    grants = lock_case_permissions(actor, patient, 'execute_assessment', qualification.purpose, timezone.now(), whole_resource=True)
    authorization = DataUseAuthorization.objects.select_for_update().get(id=payload['use_authorization_id'], patient=patient)
    source = SourceRevision.objects.select_for_update().get(id=authorization.source_revision_id)
    registry = SourceRegistry.objects.select_for_update().get(id=source.source_id)
    approver = Principal.objects.select_for_update().get(id=authorization.approved_by_id)
    retention = RetentionPolicy.objects.select_for_update().get(id=source.retention_policy_id)
    if (authorization.purpose != qualification.purpose or authorization.environment != environment
            or authorization.approved_at > known_at or authorization.revoked_at is not None
            or authorization.valid_until is not None and authorization.valid_until <= timezone.now()
            or not authorization.evidence_ref.strip() or approver.account_status != 'active'
            or source.status != 'approved' or registry.status != 'approved' or source.captured_at > known_at
            or qualification.purpose not in source.allowed_use or not source.license.strip()
            or not source.rights_evidence_ref or not source.rights_evidence_ref.strip() or retention.status != 'active'):
        raise QualificationRejected('DYNAMIC_USE_AUTHORIZATION_UNAVAILABLE')
    rows = [runtime, policy, window, input_rule, professional]
    if policy.schema_version == 'alert-trigger-2':
        for reference in policy.payload['combination']['evidence_refs']:
            for point in (known_at, timezone.now()):
                _, _, reason = inspect_approved_evidence(reference, purpose='clinical_policy', lock=True, now=point)
                if reason:
                    raise QualificationRejected(reason)
    context_ids = {'population': window.payload['scope']['population_config_id'], 'therapy': window.payload['scope']['therapy_policy_id'],
        'clinical_finding': window.payload['clinical_finding_validity_policy_id'], 'late_result': window.payload['late_result_policy_id']}
    contexts, variables, context_by_code = {}, {item.variable_code: item for item in definition.variables}, {}
    for role, identifier in context_ids.items():
        row = config(identifier, 'rule', 'assessment-context-policy-2')
        rows.append(row)
        context = StaticContextPolicy.model_validate_json(json.dumps(row.payload))
        if context.role != role or context.purpose != qualification.purpose:
            raise QualificationRejected('DYNAMIC_CONTEXT_POLICY_MISMATCH')
        contexts[role] = context
        for variable in context.conditions:
            if variable.variable_code in variables or variable.concept_code in {item.concept_code for item in variables.values()}:
                raise QualificationRejected('DYNAMIC_CONTEXT_VARIABLE_DUPLICATE')
            variables[variable.variable_code] = variable
            context_by_code[variable.variable_code] = row.id
    lifecycle = {}
    for role in ('reset', 'retrigger'):
        row = config(policy.payload['episode'][role + '_policy_id'], 'rule', 'alert-lifecycle-2')
        value = LifecyclePolicy.model_validate_json(json.dumps(row.payload))
        if value.kind != role or value.purpose != qualification.purpose:
            raise QualificationRejected('DYNAMIC_LIFECYCLE_POLICY_MISMATCH')
        lifecycle[role] = value
        rows.append(row)
    condition_sets = {'trigger': policy.payload['conditions'], 'reset': lifecycle['reset'].conditions, 'retrigger': lifecycle['retrigger'].conditions}
    policies_by_code = {code: professional.id for code in variables}
    for conditions in condition_sets.values():
        for condition in conditions:
            code = condition_variable(condition)
            if code is None:
                rows.append(config(condition['assessment_rule_id'], 'rule',
                    'hlh-2004-rule-1' if condition['kind'] == 'hlh_reference_met' else 'assessment-rule-2'))
                continue
            if code not in variables or code in context_by_code:
                raise QualificationRejected('DYNAMIC_CONDITION_VARIABLE_REQUIRED')
            if condition['kind'] == 'trend' and variables[code].value_type != 'decimal':
                raise QualificationRejected('DYNAMIC_CONDITION_TYPE_MISMATCH')
            if condition['kind'] == 'trend' and condition['unit'] != (
                    '%' if condition['metric'] == 'relative_delta' else variables[code].normalized_unit):
                raise QualificationRejected('DYNAMIC_THRESHOLD_UNIT_MISMATCH')
            if condition['kind'] == 'professional_finding':
                if variables[code].value_type != 'categorical':
                    raise QualificationRejected('DYNAMIC_CONDITION_TYPE_MISMATCH')
                identifier = UUID(condition['verification_policy_id'])
                if policies_by_code[code] != professional.id and policies_by_code[code] != identifier:
                    raise QualificationRejected('DYNAMIC_FIELD_POLICY_CONFLICT')
                policies_by_code[code] = identifier
                row = config(identifier, 'verification', 'qualification-policy-2')
                if row.payload['purpose'] != qualification.purpose:
                    raise QualificationRejected('DYNAMIC_FIELD_POLICY_PURPOSE_MISMATCH')
                rows.append(row)
            for reference in condition['evidence_refs']:
                _, _, reason = inspect_approved_evidence(reference, purpose='clinical_policy', lock=True, now=known_at)
                if reason:
                    raise QualificationRejected(reason)
    windows = {item['variable_code']: item for item in window.payload['variables']}
    if not set(variables) <= set(windows):
        raise QualificationRejected('DYNAMIC_VARIABLE_WINDOW_REQUIRED')
    period = observation_scope(patient, payload, known_at)
    if period is not None and (assessment_at < period['lower'] or assessment_at > period['upper']
            or assessment_at == period['upper'] and not period['upper_inclusive']):
        raise QualificationRejected('DYNAMIC_TIME_OUTSIDE_OBSERVATION_PERIOD')
    choices = {}
    for choice in payload.get('clinical_selections', []):
        key = choice['input_role'], choice['variable_code']
        if (key in choices or key[1] not in variables or not choice['reason'].strip()
                or key[0] == 'current' and windows[key[1]]['current_selection'] != 'explicit_clinical_selection'
                or key[0] == 'baseline' and windows[key[1]]['baseline_selection'] != 'explicit_clinical_baseline'):
            raise QualificationRejected('DYNAMIC_EXPLICIT_SELECTION_INVALID')
        choices[key] = {key: value for key, value in choice.items() if key != 'input_role'}
    candidates, selected, baselines, series, relation_sets, candidate_refs, exclusions, relation_refs, proofs = {}, {}, {}, {}, {}, [], [], {}, []
    relation_proofs = {}
    from assessment.simulation_inputs import collect_simulation_fact, simulation_scope

    simulated_scope = simulation_scope(environment=environment, patient_id=patient.id, actor_id=actor.id,
        rule_id=input_rule.id, source_id=authorization.source_revision_id)
    for code, variable in variables.items():
        values = []
        fields = RecordField.objects.filter(patient=patient, concept_code=variable.concept_code)
        if simulated_scope:
            fields = RecordField.objects.filter(patient=patient).filter(Q(concept_code=variable.concept_code)
                | Q(concept_code__isnull=True, field_code=variable.concept_code))
        for fact in ArchiveFactVersion.objects.select_for_update().filter(patient_id=patient.id, field_id__in=fields.values('id')).order_by('id'):
            if simulated_scope:
                value = collect_simulation_fact(fact=fact, patient=patient, actor=actor, rule_config=input_rule,
                    variable=variable, window=windows[code], environment=environment, known_at=known_at,
                    assessment_at=assessment_at, mode=payload['mode'], purpose=qualification.purpose, input_role='context')
            else:
                value = qualify_fact(fact_id=fact.id, patient=patient, actor=actor, auth_epoch=auth_epoch,
                    rule_id=input_rule.id, window_policy_id=window.id, verification_policy_id=policies_by_code[code], variable_code=code,
                    environment=environment, known_at=known_at, assessment_at=assessment_at, mode=payload['mode'],
                    context_policy_id=context_by_code.get(code), input_role='series' if variable.value_type == 'decimal' else 'context',
                    rule_schema='dynamic-input-policy-2')
            if value.eligible and period is not None and (value.interval.lower < period['lower'] or value.interval.upper > period['upper']
                    or value.interval.upper == period['upper'] and value.interval.upper_inclusive and not period['upper_inclusive']):
                value = replace(value, reasons=('FACT_OUTSIDE_OBSERVATION_PERIOD',))
            values.append(value)
            candidate_refs.append({'variable_code': code, 'reference': value.reference, 'reason_codes': list(value.reasons)})
            if len(json.dumps(candidate_refs, separators=(',', ':')).encode('utf-8')) > runtime.payload['api']['max_json_bytes']:
                raise QualificationRejected('DYNAMIC_CONTEXT_LIMIT_EXCEEDED')
        policy_config = professional if policies_by_code[code] == professional.id else config(policies_by_code[code], 'verification', 'qualification-policy-2')
        field_policy = QualificationPolicy.model_validate_json(json.dumps(policy_config.payload))
        relations = qualified_relations(facts=values, patient=patient, policy_config=policy_config, policy=field_policy,
            environment=environment, known_at=known_at, mode=payload['mode'], maximum_bytes=runtime.payload['api']['max_json_bytes'])
        independent, excluded, refs = independent_samples(values, relations)
        eligible = list(independent.values())
        exclusions.extend(excluded)
        relation_refs.update({item['id']: item for item in refs})
        for relation in relations:
            relation_proofs[relation.reference['id']] = {'relation_id': relation.reference['id'],
                'references': list(relation.proof_refs), 'reason_codes': list(relation.reasons)}
        relation_sets[code], series[code], candidates[code] = relations, eligible, values
        current_values = [replace(item, reasons=(reason,)) if item.eligible and (reason := window_reason(item.interval, assessment_at, windows[code])) else item for item in values]
        choice = choices.get(('current', code))
        if choice:
            proofs.append(qualified_clinical_selection(choice=choice, facts=current_values, patient=patient, actor=actor,
                policy=field_policy, environment=environment, known_at=known_at, payload=payload))
        current = select_current(current_values, relations, windows[code], explicit_fact_id=choice['fact_version_id'] if choice else None)
        selected[code] = current
        exclusions.extend(current.exclusions)
        baseline = None
        if variable.value_type == 'decimal' and current.selected is not None:
            preceding = [item for item in eligible if strictly_before(item.interval, current.selected.interval)]
            choice = choices.get(('baseline', code))
            if windows[code]['baseline_selection'] == 'explicit_clinical_baseline':
                if choice:
                    proofs.append(qualified_clinical_selection(choice=choice, facts=preceding, patient=patient, actor=actor,
                        policy=field_policy, environment=environment, known_at=known_at, payload=payload, input_role='baseline'))
                    baseline = next(item for item in preceding if item.reference['fact_version_id'] == choice['fact_version_id'])
            elif choice:
                raise QualificationRejected('DYNAMIC_BASELINE_SELECTION_NOT_APPROVED')
            else:
                latest = [item for item in preceding if all(other is item or strictly_before(other.interval, item.interval) for other in preceding)]
                if len(latest) == 1:
                    baseline = latest[0]
        baselines[code] = baseline
    consumed_choices = {(proof['input_role'], proof['choice']['variable_code']) for proof in proofs}
    if consumed_choices != set(choices):
        raise QualificationRejected('DYNAMIC_EXPLICIT_SELECTION_NOT_ADOPTED')
    context_states, context_reasons, limitations, applicability = medical_context(contexts, selected)
    gap_reason = cross_analyte_reason({code: value for code, value in selected.items() if variables[code].value_type == 'decimal'}, window.payload['max_cross_analyte_gap_seconds'])
    if gap_reason:
        context_reasons.append({'variable_code': 'context', 'reason_codes': [gap_reason]})
    comparisons = {item.variable_code: item for item in definition.comparisons}
    signals, groups = [], {}
    for scope, conditions in condition_sets.items():
        results = {}
        for condition in conditions:
            code = condition_variable(condition)
            if code is None:
                value = static_signal(condition, patient, actor, auth_epoch, environment, payload)
            elif condition['kind'] == 'trend':
                value = trend_signal(condition, series[code], baselines[code], selected[code].selected,
                    comparisons[code], windows[code], relation_sets[code], runtime.payload['limits']['max_decimal_digits'])
            else:
                fact = selected[code].selected
                samples = []
                if fact is not None:
                    ordered = sorted([item for item in series[code] if item is fact or strictly_before(item.interval, fact.interval)],
                        key=lambda item: (item.interval.lower, item.reference['fact_version_id']), reverse=True)
                    for item in ordered:
                        if item.reference['assertion_state'] != condition['required_state']:
                            break
                        if samples and (not strictly_before(item.interval, samples[-1].interval)
                                or not any(not relation.reasons and relation.relation_type == 'independent_sampling'
                                    and {relation.left_id, relation.right_id} == {item.reference['fact_version_id'], samples[-1].reference['fact_version_id']}
                                    for relation in relation_sets[code])):
                            break
                        samples.append(item)
                    samples.reverse()
                value = {'state': 'unknown' if fact is None else 'met' if fact.reference['assertion_state'] == condition['required_state'] else 'not_met',
                    'reason_codes': list(selected[code].reasons) if fact is None else [], 'pair_results': [], 'samples': samples if samples else [] if fact is None else [fact]}
            if context_reasons:
                value = {**value, 'state': 'unknown', 'reason_codes': list(dict.fromkeys(value['reason_codes'] + [reason for item in context_reasons for reason in item['reason_codes']]))}
            elif applicability == 'not_applicable':
                value = {**value, 'state': 'not_met', 'reason_codes': list(dict.fromkeys(value['reason_codes'] + ['DYNAMIC_POPULATION_NOT_APPLICABLE']))}
            context_samples = [selected[item.variable_code].selected for context in contexts.values() for item in context.conditions if selected[item.variable_code].selected is not None]
            samples = value.pop('samples')
            inputs = []
            for position, fact in enumerate(samples + context_samples):
                role = 'context' if fact in context_samples else 'current' if selected[code].selected is fact else 'baseline' if baselines[code] is fact else 'series'
                inputs.append({'position': position, 'variable_code': code if role != 'context' else next(name for name, selection in selected.items() if selection.selected is fact),
                    'input_role': role, 'reference': fact.reference, 'value': format(fact.value, 'f')})
            value.update({'signal_id': scope + ':' + condition['signal_id'], 'schema_version': 'alert-signal-1',
                'algorithm_version': 'dynamic-2', 'input_field_ids': sorted({item['reference']['field_id'] for item in inputs}), 'inputs': inputs})
            results[condition['signal_id']] = value
            signals.append(value)
        group = policy.payload['combination'] if scope == 'trigger' else lifecycle[scope].combination
        groups[scope] = combination_result(group, results, maximum_depth=definition.max_combination_depth,
            maximum_operands=definition.max_combination_operands,
            maximum_digits=runtime.payload['limits']['max_decimal_digits'])
    for variable in variables.values():
        if variable.mapping_config_id is not None:
            rows.append(config(variable.mapping_config_id, 'mapping', 'unit-mapping-2'))
    configurations = {str(row.id): row for row in rows}
    selected_refs = {f'current:{code}': value.selected.reference if value.selected is not None else None for code, value in selected.items()}
    selected_refs.update({f'baseline:{code}': value.reference if value is not None else None for code, value in baselines.items()})
    snapshot = {'schema_version': 'dynamic-evaluation-inputs-2', 'candidate_refs': candidate_refs, 'exclusions': exclusions,
        'relation_refs': list(relation_refs.values()), 'relation_proofs': list(relation_proofs.values()),
        'selected_refs': selected_refs, 'clinical_selections': proofs,
        'context_states': context_states, 'context_reasons': context_reasons, 'limitations': limitations, 'applicability': applicability,
        'signals': signals, 'combinations': groups, 'lifecycle': {key: value.model_dump(mode='json') for key, value in lifecycle.items()},
        'observation_period': None if period is None else {'references': period['references'], 'lower': period['lower'].isoformat(),
            'upper': period['upper'].isoformat(), 'upper_inclusive': period['upper_inclusive']},
        'use_references': [entity_reference(value) for value in (authorization, source, registry, retention, approver)],
        'configurations': [configuration_proof(row, environment, known_at, payload['mode']) for row in sorted(configurations.values(), key=lambda row: str(row.id))]}
    if policy.schema_version == 'alert-trigger-2':
        snapshot['sensitivity_preset'] = {**policy.payload['sensitivity_preset'],
            'config_id': str(policy.id), 'config_checksum': policy.checksum}
    if len(json.dumps(snapshot, separators=(',', ':')).encode('utf-8')) > runtime.payload['api']['max_json_bytes']:
        raise QualificationRejected('DYNAMIC_CONTEXT_LIMIT_EXCEEDED')
    context = {'environment': environment, 'actor_id': str(actor.id), 'patient_id': str(patient.id), 'purpose': qualification.purpose,
        'auth_epoch': str(actor.auth_epoch), 'data_epoch': str(patient.data_epoch), 'verification_epoch': str(patient.verification_epoch),
        'publication_epoch': str(gate.publication_epoch), 'runtime_id': str(runtime.id), 'runtime_checksum': runtime.checksum,
        'grant_id': str(grant.id), 'case_grant_ids': sorted(str(item.id) for item in grants), 'snapshot_sha256': compute_checksum(snapshot),
        'verification_policy_id': str(professional.id), 'rule_family_key': policy.payload['episode']['rule_family_key'],
        'assessment_at_utc': assessment_at.astimezone(datetime_timezone.utc).isoformat(timespec='microseconds'),
        'implementation_sha256': snapshot['configurations'][0]['artifact_sha256']}
    return DynamicInputs(patient, actor, runtime, policy, window, authorization, tuple(configurations.values()), tuple(signals), snapshot, context)
