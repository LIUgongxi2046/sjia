from uuid import UUID
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from alerting.models import AlertDecision, AlertEpisode, AlertEvent, AlertEvaluation
from assessment.qualified_facts import QualificationRejected
from assessment.service import TIMESTAMP
from dependencies.models import DependencyNode
from family_content.models import EnvironmentGate, ReleaseBundle, ReleaseComponent
from family_content.gate_locks import read_environment_gate_shared
from family_content.qualification import release_qualification_reason
from family_content.service import lock_release_dependencies
from files.purpose_dependencies import connect, resource_node
from tasks.outbox import emit_event
from tasks.models import Task


def lifecycle_condition(snapshot, name, signals):
    policy = snapshot['lifecycle'][name]
    if snapshot['combinations'][name]['state'] != 'met':
        return False
    groups = {tuple(group['path']): group for group in snapshot['combinations'][name]['groups']}
    selected = {name + ':' + reference for path, group in groups.items() if group['state'] == 'met'
        and all(groups[path[:index]]['state'] == 'met' for index in range(len(path))) for reference in group['signal_refs']}
    inputs = [item for signal in signals if signal['signal_id'] in selected and signal['state'] == 'met'
        for item in signal['inputs'] if item['input_role'] != 'context']
    samples = {(item['reference']['medical_interval']['lower'], item['reference']['medical_interval']['upper'],
        item['reference']['medical_interval']['upper_inclusive']): item['reference']['medical_interval'] for item in inputs}
    if len(samples) < policy['minimum_sample_count']:
        return False
    earliest_end = min(TIMESTAMP.validate_python(item['upper']) for item in samples.values())
    latest_start = max(TIMESTAMP.validate_python(item['lower']) for item in samples.values())
    return latest_start - earliest_end >= timedelta(seconds=policy['minimum_duration_seconds'])


def publication_reason(inputs, gate):
    if gate.active_release_id is None:
        return None, 'DYNAMIC_RELEASE_UNAVAILABLE'
    release = ReleaseBundle.objects.select_for_update().get(id=gate.active_release_id)
    lock_release_dependencies(release, gate, read_only=True)
    reason = release_qualification_reason(release)
    if reason:
        return None, reason
    components = {}
    for item in ReleaseComponent.objects.filter(release=release):
        components.setdefault(item.component_kind, set()).add(item.config_id)
    publishing = inputs.policy.payload['publication']
    required = {'rule': inputs.policy.id, 'window': inputs.window.id,
        'verification': UUID(inputs.context['verification_policy_id'])}
    required.update({name: UUID(publishing[name + '_config_id']) for name in ('visibility', 'content', 'notification')})
    mappings = {row.id for row in inputs.configuration_rows if row.kind == 'mapping'}
    if (any(components.get(name) != {identifier} for name, identifier in required.items())
            or not mappings <= components.get('mapping', set())
            or release.contact_version_id != UUID(publishing['contact_version_id'])):
        return None, 'DYNAMIC_RELEASE_REFERENCE_MISMATCH'
    return release, None


def record_decision(evaluation, rule_key, action, reason, episode_id):
    previous = AlertDecision.objects.select_for_update().filter(patient_id=evaluation.patient_id, rule_key=rule_key).order_by('-sequence').first()
    sequence = previous.sequence + 1 if previous else 1
    if sequence > 9223372036854775807:
        raise QualificationRejected('DYNAMIC_DECISION_VERSION_EXHAUSTED')
    AlertDecision.objects.create(evaluation_id=evaluation.id, patient_id=evaluation.patient_id, rule_key=rule_key,
        state=evaluation.decision, action=action, reason_codes=[] if reason is None else [reason], episode_id=episode_id,
        sequence=sequence, previous_decision_id=previous.id if previous else None)


