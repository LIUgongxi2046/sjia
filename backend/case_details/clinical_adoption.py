"""Adopt only independently qualified captures backed by actual saved replays."""
from uuid import UUID
from functools import wraps
from django.core.exceptions import ObjectDoesNotExist

from django.db import transaction
from django.utils import timezone

from alerting.models import AlertEvaluation, AlertEvent, AlertDecision
from alerting.service import read_dynamic_evaluation
from archives.models import ArchiveFactVersion
from assessment.models import Assessment
from assessment.provenance import entity_reference
from assessment.service import read_assessment
from case_details.clinical_metrics import evaluate, EvaluationInputError
from case_details.clinical_metrics_v2 import ClinicalCaptureV2, MeasurementPlanV2, validate_v2
from case_details.evaluation_evidence import approved_evaluation_artifact, artifact_proof
from case_details.evaluation_units import EvaluationUnitsRejected
from configurations.evidence import artifact_verification_scope
from configurations.models import ConfigVersion
from configurations.service import compute_checksum
from files.models import FilePage
from identity.models import Principal
from medical_time.models import MedicalTime
from recognition.models import SourceAnchor
from records.models import RecordField, RecordVersion
from sources.models import SourceRevision
from tasks.models import Task, TaskAttempt, OutboxEvent
from verification.models import Verification, VerificationField


def fail(reason):
    raise EvaluationUnitsRejected(reason)


def capture_binding_errors(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, KeyError, TypeError, IndexError, ObjectDoesNotExist):
            # Approved artifact bytes can still contain stale/malformed entity
            # references. Refuse them without exposing input values or a 500.
            fail('CLINICAL_CAPTURE_BINDING_INVALID')
    return checked


def fact_reference_digest(fact, field, version, source, verification):
    return compute_checksum({'fact': entity_reference(fact), 'field': entity_reference(field),
        'version': entity_reference(version), 'source': entity_reference(source),
        'verification': entity_reference(verification) if verification is not None else None})


def validate_frozen_scope(plan, capture, row, sources, snapshot):
    expected = {'dataset_version_id': str(row.id), 'dataset_checksum': row.checksum,
        'implementation_sha256': snapshot['implementation_sha256'],
        'configuration_checksums': {value['id']: value['sha256'] for value in snapshot['configurations']},
        'measurement_plan_ref': sources.protocol.measurement_plan_ref}
    if any(getattr(capture, key) != value for key, value in expected.items()):
        fail('CLINICAL_FROZEN_BINDING_MISMATCH')
    patients = set(snapshot['denominators']['patient_ids'])
    if ({value.patient_id for value in capture.patients} != patients
            or any(value.partition != snapshot['input']['partition'] for value in capture.patients)):
        fail('CLINICAL_PARTITION_MISMATCH')
    ids = snapshot['denominators']['unit_ids']
    if {period.id for period in capture.periods} != set(ids['observation_periods']):
        fail('CLINICAL_PERIOD_COVERAGE_MISMATCH')
    labels = {value['label_id']: value for value in row.coverage['labels'] if value['patient_id'] in patients}
    if {period.label_id for period in capture.periods} != set(labels):
        fail('CLINICAL_LABEL_COVERAGE_MISMATCH')
    periods = {str(value.id): value for value in sources.manifest.entries('observation_periods')}
    for period in capture.periods:
        label, saved = labels[period.label_id], periods[period.id]
        if (label['patient_id'] != period.patient_id or str(saved.patient_id) != period.patient_id
                or label['observation_period_id'] != period.id or label['label_state'] != period.label_state
                or label['reference_answer_ref'] != period.reference_answer_ref):
            fail('CLINICAL_LABEL_CONTENT_MISMATCH')
        start = MedicalTime.objects.select_for_update().get(id=saved.start_time_id, patient_id=period.patient_id)
        end = MedicalTime.objects.select_for_update().get(id=saved.end_time_id, patient_id=period.patient_id)
        if start.instant != period.start or end.instant != period.end:
            fail('CLINICAL_OBSERVATION_DENOMINATOR_MISMATCH')
    events = {str(value.id): value for value in sources.manifest.entries('mas_events') if str(value.patient_id) in patients}
    if {value.id for value in capture.events} != set(events):
        fail('CLINICAL_EVENT_COVERAGE_MISMATCH')
    for event in capture.events:
        saved = events[event.id]
        if event.patient_id != str(saved.patient_id) or event.reference_answer_ref != saved.reference_answer_ref:
            fail('CLINICAL_EVENT_CONTENT_MISMATCH')
        if saved.event_time_id is not None:
            medical_time = MedicalTime.objects.select_for_update().get(id=saved.event_time_id, patient_id=event.patient_id)
            if medical_time.instant != event.reference_at:
                fail('CLINICAL_EVENT_REFERENCE_TIME_MISMATCH')
    if set(plan.criteria_refs) != set(sources.protocol.criteria_refs):
        fail('CLINICAL_CRITERIA_BINDING_MISMATCH')
    if plan.fixed_before > row.frozen_at:
        fail('CLINICAL_PLAN_NOT_FIXED_BEFORE_FREEZE')


