import json
from datetime import timedelta
from uuid import UUID

from django.apps import apps
from django.db import connection, transaction
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from archives.inputs import PROTOCOL, REGISTRY
from archives.models import ArchiveFactVersion, ArchiveRelationVersion
from alerting.inputs import collect_dynamic_inputs
from alerting.lifecycle import adopt_lifecycle
from alerting.models import AlertEvaluation, AlertEvaluationInput, AlertEvaluationAssessment, AlertSignalResult, AlertDecision
from assessment.provenance import entity_reference
from assessment.qualified_facts import QualificationRejected, lock_case_permissions
from assessment.service import TIMESTAMP, TERMINAL, RESOURCE_NAMES, lock_actor
from authorization.models import AccessGrant, ServiceRoleGrant
from authorization.policy import check_service_grant
from configurations.models import ConfigVersion
from configurations.evidence import artifact_verification_scope
from configurations.service import compute_checksum
from dependencies.models import DependencyNode, DependencyEdge
from files.purpose_dependencies import resource_node, connect
from tasks.lease import lease_heartbeat
from tasks.models import Task, TaskAttempt, ConsumerReceipt
from tasks.outbox import emit_event, record_receipt
from tasks.service import register_task, input_digest, acquire_lease, create_attempt, finish_attempt
from records.models import RecordField, RecordVersion
from verification.models import Verification, VerificationField


INPUT = Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/DynamicInput'}, registry=REGISTRY, format_checker=FormatChecker())
SIGNAL = Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/DynamicSignal'}, registry=REGISTRY, format_checker=FormatChecker())
OUTPUT = Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/DynamicView'}, registry=REGISTRY, format_checker=FormatChecker())
MAXIMUM_VERSION = 9223372036854775807


def dynamic_payload(payload):
    if not INPUT.is_valid(payload):
        raise QualificationRejected('DYNAMIC_INPUT_INVALID')
    return payload


@transaction.atomic
@artifact_verification_scope()
def prepare_dynamic_scope(*, patient_id, actor, auth_epoch, environment, payload, automatic_trigger=None):
    dynamic_payload(payload)
    inputs = collect_dynamic_inputs(patient_id=patient_id, actor=actor, auth_epoch=auth_epoch,
        environment=environment, payload=payload)
    from alerting.presets import evaluation_binding
    preset = evaluation_binding(inputs, payload)
    if preset is not None:
        inputs.snapshot['preset_selection'] = preset
        inputs.snapshot['configurations'].extend([preset['assignment'], preset['schedule']])
        inputs.context['snapshot_sha256'] = compute_checksum(inputs.snapshot)
    if automatic_trigger is not None:
        from alerting.triggers import trigger_scope

        proof = trigger_scope(automatic_trigger, patient=inputs.patient, actor=inputs.actor, payload=payload,
            environment=environment, purpose=inputs.context['purpose'])
        if not any(item['config']['id'] == proof['config']['id'] for item in inputs.snapshot['configurations']):
            inputs.snapshot['configurations'].append(proof)
        inputs.snapshot['automatic_trigger'] = automatic_trigger
        if automatic_trigger.get('preset_selection') != preset:
            raise QualificationRejected('PRESET_SELECTION_CHANGED')
        inputs.context['snapshot_sha256'] = compute_checksum(inputs.snapshot)
    policies = [item for item in inputs.runtime.payload['tasks'] if item['task_type'] == 'alert_evaluation']
    if len(policies) != 1:
        raise QualificationRejected('DYNAMIC_TASK_POLICY_UNAVAILABLE')
    return inputs, policies[0]


