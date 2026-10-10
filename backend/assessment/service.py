import json
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.core.serializers.json import DjangoJSONEncoder
from django.apps import apps
from django.db import connection, transaction
from django.utils import timezone
from pydantic import TypeAdapter

from archives.inputs import PROTOCOL, REGISTRY
from archives.models import ObservationPeriodVersion
from assessment.medical_intervals import medical_interval
from assessment.configuration_proofs import configuration_proof
from assessment.models import Assessment, AssessmentInput, AssessmentOutput
from assessment.provenance import entity_reference
from assessment.qualified_facts import QualificationRejected, lock_case_permissions
from assessment.static_computation import compute_static
from assessment.static_inputs import collect_static_inputs
from authorization.policy import check_principal_session, check_service_grant
from authorization.models import AccessGrant, ServiceRoleGrant
from configurations.evidence import artifact_verification_scope, deployed_artifact_checksum
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from dependencies.models import DependencyEdge, DependencyNode
from family_content.gate_locks import read_environment_gate_shared
from files.purpose_dependencies import connect, resource_node
from identity.models import Patient, Principal
from jsonschema import Draft202012Validator, FormatChecker
from medical_time.models import MedicalTime
from tasks.lease import lease_heartbeat
from tasks.models import ConsumerReceipt, Task, TaskAttempt
from tasks.outbox import emit_event, record_receipt
from tasks.service import acquire_lease, create_attempt, finish_attempt, input_digest, register_task


INPUT = Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/AssessmentInput'}, registry=REGISTRY, format_checker=FormatChecker())
TIMESTAMP = TypeAdapter(datetime)
TERMINAL = ('completed', 'failed', 'cancelled', 'superseded', 'awaiting_resolution')


def assessment_payload(payload):
    if not INPUT.is_valid(payload):
        raise QualificationRejected('ASSESSMENT_INPUT_INVALID')
    return payload


def lock_actor(patient_id, actor, auth_epoch, environment, action):
    gate = read_environment_gate_shared(environment)
    patient = Patient.objects.select_for_update().get(id=patient_id)
    actor = Principal.objects.select_for_update().get(id=actor.id)
    check_principal_session(actor, auth_epoch)
    if not gate.enabled or patient.status != 'active':
        raise QualificationRejected('ASSESSMENT_ENVIRONMENT_UNAVAILABLE')
    grant = check_service_grant(actor, action, environment, timezone.now())
    runtime = ConfigVersion.objects.select_for_update().filter(id=gate.runtime_config_id,
        kind='provider', schema_version='backend-runtime-1').first()
    if runtime is None:
        raise QualificationRejected('ASSESSMENT_RUNTIME_UNAVAILABLE')
    reason = configuration_qualification_reason(runtime, environment, audience='support')
    if reason:
        raise QualificationRejected(reason)
    return gate, patient, actor, grant, runtime


def observation_scope(patient, payload, known_at):
    if payload['observation_period_id'] is None:
        return None
    row = ObservationPeriodVersion.objects.select_for_update().get(id=payload['observation_period_id'], patient_id=patient.id)
    newer = ObservationPeriodVersion.objects.filter(patient_id=patient.id, period_id=row.period_id, version__gt=row.version)
    if payload['mode'] == 'replay':
        newer = newer.filter(created_at__lte=known_at)
    if row.created_at > known_at or newer.exists():
        raise QualificationRejected('ASSESSMENT_OBSERVATION_PERIOD_UNAVAILABLE')
    start = MedicalTime.objects.select_for_update().get(id=row.start_time_id, patient_id=patient.id)
    end = MedicalTime.objects.select_for_update().get(id=row.end_time_id, patient_id=patient.id)
    first, last = medical_interval(start), medical_interval(end)
    if first.lower > last.upper:
        raise QualificationRejected('ASSESSMENT_OBSERVATION_PERIOD_INVALID')
    return {'lower': first.lower, 'upper': last.upper, 'upper_inclusive': last.upper_inclusive,
        'references': [entity_reference(value) for value in (row, start, end)]}


