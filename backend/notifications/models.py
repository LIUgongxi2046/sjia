import uuid

from django.db import models

from core.fields import Sha256HexField


class NotificationAttempt(models.Model):
    class Status(models.TextChoices):
        AUTHORIZED = "authorized", "authorized"
        STARTED = "started", "started"
        ACCEPTED = "accepted", "accepted"
        FAILED = "failed", "failed"
        UNCERTAIN = "uncertain", "uncertain"
        CANCELLED = "cancelled", "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    recipient_id = models.UUIDField()
    attempt_number = models.IntegerField()
    send_permit_id = models.UUIDField(unique=True)
    provider_idempotency_key = models.TextField()
    authorized_at = models.DateTimeField()
    permit_expires_at = models.DateTimeField()
    principal_auth_epoch = models.BigIntegerField(default=0)
    relation_auth_epoch = models.BigIntegerField(default=0)
    publication_epoch = models.BigIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices)
    provider_request_id = models.TextField(null=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        db_table = 'sjia"."notification_attempt'
        constraints = [
            models.UniqueConstraint(fields=["recipient_id", "attempt_number"], name="notification_attempt_recipient_id_attempt_number_uniq"),
        ]


class NotificationReceipt(models.Model):
    class Status(models.TextChoices):
        ACCEPTED = "accepted", "accepted"
        DELIVERED = "delivered", "delivered"
        FAILED = "failed", "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    attempt = models.ForeignKey(NotificationAttempt, on_delete=models.RESTRICT, db_column="attempt_id", related_name="+")
    provider = models.TextField()
    provider_event_id = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices)
    occurred_at = models.DateTimeField()
    received_at = models.DateTimeField()
    evidence_sha256 = Sha256HexField()

    class Meta:
        db_table = 'sjia"."notification_receipt'
        constraints = [
            models.UniqueConstraint(fields=["provider", "provider_event_id"], name="notification_receipt_provider_provider_event_id_uniq"),
        ]