@transaction.atomic
def create_dynamic_task(*, patient_id, actor, auth_epoch, environment, payload, automatic_trigger=None):
    inputs, policy = prepare_dynamic_scope(patient_id=patient_id, actor=actor, auth_epoch=auth_epoch,
        environment=environment, payload=payload, automatic_trigger=automatic_trigger)
    patient = inputs.patient
    if patient.evaluation_sequence >= MAXIMUM_VERSION or inputs.actor.auth_epoch >= MAXIMUM_VERSION:
        raise QualificationRejected('DYNAMIC_VERSION_EXHAUSTED')
    patient.evaluation_sequence += 1
    patient.save(update_fields=['evaluation_sequence'])
    task = register_task(actor=inputs.actor, patient=patient, task_type='alert_evaluation',
        schema_version='dynamic-evaluation-task-2', input_refs={'payload': payload, 'snapshot': inputs.snapshot,
            'authorization_context': inputs.context, 'context_sequence': patient.evaluation_sequence},
        deadline_seconds=policy['queue_wait_seconds'] + policy['execution_seconds'])
    if len(json.dumps(task.input_refs, separators=(',', ':')).encode('utf-8')) > inputs.runtime.payload['api']['max_json_bytes']:
        raise QualificationRejected('DYNAMIC_CONTEXT_LIMIT_EXCEEDED')
    emit_event(event_type='alert_evaluation.requested', schema_version='dynamic-evaluation-task-2', aggregate_id=task.id,
        aggregate_version=1, correlation_id=task.id, resource_refs={'task_id': str(task.id)}, task=task)
    return task, inputs.runtime


def checked_task(task):
    sequence = task.input_refs['context_sequence']
    context = task.input_refs['authorization_context']
    if (task.task_type != 'alert_evaluation' or task.schema_version != 'dynamic-evaluation-task-2'
            or input_digest(task.input_refs) != task.input_sha256 or task.patient_id is None
            or context['patient_id'] != str(task.patient_id) or context['actor_id'] != str(task.actor_id)
            or type(sequence) is not int or not 0 < sequence <= MAXIMUM_VERSION
            or compute_checksum(task.input_refs['snapshot']) != context['snapshot_sha256']):
        raise QualificationRejected('DYNAMIC_TASK_INPUT_CHANGED')
    dynamic_payload(task.input_refs['payload'])


def dependency_reference(node, reference, patient_id, edge_type):
    row = apps.get_model(reference['model']).objects.select_for_update().get(id=reference['id'])
    if entity_reference(row) != reference:
        raise QualificationRejected('DYNAMIC_DEPENDENCY_CHANGED')
    names = {**RESOURCE_NAMES, 'sources.sourceregistry': 'source_registry', 'retention.retentionpolicy': 'retention_policy',
        'identity.principal': 'principal', 'authorization.accessgrant': 'access_grant',
        'authorization.servicerolegrant': 'service_role_grant', 'tasks.task': 'task', 'tasks.taskattempt': 'task_attempt',
        'requests.writerequest': 'write_request', 'requests.writeintent': 'write_intent', 'audit.auditevent': 'audit_event',
        'archives.observationperiodversion': 'observation_period_version', 'assessment.assessment': 'assessment'}
    resource_type = names[reference['model']]
    version = (row.object_version if resource_type == 'file_object' else str(row.version) if hasattr(row, 'version')
        else row.verified_at.isoformat() if resource_type == 'verification'
        else row.valid_from.isoformat() if resource_type in ('access_grant', 'service_role_grant')
        else row.approved_at.isoformat() if resource_type == 'data_use_authorization'
        else '1' if resource_type == 'assessment' else reference['sha256'])
    global_types = ('config_version', 'unit_mapping', 'source_revision', 'source_registry', 'retention_policy',
        'principal', 'service_role_grant', 'task_attempt')
    connect(node, resource_node(resource_type, row.id, version, None if resource_type in global_types else patient_id), edge_type)
    if resource_type == 'archive_document_version':
        connect(node, resource_node('archive_document', row.document_id, str(row.version), patient_id), 'source')


