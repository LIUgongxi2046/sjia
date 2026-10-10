# 复合主键表 verification_field 与 field_verification_state 增加代理主键 id，
# 原复合主键分别改为 UniqueConstraint verification_field_pk_uniq 与 field_verification_state_pk_uniq。
# 复合外键（patient_id 与其它列成对出现）无法由 Django 表达，由原生 SQL 集中建立。
import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models


class Verification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    record_version_id = models.UUIDField()
    verifier = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="verifier_id", related_name="+")
    policy = models.ForeignKey("configurations.ConfigVersion", on_delete=models.RESTRICT, db_column="policy_id", related_name="+")
    purpose = models.TextField()
    evidence_ref = models.TextField()
    evidence_source = models.ForeignKey("sources.SourceRevision", on_delete=models.RESTRICT, db_column="evidence_source_id", related_name="+")
    verified_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)
    revoked_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, null=True, db_column="revoked_by", related_name="+")
    revocation_reason = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."verification'
        constraints = [
            models.UniqueConstraint(fields=["id", "record_version_id", "patient_id"], name="verification_id_record_version_patient_uniq"),
        ]


class VerificationField(models.Model):
    class Result(models.TextChoices):
        CONSISTENT = "consistent", "consistent"
        NEEDS_CORRECTION = "needs_correction", "needs_correction"
        UNCERTAIN = "uncertain", "uncertain"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    verification_id = models.UUIDField()
    record_version_id = models.UUIDField()
    patient_id = models.UUIDField()
    field_id = models.UUIDField()
    result = models.CharField(max_length=16, choices=Result.choices)
    reason = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."verification_field'
        constraints = [
            models.UniqueConstraint(fields=["verification_id", "field_id"], name="verification_field_pk_uniq"),
        ]
        indexes = [
            models.Index(fields=["field_id", "verification_id"], name="verification_field_reverse"),
        ]


class VerificationRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "pending"
        IN_REVIEW = "in_review", "in_review"
        COMPLETED = "completed", "completed"
        CANCELLED = "cancelled", "cancelled"
        SUPERSEDED = "superseded", "superseded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    record_version_id = models.UUIDField()
    requested_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="requested_by", related_name="+")
    assigned_to = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, null=True, db_column="assigned_to", related_name="+")
    purpose = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices)
    created_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = 'sjia"."verification_request'
        constraints = [
            models.UniqueConstraint(fields=["id", "record_version_id", "patient_id"], name="verification_request_id_record_version_patient_uniq"),
        ]


class FieldVerificationState(models.Model):
    class Status(models.TextChoices):
        UNVERIFIED = "unverified", "unverified"
        PENDING = "pending", "pending"
        VERIFIED = "verified", "verified"
        NEEDS_CORRECTION = "needs_correction", "needs_correction"
        REVOKED = "revoked", "revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    field_id = models.UUIDField()
    record_version_id = models.UUIDField()
    patient_id = models.UUIDField()
    purpose = models.TextField()
    version = models.PositiveBigIntegerField()
    request_id = models.UUIDField(null=True)
    current_verification_id = models.UUIDField(null=True)
    status = models.CharField(max_length=16, choices=Status.choices)

    class Meta:
        db_table = 'sjia"."field_verification_state'
        constraints = [
            models.UniqueConstraint(fields=["field_id", "purpose"], name="field_verification_state_pk_uniq"),
        ]


class CorrectionRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "pending"
        IN_REVIEW = "in_review", "in_review"
        RESOLVED = "resolved", "resolved"
        REJECTED = "rejected", "rejected"
        WITHDRAWN = "withdrawn", "withdrawn"
        SUPERSEDED = "superseded", "superseded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    record_version_id = models.UUIDField()
    requested_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="requested_by", related_name="+")
    field_ids = ArrayField(models.UUIDField())
    reason = models.TextField()
    proposed_text = models.TextField(null=True)
    status = models.CharField(max_length=16, choices=Status.choices)
    version = models.PositiveBigIntegerField()
    created_at = models.DateTimeField()
    resolved_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, null=True, db_column="resolved_by", related_name="+")
    resolved_at = models.DateTimeField(null=True)
    resolution_record_version_id = models.UUIDField(null=True)
    resolution_reason = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."correction_request'
        indexes = [
            models.Index(fields=["patient_id", "status", "created_at", "id"], name="correction_request_scope"),
        ]
