from django.db import transaction
from django.utils import timezone

from assessment.provenance import entity_reference
from assessment.qualified_facts import QualificationRejected, lock_case_permissions
from audit.models import AuditEvent
from authorization.policy import check_service_grant
from configurations.evidence import inspect_approved_evidence
from identity.models import Principal
from records.models import RecordField, RecordVersion
from requests.models import WriteIntent, WriteRequest
from requests.service import NORMALIZATION_VERSION, compute_digest
from tasks.models import Task, TaskAttempt
from tasks.service import input_digest
from configurations.service import compute_checksum


def selection_index(choices, variables, windows):
    indexed = {}
    for choice in choices:
        code = choice['variable_code']
        if (code in indexed or code not in variables
                or windows[code]['current_selection'] != 'explicit_clinical_selection'
                or not choice['reason'].strip()):
            raise QualificationRejected('ASSESSMENT_EXPLICIT_SELECTION_INVALID')
        indexed[code] = choice
    return indexed


@transaction.atomic
def previous_selection(choice, patient, payload, known_at, input_role):
    dynamic = 'policy_id' in payload
    operation = 'alert_evaluation_v2_create' if dynamic else 'assessment_v2_create'
    suffix = 'alert-evaluations' if dynamic else 'assessments'
    task = Task.objects.select_for_update().get(id=choice['prior_task_id'], patient_id=patient.id,
        task_type='alert_evaluation' if dynamic else 'assessment',
        schema_version='dynamic-evaluation-task-2' if dynamic else 'static-assessment-task-2', status='completed')
    attempt = TaskAttempt.objects.select_for_update().get(task=task, status='completed', lease_generation=task.lease_generation)
    if (task.created_at > known_at or attempt.finished_at is None or attempt.finished_at > known_at
            or input_digest(task.input_refs) != task.input_sha256 or task.request_row_id is None
            or compute_checksum(task.input_refs['snapshot']) != task.input_refs['authorization_context']['snapshot_sha256']):
        raise QualificationRejected('ASSESSMENT_SELECTION_NOT_AVAILABLE_BY_CUTOFF')
    original = task.input_refs['payload']
    keys = ('policy_id', 'input_rule_id', 'window_policy_id', 'verification_policy_id', 'use_authorization_id') if dynamic else (
        'kind', 'rule_id', 'window_policy_id', 'verification_policy_id')
    if original['mode'] != 'current' or any(original[key] != payload[key] for key in keys):
        raise QualificationRejected('ASSESSMENT_SELECTION_POLICY_MISMATCH')
    saved = WriteRequest.objects.select_for_update().get(id=task.request_row_id)
    intent = WriteIntent.objects.select_for_update().get(write_request_id=saved.id,
        operation_id=operation, status='committed')
    if (saved.status != 'committed' or saved.action != operation
            or saved.target_key != f'/support/patients/{patient.id}/{suffix}'
            or saved.resource_type != 'task' or saved.resource_id != task.id
            or saved.actor_id != task.actor_id or saved.patient_id != patient.id
            or saved.expected_version != 1 or saved.completed_at is None or saved.completed_at > known_at
            or intent.actor_id != task.actor_id or intent.patient_id != patient.id or intent.request_id != saved.request_id
            or intent.target_key != saved.target_key or intent.completed_at != saved.completed_at
            or intent.recovery_payload != original or intent.normalization_version != NORMALIZATION_VERSION
            or saved.normalization_version != NORMALIZATION_VERSION
            or intent.payload_sha256 != saved.payload_sha256
            or compute_digest(operation, saved.target_key, '2.0.0', original) != saved.payload_sha256):
        raise QualificationRejected('ASSESSMENT_SELECTION_RECEIPT_CHANGED')
    audit = AuditEvent.objects.select_for_update().get(actor_id=task.actor_id, patient_id=patient.id,
        action=operation, object_type='task', object_id=task.id, object_version='1',
        request_id=saved.request_id, outcome='committed', occurred_at=saved.completed_at,
        retention_policy_id=intent.retention_policy_id)
    matches = [item for item in task.input_refs['snapshot']['clinical_selections'] if item['choice']['variable_code'] == choice['variable_code']
        and item.get('input_role', 'current') == input_role]
    original_choice = {**choice, 'prior_task_id': None}
    reference_key = f'{input_role}:{choice["variable_code"]}' if dynamic else choice['variable_code']
    if (len(matches) != 1 or matches[0]['choice'] != original_choice
            or matches[0]['actor_id'] != str(task.actor_id)
            or task.input_refs['snapshot']['selected_refs'][reference_key] is None
            or task.input_refs['snapshot']['selected_refs'][reference_key]['fact_version_id'] != choice['fact_version_id']):
        raise QualificationRejected('ASSESSMENT_SELECTION_CONTENT_CHANGED')
    return task, saved, matches[0], [entity_reference(row) for row in (task, saved, intent, audit, attempt)]


