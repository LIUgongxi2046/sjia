"""Patient-specific approved schedule choices. IDs select policy; they grant no authority."""
import json
from uuid import uuid5, NAMESPACE_URL

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from alerting.models import AlertPresetChoice
from alerting.policies import DynamicSchedulePolicy
from alerting.preset_policies import PatientPresetAssignment, PresetDirectoryView
from assessment.configuration_proofs import configuration_proof
from assessment.provenance import entity_reference
from assessment.qualified_facts import QualificationRejected, qualified_configuration
from authorization.policy import evaluate, check_principal_session, matching_case_grants, Denied
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from configurations.evidence import artifact_verification_scope
from family_content.models import EnvironmentGate, ReleaseComponent
from family_content.qualification import release_qualification_reason
from identity.models import Patient, Principal
from relations.models import Relation

PURPOSE = 'family_observation'
SCHEMA = 'patient-alert-presets-1'


def scope_id(patient_id, environment, purpose):
    return uuid5(NAMESPACE_URL, f'sjia:alert-preset:{patient_id}:{environment}:{purpose}')


def lock_scope(actor, patient_id, auth_epoch, audience, *, environment=None):
    environment = environment or settings.SJIA_ENVIRONMENT
    gate = EnvironmentGate.objects.select_for_update().get(environment=environment)
    actor = Principal.objects.select_for_update().get(id=actor.id)
    patient = Patient.objects.select_for_update().get(id=patient_id)
    if audience != 'family' or patient.status != 'active':
        raise QualificationRejected('PRESET_FAMILY_SCOPE_UNAVAILABLE')
    check_principal_session(actor, auth_epoch)
    decision = evaluate(principal=actor, patient=patient, action='select_alert_preset', purpose=PURPOSE,
        environment=environment, session_auth_epoch=auth_epoch, require_relation=True, whole_resource=True)
    grant = decision.grant
    relation = Relation.objects.select_for_update().get(id=grant.relation_id, patient=patient, guardian=actor)
    context = {'schema_version': 'preset-selection-authorization-1', 'actor_id': str(actor.id),
        'auth_epoch': str(actor.auth_epoch), 'patient_id': str(patient.id), 'environment': environment,
        'purpose': PURPOSE, 'audience': audience, 'grant': entity_reference(grant),
        'relation': entity_reference(relation), 'gate_version': str(gate.version)}
    return gate, actor, patient, context


def assignment_rows(patient_id, environment, purpose, *, enabled_only=True, lock=True):
    rows = ConfigVersion.objects.select_for_update() if lock else ConfigVersion.objects
    rows = rows.filter(kind='provider', schema_version=SCHEMA,
        payload__patient_id=str(patient_id), payload__environment=environment, payload__purpose=purpose).order_by('id')
    return list(rows.filter(status='enabled') if enabled_only else rows.filter(status__in=['enabled', 'disabled']))


def approved_assignment(patient_id, environment, purpose, *, lock=True):
    rows = assignment_rows(patient_id, environment, purpose, lock=lock)
    if len(rows) != 1:
        raise QualificationRejected('PRESET_ASSIGNMENT_UNAVAILABLE')
    now = timezone.now()
    config = qualified_configuration(rows[0].id, kind='provider', schema_version=SCHEMA,
        environment=environment, known_at=now, mode='current', lock=lock)
    for audience in ('family', 'support'):
        reason = configuration_qualification_reason(config, environment, now, audience=audience)
        if reason:
            raise QualificationRejected(reason)
    policy = PatientPresetAssignment.model_validate_json(json.dumps(config.payload))
    return config, policy, configuration_proof(config, environment, now, 'current', lock=lock)


def approved_option(assignment, option, *, verify_inputs=True, lock=True):
    """Validate the entire professionally configured schedule, not just its label."""
    environment, now = assignment.environment, timezone.now()
    schedule = qualified_configuration(option.schedule_config_id, kind='provider', schema_version='dynamic-schedule-2',
        environment=environment, known_at=now, mode='current', lock=lock)
    plan = DynamicSchedulePolicy.model_validate_json(json.dumps(schedule.payload))
    if (plan.patient_id != assignment.patient_id or plan.environment != environment or plan.purpose != assignment.purpose):
        raise QualificationRejected('PRESET_SCHEDULE_SCOPE_INVALID')
    trigger = qualified_configuration(plan.policy_id, kind='rule', schema_version='alert-trigger-2',
        environment=environment, known_at=now, mode='current', lock=lock)
    gates = EnvironmentGate.objects.select_for_update() if lock else EnvironmentGate.objects
    gate = gates.get(environment=environment)
    if gate.active_release_id is None:
        raise QualificationRejected('PRESET_PUBLICATION_UNAVAILABLE')
    reason = release_qualification_reason(gate.active_release)
    if reason:
        raise QualificationRejected(reason)
    if not ReleaseComponent.objects.filter(release_id=gate.active_release_id, component_kind='rule', config=trigger).exists():
        raise QualificationRejected('PRESET_PUBLICATION_BINDING_REQUIRED')
    if verify_inputs:
        if not lock:
            raise ValueError('Input capture requires the mutation lock path.')
        from alerting.triggers import check_schedule_inputs
        actor = Principal.objects.select_for_update().get(id=plan.actor_id)
        check_schedule_inputs(schedule, plan, actor, now.isoformat())
    return schedule, plan, trigger