def verify_corpus(period, step, row, sources):
    files = {str(value.id) for value in sources.manifest.entries('files') if str(value.patient_id) == period.patient_id}
    proofs = []
    for item in step.facts:
        fact = ArchiveFactVersion.objects.select_for_update().get(id=UUID(item.fact_ref), patient_id=period.patient_id)
        field = RecordField.objects.select_for_update().get(id=UUID(item.field_id), patient_id=period.patient_id,
            record_version_id=UUID(item.record_version_id))
        version = RecordVersion.objects.select_for_update().get(id=field.record_version_id, patient_id=period.patient_id)
        source = SourceRevision.objects.select_for_update().get(id=version.source_revision_id)
        verification = (Verification.objects.select_for_update().get(id=UUID(item.verification_id),
            patient_id=period.patient_id, record_version_id=version.id) if item.verification_id else None)
        if (fact.field_id != field.id or fact.record_version_id != version.id or fact.created_at > row.frozen_at
                or version.recorded_at > row.frozen_at or item.available_at != fact.available_at
                or item.verified_at != (verification.verified_at if verification else None)
                or item.source_ref != f'urn:sjia:source-revision:{source.id}'
                or item.reference_sha256 != fact_reference_digest(fact, field, version, source, verification)):
            fail('CLINICAL_CORPUS_PROOF_CHANGED')
        if verification is not None and not VerificationField.objects.filter(verification_id=verification.id,
                field_id=field.id, record_version_id=version.id, patient_id=period.patient_id).exists():
            fail('CLINICAL_CORPUS_VERIFICATION_MISMATCH')
        if field.anchor_id is None:
            fail('CLINICAL_CORPUS_SOURCE_REQUIRED')
        anchor = SourceAnchor.objects.select_for_update().get(id=field.anchor_id, patient_id=period.patient_id)
        page = FilePage.objects.select_for_update().get(id=anchor.page_id, patient_id=period.patient_id)
        if str(page.file_id) not in files or fact.source_anchor_id != anchor.id:
            fail('CLINICAL_CORPUS_OUTSIDE_FROZEN_SOURCES')
        proofs.append({'fact_id': item.fact_ref, 'reference_sha256': item.reference_sha256})
    return proofs


def stable_result_digest(view):
    # Task readback polling hints are operational metadata, not evaluation output.
    return compute_checksum({key: value for key, value in view.items() if key != 'task'})


def criterion_met(plan, mode, view):
    if mode == 'dynamic':
        return view['status'] == 'completed' and view['decision'] == 'met'
    output = view.get('output') or {}
    if (view['computation_status'] == 'completed' and
            (view.get('applicability') == 'not_applicable' or output.get('classification_result') == 'not_applicable'
             or view.get('kind') == 'ms' and output.get('ms_threshold_result') is None)):
        # The qualified v2 plan has no independent rule for assigning a
        # completed but non-applicable assessment to a clinical denominator.
        fail('CLINICAL_APPLICABILITY_POLICY_UNSUPPORTED')
    if plan.static_criterion == 'classification_meets':
        if view.get('kind') != 'classification':
            fail('CLINICAL_STATIC_CRITERION_MISMATCH')
        return view['computation_status'] == 'completed' and output.get('classification_result') == 'meets'
    if view.get('kind') != 'ms':
        fail('CLINICAL_STATIC_CRITERION_MISMATCH')
    return view['computation_status'] == 'completed' and output.get('ms_threshold_result') is True