def locked_inputs(patient, actor, auth_epoch, payload, environment, runtime):
    assessment_payload(payload)
    assessment_at, known_at = TIMESTAMP.validate_python(payload['assessment_at']), TIMESTAMP.validate_python(payload['known_at'])
    period = observation_scope(patient, payload, known_at)
    if period is not None and (assessment_at < period['lower'] or assessment_at > period['upper']
            or assessment_at == period['upper'] and not period['upper_inclusive']):
        raise QualificationRejected('ASSESSMENT_TIME_OUTSIDE_OBSERVATION_PERIOD')
    inputs = collect_static_inputs(patient=patient, actor=actor, auth_epoch=auth_epoch, rule_id=payload['rule_id'],
        window_policy_id=payload['window_policy_id'], verification_policy_id=payload['verification_policy_id'],
        environment=environment, known_at=known_at, assessment_at=assessment_at, mode=payload['mode'],
        maximum_bytes=runtime.payload['api']['max_json_bytes'], observation_scope=period, selection_payload=payload)
    if inputs.rule.payload['kind'] != payload['kind']:
        raise QualificationRejected('ASSESSMENT_RULE_KIND_MISMATCH')
    return inputs, period


def input_snapshot(inputs, period, runtime, payload):
    selected = {**inputs.selections, **inputs.context_selections}
    configs = {row.id: row for row in (runtime, inputs.rule, inputs.window, inputs.verification_policy, *inputs.context_policies)}
    for selection in selected.values():
        if selection.selected is not None:
            for reference in selection.selected.reference['entity_refs']:
                if reference['model'] == 'configurations.configversion':
                    row = ConfigVersion.objects.select_for_update().get(id=reference['id'])
                    if entity_reference(row) != reference:
                        raise QualificationRejected('ASSESSMENT_CONFIGURATION_REFERENCE_CHANGED')
                    configs[row.id] = row
    return {'schema_version': 'static-assessment-inputs-2', 'context_sha256': inputs.sha256,
        'candidate_refs': list(inputs.candidate_refs), 'exclusions': list(inputs.exclusions),
        'relation_refs': list(inputs.relation_refs), 'context_states': inputs.context_states,
        'clinical_selections': list(inputs.clinical_selections),
        'context_reasons': list(inputs.context_reasons), 'limitations': list(inputs.limitations),
        'applicability': inputs.applicability,
        'selected_refs': {code: value.selected.reference if value.selected is not None else None for code, value in selected.items()},
        'configurations': [configuration_proof(value, runtime.payload['environment'], TIMESTAMP.validate_python(payload['known_at']),
            payload['mode']) for value in sorted(configs.values(), key=lambda item: str(item.id))],
        'observation_period': None if period is None else json.loads(json.dumps(period, cls=DjangoJSONEncoder))}


@transaction.atomic
@artifact_verification_scope()
def _prepare_assessment_scope(*, patient_id, actor, auth_epoch, environment, payload):
    gate, patient, actor, grant, runtime = lock_actor(patient_id, actor, auth_epoch, environment, 'execute_assessment')
    inputs, period = locked_inputs(patient, actor, auth_epoch, payload, environment, runtime)
    policies = [item for item in runtime.payload['tasks'] if item['task_type'] == 'assessment']
    if len(policies) != 1:
        raise QualificationRejected('ASSESSMENT_TASK_POLICY_UNAVAILABLE')
    snapshot = input_snapshot(inputs, period, runtime, payload)
    context = {'environment': environment, 'actor_id': str(actor.id), 'patient_id': str(patient.id),
        'auth_epoch': str(actor.auth_epoch), 'data_epoch': str(patient.data_epoch), 'verification_epoch': str(patient.verification_epoch),
        'publication_epoch': str(gate.publication_epoch), 'grant_id': str(grant.id), 'runtime_id': str(runtime.id),
        'runtime_checksum': runtime.checksum, 'snapshot_sha256': compute_checksum(snapshot),
        'implementation_sha256': deployed_artifact_checksum(inputs.rule)}
    context['purpose'] = inputs.verification_policy.payload['purpose']
    return patient, actor, runtime, policies[0], snapshot, context, inputs


def prepare_assessment_scope(*, patient_id, actor, auth_epoch, environment, payload):
    return _prepare_assessment_scope(patient_id=patient_id, actor=actor, auth_epoch=auth_epoch,
        environment=environment, payload=payload)[:6]


