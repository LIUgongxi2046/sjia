"""Real synthetic static/dynamic replay and evidence-adoption prerequisites.

The frozen dataset boundary is represented explicitly by immutable namespace
inputs in these adoption tests; no claim of full purpose-copy/dataset API flow.
All source files, configuration qualification and replay tasks are real local
software-test artifacts. No model, clinical data or provider is called.
"""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
from django.utils import timezone
from alerting.models import AlertEvaluation
from alerting.service import create_dynamic_task, execute_dynamic_evaluation, read_dynamic_evaluation
from archives.models import ArchiveFactVersion, ObservationPeriodVersion
from assessment.models import Assessment
from assessment.policies import CLASSIFICATION_VARIABLES, DECIMAL_UNITS
from assessment.service import create_assessment_task, execute_static_assessment, read_assessment
from authorization.models import AccessGrant
from case_details.clinical_adoption import fact_reference_digest, stable_result_digest
from case_details.clinical_metrics_v2 import ClinicalCaptureV2, corpus_digest
from case_details.clinical_metrics import Event, Prompt
from configurations.models import ConfigVersion
from medical_time.models import MedicalTime
from records.models import RecordField, RecordVersion
from records.service import create_record
from tasks.models import OutboxEvent
from verification.models import Verification, VerificationField, FieldVerificationState
from tests.synthetic_dynamic import synthetic_dynamic_native