def verify_execution(plan, capture, period, step, mode, result, *, purpose, environment, frozen_at):
    task = Task.objects.select_for_update().get(id=UUID(result.task_id), patient_id=period.patient_id,
        task_type='assessment' if mode == 'static' else 'alert_evaluation',
        schema_version='static-assessment-task-2' if mode == 'static' else 'dynamic-evaluation-task-2')
    payload, context, saved = task.input_refs['payload'], task.input_refs['authorization_context'], task.input_refs['snapshot']
    if (context['purpose'] != purpose or context['environment'] != environment
            or task.status != 'completed' or task.input_sha256 != result.execution_input_sha256
            or payload['mode'] != 'replay' or payload.get('observation_period_id') != period.id
            or timezone.datetime.fromisoformat(payload['assessment_at'].replace('Z', '+00:00')) != step.assessment_at
            or timezone.datetime.fromisoformat(payload['known_at'].replace('Z', '+00:00')) != step.known_at
            or context['implementation_sha256'] != capture.implementation_sha256
            or task.created_at < plan.fixed_before or task.created_at < frozen_at or task.created_at > capture.captured_at):
        fail('CLINICAL_REPLAY_EXECUTION_MISMATCH')
    point = next(item for item in plan.schedule if item.period_id == period.id and item.step_id == step.id)
    declared = getattr(point, mode + '_policies').model_dump()
    if (context.get('runtime_id') != declared.pop('runtime_config_id')
            or any(payload.get(key) != value for key, value in declared.items())):
        fail('CLINICAL_REPLAY_POLICY_MISMATCH')
    expected_schema = 'assessment-rule-2' if mode == 'static' else 'alert-trigger-1'
    key = 'rule_id' if mode == 'static' else 'policy_id'
    if payload[key] != result.rule_config_id:
        fail('CLINICAL_REPLAY_RULE_MISMATCH')
    for proof in saved['configurations']:
        config = ConfigVersion.objects.select_for_update().get(id=proof['config']['id'])
        if (capture.configuration_checksums.get(str(config.id)) != config.checksum
                or proof['config'] != entity_reference(config) or proof['artifact_sha256'] != capture.implementation_sha256):
            fail('CLINICAL_REPLAY_CONFIGURATION_MISMATCH')
    ConfigVersion.objects.get(id=result.rule_config_id, schema_version=expected_schema)
    owner = Principal.objects.select_for_update().get(id=task.actor_id)
    if mode == 'static':
        row = Assessment.objects.select_for_update().get(task_id=task.id, patient_id=period.patient_id)
        view = read_assessment(assessment_id=row.id, actor=owner, auth_epoch=int(context['auth_epoch']), environment=context['environment'])
        reference = f'urn:sjia:assessment:{row.id}'
        state = view['computation_status']
    else:
        row = AlertEvaluation.objects.select_for_update().get(task_id=task.id, patient_id=period.patient_id)
        view = read_dynamic_evaluation(evaluation_id=row.id, actor=owner, auth_epoch=int(context['auth_epoch']), environment=context['environment'])
        reference = f'urn:sjia:alert-evaluation:{row.id}'
        state = view['status']
        if AlertEvent.objects.filter(evaluation_id=row.id).exists() or AlertDecision.objects.filter(evaluation_id=row.id).exists():
            fail('CLINICAL_REPLAY_LIVE_SIDE_EFFECT')
        attempt = TaskAttempt.objects.get(task=task, lease_generation=row.lease_generation, status='completed')
        if attempt.usage_evidence['lifecycle'] != {'state': 'replay', 'action': 'blocked',
                'reason_codes': ['DYNAMIC_REPLAY_NO_PUBLICATION'], 'event_id': None, 'episode_id': None}:
            fail('CLINICAL_REPLAY_LIVE_SIDE_EFFECT')
    if (view['validity'] != 'valid' or view['mode'] != 'replay' or result.execution_ref != reference
            or result.result_sha256 != stable_result_digest(view) or result.status != state):
        fail('CLINICAL_REPLAY_RESULT_CHANGED')
    attempt = TaskAttempt.objects.get(task=task, lease_generation=task.lease_generation, status='completed')
    if attempt.finished_at is None or attempt.finished_at > capture.captured_at:
        fail('CLINICAL_CAPTURE_PRECEDES_EXECUTION')
    if OutboxEvent.objects.filter(task=task).exclude(event_type__in=['assessment.requested', 'alert_evaluation.requested']).exists():
        fail('CLINICAL_REPLAY_LIVE_SIDE_EFFECT')
    selected = {item['fact_version_id']: item for item in saved['selected_refs'].values() if item is not None}
    if mode == 'dynamic':
        selected.update({item['reference']['fact_version_id']: item['reference'] for signal in saved['signals'] for item in signal['inputs']})
    if set(selected) != set(result.used_fact_refs):
        fail('CLINICAL_REPLAY_USED_FACTS_MISMATCH')
    corpus = {item.fact_ref: item for item in step.facts}
    for identity, proof in selected.items():
        item = corpus[identity]
        if (proof['field_id'] != item.field_id or proof['record_version_id'] != item.record_version_id
                or proof['verification_id'] != item.verification_id or proof['source_revision_id'] != item.source_ref.rsplit(':', 1)[1]):
            fail('CLINICAL_REPLAY_CORPUS_MISMATCH')
    if bool(result.prompts) != criterion_met(plan, mode, view):
        fail('CLINICAL_REPLAY_PROMPT_MISMATCH')
    return {'task': entity_reference(task), 'attempt': entity_reference(attempt),
        'execution_ref': reference, 'result_sha256': result.result_sha256,
        'candidate_fact_ids': sorted({item['reference']['fact_version_id'] for item in saved['candidate_refs']})}


