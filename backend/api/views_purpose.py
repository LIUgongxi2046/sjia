from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from django.urls import resolve
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, current_principal, handle
from archives.models import PurposeCopy, PurposeCopyReview
from audit.service import write_audit
from configurations.models import ConfigVersion
from files.models import FileObject
from files.purpose import audit_copy, checked_bytes, copy_content, copy_restrictions, copy_view, current_review, lock_copy_context, lock_file_access, lock_original, lock_prepared_image, lock_processing_config, lock_use_authorization, processing_page, purpose_result, review_copy, revoke_copy
from files.purpose_jobs import submit_copy
from files.purpose_schemas import PurposeCopyInput, PurposeDispositionApprovalInput, PurposeDispositionExecuteInput, PurposeDispositionRetainInput, PurposeDispositionRetryInput, PurposeIntentInput, PurposeOptionsInput, PurposeProviderCopyInput, PurposeReviewInput, PurposeRevokeInput, PurposeRetryInput, PurposeWriteHeaders
from sources.models import DataUseAuthorization
from files.storage import parse_range
from requests.models import WriteIntent
from requests.service import commit_intent, compute_digest, fail_intent, prepare_intent, retry_intent
from tasks.models import Task


def support_principal(request):
    principal, epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience != 'support':
        raise ApiError('SUPPORT_AUDIENCE_REQUIRED', 403)
    return principal, epoch


def locked_intent(request, principal, operation_id, patient):
    target_key = request.path.removeprefix('/api/v2')
    if not request.headers.get('X-Write-Intent'):
        raise ApiError('WRITE_INTENT_REQUIRED', 422)
    headers = PurposeWriteHeaders.model_validate({'intent_id': request.headers['X-Write-Intent'],
        'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')})
    intent = WriteIntent.objects.select_for_update().filter(id=headers.intent_id,
        actor=principal, operation_id=operation_id, target_key=target_key).first()
    if (intent is None or intent.payload_sha256 != compute_digest(operation_id, target_key, '2.0.0', request.data)
            or headers.request_id != intent.request_id or headers.generation != str(intent.generation)):
        raise ApiError('WRITE_INTENT_MISMATCH', 409)
    if intent.patient_id is not None and intent.patient_id != patient.id:
        raise ApiError('WRITE_INTENT_PATIENT_MISMATCH', 409)
    if intent.status != 'committed' and (intent.status != 'prepared' or intent.expires_at <= timezone.now()):
        raise ApiError('WRITE_INTENT_EXPIRED', 409)
    if intent.patient_id is None:
        intent.patient = patient
        intent.save(update_fields=['patient'])
    return intent


def write_receipt(intent, *, version, task_id=None):
    saved = intent.write_request
    return {'schema_version': '2.0.0', 'resource_type': saved.resource_type,
        'resource_id': str(saved.resource_id), 'version': str(version), 'saved_at': saved.completed_at.isoformat(),
        'request_id': str(saved.request_id), 'task_id': str(task_id) if task_id is not None else None}


INPUT_MODELS = {'purpose_copy_create': PurposeCopyInput, 'purpose_copy_review': PurposeReviewInput,
    'purpose_copy_revoke': PurposeRevokeInput, 'simulation_provider_copy_create': PurposeProviderCopyInput,
    'disposition_approve': PurposeDispositionApprovalInput,
    'disposition_execute': PurposeDispositionExecuteInput, 'disposition_retain': PurposeDispositionRetainInput,
    'disposition_retry': PurposeDispositionRetryInput}


