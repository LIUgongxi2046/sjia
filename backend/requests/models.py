import uuid

from django.db import models
from retention.erasure_models import ErasureModel

from core.fields import Sha256HexField


class WriteRequest(ErasureModel, models.Model):
    class Status(models.TextChoices):
        PROCESSING = "processing", "processing"
        COMMITTED = "committed", "committed"
        FAILED = "failed", "failed"
        UNCERTAIN = "uncertain", "uncertain"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    actor = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="actor_id", related_name="+")
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, null=True, db_column="patient_id", related_name="+")
    action = models.TextField()
    target_key = models.TextField()
    request_id = models.UUIDField()
    payload_sha256 = Sha256HexField()
    normalization_version = models.TextField()
    expected_version = models.BigIntegerField(null=True)
    status = models.CharField(max_length=16, choices=Status.choices)
    resource_type = models.TextField(null=True)
    resource_id = models.UUIDField(null=True)
    error_code = models.TextField(null=True)
    created_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(null=True)

    class Meta:
        db_table = 'sjia"."write_request'
        constraints = [
            models.UniqueConstraint(fields=["actor_id", "action", "target_key", "request_id"], name="write_request_actor_action_target_request_uniq"),
        ]


class WriteIntent(ErasureModel, models.Model):
    class Status(models.TextChoices):
        PREPARED = "prepared", "prepared"
        COMMITTED = "committed", "committed"
        FAILED = "failed", "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    actor = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="actor_id", related_name="+")
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, null=True, db_column="patient_id", related_name="+")
    operation_id = models.CharField(max_length=80)
    target_key = models.CharField(max_length=256)
    request_id = models.UUIDField()
    payload_sha256 = Sha256HexField()
    normalization_version = models.CharField(max_length=32)
    generation = models.PositiveBigIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices)
    prepared_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True)
    write_request = models.ForeignKey(WriteRequest, on_delete=models.RESTRICT, null=True, db_column="write_request_id", related_name="+")
    error_code = models.CharField(max_length=64, null=True)
    authorization_context = models.JSONField(null=True)
    recovery_payload = models.JSONField(null=True)
    retention_policy = models.ForeignKey("retention.RetentionPolicy", on_delete=models.RESTRICT, db_column="retention_policy_id", related_name="+")

    class Meta:
        db_table = 'sjia"."write_intent'
        constraints = [
            models.UniqueConstraint(fields=["actor_id", "operation_id", "target_key", "request_id"], name="write_intent_actor_operation_target_request_uniq"),
        ]
        indexes = [
            models.Index(fields=["expires_at", "id"], condition=models.Q(status="prepared"), name="write_intent_expiry"),
            models.Index(fields=["actor_id", "patient_id", "-prepared_at", "id"], name="write_intent_owner"),
        ]
