from decimal import Decimal

from django.conf import settings
from django.db import connection
from django.utils import timezone

from archives.models import ArchiveFact
from assessment.medical_intervals import medical_interval, window_reason
from assessment.provenance import entity_reference
from assessment.qualified_facts import QualifiedFact, QualificationRejected, lock_case_permissions
from configurations.service import compute_checksum
from medical_time.models import MedicalTime
from records.models import Record, RecordField, RecordVersion
from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision


def simulation_scope(*, environment, patient_id, actor_id, rule_id, source_id):
    scope = getattr(settings, 'SJIA_SIMULATION_ASSESSMENT', None)
    if scope is None or str(rule_id) not in scope['input_rule_ids']:
        return False
    # 精确登记环境与对象，真实环境不能采用模拟资料资格。
    if (settings.DEBUG is not True or settings.SJIA_ENVIRONMENT != 'simulation'
            or environment != 'simulation' or connection.settings_dict['NAME'] != scope['database']
            or not scope['database'].startswith('test_')
            or str(patient_id) not in scope['patient_ids'] or str(actor_id) not in scope['actor_ids']
            or str(source_id) not in scope['source_revision_ids']):
        raise QualificationRejected('SIMULATION_ASSESSMENT_SCOPE_DENIED')
    return True


def collect_simulation_fact(*, fact, patient, actor, rule_config, variable, window, environment,
        known_at, assessment_at, input_role, mode, purpose):
    field = RecordField.objects.select_for_update().get(id=fact.field_id, patient_id=patient.id,
        record_version_id=fact.record_version_id)
    version = RecordVersion.objects.select_for_update().get(id=field.record_version_id, patient_id=patient.id)
    record = Record.objects.select_for_update().get(id=version.record_id, patient_id=patient.id)
    logical = ArchiveFact.objects.select_for_update().get(id=fact.fact_id, patient_id=patient.id)
    if (fact.medical_time_id != field.medical_time_id or fact.source_anchor_id != field.anchor_id
            or fact.confirmation_id != field.confirmation_id
            or not (field.concept_code == variable.concept_code
                or field.concept_code is None and field.field_code == variable.concept_code)):
        raise QualificationRejected('FACT_REFERENCE_CHANGED')
    grants = lock_case_permissions(actor, patient, 'execute_assessment', purpose, timezone.now(),
        record_id=record.id, author_id=version.author_id, field_codes=[field.field_code])
    reference = {'fact_version_id': str(fact.id), 'field_id': str(field.id), 'record_version_id': str(version.id),
        'patient_id': str(patient.id), 'verification_id': None, 'normalization_id': None,
        'medical_time_id': str(field.medical_time_id) if field.medical_time_id else None,
        'source_revision_id': str(version.source_revision_id), 'source_anchor_id': None,
        'document_version_id': None, 'page_id': None, 'qualifier': field.qualifier, 'method': field.method,
        'entity_refs': [entity_reference(item) for item in (fact, field, version)],
        'access_grant_ids': [str(item.id) for item in grants],
        'available_at': fact.available_at.isoformat() if fact.available_at else None,
        'availability_status': fact.availability_status}
    result = qualify_simulation_record(fact=fact, field=field, version=version, record=record, logical=logical,
        actor=actor, rule_config=rule_config, variable=variable, window=window, reference=reference,
        environment=environment, known_at=known_at, assessment_at=assessment_at, input_role=input_role, mode=mode, purpose=purpose)
    if result is None:
        raise QualificationRejected('SIMULATION_ASSESSMENT_SCOPE_DENIED')
    return result


def qualify_simulation_record(*, fact, field, version, record, logical, actor, rule_config,
        variable, window, reference, environment, known_at, assessment_at, input_role, mode, purpose):
    if not simulation_scope(environment=environment, patient_id=field.patient_id, actor_id=actor.id,
            rule_id=rule_config.id, source_id=version.source_revision_id):
        return None
    if purpose != 'family_observation' or variable.value_type != 'categorical':
        raise QualificationRejected('SIMULATION_INPUT_POLICY_UNSUPPORTED')
    revision = SourceRevision.objects.select_for_update().get(id=version.source_revision_id)
    registry = SourceRegistry.objects.select_for_update().get(id=revision.source_id)
    authorization = DataUseAuthorization.objects.select_for_update().get(id=version.use_authorization_id,
        patient_id=field.patient_id, source_revision=revision, purpose=purpose)
    if (registry.sensitivity != 'synthetic' or registry.status != 'approved' or revision.status != 'approved'
            or purpose not in revision.allowed_use or revision.captured_at > known_at
            or authorization.environment != environment or authorization.revoked_at is not None
            or authorization.approved_at > known_at
            or authorization.valid_until is not None and authorization.valid_until <= timezone.now()
            or not revision.rights_evidence_ref or not authorization.evidence_ref):
        raise QualificationRejected('SIMULATION_SOURCE_NOT_APPROVED')
    reasons = []
    if fact.availability_status != 'known' or fact.available_at is None:
        reasons.append('FACT_AVAILABILITY_UNKNOWN')
    elif fact.available_at > known_at or fact.created_at > known_at or version.recorded_at > known_at:
        reasons.append('FACT_NOT_AVAILABLE_BY_CUTOFF')
    if record.status != 'active' or version.quality_status != 'valid' or version.capture_method != 'manual':
        reasons.append('FACT_RECORD_INELIGIBLE')
    if mode != 'current' or logical.current_version != fact.version or record.current_version != version.version:
        reasons.append('FACT_VERSION_SUPERSEDED')
    if fact.time_origin != 'observation' or field.medical_time_id is None:
        reasons.append('FACT_MEDICAL_TIME_REQUIRED')
    states = [item for item in variable.categorical_values if item.value == field.categorical_value]
    if field.value_state != 'known' or len(states) != 1:
        reasons.append('FIELD_CATEGORICAL_STATE_UNKNOWN')
    reference = {**reference, 'qualification_basis': 'simulation_record',
        'context_type': 'current', 'assertion_state': states[0].assertion_state if len(states) == 1 else 'unknown'}
    reference['entity_refs'].extend(entity_reference(item) for item in (authorization, revision, registry))
    if reasons:
        return QualifiedFact(reference, None, None, tuple(reasons), simulation_qualified=True)
    medical = MedicalTime.objects.select_for_update().get(id=field.medical_time_id, patient_id=field.patient_id)
    reference['entity_refs'].append(entity_reference(medical))
    if (medical.precision not in window['accepted_precision'] or medical.resolution_status != 'resolved'
            or medical.instant is None or medical.timezone_name != 'Asia/Shanghai'):
        return QualifiedFact(reference, None, None, ('MEDICAL_TIME_UNRESOLVED',), simulation_qualified=True)
    interval = medical_interval(medical)
    timing = window if input_role in ('current', 'context') else {
        **window, 'current_max_age_seconds': window['baseline_max_age_seconds']}
    reason = window_reason(interval, assessment_at, timing)
    if reason:
        return QualifiedFact(reference, interval, None, (reason,), simulation_qualified=True)
    reference.update(medical_interval=interval.snapshot(), medical_precision=medical.precision, normalized_unit=None)
    value = Decimal(states[0].encoded)
    reference['value_sha256'] = compute_checksum({'value': str(value), 'assertion_state': reference['assertion_state']})
    reference['reference_sha256'] = compute_checksum(reference)
    return QualifiedFact(reference, interval, value, (), simulation_qualified=True)