def qualified_replay_capture(case, *, applicable=True, interval_factory=None, prepare_only=False,
        shared_base=None, observation_padding_minutes=0):
    base = synthetic_dynamic_native(case, purpose='clinical_evaluation', execute_current=False, shared=shared_base)
    fx, actor, patient, configs = base.fixture, base.actor, base.patient, base.configs
    purpose = 'clinical_evaluation'
    codes = sorted(CLASSIFICATION_VARIABLES | {'sjia', 'fever'})
    for action in ('execute_assessment', 'verify_field', 'read_raw'):
        AccessGrant.objects.create(patient=patient, principal=actor, relation_id=base.relation.id, action=action,
            field_codes=codes, record_scope='all', purpose=purpose, granted_by=actor,
            evidence_ref=fx.reference, valid_from=timezone.now())
    mapping = fx.configuration('mapping', {'schema_version': 'unit-mapping-2', 'evidence_refs': [fx.reference],
        'mappings': [{'mapping_id': str(uuid4()), 'concept_code': code, 'source_revision_id': str(fx.source.id),
            'raw_unit': DECIMAL_UNITS[code], 'normalized_unit': DECIMAL_UNITS[code], 'method_scope': 'synthetic',
            'multiplier': '1', 'additive': '0', 'exact_values_only': True} for code in sorted(CLASSIFICATION_VARIABLES)]})
    binary = [{'value': 'yes', 'encoded': 1, 'assertion_state': 'present'}, {'value': 'no', 'encoded': 0, 'assertion_state': 'absent'}]
    rule = fx.configuration('rule', {'schema_version': 'assessment-rule-2', 'kind': 'classification',
        'sjia': {'concept_code': 'sjia', 'states': binary, 'accepted_context_types': ['current']},
        'fever': {'concept_code': 'fever', 'states': binary, 'accepted_context_types': ['current']},
        'variables': [{'variable_code': code, 'concept_code': code, 'value_type': 'decimal',
            'normalized_unit': DECIMAL_UNITS[code], 'mapping_config_id': str(mapping.id), 'categorical_values': [],
            'accepted_context_types': ['current']} for code in sorted(CLASSIFICATION_VARIABLES)], 'evidence_refs': [fx.reference]})
    window_payload = deepcopy(configs['window'].payload)
    window_payload['variables'] = [{**window_payload['variables'][0], 'variable_code': code,
        'comparison_policy_id': str(mapping.id if code in CLASSIFICATION_VARIABLES else configs['verification'].id)} for code in codes]
    window = fx.configuration('window', window_payload)
    measured = (timezone.now() - timedelta(minutes=1)).replace(microsecond=0)
    numeric = {'ferritin': '1000', 'platelet_count': '100', 'ast': '60', 'triglycerides': '200', 'fibrinogen': '200'}
    fields = []
    for code in codes:
        value = {'field_code': code, 'concept_code': code, 'value_state': 'known',
            'source_file_id': str(base.file.id), 'source_text': 'SYNTHETIC SOFTWARE TEST', 'sample_time_origin': 'observation',
            'medical_time': {'precision': 'second', 'local_datetime': measured.replace(tzinfo=None).isoformat(),
                'timezone_name': 'UTC', 'resolution_status': 'local_only'}}
        value.update({'value_type': 'decimal', 'raw_numeric': numeric[code], 'raw_unit': DECIMAL_UNITS[code],
            'method': 'synthetic', 'qualifier': 'eq'} if code in numeric else {'value_type': 'categorical', 'categorical_value': 'no' if code == 'fever' and not applicable else 'yes'})
        fields.append(value)
    created = create_record(patient=patient, actor=actor, kind='observation', capture_method='manual',
        origin_type='self_report', retention_policy=fx.retention, use_authorization_id=base.authorization.id,
        source_revision=fx.source, file_ids=[base.file.id], fields=fields)
    version = RecordVersion.objects.get(id=created['record_version_id'])
    verification = Verification.objects.create(patient=patient, record_version_id=version.id, verifier=actor,
        policy=configs['verification'], purpose=purpose, evidence_source=fx.source, evidence_ref=fx.reference, verified_at=timezone.now())
    for field in RecordField.objects.filter(record_version_id=version.id):
        ArchiveFactVersion.objects.filter(field_id=field.id).update(context_type='current',
            assertion_state='absent' if field.categorical_value == 'no' else 'present')
        VerificationField.objects.create(verification_id=verification.id, record_version_id=version.id,
            patient_id=patient.id, field_id=field.id, result='consistent', reason='synthetic fixture')
        FieldVerificationState.objects.create(field_id=field.id, record_version_id=version.id, patient_id=patient.id,
            purpose=purpose, version=1, current_verification_id=verification.id, status='verified')
    def medical(point):
        return MedicalTime.objects.create(patient=patient, precision='second', local_datetime=point.replace(tzinfo=None),
            timezone_name='UTC', utc_offset_minutes=0, instant=point, resolution_status='resolved')
    start = medical(measured - timedelta(minutes=1 + observation_padding_minutes))
    end = medical(timezone.now().replace(microsecond=0) + timedelta(seconds=2))
    period = ObservationPeriodVersion.objects.create(patient_id=patient.id, period_id=uuid4(), version=1, owner_id=actor.id,
        start_time_id=start.id, end_time_id=end.id, label='synthetic observation window', label_source='self_report', created_at=timezone.now())
    point = timezone.now()
    dataset_id, label_id, event_id, plan_id, matching_id = [uuid4() for _ in range(5)]
    plan_ref, matching_ref = [f'urn:sjia:source-revision:{value}' for value in (plan_id, matching_id)]
    criteria, criteria_ref = fx.evidence({'mode': 'SYNTHETIC_ONLY', 'clinical_validation': False}, purposes=['evaluation_protocol', purpose])
    actual_configs = list(ConfigVersion.objects.filter(id__in=[value.id for value in fx.created if isinstance(value, ConfigVersion)], status='enabled'))
    config_checksums = {str(value.id): value.checksum for value in actual_configs}
    facts = []
    for fact in ArchiveFactVersion.objects.filter(patient_id=patient.id).order_by('id'):
        field = RecordField.objects.get(id=fact.field_id)
        version = RecordVersion.objects.get(id=fact.record_version_id)
        verification = Verification.objects.get(id=FieldVerificationState.objects.get(field_id=field.id, purpose=purpose).current_verification_id)
        facts.append({'fact_ref': str(fact.id), 'field_id': str(field.id), 'record_version_id': str(version.id),
            'verification_id': str(verification.id), 'reference_sha256': fact_reference_digest(fact, field, version, fx.source, verification),
            'source_ref': fx.reference, 'available_at': fact.available_at.isoformat(), 'verified_at': verification.verified_at.isoformat()})
    event = {'id': str(event_id), 'patient_id': str(patient.id), 'period_id': str(period.id), 'label_state': 'positive',
        'reference_answer_ref': matching_ref, 'reference_at': point.isoformat(),
        'valid_alert_start': point.isoformat(), 'valid_alert_end': point.isoformat()}
    prompt = {'id': 'synthetic-scheduled-positive', 'at': point.isoformat(), 'outcome': 'positive', 'outcome_ref': matching_ref,
        'matched_event_id': str(event_id), 'occurrence': 'first'}
    placeholder = {'execution_ref': 'pending', 'task_id': 'pending', 'rule_config_id': str(rule.id),
        'execution_input_sha256': '0' * 64, 'result_sha256': '0' * 64, 'status': 'completed', 'used_fact_refs': [], 'prompts': [prompt]}
    capture = {'schema_version': 'clinical-result-capture-2', 'data_mode': 'synthetic', 'mode': 'replay',
        'dataset_version_id': str(dataset_id), 'dataset_checksum': 'b' * 64,
        'implementation_sha256': base.runtime.validations.get().artifact_sha256, 'configuration_checksums': config_checksums,
        'measurement_plan_ref': plan_ref, 'captured_at': timezone.now().isoformat(),
        'patients': [{'patient_id': str(patient.id), 'partition': 'development'}], 'events': [event],
        'periods': [{'id': str(period.id), 'patient_id': str(patient.id), 'label_id': str(label_id), 'label_state': 'positive',
            'reference_answer_ref': matching_ref, 'start': start.instant.isoformat(), 'end': end.instant.isoformat(), 'subgroups': {},
            'steps': [{'id': 'point-one', 'assessment_at': point.isoformat(), 'known_at': point.isoformat(), 'corpus_sha256': '0' * 64,
                'facts': facts, 'static': {**deepcopy(placeholder), 'prompts': [prompt] if applicable else []},
                'dynamic': {**deepcopy(placeholder), 'task_id': 'pending-dynamic'}}]}]}
    typed = ClinicalCaptureV2.model_validate(capture)
    step = capture['periods'][0]['steps'][0]
    step['corpus_sha256'] = corpus_digest(typed, typed.periods[0], typed.periods[0].steps[0])
    fixed_before = timezone.now()
    plan = {'schema_version': 'clinical-measurement-plan-2', 'plan_ref': plan_ref, 'matching_policy_ref': matching_ref,
        'matching_mode': 'independent_explicit_pairings', 'confidence_interval_protocol_ref': None, 'subgroup_dimensions': [],
        'fixed_before': fixed_before.isoformat(), 'criteria_refs': [criteria_ref], 'static_criterion': 'classification_meets',
        'dynamic_criterion': 'decision_met', 'assessability': 'all_scheduled_steps_completed',
        'occurrence_policy': 'first_positive_then_repeat', 'reset_policy': 'period_boundary_only',
        'schedule': [{'period_id': str(period.id), 'step_id': step['id'],
            'static_rule_config_id': str(rule.id), 'dynamic_rule_config_id': str(configs['rule'].id),
            'static_policies': {'runtime_config_id': str(base.runtime.id), 'window_policy_id': str(window.id), 'verification_policy_id': str(configs['verification'].id)},
            'dynamic_policies': {'runtime_config_id': str(base.runtime.id), 'input_rule_id': str(base.input_rule.id), 'window_policy_id': str(configs['window'].id),
                'verification_policy_id': str(configs['verification'].id)}, **{key: step[key] for key in ('assessment_at', 'known_at', 'corpus_sha256')}}]}
    if prepare_only:
        # Multi-patient fixture prepares all facts, schedules and independent
        # outcomes before any replay executes or the common dataset freezes.
        return SimpleNamespace(base=base, plan=plan, capture=capture, rule=rule, window=window,
            period=period, end=end, point=point, prompt=prompt, applicable=applicable,
            criteria_ref=criteria_ref, label_id=label_id, event_id=event_id)
    interval = interval_factory(fx, plan) if interval_factory is not None else None
    fx.evidence(plan, purposes=['evaluation_protocol', purpose], identity=plan_id)
    matching_prompt = Prompt.model_validate(prompt).model_dump(mode='json')
    matching = {'schema_version': 'clinical-explicit-pairings-2', 'measurement_plan_ref': plan_ref,
        'events': [Event.model_validate(event).model_dump(mode='json')],
        'periods': [{'id': str(period.id), 'subgroups': {}, 'steps': [{'id': step['id'], 'static': [matching_prompt] if applicable else [], 'dynamic': [matching_prompt]}]}]}
    fx.evidence(matching, purposes=['evaluation_answer', purpose], identity=matching_id)
    import time
    time.sleep(max(0, (end.instant - timezone.now()).total_seconds()))
    frozen = timezone.now()
    patient.refresh_from_db()
    tasks = {}
    for mode in ('static', 'dynamic'):
        common = {'mode': 'replay', 'assessment_at': point.isoformat(), 'known_at': point.isoformat(), 'observation_period_id': str(period.id)}
        if mode == 'static':
            payload = {**common, 'kind': 'classification', 'rule_id': str(rule.id), 'window_policy_id': str(window.id),
                'verification_policy_id': str(configs['verification'].id)}
            task, _ = create_assessment_task(patient_id=patient.id, actor=actor, auth_epoch=actor.auth_epoch, environment=fx.gate.environment, payload=payload)
            execute_static_assessment(OutboxEvent.objects.get(task=task, event_type='assessment.requested'), 'synthetic-replay-static')
            result = Assessment.objects.get(task_id=task.id)
            view = read_assessment(assessment_id=result.id, actor=actor, auth_epoch=actor.auth_epoch, environment=fx.gate.environment)
            reference = f'urn:sjia:assessment:{result.id}'
            status = view['computation_status']
            case.assertEqual(view['output']['classification_result'], 'meets' if applicable else 'not_applicable', view)
        else:
            task, _ = create_dynamic_task(patient_id=patient.id, actor=actor, auth_epoch=actor.auth_epoch, environment=fx.gate.environment,
                payload={**base.dynamic_payload, **common})
            execute_dynamic_evaluation(OutboxEvent.objects.get(task=task, event_type='alert_evaluation.requested'), 'synthetic-replay-dynamic')
            result = AlertEvaluation.objects.get(task_id=task.id)
            view = read_dynamic_evaluation(evaluation_id=result.id, actor=actor, auth_epoch=actor.auth_epoch, environment=fx.gate.environment)
            reference, status = f'urn:sjia:alert-evaluation:{result.id}', view['status']
            case.assertEqual(view['decision'], 'met', view)
        task.refresh_from_db()
        case.assertEqual((task.status, status, view['validity']), ('completed', 'completed', 'valid'), view)
        selected = {item['fact_version_id'] for item in task.input_refs['snapshot']['selected_refs'].values() if item is not None}
        if mode == 'dynamic':
            selected.update(item['reference']['fact_version_id'] for signal in task.input_refs['snapshot']['signals'] for item in signal['inputs'])
        step[mode] = {'execution_ref': reference, 'task_id': str(task.id), 'rule_config_id': str(rule.id if mode == 'static' else configs['rule'].id),
            'execution_input_sha256': task.input_sha256, 'result_sha256': stable_result_digest(view), 'status': status,
            'used_fact_refs': sorted(selected), 'prompts': [prompt] if mode == 'dynamic' or applicable else []}
        tasks[mode] = task
    capture['captured_at'] = timezone.now().isoformat()
    capture_source, capture_ref = fx.evidence(capture, purposes=['evaluation_capture', purpose])
    entries = {'files': [SimpleNamespace(id=base.file.id, patient_id=patient.id)], 'observation_periods': [period],
        'mas_events': [SimpleNamespace(id=event_id, patient_id=patient.id, reference_answer_ref=matching_ref, event_time_id=None)]}
    row = SimpleNamespace(id=dataset_id, checksum=capture['dataset_checksum'], frozen_at=frozen, purpose=purpose,
        environment=fx.gate.environment, coverage={'labels': [{'label_id': str(label_id), 'patient_id': str(patient.id),
            'observation_period_id': str(period.id), 'label_state': 'positive', 'reference_answer_ref': matching_ref}]})
    sources = SimpleNamespace(protocol=SimpleNamespace(evaluation_kind='clinical', measurement_plan_ref=plan_ref, criteria_refs=[criteria_ref]),
        manifest=SimpleNamespace(entries=lambda name: entries.get(name, [])))
    snapshot = {'implementation_sha256': capture['implementation_sha256'], 'configurations': [{'id': key, 'sha256': value} for key, value in config_checksums.items()],
        'input': {'partition': 'development'}, 'denominators': {'patient_ids': [str(patient.id)], 'unit_ids': {'observation_periods': [str(period.id)]}}}
    return SimpleNamespace(base=base, row=row, sources=sources, snapshot=snapshot, capture=capture, plan=plan,
        capture_ref=capture_ref, capture_source=capture_source, tasks=tasks, interval=interval)