def authorize_target(principal, epoch, operation_id, target_key, payload):
    if not target_key.startswith('/support/') or '?' in target_key or '#' in target_key:
        raise ApiError('TARGET_KEY_INVALID', 422)
    route = resolve('/api/v2' + target_key)
    if route.url_name != operation_id:
        raise ApiError('TARGET_KEY_INVALID', 422)
    if operation_id.startswith('disposition_'):
        from api.views_disposition import authorize_disposition_target

        disposition, policy, retention, context = authorize_disposition_target(principal, epoch, operation_id, route)
        return disposition.patient, retention, context
    if operation_id == 'purpose_copy_create':
        original = FileObject.objects.get(id=route.kwargs['file_id'])
        principal, patient, gate, group = lock_file_access(original, principal, epoch,
            purpose=payload.purpose, service_action='manage_data_use')
        purpose = payload.purpose
    else:
        copy, original, derived, principal, patient, gate, group = lock_copy_context(route.kwargs['copy_id'],
            principal, epoch, allow_unavailable=True)
        purpose = copy.purpose
    return patient, original.retention_policy, {'patient_id': str(patient.id), 'original_file_id': str(original.id),
        'purpose': purpose, 'environment': gate.environment}


def authorize_saved_intent(intent, principal, epoch):
    context = intent.authorization_context
    if context is None:
        raise ApiError('WRITE_INTENT_SCOPE_REQUIRED', 409)
    route = resolve('/api/v2' + intent.target_key)
    if route.url_name != intent.operation_id:
        raise ApiError('TARGET_KEY_INVALID', 422)
    if intent.operation_id.startswith('disposition_'):
        from api.views_disposition import authorize_disposition_target

        disposition, policy, retention, current = authorize_disposition_target(principal, epoch, intent.operation_id, route)
        if current != context or disposition.patient_id != intent.patient_id:
            raise ApiError('WRITE_INTENT_SCOPE_MISMATCH', 409)
        return
    if intent.operation_id == 'purpose_copy_create':
        original = FileObject.objects.get(id=route.kwargs['file_id'])
        principal, patient, gate, group = lock_file_access(original, principal, epoch,
            purpose=context['purpose'], service_action='manage_data_use')
    else:
        copy, original, derived, principal, patient, gate, group = lock_copy_context(route.kwargs['copy_id'],
            principal, epoch, allow_unavailable=True)
        if copy.purpose != context['purpose']:
            raise ApiError('WRITE_INTENT_SCOPE_MISMATCH', 409)
    if (str(patient.id) != context['patient_id'] or patient.id != intent.patient_id
            or str(original.id) != context['original_file_id'] or gate.environment != context['environment']):
        raise ApiError('WRITE_INTENT_SCOPE_MISMATCH', 409)


def intent_view(intent):
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'generation': str(intent.generation),
        'status': intent.status, 'expires_at': intent.expires_at.isoformat(), 'payload_sha256': intent.payload_sha256}


def prepare_purpose_intent(request):
    principal, epoch = support_principal(request)
    data = PurposeIntentInput.model_validate(request.data)
    payload = INPUT_MODELS[data.operation_id].model_validate(data.payload)
    patient, retention, context = authorize_target(principal, epoch, data.operation_id, data.target_key, payload)
    intent = prepare_intent(actor=principal, operation_id=data.operation_id, target_key=data.target_key,
        request_id=data.request_id, payload=data.payload, schema_version='2.0.0', retention_policy=retention,
        patient=patient, authorization_context=context)
    return Response(intent_view(intent), status=201)


def read_purpose_request(request, intent_id):
    principal, epoch = support_principal(request)
    intent = WriteIntent.objects.get(id=intent_id, actor=principal, operation_id__in=INPUT_MODELS)
    authorize_saved_intent(intent, principal, epoch)
    intent = WriteIntent.objects.select_for_update().get(id=intent.id, actor=principal)
    if intent.status == 'prepared' and intent.expires_at <= timezone.now() and intent.write_request_id is None:
        fail_intent(intent=intent, error_code='WRITE_INTENT_EXPIRED')
    receipt = None
    if intent.status == 'committed':
        receipt = write_receipt(intent, version=intent.write_request.expected_version or '1',
            task_id=intent.write_request.resource_id if intent.write_request.resource_type == 'task' else None)
    return Response({'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': intent.operation_id,
        'target_key': intent.target_key, 'prepared_at': intent.prepared_at.isoformat(),
        'state': {'prepared': 'pending', 'committed': 'committed', 'failed': 'failed'}[intent.status],
        'may_retry': intent.status == 'failed', 'receipt': receipt, 'error_code': intent.error_code})


