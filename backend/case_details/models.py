# SQL 中 record_field_group_member、statement_evidence、imaging_study_site、external_score_component
# 使用复合主键。Django 不支持复合主键，因此增加代理主键 id，原复合主键改由 UniqueConstraint 表达。
import uuid

from django.db import models
from retention.erasure_models import ErasureModel

from core.fields import FiniteDecimalField


class PatientProfileVersion(ErasureModel, models.Model):
    class Sex(models.TextChoices):
        MALE = "male", "male"
        FEMALE = "female", "female"
        OTHER = "other", "other"
        UNKNOWN = "unknown", "unknown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    record_version_id = models.UUIDField(unique=True)
    source_field_id = models.UUIDField()
    sex = models.CharField(max_length=16, choices=Sex.choices, null=True)
    reported_age_years = models.IntegerField(null=True)
    reported_age_months = models.SmallIntegerField(null=True)
    age_raw_text = models.TextField(null=True)
    age_reference_time_id = models.UUIDField(null=True)
    birth_date = models.DateField(null=True)
    patient_name = models.TextField(null=True)
    chief_complaint = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."patient_profile_version'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="patient_profile_version_id_patient_uniq"),
        ]


class ClinicalEpisode(ErasureModel, models.Model):
    class EpisodeType(models.TextChoices):
        ONSET = "onset", "onset"
        HOSPITALIZATION = "hospitalization", "hospitalization"
        ICU = "icu", "icu"
        CONSULTATION = "consultation", "consultation"
        TRANSFER = "transfer", "transfer"
        RELAPSE = "relapse", "relapse"
        OUTPATIENT = "outpatient", "outpatient"
        OTHER = "other", "other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    record_version_id = models.UUIDField()
    source_field_id = models.UUIDField()
    episode_type = models.CharField(max_length=16, choices=EpisodeType.choices, null=True)
    label = models.TextField(null=True)
    start_time_id = models.UUIDField(null=True)
    end_time_id = models.UUIDField(null=True)
    organization_text = models.TextField(null=True)
    department_text = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."clinical_episode'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="clinical_episode_id_patient_uniq"),
        ]
        indexes = [
            models.Index(fields=["patient_id", "start_time_id", "id"], name="episode_patient_time"),
        ]


class RecordFieldGroup(ErasureModel, models.Model):
    class GroupType(models.TextChoices):
        LABORATORY = "laboratory", "laboratory"
        VITALS = "vitals", "vitals"
        MICROBIOLOGY = "microbiology", "microbiology"
        PHYSICAL_EXAM = "physical_exam", "physical_exam"
        OTHER = "other", "other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    record_version_id = models.UUIDField()
    source_field_id = models.UUIDField()
    group_type = models.CharField(max_length=16, choices=GroupType.choices, null=True)
    label = models.TextField(null=True)
    specimen_text = models.TextField(null=True)
    method_text = models.TextField(null=True)
    medical_time_id = models.UUIDField(null=True)
    source_quote = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."record_field_group'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="record_field_group_id_patient_uniq"),
            models.UniqueConstraint(fields=["id", "record_version_id", "patient_id"], name="record_field_group_id_record_version_id_patient_uniq"),
        ]
        indexes = [
            models.Index(fields=["patient_id", "group_type", "medical_time_id", "id"], name="group_patient_kind"),
        ]


class RecordFieldGroupMember(ErasureModel, models.Model):
    class Role(models.TextChoices):
        RESULT = "result", "result"
        SPECIMEN = "specimen", "specimen"
        METHOD = "method", "method"
        INTERPRETATION = "interpretation", "interpretation"
        CONTEXT = "context", "context"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    group_id = models.UUIDField()
    patient_id = models.UUIDField()
    record_version_id = models.UUIDField()
    field_id = models.UUIDField()
    ordinal = models.IntegerField()
    role = models.CharField(max_length=16, choices=Role.choices, null=True)

    class Meta:
        db_table = 'sjia"."record_field_group_member'
        constraints = [
            models.UniqueConstraint(fields=["group_id", "field_id"], name="record_field_group_member_pk_uniq"),
            models.UniqueConstraint(fields=["group_id", "ordinal"], name="record_field_group_member_group_id_ordinal_uniq"),
        ]


