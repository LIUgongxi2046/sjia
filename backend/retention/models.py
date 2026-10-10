import uuid

from django.db import models

from core.fields import Sha256HexField


class RetentionPolicy(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "draft"
        APPROVED = "approved", "approved"
        ACTIVE = "active", "active"
        DISABLED = "disabled", "disabled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    policy_key = models.TextField()
    version = models.PositiveBigIntegerField()
    data_class = models.TextField()
    rules = models.JSONField()
    schema_version = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices)
    approved_by = models.ForeignKey("identity.Principal", on_delete=models.SET_NULL, null=True, related_name="+")
    approved_at = models.DateTimeField(null=True)
    evidence_ref = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."retention_policy'
        constraints = [
            models.UniqueConstraint(fields=["policy_key", "version"], name="retention_policy_key_version_uniq"),
        ]


class SimulatedProviderCopy(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    purpose_copy = models.ForeignKey("archives.PurposeCopy", on_delete=models.RESTRICT,
        db_column="purpose_copy_id", related_name="+")
    purpose_copy_version = models.BigIntegerField()
    provider_id = models.TextField()
    object_id = models.UUIDField()
    object_version = models.TextField()
    checksum = Sha256HexField()
    size_bytes = models.BigIntegerField()
    environment = models.TextField()
    status = models.TextField()
    deletion_request_id = models.UUIDField(null=True)
    receipt_ref = models.TextField(null=True)
    receipt_sha256 = Sha256HexField(null=True)
    created_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT,
        db_column="created_by", related_name="+")
    created_at = models.DateTimeField()
    deleted_at = models.DateTimeField(null=True)
    processing_evidence = models.JSONField()

    class Meta:
        db_table = 'sjia"."simulated_provider_copy'
        constraints = [
            models.UniqueConstraint(fields=["provider_id", "object_id", "object_version"],
                name="sim_provider_object_version_uniq"),
            models.UniqueConstraint(fields=["purpose_copy", "purpose_copy_version", "provider_id"],
                name="sim_provider_purpose_copy_uniq"),
        ]


class DispositionRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "pending"
        APPROVED = "approved", "approved"
        RUNNING = "running", "running"
        BLOCKED = "blocked", "blocked"
        COMPLETED = "completed", "completed"
        REJECTED = "rejected", "rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, null=True, db_column="patient_id", related_name="+")
    requested_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="requested_by", related_name="+")
    policy = models.ForeignKey(RetentionPolicy, on_delete=models.RESTRICT, db_column="policy_id", related_name="+")
    evidence_ref = models.TextField()
    scope = models.JSONField()
    status = models.CharField(max_length=16, choices=Status.choices)
    approved_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, null=True, db_column="approved_by", related_name="+")
    approved_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = 'sjia"."disposition_request'


class DispositionItem(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "pending"
        BLOCKED = "blocked", "blocked"
        RETAINED = "retained", "retained"
        DELETED = "deleted", "deleted"
        FAILED = "failed", "failed"
        AWAITING_PROVIDER = "awaiting_provider", "awaiting_provider"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    request = models.ForeignKey(DispositionRequest, on_delete=models.RESTRICT, db_column="request_id", related_name="+")
    target_type = models.TextField()
    target_id = models.TextField()
    target_version = models.TextField()
    status = models.CharField(max_length=32, choices=Status.choices)
    reason = models.TextField(null=True)
    retention_evidence_ref = models.TextField(null=True)
    recheck_at = models.DateTimeField(null=True)
    executed_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, null=True, db_column="executed_by", related_name="+")
    executed_at = models.DateTimeField(null=True)
    receipt_ref = models.TextField(null=True)
    receipt_sha256 = Sha256HexField(null=True)

    class Meta:
        db_table = 'sjia"."disposition_item'
        constraints = [
            models.UniqueConstraint(fields=["request_id", "target_type", "target_id", "target_version"], name="disposition_item_target_version_uniq"),
        ]