@transaction.atomic
def create_assessment_task(*, patient_id, actor, auth_epoch, environment, payload):
    patient, actor, runtime, policy, snapshot, context = prepare_assessment_scope(
        patient_id=patient_id, actor=actor, auth_epoch=auth_epoch, environment=environment, payload=payload)
    task = register_task(actor=actor, patient=patient, task_type='assessment', schema_version='static-assessment-task-2',
        input_refs={'payload': payload, 'snapshot': snapshot, 'authorization_context': context},
        deadline_seconds=policy['queue_wait_seconds'] + policy['execution_seconds'])
    if len(json.dumps(task.input_refs, separators=(',', ':')).encode('utf-8')) > runtime.payload['api']['max_json_bytes']:
        raise QualificationRejected('ASSESSMENT_CONTEXT_LIMIT_EXCEEDED')
    emit_event(event_type='assessment.requested', schema_version='static-assessment-task-2', aggregate_id=task.id,
        aggregate_version=1, correlation_id=task.id, resource_refs={'task_id': str(task.id)}, task=task)
    return task, runtime


def checked_task(task):
    if (task.task_type != 'assessment' or task.schema_version != 'static-assessment-task-2'
            or input_digest(task.input_refs) != task.input_sha256 or task.patient_id is None
            or task.input_refs['authorization_context']['patient_id'] != str(task.patient_id)
            or task.input_refs['authorization_context']['actor_id'] != str(task.actor_id)
            or compute_checksum(task.input_refs['snapshot']) != task.input_refs['authorization_context']['snapshot_sha256']):
        raise QualificationRejected('ASSESSMENT_TASK_INPUT_CHANGED')
    assessment_payload(task.input_refs['payload'])


RESOURCE_NAMES = {
    'archives.archivefactversion': 'archive_fact_version', 'archives.archivedocumentversion': 'archive_document_version',
    'archives.archivedocumentpage': 'archive_document_page', 'archives.archivepage': 'archive_page',
    'records.recordfield': 'record_field', 'records.recordversion': 'record_version',
    'records.normalizedmeasurement': 'normalized_measurement', 'recognition.sourceanchor': 'source_anchor',
    'recognition.candidateconfirmation': 'candidate_confirmation', 'recognition.candidatefield': 'candidate_field',
    'recognition.candidateset': 'candidate_set', 'files.filepage': 'file_page', 'files.fileobject': 'file_object',
    'files.filegroup': 'file_group', 'sources.datauseauthorization': 'data_use_authorization',
    'sources.sourcerevision': 'source_revision', 'verification.verification': 'verification',
    'verification.verificationfield': 'verification_field', 'medical_time.medicaltime': 'medical_time',
    'dictionaries.unitmapping': 'unit_mapping', 'configurations.configversion': 'config_version',
}


