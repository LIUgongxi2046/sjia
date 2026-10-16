"""Fresh native cross-domain canaries for test_content_disposition_engine only.

This is never imported by application runtime. None of the fixture configuration,
clinical classifications or notification rows is an approved real-world result.
No provider, delivery, inference, backup or policy-installation action is called.
"""
from datetime import timedelta
from uuid import uuid4
from django.db import connection, transaction
from django.conf import settings
from archives import models as ar
from assessment import models as am
from alerting import models as al
from notifications import models as nm
from verification import models as vm
from recognition.voice_models import VoiceInput, TranscriptionReceipt
from drafts.models import Draft
from contacts.models import ContactVersion
from family_content.models import ReleaseBundle
from relations.models import Relation, RelationApplication
from tasks.models import Task, TaskAttempt, OutboxEvent
from retention.content_registry import digest
from tasks.service import input_digest


def add_cross_domain_canaries(case, canary):
    if connection.vendor != 'postgresql' or not str(connection.settings_dict['NAME']).startswith('test_'):
        raise RuntimeError('Synthetic content closure fixture refuses non-test PostgreSQL databases')
    patient, actor, now = case.patient, case.actor, case.now
    rows = []
    def make(model, **values):
        row = model.objects.create(**values)
        if hasattr(row, 'content_erased_at'):
            rows.append(row)
        return row
    refs = {'record_version_id': str(case.first.id), 'field_id': str(case.field.id), 'clinical': canary}
    patient_args = {'patient_id': patient.id}

    draft = make(Draft, patient=patient, owner=actor, form_type='observation', version=1,
        schema_version='synthetic-draft-1', payload=refs, status='editing', saved_at=now,
        retention_policy=case.evidence.retention)
    verification = make(vm.Verification, patient=patient, record_version_id=case.first.id, verifier=actor,
        policy=case.config, purpose='synthetic_content_disposition', evidence_ref=canary + ':verification_quote',
        evidence_source=case.evidence.source, verified_at=now)
    make(vm.VerificationField, **patient_args, verification_id=verification.id, record_version_id=case.first.id,
        field_id=case.field.id, result='uncertain', reason=canary + ':verification_reason')
    verification_request = make(vm.VerificationRequest, **patient_args, record_version_id=case.first.id,
        requested_by=actor, purpose='synthetic_content_disposition', status='completed', created_at=now, completed_at=now)
    make(vm.FieldVerificationState, **patient_args, field_id=case.field.id, record_version_id=case.first.id,
        purpose='synthetic_content_disposition', version=1, request_id=verification_request.id,
        current_verification_id=verification.id, status='verified')
    make(vm.CorrectionRequest, **patient_args, record_version_id=case.first.id, requested_by=actor,
        field_ids=[case.field.id], reason=canary + ':correction_reason', proposed_text=canary + ':correction_text',
        status='pending', version=1, created_at=now)

    assessment_task = case.task_row({'snapshot': canary + ':assessment_task'}, task_type='assessment')
    assessment = make(am.Assessment, patient=patient, kind='classification', assessment_at=now,
        rule=case.config, window_policy=case.config, task_id=assessment_task.id,
        data_epoch=0, verification_epoch=0, computation_status='insufficient', validity='valid',
        missing_items=[canary + ':assessment_missing'], computed_at=now)
    make(am.AssessmentInput, **patient_args, assessment_id=assessment.id, variable_code=canary + ':variable',
        record_version_id=case.first.id, field_id=case.field.id, verification_id=verification.id,
        normalization_id=case.normalized.id, input_snapshot=refs)
    make(am.AssessmentOutput, assessment=assessment, schema_version='synthetic-assessment-output-1',
        classification_conditions={'synthetic': canary}, classification_result='insufficient',
        ms_contributions={'synthetic': canary}, ms_total='987.654', ms_threshold_result=False)
    # A second legal shape proves HLH JSON is scrubbed, without filling mutually
    # exclusive classification and HLH columns with fabricated values.
    hlh_task = case.task_row({'snapshot': canary + ':hlh_task'}, task_type='assessment')
    hlh = make(am.Assessment, patient=patient, kind='hlh', assessment_at=now, rule=case.config,
        window_policy=case.config, task_id=hlh_task.id, data_epoch=0, verification_epoch=0,
        computation_status='insufficient', validity='valid', missing_items=[canary], computed_at=now)
    make(am.AssessmentInput, **patient_args, assessment_id=hlh.id, variable_code=canary + ':hlh',
        record_version_id=case.first.id, field_id=case.field.id, verification_id=verification.id, input_snapshot=refs)
    make(am.AssessmentOutput, assessment=hlh, schema_version='synthetic-hlh-output-1', hlh_result={'body': canary})

    alert_task = case.task_row({'snapshot': canary + ':alert_task'}, task_type='alert_evaluation')
    evaluation = make(al.AlertEvaluation, patient=patient, task_id=alert_task.id,
        environment=settings.SJIA_ENVIRONMENT, policy=case.config, window_policy=case.config,
        assessment_at=now, known_at=now, context_sequence=1, mode='replay', status='insufficient', decision='unknown',
        authorization_version=1, lease_generation=1, use_authorization_id=case.authorization.id,
        input_digest=digest(refs), missing_items=[canary + ':alert_missing'], computed_at=now, validity='valid')
    make(al.AlertEvaluationInput, **patient_args, evaluation_id=evaluation.id, signal_id=canary + ':signal',
        position=1, variable_code=canary + ':variable', input_role='current', record_version_id=case.first.id,
        field_id=case.field.id, verification_id=verification.id, normalization_id=case.normalized.id,
        medical_time_id=case.medical_time.id, source_revision=case.evidence.source)
    make(al.AlertEvaluationAssessment, **patient_args, evaluation_id=evaluation.id, assessment_id=assessment.id,
        role=canary + ':assessment_role')
    make(al.AlertSignalResult, **patient_args, evaluation_id=evaluation.id, signal_id=canary + ':signal',
        schema_version='synthetic-signal-1', state='unknown', algorithm_version='synthetic-1',
        input_field_ids=[case.field.id], reason_codes=[canary + ':reason'], pair_results=[{'copy': canary}])
    episode = make(al.AlertEpisode, patient=patient, rule_family_key=canary + ':rule_family', status='invalid',
        opened_at=now, last_evaluation_id=evaluation.id, state_version=1, last_assessment_at=now, last_context_sequence=1)
    make(al.AlertDecision, **patient_args, evaluation_id=evaluation.id, rule_key=canary + ':rule', state='unknown',
        action='blocked', reason_codes=[canary + ':blocked'], episode_id=episode.id, sequence=1)
    contact = make(ContactVersion, contact_id=uuid4(), version=1, contact_name='SYNTHETIC NO DELIVERY',
        purpose='synthetic_content_disposition', channel='in_app', channel_value='synthetic.invalid',
        owner=actor, source_revision=case.evidence.source, approval_config=case.config,
        updated_at=now, valid_until=now + timedelta(days=1), status='draft')
    release = make(ReleaseBundle, environment=settings.SJIA_ENVIRONMENT, version=1,
        status='draft', contact_version=contact, validation_config=case.config)
    event = make(al.AlertEvent, patient=patient, evaluation_id=evaluation.id, release=release,
        trigger_key=canary + ':trigger', trigger_policy=case.config, input_snapshot=refs,
        validity='invalid', created_at=now)
    application = make(RelationApplication, applicant=actor, patient=patient, version=1,
        evidence_ref=case.evidence.reference, status='approved', checked_by=actor, checked_at=now, created_at=now)
    relation = make(Relation, patient=patient, guardian=actor, application=application, status='active',
        version=1, checked_by=actor, checked_at=now, valid_from=now)
    recipient = make(al.AlertRecipient, **patient_args, alert_id=event.id, relation_id=relation.id,
        channel='in_app', delivery_status='not_sent')
    notification = make(nm.NotificationAttempt, **patient_args, recipient_id=recipient.id, attempt_number=1,
        send_permit_id=uuid4(), provider_idempotency_key='synthetic-only-' + str(uuid4()),
        authorized_at=now, permit_expires_at=now + timedelta(minutes=1), status='cancelled')
    make(nm.NotificationReceipt, attempt=notification, provider='SYNTHETIC_NO_NETWORK',
        provider_event_id=str(uuid4()), status='failed', occurred_at=now, received_at=now, evidence_sha256=digest(refs))

    archive = make(ar.Archive, **patient_args, created_at=now)
    batch = make(ar.ArchiveBatch, **patient_args, archive_id=archive.id, submitted_by=actor.id,
        logical_request_id=uuid4(), version=1, received_at=now, quota_policy_id=case.config.id,
        budget_status='unknown', status='archived')
    regroup = make(ar.ArchiveRegrouping, **patient_args, batch_id=batch.id, actor_id=actor.id,
        request_id=uuid4(), input_sha256=digest(refs), source_refs=[refs], result_refs=refs,
        result_sha256=digest(refs), created_at=now)
    with transaction.atomic():
        document = make(ar.ArchiveDocument, **patient_args, current_version=1, created_at=now,
            retired_at=now, retired_by=actor.id, retirement_id=regroup.id, retirement_reason=canary + ':retired')
        document_version = make(ar.ArchiveDocumentVersion, **patient_args, document_id=document.id,
            version=1, batch_id=batch.id, document_type='laboratory', organization=canary + ':document_org',
            proposed_metadata={'body': canary}, confirmed_text=canary + ':document_text', grouping_status='confirmed',
            documented_time_id=case.medical_time.id, available_at=now, confirmed_by=actor.id, confirmed_at=now)
    page = make(ar.ArchivePage, **patient_args, batch_id=batch.id, ordinal=1, file_page_id=case.page.id,
        file_id=case.original.id, file_object_version=case.original.object_version, checksum=case.original.checksum,
        version=1, status='confirmed', status_version=1, current_task_id=case.ocr_task.id, available_at=now)
    make(ar.ArchiveDocumentPage, **patient_args, document_version_id=document_version.id, page_id=page.id, page_order=1)
    ar.ArchiveFactVersion.objects.filter(id=case.fact_rows[0].id).update(document_version_id=document_version.id,
        page_id=page.id, source_anchor_id=case.anchor.id)
    ocr_attempt = make(TaskAttempt, task=case.ocr_task, attempt_number=1, lease_generation=1,
        started_at=now, finished_at=now, status='completed', usage_evidence={'copy': canary})
    make(ar.ArchivePageAttempt, **patient_args, page_id=page.id, page_version=1, task_id=case.ocr_task.id,
        task_attempt_id=ocr_attempt.id, attempt_number=1, input_sha256=case.ocr_task.input_sha256,
        model_version='synthetic-1', candidate_set_id=case.candidate_set.id, started_at=now, finished_at=now,
        usage_evidence={'copy': canary}, status='completed')
    make(ar.ArchiveRelationVersion, **patient_args, relation_id=uuid4(), version=1,
        left_fact_version_id=case.fact_rows[0].id, right_fact_version_id=case.fact_rows[1].id,
        relation_type='independent_sampling', status='proposed', evidence_ref=canary + ':relation_evidence',
        reason=canary + ':relation_reason', author_id=actor.id, created_at=now)
    purpose_copy = make(ar.PurposeCopy, **patient_args, original_file_id=case.original.id,
        derived_file_id=case.response_file.id, version=1, purpose='external_recognition',
        environment=settings.SJIA_ENVIRONMENT, use_authorization_id=case.authorization.id,
        processing_version='synthetic-1', processing_evidence={'copy': canary}, date_strategy='unchanged',
        clinical_integrity_status='pending', status='pending_review', created_by=actor.id, created_at=now)
    make(ar.PurposeCopyReview, **patient_args, copy_id=purpose_copy.id, copy_version=1,
        reviewer_id=actor.id, reviewed_at=now, evidence_ref=canary + ':copy_review', checked_scopes=[canary],
        identity_result='failed', clinical_result='failed', original_checksum=case.original.checksum,
        derived_checksum=case.response_file.checksum)
    period = make(ar.ObservationPeriodVersion, **patient_args, period_id=uuid4(), version=1,
        owner_id=actor.id, start_time_id=case.medical_time.id, end_time_id=case.adjacent_time.id,
        label=canary + ':period', label_source='self_report', created_at=now)
    delta_task = case.task_row({'copy': canary + ':delta_task'})
    delta = make(ar.DeltaSummary, **patient_args, owner_id=actor.id, batch_id=batch.id,
        baseline_refs=[refs], current_refs=[refs], data_epoch=0, authorization_version=1,
        visibility_policy_id=case.config.id, validity='invalid', generated_at=now, task_id=delta_task.id)
    make(ar.DeltaSummaryItem, **patient_args, summary_id=delta.id, ordinal=1, item_kind='fact',
        item_source_refs=[refs], limitations=[canary], content={'body': canary})
    dataset = make(ar.EvaluationDatasetVersion, dataset_id=uuid4(), version=1, purpose='synthetic_software_only',
        environment=settings.SJIA_ENVIRONMENT, created_by=actor.id, created_at=now, frozen_at=now,
        protocol_ref=case.evidence.reference, coverage={'synthetic': True}, checksum=digest({'synthetic': True}), status='frozen')
    partition = make(ar.EvaluationPatientPartition, **patient_args, dataset_version_id=dataset.id,
        partition='validation', use_authorization_id=case.authorization.id, source_refs=[refs])
    make(ar.EvaluationLabel, **patient_args, partition_id=partition.id, observation_period_id=period.id,
        label_state='unknown', reference_answer_ref=canary + ':label_ref', limitations=[canary])
    run = make(ar.EvaluationRun, dataset_version_id=dataset.id, operator_id=actor.id,
        implementation_sha256=digest({'synthetic': True}), rules_refs=[{'config_id': str(case.config.id)}],
        input_refs=[{'body': canary}], result_refs=[{'confidence_interval': {'synthetic': canary}}],
        denominators={'count': 1, 'copy': canary}, limitations=[canary], started_at=now,
        finished_at=now, status='completed', mode='replay')
    # This mirrors the real evaluation architecture: patient-null Task linked by
    # evaluation_run_id, with the interval/result duplicated in TaskAttempt.
    eval_input = {'evaluation_run_id': str(run.id), 'snapshot': {'body': canary}}
    eval_task = make(Task, actor=actor, patient=None, task_type='assessment', schema_version='evaluation-run-1',
        input_refs=eval_input, input_sha256=input_digest(eval_input), status='completed', status_version=2,
        lease_generation=1, created_at=now, deadline_at=now + timedelta(hours=1))
    make(TaskAttempt, task=eval_task, attempt_number=1, lease_generation=1, started_at=now, finished_at=now,
        status='completed', usage_evidence={'confidence_interval': {'synthetic_copy': canary},
            'result_sha256': digest(run.result_refs), 'denominators_sha256': digest(run.denominators)})
    make(OutboxEvent, event_type='evaluation_run.requested', schema_version='evaluation-run-1', aggregate_id=run.id,
        aggregate_version=1, occurred_at=now, correlation_id=eval_task.id, task=eval_task,
        resource_refs={'evaluation_run_id': str(run.id), 'copy': canary}, available_at=now)
    todo = make(ar.PersonalRecordTask, **patient_args, owner_id=actor.id, version=1,
        title=canary + ':todo', description=f'{canary}:source version {case.first.id}', status='pending',
        retention_policy=case.evidence.retention, created_at=now, updated_at=now)
    reminder = make(ar.MaterialReminder, **patient_args, owner_id=actor.id, version=1, target_type='record_task',
        target_ref=todo.id, target_version=1, timezone_name='UTC', status='disabled', created_at=now, updated_at=now)
    make(ar.MaterialReminderNotice, **patient_args, reminder_id=reminder.id, reminder_version=1,
        scheduled_at=now, channel='in_app', deduplication_key=str(uuid4()), status='cancelled',
        permit_refs={'copy': canary}, receipt_refs=[{'copy': canary}], created_at=now)

    make(VoiceInput, patient=patient, actor=actor, request_id=uuid4(), transcript=canary + ':voice',
        transcript_sha256=digest(canary + ':voice'), transcription_evidence={'copy': canary},
        input_mode='SIMULATION_TEXT_ONLY', original_file=case.original, purpose_copy=purpose_copy,
        use_authorization=case.authorization, task=case.ocr_task, field_config=case.config, status='saved',
        candidates=[{'copy': canary}], raw_response_file=case.response_file, decisions=[{'copy': canary}],
        saved_record_version=case.first, confirmed_at=now, created_at=now, decided_at=now)
    make(TranscriptionReceipt, patient=patient, actor=actor, auth_epoch=actor.auth_epoch,
        use_authorization=case.authorization, retention_policy=case.evidence.retention,
        audio_sha256=case.original.checksum, audio_media_type='audio/wav', audio_size_bytes=case.original.size_bytes,
        duration_seconds=1, inference_duration_ms=1, model_name='SYNTHETIC_NO_INFERENCE', implementation_version='synthetic-1',
        transcript=canary + ':transcript', transcript_sha256=digest(canary + ':transcript'),
        confirmed_text=canary + ':confirmed_transcript', confirmed_text_sha256=digest(canary + ':confirmed_transcript'),
        confirmed_original_file=case.original, status='confirmed', created_at=now, confirmed_at=now)
    case.cross_domain = {'dataset': dataset, 'partition': partition, 'run': run, 'evaluation_task': eval_task,
        'document': document, 'document_version': document_version, 'notification': notification}
    return rows
