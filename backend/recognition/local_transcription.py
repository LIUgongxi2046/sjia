import hashlib
import json
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from api.base import ApiError
from authorization.policy import evaluate
from identity.models import Patient, Principal
from recognition.models import TranscriptionReceipt
from recognition.speech import transcribe_audio
from recognition.voice_input_service import _lock_environment_gate
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision


class LocalTranscriptionRejected(ApiError):
    def __init__(self, reason_code, http_status=409):
        super().__init__(reason_code, http_status)
        self.reason_code = reason_code


def _model_identity():
    directory = Path(settings.SPEECH_MODEL_DIR)
    if not directory.is_dir():
        raise LocalTranscriptionRejected('SPEECH_MODEL_NOT_CONFIGURED', 503)
    paths = sorted(directory.rglob('*'))
    if not paths or any(path.is_symlink() for path in paths):
        raise LocalTranscriptionRejected('SPEECH_MODEL_ASSETS_INVALID', 503)
    manifest = []
    for path in paths:
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        size = 0
        with path.open('rb') as model_file:
            while chunk := model_file.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        manifest.append({'path': path.relative_to(directory).as_posix(),
            'size_bytes': size, 'sha256': digest.hexdigest()})
    if not manifest:
        raise LocalTranscriptionRejected('SPEECH_MODEL_ASSETS_INVALID', 503)
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')
    artifact_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    implementation = (f"faster-whisper=={version('faster-whisper')};language=zh;beam_size=5;"
        'condition_on_previous_text=false;vad_filter=true;sample_rate=16000;max_seconds=61')
    return {'model_name': f'{directory.name};asset_sha256={artifact_sha256}',
        'implementation_version': implementation, 'artifact_sha256': artifact_sha256}


def _authorize(actor_id, patient_id, auth_epoch, authorization_id):
    with transaction.atomic():
        _lock_environment_gate()
        actor = Principal.objects.select_for_update().get(id=actor_id)
        patient = Patient.objects.select_for_update().get(id=patient_id)
        evaluate(principal=actor, patient=patient, action='write_record', purpose='family_observation',
            environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=auth_epoch, require_relation=True)
        authorization = DataUseAuthorization.objects.select_for_update().get(
            id=authorization_id, patient=patient)
        revision = SourceRevision.objects.select_for_update().get(id=authorization.source_revision_id)
        source = SourceRegistry.objects.select_for_update().get(id=revision.source_id)
        approver = Principal.objects.select_for_update().get(id=authorization.approved_by_id)
        if (authorization.purpose != 'family_observation'
                or authorization.environment != settings.SJIA_ENVIRONMENT
                or not authorization.evidence_ref.strip()
                or authorization.approved_at > timezone.now()
                or authorization.revoked_at is not None
                or authorization.valid_until is not None and authorization.valid_until <= timezone.now()
                or revision.status != 'approved' or source.status != 'approved'
                or not revision.rights_evidence_ref or not revision.rights_evidence_ref.strip()
                or approver.account_status != Principal.AccountStatus.ACTIVE
                or not isinstance(revision.allowed_use, list)
                or 'family_observation' not in revision.allowed_use):
            raise LocalTranscriptionRejected('TRANSCRIPTION_AUTHORIZATION_UNAVAILABLE', 403)
        return actor, patient, authorization


def _receipt_output(receipt):
    return {'transcription_evidence_id': str(receipt.id),
        'patient_id': str(receipt.patient_id), 'actor_id': str(receipt.actor_id),
        'auth_epoch': str(receipt.auth_epoch), 'authorization_id': str(receipt.use_authorization_id),
        'status': receipt.status, 'audio_sha256': receipt.audio_sha256,
        'audio_media_type': receipt.audio_media_type, 'audio_size_bytes': receipt.audio_size_bytes,
        'duration_seconds': str(receipt.duration_seconds),
        'inference_duration_ms': receipt.inference_duration_ms, 'model_name': receipt.model_name,
        'implementation_version': receipt.implementation_version,
        'transcript': receipt.transcript, 'transcript_sha256': receipt.transcript_sha256,
        'confirmed_text': receipt.confirmed_text,
        'confirmed_text_sha256': receipt.confirmed_text_sha256,
        'confirmed_original_file_id': str(receipt.confirmed_original_file_id)
            if receipt.confirmed_original_file_id else None,
        'status_version': str(receipt.status_version), 'created_at': receipt.created_at.isoformat(),
        'confirmed_at': receipt.confirmed_at.isoformat() if receipt.confirmed_at else None}