def save_dependencies(assessment, inputs, snapshot, context):
    node = resource_node('assessment', assessment.id, '1', assessment.patient_id)
    period = snapshot['observation_period']
    if period is not None:
        for reference in period['references']:
            row = apps.get_model(reference['model']).objects.select_for_update().get(id=reference['id'])
            if entity_reference(row) != reference:
                raise QualificationRejected('ASSESSMENT_OBSERVATION_PERIOD_CHANGED')
            resource_type = 'observation_period_version' if isinstance(row, ObservationPeriodVersion) else 'medical_time'
            version = str(row.version) if isinstance(row, ObservationPeriodVersion) else reference['sha256']
            connect(node, resource_node(resource_type, row.id, version, assessment.patient_id), 'input')
    for proof in snapshot['configurations']:
        configuration = ConfigVersion.objects.select_for_update().get(id=proof['config']['id'])
        connect(node, resource_node('config_version', configuration.id, str(configuration.version), None), 'policy')
        for kind in ('approval', 'validation'):
            reference = proof[kind]
            connect(node, resource_node('config_' + kind, UUID(reference['id']), reference['sha256'], None), 'policy')
    role = ServiceRoleGrant.objects.select_for_update().get(id=context['grant_id'])
    connect(node, resource_node('service_role_grant', role.id, role.valid_from.isoformat(), None), 'policy')
    for selection in {**inputs.selections, **inputs.context_selections}.values():
        if selection.selected is None:
            continue
        reference = selection.selected.reference
        for item in reference['entity_refs']:
            resource_type = RESOURCE_NAMES[item['model']]
            row = apps.get_model(item['model']).objects.select_for_update().get(id=item['id'])
            if entity_reference(row) != item:
                raise QualificationRejected('ASSESSMENT_DEPENDENCY_CHANGED')
            version = (row.object_version if resource_type == 'file_object' else str(row.version) if hasattr(row, 'version')
                else row.verified_at.isoformat() if resource_type == 'verification' else item['sha256'])
            connect(node, resource_node(resource_type, UUID(item['id']), version,
                None if resource_type in ('config_version', 'unit_mapping', 'source_revision') else assessment.patient_id),
                'verification' if resource_type in ('verification', 'verification_field') else 'input')
            if resource_type == 'archive_document_version':
                connect(node, resource_node('archive_document', row.document_id, str(row.version), assessment.patient_id), 'source')
        for grant in AccessGrant.objects.select_for_update().filter(id__in=reference['access_grant_ids']).order_by('id'):
            connect(node, resource_node('access_grant', grant.id, grant.valid_from.isoformat(), assessment.patient_id), 'policy')
    for reference in inputs.relation_refs:
        connect(node, resource_node('archive_relation_version', UUID(reference['id']), reference['sha256'], assessment.patient_id), 'input')
    for proof in inputs.clinical_selections:
        for reference in proof['references']:
            row = apps.get_model(reference['model']).objects.select_for_update().get(id=reference['id'])
            if entity_reference(row) != reference:
                raise QualificationRejected('ASSESSMENT_SELECTION_DEPENDENCY_CHANGED')
            resource_type = {'authorization.accessgrant': 'access_grant', 'authorization.servicerolegrant': 'service_role_grant',
                'identity.principal': 'principal', 'sources.sourcerevision': 'source_revision', 'sources.sourceregistry': 'source_registry',
                'retention.retentionpolicy': 'retention_policy', 'tasks.task': 'task', 'requests.writerequest': 'write_request',
                'requests.writeintent': 'write_intent', 'audit.auditevent': 'audit_event', 'tasks.taskattempt': 'task_attempt'}[reference['model']]
            version = row.valid_from.isoformat() if resource_type in ('access_grant', 'service_role_grant') else reference['sha256']
            connect(node, resource_node(resource_type, UUID(reference['id']), version,
                assessment.patient_id if resource_type in ('access_grant', 'task', 'write_request', 'write_intent', 'audit_event') else None), 'policy')


