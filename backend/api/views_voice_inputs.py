from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response
from pydantic import Field
from uuid import UUID

from api.base import ApiError, current_principal, get_patient, handle
from authorization.policy import evaluate
from django.conf import settings
from files.purpose import checked_bytes, lock_original, lock_use_authorization
from recognition.models import VoiceInput
from recognition.voice_input_schemas import VoiceInputCreate, VoiceModel
from recognition.voice_input_service import adopt_voice_input, cancel_voice_input, create_voice_input, prepare_voice_processing
from tasks.models import Task, TaskAttempt
from django.utils import timezone
from archives.models import PurposeCopy
from authorization.policy import matching_case_grants
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from family_content.models import EnvironmentGate
from files.models import FileGroup, FileObject
from files.purpose import copy_restrictions, current_review
from sources.models import DataUseAuthorization


class VoiceCreateRequest(VoiceInputCreate):
    original_file_id: UUID = Field(strict=False)


class VoiceCancelRequest(VoiceModel):
    expected_status_version: str = Field(pattern=r'^[1-9][0-9]*$')


class VoiceInputLookup(VoiceModel):
    request_id: UUID = Field(strict=False)


def voice_actor(request, patient_id):
    actor, epoch, audience = current_principal(request)
    if actor is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience != 'family':
        raise ApiError('FAMILY_AUDIENCE_REQUIRED', 403)
    patient = get_patient(patient_id)
    action = 'read_raw' if request.method == 'GET' else 'write_record'
    evaluate(principal=actor, patient=patient, action=action, purpose='family_observation',
        environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=epoch, require_relation=True)
    return actor, epoch, patient


def locked_voice(request, patient_id, voice_id):
    actor, epoch, patient = voice_actor(request, patient_id)
    row = VoiceInput.objects.select_for_update().get(
        id=voice_id, patient=patient, actor=actor)
    original = lock_original(row.original_file_id)
    checked_bytes(original)
    lock_use_authorization(row.use_authorization_id, original, 'external_recognition', settings.SJIA_ENVIRONMENT)
    return row, actor, epoch, patient


def voice_view(row):
    task = row.task
    attempts = list(TaskAttempt.objects.filter(task_id=row.task_id).order_by('attempt_number')) if task else []
    return {'id': str(row.id), 'patient_id': str(row.patient_id), 'request_id': str(row.request_id),
        'input_mode': row.input_mode, 'status': row.status, 'status_version': str(row.status_version),
        'transcript': row.transcript, 'transcript_sha256': row.transcript_sha256,
        'original_file_id': str(row.original_file_id), 'field_config_id': str(row.field_config_id),
        'task_id': str(task.id) if task else None, 'task_status': task.status if task else None,
        'task_status_version': str(task.status_version) if task else None,
        'provider_config_id': str(row.provider_config_id) if row.provider_config_id else None,
        'purpose_copy_id': str(row.purpose_copy_id) if row.purpose_copy_id else None,
        'candidates': row.candidates, 'decisions': row.decisions,
        'saved_record_version_id': str(row.saved_record_version_id) if row.saved_record_version_id else None,
        'raw_response_file_id': str(row.raw_response_file_id) if row.raw_response_file_id else None,
        'provider_evidence': [item.usage_evidence for item in attempts],
        'transcription_evidence': row.transcription_evidence,
        'confirmed_at': row.confirmed_at.isoformat(),
        'created_at': row.created_at.isoformat(),
        'decided_at': row.decided_at.isoformat() if row.decided_at else None}


def voice_task_view(row):
    task = row.task
    if task is None:
        raise ApiError('VOICE_TASK_NOT_FOUND', 404)
    attempts = list(TaskAttempt.objects.filter(task=task).order_by('attempt_number'))
    error_code = attempts[-1].error_code if attempts else None
    terminal = task.status in ('completed', 'failed', 'cancelled', 'superseded', 'uncertain', 'awaiting_resolution')
    return {'task_id': str(task.id), 'task_type': task.task_type, 'request_id': str(row.request_id),
        'accepted_at': task.created_at.isoformat(), 'status': task.status,
        'status_version': str(task.status_version), 'poll_after_ms': None if terminal else 1500,
        'allowed_actions': ['cancel'] if row.status in ('ready', 'running', 'awaiting_confirmation') else [],
        'result_type': 'voice_input', 'result_ref': str(row.id), 'error_code': error_code}


