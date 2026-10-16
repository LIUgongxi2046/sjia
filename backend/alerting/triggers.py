import json
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import connection, transaction
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from alerting.policies import DynamicSchedulePolicy
from family_content.gate_locks import read_environment_gate_shared
from assessment.configuration_proofs import configuration_proof
from assessment.provenance import entity_reference
from assessment.qualified_facts import QualificationRejected, qualified_configuration, lock_case_permissions
from assessment.service import lock_actor, TERMINAL, TIMESTAMP
from config.celery import app
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.evidence import inspect_approved_evidence
from configurations.service import compute_checksum
from authorization.policy import matching_case_grants
from identity.models import Patient, Principal
from tasks.models import ConsumerReceipt, OutboxEvent, Task, TaskAttempt
from tasks.outbox import emit_event, record_receipt
from tasks.service import register_task, input_digest, create_attempt, finish_attempt


SOURCE_TYPES = ('verification.changed', 'configuration.changed', 'clinical.context_changed', 'dynamic.timer')
TRIGGER_SCHEMA = 'dynamic-trigger-task-2'


class DynamicTriggerQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    cursor: str | None = Field(default=None, min_length=1, max_length=65536)


class TriggerCursor(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    snapshot_version: str = Field(min_length=1)
    offset: int = Field(ge=0)
    expires_at: float


def emit_verification_change(patient, resource, operation, environment, purpose):
    return emit_event(event_type='verification.changed', schema_version='clinical-change-2',
        aggregate_id=patient.id, aggregate_version=patient.verification_epoch, correlation_id=resource.id,
        resource_refs={'patient_id': str(patient.id), 'environment': environment, 'purpose': purpose,
            'resource': entity_reference(resource), 'operation': operation})


def emit_configuration_change(config, gate, operation):
    return emit_event(event_type='configuration.changed', schema_version='clinical-change-2',
        aggregate_id=config.id, aggregate_version=gate.publication_epoch, correlation_id=config.id,
        resource_refs={'environment': gate.environment, 'config_id': str(config.id),
            'resource': entity_reference(config), 'operation': operation})


def source_matches(event, config, policy):
    refs = event.resource_refs
    if event.event_type not in policy.event_types or refs['environment'] != policy.environment:
        return False
    if event.event_type == 'configuration.changed':
        return refs['config_id'] in {str(item) for item in policy.watch_config_ids} | {str(config.id)}
    if refs['patient_id'] != str(policy.patient_id):
        return False
    if event.event_type == 'verification.changed':
        return refs['purpose'] == policy.purpose
    return event.event_type != 'dynamic.timer' or refs['schedule_id'] == str(config.id)


@transaction.atomic
def fanout_dynamic_change(event):
    from alerting.presets import schedule_binding
    read_environment_gate_shared(event.resource_refs['environment'])
    source = OutboxEvent.objects.select_for_update().get(id=event.id)
    if source.event_type not in SOURCE_TYPES or source.schema_version != 'clinical-change-2':
        raise QualificationRejected('DYNAMIC_TRIGGER_SOURCE_INVALID')
    if ConsumerReceipt.objects.filter(consumer='dynamic_trigger_fanout', event=source).exists():
        return
    schedules = ConfigVersion.objects.filter(kind='provider', schema_version='dynamic-schedule-2',
        status='enabled', payload__environment=source.resource_refs['environment']).order_by('id')
    for config in schedules:
        policy = DynamicSchedulePolicy.model_validate_json(json.dumps(config.payload))
        if not source_matches(source, config, policy):
            continue
        try:
            binding = schedule_binding(config, policy)
        except QualificationRejected:
            continue  # Ineligible or stale plans never create a new clinical task.
        patient = Patient.objects.get(id=policy.patient_id)
        actor = Principal.objects.get(id=policy.actor_id)
        task = register_task(actor=actor, patient=patient, task_type='reconcile', schema_version=TRIGGER_SCHEMA,
            input_refs={'schedule': entity_reference(config), 'source': source_reference(source),
                **({'preset_selection': binding} if binding is not None else {})},
            deadline_seconds=policy.trigger_deadline_seconds)
        emit_event(event_type='alert_evaluation.trigger_requested', schema_version=TRIGGER_SCHEMA,
            aggregate_id=task.id, aggregate_version=1, correlation_id=source.correlation_id, causation_id=source.id,
            resource_refs={'task_id': str(task.id)}, task=task)
    record_receipt('dynamic_trigger_fanout', source, source.id)


def trigger_scope(reference, *, patient, actor, payload, environment, purpose):
    known_at = TIMESTAMP.validate_python(payload['known_at'])
    config = qualified_configuration(reference['schedule']['id'], kind='provider', schema_version='dynamic-schedule-2',
        environment=environment, known_at=known_at, mode='current')
    if entity_reference(config) != reference['schedule']:
        raise QualificationRejected('DYNAMIC_TRIGGER_CONFIGURATION_CHANGED')
    policy = DynamicSchedulePolicy.model_validate_json(json.dumps(config.payload))
    source = OutboxEvent.objects.get(id=reference['source']['id'])
    parent = Task.objects.get(id=reference['trigger_task_id'], patient_id=patient.id, actor_id=actor.id,
        schema_version=TRIGGER_SCHEMA, task_type='reconcile')
    expected_refs = {'schedule': reference['schedule'], 'source': reference['source']}
    from alerting.presets import schedule_binding
    binding = schedule_binding(config, policy)
    if binding is not None:
        expected_refs['preset_selection'] = binding
    if reference.get('preset_selection') != binding:
        raise QualificationRejected('PRESET_SELECTION_CHANGED')
    if (parent.input_sha256 != input_digest(parent.input_refs) or parent.input_refs != expected_refs
            or parent.status not in ('running', 'completed')):
        raise QualificationRejected('DYNAMIC_TRIGGER_INPUT_CHANGED')
    # 投递租约属于运行状态；来源摘要只固定事件的业务身份。
    if source_reference(source) != reference['source']:
        raise QualificationRejected('DYNAMIC_TRIGGER_SOURCE_CHANGED')
    expected = schedule_payload(policy, payload['assessment_at'])
    if (policy.patient_id != patient.id or policy.actor_id != actor.id or policy.environment != environment
            or policy.purpose != purpose or payload != expected or not source_matches(source, config, policy)
            or source.schema_version != 'clinical-change-2' or source.event_type not in SOURCE_TYPES
            or source.occurred_at > known_at):
        raise QualificationRejected('DYNAMIC_TRIGGER_SCOPE_INVALID')
    return configuration_proof(config, environment, known_at, 'current')


def source_reference(event):
    value = {'event_type': event.event_type, 'schema_version': event.schema_version, 'aggregate_id': str(event.aggregate_id),
        'aggregate_version': event.aggregate_version, 'occurred_at': event.occurred_at.isoformat(),
        'causation_id': str(event.causation_id) if event.causation_id else None, 'correlation_id': str(event.correlation_id),
        'resource_refs': event.resource_refs}
    return {'model': 'tasks.outboxevent', 'id': str(event.id), 'sha256': compute_checksum(value)}


def schedule_payload(policy, assessment_at):
    return {**{name: str(getattr(policy, name)) for name in ('policy_id', 'input_rule_id', 'window_policy_id',
        'verification_policy_id', 'use_authorization_id')}, 'observation_period_id':
        str(policy.observation_period_id) if policy.observation_period_id else None,
        'assessment_at': assessment_at, 'known_at': assessment_at, 'mode': 'current', 'clinical_selections': []}


def static_payload(plan, policy, assessment_at):
    return {**plan.model_dump(mode='json', exclude_none=True), 'mode': 'current', 'assessment_at': assessment_at, 'known_at': assessment_at,
        'observation_period_id': str(policy.observation_period_id) if policy.observation_period_id else None,
        'clinical_selections': []}


def check_schedule_inputs(config, policy, actor, assessment_at):
    from alerting.inputs import collect_dynamic_inputs
    from assessment.service import prepare_assessment_scope

    # Eligibility of an approved option is distinct from selecting/admitting it.
    # Collect qualified facts/configurations without creating a task or applying
    # the currently selected preset; task admission applies that guard separately.
    inputs = collect_dynamic_inputs(patient_id=policy.patient_id, actor=actor, auth_epoch=actor.auth_epoch,
        environment=policy.environment, payload=schedule_payload(policy, assessment_at))
    if policy.purpose != inputs.context['purpose']:
        raise QualificationRejected('DYNAMIC_TRIGGER_SCOPE_INVALID')
    sets = [inputs.policy.payload['conditions'], *[value['conditions'] for value in inputs.snapshot['lifecycle'].values()]]
    from alerting.inputs import STATIC_SIGNAL_KINDS
    required = {(condition['assessment_rule_id'], STATIC_SIGNAL_KINDS[condition['kind']])
        for conditions in sets for condition in conditions if condition['kind'] in STATIC_SIGNAL_KINDS}
    configured = {(str(item.rule_id), item.kind) for item in policy.static_assessments}
    if not required <= configured:
        raise QualificationRejected('DYNAMIC_TRIGGER_STATIC_PLAN_REQUIRED')
    references = {row.id for row in inputs.configuration_rows}
    if policy.static_assessments:
        lock_actor(policy.patient_id, actor, actor.auth_epoch, policy.environment, 'read_assessment')
        lock_case_permissions(actor, inputs.patient, 'read_assessment', policy.purpose, timezone.now(), whole_resource=True)
    for plan in policy.static_assessments:
        _, _, _, _, snapshot, context = prepare_assessment_scope(patient_id=policy.patient_id, actor=actor,
            auth_epoch=actor.auth_epoch, environment=policy.environment, payload=static_payload(plan, policy, assessment_at))
        if context['purpose'] != policy.purpose:
            raise QualificationRejected('DYNAMIC_TRIGGER_SCOPE_INVALID')
        references.update(row['config']['id'] for row in snapshot['configurations'])
    if 'configuration.changed' in policy.event_types and not {str(value) for value in references} <= {str(value) for value in policy.watch_config_ids}:
        raise QualificationRejected('DYNAMIC_TRIGGER_CONFIGURATION_RANGE_REQUIRED')


@transaction.atomic
def emit_static_completion(task_id):
    task = Task.objects.select_for_update().get(id=task_id)
    parent_id = task.input_refs.get('dynamic_trigger_task_id')
    if parent_id is None or task.status not in TERMINAL:
        return
    if OutboxEvent.objects.filter(event_type='alert_evaluation.static_finished', aggregate_id=task.id,
            aggregate_version=task.status_version).exists():
        return
    parent = Task.objects.get(id=parent_id, patient_id=task.patient_id, schema_version=TRIGGER_SCHEMA)
    emit_event(event_type='alert_evaluation.static_finished', schema_version=TRIGGER_SCHEMA, aggregate_id=task.id,
        aggregate_version=task.status_version, correlation_id=parent.id, resource_refs={'assessment_task_id': str(task.id),
            'input_sha256': task.input_sha256}, task=parent)


@transaction.atomic
def execute_dynamic_trigger(event, owner):
    from alerting.service import create_dynamic_task
    from assessment.service import create_assessment_task

    source = OutboxEvent.objects.get(id=event.id)
    continuing = source.event_type == 'alert_evaluation.static_finished'
    if source.event_type not in ('alert_evaluation.trigger_requested', 'alert_evaluation.static_finished') or source.schema_version != TRIGGER_SCHEMA:
        raise QualificationRejected('DYNAMIC_TRIGGER_SOURCE_INVALID')
    if ConsumerReceipt.objects.filter(consumer='dynamic_trigger', event=source).exists():
        return
    initial = Task.objects.get(id=source.task_id, schema_version=TRIGGER_SCHEMA)
    config = ConfigVersion.objects.get(id=initial.input_refs['schedule']['id'])
    policy = DynamicSchedulePolicy.model_validate_json(json.dumps(config.payload))
    actor = Principal.objects.get(id=initial.actor_id)
    _, _, _, _, runtime = lock_actor(initial.patient_id, actor, actor.auth_epoch, policy.environment, 'execute_assessment')
    with connection.cursor() as cursor:
        cursor.execute('SELECT set_config(%s, %s, true)', ['lock_timeout', f"{runtime.payload['api']['lock_timeout_ms']}ms"])
        cursor.execute('SELECT set_config(%s, %s, true)', ['statement_timeout', f"{runtime.payload['api']['statement_timeout_ms']}ms"])
    task = Task.objects.select_for_update().get(id=initial.id, schema_version=TRIGGER_SCHEMA)
    source = OutboxEvent.objects.select_for_update().get(id=source.id)
    if ConsumerReceipt.objects.filter(consumer='dynamic_trigger', event=source).exists():
        return
    if input_digest(task.input_refs) != task.input_sha256 or task.task_type != 'reconcile':
        raise QualificationRejected('DYNAMIC_TRIGGER_INPUT_CHANGED')
    if task.status in TERMINAL:
        record_receipt('dynamic_trigger', source, task.id)
        return
    if task.deadline_at <= timezone.now():
        raise QualificationRejected('DYNAMIC_TRIGGER_DEADLINE_EXCEEDED')
    if continuing:
        if task.status != 'awaiting_confirmation':
            raise QualificationRejected('DYNAMIC_TRIGGER_STAGE_INVALID')
        attempt = TaskAttempt.objects.select_for_update().get(task=task, lease_generation=task.lease_generation, status='running')
        evidence = attempt.usage_evidence
        if evidence['schema_version'] != 'dynamic-trigger-wait-2':
            raise QualificationRejected('DYNAMIC_TRIGGER_STAGE_INVALID')
        references = {item['task_id']: item['input_sha256'] for item in evidence['static_tasks']}
        if references.get(source.resource_refs['assessment_task_id']) != source.resource_refs['input_sha256']:
            raise QualificationRejected('DYNAMIC_TRIGGER_SOURCE_INVALID')
        children = list(Task.objects.filter(id__in=references, patient_id=task.patient_id,
            actor_id=task.actor_id, schema_version='static-assessment-task-2'))
        if len(children) != len(references) or any(child.input_sha256 != references[str(child.id)]
                or child.input_refs['dynamic_trigger_task_id'] != str(task.id) for child in children):
            raise QualificationRejected('DYNAMIC_TRIGGER_STATIC_TASK_CHANGED')
        if any(child.status not in TERMINAL for child in children):
            record_receipt('dynamic_trigger', source, task.id)
            return
        assessment_at = evidence['assessment_at']
        static_tasks = evidence['static_tasks']
    else:
        assessment_at, static_tasks = timezone.now().isoformat(), []
        if task.status not in ('accepted', 'queued'):
            raise QualificationRejected('DYNAMIC_TRIGGER_STAGE_INVALID')
    task.status, task.lease_owner, task.lease_until = 'running', owner, task.deadline_at
    if not continuing:
        task.lease_generation += 1
    task.status_version += 1
    task.save(update_fields=['status', 'lease_owner', 'lease_until', 'lease_generation', 'status_version'])
    if not continuing:
        attempt = create_attempt(task, task.lease_generation)
    payload = schedule_payload(policy, assessment_at)
    reference = {'schedule': task.input_refs['schedule'], 'source': task.input_refs['source'], 'trigger_task_id': str(task.id),
        **({'preset_selection': task.input_refs['preset_selection']} if 'preset_selection' in task.input_refs else {})}
    if not continuing and policy.static_assessments:
        trigger_scope(reference, patient=task.patient, actor=actor, payload=payload,
            environment=policy.environment, purpose=policy.purpose)
        check_schedule_inputs(config, policy, actor, assessment_at)
        for plan in policy.static_assessments:
            child, _ = create_assessment_task(patient_id=task.patient_id, actor=actor, auth_epoch=actor.auth_epoch,
                environment=policy.environment, payload=static_payload(plan, policy, assessment_at))
            child.input_refs['dynamic_trigger_task_id'] = str(task.id)
            child.input_sha256 = input_digest(child.input_refs)
            child.save(update_fields=['input_refs', 'input_sha256'])
            static_tasks.append({'task_id': str(child.id), 'input_sha256': child.input_sha256})
        attempt.usage_evidence = {'schema_version': 'dynamic-trigger-wait-2', 'assessment_at': assessment_at, 'static_tasks': static_tasks}
        attempt.save(update_fields=['usage_evidence'])
        task.status, task.status_version = 'awaiting_confirmation', task.status_version + 1
        task.save(update_fields=['status', 'status_version'])
        record_receipt('dynamic_trigger', source, task.id)
        return
    evaluation_task, _ = create_dynamic_task(patient_id=task.patient_id, actor=actor, auth_epoch=actor.auth_epoch,
        environment=policy.environment, payload=payload, automatic_trigger=reference)
    if task.deadline_at <= timezone.now():
        raise QualificationRejected('DYNAMIC_TRIGGER_DEADLINE_EXCEEDED')
    task.status, task.status_version, task.lease_until = 'completed', task.status_version + 1, timezone.now()
    task.save(update_fields=['status', 'status_version', 'lease_until'])
    finish_attempt(attempt, 'completed', usage_evidence={'schema_version': 'dynamic-trigger-execution-2',
        'evaluation_task_id': str(evaluation_task.id), 'input_sha256': evaluation_task.input_sha256, 'static_tasks': static_tasks})
    record_receipt('dynamic_trigger', source, evaluation_task.id)


@app.task(name='alerting.triggers.schedule_due')
@transaction.atomic
def schedule_due():
    now = timezone.now()
    schedules = ConfigVersion.objects.select_for_update(skip_locked=True).filter(kind='provider',
        schema_version='dynamic-schedule-2', status='enabled', payload__environment=settings.SJIA_ENVIRONMENT).order_by('id')
    count = 0
    for config in schedules:
        policy = DynamicSchedulePolicy.model_validate_json(json.dumps(config.payload))
        if 'dynamic.timer' not in policy.event_types or now < policy.starts_at:
            continue
        reason = configuration_qualification_reason(config, policy.environment, now=now, audience='support')
        evidence = [inspect_approved_evidence(item, purpose='clinical_policy', lock=True, now=now)[2]
            for item in policy.evidence_refs]
        if reason or any(evidence):
            continue
        slot = (now - policy.starts_at) // timedelta(seconds=policy.interval_seconds)
        if slot >= 9223372036854775807:
            raise QualificationRejected('DYNAMIC_TRIGGER_VERSION_EXHAUSTED')
        if OutboxEvent.objects.filter(event_type='dynamic.timer', aggregate_id=config.id, aggregate_version=slot + 1).exists():
            continue
        emit_event(event_type='dynamic.timer', schema_version='clinical-change-2', aggregate_id=config.id,
            aggregate_version=slot + 1, correlation_id=config.id, resource_refs={'environment': policy.environment,
                'patient_id': str(policy.patient_id), 'schedule_id': str(config.id), 'scheduled_at':
                (policy.starts_at + timedelta(seconds=slot * policy.interval_seconds)).isoformat()})
        count += 1
    return count


@transaction.atomic
def read_dynamic_triggers(patient_id, actor, auth_epoch, environment, query, expires_at):
    gate, patient, actor, _, runtime = lock_actor(patient_id, actor, auth_epoch, environment, 'read_assessment')
    now = timezone.now()
    purposes = {grant.purpose for grant in matching_case_grants(actor, patient, 'read_assessment', now, whole_resource=True)
        if grant.evidence_ref.strip()}
    if not purposes:
        raise QualificationRejected('ASSESSMENT_FIELD_SCOPE_DENIED')
    grants = [grant for purpose in sorted(purposes)
        for grant in lock_case_permissions(actor, patient, 'read_assessment', purpose, now, whole_resource=True)]
    schedule_ids = [str(value) for value in ConfigVersion.objects.filter(schema_version='dynamic-schedule-2',
        payload__environment=environment, payload__purpose__in=purposes).values_list('id', flat=True)]
    rows = list(Task.objects.filter(patient=patient, schema_version=TRIGGER_SCHEMA,
        input_refs__schedule__id__in=schedule_ids).order_by('-created_at', '-id'))
    bound = {'patient_id': str(patient.id), 'actor_id': str(actor.id), 'auth_epoch': actor.auth_epoch,
        'data_epoch': patient.data_epoch, 'verification_epoch': patient.verification_epoch,
        'publication_epoch': gate.publication_epoch, 'runtime': entity_reference(runtime),
        'grants': [entity_reference(grant) for grant in grants],
        'tasks': [{'id': str(task.id), 'input_sha256': task.input_sha256} for task in rows]}
    snapshot_version, offset = compute_checksum(bound), 0
    if expires_at <= now.timestamp():
        raise QualificationRejected('DYNAMIC_TRIGGER_SESSION_EXPIRED')
    if query['cursor'] is not None:
        cursor = TriggerCursor.model_validate_json(json.dumps(signing.loads(query['cursor'], salt='dynamic-trigger-history')))
        if cursor.snapshot_version != snapshot_version or cursor.offset >= len(rows) or not now.timestamp() < cursor.expires_at <= expires_at:
            raise QualificationRejected('DYNAMIC_TRIGGER_HISTORY_CHANGED')
        offset = cursor.offset
    maximum = runtime.payload['api']['max_page_size']
    selected = rows[offset:offset + maximum]
    result = []
    for saved in selected:
        task = Task.objects.select_for_update().get(id=saved.id)
        config = ConfigVersion.objects.get(id=task.input_refs['schedule']['id'])
        policy = DynamicSchedulePolicy.model_validate_json(json.dumps(config.payload))
        if policy.environment != environment:
            continue
        lock_case_permissions(actor, patient, 'read_assessment', policy.purpose, timezone.now(), whole_resource=True)
        if input_digest(task.input_refs) != task.input_sha256:
            raise QualificationRejected('DYNAMIC_TRIGGER_INPUT_CHANGED')
        if task.status not in TERMINAL and task.deadline_at <= timezone.now():
            from alerting.service import expire_task

            expire_task(task, 'DYNAMIC_TRIGGER_DEADLINE_EXCEEDED')
        attempt = TaskAttempt.objects.filter(task=task).order_by('-attempt_number').first()
        effect = attempt.usage_evidence if attempt and attempt.status == 'completed' else None
        event = OutboxEvent.objects.get(id=task.input_refs['source']['id'])
        if source_reference(event) != task.input_refs['source']:
            raise QualificationRejected('DYNAMIC_TRIGGER_SOURCE_CHANGED')
        if effect is not None:
            saved = Task.objects.get(id=effect['evaluation_task_id'], patient=patient,
                schema_version='dynamic-evaluation-task-2')
            if saved.input_sha256 != effect['input_sha256'] or saved.input_refs['snapshot']['automatic_trigger']['trigger_task_id'] != str(task.id):
                raise QualificationRejected('DYNAMIC_TRIGGER_RESULT_CHANGED')
        result.append({'task_id': str(task.id), 'status': task.status, 'status_version': str(task.status_version),
            'created_at': task.created_at.isoformat(), 'error_code': attempt.error_code if attempt else None,
            'schedule_id': str(config.id), 'source_id': str(event.id), 'event_type': event.event_type,
            'occurred_at': event.occurred_at.isoformat(), 'evaluation_task_id': effect['evaluation_task_id'] if effect else None})
        if len(json.dumps(result, separators=(',', ':')).encode()) > runtime.payload['api']['max_json_bytes']:
            raise QualificationRejected('DYNAMIC_TRIGGER_HISTORY_LIMIT_EXCEEDED')
    following = offset + len(result)
    next_cursor = signing.dumps({'snapshot_version': snapshot_version, 'offset': following, 'expires_at': expires_at},
        salt='dynamic-trigger-history') if following < len(rows) else None
    return {'patient_id': str(patient.id), 'items': result, 'next_cursor': next_cursor, 'snapshot_version': snapshot_version}