def save_dependencies(evaluation, inputs):
    node = resource_node('alert_evaluation', evaluation.id, '1', evaluation.patient_id)
    selection = inputs.snapshot.get('preset_selection')
    if selection is not None:
        connect(node, resource_node('alert_preset_scope', UUID(selection['scope_id']),
            selection['selection_version'], evaluation.patient_id), 'policy')
        if selection['choice'] is not None:
            from alerting.models import AlertPresetChoice
            choice = AlertPresetChoice.objects.select_for_update().get(id=selection['choice']['id'])
            if entity_reference(choice) != selection['choice']:
                raise QualificationRejected('PRESET_SELECTION_CHANGED')
            context = choice.authorization_context
            for reference in (context['grant'],):
                dependency_reference(node, reference, evaluation.patient_id, 'policy')
            connect(node, resource_node('relation', UUID(context['relation']['id']),
                context['relation']['sha256'], evaluation.patient_id), 'policy')
            connect(node, resource_node('principal', choice.selected_by_id, context['auth_epoch'], None), 'policy')
    trigger = inputs.snapshot.get('automatic_trigger')
    if trigger is not None:
        connect(node, resource_node('outbox_event', UUID(trigger['source']['id']), trigger['source']['sha256'], evaluation.patient_id), 'input')
        parent = Task.objects.select_for_update().get(id=trigger['trigger_task_id'], patient_id=evaluation.patient_id)
        connect(node, resource_node('task', parent.id, parent.input_sha256, evaluation.patient_id), 'input')
    for proof in inputs.snapshot['configurations']:
        row = ConfigVersion.objects.select_for_update().get(id=proof['config']['id'])
        connect(node, resource_node('config_version', row.id, str(row.version), None), 'policy')
        for kind in ('approval', 'validation'):
            reference = proof[kind]
            connect(node, resource_node('config_' + kind, UUID(reference['id']), reference['sha256'], None), 'policy')
    role = ServiceRoleGrant.objects.select_for_update().get(id=inputs.context['grant_id'])
    connect(node, resource_node('service_role_grant', role.id, role.valid_from.isoformat(), None), 'policy')
    for row in AccessGrant.objects.select_for_update().filter(id__in=inputs.context['case_grant_ids']).order_by('id'):
        connect(node, resource_node('access_grant', row.id, row.valid_from.isoformat(), evaluation.patient_id), 'policy')
    references = {item['id']: item for item in inputs.snapshot['use_references']}
    for signal in inputs.signals:
        if 'assessment_reference' in signal:
            references[signal['assessment_reference']['id']] = signal['assessment_reference']
        for item in signal['inputs']:
            for reference in item['reference']['entity_refs']:
                references[reference['id']] = reference
            for grant in AccessGrant.objects.select_for_update().filter(id__in=item['reference']['access_grant_ids']).order_by('id'):
                connect(node, resource_node('access_grant', grant.id, grant.valid_from.isoformat(), evaluation.patient_id), 'policy')
    for selected in inputs.snapshot['selected_refs'].values():
        if selected is not None:
            for reference in selected['entity_refs']:
                references[reference['id']] = reference
    for proof in inputs.snapshot['clinical_selections']:
        for reference in proof['references']:
            references[reference['id']] = reference
    period = inputs.snapshot['observation_period']
    if period is not None:
        for reference in period['references']:
            references[reference['id']] = reference
    for reference in references.values():
        dependency_reference(node, reference, evaluation.patient_id, 'input')
    consumed = {item['reference']['fact_version_id'] for signal in inputs.signals for item in signal['inputs']}
    consumed.update(value['fact_version_id'] for value in inputs.snapshot['selected_refs'].values() if value is not None)
    for reference in inputs.snapshot['relation_refs']:
        relation = ArchiveRelationVersion.objects.select_for_update().get(id=reference['id'], patient_id=evaluation.patient_id)
        if entity_reference(relation) != reference:
            raise QualificationRejected('DYNAMIC_RELATION_CHANGED')
        connect(node, resource_node('archive_relation_version', UUID(reference['id']), reference['sha256'], evaluation.patient_id), 'input')
        if relation.status != 'confirmed' or not consumed.intersection({str(relation.left_fact_version_id), str(relation.right_fact_version_id)}):
            continue
        frozen = next(item for item in inputs.snapshot['relation_proofs'] if item['relation_id'] == reference['id'])
        if frozen['reason_codes']:
            raise QualificationRejected('DYNAMIC_RELATION_PROOF_CHANGED')
        for item in frozen['references']:
            dependency_reference(node, item, evaluation.patient_id, 'verification')
        proof = Verification.objects.select_for_update().get(id=relation.verification_id, patient_id=evaluation.patient_id)
        if proof.revoked_at is not None:
            raise QualificationRejected('DYNAMIC_RELATION_PROOF_CHANGED')
        for row in (proof, proof.verifier, proof.evidence_source, proof.evidence_source.source, proof.evidence_source.retention_policy):
            dependency_reference(node, entity_reference(row), evaluation.patient_id, 'verification')
        for point in (proof.verified_at, timezone.now()):
            role = check_service_grant(proof.verifier, 'verify_field', inputs.context['environment'], point)
            dependency_reference(node, entity_reference(role), evaluation.patient_id, 'policy')
        for item in VerificationField.objects.select_for_update().filter(verification_id=proof.id, patient_id=evaluation.patient_id,
                result='consistent', field_id__in=ArchiveFactVersion.objects.filter(patient_id=evaluation.patient_id,
                    id__in=[relation.left_fact_version_id, relation.right_fact_version_id]).values('field_id')).order_by('id'):
            dependency_reference(node, entity_reference(item), evaluation.patient_id, 'verification')
            field = RecordField.objects.select_for_update().get(id=item.field_id, patient_id=evaluation.patient_id)
            version = RecordVersion.objects.select_for_update().get(id=field.record_version_id, patient_id=evaluation.patient_id)
            for point in (proof.verified_at, timezone.now()):
                for grant in lock_case_permissions(proof.verifier, inputs.patient, 'verify_field', proof.purpose, point,
                        record_id=version.record_id, author_id=version.author_id, field_codes=[field.field_code]):
                    dependency_reference(node, entity_reference(grant), evaluation.patient_id, 'policy')