def execute_static_assessment(event, owner):
    with transaction.atomic():
        if ConsumerReceipt.objects.filter(consumer='assessment', event=event).exists():
            return
        initial = Task.objects.get(id=event.task_id)
        checked_task(initial)
        context = initial.input_refs['authorization_context']
        _, _, _, _, runtime = lock_actor(initial.patient_id, initial.actor, int(context['auth_epoch']), context['environment'], 'execute_assessment')
        task = Task.objects.select_for_update().get(id=initial.id)
        checked_task(task)
        if task.input_sha256 != initial.input_sha256 or task.actor_id != initial.actor_id or task.patient_id != initial.patient_id:
            raise QualificationRejected('ASSESSMENT_TASK_INPUT_CHANGED')
        if ConsumerReceipt.objects.filter(consumer='assessment', event=event).exists():
            return
        if task.status in TERMINAL:
            record_receipt('assessment', event, task.id)
            return
        policies = [item for item in runtime.payload['tasks'] if item['task_type'] == 'assessment']
        if len(policies) != 1:
            raise QualificationRejected('ASSESSMENT_TASK_POLICY_UNAVAILABLE')
        policy = policies[0]
        execution_until = min(task.deadline_at, timezone.now() + timedelta(seconds=policy['execution_seconds']))
        if execution_until <= timezone.now():
            task.status, task.status_version = 'failed', task.status_version + 1
            task.save(update_fields=['status', 'status_version'])
            TaskAttempt.objects.create(task=task, attempt_number=TaskAttempt.objects.filter(task=task).count() + 1,
                lease_generation=task.lease_generation, started_at=timezone.now(), finished_at=timezone.now(), status='failed',
                error_code='TASK_QUEUE_DEADLINE_EXCEEDED')
            record_receipt('assessment', event, task.id)
            return
        if not acquire_lease(task.id, owner, min(execution_until, timezone.now() + timedelta(seconds=policy['lease_seconds']))):
            return
        task.refresh_from_db()
        attempt = create_attempt(task, task.lease_generation)
    with lease_heartbeat(task.id, attempt.lease_generation, policy, execution_until) as stop_heartbeat:
        with transaction.atomic(), artifact_verification_scope():
            with connection.cursor() as cursor:
                cursor.execute('SELECT set_config(%s, %s, true)', ['lock_timeout', f"{runtime.payload['api']['lock_timeout_ms']}ms"])
                cursor.execute('SELECT set_config(%s, %s, true)', ['statement_timeout', f"{runtime.payload['api']['statement_timeout_ms']}ms"])
            patient, actor, runtime, _, snapshot, current_context, inputs = _prepare_assessment_scope(patient_id=task.patient_id,
                actor=task.actor, auth_epoch=int(context['auth_epoch']), environment=context['environment'], payload=task.input_refs['payload'])
            if current_context != context or snapshot != task.input_refs['snapshot']:
                stop_heartbeat()
                task = Task.objects.select_for_update().get(id=task.id)
                if task.status == 'running' and task.lease_generation == attempt.lease_generation:
                    task.status, task.status_version = 'superseded', task.status_version + 1
                    task.save(update_fields=['status', 'status_version'])
                    finish_attempt(attempt, 'superseded', error_code='ASSESSMENT_CONTEXT_CHANGED')
                    record_receipt('assessment', event, task.id)
                return
            computed = compute_static(task.input_refs['payload']['kind'], inputs.selections,
                maximum_digits=runtime.payload['limits']['max_decimal_digits'], context_reasons=list(inputs.context_reasons),
                applicability=inputs.applicability)
            stop_heartbeat()
            task = Task.objects.select_for_update().get(id=task.id)
            checked_task(task)
            if task.input_refs['authorization_context'] != context or task.input_refs['snapshot'] != snapshot:
                raise QualificationRejected('ASSESSMENT_TASK_INPUT_CHANGED')
            if (task.status != 'running' or task.lease_generation != attempt.lease_generation or task.lease_owner != owner
                    or task.lease_until is None or task.lease_until <= timezone.now() or execution_until <= timezone.now()):
                raise QualificationRejected('ASSESSMENT_TASK_LEASE_INVALID')
            assessment = Assessment.objects.create(patient=patient, kind=task.input_refs['payload']['kind'],
                assessment_at=TIMESTAMP.validate_python(task.input_refs['payload']['assessment_at']), rule=inputs.rule,
                window_policy=inputs.window, task_id=task.id, data_epoch=patient.data_epoch, verification_epoch=patient.verification_epoch,
                computation_status=computed['computation_status'], validity='valid', missing_items=computed['missing_items'], computed_at=timezone.now())
            for code, selection in {**inputs.selections, **inputs.context_selections}.items():
                if selection.selected is None:
                    continue
                reference = selection.selected.reference
                AssessmentInput.objects.create(assessment_id=assessment.id, patient_id=patient.id, variable_code=code,
                    record_version_id=reference['record_version_id'], field_id=reference['field_id'], verification_id=reference['verification_id'],
                    normalization_id=reference['normalization_id'], input_snapshot={'reference': reference,
                        'value': format(selection.selected.value, 'f'), 'unit': reference['normalized_unit'],
                        'role': 'context' if code in inputs.context_selections or code in ('sjia', 'fever') else 'current'})
            if computed['ms_contributions'] is not None:
                computed['ms_contributions'] = {code: format(Decimal(value), 'f') for code, value in computed['ms_contributions'].items()}
            output = AssessmentOutput.objects.create(assessment=assessment, schema_version='static-assessment-output-2',
                **{key: computed[key] for key in ('classification_conditions', 'classification_result', 'ms_contributions', 'ms_total', 'ms_threshold_result')})
            save_dependencies(assessment, inputs, snapshot, context)
            task.status, task.status_version, task.lease_until = 'completed', task.status_version + 1, timezone.now()
            task.save(update_fields=['status', 'status_version', 'lease_until'])
            finish_attempt(attempt, 'completed', usage_evidence={'schema_version': 'static-assessment-execution-2',
                'assessment_id': str(assessment.id), 'context_sha256': inputs.sha256, 'output_reference': entity_reference(output),
                'assessment_reference': entity_reference(assessment),
                'input_references': [entity_reference(row) for row in AssessmentInput.objects.filter(
                    assessment_id=assessment.id, patient_id=patient.id).order_by('variable_code')],
                'applicability': computed.get('applicability', inputs.applicability), 'context_states': inputs.context_states,
                'limitations': list(inputs.limitations), 'implementation_sha256': context['implementation_sha256']})
            record_receipt('assessment', event, assessment.id)


