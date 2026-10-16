# 复合主键表 assessment_input 增加代理主键 id，原复合主键 (assessment_id, variable_code) 改为
# UniqueConstraint assessment_input_pk_uniq。assessment_output 的主键列同时是外键，用 OneToOneField 表达。
# 复合外键（patient_id 与其它列成对出现）无法由 Django 表达，由原生 SQL 集中建立。
import uuid

from django.db import models
from retention.erasure_models import ErasureModel

from core.fields import FiniteDecimalField


class Assessment(ErasureModel, models.Model):
    class Kind(models.TextChoices):
        CLASSIFICATION = "classification", "classification"
        MS = "ms", "ms"
        HLH = "hlh", "hlh"

    class ComputationStatus(models.TextChoices):
        NOT_COMPUTED = "not_computed", "not_computed"
        CONFIGURATION_PENDING = "configuration_pending", "configuration_pending"
        INSUFFICIENT = "insufficient", "insufficient"
        COMPLETED = "completed", "completed"
        FAILED = "failed", "failed"

    class Validity(models.TextChoices):
        VALID = "valid", "valid"
        INVALID = "invalid", "invalid"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    assessment_at = models.DateTimeField(null=True)
    rule = models.ForeignKey("configurations.ConfigVersion", on_delete=models.RESTRICT, null=True, db_column="rule_id", related_name="+")
    window_policy = models.ForeignKey("configurations.ConfigVersion", on_delete=models.RESTRICT, null=True, db_column="window_policy_id", related_name="+")
    task_id = models.UUIDField()
    data_epoch = models.BigIntegerField()
    verification_epoch = models.BigIntegerField()
    computation_status = models.CharField(max_length=32, choices=ComputationStatus.choices, null=True)
    validity = models.CharField(max_length=16, choices=Validity.choices)
    missing_items = models.JSONField(null=True)
    computed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = 'sjia"."assessment'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="assessment_id_patient_uniq"),
            models.CheckConstraint(condition=models.Q(kind__in=["classification", "ms", "hlh"]), name="assessment_reference_kind_valid"),
        ]


class AssessmentInput(ErasureModel, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    assessment_id = models.UUIDField()
    patient_id = models.UUIDField()
    variable_code = models.TextField(null=True)
    record_version_id = models.UUIDField()
    field_id = models.UUIDField()
    verification_id = models.UUIDField()
    normalization_id = models.UUIDField(null=True)
    input_snapshot = models.JSONField(null=True)

    class Meta:
        db_table = 'sjia"."assessment_input'
        constraints = [
            models.UniqueConstraint(fields=["assessment_id", "variable_code"], name="assessment_input_pk_uniq"),
        ]


class AssessmentOutput(ErasureModel, models.Model):
    class ClassificationResult(models.TextChoices):
        MEETS = "meets", "meets"
        NOT_MEETS = "not_meets", "not_meets"
        INSUFFICIENT = "insufficient", "insufficient"
        NOT_APPLICABLE = "not_applicable", "not_applicable"

    assessment = models.OneToOneField("assessment.Assessment", primary_key=True, on_delete=models.RESTRICT, db_column="assessment_id", related_name="+")
    schema_version = models.TextField()
    classification_conditions = models.JSONField(null=True)
    classification_result = models.CharField(max_length=16, choices=ClassificationResult.choices, null=True)
    ms_contributions = models.JSONField(null=True)
    ms_total = FiniteDecimalField(null=True)
    ms_threshold_result = models.BooleanField(null=True)
    hlh_result = models.JSONField(null=True)

    class Meta:
        db_table = 'sjia"."assessment_output'
        constraints = [models.CheckConstraint(
            condition=models.Q(hlh_result__isnull=True) | (models.Q(classification_conditions__isnull=True)
                & models.Q(classification_result__isnull=True) & models.Q(ms_contributions__isnull=True)
                & models.Q(ms_total__isnull=True) & models.Q(ms_threshold_result__isnull=True)),
            name='assessment_hlh_output_separate')]