def stored_rows(evaluation):
    return {'evaluation': entity_reference(evaluation),
        'signals': [entity_reference(row) for row in AlertSignalResult.objects.filter(evaluation_id=evaluation.id).order_by('signal_id')],
        'inputs': [entity_reference(row) for row in AlertEvaluationInput.objects.filter(evaluation_id=evaluation.id).order_by('signal_id', 'position')],
        'assessments': [entity_reference(row) for row in AlertEvaluationAssessment.objects.filter(evaluation_id=evaluation.id).order_by('role', 'assessment_id')],
        'decisions': [entity_reference(row) for row in AlertDecision.objects.filter(evaluation_id=evaluation.id).order_by('rule_key')]}


def persist_evaluation(task, inputs, attempt):
    payload, context = task.input_refs['payload'], inputs.context
    decision = inputs.snapshot['combinations']['trigger']['state']
    evaluation = AlertEvaluation.objects.create(patient=inputs.patient, task_id=task.id, environment=context['environment'],
        policy=inputs.policy, window_policy=inputs.window, assessment_at=TIMESTAMP.validate_python(payload['assessment_at']),
        known_at=TIMESTAMP.validate_python(payload['known_at']), context_sequence=task.input_refs['context_sequence'],
        mode=payload['mode'], status='insufficient' if decision == 'unknown' else 'completed', decision=decision,
        data_epoch=inputs.patient.data_epoch, verification_epoch=inputs.patient.verification_epoch,
        publication_epoch=int(context['publication_epoch']), authorization_version=inputs.actor.auth_epoch + 1,
        lease_generation=attempt.lease_generation, use_authorization_id=inputs.use_authorization.id,
        input_digest=task.input_sha256, missing_items=[{'variable_code': signal['signal_id'], 'reason_codes': signal['reason_codes']}
            for signal in inputs.signals if signal['state'] == 'unknown'], computed_at=timezone.now(), validity='valid')
    for signal in inputs.signals:
        if not SIGNAL.is_valid(signal):
            raise QualificationRejected('DYNAMIC_SIGNAL_OUTPUT_INVALID')
        AlertSignalResult.objects.create(evaluation_id=evaluation.id, patient_id=evaluation.patient_id,
            **{name: signal[name] for name in ('signal_id', 'schema_version', 'state', 'algorithm_version', 'input_field_ids', 'reason_codes', 'pair_results')})
        for item in signal['inputs']:
            ref = item['reference']
            AlertEvaluationInput.objects.create(evaluation_id=evaluation.id, patient_id=evaluation.patient_id,
                signal_id=signal['signal_id'], position=item['position'], variable_code=item['variable_code'], input_role=item['input_role'],
                **{name: ref[name] for name in ('record_version_id', 'field_id', 'verification_id', 'normalization_id', 'medical_time_id', 'source_revision_id')})
        if 'assessment_id' in signal:
            AlertEvaluationAssessment.objects.create(evaluation_id=evaluation.id, patient_id=evaluation.patient_id,
                assessment_id=signal['assessment_id'], role=signal['signal_id'])
    save_dependencies(evaluation, inputs)
    lifecycle = adopt_lifecycle(evaluation, inputs)
    if lifecycle['state'] == 'superseded':
        evaluation.status = 'superseded'
        evaluation.save(update_fields=['status'])
    return evaluation, lifecycle


