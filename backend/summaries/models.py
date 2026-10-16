# summary_item 使用现有 UUID 主键，(summary_id, ordinal) 由 UniqueConstraint 约束。
import uuid

from django.db import models
from retention.erasure_models import ErasureModel


class Summary(ErasureModel, models.Model):
    class Validity(models.TextChoices):
        VALID = "valid", "valid"
        INVALID = "invalid", "invalid"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    requested_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="requested_by", related_name="+")
    from_date = models.DateField(null=True)
    to_date = models.DateField(null=True)
    scope_timezone = models.TextField(null=True)
    include_unknown_time = models.BooleanField(null=True)
    template = models.ForeignKey("configurations.ConfigVersion", on_delete=models.RESTRICT, db_column="template_id", related_name="+")
    visibility_policy = models.ForeignKey("configurations.ConfigVersion", on_delete=models.RESTRICT, db_column="visibility_policy_id", related_name="+")
    data_epoch = models.BigIntegerField(default=0)
    verification_epoch = models.BigIntegerField(default=0)
    validity = models.CharField(max_length=16, choices=Validity.choices)
    task_id = models.UUIDField()
    generated_at = models.DateTimeField()
    # Async tasks retain immutable admission input; render evidence is separate.
    # Legacy summaries retain their original Task.input_refs representation.
    execution_snapshot = models.JSONField(null=True)
    execution_sha256 = models.CharField(max_length=64, null=True)

    class Meta:
        db_table = 'sjia"."summary'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="summary_id_patient_uniq"),
        ]


class SummaryItem(ErasureModel, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    summary_id = models.UUIDField()
    patient_id = models.UUIDField()
    record_version_id = models.UUIDField()
    field_id = models.UUIDField()
    ordinal = models.IntegerField()
    snapshot = models.JSONField(null=True)

    class Meta:
        db_table = 'sjia"."summary_item'
        constraints = [
            models.UniqueConstraint(fields=["summary_id", "ordinal"], name="summary_item_pk_uniq"),
            models.UniqueConstraint(fields=["summary_id", "field_id"], name="summary_item_summary_id_field_id_uniq"),
        ]