def retry_purpose_intent(request, intent_id):
    principal, epoch = support_principal(request)
    data = PurposeRetryInput.model_validate(request.data)
    intent = WriteIntent.objects.get(id=intent_id, actor=principal, operation_id__in=INPUT_MODELS)
    authorize_saved_intent(intent, principal, epoch)
    intent = WriteIntent.objects.select_for_update().get(id=intent.id, actor=principal)
    retry_intent(intent=intent, expected_generation=int(data.expected_generation))
    return Response(intent_view(intent))


@api_view(['POST'])
@handle
@transaction.atomic
def purpose_intent_prepare(request):
    return prepare_purpose_intent(request)


@api_view(['GET'])
@handle
@transaction.atomic
def purpose_request_read(request, intent_id):
    return read_purpose_request(request, intent_id)


@api_view(['POST'])
@handle
@transaction.atomic
def purpose_intent_retry(request, intent_id):
    return retry_purpose_intent(request, intent_id)


@api_view(['POST'])
@handle
@transaction.atomic
def purpose_copy_create(request, file_id):
    principal, epoch = support_principal(request)
    payload = PurposeCopyInput.model_validate(request.data)
    original = FileObject.objects.get(id=file_id)
    principal, patient, gate, group = lock_file_access(original, principal, epoch,
        purpose=payload.purpose, service_action='manage_data_use')
    intent = locked_intent(request, principal, 'purpose_copy_create', patient)
    if intent.status == 'committed':
        task = Task.objects.get(id=intent.write_request.resource_id, actor=principal, patient=patient)
        return Response(write_receipt(intent, version='1', task_id=task.id), status=202)
    task = submit_copy(file_id=file_id, actor=principal, auth_epoch=epoch, payload=payload)
    saved = commit_intent(intent=intent, action='purpose_copy_create', resource_type='task', resource_id=task.id)
    task.request_row = saved
    task.save(update_fields=['request_row'])
    return Response(write_receipt(intent, version='1', task_id=task.id), status=202)


@api_view(['GET'])
@handle
@transaction.atomic
def purpose_copy_options(request, file_id, purpose):
    principal, epoch = support_principal(request)
    payload = PurposeOptionsInput.model_validate({'purpose': purpose})
    original = FileObject.objects.get(id=file_id)
    principal, patient, gate, group = lock_file_access(original, principal, epoch,
        purpose=payload.purpose, service_action='manage_data_use')
    original = lock_original(original.id)
    checked_bytes(original)
    now = timezone.now()
    authorizations = []
    for item in DataUseAuthorization.objects.filter(patient=patient, source_revision_id=group.source_revision_id,
            purpose=payload.purpose, environment=gate.environment, revoked_at__isnull=True,
            approved_at__lte=now).exclude(valid_until__lte=now).order_by('-approved_at', 'id'):
        auth = lock_use_authorization(item.id, original, payload.purpose, gate.environment)
        authorizations.append({'authorization_id': str(auth.id), 'evidence_ref': auth.evidence_ref,
            'approved_at': auth.approved_at.isoformat(),
            'valid_until': auth.valid_until.isoformat() if auth.valid_until is not None else None})
    configs = []
    for item in ConfigVersion.objects.filter(kind='provider',
            schema_version__in=['purpose-processing-2', 'purpose-processing-text-1'], status='enabled',
            payload__original_file_id=str(original.id), payload__patient_id=str(patient.id),
            payload__purpose=payload.purpose, payload__environment=gate.environment).order_by('config_key', '-version', 'id'):
        config, plan, approvals, validations = lock_processing_config(item.id, original,
            payload.purpose, item.payload['target'], gate.environment)
        lock_prepared_image(plan, original)
        page, page_number = processing_page(plan, original)
        configs.append({'processing_config_id': str(config.id), 'version': str(config.version),
            'file_page_id': str(page.id), 'source_page_number': page_number,
            'checksum': config.checksum, 'target': plan.target, 'date_strategy': plan.date_strategy,
            'approval_evidence_refs': [approval.evidence_ref for approval in approvals],
            'validation_evidence_refs': [validation.evidence_ref for validation in validations]})
    write_audit(actor=principal, patient=patient, action='purpose_copy_options', object_type='file_object',
        object_id=original.id, object_version=original.object_version, outcome='allowed',
        retention_policy=original.retention_policy)
    return Response({'patient_id': str(patient.id), 'original_file_id': str(original.id),
        'original_version': original.object_version, 'original_checksum': original.checksum,
        'purpose': payload.purpose, 'environment': gate.environment,
        'authorizations': authorizations, 'processing_configs': configs})