def execute_dynamic_evaluation(event, owner):
    with transaction.atomic():
        if ConsumerReceipt.objects.filter(consumer='alert_evaluation', event=event).exists():
            return
        initial = Task.objects.get(id=event.task_id)
        checked_task(initial)
        context = initial.input_refs['authorization_context']
        _, _, _, _, runtime = lock_actor(initial.patient_id, initial.actor, int(context['auth_epoch']), context['environment'], 'execute_assessment')
        task = Task.objects.select_for_update().get(id=initial.id)
        checked_task(task)
        if task.input_sha256 != initial.input_sha256:
            raise QualificationRejected('DYNAMIC_TASK_INPUT_CHANGED')
        if ConsumerReceipt.objects.filter(consumer='alert_evaluation', event=event).exists():
            return
        if task.status in TERMINAL:
            record_receipt('alert_evaluation', event, task.id)
            return
        policies = [item for item in runtime.payload['tasks'] if item['task_type'] == 'alert_evaluation']
        if len(policies) != 1:
            raise QualificationRejected('DYNAMIC_TASK_POLICY_UNAVAILABLE')
        policy = policies[0]
        execution_until = min(task.deadline_at, timezone.now() + timedelta(seconds=policy['execution_seconds']))
        if execution_until <= timezone.now():
            expire_task(task, 'TASK_QUEUE_DEADLINE_EXCEEDED')
            record_receipt('alert_evaluation', event, task.id)
            return
        if not acquire_lease(task.id, owner, min(execution_until, timezone.now() + timedelta(seconds=policy['lease_seconds']))):
            return
        task.refresh_from_db()
        attempt = create_attempt(task, task.lease_generation)
    with lease_heartbeat(task.id, attempt.lease_generation, policy, execution_until) as stop_heartbeat:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SELECT set_config(%s, %s, true)', ['lock_timeout', f"{runtime.payload['api']['lock_timeout_ms']}ms"])
                cursor.execute('SELECT set_config(%s, %s, true)', ['statement_timeout', f"{runtime.payload['api']['statement_timeout_ms']}ms"])
            inputs, _ = prepare_dynamic_scope(patient_id=task.patient_id, actor=task.actor, auth_epoch=int(context['auth_epoch']),
                environment=context['environment'], payload=task.input_refs['payload'],
                automatic_trigger=task.input_refs['snapshot'].get('automatic_trigger'))
            stop_heartbeat()
            task = Task.objects.select_for_update().get(id=task.id)
            checked_task(task)
            if (task.status != 'running' or task.lease_generation != attempt.lease_generation or task.lease_owner != owner
                    or task.lease_until is None or task.lease_until <= timezone.now() or execution_until <= timezone.now()):
                raise QualificationRejected('DYNAMIC_TASK_LEASE_INVALID')
            if inputs.context != context or inputs.snapshot != task.input_refs['snapshot']:
                task.status, task.status_version, task.lease_until = 'superseded', task.status_version + 1, timezone.now()
                task.save(update_fields=['status', 'status_version', 'lease_until'])
                finish_attempt(attempt, 'superseded', error_code='DYNAMIC_CONTEXT_CHANGED')
                record_receipt('alert_evaluation', event, task.id)
                return
            evaluation, lifecycle = persist_evaluation(task, inputs, attempt)
            task.status, task.status_version, task.lease_until = 'completed', task.status_version + 1, timezone.now()
            task.save(update_fields=['status', 'status_version', 'lease_until'])
            finish_attempt(attempt, 'completed', usage_evidence={'schema_version': 'dynamic-evaluation-execution-2',
                'evaluation_id': str(evaluation.id), 'snapshot_sha256': context['snapshot_sha256'],
                'implementation_sha256': context['implementation_sha256'], 'rows': stored_rows(evaluation), 'lifecycle': lifecycle})
            record_receipt('alert_evaluation', event, evaluation.id)


