import json
from dataclasses import dataclass
from decimal import Decimal, Inexact, Rounded, localcontext

from django.db import transaction
from django.utils import timezone

from archives.models import ArchiveFact, ArchiveFactVersion
from assessment.medical_intervals import MedicalInterval, medical_interval, window_reason
from assessment.policies import MappingPolicy, QualificationPolicy, StaticContextPolicy, StaticRulePolicy, static_variable
from assessment.provenance import document_provenance, entity_reference
from authorization.models import AccessGrant
from authorization.policy import check_principal_session, check_service_grant, matching_case_grants
from configurations.evidence import inspect_approved_evidence
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from dictionaries.models import UnitMapping
from files.models import FileGroup, FilePage
from files.purpose import checked_bytes, lock_file_access, lock_original, lock_use_authorization
from family_content.gate_locks import read_environment_gate_shared
from identity.models import Patient, Principal
from medical_time.models import MedicalTime
from recognition.models import CandidateConfirmation, CandidateField, CandidateSet, SourceAnchor
from records.models import NormalizedMeasurement, Record, RecordAttachment, RecordField, RecordVersion
from relations.models import Relation
from sources.models import SourceRegistry, SourceRevision
from verification.models import FieldVerificationState, Verification, VerificationField
from alerting.policies import DynamicInputPolicy


class QualificationRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class QualifiedFact:
    reference: dict
    interval: MedicalInterval | None
    value: Decimal | str | None
    reasons: tuple[str, ...]

    @property
    def eligible(self):
        return not self.reasons


def lock_case_permissions(actor, patient, action, purpose, point, **scope):
    matches = [grant for grant in matching_case_grants(actor, patient, action, point, **scope)
        if grant.purpose == purpose and grant.evidence_ref.strip()]
    locked = {grant.id for grant in AccessGrant.objects.select_for_update().filter(id__in=[item.id for item in matches]).order_by('id')}
    relation_ids = {item.relation_id for item in matches if item.relation_id is not None}
    list(Relation.objects.select_for_update().filter(id__in=relation_ids, patient_id=patient.id).order_by('id'))
    result = [grant for grant in matching_case_grants(actor, patient, action, point, **scope)
        if grant.id in locked and grant.purpose == purpose and grant.evidence_ref.strip()]
    if not result:
        raise QualificationRejected('ASSESSMENT_FIELD_SCOPE_DENIED')
    return result


def fact_chronology_reasons(fact, version, *, known_at, accepted_context_types):
    reasons = []
    if fact.availability_status != 'known' or fact.available_at is None:
        reasons.append('FACT_AVAILABILITY_UNKNOWN')
    elif fact.available_at > known_at:
        reasons.append('FACT_NOT_AVAILABLE_BY_CUTOFF')
    if fact.created_at > known_at or version.recorded_at > known_at:
        reasons.append('FACT_NOT_RECORDED_BY_CUTOFF')
    if fact.context_type not in accepted_context_types:
        reasons.append('FACT_CONTEXT_INELIGIBLE')
    if fact.assertion_state == 'unknown':
        reasons.append('FACT_ASSERTION_UNKNOWN')
    if fact.time_origin == 'unknown':
        reasons.append('FACT_MEDICAL_TIME_ORIGIN_UNKNOWN')
    return reasons


def qualified_configuration(config_id, *, kind, schema_version, environment, known_at, mode, lock=True):
    rows = ConfigVersion.objects.select_for_update() if lock else ConfigVersion.objects
    row = rows.filter(id=config_id, kind=kind, schema_version=schema_version).first()
    if row is None:
        raise QualificationRejected('ASSESSMENT_CONFIGURATION_UNAVAILABLE')
    if row.created_at > known_at:
        raise QualificationRejected('ASSESSMENT_CONFIGURATION_NOT_AVAILABLE_BY_CUTOFF')
    points = (known_at, timezone.now())
    for point in points:
        reason = configuration_qualification_reason(row, environment, now=point, audience='support', allow_historical=mode == 'replay')
        if reason:
            raise QualificationRejected(reason)
        for reference in row.payload['evidence_refs']:
            _, _, reason = inspect_approved_evidence(reference, purpose='clinical_policy', lock=lock, now=point)
            if reason:
                raise QualificationRejected(reason)
    return row


