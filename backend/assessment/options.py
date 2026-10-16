import json
from itertools import product
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from archives.models import ArchiveFactVersion, ObservationPeriodVersion
from assessment.configuration_proofs import configuration_proof
from assessment.hlh_contracts import HLHInputPolicy, HLHRulePolicy, validate_hlh_policy_pair
from assessment.hlh_inputs import validate_hlh_windows
from assessment.policies import QualificationPolicy, StaticContextPolicy, StaticRulePolicy, static_variable
from assessment.qualified_facts import QualificationRejected, configuration_evidence_refs, lock_case_permissions
from assessment.service import TIMESTAMP, lock_actor
from authorization.models import AccessGrant
from authorization.policy import check_principal_session, check_service_grant, matching_case_grants
from configurations.evidence import deployed_artifact_checksum, inspect_approved_evidence
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from records.models import Record, RecordField, RecordVersion
from records.service import medical_time_view
from relations.models import Relation
from sources.models import DataUseAuthorization, SourceRevision
from tasks.models import Task
from requests.models import WriteRequest
from verification.workflow import permitted_fields


def configuration_view(row):
    return {'config_id': str(row.id), 'config_key': row.config_key, 'version': str(row.version), 'checksum': row.checksum}


def directory_record_view(record, version, purpose):
    return {'record_id': str(record.id), 'record_version_id': str(version.id), 'version': str(version.version),
        'kind': record.kind, 'source_organization': version.source_organization,
        'recorded_at': version.recorded_at.isoformat(), 'purpose': purpose}


def selection_candidate_view(fact, field, patient):
    return {'fact_version_id': str(fact.id), 'field_id': str(field.id), 'record_version_id': str(field.record_version_id),
        'fact_version': str(fact.version), 'original_name': field.original_name,
        'raw_value': field.raw_text if field.raw_text is not None else format(field.raw_numeric, 'f') if field.raw_numeric is not None else field.categorical_value,
        'raw_unit': field.raw_unit, 'qualifier': field.qualifier, 'context_type': fact.context_type,
        'medical_time': medical_time_view(fact.medical_time_id, patient) if fact.medical_time_id else None,
        'available_at': fact.available_at.isoformat() if fact.available_at else None}


def selection_options(patient, actor, rule_row, window_row, policy_row, variables, known_at, input_rule_row=None):
    windows = {item['variable_code']: item for item in window_row.payload['variables']}
    result = []
    for variable in variables:
        if windows[variable.variable_code]['current_selection'] != 'explicit_clinical_selection':
            continue
        candidates = []
        for fact in ArchiveFactVersion.objects.filter(patient_id=patient.id, created_at__lte=known_at,
                field_id__in=RecordField.objects.filter(patient=patient, concept_code=variable.concept_code).values('id')).order_by('id'):
            field = RecordField.objects.get(id=fact.field_id, patient=patient)
            version = RecordVersion.objects.get(id=fact.record_version_id, patient=patient)
            authorization = DataUseAuthorization.objects.select_for_update().get(id=version.use_authorization_id, patient=patient)
            if (field.record_version_id != version.id or authorization.purpose != policy_row.payload['purpose']
                    or authorization.approved_at > known_at or authorization.revoked_at is not None
                    or authorization.valid_until is not None and authorization.valid_until <= timezone.now()
                    or not permitted_fields(actor, patient, version, authorization.purpose, [field])):
                continue
            candidates.append(selection_candidate_view(fact, field, patient))
        result.append({'variable_code': variable.variable_code, 'concept_code': variable.concept_code, 'candidates': candidates})
    evidence = []
    policy = QualificationPolicy.model_validate(policy_row.payload)
    for source in SourceRevision.objects.filter(status='approved', captured_at__lte=known_at).order_by('id'):
        reference = f'urn:sjia:source-revision:{source.id}'
        if policy.purpose not in source.allowed_use:
            continue
        if inspect_approved_evidence(reference, purpose=policy.verification_evidence_purpose, lock=True)[2] is None:
            evidence.append(reference)
    prior = []
    for task in Task.objects.filter(patient=patient, task_type='assessment', schema_version='static-assessment-task-2',
            status='completed', created_at__lte=known_at, input_refs__payload__mode='current',
            input_refs__payload__rule_id=str(rule_row.id), input_refs__payload__window_policy_id=str(window_row.id),
            input_refs__payload__verification_policy_id=str(policy_row.id)).order_by('created_at', 'id'):
        if input_rule_row is not None and task.input_refs['payload'].get('input_rule_id') != str(input_rule_row.id):
            continue
        if task.request_row_id is None:
            continue
        saved = WriteRequest.objects.get(id=task.request_row_id, patient=patient)
        if saved.status != 'committed' or saved.completed_at is None or saved.completed_at > known_at:
            continue
        choices = [item['choice'] for item in task.input_refs['snapshot'].get('clinical_selections', [])]
        if choices:
            prior.append({'task_id': str(task.id), 'saved_at': saved.completed_at.isoformat(), 'choices': choices})
    return result, evidence, prior


