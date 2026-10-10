import uuid

from django.db import models


class Draft(models.Model):
    class FormType(models.TextChoices):
        OBSERVATION = "observation", "observation"
        REPORT = "report", "report"
        THERAPY = "therapy", "therapy"
        CORRECTION = "correction", "correction"

    class Status(models.TextChoices):
        EDITING = "editing", "editing"
        SUBMITTED = "submitted", "submitted"
        ABANDONED = "abandoned", "abandoned"
        EXPIRED = "expired", "expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    owner = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="owner_id", related_name="+")
    form_type = models.CharField(max_length=16, choices=FormType.choices)
    version = models.PositiveBigIntegerField()
    schema_version = models.TextField()
    payload = models.JSONField()
    # correction_record_id 与 correction_version 参与复合外键 (correction_record_id, patient_id, correction_version)，约束由原生 SQL 迁移建立
    correction_record_id = models.UUIDField(null=True)
    correction_version = models.BigIntegerField(null=True)
    status = models.CharField(max_length=16, choices=Status.choices)
    submitted_request = models.ForeignKey("requests.WriteRequest", on_delete=models.RESTRICT, null=True, db_column="submitted_request_id", related_name="+")
    saved_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True)
    retention_policy = models.ForeignKey("retention.RetentionPolicy", on_delete=models.RESTRICT, db_column="retention_policy_id", related_name="+")

    class Meta:
        db_table = 'sjia"."draft'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="draft_id_patient_uniq"),
        ]
        indexes = [
            models.Index(fields=["owner_id", "patient_id", "form_type", "status", "-saved_at"], name="draft_lookup"),
        ]
