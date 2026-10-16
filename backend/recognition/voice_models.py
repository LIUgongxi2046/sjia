import uuid

from django.db import models
from retention.erasure_models import ErasureModel

from core.fields import FiniteDecimalField, Sha256HexField


class VoiceInput(ErasureModel, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey('identity.Patient', on_delete=models.RESTRICT, related_name='+')
    actor = models.ForeignKey('identity.Principal', on_delete=models.RESTRICT, related_name='+')
    request_id = models.UUIDField()
    transcript = models.TextField(null=True)
    transcript_sha256 = Sha256HexField()
    transcription_evidence = models.JSONField(null=True)
    input_mode = models.CharField(max_length=32)
    original_file = models.ForeignKey('files.FileObject', on_delete=models.RESTRICT, related_name='+')
    purpose_copy = models.ForeignKey('archives.PurposeCopy', on_delete=models.RESTRICT, related_name='+', null=True)
    use_authorization = models.ForeignKey('sources.DataUseAuthorization', on_delete=models.RESTRICT, related_name='+')
    task = models.ForeignKey('tasks.Task', on_delete=models.RESTRICT, related_name='+', null=True)
    provider_config = models.ForeignKey('configurations.ConfigVersion', on_delete=models.RESTRICT, related_name='+', null=True)
    field_config = models.ForeignKey('configurations.ConfigVersion', on_delete=models.RESTRICT, related_name='+')
    status = models.CharField(max_length=32)
    status_version = models.PositiveBigIntegerField(default=1)
    candidates = models.JSONField(null=True)
    raw_response_file = models.ForeignKey('files.FileObject', on_delete=models.RESTRICT, related_name='+', null=True)
    decisions = models.JSONField(null=True)
    saved_record_version = models.ForeignKey('records.RecordVersion', on_delete=models.RESTRICT, related_name='+', null=True)
    confirmed_at = models.DateTimeField()
    created_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True)
    cancelled_at = models.DateTimeField(null=True)

    class Meta:
        app_label = 'recognition'
        db_table = 'sjia"."voice_input'
        constraints = [
            models.UniqueConstraint(fields=['actor', 'request_id'], name='voice_input_actor_request_uniq'),
            models.UniqueConstraint(fields=['id', 'patient'], name='voice_input_id_patient_uniq'),
            models.CheckConstraint(condition=models.Q(input_mode__in=['LOCAL_AUDIO_CONFIRMED_TEXT',
                'SIMULATION_TEXT_ONLY']), name='voice_input_mode_check'),
            models.CheckConstraint(condition=models.Q(status__in=['awaiting_review', 'ready', 'running',
                'awaiting_confirmation', 'saved', 'failed', 'uncertain', 'cancelled', 'revoked']), name='voice_input_status_check'),
        ]


class TranscriptionReceipt(ErasureModel, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey('identity.Patient', on_delete=models.RESTRICT, related_name='+')
    actor = models.ForeignKey('identity.Principal', on_delete=models.RESTRICT, related_name='+')
    auth_epoch = models.PositiveBigIntegerField()
    use_authorization = models.ForeignKey('sources.DataUseAuthorization', on_delete=models.RESTRICT, related_name='+')
    retention_policy = models.ForeignKey('retention.RetentionPolicy', on_delete=models.RESTRICT, related_name='+')
    audio_sha256 = Sha256HexField()
    audio_media_type = models.CharField(max_length=128)
    audio_size_bytes = models.PositiveBigIntegerField()
    duration_seconds = FiniteDecimalField()
    inference_duration_ms = models.PositiveBigIntegerField()
    model_name = models.CharField(max_length=256)
    implementation_version = models.CharField(max_length=256)
    transcript = models.TextField(null=True)
    transcript_sha256 = Sha256HexField()
    confirmed_text = models.TextField(null=True)
    confirmed_text_sha256 = Sha256HexField(null=True)
    confirmed_original_file = models.ForeignKey('files.FileObject', on_delete=models.RESTRICT, related_name='+', null=True)
    status = models.CharField(max_length=32)
    status_version = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True)

    class Meta:
        app_label = 'recognition'
        db_table = 'sjia"."transcription_receipt'
        constraints = [
            models.UniqueConstraint(fields=['id', 'patient'], name='transcription_receipt_patient_uniq'),
            models.CheckConstraint(condition=models.Q(status__in=['completed', 'confirmed', 'revoked']),
                name='transcription_receipt_status_check'),
        ]