@transaction.atomic
def qualified_clinical_selection(*, choice, facts, patient, actor, policy, environment, known_at, payload, input_role='current'):
    values = [item for item in facts if item.reference['fact_version_id'] == choice['fact_version_id']]
    if len(values) != 1 or not values[0].eligible:
        raise QualificationRejected('ASSESSMENT_EXPLICIT_SELECTION_INELIGIBLE')
    selected = values[0]
    references = []
    previous = None
    saved_at = None
    if payload['mode'] == 'replay':
        if choice['prior_task_id'] is None:
            raise QualificationRejected('ASSESSMENT_REPLAY_SELECTION_REQUIRED')
        _, saved, previous, references = previous_selection(choice, patient, payload, known_at, input_role)
        saved_at = saved.completed_at.isoformat()
        actor = Principal.objects.select_for_update().get(id=saved.actor_id)
        points = (saved.completed_at, timezone.now())
    else:
        if choice['prior_task_id'] is not None:
            raise QualificationRejected('ASSESSMENT_CURRENT_SELECTION_REFERENCE_INVALID')
        points = (timezone.now(),)
    if actor.account_status != 'active':
        raise QualificationRejected('ASSESSMENT_SELECTION_ACTOR_UNAVAILABLE')
    field = RecordField.objects.select_for_update().get(id=selected.reference['field_id'], patient=patient)
    version = RecordVersion.objects.select_for_update().get(id=field.record_version_id, patient=patient)
    roles, grants = {}, {}
    for point in points:
        role = check_service_grant(actor, 'verify_field', environment, point)
        roles[str(role.id)] = role
        for grant in lock_case_permissions(actor, patient, 'verify_field', policy.purpose, point,
                record_id=version.record_id, author_id=version.author_id, field_codes=[field.field_code]):
            grants[str(grant.id)] = grant
    evidence, _, reason = inspect_approved_evidence(choice['evidence_ref'],
        purpose=policy.verification_evidence_purpose, lock=True)
    if reason:
        raise QualificationRejected(reason)
    if evidence.captured_at > known_at or policy.purpose not in evidence.allowed_use:
        raise QualificationRejected('ASSESSMENT_SELECTION_EVIDENCE_UNAVAILABLE')
    references.extend(entity_reference(value) for value in (actor, evidence, evidence.source,
        evidence.retention_policy, *roles.values(), *grants.values()))
    proof = {'choice': choice, 'actor_id': str(actor.id), 'purpose': policy.purpose,
        'verification_id': selected.reference['verification_id'], 'references': references, 'saved_at': saved_at, 'input_role': input_role}
    if previous is not None:
        current_refs = {(item['model'], item['id']): item for item in references}
        if any(current_refs.get((item['model'], item['id'])) != item for item in previous['references']):
            raise QualificationRejected('ASSESSMENT_SELECTION_AUTHORIZATION_CHANGED')
    return proof
