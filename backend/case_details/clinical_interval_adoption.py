"""Qualified interval evidence and signed saved calculations.

Qualification is called inside existing admission/read locks. Resampling is
separate and refuses a DB transaction. Signatures protect saved computed bytes
without bootstrapping on every read; they never substitute for eligibility,
current source approval, saved replay, or permission rechecks.
"""
from copy import deepcopy
from hashlib import sha256
import json

from django.core import signing
from django.db import connection
from django.utils import timezone

from authorization.policy import check_service_grant, Denied

from audit.models import AuditEvent
from case_details.clinical_intervals import evaluate_intervals, add_interval_report
from case_details.evaluation_evidence import approved_evaluation_artifact, artifact_proof
from case_details.evaluation_units import EvaluationUnitsRejected
from configurations.service import compute_checksum
from identity.models import Principal
from requests.models import WriteRequest


def qualify_interval_protocol(plan, capture, row):
    reference = plan.confidence_interval_protocol_ref
    if reference is None:
        plan_payload, capture_payload = plan.model_dump(mode='json'), capture.model_dump(mode='json')
        return {'proof': None, 'section': evaluate_intervals(plan_payload, capture_payload, None, compute=False),
            'plan': plan_payload, 'capture': capture_payload, 'protocol': None}
    binding = plan.confidence_interval_binding
    if binding is None or binding.purpose != row.purpose:
        raise EvaluationUnitsRejected('INTERVAL_FROZEN_EVIDENCE_BINDING_REQUIRED')
    revision, body = approved_evaluation_artifact(reference, kind='evaluation_protocol', purpose=row.purpose,
        checksum=binding.protocol_sha256)
    if revision.rights_evidence_ref != binding.approval_evidence_ref:
        raise EvaluationUnitsRejected('INTERVAL_APPROVAL_EVIDENCE_CHANGED')
    if revision.captured_at > row.frozen_at:
        raise EvaluationUnitsRejected('INTERVAL_PROTOCOL_NOT_FIXED_BEFORE_FREEZE')
    # captured_at is not approved_at. The existing controlled source approval
    # action writes the committed audit event and receipt in one transaction.
    approval = AuditEvent.objects.select_for_update().filter(id=binding.approval_audit_id, action='source_revision_approve',
        object_type='source_revision', object_id=revision.id, outcome='committed',
        occurred_at__gte=revision.captured_at, occurred_at__lte=row.frozen_at).order_by('-occurred_at', '-id').first()
    if approval is None or approval.actor_id is None or approval.request_id is None:
        raise EvaluationUnitsRejected('INTERVAL_PRE_FREEZE_APPROVAL_REQUIRED')
    approver = Principal.objects.select_for_update().get(id=approval.actor_id)
    receipt = WriteRequest.objects.select_for_update().filter(actor_id=approval.actor_id,
        request_id=approval.request_id, action='source_revision_approve', status='committed',
        resource_type='source_revision', resource_id=revision.id, completed_at=approval.occurred_at).first()
    if approver.account_status != 'active' or receipt is None:
        raise EvaluationUnitsRejected('INTERVAL_APPROVAL_UNAVAILABLE')
    try:
        grant = check_service_grant(approver, 'manage_config', row.environment, approval.occurred_at)
        current_grant = check_service_grant(approver, 'manage_config', row.environment, timezone.now())
    except Denied:
        raise EvaluationUnitsRejected('INTERVAL_APPROVAL_AUTHORITY_UNAVAILABLE')
    if grant.id != current_grant.id:
        raise EvaluationUnitsRejected('INTERVAL_APPROVAL_AUTHORITY_CHANGED')
    approval_revision, _ = approved_evaluation_artifact(revision.rights_evidence_ref,
        kind='configuration_approval', purpose=row.purpose, checksum=binding.approval_evidence_sha256)
    if approval_revision.captured_at > approval.occurred_at:
        raise EvaluationUnitsRejected('INTERVAL_APPROVAL_CHRONOLOGY_INVALID')
    try:
        def invalid_constant(value):
            raise ValueError('Non-finite JSON number')
        payload = json.loads(body, parse_constant=invalid_constant)
    except (ValueError, UnicodeDecodeError):
        payload = None
    # A approved prose document is an unsupported protocol, not absent config.
    section_payload = payload if payload is not None else {'unsupported_artifact_format': 'not_json'}
    plan_payload, capture_payload = plan.model_dump(mode='json'), capture.model_dump(mode='json')
    section = evaluate_intervals(plan_payload, capture_payload, section_payload,
        protocol_body_sha256=sha256(body).hexdigest(), compute=False)
    proof = {**artifact_proof(reference, revision), 'body_sha256': sha256(body).hexdigest(),
        'measurement_plan_ref': plan.plan_ref, 'frozen_at': row.frozen_at.isoformat(), 'purpose': row.purpose,
        'approval': {'audit_id': str(approval.id), 'actor_id': str(approval.actor_id),
            'request_id': str(approval.request_id), 'receipt_id': str(receipt.id),
            'approved_at': approval.occurred_at.isoformat(),
            'authority': {'grant_id': str(grant.id), 'environment': row.environment, 'action': 'manage_config',
                'valid_from': grant.valid_from.isoformat(), 'valid_until': grant.valid_until.isoformat() if grant.valid_until else None,
                'evidence_ref': grant.evidence_ref},
            'evidence': artifact_proof(revision.rights_evidence_ref, approval_revision)},
        'engine': section['engine']}
    return {'proof': proof, 'section': section, 'plan': plan_payload,
        'capture': capture_payload, 'protocol': section_payload}


