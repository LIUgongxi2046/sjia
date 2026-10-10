import uuid

from django.db import models

from core.fields import Sha256HexField


class SourceRegistry(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "pending"
        APPROVED = "approved", "approved"
        DISABLED = "disabled", "disabled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    source_key = models.TextField(unique=True)
    owner = models.TextField()
    location = models.TextField()
    purpose = models.TextField()
    sensitivity = models.TextField()
    update_frequency = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices)

    class Meta:
        db_table = 'sjia"."source_registry'


class SourceRevision(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "pending"
        APPROVED = "approved", "approved"
        REVOKED = "revoked", "revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    source = models.ForeignKey(SourceRegistry, on_delete=models.RESTRICT, db_column="source_id", related_name="revisions")
    version = models.TextField()
    checksum = Sha256HexField()
    license = models.TextField()
    allowed_use = models.JSONField()
    rights_evidence_ref = models.TextField(null=True)
    retention_policy = models.ForeignKey("retention.RetentionPolicy", on_delete=models.SET_NULL, null=True, related_name="+")
    captured_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices)

    class Meta:
        db_table = 'sjia"."source_revision'
        constraints = [
            models.UniqueConstraint(fields=["source_id", "version"], name="source_revision_source_version_uniq"),
        ]


class DataUseAuthorization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    source_revision = models.ForeignKey(SourceRevision, on_delete=models.RESTRICT, db_column="source_revision_id", related_name="+")
    purpose = models.TextField()
    environment = models.TextField()
    evidence_ref = models.TextField()
    deidentification_ref = models.TextField(null=True)
    approved_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, related_name="+")
    approved_at = models.DateTimeField()
    valid_until = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)

    class Meta:
        db_table = 'sjia"."data_use_authorization'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="data_use_authorization_id_patient_uniq"),
        ]


class IngestionRun(models.Model):
    class Status(models.TextChoices):
        VALIDATING = "validating", "validating"
        BLOCKED = "blocked", "blocked"
        IMPORTING = "importing", "importing"
        COMPLETED = "completed", "completed"
        FAILED = "failed", "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    source_revision = models.ForeignKey(SourceRevision, on_delete=models.RESTRICT, db_column="source_revision_id", related_name="+")
    dataset_version = models.TextField()
    checksum = Sha256HexField()
    schema_version = models.TextField()
    operator = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="operator_id", related_name="+")
    environment = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True)
    validation_report_ref = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."ingestion_run'
        constraints = [
            models.UniqueConstraint(fields=["source_revision_id", "dataset_version"], name="ingestion_run_source_dataset_uniq"),
        ]