@api_view(['GET'])
@handle
@transaction.atomic
def purpose_copy_read(request, copy_id):
    principal, epoch = support_principal(request)
    copy, original, derived, principal, patient, gate, group = lock_copy_context(copy_id, principal, epoch, allow_unavailable=True)
    review = current_review(copy, original, derived)
    result = copy_view(copy, original, derived, review, restrictions=copy_restrictions(copy, original, derived), actor=principal)
    audit_copy(copy, original, principal, 'purpose_copy_read')
    return Response(result)


@api_view(['POST'])
@handle
@transaction.atomic
def purpose_copy_review(request, copy_id):
    principal, epoch = support_principal(request)
    payload = PurposeReviewInput.model_validate(request.data)
    copy, original, derived, principal, patient, gate, group = lock_copy_context(copy_id, principal, epoch, allow_unavailable=True)
    intent = locked_intent(request, principal, 'purpose_copy_review', patient)
    if intent.status != 'committed':
        copy = review_copy(copy_id=copy.id, actor=principal, auth_epoch=epoch, payload=payload, request_id=intent.request_id)
        commit_intent(intent=intent, action='purpose_copy_review', resource_type='purpose_copy', resource_id=copy.id,
            expected_version=int(payload.expected_version))
    return Response(write_receipt(intent, version=payload.expected_version), status=201)


@api_view(['POST'])
@handle
@transaction.atomic
def purpose_copy_revoke(request, copy_id):
    principal, epoch = support_principal(request)
    payload = PurposeRevokeInput.model_validate(request.data)
    copy, original, derived, principal, patient, gate, group = lock_copy_context(copy_id, principal, epoch, allow_unavailable=True)
    intent = locked_intent(request, principal, 'purpose_copy_revoke', patient)
    if intent.status != 'committed':
        copy = revoke_copy(copy_id=copy.id, actor=principal, auth_epoch=epoch, payload=payload, request_id=intent.request_id)
        commit_intent(intent=intent, action='purpose_copy_revoke', resource_type='purpose_copy', resource_id=copy.id,
            expected_version=int(payload.expected_version))
    return Response(write_receipt(intent, version=payload.expected_version))