def expire_task(task, error):
    task.status, task.status_version, task.lease_until = 'failed', task.status_version + 1, timezone.now()
    task.save(update_fields=['status', 'status_version', 'lease_until'])
    attempts = TaskAttempt.objects.filter(task=task, lease_generation=task.lease_generation, status='running')
    if attempts.exists():
        attempts.update(status='failed', finished_at=timezone.now(), error_code=error)
    else:
        TaskAttempt.objects.create(task=task, attempt_number=TaskAttempt.objects.filter(task=task).count() + 1,
            lease_generation=task.lease_generation, started_at=timezone.now(), finished_at=timezone.now(), status='failed', error_code=error)


def dynamic_task_view(task, runtime):
    evaluation = AlertEvaluation.objects.filter(task_id=task.id, patient_id=task.patient_id).first()
    attempt = TaskAttempt.objects.filter(task=task).order_by('-attempt_number').first()
    return {'task_id': str(task.id), 'task_type': 'alert_evaluation', 'request_id': str(task.request_row.request_id) if task.request_row_id else None,
        'accepted_at': task.created_at.isoformat(), 'status': task.status, 'status_version': str(task.status_version),
        'poll_after_ms': None if task.status in TERMINAL else runtime.payload['api']['poll_after_ms'], 'allowed_actions': [],
        'result_type': 'alert_evaluation' if evaluation else None, 'result_ref': str(evaluation.id) if evaluation else None,
        'error_code': attempt.error_code if attempt else None}


@transaction.atomic
def read_dynamic_task(*, task_id, actor, auth_epoch, environment, action='read_assessment'):
    task = Task.objects.filter(id=task_id, schema_version='dynamic-evaluation-task-2').first()
    if task is None:
        raise QualificationRejected('DYNAMIC_TASK_NOT_FOUND')
    checked_task(task)
    if action not in ('read_assessment', 'execute_assessment') or action == 'execute_assessment' and task.actor_id != actor.id:
        raise QualificationRejected('DYNAMIC_TASK_SCOPE_DENIED')
    _, patient, actor, _, runtime = lock_actor(task.patient_id, actor, auth_epoch, environment, action)
    lock_case_permissions(actor, patient, action, task.input_refs['authorization_context']['purpose'], timezone.now(), whole_resource=True)
    task = Task.objects.select_for_update().get(id=task.id)
    checked_task(task)
    if task.status not in TERMINAL and task.deadline_at <= timezone.now():
        expire_task(task, 'TASK_DEADLINE_EXCEEDED')
    return dynamic_task_view(task, runtime)