def verification_proof(field, version, policy_config, policy, *, environment, known_at, mode):
    if mode == 'current':
        state = FieldVerificationState.objects.select_for_update().filter(field_id=field.id,
            record_version_id=version.id, patient_id=field.patient_id, purpose=policy.purpose, status='verified').first()
        if state is None or state.current_verification_id is None:
            return None, 'FIELD_VERIFICATION_REQUIRED'
        verification = Verification.objects.select_for_update().get(id=state.current_verification_id,
            patient_id=field.patient_id, record_version_id=version.id)
    else:
        proofs = VerificationField.objects.filter(field_id=field.id, patient_id=field.patient_id, record_version_id=version.id)
        verification = Verification.objects.select_for_update().filter(id__in=proofs.values('verification_id'),
            patient_id=field.patient_id, record_version_id=version.id, purpose=policy.purpose,
            policy=policy_config, verified_at__lte=known_at).order_by('-verified_at', '-id').first()
        if verification is None:
            return None, 'FIELD_VERIFICATION_REQUIRED'
    if (verification.policy_id != policy_config.id or verification.purpose != policy.purpose
            or verification.verified_at > known_at or verification.revoked_at is not None):
        return None, 'FIELD_VERIFICATION_NOT_VALID'
    if not VerificationField.objects.select_for_update().filter(verification_id=verification.id,
            field_id=field.id, patient_id=field.patient_id, record_version_id=version.id, result='consistent').exists():
        return None, 'FIELD_VERIFICATION_NOT_CONSISTENT'
    verifier = Principal.objects.select_for_update().get(id=verification.verifier_id)
    if verifier.account_status != 'active':
        return None, 'FIELD_VERIFIER_UNAVAILABLE'
    historical_grant = check_service_grant(verifier, 'verify_field', environment, verification.verified_at)
    current_grant = check_service_grant(verifier, 'verify_field', environment, timezone.now())
    if historical_grant.revoked_at is not None or current_grant.revoked_at is not None:
        return None, 'FIELD_VERIFIER_UNAVAILABLE'
    for point in (verification.verified_at, timezone.now()):
        lock_case_permissions(verifier, field.patient, 'verify_field', policy.purpose, point,
            record_id=version.record_id, author_id=version.author_id, field_codes=[field.field_code])
    evidence, _, reason = inspect_approved_evidence(verification.evidence_ref,
        purpose=policy.verification_evidence_purpose, lock=True)
    if reason:
        return None, reason
    if evidence.id != verification.evidence_source_id or evidence.captured_at > known_at or policy.purpose not in evidence.allowed_use:
        return None, 'FIELD_VERIFICATION_EVIDENCE_CHANGED'
    return verification, None