class ClinicalStatement(ErasureModel, models.Model):
    class StatementType(models.TextChoices):
        HISTORY = "history", "history"
        PHYSICAL_EXAM = "physical_exam", "physical_exam"
        DIAGNOSIS = "diagnosis", "diagnosis"
        DIFFERENTIAL = "differential", "differential"
        TOLERANCE = "tolerance", "tolerance"
        SYMPTOM = "symptom", "symptom"
        LABORATORY_SUMMARY = "laboratory_summary", "laboratory_summary"
        FOLLOWUP_MODE = "followup_mode", "followup_mode"

    class Subject(models.TextChoices):
        PATIENT = "patient", "patient"
        FAMILY = "family", "family"

    class AssertionStatus(models.TextChoices):
        PRESENT = "present", "present"
        ABSENT = "absent", "absent"
        NORMAL = "normal", "normal"
        ABNORMAL = "abnormal", "abnormal"
        SOURCE_DIAGNOSIS = "source_diagnosis", "source_diagnosis"
        HISTORICAL_CONSIDERATION = "historical_consideration", "historical_consideration"
        UNSUPPORTED = "unsupported", "unsupported"
        REQUIRES_DIFFERENTIATION = "requires_differentiation", "requires_differentiation"
        UNKNOWN = "unknown", "unknown"

    class Laterality(models.TextChoices):
        LEFT = "left", "left"
        RIGHT = "right", "right"
        BILATERAL = "bilateral", "bilateral"
        MIDLINE = "midline", "midline"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    record_version_id = models.UUIDField()
    source_field_id = models.UUIDField()
    statement_type = models.CharField(max_length=24, choices=StatementType.choices, null=True)
    subject = models.CharField(max_length=16, choices=Subject.choices, null=True)
    category = models.TextField(null=True)
    concept_code = models.TextField(null=True)
    label = models.TextField(null=True)
    assertion_status = models.CharField(max_length=32, choices=AssertionStatus.choices, null=True)
    status_raw_text = models.TextField(null=True)
    medical_time_id = models.UUIDField(null=True)
    body_site = models.TextField(null=True)
    laterality = models.CharField(max_length=16, choices=Laterality.choices, null=True)

    class Meta:
        db_table = 'sjia"."clinical_statement'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="clinical_statement_id_patient_uniq"),
        ]
        indexes = [
            models.Index(fields=["patient_id", "statement_type", "medical_time_id", "id"], name="statement_patient_kind"),
        ]


class StatementEvidence(ErasureModel, models.Model):
    class Role(models.TextChoices):
        SUPPORTS = "supports", "supports"
        CONTRADICTS = "contradicts", "contradicts"
        CONTEXT = "context", "context"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    statement_id = models.UUIDField()
    patient_id = models.UUIDField()
    evidence_field_id = models.UUIDField()
    evidence_record_version_id = models.UUIDField()
    role = models.CharField(max_length=16, choices=Role.choices, null=True)

    class Meta:
        db_table = 'sjia"."statement_evidence'
        constraints = [
            models.UniqueConstraint(fields=["statement_id", "evidence_field_id", "role"], name="statement_evidence_pk_uniq"),
        ]


class ImagingStudy(ErasureModel, models.Model):
    class Modality(models.TextChoices):
        CT = "CT", "CT"
        MRI = "MRI", "MRI"
        ULTRASOUND = "ultrasound", "ultrasound"
        RADIOGRAPH = "radiograph", "radiograph"
        OTHER = "other", "other"
        UNKNOWN = "unknown", "unknown"

    class Contrast(models.TextChoices):
        NONCONTRAST = "noncontrast", "noncontrast"
        ENHANCED = "enhanced", "enhanced"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    record_version_id = models.UUIDField()
    source_field_id = models.UUIDField()
    report_id = models.UUIDField()
    modality = models.CharField(max_length=16, choices=Modality.choices, null=True)
    contrast = models.CharField(max_length=16, choices=Contrast.choices, null=True)
    study_time_id = models.UUIDField(null=True)
    external_study_ref = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."imaging_study'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="imaging_study_id_patient_uniq"),
        ]
        indexes = [
            models.Index(fields=["patient_id", "modality", "study_time_id", "id"], name="imaging_patient_time"),
        ]


class ImagingStudySite(ErasureModel, models.Model):
    class Laterality(models.TextChoices):
        LEFT = "left", "left"
        RIGHT = "right", "right"
        BILATERAL = "bilateral", "bilateral"
        MIDLINE = "midline", "midline"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    study_id = models.UUIDField()
    patient_id = models.UUIDField()
    ordinal = models.IntegerField()
    body_site = models.TextField(null=True)
    laterality = models.CharField(max_length=16, choices=Laterality.choices, null=True)

    class Meta:
        db_table = 'sjia"."imaging_study_site'
        constraints = [
            models.UniqueConstraint(fields=["study_id", "ordinal"], name="imaging_study_site_pk_uniq"),
        ]