@transaction.atomic
def read_dynamic_evaluation(*, evaluation_id, actor, auth_epoch, environment):
    evaluation = AlertEvaluation.objects.filter(id=evaluation_id).first()
    if evaluation is None:
        raise QualificationRejected('DYNAMIC_EVALUATION_NOT_FOUND')
    _, patient, actor, _, runtime = lock_actor(evaluation.patient_id, actor, auth_epoch, environment, 'read_assessment')
    evaluation = AlertEvaluation.objects.select_for_update().get(id=evaluation.id)
    task = Task.objects.select_for_update().get(id=evaluation.task_id, patient_id=patient.id)
    checked_task(task)
    context = task.input_refs['authorization_context']
    lock_case_permissions(actor, patient, 'read_assessment', context['purpose'], timezone.now(), whole_resource=True)
    attempt = TaskAttempt.objects.select_for_update().get(task=task, lease_generation=evaluation.lease_generation, status='completed')
    evidence = attempt.usage_evidence
    rows = stored_rows(evaluation)
    if (task.status != 'completed' or evaluation.input_digest != task.input_sha256
            or evaluation.context_sequence != task.input_refs['context_sequence'] or evidence['evaluation_id'] != str(evaluation.id)
            or evidence['snapshot_sha256'] != context['snapshot_sha256'] or evidence['implementation_sha256'] != context['implementation_sha256']
            or any(rows[key] != evidence['rows'][key] for key in ('signals', 'inputs', 'assessments', 'decisions'))
            or evaluation.validity == 'valid' and rows['evaluation'] != evidence['rows']['evaluation']):
        raise QualificationRejected('DYNAMIC_RESULT_CHANGED')
    node = DependencyNode.objects.select_for_update().get(resource_type='alert_evaluation', resource_id=evaluation.id, resource_version='1', patient_id=patient.id)
    invalid = (evaluation.data_epoch != patient.data_epoch or evaluation.verification_epoch != patient.verification_epoch
        or node.validity != 'valid' or DependencyEdge.objects.filter(dependent=node, prerequisite__validity='invalid').exists())
    if not invalid and evaluation.validity == 'valid':
        inputs, _ = prepare_dynamic_scope(patient_id=patient.id, actor=task.actor, auth_epoch=int(context['auth_epoch']),
            environment=environment, payload=task.input_refs['payload'], automatic_trigger=task.input_refs['snapshot'].get('automatic_trigger'))
        invalid = inputs.context != context or inputs.snapshot != task.input_refs['snapshot']
    if invalid and evaluation.validity == 'valid':
        evaluation.validity = 'invalid'
        evaluation.save(update_fields=['validity'])
    valid = evaluation.validity == 'valid'
    snapshot = task.input_refs['snapshot']
    value = {'schema_version': 'dynamic-evaluation-view-2', 'evaluation_id': str(evaluation.id), 'patient_id': str(patient.id),
        'task': dynamic_task_view(task, runtime), 'mode': evaluation.mode, 'status': evaluation.status, 'validity': evaluation.validity,
        'decision': evaluation.decision if valid else 'unknown', 'assessment_at': evaluation.assessment_at.isoformat(),
        'known_at': evaluation.known_at.isoformat(), 'context_sequence': str(evaluation.context_sequence),
        'policy_id': str(evaluation.policy_id), 'window_policy_id': str(evaluation.window_policy_id),
        'input_sha256': task.input_sha256, 'implementation_sha256': context['implementation_sha256'], 'computed_at': evaluation.computed_at.isoformat(),
        'missing_items': evaluation.missing_items if valid else [], 'context_states': snapshot['context_states'] if valid else None,
        'limitations': snapshot['limitations'] if valid else [], 'applicability': snapshot['applicability'] if valid else 'insufficient',
        'signals': snapshot['signals'] if valid else [], 'combinations': snapshot['combinations'] if valid else None,
        'selected_refs': snapshot['selected_refs'] if valid else {}, 'exclusions': snapshot['exclusions'] if valid else [],
        'relation_refs': snapshot['relation_refs'] if valid else [], 'relation_proofs': snapshot['relation_proofs'] if valid else [],
        'clinical_selections': [{**item, 'saved_at': task.request_row.completed_at.isoformat() if item['saved_at'] is None else item['saved_at']}
            for item in snapshot['clinical_selections']] if valid else [], 'lifecycle': evidence['lifecycle'] if valid else None,
        # Preset authorization proofs stay in the immutable internal snapshot;
        # the existing public trigger DTO contains only these three identities.
        'automatic_trigger': {key: snapshot['automatic_trigger'][key] for key in ('schedule', 'source', 'trigger_task_id')}
            if valid and snapshot.get('automatic_trigger') is not None else None,
        'sensitivity_preset': snapshot.get('sensitivity_preset') if valid else None,
        'reason_code': None if valid else 'DYNAMIC_CONTEXT_INVALIDATED'}
    if not OUTPUT.is_valid(value):
        raise QualificationRejected('DYNAMIC_RESULT_FORMAT_INVALID')
    return value
