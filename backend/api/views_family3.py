"""家庭端 API：文件/原件、识别候选、更正申请、字典、处置、联系与提示详情。"""
import uuid
from datetime import datetime, timezone
from pydantic import BaseModel
from django.db import transaction
from django.utils.http import parse_header_parameters
from django.conf import settings

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from alerting.models import AlertEvent, AlertRecipient
from api.base import ApiError, authorize_case, current_principal, get_patient, handle
from contacts.models import ContactVersion
from dictionaries.models import DictionaryEntry
from files.models import FileGroup, FileObject, FilePage, UploadPart
from files.service import complete_file_group, create_file_group, declare_upload_part, store_file_object
from files.uploading import UploadGroupInput, select_upload_authorization, upload_options
from files.purpose import checked_bytes, lock_use_authorization
from archives.batches import batch_runtime
from files.storage import RangeNotSatisfiable, parse_range
from files.purpose_schemas import PurposeDispositionInput, PurposeDispositionScope
from recognition.ark import ArkRejected, recognize_report_image, recognize_therapy_image, structure_voice_text
from recognition.schemas import RecognitionRequest, ReportRequest, VoiceRequest
from recognition.models import CandidateSet, CandidateField, CandidateConfirmation
from recognition.jobs import submit_recognition
from records.models import Record
from retention.disposition import create_disposition_request, scope_sha256
from retention.models import DispositionItem, RetentionPolicy
from sources.models import DataUseAuthorization
from tasks.models import Task
from verification.models import CorrectionRequest
from records.snapshot import record_read_snapshot


class TranscriptionAuthorizationInput(BaseModel):
    authorization_id: uuid.UUID