@api_view(['POST'])
@handle
@transaction.atomic
def simulation_provider_copy_create(request, copy_id):
    principal, epoch = support_principal(request)
    payload = PurposeProviderCopyInput.model_validate(request.data)
    copy, original, derived, principal, patient, gate, group = lock_copy_context(
        copy_id, principal, epoch, service_action='manage_data_use')
    if str(copy.version) != payload.expected_version:
        raise ApiError('VERSION_CONFLICT', 409)
    intent = locked_intent(request, principal, 'simulation_provider_copy_create', patient)
    if intent.status != 'committed':
        from retention.external_deletion import create_simulated_provider_copy

        provider_copy = create_simulated_provider_copy(purpose_copy_id=copy.id, actor=principal,
            auth_epoch=epoch, review_id=payload.review_id)
        commit_intent(intent=intent, action='simulation_provider_copy_create',
            resource_type='provider_copy', resource_id=provider_copy.id, expected_version=payload.expected_version)
    else:
        from retention.models import SimulatedProviderCopy

        provider_copy = SimulatedProviderCopy.objects.get(id=intent.write_request.resource_id,
            purpose_copy=copy, purpose_copy_version=copy.version)
    return Response(write_receipt(intent, version=payload.expected_version), status=201)


def content_response(request, data, file):
    bounds = parse_range(request.headers.get('Range'), len(data))
    headers = {'Accept-Ranges': 'bytes', 'Content-Disposition': 'inline',
        'X-Content-Type-Options': 'nosniff', 'ETag': f'"{file.checksum}"'}
    if bounds is None:
        return HttpResponse(data, content_type=file.media_type, headers=headers)
    start, end = bounds
    headers['Content-Range'] = f'bytes {start}-{end}/{len(data)}'
    return HttpResponse(data[start:end + 1], content_type=file.media_type, status=206, headers=headers)


@api_view(['GET'])
@handle
@transaction.atomic
def purpose_copy_content(request, copy_id):
    principal, epoch = support_principal(request)
    copy = PurposeCopy.objects.get(id=copy_id)
    data, file = copy_content(copy_id=copy.id, actor=principal, auth_epoch=epoch,
        purpose=copy.purpose, target=copy.processing_evidence['target'])
    return content_response(request, data, file)


@api_view(['GET'])
@handle
@transaction.atomic
def purpose_review_content(request, copy_id):
    principal, epoch = support_principal(request)
    copy = PurposeCopy.objects.get(id=copy_id)
    data, file = copy_content(copy_id=copy.id, actor=principal, auth_epoch=epoch,
        purpose=copy.purpose, target=copy.processing_evidence['target'], review_only=True)
    return content_response(request, data, file)


@api_view(['GET'])
@handle
@transaction.atomic
def purpose_copy_evidence(request, copy_id):
    principal, epoch = support_principal(request)
    copy, original, derived, principal, patient, gate, group = lock_copy_context(copy_id, principal, epoch, allow_unavailable=True)
    audit_copy(copy, original, principal, 'purpose_copy_evidence')
    review = PurposeCopyReview.objects.filter(copy_id=copy.id, copy_version=copy.version,
        patient_id=copy.patient_id).order_by('-reviewed_at', '-id').first()
    from retention.models import SimulatedProviderCopy

    simulated_provider_copies = list(SimulatedProviderCopy.objects.filter(purpose_copy=copy,
        purpose_copy_version=copy.version, environment='simulation').order_by('created_at', 'id'))
    return Response({'copy_id': str(copy.id), 'version': str(copy.version), 'environment': copy.environment,
        'use_authorization_id': str(copy.use_authorization_id), 'processing_version': copy.processing_version,
        'processing_evidence': copy.processing_evidence, 'date_strategy': copy.date_strategy,
        'clinical_integrity_status': copy.clinical_integrity_status, 'created_by': str(copy.created_by),
        'restriction_reasons': copy_restrictions(copy, original, derived),
        'created_at': copy.created_at.isoformat(), 'disposition_ref': str(copy.disposition_ref) if copy.disposition_ref else None,
        'reviewer_id': str(review.reviewer_id) if review is not None else None,
        'reviewed_at': review.reviewed_at.isoformat() if review is not None else None,
        'review_evidence_ref': review.evidence_ref if review is not None else None,
        'date_review': copy.processing_evidence.get('date_review'),
        'simulated_provider_copies': [{'provider_copy_id': str(item.id), 'provider_id': item.provider_id,
            'object_id': str(item.object_id), 'object_version': item.object_version, 'checksum': item.checksum,
            'size_bytes': item.size_bytes, 'status': item.status, 'deletion_request_id': str(item.deletion_request_id) if item.deletion_request_id else None,
            'receipt_ref': item.receipt_ref, 'receipt_sha256': item.receipt_sha256}
            for item in simulated_provider_copies]})