def prepare_interval_adoption(adoption, interval):
    if interval is None:
        return adoption
    adoption['report'] = add_interval_report(adoption['report'], interval['section'])
    adoption['proofs']['confidence_interval'] = interval['proof']
    adoption['report_sha256'] = compute_checksum(adoption['report'])
    adoption['proofs_sha256'] = compute_checksum(adoption['proofs'])
    # Ephemeral computation inputs: intentionally not saved in task snapshots.
    adoption['_interval_inputs'] = {key: interval[key] for key in ('plan', 'capture', 'protocol')}
    return adoption


def calculate_interval_adoption(adoption, task_input_sha256):
    if '_interval_inputs' not in adoption:
        return adoption
    if adoption['_interval_inputs']['protocol'] is not None and connection.in_atomic_block:
        raise EvaluationUnitsRejected('INTERVAL_COMPUTATION_REQUIRES_UNLOCKED_PHASE')
    from case_details.clinical_adoption import clinical_admission_binding
    admission = clinical_admission_binding(adoption)
    result = deepcopy(adoption)
    inputs = result.pop('_interval_inputs')
    section = evaluate_intervals(inputs['plan'], inputs['capture'], inputs['protocol'],
        protocol_body_sha256=(result['proofs']['confidence_interval'] or {}).get('body_sha256'))
    result['report']['confidence_intervals'] = section
    result['report_sha256'] = compute_checksum(result['report'])
    result['interval_integrity'] = signing.Signer(salt='sjia.clinical-interval-result-1').sign_object({
        'schema_version': 'clinical-interval-saved-proof-1', 'task_input_sha256': task_input_sha256,
        'admission': admission, 'report_sha256': result['report_sha256'], 'proofs_sha256': result['proofs_sha256']})
    return result


def verify_saved_interval_adoption(current, saved, task_input_sha256):
    from case_details.clinical_adoption import clinical_admission_binding
    if (not isinstance(saved, dict) or not isinstance(saved.get('report'), dict)
            or not isinstance(saved.get('proofs'), dict)
            or not isinstance(saved['report'].get('confidence_intervals'), dict)
            or not isinstance(saved.get('interval_integrity'), str)):
        raise EvaluationUnitsRejected('CLINICAL_SAVED_REPORT_CHANGED')
    try:
        proof = signing.Signer(salt='sjia.clinical-interval-result-1').unsign_object(saved.get('interval_integrity', ''))
    except (signing.BadSignature, ValueError, TypeError):
        raise EvaluationUnitsRejected('INTERVAL_SAVED_INTEGRITY_INVALID')
    expected = {'schema_version': 'clinical-interval-saved-proof-1', 'task_input_sha256': task_input_sha256,
        'admission': clinical_admission_binding(current), 'report_sha256': compute_checksum(saved['report']),
        'proofs_sha256': compute_checksum(saved['proofs'])}
    if (proof != expected or saved['proofs'] != current['proofs']
            or saved.get('report_sha256') != expected['report_sha256']
            or saved.get('proofs_sha256') != expected['proofs_sha256']):
        raise EvaluationUnitsRejected('INTERVAL_SAVED_INTEGRITY_INVALID')
    # Everything except the stochastic section must exactly match the freshly
    # requalified deterministic report, even if someone copied another signature.
    if {key:value for key,value in saved['report'].items() if key != 'confidence_intervals'} != {
            key:value for key,value in current['report'].items() if key != 'confidence_intervals'}:
        raise EvaluationUnitsRejected('CLINICAL_SAVED_REPORT_CHANGED')
    section, admitted = saved['report']['confidence_intervals'], current['report']['confidence_intervals']
    for key in ('protocol_sha256', 'protocol_ref', 'input_sha256', 'protocol', 'engine'):
        if key not in section or section[key] != admitted[key]:
            raise EvaluationUnitsRejected('INTERVAL_SAVED_BINDING_CHANGED')
    return saved['report']
