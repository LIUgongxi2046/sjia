# 本模块四张表均为单列 uuid 主键。
# 复合外键（patient_id 与其它列成对出现）无法由 Django 表达，由原生 SQL 集中建立。
import uuid
from recognition.voice_models import TranscriptionReceipt, VoiceInput

from django.db import models

from core.fields import FiniteDecimalField


class CandidateSet(models.Model):
    class Status(models.TextChoices):
        PENDING_CONFIRMATION = "pending_confirmation", "pending_confirmation"
        CONFIRMED = "confirmed", "confirmed"
        SUPERSEDED = "superseded", "superseded"
        CANCELLED = "cancelled", "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    file_group_id = models.UUIDField()
    file_group_version = models.BigIntegerField()
    task_id = models.UUIDField()
    provider = models.TextField()
    model_version = models.TextField()
    provider_request_id = models.TextField()
    schema_version = models.TextField()
    raw_response_file_id = models.UUIDField(null=True)
    status = models.CharField(max_length=32, choices=Status.choices)
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."candidate_set'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="candidate_set_id_patient_uniq"),
        ]


class SourceAnchor(models.Model):
    class AnchorKind(models.TextChoices):
        PAGE = "page", "page"
        BBOX = "bbox", "bbox"

    class CoordinateSystem(models.TextChoices):
        DECODED_PIXEL = "decoded_pixel", "decoded_pixel"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    page_id = models.UUIDField()
    anchor_kind = models.CharField(max_length=16, choices=AnchorKind.choices)
    coordinate_system = models.CharField(max_length=16, choices=CoordinateSystem.choices)
    x = FiniteDecimalField(null=True)
    y = FiniteDecimalField(null=True)
    width = FiniteDecimalField(null=True)
    height = FiniteDecimalField(null=True)
    row_label = models.TextField(null=True)
    column_label = models.TextField(null=True)
    original_text = models.TextField()

    class Meta:
        db_table = 'sjia"."source_anchor'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="source_anchor_id_patient_uniq"),
        ]


class CandidateField(models.Model):
    class RecognitionStatus(models.TextChoices):
        RECOGNIZED = "recognized", "recognized"
        UNCERTAIN = "uncertain", "uncertain"
        MISSING = "missing", "missing"
        UNLOCATED = "unlocated", "unlocated"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    candidate_set_id = models.UUIDField()
    field_path = models.TextField()
    raw_text = models.TextField(null=True)
    field_config_version = models.ForeignKey("configurations.ConfigVersion", on_delete=models.RESTRICT, db_column="field_config_version", related_name="+")
    proposed_field = models.JSONField(null=True)
    anchor_id = models.UUIDField(null=True)
    recognition_status = models.CharField(max_length=16, choices=RecognitionStatus.choices)
    confidence = FiniteDecimalField(null=True)

    class Meta:
        db_table = 'sjia"."candidate_field'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="candidate_field_id_patient_uniq"),
            models.UniqueConstraint(fields=["candidate_set_id", "field_path"], name="candidate_field_set_path_uniq"),
        ]


class CandidateConfirmation(models.Model):
    class Decision(models.TextChoices):
        ACCEPTED = "accepted", "accepted"
        MODIFIED = "modified", "modified"
        UNKNOWN = "unknown", "unknown"
        EXCLUDED = "excluded", "excluded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    candidate_field_id = models.UUIDField()
    confirmed_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="confirmed_by", related_name="+")
    confirmed_text = models.TextField(null=True)
    decision = models.CharField(max_length=16, choices=Decision.choices)
    confirmed_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."candidate_confirmation'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="candidate_confirmation_id_patient_uniq"),
        ]
