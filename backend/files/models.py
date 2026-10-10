# 本模块四张表均为单列 uuid 主键。
# 复合外键（patient_id 与其它列成对出现）无法由 Django 表达，由原生 SQL 集中建立。
import uuid

from django.db import models

from core.fields import Sha256HexField


class FileGroup(models.Model):
    class Status(models.TextChoices):
        INITIATED = "initiated", "initiated"
        UPLOADED = "uploaded", "uploaded"
        VALIDATING = "validating", "validating"
        AVAILABLE = "available", "available"
        FAILED = "failed", "failed"
        SUPERSEDED = "superseded", "superseded"
        DISPOSED = "disposed", "disposed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    owner = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="owner_id", related_name="+")
    draft_id = models.UUIDField(null=True)
    version = models.PositiveBigIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices)
    source_revision = models.ForeignKey("sources.SourceRevision", on_delete=models.RESTRICT, db_column="source_revision_id", related_name="+")
    use_authorization_id = models.UUIDField()
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."file_group'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="file_group_id_patient_uniq"),
        ]


class FileObject(models.Model):
    class Purpose(models.TextChoices):
        ORIGINAL = "original", "original"
        THUMBNAIL = "thumbnail", "thumbnail"
        DEIDENTIFIED = "deidentified", "deidentified"
        OCR_RESPONSE = "ocr_response", "ocr_response"

    class Status(models.TextChoices):
        QUARANTINED = "quarantined", "quarantined"
        AVAILABLE = "available", "available"
        FAILED = "failed", "failed"
        DISPOSED = "disposed", "disposed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    file_group_id = models.UUIDField()
    derived_from_id = models.UUIDField(null=True)
    object_key = models.TextField()
    object_version = models.TextField()
    checksum = Sha256HexField()
    size_bytes = models.PositiveBigIntegerField()
    media_type = models.TextField()
    purpose = models.CharField(max_length=16, choices=Purpose.choices)
    processing_version = models.TextField(null=True)
    status = models.CharField(max_length=16, choices=Status.choices)
    retention_policy = models.ForeignKey("retention.RetentionPolicy", on_delete=models.RESTRICT, db_column="retention_policy_id", related_name="+")
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."file_object'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="file_object_id_patient_uniq"),
            models.UniqueConstraint(fields=["object_key", "object_version"], name="file_object_key_version_uniq"),
        ]


class FilePage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    file_group_id = models.UUIDField()
    file_id = models.UUIDField()
    ordinal = models.IntegerField()
    width = models.IntegerField()
    height = models.IntegerField()
    rotation = models.IntegerField()

    class Meta:
        db_table = 'sjia"."file_page'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="file_page_id_patient_uniq"),
            models.UniqueConstraint(fields=["file_group_id", "ordinal"], name="file_page_group_ordinal_uniq"),
        ]


class UploadPart(models.Model):
    class Status(models.TextChoices):
        DECLARED = "declared", "declared"
        RECEIVING = "receiving", "receiving"
        STORED = "stored", "stored"
        FAILED = "failed", "failed"
        UNCERTAIN = "uncertain", "uncertain"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    file_group_id = models.UUIDField()
    client_upload_id = models.UUIDField()
    ordinal = models.IntegerField()
    declared_checksum = Sha256HexField()
    declared_size_bytes = models.PositiveBigIntegerField()
    declared_media_type = models.CharField(max_length=128)
    status = models.CharField(max_length=16, choices=Status.choices)
    object_key = models.TextField(unique=True)
    object_version = models.TextField(null=True)
    file_id = models.UUIDField(null=True)
    intent = models.ForeignKey("requests.WriteIntent", on_delete=models.RESTRICT, null=True, db_column="intent_id", related_name="+")
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."upload_part'
        constraints = [
            models.UniqueConstraint(fields=["file_group_id", "ordinal"], name="upload_part_group_ordinal_uniq"),
            models.UniqueConstraint(fields=["file_group_id", "client_upload_id"], name="upload_part_group_client_upload_uniq"),
        ]