def assessment_task_view(task, runtime):
    result = Assessment.objects.filter(task_id=task.id, patient_id=task.patient_id).first()
    attempt = TaskAttempt.objects.filter(task=task).order_by('-attempt_number').first()
    return {'task_id': str(task.id), 'task_type': 'assessment',
        'request_id': str(task.request_row.request_id) if task.request_row_id else None,
        'accepted_at': task.created_at.isoformat(), 'status': task.status, 'status_version': str(task.status_version),
        'poll_after_ms': None if task.status in TERMINAL else runtime.payload['api']['poll_after_ms'],
        'allowed_actions': [], 'result_type': 'assessment' if result is not None else None,
        'result_ref': str(result.id) if result is not None else None, 'error_code': attempt.error_code if attempt is not None else None}


@transaction.atomic
def read_assessment_task(*, task_id, actor, auth_epoch, environment, action='read_assessment'):
    task = Task.objects.filter(id=task_id, schema_version='static-assessment-task-2').first()
    if task is None:
        raise QualificationRejected('ASSESSMENT_TASK_NOT_FOUND')
    checked_task(task)
    if action not in ('read_assessment', 'execute_assessment') or action == 'execute_assessment' and task.actor_id != actor.id:
        raise QualificationRejected('ASSESSMENT_TASK_SCOPE_DENIED')
    _, patient, actor, _, runtime = lock_actor(task.patient_id, actor, auth_epoch, environment, action)
    lock_case_permissions(actor, patient, action, task.input_refs['authorization_context']['purpose'], timezone.now(), whole_resource=True)
    task = Task.objects.select_for_update().get(id=task.id)
    checked_task(task)
    if task.status not in TERMINAL and task.deadline_at <= timezone.now():
        task.status, task.status_version, task.lease_until = 'failed', task.status_version + 1, timezone.now()
        task.save(update_fields=['status', 'status_version', 'lease_until'])
        attempts = TaskAttempt.objects.filter(task=task, lease_generation=task.lease_generation, status='running')
        if attempts.exists():
            attempts.update(status='failed', finished_at=timezone.now(), error_code='TASK_DEADLINE_EXCEEDED')
        else:
            TaskAttempt.objects.create(task=task, attempt_number=TaskAttempt.objects.filter(task=task).count() + 1,
                lease_generation=task.lease_generation, started_at=timezone.now(), finished_at=timezone.now(), status='failed',
                error_code='TASK_QUEUE_DEADLINE_EXCEEDED')
    return assessment_task_view(task, runtime)