@api_view(['GET'])
@handle
@transaction.atomic
def purpose_task_read(request, task_id):
    voice_task = Task.objects.filter(id=task_id, schema_version='voice-input-1').first()
    if voice_task is not None:
        from api.views_voice_inputs import locked_voice, voice_task_view
        from recognition.models import VoiceInput

        actor, _, _ = current_principal(request)
        if actor is None:
            raise ApiError('AUTH_REQUIRED', 401)
        row = VoiceInput.objects.get(task=voice_task, actor=actor)
        row, *_ = locked_voice(request, voice_task.patient_id, row.id)
        return Response(voice_task_view(row))
    principal, epoch = support_principal(request)
    evaluation = Task.objects.filter(id=task_id, task_type='assessment', schema_version='evaluation-run-1').first()
    if evaluation is not None:
        from case_details.evaluation_runs import evaluation_task_view, lock_run_read

        run, task, _, runtime = lock_run_read(run_id=evaluation.input_refs['evaluation_run_id'], actor=principal, auth_epoch=epoch)
        if task.id != evaluation.id:
            raise ApiError('DATASET_RUN_TASK_CHANGED', 409)
        return Response(evaluation_task_view(run, task, runtime))
    task = Task.objects.get(id=task_id, actor=principal, task_type__in=[Task.TaskType.PURPOSE_COPY, Task.TaskType.DISPOSITION])
    allowed_actions = []
    if task.task_type == 'disposition':
        if task.schema_version == 'simulation-provider-deletion-1':
            linked = task.input_refs.get('disposition_ref')
            if linked is None:
                from retention.external_deletion import read_simulated_external_deletion_task

                provider_result = read_simulated_external_deletion_task(task.id)
                result = {'result_type': None, 'result_ref': None,
                    'error_code': None if provider_result['provider_state'] in ('deleted', 'not_requested')
                    else 'EXTERNAL_DELETE_RESULT_UNKNOWN'}
            else:
                from retention.jobs import read_simulated_provider_disposition

                provider_result = read_simulated_provider_disposition(task_id=task.id, actor=principal,
                    auth_epoch=epoch)
                result = {'result_type': 'disposition' if provider_result['provider_state'] == 'deleted' else None,
                    'result_ref': linked['disposition_id'] if provider_result['provider_state'] == 'deleted' else None,
                    'error_code': None if provider_result['provider_state'] in ('deleted', 'not_requested')
                    else 'EXTERNAL_DELETE_RESULT_UNKNOWN'}
        else:
            from retention.jobs import disposition_task_result, validate_refs

            validate_refs(task, principal, epoch, require_actor_epoch=False)
            result = disposition_task_result(task)
            if task.status in ('failed', 'awaiting_resolution'):
                allowed_actions = ['retry']
    else:
        original = FileObject.objects.get(id=task.input_refs['original_file_id'], patient=task.patient)
        lock_file_access(original, principal, epoch, purpose=task.input_refs['purpose'], service_action='manage_data_use')
        result = purpose_result(task)
    terminal = task.status in ('completed', 'failed', 'cancelled', 'superseded', 'awaiting_resolution', 'uncertain')
    return Response({'task_id': str(task.id), 'task_type': task.task_type,
        'request_id': str(task.request_row.request_id) if task.request_row_id else None,
        'accepted_at': task.created_at.isoformat(), 'status': task.status, 'status_version': str(task.status_version),
        'poll_after_ms': None if terminal else 1500, 'allowed_actions': allowed_actions, **result})