@transaction.atomic
def adopt_lifecycle(evaluation, inputs):
    if evaluation.mode == 'replay':
        return {'state': 'replay', 'action': 'blocked', 'reason_codes': ['DYNAMIC_REPLAY_NO_PUBLICATION'], 'event_id': None, 'episode_id': None}
    family, rule_key = inputs.policy.payload['episode']['rule_family_key'], inputs.policy.payload['rule_key']
    newer = AlertEvaluation.objects.filter(patient=evaluation.patient, mode='current', policy__payload__episode__rule_family_key=family).exclude(id=evaluation.id)
    if newer.filter(Q(assessment_at__gt=evaluation.assessment_at)
            | Q(assessment_at=evaluation.assessment_at, context_sequence__gt=evaluation.context_sequence)).exists():
        record_decision(evaluation, rule_key, 'blocked', 'DYNAMIC_NEWER_CONTEXT_ALREADY_ADOPTED', None)
        return {'state': 'superseded', 'action': 'blocked', 'reason_codes': ['DYNAMIC_NEWER_CONTEXT_ALREADY_ADOPTED'], 'event_id': None, 'episode_id': None}
    pending = Task.objects.filter(patient=evaluation.patient, task_type='alert_evaluation', schema_version='dynamic-evaluation-task-2',
        input_refs__payload__mode='current', input_refs__authorization_context__rule_family_key=family).exclude(id=evaluation.task_id)
    point = inputs.context['assessment_at_utc']
    if pending.filter(Q(input_refs__authorization_context__assessment_at_utc__gt=point)
            | Q(input_refs__authorization_context__assessment_at_utc=point, input_refs__context_sequence__gt=evaluation.context_sequence)).exists():
        record_decision(evaluation, rule_key, 'blocked', 'DYNAMIC_NEWER_CONTEXT_REGISTERED', None)
        return {'state': 'superseded', 'action': 'blocked', 'reason_codes': ['DYNAMIC_NEWER_CONTEXT_REGISTERED'], 'event_id': None, 'episode_id': None}
    gate = read_environment_gate_shared(evaluation.environment)
    episodes = AlertEpisode.objects.select_for_update().filter(patient=evaluation.patient, rule_family_key=family).order_by('-last_assessment_at', '-last_context_sequence')
    episode = episodes.first()
    action, reason, event, release = 'no_event', None, None, None
    state = evaluation.decision
    reset = lifecycle_condition(inputs.snapshot, 'reset', inputs.signals)
    retrigger = lifecycle_condition(inputs.snapshot, 'retrigger', inputs.signals)
    if episode is not None and episode.status == 'open':
        previous = AlertEvaluation.objects.select_for_update().get(id=episode.last_evaluation_id, patient=evaluation.patient)
        dependency = DependencyNode.objects.select_for_update().filter(resource_type='alert_evaluation', resource_id=previous.id, resource_version='1').first()
        if previous.validity == 'invalid' or dependency is None or dependency.validity == 'invalid':
            episode.status, episode.state_version = 'invalid', episode.state_version + 1
            episode.save(update_fields=['status', 'state_version'])
            if episode.last_event_id:
                AlertEvent.objects.filter(id=episode.last_event_id, patient=evaluation.patient).update(validity='invalid')
    if state == 'unknown':
        reason = 'DYNAMIC_UNKNOWN_DOES_NOT_RESET'
    elif state == 'not_met':
        if episode is not None and episode.status == 'open' and reset:
            action, episode.status = 'close_episode', 'closed'
            episode.reset_evaluation_id = evaluation.id
        else:
            reason = 'DYNAMIC_APPROVED_RESET_NOT_MET'
    elif state == 'met':
        release, reason = publication_reason(inputs, gate)
        if reason:
            action = 'blocked'
        elif episode is None:
            action = 'create_event'
        elif episode.status != 'open':
            action = 'create_event' if retrigger else 'blocked'
            reason = None if retrigger else 'DYNAMIC_APPROVED_RETRIGGER_NOT_MET'
        else:
            previous_event = AlertEvent.objects.select_for_update().get(id=episode.last_event_id, patient=evaluation.patient)
            previous_policy = previous_event.trigger_policy
            changed = previous_policy.id != inputs.policy.id
            if changed and not inputs.snapshot['lifecycle']['retrigger']['allow_rule_version_change']:
                action, reason = 'blocked', 'DYNAMIC_RULE_VERSION_SWITCH_NOT_APPROVED'
            else:
                repeat = inputs.policy.payload['episode']
                due = repeat['repeat_policy'] == 'approved_interval' and evaluation.assessment_at - previous_event.created_at >= timedelta(seconds=repeat['repeat_interval_seconds'])
                levels = {'attention': 1, 'warning': 2, 'urgent': 3}
                escalation = (repeat['escalation_bypasses_cooldown'] and levels[inputs.policy.payload['publication']['level']]
                    > levels[previous_policy.payload['publication']['level']])
                action = 'create_event' if retrigger and (due or escalation) else 'continue_episode'
                reason = None if action == 'create_event' else 'DYNAMIC_REPEAT_CONDITIONS_NOT_MET'
    else:
        raise QualificationRejected('DYNAMIC_DECISION_INVALID')
    if action == 'create_event':
        if episode is None or episode.status != 'open':
            episode = AlertEpisode.objects.create(patient=evaluation.patient, rule_family_key=family, status='open',
                opened_at=timezone.now(), last_evaluation_id=evaluation.id, last_event_id=None, state_version=1,
                reset_evaluation_id=None, last_assessment_at=evaluation.assessment_at, last_context_sequence=evaluation.context_sequence)
        sequence = evaluation.context_sequence
        trigger_key = f'{family}:{episode.id}:{sequence}'
        event = AlertEvent.objects.create(patient=evaluation.patient, evaluation_id=evaluation.id, release=release,
            trigger_key=trigger_key, trigger_policy=inputs.policy, input_snapshot={'schema_version': 'dynamic-family-event-2',
                'evaluation_id': str(evaluation.id), 'episode_id': str(episode.id), 'input_digest': evaluation.input_digest,
                'context_sequence': str(sequence), 'publication': inputs.policy.payload['publication'],
                'data_epoch': str(evaluation.data_epoch), 'verification_epoch': str(evaluation.verification_epoch),
                'publication_epoch': str(evaluation.publication_epoch)}, validity='valid', created_at=timezone.now())
        episode.last_event_id = event.id
        connect(resource_node('alert_event', event.id, '1', evaluation.patient_id),
            resource_node('alert_evaluation', evaluation.id, '1', evaluation.patient_id), 'input')
        emit_event(event_type='alert.event_created', schema_version='dynamic-family-event-2', aggregate_id=event.id,
            aggregate_version=1, correlation_id=evaluation.task_id, resource_refs={'alert_id': str(event.id),
                'evaluation_id': str(evaluation.id), 'episode_id': str(episode.id), 'release_id': str(release.id)})
    if episode is not None and action != 'blocked':
        episode.last_evaluation_id, episode.last_assessment_at = evaluation.id, evaluation.assessment_at
        episode.last_context_sequence, episode.state_version = evaluation.context_sequence, episode.state_version + 1
        episode.save(update_fields=['status', 'reset_evaluation_id', 'last_event_id', 'last_evaluation_id', 'last_assessment_at', 'last_context_sequence', 'state_version'])
    record_decision(evaluation, rule_key, action, reason, episode.id if episode else None)
    return {'state': 'current', 'action': action, 'reason_codes': [] if reason is None else [reason],
        'event_id': str(event.id) if event else None, 'episode_id': str(episode.id) if episode else None}