@transaction.atomic
@artifact_verification_scope()
@capture_binding_errors
def adopt_clinical_capture(*, row, sources, snapshot, capture_ref, include_intervals=True):
    if sources.protocol.evaluation_kind != 'clinical' or row.purpose != 'clinical_evaluation':
        fail('CLINICAL_CAPTURE_PURPOSE_INVALID')
    plan_ref = sources.protocol.measurement_plan_ref
    plan_revision, plan_bytes = approved_evaluation_artifact(plan_ref, kind='evaluation_protocol', purpose=row.purpose)
    capture_revision, capture_bytes = approved_evaluation_artifact(capture_ref, kind='evaluation_capture', purpose=row.purpose)
    from pydantic import ValidationError
    try:
        plan, capture = MeasurementPlanV2.model_validate_json(plan_bytes), ClinicalCaptureV2.model_validate_json(capture_bytes)
    except ValidationError:
        fail('CLINICAL_CAPTURE_SCHEMA_INVALID')
    if (plan.plan_ref != plan_ref or plan_revision.captured_at > row.frozen_at
            or capture.captured_at > capture_revision.captured_at or capture.captured_at < row.frozen_at):
        fail('CLINICAL_EVIDENCE_CHRONOLOGY_INVALID')
    legacy_ci_refs = [plan.confidence_interval_protocol_ref] if not include_intervals and plan.confidence_interval_protocol_ref else []
    for reference in {*plan.criteria_refs, *legacy_ci_refs}:
        proof, _ = approved_evaluation_artifact(reference, kind='evaluation_protocol', purpose=row.purpose)
        if proof.captured_at > row.frozen_at:
            fail('CLINICAL_CRITERIA_NOT_FIXED_BEFORE_FREEZE')
    try:
        validate_v2(plan, capture)
    except EvaluationInputError as error:
        fail(str(error))
    validate_frozen_scope(plan, capture, row, sources, snapshot)
    # Independent pairing/outcome evidence must exactly fix events, subgroups,
    # prompt labels and match windows; capture cannot supply its own Gold.
    matching_revision, matching_bytes = approved_evaluation_artifact(plan.matching_policy_ref,
        kind='evaluation_answer', purpose=row.purpose)
    import json
    expected_matching = {'schema_version': 'clinical-explicit-pairings-2', 'measurement_plan_ref': plan_ref,
        'events': [event.model_dump(mode='json') for event in capture.events],
        'periods': [{'id': period.id, 'subgroups': period.subgroups,
            'steps': [{'id': step.id, **{mode: [prompt.model_dump(mode='json') for prompt in getattr(step, mode).prompts]
                for mode in ('static', 'dynamic')}} for step in period.steps]} for period in capture.periods]}
    if json.loads(matching_bytes) != expected_matching or matching_revision.captured_at > row.frozen_at:
        fail('CLINICAL_INDEPENDENT_PAIRINGS_MISMATCH')
    executions, corpus = [], []
    for period in capture.periods:
        for step in period.steps:
            corpus.append({'period_id': period.id, 'step_id': step.id, 'sha256': step.corpus_sha256,
                'facts': verify_corpus(period, step, row, sources)})
            step_proofs = [verify_execution(plan, capture, period, step, mode, getattr(step, mode),
                purpose=row.purpose, environment=row.environment, frozen_at=row.frozen_at) for mode in ('static', 'dynamic')]
            if {identity for proof in step_proofs for identity in proof['candidate_fact_ids']} != {item.fact_ref for item in step.facts}:
                fail('CLINICAL_REPLAY_CORPUS_COVERAGE_MISMATCH')
            executions.extend(step_proofs)
    from case_details.clinical_interval_adoption import qualify_interval_protocol, prepare_interval_adoption
    interval = qualify_interval_protocol(plan, capture, row) if include_intervals else None
    report = evaluate(plan.model_dump(mode='json'), capture.model_dump(mode='json'))
    report.update(status='CALCULATED_FROM_VERIFIED_REPLAYS', evidence_qualification='QUALIFIED_SAVED_REPLAY_BINDINGS',
        clinical_validation=False)
    report['limitations'][0] = 'Qualified evidence and saved replay bindings verified; this is not a clinical validation approval.'
    proofs = {'plan': artifact_proof(plan_ref, plan_revision), 'capture': artifact_proof(capture_ref, capture_revision),
        'matching': artifact_proof(plan.matching_policy_ref, matching_revision), 'corpus': corpus, 'executions': executions}
    return prepare_interval_adoption({'report': report, 'proofs': proofs,
        'report_sha256': compute_checksum(report), 'proofs_sha256': compute_checksum(proofs)}, interval)