def latest_choice(patient_id, environment, purpose, *, lock=True):
    rows = AlertPresetChoice.objects.select_for_update() if lock else AlertPresetChoice.objects
    return rows.filter(patient_id=patient_id, environment=environment,
        purpose=purpose).order_by('-version').first()


def choice_is_current(choice, assignment, policy):
    if choice is None:
        return True
    if choice.assignment_id != assignment.id or choice.schedule_id not in {item.schedule_config_id for item in policy.options}:
        return False
    # The caller holds the environment gate and patient scope. Permission writes
    # use the same gate; do not acquire another actor lock after the worker actor.
    actor = Principal.objects.get(id=choice.selected_by_id)
    try:
        check_principal_session(actor, int(choice.authorization_context['auth_epoch']))
    except (Denied, KeyError, ValueError):
        return False
    grants = list(matching_case_grants(actor, choice.patient, 'select_alert_preset', timezone.now(),
        purpose=policy.purpose, require_relation=True, whole_resource=True))
    grant = next((item for item in grants if str(item.id) == choice.authorization_context['grant']['id']), None)
    if grant is None:
        return False
    relation = Relation.objects.get(id=grant.relation_id)
    gate = EnvironmentGate.objects.get(environment=policy.environment)
    context = {'schema_version': 'preset-selection-authorization-1', 'actor_id': str(actor.id),
        'auth_epoch': str(actor.auth_epoch), 'patient_id': str(choice.patient_id), 'environment': policy.environment,
        'purpose': policy.purpose, 'audience': 'family', 'grant': entity_reference(grant),
        'relation': entity_reference(relation), 'gate_version': str(gate.version)}
    return context == choice.authorization_context



@transaction.atomic
@artifact_verification_scope()
def directory(*, actor, patient_id, auth_epoch, audience):
    _, actor, patient, _ = lock_scope(actor, patient_id, auth_epoch, audience)
    config, policy, _ = approved_assignment(patient.id, settings.SJIA_ENVIRONMENT, PURPOSE)
    options = []
    for option in policy.options:
        schedule, _, trigger = approved_option(policy, option)
        options.append({'schedule_config_id': str(schedule.id), 'label': option.label, 'description': option.description,
            'preset_key': trigger.payload['sensitivity_preset']['preset_key'], 'config_version': str(schedule.version)})
    choice = latest_choice(patient.id, settings.SJIA_ENVIRONMENT, PURPOSE)
    current = choice_is_current(choice, config, policy)
    selected = (choice.schedule_id if choice else policy.default_schedule_config_id) if current else None
    view = {'schema_version': 'patient-alert-presets-view-1', 'patient_id': str(patient.id),
        'assignment_id': str(config.id), 'assignment_checksum': config.checksum, 'assignment_version': str(config.version),
        'display_name': policy.display_name, 'purpose': policy.purpose, 'selection_version': str(choice.version if choice else 0),
        'selected_schedule_config_id': str(selected) if selected else None, 'selection_state':
            ('selected' if choice else 'professional_default') if current else 'requires_reselection',
        'options': options}
    return PresetDirectoryView.model_validate_json(json.dumps(view)).model_dump(mode='json')


def schedule_binding(schedule, plan, *, verify_inputs=False, lock=True):
    """Called during admission and execution. Disabled assignments cannot revive old schedules."""
    historical = assignment_rows(plan.patient_id, plan.environment, plan.purpose, enabled_only=False, lock=lock)
    managed_ids = {option['schedule_config_id'] for row in historical for option in row.payload.get('options', [])}
    if str(schedule.id) not in managed_ids:
        return None
    config, policy, proof = approved_assignment(plan.patient_id, plan.environment, plan.purpose, lock=lock)
    choice = latest_choice(plan.patient_id, plan.environment, plan.purpose, lock=lock)
    if not choice_is_current(choice, config, policy):
        raise QualificationRejected('PRESET_SELECTION_STALE')
    selected = choice.schedule_id if choice else policy.default_schedule_config_id
    if selected != schedule.id:
        raise QualificationRejected('PRESET_SCHEDULE_NOT_SELECTED')
    option = next((item for item in policy.options if item.schedule_config_id == selected), None)
    if option is None:
        raise QualificationRejected('PRESET_SELECTION_STALE')
    current_schedule, _, _ = approved_option(policy, option, verify_inputs=verify_inputs, lock=lock)
    if entity_reference(current_schedule) != entity_reference(schedule):
        raise QualificationRejected('PRESET_SCHEDULE_CHANGED')
    schedule_proof = configuration_proof(current_schedule, plan.environment, timezone.now(), 'current', lock=lock)
    return {'schema_version': 'patient-alert-preset-binding-1', 'assignment': proof,
        'schedule': schedule_proof, 'choice': entity_reference(choice) if choice else None, 'selection_version': str(choice.version if choice else 0),
        'scope_id': str(scope_id(plan.patient_id, plan.environment, plan.purpose)),
        'schedule_id': str(schedule.id)}


