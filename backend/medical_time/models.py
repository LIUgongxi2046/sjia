import uuid

from django.db import models
from retention.erasure_models import ErasureModel

from core.fields import LocalTimestampField


class MedicalTime(ErasureModel, models.Model):
    class Precision(models.TextChoices):
        UNKNOWN = "unknown", "unknown"
        YEAR = "year", "year"
        MONTH = "month", "month"
        MONTH_RANGE = "month_range", "month_range"
        RELATIVE = "relative", "relative"
        DATE = "date", "date"
        MINUTE = "minute", "minute"
        SECOND = "second", "second"

    class ResolutionStatus(models.TextChoices):
        UNKNOWN = "unknown", "unknown"
        LOCAL_ONLY = "local_only", "local_only"
        RESOLVED = "resolved", "resolved"
        AMBIGUOUS = "ambiguous", "ambiguous"
        INVALID = "invalid", "invalid"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    raw_text = models.TextField(null=True)
    precision = models.CharField(max_length=16, choices=Precision.choices, null=True)
    local_year = models.IntegerField(null=True)
    local_month = models.SmallIntegerField(null=True)
    end_year = models.IntegerField(null=True)
    end_month = models.SmallIntegerField(null=True)
    relative_text = models.TextField(null=True)
    local_date = models.DateField(null=True)
    local_datetime = LocalTimestampField(null=True)
    timezone_name = models.TextField(null=True)
    utc_offset_minutes = models.SmallIntegerField(null=True)
    instant = models.DateTimeField(null=True)
    resolution_status = models.CharField(max_length=16, choices=ResolutionStatus.choices, null=True)

    class Meta:
        db_table = 'sjia"."medical_time'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="medical_time_id_patient_uniq"),
        ]
        indexes = [
            models.Index(fields=["patient_id", "precision", "-instant", "id"], name="time_ordering"),
        ]