def clinical_admission_binding(adoption):
    return {'report_sha256': adoption['report_sha256'], 'proofs_sha256': adoption['proofs_sha256'],
        'plan': adoption['proofs']['plan'], 'capture': adoption['proofs']['capture'], 'matching': adoption['proofs']['matching'],
        **({'confidence_interval': adoption['proofs']['confidence_interval']} if 'confidence_interval' in adoption['proofs'] else {})}


def checked_saved_clinical(run, task, row, sources):
    snapshot = run.input_refs[0]
    reference = snapshot['input'].get('clinical_capture_ref')
    if reference is None:
        return None
    # Previously saved report-2 bindings retain their original null-CI contract.
    # They still recheck every original evidence/replay gate; no interval is added.
    adoption = adopt_clinical_capture(row=row, sources=sources, snapshot=snapshot, capture_ref=reference,
        include_intervals='confidence_interval' in snapshot.get('clinical_capture_binding', {}))
    if clinical_admission_binding(adoption) != snapshot.get('clinical_capture_binding'):
        fail('CLINICAL_CAPTURE_BINDING_CHANGED')
    if run.status not in ('completed', 'insufficient'):
        return None
    proof = TaskAttempt.objects.get(task=task, status='completed', lease_generation=task.lease_generation)
    if '_interval_inputs' in adoption:
        from case_details.clinical_interval_adoption import verify_saved_interval_adoption
        return verify_saved_interval_adoption(adoption, proof.usage_evidence.get('clinical_adoption'), task.input_sha256)
    if proof.usage_evidence.get('clinical_adoption') != adoption:
        fail('CLINICAL_SAVED_REPORT_CHANGED')
    return adoption['report']