def evaluation_binding(inputs, payload):
    """A manual current evaluation cannot bypass a patient's selected plan.

    Replay remains a support-only historical query with publication disabled.
    Unmanaged legacy rules retain their existing behavior.
    """
    if payload['mode'] != 'current':
        return None
    historical = assignment_rows(inputs.patient.id, inputs.context['environment'], inputs.context['purpose'], enabled_only=False)
    ids = {option['schedule_config_id'] for assignment in historical for option in assignment.payload.get('options', [])}
    schedules = list(ConfigVersion.objects.filter(id__in=ids, schema_version='dynamic-schedule-2',
        payload__policy_id=payload['policy_id']).order_by('id'))
    if not schedules:
        return None
    accepted = []
    for schedule in schedules:
        plan = DynamicSchedulePolicy.model_validate_json(json.dumps(schedule.payload))
        try:
            binding = schedule_binding(schedule, plan)
        except QualificationRejected:
            continue
        keys = ('policy_id', 'input_rule_id', 'window_policy_id', 'verification_policy_id', 'use_authorization_id')
        if (plan.actor_id != inputs.actor.id or any(str(getattr(plan, key)) != payload[key] for key in keys)
                or (str(plan.observation_period_id) if plan.observation_period_id else None) != payload['observation_period_id']
                or payload.get('clinical_selections', [])):
            continue
        accepted.append(binding)
    if len(accepted) != 1:
        raise QualificationRejected('PRESET_EVALUATION_SCOPE_INVALID')
    return accepted[0]


def intent_ttl_seconds(environment):
    gate = EnvironmentGate.objects.get(environment=environment)
    runtime = ConfigVersion.objects.select_for_update().filter(id=gate.runtime_config_id,
        kind='provider', schema_version='backend-runtime-1').first()
    if runtime is None:
        raise QualificationRejected('PRESET_RUNTIME_UNAVAILABLE')
    reason = configuration_qualification_reason(runtime, environment, audience='support')
    if reason:
        raise QualificationRejected(reason)
    return runtime.payload['api']['intent_ttl_seconds']


@transaction.atomic
@artifact_verification_scope()
def publication_binding_reason(evaluation):
    """Recheck choice provenance on every family read, including time boundaries.

    Revoking the selecting guardian need not change any clinical epoch or notify
    a different guardian. Old published content must still stop being eligible.
    """
    from django.core.exceptions import ObjectDoesNotExist
    from family_content.gate_locks import read_environment_gate_shared
    from tasks.models import Task
    from alerting.service import checked_task
    read_environment_gate_shared(evaluation.environment)
    # A family read may already hold this shared gate. Every helper below must
    # stay read-only: upgrading to UPDATE would deadlock concurrent readers.
    rows = ConfigVersion.objects.filter(kind='provider', schema_version=SCHEMA, status__in=['enabled', 'disabled'],
        payload__patient_id=str(evaluation.patient_id), payload__environment=evaluation.environment)
    ids = {item['schedule_config_id'] for row in rows for item in row.payload.get('options', [])}
    schedules = ConfigVersion.objects.filter(id__in=ids, schema_version='dynamic-schedule-2',
        payload__policy_id=str(evaluation.policy_id))
    task = Task.objects.filter(id=evaluation.task_id, patient_id=evaluation.patient_id).first()
    captured = (task.input_refs.get('snapshot', {}).get('preset_selection')
        if task is not None and isinstance(task.input_refs, dict) else None)
    if not schedules.exists() and captured is None:
        return None
    try:
        if task is None or task.schema_version != 'dynamic-evaluation-task-2' or task.status != 'completed':
            return 'PRESET_PUBLICATION_CHANGED'
        checked_task(task)
        frozen = task.input_refs['snapshot']['preset_selection']
        schedule = schedules.get(id=frozen['schedule_id'])
        policy = DynamicSchedulePolicy.model_validate_json(json.dumps(schedule.payload))
        if (policy.actor_id != task.actor_id or policy.purpose != task.input_refs['authorization_context']['purpose']
                or schedule_binding(schedule, policy, lock=False) != frozen):
            return 'PRESET_PUBLICATION_CHANGED'
    except (QualificationRejected, ObjectDoesNotExist, KeyError, TypeError, ValueError, Denied):
        return 'PRESET_PUBLICATION_CHANGED'
    return None
