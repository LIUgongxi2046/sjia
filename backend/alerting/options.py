import json
from itertools import product
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from alerting.policies import DynamicInputPolicy, LifecyclePolicy
from alerting.inputs import condition_variable
from archives.models import ArchiveFactVersion, ObservationPeriodVersion
from assessment.options import approved_configurations, configuration_view, limited_json, selection_candidate_view
from assessment.policies import QualificationPolicy, StaticContextPolicy
from assessment.qualified_facts import QualificationRejected, lock_case_permissions
from assessment.service import TIMESTAMP, lock_actor
from authorization.policy import matching_case_grants
from configurations.evidence import inspect_approved_evidence
from records.models import RecordField, RecordVersion
from records.service import medical_time_view
from identity.models import Principal
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision
from tasks.models import Task
from verification.workflow import permitted_fields


def selections(patient, actor, variables, window, professional, known_at, policy, input_rule):
    windows = {item['variable_code']: item for item in window.payload['variables']}
    result = []
    for variable in variables:
        roles = [role for role, value in (('current', 'explicit_clinical_selection'), ('baseline', 'explicit_clinical_baseline'))
            if windows[variable.variable_code][role + '_selection'] == value]
        if not roles:
            continue
        candidates = []
        for fact in ArchiveFactVersion.objects.filter(patient_id=patient.id, created_at__lte=known_at,
                field_id__in=RecordField.objects.filter(patient=patient, concept_code=variable.concept_code).values('id')).order_by('id'):
            field = RecordField.objects.get(id=fact.field_id, patient=patient)
            version = RecordVersion.objects.get(id=fact.record_version_id, patient=patient)
            authorization = DataUseAuthorization.objects.get(id=version.use_authorization_id, patient=patient)
            if (field.record_version_id != version.id or authorization.purpose != professional.payload['purpose']
                    or authorization.approved_at > known_at or authorization.revoked_at is not None
                    or authorization.valid_until is not None and authorization.valid_until <= timezone.now()
                    or not permitted_fields(actor, patient, version, authorization.purpose, [field])):
                continue
            candidates.append(selection_candidate_view(fact, field, patient))
        for role in roles:
            result.append({'variable_code': variable.variable_code, 'input_role': role,
                'concept_code': variable.concept_code, 'candidates': candidates})
    evidence = []
    definition = QualificationPolicy.model_validate(professional.payload)
    for source in SourceRevision.objects.filter(status='approved', captured_at__lte=known_at).order_by('id'):
        reference = f'urn:sjia:source-revision:{source.id}'
        if definition.purpose in source.allowed_use and inspect_approved_evidence(reference,
                purpose=definition.verification_evidence_purpose, lock=True)[2] is None:
            evidence.append(reference)
    prior = []
    for task in Task.objects.filter(patient=patient, task_type='alert_evaluation', schema_version='dynamic-evaluation-task-2',
            status='completed', created_at__lte=known_at, input_refs__payload__mode='current', input_refs__payload__policy_id=str(policy.id),
            input_refs__payload__input_rule_id=str(input_rule.id), input_refs__payload__window_policy_id=str(window.id),
            input_refs__payload__verification_policy_id=str(professional.id)).order_by('created_at', 'id'):
        saved = task.request_row
        if saved is None or saved.status != 'committed' or saved.completed_at is None or saved.completed_at > known_at:
            continue
        choices = [{**item['choice'], 'input_role': item['input_role']} for item in task.input_refs['snapshot']['clinical_selections']]
        if choices:
            prior.append({'task_id': str(task.id), 'saved_at': saved.completed_at.isoformat(), 'choices': choices})
    return result, evidence, prior