def transcribe_local_audio(*, audio_bytes, audio_media_type, actor, patient, auth_epoch,
        authorization_id):
    if not isinstance(audio_bytes, bytes) or not audio_bytes or len(audio_bytes) > 5 * 1024 * 1024:
        raise LocalTranscriptionRejected('AUDIO_SIZE_INVALID', 413)
    if audio_media_type not in ('audio/webm', 'audio/mp4', 'audio/mpeg', 'audio/wav',
            'audio/x-wav', 'audio/ogg'):
        raise LocalTranscriptionRejected('AUDIO_TYPE_INVALID', 415)
    actor, patient, authorization = _authorize(actor.id, patient.id, auth_epoch, authorization_id)
    retention = RetentionPolicy.objects.filter(status='active', data_class='observation',
        approved_at__isnull=False, approved_by__isnull=False).order_by('-approved_at', '-version').first()
    if retention is None:
        raise LocalTranscriptionRejected('TRANSCRIPTION_RETENTION_POLICY_REQUIRED', 503)
    audio_sha256 = hashlib.sha256(audio_bytes).hexdigest()
    model_identity = _model_identity()
    inference_started = perf_counter()
    result = transcribe_audio(audio_bytes)
    inference_duration_ms = round((perf_counter() - inference_started) * 1000)
    if _model_identity() != model_identity:
        raise LocalTranscriptionRejected('SPEECH_MODEL_CHANGED_DURING_TRANSCRIPTION', 503)
    transcript = result['text']
    transcript_sha256 = hashlib.sha256(transcript.encode('utf-8')).hexdigest()
    with transaction.atomic():
        actor, patient, current_authorization = _authorize(actor.id, patient.id,
            auth_epoch, authorization.id)
        retention = RetentionPolicy.objects.select_for_update().get(id=retention.id,
            status='active', data_class='observation', approved_at__isnull=False,
            approved_by__isnull=False)
        if current_authorization.id != authorization.id:
            raise LocalTranscriptionRejected('TRANSCRIPTION_AUTHORIZATION_CHANGED', 403)
        receipt = TranscriptionReceipt.objects.create(patient=patient, actor=actor,
            auth_epoch=auth_epoch, use_authorization=authorization, retention_policy=retention,
            audio_sha256=audio_sha256, audio_media_type=audio_media_type,
            audio_size_bytes=len(audio_bytes), duration_seconds=result['duration_seconds'],
            inference_duration_ms=inference_duration_ms,
            model_name=model_identity['model_name'],
            implementation_version=model_identity['implementation_version'], transcript=transcript,
            transcript_sha256=transcript_sha256, confirmed_text=None,
            confirmed_text_sha256=None, confirmed_original_file=None, status='completed',
            status_version=1, created_at=timezone.now(), confirmed_at=None)
        saved = TranscriptionReceipt.objects.select_related('use_authorization').get(
            id=receipt.id, patient=patient, actor=actor)
        if (saved.audio_sha256 != audio_sha256 or saved.audio_media_type != audio_media_type
                or saved.audio_size_bytes != len(audio_bytes)
                or saved.inference_duration_ms != inference_duration_ms
                or saved.duration_seconds != Decimal(str(result['duration_seconds']))
                or saved.transcript != transcript or saved.transcript_sha256 != transcript_sha256
                or saved.model_name != model_identity['model_name']
                or saved.implementation_version != model_identity['implementation_version']
                or saved.use_authorization_id != authorization.id
                or saved.retention_policy_id != retention.id):
            raise LocalTranscriptionRejected('TRANSCRIPTION_RECEIPT_READBACK_MISMATCH', 500)
    return _receipt_output(saved)


