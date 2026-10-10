# 复合主键表 record_attachment 增加代理主键 id，原复合主键 (record_version_id, ordinal) 改为 UniqueConstraint。
# 复合外键（patient_id 与其它列成对出现）无法由 Django 表达，由原生 SQL 集中建立。
import uuid

from django.db import models

from core.fields import FiniteDecimalField


class Report(models.Model):
    class ReportStatus(models.TextChoices):
        UNKNOWN = "unknown", "unknown"
        PRELIMINARY = "preliminary", "preliminary"
        FINAL = "final", "final"
        CORRECTED = "corrected", "corrected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    source_revision = models.ForeignKey("sources.SourceRevision", on_delete=models.RESTRICT, null=True, db_column="source_revision_id", related_name="+")
    external_ref = models.TextField(null=True)
    source_organization = models.TextField(null=True)
    report_status = models.CharField(max_length=16, choices=ReportStatus.choices)
    sample_time_id = models.UUIDField(null=True)
    result_time_id = models.UUIDField(null=True)
    file_group_id = models.UUIDField(null=True)
    replaces_report_id = models.UUIDField(null=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."report'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="report_id_patient_uniq"),
        ]


class Record(models.Model):
    class Kind(models.TextChoices):
        OBSERVATION = "observation", "observation"
        REPORT = "report", "report"
        THERAPY = "therapy", "therapy"
        PROFESSIONAL = "professional", "professional"

    class Status(models.TextChoices):
        ACTIVE = "active", "active"
        WITHDRAWN = "withdrawn", "withdrawn"
        DISPOSED = "disposed", "disposed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    current_version = models.PositiveBigIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices)
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."record'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="record_id_patient_uniq"),
        ]
        indexes = [
            models.Index(fields=["patient_id", "kind", "-created_at", "id"], name="record_patient_kind"),
        ]


class RecordVersion(models.Model):
    class OriginType(models.TextChoices):
        SELF_REPORT = "self_report", "self_report"
        REPORT_TRANSCRIPTION = "report_transcription", "report_transcription"
        AUTHORIZED_CLINICAL = "authorized_clinical", "authorized_clinical"

    class CaptureMethod(models.TextChoices):
        MANUAL = "manual", "manual"
        OCR_CONFIRMED = "ocr_confirmed", "ocr_confirmed"
        VOICE_CONFIRMED = "voice_confirmed", "voice_confirmed"
        AUTHORIZED_IMPORT = "authorized_import", "authorized_import"

    class QualityStatus(models.TextChoices):
        PENDING = "pending", "pending"
        VALID = "valid", "valid"
        NEEDS_CORRECTION = "needs_correction", "needs_correction"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    record_id = models.UUIDField()
    version = models.PositiveBigIntegerField()
    # 自引用复合外键 (record_id, patient_id, supersedes_version) 指向 (record_id, patient_id, version)，由原生 SQL 建立。
    supersedes_version = models.BigIntegerField(null=True)
    author = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="author_id", related_name="+")
    origin_type = models.CharField(max_length=32, choices=OriginType.choices)
    capture_method = models.CharField(max_length=32, choices=CaptureMethod.choices)
    source_revision = models.ForeignKey("sources.SourceRevision", on_delete=models.RESTRICT, null=True, db_column="source_revision_id", related_name="+")
    source_organization = models.TextField(null=True)
    report_id = models.UUIDField(null=True)
    use_authorization_id = models.UUIDField()
    quality_status = models.CharField(max_length=16, choices=QualityStatus.choices)
    quality_reasons = models.JSONField()
    correction_reason = models.TextField(null=True)
    corrected_at = models.DateTimeField(null=True)
    recorded_at = models.DateTimeField()
    retention_policy = models.ForeignKey("retention.RetentionPolicy", on_delete=models.RESTRICT, db_column="retention_policy_id", related_name="+")

    class Meta:
        db_table = 'sjia"."record_version'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="record_version_id_patient_uniq"),
            models.UniqueConstraint(fields=["record_id", "patient_id", "version"], name="record_version_record_patient_version_uniq"),
        ]
        indexes = [
            models.Index(fields=["patient_id", "-recorded_at", "id"], name="version_recorded"),
        ]


class RecordAttachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    record_version_id = models.UUIDField()
    patient_id = models.UUIDField()
    file_id = models.UUIDField()
    ordinal = models.IntegerField()

    class Meta:
        db_table = 'sjia"."record_attachment'
        constraints = [
            models.UniqueConstraint(fields=["record_version_id", "ordinal"], name="record_attachment_pk_uniq"),
            models.UniqueConstraint(fields=["record_version_id", "file_id"], name="record_attachment_record_version_file_uniq"),
        ]


class RecordField(models.Model):
    class ValueType(models.TextChoices):
        DECIMAL = "decimal", "decimal"
        CATEGORICAL = "categorical", "categorical"
        TEXT = "text", "text"
        TIME = "time", "time"

    class ValueState(models.TextChoices):
        KNOWN = "known", "known"
        UNKNOWN = "unknown", "unknown"
        INVALID = "invalid", "invalid"

    class Qualifier(models.TextChoices):
        EQ = "eq", "eq"
        LT = "lt", "lt"
        LE = "le", "le"
        GT = "gt", "gt"
        GE = "ge", "ge"

    class SampleTimeOrigin(models.TextChoices):
        REPORT = "report", "report"
        ITEM = "item", "item"
        OBSERVATION = "observation", "observation"
        THERAPY = "therapy", "therapy"
        UNKNOWN = "unknown", "unknown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    record_version_id = models.UUIDField()
    ordinal = models.IntegerField()
    field_code = models.TextField()
    concept_code = models.TextField(null=True)
    dictionary_entry = models.ForeignKey("dictionaries.DictionaryEntry", on_delete=models.RESTRICT, null=True, db_column="dictionary_entry_id", related_name="+")
    original_name = models.TextField(null=True)
    value_type = models.CharField(max_length=16, choices=ValueType.choices)
    value_state = models.CharField(max_length=16, choices=ValueState.choices)
    raw_text = models.TextField(null=True)
    raw_numeric = FiniteDecimalField(null=True)
    categorical_value = models.TextField(null=True)
    qualifier = models.CharField(max_length=16, choices=Qualifier.choices, null=True)
    raw_unit = models.TextField(null=True)
    method = models.TextField(null=True)
    reference_interval = models.TextField(null=True)
    report_flag = models.TextField(null=True)
    medical_time_id = models.UUIDField(null=True)
    sample_time_origin = models.CharField(max_length=16, choices=SampleTimeOrigin.choices)
    anchor_id = models.UUIDField(null=True)
    confirmation_id = models.UUIDField(null=True)
    quality_reasons = models.JSONField()

    class Meta:
        db_table = 'sjia"."record_field'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="record_field_id_patient_uniq"),
            models.UniqueConstraint(fields=["id", "record_version_id", "patient_id"], name="record_field_id_record_version_patient_uniq"),
            models.UniqueConstraint(fields=["record_version_id", "ordinal"], name="record_field_record_version_ordinal_uniq"),
        ]
        indexes = [
            models.Index(fields=["patient_id", "concept_code", "medical_time_id"], name="field_patient_concept"),
        ]


class NormalizedMeasurement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    field_id = models.UUIDField()
    mapping = models.ForeignKey("dictionaries.UnitMapping", on_delete=models.RESTRICT, db_column="mapping_id", related_name="+")
    normalized_value = FiniteDecimalField()
    normalized_unit = models.TextField()
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."normalized_measurement'
        constraints = [
            models.UniqueConstraint(fields=["id", "field_id", "patient_id"], name="normalized_measurement_id_field_patient_uniq"),
            models.UniqueConstraint(fields=["field_id", "mapping_id"], name="normalized_measurement_field_mapping_uniq"),
        ]