@api_view(["POST"])
@handle
@transaction.atomic
def file_group_create(request, patient_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    patient = get_patient(patient_id)
    authorize_case(request, principal, patient, "write_record")
    payload = UploadGroupInput.model_validate(request.data)
    use_auth = select_upload_authorization(patient, payload)
    revision = use_auth.source_revision
    group = FileGroup.objects.select_for_update().filter(id=payload.client_group_id).first() if payload.client_group_id else None
    if group is not None:
        if group.patient_id != patient.id or group.owner_id != principal.id or group.source_revision_id != revision.id or group.use_authorization_id != use_auth.id:
            raise ApiError('UPLOAD_REQUEST_MISMATCH', 409)
    else:
        group = create_file_group(patient=patient, owner=principal, source_revision=revision, use_authorization_id=use_auth.id, group_id=payload.client_group_id)
    return Response({"group_id": str(group.id), "version": str(group.version), "status": group.status}, status=201)


@api_view(['GET'])
@handle
@record_read_snapshot
def archive_upload_options(request, patient_id):
    principal, _, audience = current_principal(request)
    if principal is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience != 'family':
        raise ApiError('FAMILY_AUDIENCE_REQUIRED', 403)
    patient = get_patient(patient_id)
    authorize_case(request, principal, patient, 'write_record', author_id=principal.id, whole_resource=True, lock=False)
    return Response(upload_options(patient))


@api_view(["PUT"])
@handle
@transaction.atomic
def file_upload(request, group_id, upload_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    group = FileGroup.objects.filter(id=group_id, owner=principal).first()
    if group is None:
        raise ApiError("GROUP_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    authorize_case(request, principal, group.patient, "write_record", author_id=group.owner_id, whole_resource=True)
    from identity.models import Patient
    patient = Patient.objects.select_for_update().get(id=group.patient_id)
    if patient.status != 'active':
        raise ApiError('PATIENT_UNAVAILABLE', 409)
    group = FileGroup.objects.select_for_update().get(id=group.id, owner=principal, patient=patient)
    limits = batch_runtime().payload['limits']
    revision = group.source_revision
    policies = list(RetentionPolicy.objects.select_for_update().filter(
        id__in=batch_runtime().payload['retention_policy_ids'], data_class='file', status='active'))
    if len(policies) != 1 or policies[0].approved_by_id is None or policies[0].approved_at is None or not policies[0].evidence_ref:
        raise ApiError('CONFIGURATION_PENDING', status.HTTP_503_SERVICE_UNAVAILABLE)
    retention = policies[0]
    declared_checksum = request.query_params.get("checksum", "")
    size_text = request.query_params.get('size', '')
    ordinal_text = request.query_params.get('ordinal', '1')
    if not size_text.isascii() or not size_text.isdecimal() or len(size_text) > 9:
        raise ApiError('FILE_SIZE_INVALID', status.HTTP_422_UNPROCESSABLE_ENTITY)
    if not ordinal_text.isascii() or not ordinal_text.isdecimal() or len(ordinal_text) > 19 or not 1 <= int(ordinal_text) <= limits['max_pages']:
        raise ApiError('FILE_ORDINAL_INVALID', status.HTTP_422_UNPROCESSABLE_ENTITY)
    declared_size = int(size_text)
    media_type = request.query_params.get("media_type", "image/png")
    data = request.body
    if declared_size <= 0 or declared_size > limits['max_upload_bytes'] or len(data) > limits['max_upload_bytes']:
        raise ApiError('FILE_SIZE_INVALID', status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    if media_type not in limits['media_types']:
        raise ApiError('FILE_TYPE_INVALID', status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
    select_upload_authorization(group.patient, UploadGroupInput(authorization_id=group.use_authorization_id))
    existing = UploadPart.objects.filter(file_group_id=group.id, client_upload_id=upload_id).first()
    if existing is not None:
        if (existing.status != 'stored' or existing.ordinal != int(ordinal_text)
                or existing.declared_checksum != declared_checksum or existing.declared_size_bytes != declared_size
                or existing.declared_media_type != media_type):
            raise ApiError('UPLOAD_REQUEST_MISMATCH', 409)
        saved = FileObject.objects.get(id=existing.file_id, patient=group.patient, file_group_id=group.id)
        lock_use_authorization(group.use_authorization_id, saved, 'family_observation', settings.SJIA_ENVIRONMENT)
        if checked_bytes(saved) != data:
            raise ApiError('UPLOAD_REQUEST_MISMATCH', 409)
        return Response({'file_id': str(saved.id), 'checksum': saved.checksum, 'size_bytes': saved.size_bytes}, status=201)
    if UploadPart.objects.filter(client_upload_id=upload_id, patient_id=group.patient_id).exists():
        raise ApiError('UPLOAD_REQUEST_MISMATCH', 409)
    if group.status not in (FileGroup.Status.INITIATED, FileGroup.Status.UPLOADED):
        raise ApiError('FILE_GROUP_NOT_EDITABLE', status.HTTP_409_CONFLICT)
    if UploadPart.objects.filter(file_group_id=group.id, ordinal=int(ordinal_text)).exists():
        raise ApiError('FILE_ORDINAL_CONFLICT', status.HTTP_409_CONFLICT)
    part = declare_upload_part(file_group=group, client_upload_id=upload_id, ordinal=int(ordinal_text), declared_checksum=declared_checksum, declared_size_bytes=declared_size, declared_media_type=media_type)
    file_object = store_file_object(file_group=group, upload_part=part, data=data, purpose="original", retention_policy=retention, media_type=media_type, limits=limits)
    lock_use_authorization(group.use_authorization_id, file_object, 'family_observation', settings.SJIA_ENVIRONMENT)
    return Response({"file_id": str(file_object.id), "checksum": file_object.checksum, "size_bytes": file_object.size_bytes}, status=201)


@api_view(["POST"])
@handle
@transaction.atomic
def file_group_complete(request, group_id):
    principal, epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    from files.validation import submit_validation
    result = submit_validation(actor=principal, auth_epoch=epoch, audience=audience,
        target=request.path, payload=request.data, headers=request.headers)
    return Response(result, status=202)


@api_view(["GET"])
@handle
@transaction.atomic
def file_group_read(request, group_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    group = FileGroup.objects.filter(id=group_id).first()
    if group is None:
        raise ApiError("GROUP_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    authorize_case(request, principal, group.patient, "read_file", author_id=group.owner_id, whole_resource=True)
    select_upload_authorization(group.patient, UploadGroupInput(authorization_id=group.use_authorization_id))
    files = list(FileObject.objects.filter(file_group_id=group.id, patient=group.patient, purpose='original'))
    parts = {part.file_id: part for part in UploadPart.objects.filter(file_group_id=group.id, status='stored')}
    files.sort(key=lambda file: parts[file.id].ordinal)
    return Response({'group_id': str(group.id), 'patient_id': str(group.patient_id), 'status': group.status,
        'version': str(group.version), 'authorization_id': str(group.use_authorization_id),
        'source_revision_id': str(group.source_revision_id), 'files': [{'file_id': str(file.id),
            'purpose': file.purpose, 'media_type': file.media_type, 'checksum': file.checksum, 'size_bytes': file.size_bytes,
            'ordinal': parts[file.id].ordinal,
            'page_count': FilePage.objects.filter(file_id=file.id, patient=group.patient).count()} for file in files]})


@api_view(["POST"])
@handle
@transaction.atomic
def file_group_revise(request, group_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    group = FileGroup.objects.filter(id=group_id).first()
    if group is None:
        raise ApiError("GROUP_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    authorize_case(request, principal, group.patient, "write_record")
    from identity.models import Patient
    patient = Patient.objects.select_for_update().get(id=group.patient_id)
    if patient.status != 'active':
        raise ApiError('PATIENT_UNAVAILABLE', 409)
    group = FileGroup.objects.select_for_update().get(id=group.id, patient=patient)
    data = request.data or {}
    new_group = create_file_group(
        patient=group.patient, owner=principal, source_revision=group.source_revision,
        use_authorization_id=group.use_authorization_id, draft_id=data.get("draft_id") or group.draft_id,
    )
    new_group.version = group.version + 1
    new_group.save(update_fields=["version"])
    group.status = FileGroup.Status.SUPERSEDED
    group.save(update_fields=["status"])
    CandidateSet.objects.filter(file_group_id=group.id).update(status=CandidateSet.Status.SUPERSEDED)
    pending = Task.objects.filter(task_type=Task.TaskType.OCR, input_refs__file_group_id=str(group.id),
        status__in=[Task.Status.ACCEPTED, Task.Status.QUEUED, Task.Status.RUNNING, Task.Status.AWAITING_CONFIRMATION])
    from django.db.models import F

    pending.update(status=Task.Status.SUPERSEDED, status_version=F('status_version') + 1)
    return Response({"group_id": str(new_group.id), "version": str(new_group.version), "status": new_group.status}, status=201)


@api_view(["POST"])
@handle
def recognition_create(request):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    data = RecognitionRequest.model_validate(request.data)
    group = FileGroup.objects.filter(id=data.file_group_id).first()
    if group is None:
        raise ApiError("GROUP_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    authorize_case(request, principal, group.patient, "write_record")
    authorize_case(request, principal, group.patient, "read_file")
    task = submit_recognition(group=group, actor=principal, version=data.file_group_version)
    return Response({'task_id': str(task.id), 'status': task.status, 'status_version': str(task.status_version)}, status=202)


@api_view(["POST"])
@handle
def voice_structure(request):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    raise ApiError('VOICE_CONFIRMED_INPUT_REQUIRED', 422,
        '请保存本人确认的文字原件，取得用途许可后，通过语音任务提取并核对候选。')


@api_view(["POST"])
@handle
def voice_transcription(request, patient_id):
    principal, epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError('AUTH_REQUIRED', status.HTTP_401_UNAUTHORIZED)
    if audience != 'family':
        raise ApiError('FAMILY_AUDIENCE_REQUIRED', 403)
    patient = get_patient(patient_id)
    authorize_case(request, principal, patient, 'write_record')
    data = request.body
    if not data or len(data) > 5 * 1024 * 1024:
        raise ApiError('AUDIO_SIZE_INVALID', 413, '录音不能为空，且不能超过5MB。')
    media_type, _ = parse_header_parameters(request.content_type)
    if media_type not in ('audio/webm', 'audio/mp4', 'audio/mpeg', 'audio/wav', 'audio/x-wav', 'audio/ogg'):
        raise ApiError('AUDIO_TYPE_INVALID', 415, '请选择浏览器录音或 WebM、MP4、MP3、WAV、Ogg 音频。')
    from recognition.local_transcription import transcribe_local_audio

    authorization_id = request.headers.get('X-Use-Authorization')
    if authorization_id is None:
        raise ApiError('TRANSCRIPTION_AUTHORIZATION_REQUIRED', 422)
    payload = TranscriptionAuthorizationInput.model_validate({'authorization_id': authorization_id})
    result = transcribe_local_audio(audio_bytes=data, audio_media_type=media_type,
        actor=principal, patient=patient, auth_epoch=epoch, authorization_id=payload.authorization_id)
    return Response({**result, 'text': result['transcript']})


@api_view(["POST"])
@handle
def report_ocr(request):
    return image_candidates(request, recognize_report_image)


@api_view(["POST"])
@handle
def therapy_ocr(request):
    return image_candidates(request, recognize_therapy_image)


def image_candidates(request, recognizer):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    payload = ReportRequest.model_validate(request.data)
    file_object = FileObject.objects.filter(id=payload.file_id).first()
    if file_object is None:
        raise ApiError("FILE_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    from identity.models import Patient

    patient = Patient.objects.filter(id=file_object.patient_id).first()
    authorize_case(request, principal, patient, "write_record")
    authorize_case(request, principal, patient, "read_file")
    if file_object.status != FileObject.Status.AVAILABLE:
        raise ApiError('FILE_NOT_AVAILABLE', status.HTTP_409_CONFLICT)
    if not FileGroup.objects.filter(id=file_object.file_group_id, patient=patient, status=FileGroup.Status.AVAILABLE).exists():
        raise ApiError('FILE_GROUP_NOT_AVAILABLE', status.HTTP_409_CONFLICT)
    from recognition.sync_images import synchronous_image_candidates

    _, epoch, _ = current_principal(request)
    kind = 'report' if recognizer is recognize_report_image else 'therapy'
    result = synchronous_image_candidates(file_object=file_object, actor=principal,
        auth_epoch=epoch, kind=kind)
    return Response({"candidates": result})


@api_view(["GET"])
@handle
def file_content(request, file_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    file_object = FileObject.objects.filter(id=file_id).first()
    if file_object is None:
        raise ApiError("FILE_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    if file_object.purpose == FileObject.Purpose.DEIDENTIFIED:
        raise ApiError('PURPOSE_COPY_ENDPOINT_REQUIRED', status.HTTP_403_FORBIDDEN)
    group = FileGroup.objects.filter(id=file_object.file_group_id, patient_id=file_object.patient_id).first()
    if group is None:
        raise ApiError('GROUP_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    # 原件字节范围要求完整原件授权，字段及页面范围授权保持独立边界。
    authorize_case(request, principal, group.patient, "read_file", author_id=group.owner_id, whole_resource=True)
    if file_object.status != FileObject.Status.AVAILABLE:
        raise ApiError('FILE_NOT_AVAILABLE', status.HTTP_409_CONFLICT)
    from django.http import HttpResponse

    from files.service import object_store

    from retention.content_fences import assert_content_available
    assert_content_available('files.fileobject', file_object.id)
    range_header = request.headers.get("Range")
    if range_header is None:
        from files.http_content import verified_file_response
        return verified_file_response(file_object)
    size = file_object.size_bytes
    start, end = parse_range(range_header, size)
    data = object_store.get_range(file_object.object_key, start, end)
    if len(data) != end - start + 1:
        raise ApiError('FILE_OBJECT_SIZE_MISMATCH', status.HTTP_409_CONFLICT,
            '原件读取范围与登记大小不一致，请核查原件。')
    return HttpResponse(data, content_type=file_object.media_type, status=status.HTTP_206_PARTIAL_CONTENT,
        headers={'Accept-Ranges': 'bytes', 'Content-Range': f'bytes {start}-{end}/{size}'})


@api_view(["GET"])
@handle
def candidate_read(request, candidate_set_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    candidate_set = CandidateSet.objects.filter(id=candidate_set_id).first()
    if candidate_set is None:
        raise ApiError("CANDIDATE_SET_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    from identity.models import Patient

    patient = Patient.objects.filter(id=candidate_set.patient_id).first()
    authorize_case(request, principal, patient, "read_file")
    candidates = CandidateField.objects.filter(candidate_set_id=candidate_set.id).order_by("field_path")
    confirmations = {c.candidate_field_id: c for c in CandidateConfirmation.objects.filter(candidate_field_id__in=candidates.values_list("id", flat=True))}
    return Response({"candidate_set_id": str(candidate_set.id), "status": candidate_set.status, "candidates": [{"candidate_field_id": str(c.id), "field_path": c.field_path, "raw_text": c.raw_text, "recognition_status": c.recognition_status, "proposed_field": c.proposed_field, "decision": confirmations[c.id].decision if c.id in confirmations else None} for c in candidates]})


@api_view(["POST"])
@handle
def dictionary_search(request):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    from configurations.schemas import validate_operation_input
    from dictionaries.service import search_dictionary

    data = validate_operation_input('DictionarySearch', request.data)
    return Response(search_dictionary(actor=principal, **data))


@api_view(["POST"])
@handle
def correction_request_create(request, record_id):
    principal, epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience != 'family':
        raise ApiError('FAMILY_AUDIENCE_REQUIRED', 403)
    from verification.correction_requests import submit_correction

    result = submit_correction(operation='correction_request_create', target=request.path, payload=request.data,
        actor=principal, epoch=epoch, audience=audience, environment=settings.SJIA_ENVIRONMENT,
        headers={'intent_id': request.headers.get('X-Write-Intent'), 'request_id': request.headers.get('Idempotency-Key'),
            'generation': request.headers.get('X-Intent-Generation')})
    return Response(result, status=201)


@api_view(["GET"])
@handle
def correction_request_read(request, correction_request_id):
    principal, epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience == 'support':
        from verification.correction_review import read_review
        return Response(read_review(correction_request_id, actor=principal, epoch=epoch,
            audience=audience, environment=settings.SJIA_ENVIRONMENT)['correction'])
    if audience != 'family':
        raise ApiError('FAMILY_AUDIENCE_REQUIRED', 403)
    from verification.corrections import read_correction_request, request_view

    row = read_correction_request(request_id=correction_request_id, actor=principal, epoch=epoch,
        environment=settings.SJIA_ENVIRONMENT)
    return Response(request_view(row))


@api_view(["POST"])
@handle
@transaction.atomic
def disposition_create(request, patient_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    patient = get_patient(patient_id)
    authorize_case(request, principal, patient, "request_disposition")
    payload = PurposeDispositionInput.model_validate(request.data)
    retention = RetentionPolicy.objects.get(id=payload.policy_id, status='active')
    disposition = create_disposition_request(patient=patient, requested_by=principal, policy=retention,
        evidence_ref=payload.evidence_ref, scope={'targets': [item.model_dump() for item in payload.targets]})
    return Response({"disposition_id": str(disposition.id), "status": disposition.status}, status=201)


@api_view(["GET"])
@handle
def disposition_read(request, disposition_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    from retention.models import DispositionRequest

    disposition = DispositionRequest.objects.filter(id=disposition_id).first()
    if disposition is None:
        raise ApiError("DISPOSITION_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    authorize_case(request, principal, disposition.patient, 'request_disposition')
    if disposition.requested_by_id != principal.id:
        raise ApiError('DISPOSITION_NOT_FOUND', status.HTTP_404_NOT_FOUND)
    return Response({"disposition_id": str(disposition.id), "status": disposition.status,
        'policy_id': str(disposition.policy_id), 'policy_version': str(disposition.policy.version),
        'scope_sha256': scope_sha256(disposition.scope),
        'targets': [target.model_dump(mode='json') for target in PurposeDispositionScope.model_validate(disposition.scope).targets],
        'items': [{'item_id': str(item.id), 'target_type': item.target_type, 'target_id': item.target_id,
            'target_version': item.target_version, 'status': item.status, 'reason': item.reason,
            'executed_at': item.executed_at.isoformat() if item.executed_at is not None else None,
            'receipt_sha256': item.receipt_sha256, 'recheck_at': item.recheck_at.isoformat() if item.recheck_at is not None else None}
            for item in DispositionItem.objects.filter(request=disposition).order_by('target_type', 'target_id', 'target_version')]})


@api_view(["GET"])
@handle
def contact_read(request, patient_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    patient = get_patient(patient_id)
    authorize_case(request, principal, patient, "receive_alert")
    from family_content.models import EnvironmentGate
    from family_content.service import release_available
    gate = EnvironmentGate.objects.filter(environment=settings.SJIA_ENVIRONMENT, enabled=True).select_related('active_release__contact_version').first()
    contact = gate.active_release.contact_version if gate and gate.active_release and release_available(gate.active_release) else None
    if contact is None:
        return Response({"contact": None})
    return Response({"contact": {"contact_version_id": str(contact.id), "contact_name": contact.contact_name, "channel": contact.channel, "channel_value": contact.channel_value, "valid_until": contact.valid_until.isoformat()}})


@api_view(["GET"])
@handle
@record_read_snapshot
def alert_read(request, alert_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    event = AlertEvent.objects.filter(id=alert_id).first()
    if event is None:
        raise ApiError("ALERT_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    decision = authorize_case(request, principal, event.patient, 'receive_alert', lock=False)
    from relations.models import Relation

    relation_ids = Relation.objects.filter(patient=event.patient, guardian=principal, status='active').values('id')
    recipient = AlertRecipient.objects.filter(alert_id=event.id, patient_id=event.patient_id, relation_id__in=relation_ids).first()
    from family_content.alert_detail import alert_content
    return Response({"alert_id": str(event.id), "created_at": event.created_at.isoformat(), "read_at": recipient.read_at.isoformat() if recipient and recipient.read_at else None, **alert_content(event, decision.grant.field_codes)})