class ImagingAssetLink(ErasureModel, models.Model):
    class LinkStatus(models.TextChoices):
        REQUIRES_CONFIRMATION = "requires_confirmation", "requires_confirmation"
        CONFIRMED = "confirmed", "confirmed"
        REJECTED = "rejected", "rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    record_version_id = models.UUIDField()
    source_field_id = models.UUIDField()
    file_id = models.UUIDField()
    study_id = models.UUIDField(null=True)
    link_status = models.CharField(max_length=24, choices=LinkStatus.choices, null=True)
    evidence_field_id = models.UUIDField(null=True)
    evidence_record_version_id = models.UUIDField(null=True)
    confirmed_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, null=True, db_column="confirmed_by", related_name="+")
    confirmed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = 'sjia"."imaging_asset_link'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="imaging_asset_link_id_patient_uniq"),
            models.UniqueConstraint(fields=["record_version_id", "file_id"], name="imaging_asset_link_record_version_id_file_id_uniq"),
        ]


class MedicationEvent(ErasureModel, models.Model):
    class Action(models.TextChoices):
        HISTORICAL_TREATMENT = "historical_treatment", "historical_treatment"
        STOPPED_BY_FAMILY = "stopped_by_family", "stopped_by_family"
        RESTART = "restart", "restart"
        STOP = "stop", "stop"
        START = "start", "start"
        REGIMEN_RECORD = "regimen_record", "regimen_record"
        TAPER = "taper", "taper"
        DOSE_ROUTE_CHANGE = "dose_route_change", "dose_route_change"
        INTERVAL_CHANGE = "interval_change", "interval_change"
        ONGOING = "ongoing", "ongoing"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    record_version_id = models.UUIDField()
    source_field_id = models.UUIDField()
    action = models.CharField(max_length=24, choices=Action.choices, null=True)
    medical_time_id = models.UUIDField(null=True)
    previous_event_id = models.UUIDField(null=True)
    reason_statement_id = models.UUIDField(null=True)
    note = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."medication_event'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="medication_event_id_patient_uniq"),
            models.UniqueConstraint(fields=["id", "record_version_id", "patient_id"], name="medication_event_id_record_version_id_patient_uniq"),
        ]
        indexes = [
            models.Index(fields=["patient_id", "medical_time_id", "id"], name="medication_patient_time"),
        ]


class MedicationEventItem(ErasureModel, models.Model):
    class IntervalUnit(models.TextChoices):
        HOUR = "hour", "hour"
        DAY = "day", "day"
        WEEK = "week", "week"
        MONTH = "month", "month"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    event_id = models.UUIDField()
    patient_id = models.UUIDField()
    record_version_id = models.UUIDField()
    source_field_id = models.UUIDField()
    ordinal = models.IntegerField()
    medication_name = models.TextField(null=True)
    medication_code = models.TextField(null=True)
    dose = FiniteDecimalField(null=True)
    dose_unit = models.TextField(null=True)
    concentration_text = models.TextField(null=True)
    route_text = models.TextField(null=True)
    frequency_text = models.TextField(null=True)
    interval_value = FiniteDecimalField(null=True)
    interval_unit = models.CharField(max_length=16, choices=IntervalUnit.choices, null=True)

    class Meta:
        db_table = 'sjia"."medication_event_item'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="medication_event_item_id_patient_uniq"),
            models.UniqueConstraint(fields=["event_id", "ordinal"], name="medication_event_item_event_id_ordinal_uniq"),
        ]
        indexes = [
            models.Index(fields=["patient_id", "medication_name", "event_id"], name="medication_item_lookup"),
        ]


class ProcedureEvent(ErasureModel, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    record_version_id = models.UUIDField()
    source_field_id = models.UUIDField()
    procedure_name = models.TextField(null=True)
    medical_time_id = models.UUIDField(null=True)
    related_medication_event_id = models.UUIDField(null=True)
    body_site = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."procedure_event'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="procedure_event_id_patient_uniq"),
        ]