def normalized_input(field, variable, *, source_revision_id, environment, known_at, mode, maximum_digits, exact_required):
    if field.value_state != 'known' or field.value_type != variable.value_type:
        return None, None, 'FIELD_VALUE_INSUFFICIENT'
    if variable.value_type == 'categorical':
        encodings = {item.value: Decimal(item.encoded) for item in variable.categorical_values}
        if field.categorical_value not in encodings:
            return None, None, 'FIELD_CATEGORICAL_STATE_UNKNOWN'
        return encodings[field.categorical_value], None, None
    if field.raw_numeric is None or field.qualifier not in ('eq', 'lt', 'le', 'gt', 'ge'):
        return None, None, 'FIELD_VALUE_QUALIFIER_REQUIRED'
    if exact_required and field.qualifier != 'eq':
        return None, None, 'FIELD_EXACT_VALUE_REQUIRED'
    config = qualified_configuration(variable.mapping_config_id, kind='mapping', schema_version='unit-mapping-2',
        environment=environment, known_at=known_at, mode=mode)
    plan = MappingPolicy.model_validate_json(json.dumps(config.payload))
    mappings = list(UnitMapping.objects.select_for_update().filter(config=config, concept_code=field.concept_code,
        source_revision_id=source_revision_id, raw_unit=field.raw_unit, method_scope=field.method))
    create_mapping = not mappings
    if not mappings:
        definitions = [item for item in plan.mappings if item.concept_code == field.concept_code
            and item.source_revision_id == source_revision_id and item.raw_unit == field.raw_unit and item.method_scope == field.method]
        if len(definitions) != 1:
            return None, None, 'FIELD_UNIT_MAPPING_UNAVAILABLE'
        definition = definitions[0]
        mapping = UnitMapping(id=definition.mapping_id, config=config, concept_code=definition.concept_code,
            source_revision_id=definition.source_revision_id, raw_unit=definition.raw_unit, normalized_unit=definition.normalized_unit,
            method_scope=definition.method_scope, multiplier=Decimal(definition.multiplier), additive=Decimal(definition.additive),
            exact_values_only=definition.exact_values_only)
        mappings.append(mapping)
    if len(mappings) != 1:
        return None, None, 'FIELD_UNIT_MAPPING_UNAVAILABLE'
    mapping = mappings[0]
    if mapping.exact_values_only and field.qualifier != 'eq':
        return None, None, 'FIELD_EXACT_VALUE_REQUIRED'
    definitions = [item for item in plan.mappings if item.mapping_id == mapping.id]
    if len(definitions) != 1:
        raise QualificationRejected('FIELD_UNIT_MAPPING_CONTENT_CHANGED')
    entry = definitions[0]
    if (entry.concept_code != mapping.concept_code or entry.source_revision_id != mapping.source_revision_id
            or entry.raw_unit != mapping.raw_unit or entry.normalized_unit != mapping.normalized_unit
            or entry.method_scope != mapping.method_scope or Decimal(entry.multiplier) != mapping.multiplier
            or Decimal(entry.additive) != mapping.additive or entry.exact_values_only != mapping.exact_values_only):
        raise QualificationRejected('FIELD_UNIT_MAPPING_CONTENT_CHANGED')
    revision = SourceRevision.objects.select_for_update().get(id=mapping.source_revision_id)
    source = SourceRegistry.objects.select_for_update().get(id=revision.source_id)
    if (revision.status != 'approved' or source.status != 'approved' or revision.captured_at > known_at
            or not revision.rights_evidence_ref or not revision.rights_evidence_ref.strip()):
        return None, None, 'FIELD_UNIT_MAPPING_SOURCE_UNAVAILABLE'
    if mapping.normalized_unit != variable.normalized_unit:
        return None, None, 'FIELD_UNIT_NOT_COMPARABLE'
    raw = Decimal(field.raw_numeric)
    if not raw.is_finite() or max(len(raw.as_tuple().digits), len(raw.as_tuple().digits) + raw.as_tuple().exponent,
            -raw.as_tuple().exponent) > maximum_digits:
        raise QualificationRejected('FIELD_DECIMAL_LIMIT_EXCEEDED')
    with localcontext() as context:
        context.prec = maximum_digits
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        value = raw * mapping.multiplier + mapping.additive
    if create_mapping:
        mapping.save(force_insert=True)
    saved = NormalizedMeasurement.objects.select_for_update().filter(field_id=field.id,
        patient_id=field.patient_id, mapping=mapping).first()
    if saved is None:
        saved = NormalizedMeasurement.objects.create(field_id=field.id, patient_id=field.patient_id, mapping=mapping,
            normalized_value=value, normalized_unit=mapping.normalized_unit, created_at=timezone.now())
    if saved.created_at > timezone.now() or value != saved.normalized_value or mapping.normalized_unit != saved.normalized_unit:
        raise QualificationRejected('FIELD_NORMALIZATION_CONTENT_CHANGED')
    return value, saved, None


