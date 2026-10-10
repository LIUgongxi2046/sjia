# consumer_receipt 的 SQL 主键为 (consumer, event_id)。Django 需要单列主键，
# 因此增加代理主键 id，原复合主键改为唯一约束 consumer_receipt_pk_uniq。
import uuid

from django.db import models

from core.fields import Sha256HexField


class Task(models.Model):
    class TaskType(models.TextChoices):
        FILE_VALIDATION = "file_validation", "file_validation"
        PURPOSE_COPY = 'purpose_copy', 'purpose_copy'
        OCR = "ocr", "ocr"
        ASSESSMENT = "assessment", "assessment"
        ALERT_EVALUATION = "alert_evaluation", "alert_evaluation"
        SUMMARY = "summary", "summary"
        NOTIFICATION = "notification", "notification"
        DISPOSITION = "disposition", "disposition"
        RECONCILE = "reconcile", "reconcile"

    class Status(models.TextChoices):
        ACCEPTED = "accepted", "accepted"
        BLOCKED = "blocked", "blocked"
        QUEUED = "queued", "queued"
        RUNNING = "running", "running"
        AWAITING_CONFIRMATION = "awaiting_confirmation", "awaiting_confirmation"
        COMPLETED = "completed", "completed"
        FAILED = "failed", "failed"
        UNCERTAIN = "uncertain", "uncertain"
        AWAITING_RESOLUTION = "awaiting_resolution", "awaiting_resolution"
        CANCEL_REQUESTED = "cancel_requested", "cancel_requested"
        CANCELLED = "cancelled", "cancelled"
        SUPERSEDED = "superseded", "superseded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, null=True, db_column="patient_id", related_name="+")
    actor = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="actor_id", related_name="+")
    request_row = models.ForeignKey("requests.WriteRequest", on_delete=models.RESTRICT, null=True, db_column="request_row_id", related_name="+")
    task_type = models.CharField(max_length=16, choices=TaskType.choices)
    schema_version = models.TextField()
    input_refs = models.JSONField()
    input_sha256 = Sha256HexField()
    status = models.CharField(max_length=32, choices=Status.choices)
    status_version = models.PositiveBigIntegerField()
    lease_generation = models.BigIntegerField(default=0)
    lease_owner = models.TextField(null=True)
    lease_until = models.DateTimeField(null=True)
    created_at = models.DateTimeField()
    deadline_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."task'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="task_id_patient_uniq"),
        ]
        indexes = [
            models.Index(
                fields=["status", "deadline_at", "id"],
                condition=~models.Q(status__in=["completed", "failed", "cancelled", "superseded"]),
                name="task_pending",
            ),
        ]


class TaskAttempt(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "running"
        COMPLETED = "completed", "completed"
        FAILED = "failed", "failed"
        UNCERTAIN = "uncertain", "uncertain"
        SUPERSEDED = "superseded", "superseded"
        CANCELLED = "cancelled", "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    task = models.ForeignKey(Task, on_delete=models.RESTRICT, db_column="task_id", related_name="+")
    attempt_number = models.PositiveIntegerField()
    lease_generation = models.BigIntegerField()
    provider_request_id = models.TextField(null=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True)
    error_code = models.TextField(null=True)
    status = models.CharField(max_length=16, choices=Status.choices)
    usage_evidence = models.JSONField(null=True)

    class Meta:
        db_table = 'sjia"."task_attempt'
        constraints = [
            models.UniqueConstraint(fields=["task_id", "attempt_number"], name="task_attempt_task_id_attempt_number_uniq"),
        ]


class OutboxEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    event_type = models.TextField()
    schema_version = models.TextField()
    aggregate_id = models.UUIDField()
    aggregate_version = models.BigIntegerField()
    occurred_at = models.DateTimeField()
    causation_id = models.UUIDField(null=True)
    correlation_id = models.UUIDField()
    task = models.ForeignKey(Task, on_delete=models.RESTRICT, null=True, db_column="task_id", related_name="+")
    resource_refs = models.JSONField()
    available_at = models.DateTimeField()
    lease_generation = models.BigIntegerField(default=0)
    lease_owner = models.TextField(null=True)
    lease_until = models.DateTimeField(null=True)
    published_at = models.DateTimeField(null=True)

    class Meta:
        db_table = 'sjia"."outbox_event'
        indexes = [
            models.Index(fields=["available_at", "id"], condition=models.Q(published_at__isnull=True), name="outbox_dispatch"),
        ]


class ConsumerReceipt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    consumer = models.TextField()
    event = models.ForeignKey(OutboxEvent, on_delete=models.RESTRICT, db_column="event_id", related_name="+")
    processed_at = models.DateTimeField()
    effect_resource_id = models.UUIDField()

    class Meta:
        db_table = 'sjia"."consumer_receipt'
        constraints = [
            models.UniqueConstraint(fields=["consumer", "event_id"], name="consumer_receipt_pk_uniq"),
        ]