class ExternalScoreObservation(ErasureModel, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    record_version_id = models.UUIDField()
    source_field_id = models.UUIDField()
    instrument = models.TextField(null=True)
    instrument_version = models.TextField(null=True)
    medical_time_id = models.UUIDField()
    total_field_id = models.UUIDField()
    context_text = models.TextField(null=True)
    note = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."external_score_observation'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="external_score_observation_id_patient_uniq"),
            models.UniqueConstraint(fields=["id", "record_version_id", "patient_id"], name="external_score_observation_id_record_version_id_patient_uniq"),
        ]
        indexes = [
            models.Index(fields=["patient_id", "instrument", "medical_time_id", "id"], name="external_score_patient_time"),
        ]


class ExternalScoreComponent(ErasureModel, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    score_id = models.UUIDField()
    patient_id = models.UUIDField()
    record_version_id = models.UUIDField()
    field_id = models.UUIDField()
    component_code = models.TextField(null=True)
    ordinal = models.IntegerField()

    class Meta:
        db_table = 'sjia"."external_score_component'
        constraints = [
            models.UniqueConstraint(fields=["score_id", "component_code"], name="external_score_component_pk_uniq"),
            models.UniqueConstraint(fields=["score_id", "ordinal"], name="external_score_component_score_id_ordinal_uniq"),
        ]


class FollowupEncounter(ErasureModel, models.Model):
    class Kind(models.TextChoices):
        BASELINE_ASSESSMENT = "baseline_assessment", "baseline_assessment"
        TREATMENT_ASSESSMENT = "treatment_assessment", "treatment_assessment"
        FOLLOWUP_ASSESSMENT = "followup_assessment", "followup_assessment"
        TREATMENT_RESPONSE = "treatment_response", "treatment_response"
        LATEST_FOLLOWUP = "latest_followup", "latest_followup"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    record_version_id = models.UUIDField()
    source_field_id = models.UUIDField()
    kind = models.CharField(max_length=24, choices=Kind.choices, null=True)
    label = models.TextField(null=True)
    medical_time_id = models.UUIDField()
    channel_text = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."followup_encounter'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="followup_encounter_id_patient_uniq"),
        ]
        indexes = [
            models.Index(fields=["patient_id", "medical_time_id", "id"], name="followup_patient_time"),
        ]


class FollowupReference(ErasureModel, models.Model):
    class RelationKind(models.TextChoices):
        OUTCOME = "outcome", "outcome"
        LABORATORY = "laboratory", "laboratory"
        SCORE = "score", "score"
        TREATMENT_CONTEXT = "treatment_context", "treatment_context"
        REPORTED_CURRENT_TREATMENT = "reported_current_treatment", "reported_current_treatment"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    encounter_id = models.UUIDField()
    statement_id = models.UUIDField(null=True)
    group_id = models.UUIDField(null=True)
    score_id = models.UUIDField(null=True)
    medication_event_id = models.UUIDField(null=True)
    relation_kind = models.CharField(max_length=32, choices=RelationKind.choices, null=True)

    class Meta:
        db_table = 'sjia"."followup_reference'


class DataQualityIssue(ErasureModel, models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "open"
        NEEDS_REVIEW = "needs_review", "needs_review"
        RESOLVED = "resolved", "resolved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    record_version_id = models.UUIDField()
    source_field_id = models.UUIDField()
    category = models.TextField(null=True)
    description = models.TextField(null=True)
    handling = models.TextField(null=True)
    status = models.CharField(max_length=16, choices=Status.choices, null=True)
    resolution_text = models.TextField(null=True)
    resolution_field_id = models.UUIDField(null=True)
    resolution_record_version_id = models.UUIDField(null=True)
    resolved_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, null=True, db_column="resolved_by", related_name="+")
    resolved_at = models.DateTimeField(null=True)

    class Meta:
        db_table = 'sjia"."data_quality_issue'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="data_quality_issue_id_patient_uniq"),
        ]
        indexes = [
            models.Index(fields=["patient_id", "status", "id"], name="quality_patient_status"),
        ]


class DataQualityIssueReference(ErasureModel, models.Model):
    class Role(models.TextChoices):
        SOURCE = "source", "source"
        COMPARISON = "comparison", "comparison"
        AFFECTED = "affected", "affected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    issue_id = models.UUIDField()
    field_id = models.UUIDField(null=True)
    record_version_id = models.UUIDField(null=True)
    file_id = models.UUIDField(null=True)
    role = models.CharField(max_length=16, choices=Role.choices, null=True)

    class Meta:
        db_table = 'sjia"."data_quality_issue_reference'