@transaction.atomic
def verify_local_transcription_confirmation(*, evidence_id, transcript_file, actor,
        patient, confirmed_text, auth_epoch):
    receipt = TranscriptionReceipt.objects.select_for_update().get(id=evidence_id,
        patient_id=patient.id, actor_id=actor.id)
    actor, patient, _ = _authorize(actor.id, patient.id, auth_epoch, receipt.use_authorization_id)
    if receipt.auth_epoch != auth_epoch or receipt.status not in ('completed', 'confirmed'):
        raise LocalTranscriptionRejected('TRANSCRIPTION_CONFIRMATION_NOT_AVAILABLE', 409)
    from files.purpose import checked_bytes, lock_original

    original = lock_original(transcript_file.id)
    if original.patient_id != patient.id or original.media_type != 'text/plain':
        raise LocalTranscriptionRejected('CONFIRMED_TEXT_FILE_INVALID', 422)
    if not isinstance(confirmed_text, str) or not confirmed_text.strip():
        raise LocalTranscriptionRejected('CONFIRMED_TEXT_REQUIRED', 422)
    text_bytes = checked_bytes(original)
    confirmed_text_sha256 = hashlib.sha256(confirmed_text.encode('utf-8')).hexdigest()
    if (text_bytes.decode('utf-8', errors='strict') != confirmed_text
            or hashlib.sha256(text_bytes).hexdigest() != original.checksum):
        raise LocalTranscriptionRejected('CONFIRMED_TEXT_FILE_MISMATCH', 422)
    if receipt.status == 'confirmed':
        if (receipt.confirmed_text != confirmed_text
                or receipt.confirmed_text_sha256 != confirmed_text_sha256
                or receipt.confirmed_original_file_id != original.id):
            raise LocalTranscriptionRejected('TRANSCRIPTION_CONFIRMATION_CONFLICT', 409)
    else:
        receipt.confirmed_text = confirmed_text
        receipt.confirmed_text_sha256 = confirmed_text_sha256
        receipt.confirmed_original_file = original
        receipt.status = 'confirmed'
        receipt.status_version += 1
        receipt.confirmed_at = timezone.now()
        receipt.save(update_fields=['confirmed_text', 'confirmed_text_sha256',
            'confirmed_original_file', 'status', 'status_version', 'confirmed_at'])
    saved = TranscriptionReceipt.objects.select_related('confirmed_original_file').get(
        id=receipt.id, patient=patient, actor=actor)
    if (saved.transcript_sha256 != hashlib.sha256(saved.transcript.encode('utf-8')).hexdigest()
            or saved.confirmed_text != confirmed_text
            or saved.confirmed_text_sha256 != confirmed_text_sha256
            or saved.confirmed_original_file_id != original.id
            or saved.auth_epoch != auth_epoch):
        raise LocalTranscriptionRejected('TRANSCRIPTION_CONFIRMATION_READBACK_MISMATCH', 500)
    return {'transcription_evidence_id': str(saved.id), 'patient_id': str(patient.id),
        'actor_id': str(actor.id), 'auth_epoch': str(auth_epoch),
        'authorization_id': str(saved.use_authorization_id),
        'audio_sha256': saved.audio_sha256, 'audio_media_type': saved.audio_media_type,
        'audio_size_bytes': saved.audio_size_bytes, 'duration_seconds': str(saved.duration_seconds),
        'inference_duration_ms': saved.inference_duration_ms,
        'model_name': saved.model_name, 'implementation_version': saved.implementation_version,
        'transcript': saved.transcript, 'transcript_sha256': saved.transcript_sha256,
        'confirmed_text': saved.confirmed_text,
        'confirmed_text_sha256': saved.confirmed_text_sha256,
        'confirmed_original_file_id': str(saved.confirmed_original_file_id),
        'confirmed_original_file_sha256': original.checksum,
        'status': saved.status, 'status_version': str(saved.status_version),
        'confirmed_at': saved.confirmed_at.isoformat()}