@transaction.atomic
def qualify_fact(*, fact_id, patient, actor, auth_epoch, rule_id, window_policy_id, verification_policy_id,
        variable_code, environment, known_at, assessment_at, mode, context_policy_id=None, input_role='current', rule_schema='assessment-rule-2'):
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
    runtime = ConfigVersion.objects.select_for_update().filter(id=gate.runtime_config_id,
        kind='provider', schema_version='backend-runtime-1').first()
    if runtime is None:
        raise QualificationRejected('ASSESSMENT_RUNTIME_UNAVAILABLE')
    reason = configuration_qualification_reason(runtime, environment, audience='support')
    if reason:
        raise QualificationRejected(reason)
    maximum_digits = runtime.payload['limits']['max_decimal_digits']
    if input_role not in ('current', 'baseline', 'series', 'context') or rule_schema not in ('assessment-rule-2', 'dynamic-input-policy-2'):
        raise QualificationRejected('ASSESSMENT_INPUT_ROLE_INVALID')
    rule_config = qualified_configuration(rule_id, kind='rule', schema_version=rule_schema,
        environment=environment, known_at=known_at, mode=mode)
    rule = (StaticRulePolicy if rule_schema == 'assessment-rule-2' else DynamicInputPolicy).model_validate_json(json.dumps(rule_config.payload))
    window_config = qualified_configuration(window_policy_id, kind='window', schema_version='alert-window-1',
        environment=environment, known_at=known_at, mode=mode)
    if context_policy_id is None:
        if rule_schema == 'assessment-rule-2':
            variable = static_variable(rule, variable_code)
        else:
            variables = [item for item in rule.variables if item.variable_code == variable_code]
            if len(variables) != 1:
                raise QualificationRejected('ASSESSMENT_VARIABLE_REQUIRED')
            variable = variables[0]
    else:
        context_ids = {window_config.payload['scope']['population_config_id'], window_config.payload['scope']['therapy_policy_id'],
            window_config.payload['clinical_finding_validity_policy_id'], window_config.payload['late_result_policy_id']}
        if str(context_policy_id) not in context_ids:
            raise QualificationRejected('ASSESSMENT_CONTEXT_POLICY_MISMATCH')
        context_config = qualified_configuration(context_policy_id, kind='rule', schema_version='assessment-context-policy-2',
            environment=environment, known_at=known_at, mode=mode)
        context_policy = StaticContextPolicy.model_validate_json(json.dumps(context_config.payload))
        variables = [item for item in context_policy.conditions if item.variable_code == variable_code]
        if len(variables) != 1:
            raise QualificationRejected('ASSESSMENT_CONTEXT_VARIABLE_REQUIRED')
        variable = variables[0]
    windows = [item for item in window_config.payload['variables'] if item['variable_code'] == variable_code]
    if len(windows) != 1:
        raise QualificationRejected('ASSESSMENT_VARIABLE_WINDOW_REQUIRED')
    window = windows[0]
    policy_config = qualified_configuration(verification_policy_id, kind='verification', schema_version='qualification-policy-2',
        environment=environment, known_at=known_at, mode=mode)
    policy = QualificationPolicy.model_validate_json(json.dumps(policy_config.payload))
    if rule_schema == 'dynamic-input-policy-2' and rule.purpose != policy.purpose:
        raise QualificationRejected('ASSESSMENT_RULE_PURPOSE_MISMATCH')
    if context_policy_id is not None and context_policy.purpose != policy.purpose:
        raise QualificationRejected('ASSESSMENT_CONTEXT_PURPOSE_MISMATCH')
    comparison_id = variable.mapping_config_id if variable.value_type == 'decimal' else policy_config.id
    if window['comparison_policy_id'] != str(comparison_id):
        raise QualificationRejected('ASSESSMENT_COMPARISON_POLICY_MISMATCH')
    fact = ArchiveFactVersion.objects.select_for_update().get(id=fact_id, patient_id=patient.id)
    field = RecordField.objects.select_for_update().get(id=fact.field_id, patient_id=patient.id, record_version_id=fact.record_version_id)
    version = RecordVersion.objects.select_for_update().get(id=field.record_version_id, patient_id=patient.id)
    record = Record.objects.select_for_update().get(id=version.record_id, patient_id=patient.id)
    logical = ArchiveFact.objects.select_for_update().get(id=fact.fact_id, patient_id=patient.id)
    if (fact.medical_time_id != field.medical_time_id or fact.source_anchor_id != field.anchor_id
            or fact.confirmation_id != field.confirmation_id or field.concept_code != variable.concept_code):
        raise QualificationRejected('FACT_REFERENCE_CHANGED')
    grants = lock_case_permissions(actor, patient, 'execute_assessment', policy.purpose, timezone.now(),
        record_id=record.id, author_id=version.author_id, field_codes=[field.field_code])
    reference = {'fact_version_id': str(fact.id), 'field_id': str(field.id), 'record_version_id': str(version.id),
        'patient_id': str(patient.id), 'verification_id': None, 'normalization_id': None,
        'medical_time_id': str(fact.medical_time_id) if fact.medical_time_id else None,
        'source_revision_id': str(version.source_revision_id) if version.source_revision_id else None,
        'source_anchor_id': str(fact.source_anchor_id) if fact.source_anchor_id else None,
        'document_version_id': str(fact.document_version_id) if fact.document_version_id else None,
        'page_id': str(fact.page_id) if fact.page_id else None, 'qualifier': field.qualifier, 'method': field.method,
        'context_type': fact.context_type, 'assertion_state': fact.assertion_state,
        'entity_refs': [entity_reference(value) for value in (fact, field, version)],
        'access_grant_ids': [str(grant.id) for grant in grants],
        'available_at': fact.available_at.isoformat() if fact.available_at else None, 'availability_status': fact.availability_status}
    reasons = fact_chronology_reasons(fact, version, known_at=known_at, accepted_context_types=variable.accepted_context_types)
    if record.status != 'active' or version.quality_status != 'valid' or version.origin_type not in policy.allowed_origin_types:
        reasons.append('FACT_RECORD_INELIGIBLE')
    if mode == 'current' and (logical.current_version != fact.version or record.current_version != version.version):
        reasons.append('FACT_VERSION_SUPERSEDED')
    if mode == 'replay' and (ArchiveFactVersion.objects.filter(fact_id=fact.fact_id, patient_id=patient.id,
            created_at__lte=known_at, version__gt=fact.version).exists()
            or RecordVersion.objects.filter(record_id=record.id, patient_id=patient.id,
                recorded_at__lte=known_at, version__gt=version.version).exists()):
        reasons.append('FACT_VERSION_SUPERSEDED_BY_CUTOFF')
    if reasons:
        return QualifiedFact(reference, None, None, tuple(reasons))
    if field.anchor_id is None or version.source_revision_id is None:
        return QualifiedFact(reference, None, None, ('FACT_SOURCE_REQUIRED',))
    anchor = SourceAnchor.objects.select_for_update().get(id=field.anchor_id, patient_id=patient.id)
    page = FilePage.objects.select_for_update().get(id=anchor.page_id, patient_id=patient.id)
    original = lock_original(page.file_id)
    group = FileGroup.objects.select_for_update().get(id=original.file_group_id, patient_id=patient.id)
    lock_file_access(original, actor, auth_epoch, purpose=policy.purpose, service_action='execute_assessment')
    authorization = lock_use_authorization(version.use_authorization_id, original, policy.purpose, environment)
    revision = SourceRevision.objects.select_for_update().get(id=authorization.source_revision_id)
    if (authorization.source_revision_id != version.source_revision_id or authorization.approved_at > known_at
            or revision.captured_at > known_at
            or not RecordAttachment.objects.filter(record_version_id=version.id, patient_id=patient.id, file_id=original.id).exists()):
        return QualifiedFact(reference, None, None, ('FACT_SOURCE_AUTHORIZATION_CHANGED',))
    checked_bytes(original)
    document_refs, reason = document_provenance(fact, page, original, known_at=known_at, mode=mode)
    reference['entity_refs'].extend(document_refs)
    if reason:
        return QualifiedFact(reference, None, None, (reason,))
    reference['entity_refs'].extend(entity_reference(value) for value in (anchor, page, original, group, authorization, revision))
    if field.confirmation_id is not None:
        confirmation = CandidateConfirmation.objects.select_for_update().get(id=field.confirmation_id, patient_id=patient.id)
        if confirmation.decision not in ('accepted', 'modified') or confirmation.confirmed_at > known_at:
            return QualifiedFact(reference, None, None, ('FACT_CONFIRMATION_INELIGIBLE',))
        candidate = CandidateField.objects.select_for_update().get(id=confirmation.candidate_field_id, patient_id=patient.id)
        candidate_set = CandidateSet.objects.select_for_update().get(id=candidate.candidate_set_id, patient_id=patient.id)
        if (candidate.anchor_id != field.anchor_id or candidate_set.file_group_id != original.file_group_id
                or candidate_set.file_group_version != group.version
                or candidate_set.created_at > known_at or candidate_set.status != 'confirmed'):
            return QualifiedFact(reference, None, None, ('FACT_CONFIRMATION_SOURCE_CHANGED',))
        reference['entity_refs'].extend(entity_reference(value) for value in (confirmation, candidate, candidate_set))
    elif version.capture_method in ('ocr_confirmed', 'voice_confirmed'):
        return QualifiedFact(reference, None, None, ('FACT_CONFIRMATION_REQUIRED',))
    verification, reason = verification_proof(field, version, policy_config, policy, environment=environment, known_at=known_at, mode=mode)
    if reason:
        return QualifiedFact(reference, None, None, (reason,))
    reference['verification_id'] = str(verification.id)
    proof_field = VerificationField.objects.select_for_update().get(verification_id=verification.id,
        field_id=field.id, patient_id=patient.id, record_version_id=version.id)
    reference['entity_refs'].extend(entity_reference(value) for value in (verification, proof_field))
    if fact.medical_time_id is None:
        return QualifiedFact(reference, None, None, ('FACT_MEDICAL_TIME_REQUIRED',))
    medical = MedicalTime.objects.select_for_update().get(id=fact.medical_time_id, patient_id=patient.id)
    reference['entity_refs'].append(entity_reference(medical))
    if medical.precision not in window['accepted_precision']:
        return QualifiedFact(reference, None, None, ('MEDICAL_TIME_PRECISION_NOT_APPROVED',))
    if (medical.resolution_status in ('unknown', 'ambiguous', 'invalid')
            or medical.precision != 'date' and (medical.resolution_status != 'resolved' or medical.instant is None)):
        return QualifiedFact(reference, None, None, ('MEDICAL_TIME_UNRESOLVED',))
    if medical.timezone_name is None and medical.utc_offset_minutes is None:
        return QualifiedFact(reference, None, None, ('MEDICAL_TIME_ZONE_REQUIRED',))
    interval = medical_interval(medical)
    timing = window if input_role in ('current', 'context') else {**window, 'current_max_age_seconds': window['baseline_max_age_seconds']}
    reason = window_reason(interval, assessment_at, timing)
    if reason:
        return QualifiedFact(reference, interval, None, (reason,))
    if variable.value_type == 'categorical':
        states = [item.assertion_state for item in variable.categorical_values if item.value == field.categorical_value]
        if len(states) != 1:
            return QualifiedFact(reference, interval, None, ('FIELD_CATEGORICAL_STATE_UNKNOWN',))
        expected_assertion = states[0]
    else:
        expected_assertion = 'present'
    if fact.assertion_state != expected_assertion:
        return QualifiedFact(reference, interval, None, ('FACT_ASSERTION_VALUE_INCONSISTENT',))
    value, normalization, reason = normalized_input(field, variable, source_revision_id=version.source_revision_id, environment=environment,
        known_at=known_at, mode=mode, maximum_digits=maximum_digits, exact_required=rule_schema == 'dynamic-input-policy-2' or rule.kind == 'ms')
    if reason:
        return QualifiedFact(reference, interval, None, (reason,))
    reference['normalization_id'] = str(normalization.id) if normalization else None
    reference['normalized_unit'] = variable.normalized_unit
    if normalization is not None:
        reference['entity_refs'].append(entity_reference(normalization))
        mapping = UnitMapping.objects.select_for_update().get(id=normalization.mapping_id)
        reference['entity_refs'].append(entity_reference(mapping))
        reference['entity_refs'].append(entity_reference(ConfigVersion.objects.select_for_update().get(id=mapping.config_id)))
    reference['medical_interval'] = interval.snapshot()
    reference['medical_precision'] = medical.precision
    reference['value_sha256'] = compute_checksum({'value': str(value), 'unit': variable.normalized_unit,
        'qualifier': field.qualifier, 'assertion_state': fact.assertion_state})
    reference['reference_sha256'] = compute_checksum(reference)
    return QualifiedFact(reference, interval, value, ())