@transaction.atomic
def dynamic_creation_options(patient_id, actor, auth_epoch, environment, query):
    known_at = TIMESTAMP.validate_python(query['known_at'])
    if known_at.utcoffset() is None or known_at > timezone.now() or query['mode'] not in ('current', 'replay'):
        raise QualificationRejected('DYNAMIC_CONTEXT_INVALID')
    _, patient, actor, _, runtime = lock_actor(patient_id, actor, auth_epoch, environment, 'execute_assessment')
    purposes = {grant.purpose for grant in matching_case_grants(actor, patient, 'execute_assessment', timezone.now(), whole_resource=True)
        if grant.evidence_ref.strip()}
    if not purposes:
        raise QualificationRejected('DYNAMIC_FIELD_SCOPE_DENIED')
    for purpose in purposes:
        lock_case_permissions(actor, patient, 'execute_assessment', purpose, timezone.now(), whole_resource=True)
    configs = approved_configurations(environment, known_at, query['mode'],
        (('rule', 'alert-trigger-1'), ('rule', 'dynamic-input-policy-2'), ('rule', 'alert-lifecycle-2')))
    rules = [row for row in configs.values() if row.schema_version == 'alert-trigger-1']
    inputs = [row for row in configs.values() if row.schema_version == 'dynamic-input-policy-2']
    policies = [row for row in configs.values() if row.schema_version == 'qualification-policy-2']
    plans = []
    for rule, input_rule, professional in product(rules, inputs, policies):
        definition = DynamicInputPolicy.model_validate_json(json.dumps(input_rule.payload))
        purpose = professional.payload['purpose']
        window = configs.get(UUID(rule.payload['window_policy_id']))
        if purpose not in purposes or purpose != definition.purpose or window is None or window.schema_version != 'alert-window-1':
            continue
        ids = {'population': window.payload['scope']['population_config_id'], 'therapy': window.payload['scope']['therapy_policy_id'],
            'clinical_finding': window.payload['clinical_finding_validity_policy_id'], 'late_result': window.payload['late_result_policy_id']}
        context_rows = [configs.get(UUID(value)) for value in ids.values()]
        if any(row is None or row.schema_version != 'assessment-context-policy-2' for row in context_rows):
            continue
        contexts = [StaticContextPolicy.model_validate(row.payload) for row in context_rows]
        if any(value.purpose != purpose or value.role != role for value, role in zip(contexts, ids)):
            continue
        lifecycles = [configs.get(UUID(rule.payload['episode'][role + '_policy_id'])) for role in ('reset', 'retrigger')]
        if any(row is None or row.schema_version != 'alert-lifecycle-2' for row in lifecycles):
            continue
        definitions = [LifecyclePolicy.model_validate(row.payload) for row in lifecycles]
        if any(value.purpose != purpose or value.kind != role for value, role in zip(definitions, ('reset', 'retrigger'))):
            continue
        variables = [*definition.variables, *(item for value in contexts for item in value.conditions)]
        codes = {item.variable_code for item in variables}
        if (len(codes) != len(variables) or len({item.concept_code for item in variables}) != len(variables)
                or not codes <= {item['variable_code'] for item in window.payload['variables']}
                or rule.payload['scope'] != window.payload['scope']
                or any(item.mapping_config_id is not None and item.mapping_config_id not in configs for item in variables)):
            continue
        conditions = [*rule.payload['conditions'], *(item for value in definitions for item in value.conditions)]
        if any(condition_variable(item) is not None and condition_variable(item) not in {v.variable_code for v in definition.variables}
                or item['kind'] in ('classification_met', 'ms_threshold_met') and UUID(item['assessment_rule_id']) not in configs
                or item['kind'] == 'professional_finding' and (UUID(item['verification_policy_id']) not in configs
                    or configs[UUID(item['verification_policy_id'])].payload['purpose'] != purpose) for item in conditions):
            continue
        authorization_ids = []
        for authorization in DataUseAuthorization.objects.select_for_update().filter(patient=patient, purpose=purpose, environment=environment,
                approved_at__lte=known_at, revoked_at__isnull=True).order_by('id'):
            source = SourceRevision.objects.select_for_update().get(id=authorization.source_revision_id)
            registry = SourceRegistry.objects.select_for_update().get(id=source.source_id)
            approver = Principal.objects.select_for_update().get(id=authorization.approved_by_id)
            retention = RetentionPolicy.objects.select_for_update().get(id=source.retention_policy_id) if source.retention_policy_id else None
            if (authorization.valid_until is not None and authorization.valid_until <= timezone.now() or not authorization.evidence_ref.strip()
                    or approver.account_status != 'active' or source.status != 'approved' or registry.status != 'approved'
                    or source.captured_at > known_at or purpose not in source.allowed_use or not source.license.strip()
                    or not source.rights_evidence_ref or not source.rights_evidence_ref.strip()
                    or retention is None or retention.status != 'active'):
                continue
            authorization_ids.append(str(authorization.id))
        if not authorization_ids:
            continue
        variables, evidence, prior = selections(patient, actor, variables, window, professional, known_at, rule, input_rule)
        plans.append({'kind': 'dynamic', 'purpose': purpose, 'rule': configuration_view(rule), 'input_rule': configuration_view(input_rule),
            'window': configuration_view(window), 'verification': configuration_view(professional), 'use_authorization_ids': authorization_ids,
            'selection_variables': variables, 'selection_evidence': evidence, 'prior_selections': prior})
    periods, seen = [], set()
    for row in ObservationPeriodVersion.objects.filter(patient_id=patient.id, created_at__lte=known_at).order_by('period_id', '-version'):
        if row.period_id in seen:
            continue
        seen.add(row.period_id)
        if query['mode'] == 'current' and ObservationPeriodVersion.objects.filter(patient_id=patient.id, period_id=row.period_id, version__gt=row.version).exists():
            continue
        periods.append({'observation_period_id': str(row.id), 'period_id': str(row.period_id), 'version': str(row.version),
            'label': row.label, 'start_time': medical_time_view(row.start_time_id, patient), 'end_time': medical_time_view(row.end_time_id, patient)})
    return limited_json({'schema_version': 'dynamic-creation-options-2', 'patient_id': str(patient.id), 'display_id': patient.display_id,
        'known_at': known_at.isoformat(), 'mode': query['mode'], 'plans': plans, 'observation_periods': periods}, runtime)