def limited_json(value, runtime):
    if len(json.dumps(value, separators=(',', ':')).encode('utf-8')) > runtime.payload['api']['max_json_bytes']:
        raise QualificationRejected('ASSESSMENT_CONTEXT_LIMIT_EXCEEDED')
    return value


@transaction.atomic
def clinical_directory(actor, auth_epoch, environment, action):
    if action not in ('verify_field', 'execute_assessment'):
        raise QualificationRejected('ASSESSMENT_DIRECTORY_ACTION_INVALID')
    gate = EnvironmentGate.objects.select_for_update().get(environment=environment)
    actor = Principal.objects.select_for_update().get(id=actor.id)
    check_principal_session(actor, auth_epoch)
    if not gate.enabled:
        raise QualificationRejected('ASSESSMENT_ENVIRONMENT_UNAVAILABLE')
    check_service_grant(actor, action, environment, timezone.now())
    runtime = ConfigVersion.objects.select_for_update().filter(id=gate.runtime_config_id,
        kind='provider', schema_version='backend-runtime-1').first()
    if runtime is None:
        raise QualificationRejected('ASSESSMENT_RUNTIME_UNAVAILABLE')
    reason = configuration_qualification_reason(runtime, environment, audience='support')
    if reason:
        raise QualificationRejected(reason)
    grants = list(AccessGrant.objects.select_for_update().filter(principal=actor, action=action).order_by('id'))
    list(Relation.objects.select_for_update().filter(id__in=[row.relation_id for row in grants if row.relation_id is not None]).order_by('id'))
    patients = []
    for patient in Patient.objects.select_for_update().filter(status='active', id__in=[row.patient_id for row in grants]).order_by('id'):
        records = []
        purposes = set()
        if action == 'execute_assessment':
            purposes = {grant.purpose for grant in matching_case_grants(actor, patient, action, timezone.now(), whole_resource=True)
                if grant.evidence_ref.strip()}
        else:
            for record in Record.objects.select_for_update().filter(patient=patient, status='active').order_by('id'):
                version = RecordVersion.objects.select_for_update().get(patient=patient, record_id=record.id, version=record.current_version)
                fields = list(RecordField.objects.filter(patient=patient, record_version_id=version.id).order_by('ordinal', 'id'))
                purpose = DataUseAuthorization.objects.select_for_update().get(id=version.use_authorization_id, patient=patient).purpose
                if permitted_fields(actor, patient, version, purpose, fields):
                    records.append(directory_record_view(record, version, purpose))
                    purposes.add(purpose)
        if purposes:
            patients.append({'patient_id': str(patient.id), 'display_id': patient.display_id,
                'purposes': sorted(purposes), 'records': records})
    return limited_json({'schema_version': 'clinical-directory-2', 'action': action, 'patients': patients}, runtime)


def approved_configurations(environment, known_at, mode, additional_kinds=()):
    result = {}
    kinds = (('rule', 'assessment-rule-2'), ('rule', 'hlh-2004-rule-1'), ('rule', 'hlh-input-policy-1'), ('window', 'alert-window-1'),
        ('verification', 'qualification-policy-2'), ('rule', 'assessment-context-policy-2'), ('mapping', 'unit-mapping-2'), *additional_kinds)
    rows = [row for kind, schema in kinds for row in ConfigVersion.objects.select_for_update().filter(
        kind=kind, schema_version=schema, created_at__lte=known_at).order_by('id')]
    if not rows:
        return result
    artifact = deployed_artifact_checksum(rows[0])
    for row in rows:
        points = (known_at, timezone.now())
        if any(configuration_qualification_reason(row, environment, now=point, audience='support',
                allow_historical=mode == 'replay', artifact_sha256=artifact) for point in points):
            continue
        if any(inspect_approved_evidence(reference, purpose='clinical_policy', lock=True, now=point)[2]
                for point in points for reference in configuration_evidence_refs(row)):
            continue
        configuration_proof(row, environment, known_at, mode, artifact_sha256=artifact)
        result[row.id] = row
    if deployed_artifact_checksum(rows[0]) != artifact:
        raise QualificationRejected('ASSESSMENT_IMPLEMENTATION_CHANGED')
    return result


