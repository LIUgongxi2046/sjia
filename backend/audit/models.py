import uuid

from django.db import models


class AuditEvent(models.Model):
    class Outcome(models.TextChoices):
        ALLOWED = "allowed", "allowed"
        DENIED = "denied", "denied"
        COMMITTED = "committed", "committed"
        FAILED = "failed", "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    actor = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, null=True, db_column="actor_id", related_name="+")
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, null=True, db_column="patient_id", related_name="+")
    action = models.TextField()
    object_type = models.TextField()
    object_id = models.UUIDField(null=True)
    object_version = models.TextField(null=True)
    request_id = models.UUIDField(null=True)
    occurred_at = models.DateTimeField()
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    reason_code = models.TextField(null=True)
    retention_policy = models.ForeignKey("retention.RetentionPolicy", on_delete=models.RESTRICT, db_column="retention_policy_id", related_name="+")

    class Meta:
        db_table = 'sjia"."audit_event'
        indexes = [
            models.Index(fields=["object_type", "object_id", "-occurred_at"], name="audit_object"),
            models.Index(fields=["actor_id", "action", "-occurred_at"], name="audit_actor_action_time"),
            models.Index(fields=["object_id", "action", "-occurred_at"], condition=models.Q(object_type="provider_config"), name="audit_provider_state"),
        ]


class ProductEvent(models.Model):
    class EventCode(models.TextChoices):
        EV_01 = "EV-01", "EV-01"
        EV_02 = "EV-02", "EV-02"
        EV_03 = "EV-03", "EV-03"
        EV_04 = "EV-04", "EV-04"
        EV_05 = "EV-05", "EV-05"
        EV_06 = "EV-06", "EV-06"
        EV_07 = "EV-07", "EV-07"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    event_code = models.CharField(max_length=16, choices=EventCode.choices)
    request_id = models.UUIDField(null=True)
    resource_id = models.UUIDField(null=True)
    resource_version = models.TextField(null=True)
    outcome = models.TextField()
    duration_ms = models.BigIntegerField(null=True)
    occurred_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."product_event'