@api_view(['POST', 'GET'])
@handle
@transaction.atomic
def voice_inputs(request, patient_id):
    actor, epoch, patient = voice_actor(request, patient_id)
    if request.method == 'GET':
        lookup = VoiceInputLookup.model_validate(request.query_params.dict())
        row = VoiceInput.objects.get(actor=actor, patient=patient, request_id=lookup.request_id)
        row, *_ = locked_voice(request, patient_id, row.id)
        return Response(voice_view(row))
    payload = VoiceCreateRequest.model_validate(request.data)
    row = create_voice_input(patient=patient, actor=actor, auth_epoch=epoch,
        original_file_id=payload.original_file_id,
        request=payload.model_dump(mode='json', exclude={'original_file_id'}))
    return Response(voice_view(row), status=201)


@api_view(['GET'])
@handle
@transaction.atomic
def voice_input_read(request, patient_id, voice_id):
    row, *_ = locked_voice(request, patient_id, voice_id)
    return Response(voice_view(row))


@api_view(['POST'])
@handle
@transaction.atomic
def voice_input_process(request, patient_id, voice_id):
    row, actor, epoch, _ = locked_voice(request, patient_id, voice_id)
    prepare_voice_processing(voice_input_id=row.id, actor=actor, auth_epoch=epoch, request=request.data)
    row.refresh_from_db()
    return Response(voice_view(row), status=202)


@api_view(['POST'])
@handle
@transaction.atomic
def voice_input_adopt(request, patient_id, voice_id):
    row, actor, epoch, _ = locked_voice(request, patient_id, voice_id)
    if str(request.data.get('voice_input_id')) != str(row.id):
        raise ApiError('VOICE_INPUT_ID_MISMATCH', 422)
    result = adopt_voice_input(request=request.data, actor=actor, auth_epoch=epoch)
    row.refresh_from_db()
    return Response({'voice_input': voice_view(row), **result})


@api_view(['POST'])
@handle
@transaction.atomic
def voice_input_cancel(request, patient_id, voice_id):
    row, actor, epoch, _ = locked_voice(request, patient_id, voice_id)
    payload = VoiceCancelRequest.model_validate(request.data)
    cancel_voice_input(voice_input_id=row.id, actor=actor, auth_epoch=epoch,
        expected_status_version=payload.expected_status_version)
    row.refresh_from_db()
    return Response(voice_view(row))


def original_purpose_state(original, patient, target):
    copies = list(PurposeCopy.objects.select_for_update().filter(patient_id=patient.id,
        original_file_id=original.id, purpose='external_recognition', environment=settings.SJIA_ENVIRONMENT,
        processing_evidence__target=target).order_by('-created_at', '-id'))
    available = []
    reasons = {}
    for copy in copies:
        if copy.status != 'approved':
            continue
        derived = FileObject.objects.get(id=copy.derived_file_id, patient=patient)
        restrictions = copy_restrictions(copy, original, derived)
        if not restrictions and current_review(copy, original, derived) is not None:
            available.append(copy)
        else:
            reasons[copy.id] = restrictions[0] if restrictions else 'PURPOSE_REVIEW_UNAVAILABLE'
    task = Task.objects.filter(patient=patient, task_type='purpose_copy',
        input_refs__original_file_id=str(original.id), input_refs__purpose='external_recognition',
        input_refs__environment=settings.SJIA_ENVIRONMENT, input_refs__target=target).order_by('-created_at', '-id').first()
    copy = available[0] if len(available) == 1 else (copies[0] if copies else None)
    result = {'purpose_copy_id': str(copy.id) if copy else None,
        'purpose_request_id': str(task.request_row.request_id) if task and task.request_row_id else None,
        'purpose_task_id': str(task.id) if task else None, 'purpose_state': 'not_requested',
        'purpose_reason_code': None, 'state': 'awaiting_review'}
    if len(available) == 1:
        return {**result, 'state': 'available', 'purpose_state': 'approved'}
    if len(available) > 1:
        return {**result, 'purpose_state': 'approved', 'purpose_reason_code': 'PURPOSE_COPY_SELECTION_REQUIRED'}
    if task and task.status in ('accepted', 'queued', 'running', 'blocked', 'cancel_requested', 'uncertain', 'awaiting_resolution'):
        return {**result, 'purpose_state': 'processing', 'purpose_reason_code': task.status.upper()}
    if task and task.status in ('failed', 'cancelled', 'superseded') and (copy is None or task.created_at > copy.created_at):
        attempt = TaskAttempt.objects.filter(task=task).order_by('-attempt_number').first()
        return {**result, 'purpose_state': 'failed',
            'purpose_reason_code': attempt.error_code if attempt and attempt.error_code else task.status.upper()}
    if copy:
        states = {'pending_review': 'pending_review', 'approved': 'approved', 'rejected': 'rejected',
            'revoked': 'revoked', 'disposed': 'disposed', 'deleted': 'disposed'}
        if copy.status not in states:
            raise ApiError('PURPOSE_COPY_STATUS_INVALID', 422)
        return {**result, 'purpose_state': states[copy.status], 'purpose_reason_code': reasons.get(copy.id)}
    return result