@transaction.atomic
def read_assessment(*, assessment_id, actor, auth_epoch, environment):
    assessment = Assessment.objects.filter(id=assessment_id).first()
    if assessment is None:
        raise QualificationRejected('ASSESSMENT_NOT_FOUND')
    _, patient, actor, _, runtime = lock_actor(assessment.patient_id, actor, auth_epoch, environment, 'read_assessment')
    assessment = Assessment.objects.select_for_update().get(id=assessment.id)
    task = Task.objects.get(id=assessment.task_id, patient_id=patient.id)
    checked_task(task)
    context = task.input_refs['authorization_context']
    lock_case_permissions(actor, patient, 'read_assessment', context['purpose'], timezone.now(), whole_resource=True)
    output = AssessmentOutput.objects.select_for_update().get(assessment=assessment, schema_version='static-assessment-output-2')
    attempt = TaskAttempt.objects.select_for_update().get(task=task, lease_generation=task.lease_generation, status='completed')
    evidence = attempt.usage_evidence
    stored_inputs = list(AssessmentInput.objects.select_for_update().filter(assessment_id=assessment.id,
        patient_id=patient.id).order_by('variable_code'))
    expected_inputs = {code: reference for code, reference in task.input_refs['snapshot']['selected_refs'].items()
        if reference is not None}
    if (task.status != 'completed' or assessment.computed_at is None or evidence['assessment_id'] != str(assessment.id)
            or evidence['output_reference'] != entity_reference(output)
            or evidence['input_references'] != [entity_reference(row) for row in stored_inputs]
            or len(stored_inputs) != len(expected_inputs)
            or {row.variable_code: row.input_snapshot['reference'] for row in stored_inputs} != expected_inputs
            or any(str(getattr(row, key)) != row.input_snapshot['reference'][key] for row in stored_inputs
                for key in ('record_version_id', 'field_id', 'verification_id'))
            or any((str(row.normalization_id) if row.normalization_id else None) != row.input_snapshot['reference']['normalization_id']
                for row in stored_inputs)):
        raise QualificationRejected('ASSESSMENT_RESULT_CHANGED')
    saved_assessment = evidence['assessment_reference']
    current_assessment = entity_reference(assessment)
    if assessment.validity == 'valid' and saved_assessment != current_assessment:
        raise QualificationRejected('ASSESSMENT_RESULT_CHANGED')
    node = DependencyNode.objects.select_for_update().get(resource_type='assessment', resource_id=assessment.id,
        resource_version='1', patient_id=patient.id)
    invalid = (assessment.data_epoch != patient.data_epoch or assessment.verification_epoch != patient.verification_epoch
        or node.validity != 'valid' or DependencyEdge.objects.filter(dependent=node, prerequisite__validity='invalid').exists())
    if not invalid and assessment.validity == 'valid':
        _, _, _, _, snapshot, current = prepare_assessment_scope(patient_id=patient.id, actor=task.actor,
            auth_epoch=int(context['auth_epoch']), environment=environment, payload=task.input_refs['payload'])
        invalid = current != context or snapshot != task.input_refs['snapshot']
    if invalid and assessment.validity == 'valid':
        assessment.validity = 'invalid'
        assessment.save(update_fields=['validity'])
    valid = assessment.validity == 'valid'
    return {'schema_version': 'static-assessment-view-2', 'assessment_id': str(assessment.id), 'patient_id': str(patient.id),
        'kind': assessment.kind, 'assessment_at': assessment.assessment_at.isoformat(), 'known_at': task.input_refs['payload']['known_at'],
        'mode': task.input_refs['payload']['mode'], 'task': assessment_task_view(task, runtime), 'validity': assessment.validity,
        'computation_status': assessment.computation_status, 'missing_items': assessment.missing_items,
        'rule_id': str(assessment.rule_id), 'window_policy_id': str(assessment.window_policy_id),
        'input_sha256': task.input_sha256, 'implementation_sha256': context['implementation_sha256'],
        'context_states': evidence['context_states'], 'limitations': evidence['limitations'],
        'applicability': evidence['applicability'], 'computed_at': assessment.computed_at.isoformat(),
        'inputs': [{'variable_code': row.variable_code, 'input_snapshot': row.input_snapshot} for row in stored_inputs] if valid else [],
        'exclusions': task.input_refs['snapshot']['exclusions'] if valid else [],
        'clinical_selections': [{'variable_code': item['choice']['variable_code'], 'fact_version_id': item['choice']['fact_version_id'],
            'reason': item['choice']['reason'], 'evidence_ref': item['choice']['evidence_ref'], 'prior_task_id': item['choice']['prior_task_id'],
            'actor_id': item['actor_id'], 'saved_at': task.request_row.completed_at.isoformat() if item['saved_at'] is None else item['saved_at']}
            for item in task.input_refs['snapshot']['clinical_selections']] if valid else [],
        'output': {'classification_conditions': output.classification_conditions, 'classification_result': output.classification_result,
            'ms_contributions': output.ms_contributions, 'ms_total': format(output.ms_total, 'f') if output.ms_total is not None else None,
            'ms_threshold_result': output.ms_threshold_result} if valid else None,
        'reason_code': None if valid else 'ASSESSMENT_CONTEXT_INVALIDATED'}