@transaction.atomic
def assessment_creation_options(patient_id, actor, auth_epoch, environment, query):
    known_at = TIMESTAMP.validate_python(query['known_at'])
    if known_at.utcoffset() is None or known_at > timezone.now() or query['mode'] not in ('current', 'replay'):
        raise QualificationRejected('ASSESSMENT_CONTEXT_INVALID')
    _, patient, actor, _, runtime = lock_actor(patient_id, actor, auth_epoch, environment, 'execute_assessment')
    purposes = {grant.purpose for grant in matching_case_grants(actor, patient, 'execute_assessment', timezone.now(), whole_resource=True)
        if grant.evidence_ref.strip()}
    if not purposes:
        raise QualificationRejected('ASSESSMENT_FIELD_SCOPE_DENIED')
    for purpose in purposes:
        lock_case_permissions(actor, patient, 'execute_assessment', purpose, timezone.now(), whole_resource=True)
    configurations = approved_configurations(environment, known_at, query['mode'])
    rules = [row for row in configurations.values() if row.schema_version == 'assessment-rule-2']
    windows = [row for row in configurations.values() if row.schema_version == 'alert-window-1']
    policies = [row for row in configurations.values() if row.schema_version == 'qualification-policy-2']
    pairs = [(row, None) for row in rules]
    pairs.extend(product([row for row in configurations.values() if row.schema_version == 'hlh-2004-rule-1'],
        [row for row in configurations.values() if row.schema_version == 'hlh-input-policy-1']))
    plans = []
    for (rule_row, input_rule_row), window_row, policy_row in product(pairs, windows, policies):
        hlh = input_rule_row is not None
        rule = (HLHRulePolicy if hlh else StaticRulePolicy).model_validate_json(json.dumps(rule_row.payload))
        bindings = HLHInputPolicy.model_validate_json(json.dumps(input_rule_row.payload)) if hlh else None
        if hlh:
            try:
                validate_hlh_policy_pair(rule, bindings)
                validate_hlh_windows(rule, window_row.payload)
            except (ValueError, QualificationRejected):
                continue
        policy = QualificationPolicy.model_validate(policy_row.payload)
        if policy.purpose not in purposes or hlh and bindings.purpose != policy.purpose:
            continue
        context_ids = {'population': window_row.payload['scope']['population_config_id'],
            'therapy': window_row.payload['scope']['therapy_policy_id'],
            'clinical_finding': window_row.payload['clinical_finding_validity_policy_id'],
            'late_result': window_row.payload['late_result_policy_id']}
        contexts = [configurations.get(UUID(value)) for value in context_ids.values()]
        if any(row is None or row.schema_version != 'assessment-context-policy-2' for row in contexts):
            continue
        definitions = [StaticContextPolicy.model_validate(row.payload) for row in contexts]
        if any(definition.role != role or definition.purpose != policy.purpose
                for role, definition in zip(context_ids, definitions)):
            continue
        variables = (list(bindings.variables) if hlh else
            [static_variable(rule, code) for code in ['sjia', 'fever', *(item.variable_code for item in rule.variables)]])
        variables.extend(item for definition in definitions for item in definition.conditions)
        if (len({item.variable_code for item in variables}) != len(variables)
                or len({item.concept_code for item in variables}) != len(variables)
                or not {item.variable_code for item in variables} <= {item['variable_code'] for item in window_row.payload['variables']}
                or any(item.mapping_config_id is not None and item.mapping_config_id not in configurations for item in variables)):
            continue
        selection_variables, selection_evidence, prior_selections = selection_options(patient, actor, rule_row,
            window_row, policy_row, variables, known_at, input_rule_row)
        plan = {'kind': 'hlh' if hlh else rule.kind, 'purpose': policy.purpose, 'rule': configuration_view(rule_row),
            'window': configuration_view(window_row), 'verification': configuration_view(policy_row),
            'selection_variables': selection_variables, 'selection_evidence': selection_evidence, 'prior_selections': prior_selections}
        if hlh:
            plan['input_rule'] = configuration_view(input_rule_row)
        plans.append(plan)
    periods = []
    latest = set()
    for row in ObservationPeriodVersion.objects.filter(patient_id=patient.id, created_at__lte=known_at).order_by('period_id', '-version'):
        if row.period_id in latest:
            continue
        latest.add(row.period_id)
        if query['mode'] == 'current' and ObservationPeriodVersion.objects.filter(patient_id=patient.id,
                period_id=row.period_id, version__gt=row.version).exists():
            continue
        periods.append({'observation_period_id': str(row.id), 'period_id': str(row.period_id), 'version': str(row.version),
            'label': row.label, 'start_time': medical_time_view(row.start_time_id, patient), 'end_time': medical_time_view(row.end_time_id, patient)})
    return limited_json({'schema_version': 'assessment-creation-options-2', 'patient_id': str(patient.id),
        'display_id': patient.display_id, 'known_at': known_at.isoformat(), 'mode': query['mode'],
        'plans': plans, 'observation_periods': periods}, runtime)