@api_view(['GET'])
@handle
@transaction.atomic
def voice_input_options(request, patient_id):
    actor, epoch, patient = voice_actor(request, patient_id)
    now = timezone.now()
    grants = list(matching_case_grants(actor, patient, 'write_record', now,
        author_id=actor.id, require_relation=True, purpose='family_observation'))
    codes = {code for grant in grants for code in grant.field_codes}
    gate = EnvironmentGate.objects.get(environment=settings.SJIA_ENVIRONMENT)
    runtime = gate.runtime_config
    base = {'patient_id': str(patient.id), 'state': 'unavailable', 'reason_code': 'VOICE_CONFIGURATION_REQUIRED',
        'field_config_id': None, 'provider_config_id': None, 'source_options': [], 'original_options': [],
        'input_modes': ['LOCAL_AUDIO_CONFIRMED_TEXT', 'SIMULATION_TEXT_ONLY']
            if settings.SJIA_ENVIRONMENT == 'simulation' else ['LOCAL_AUDIO_CONFIRMED_TEXT']}
    if (runtime is None or not gate.enabled
            or configuration_qualification_reason(runtime, settings.SJIA_ENVIRONMENT) is not None):
        return Response(base)
    entries = [entry for entry in runtime.payload['providers'] if entry['kind'] == 'voice' and entry['enabled']]
    if len(entries) != 1:
        return Response(base)
    provider = ConfigVersion.objects.get(id=entries[0]['config_id'], kind='provider', schema_version='provider-1')
    fields = ConfigVersion.objects.get(id=provider.payload['field_config_id'], kind='content', schema_version='field-config-1')
    if (configuration_qualification_reason(provider, settings.SJIA_ENVIRONMENT) is not None
            or configuration_qualification_reason(fields, settings.SJIA_ENVIRONMENT) is not None
            or fields.payload['kind'] != 'observation' or not {'temp', 'observation_note'} <= codes):
        return Response(base)
    file_grants = list(matching_case_grants(actor, patient, 'read_file', now,
        require_relation=True, whole_resource=True, purpose='external_recognition'))
    if not file_grants:
        return Response({**base, 'reason_code': 'VOICE_SCOPE_DENIED'})
    authorizations = list(DataUseAuthorization.objects.filter(patient=patient, environment=settings.SJIA_ENVIRONMENT,
        purpose__in=['family_observation', 'external_recognition'], revoked_at=None,
        approved_at__lte=now).exclude(valid_until__lte=now).select_related('source_revision__source', 'approved_by'))
    valid = [item for item in authorizations if item.evidence_ref.strip()
        and item.approved_by.account_status == 'active' and item.source_revision.status == 'approved'
        and item.source_revision.source.status == 'approved' and item.source_revision.rights_evidence_ref.strip()
        and item.purpose in item.source_revision.allowed_use]
    source_options = []
    for external in valid:
        if external.purpose != 'external_recognition':
            continue
        family = [item for item in valid if item.purpose == 'family_observation'
            and item.source_revision_id == external.source_revision_id]
        if len(family) == 1:
            source_options.append({'source_revision_id': str(external.source_revision_id),
                'family_authorization_id': str(family[0].id), 'external_authorization_id': str(external.id)})
    originals = []
    source_ids = [item['source_revision_id'] for item in source_options]
    groups = {group.id: group for group in FileGroup.objects.filter(patient=patient, owner=actor,
        status='available', source_revision_id__in=source_ids)}
    for original in FileObject.objects.filter(patient=patient, purpose='original', status='available',
            media_type='text/plain', file_group_id__in=groups).order_by('created_at', 'id'):
        text = checked_bytes(original).decode('utf-8', errors='strict')
        if not text.strip() or len(text) > 8000:
            continue
        originals.append({'original_file_id': str(original.id), 'checksum': original.checksum,
            'confirmed_text': text, 'source_revision_id': str(groups[original.file_group_id].source_revision_id),
            **original_purpose_state(original, patient, entries[0]['endpoint'].rstrip('/'))})
    return Response({**base, 'state': 'available' if source_options else 'unavailable',
        'reason_code': None if source_options else 'VOICE_AUTHORIZATION_REQUIRED',
        'field_config_id': str(fields.id), 'provider_config_id': str(provider.id),
        'source_options': source_options, 'original_options': originals})
