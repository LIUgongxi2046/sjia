import uuid

from django.db import models
from retention.erasure_models import ErasureModel

from archives.fields import NumericField
from core.fields import Sha256HexField


class PatientEntity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()

    class Meta:
        abstract = True


# 复合外键、状态约束与索引由不可变 SQL migration 管理。
class Archive(PatientEntity):
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."archive'


class ArchiveBatch(PatientEntity):
    archive_id = models.UUIDField()
    submitted_by = models.UUIDField()
    logical_request_id = models.UUIDField()
    version = models.BigIntegerField()
    received_at = models.DateTimeField()
    quota_policy_id = models.UUIDField(null=True)
    intake_mode = models.CharField(max_length=8, default='ocr')
    budget_status = models.TextField()
    status = models.TextField()

    class Meta:
        db_table = 'sjia"."archive_batch'


class ArchiveDocument(ErasureModel, PatientEntity):
    current_version = models.BigIntegerField()
    created_at = models.DateTimeField()
    retired_at = models.DateTimeField(null=True)
    retired_by = models.UUIDField(null=True)
    retirement_id = models.UUIDField(null=True)
    retirement_reason = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."archive_document'


class ArchiveRegrouping(ErasureModel, PatientEntity):
    batch_id = models.UUIDField()
    actor_id = models.UUIDField()
    request_id = models.UUIDField()
    input_sha256 = Sha256HexField()
    source_refs = models.JSONField(null=True)
    result_refs = models.JSONField(null=True)
    result_sha256 = Sha256HexField()
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."archive_regrouping'
        constraints = [models.UniqueConstraint(fields=['actor_id', 'patient_id', 'request_id'],
            name='archive_regrouping_request_unique')]


class ArchiveDocumentVersion(ErasureModel, PatientEntity):
    document_id = models.UUIDField()
    version = models.BigIntegerField()
    batch_id = models.UUIDField()
    document_type = models.TextField(null=True)
    organization = models.TextField(null=True)
    proposed_metadata = models.JSONField(null=True)
    confirmed_text = models.TextField(null=True)
    grouping_status = models.TextField()
    documented_time_id = models.UUIDField(null=True)
    available_at = models.DateTimeField()
    confirmed_by = models.UUIDField(null=True)
    confirmed_at = models.DateTimeField(null=True)
    supersedes_version = models.BigIntegerField(null=True)
    reason = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."archive_document_version'


class ArchivePage(PatientEntity):
    batch_id = models.UUIDField()
    ordinal = models.IntegerField()
    file_page_id = models.UUIDField()
    file_id = models.UUIDField()
    file_object_version = models.TextField()
    checksum = Sha256HexField()
    version = models.BigIntegerField()
    status = models.TextField()
    status_version = models.BigIntegerField()
    current_task_id = models.UUIDField(null=True)
    available_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."archive_page'


class ArchiveDocumentPage(PatientEntity):
    document_version_id = models.UUIDField()
    page_id = models.UUIDField()
    page_order = models.IntegerField()

    class Meta:
        db_table = 'sjia"."archive_document_page'


class ArchivePageAttempt(ErasureModel, PatientEntity):
    page_id = models.UUIDField()
    page_version = models.BigIntegerField()
    task_id = models.UUIDField()
    task_attempt_id = models.UUIDField()
    attempt_number = models.IntegerField()
    input_sha256 = Sha256HexField()
    model_version = models.TextField()
    candidate_set_id = models.UUIDField(null=True)
    provider_request_id = models.TextField(null=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True)
    duration_ms = models.BigIntegerField(null=True)
    cost = NumericField(null=True)
    currency = models.TextField(null=True)
    usage_evidence = models.JSONField(null=True)
    status = models.TextField()
    error_code = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."archive_page_attempt'


class ArchiveFact(PatientEntity):
    current_version = models.BigIntegerField()
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."archive_fact'


class ArchiveFactVersion(ErasureModel, PatientEntity):
    fact_id = models.UUIDField()
    version = models.BigIntegerField()
    record_version_id = models.UUIDField()
    field_id = models.UUIDField()
    document_version_id = models.UUIDField(null=True)
    page_id = models.UUIDField(null=True)
    source_anchor_id = models.UUIDField(null=True)
    context_type = models.TextField(null=True)
    assertion_state = models.TextField(null=True)
    episode_id = models.UUIDField(null=True)
    medical_time_id = models.UUIDField(null=True)
    raw_time_text = models.TextField(null=True)
    time_origin = models.TextField(null=True)
    documented_time_id = models.UUIDField(null=True)
    available_at = models.DateTimeField(null=True)
    availability_status = models.TextField()
    confirmation_id = models.UUIDField(null=True)
    created_at = models.DateTimeField()
    supersedes_version = models.BigIntegerField(null=True)
    reason = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."archive_fact_version'


class ArchiveDocumentEpisode(PatientEntity):
    document_version_id = models.UUIDField()
    episode_id = models.UUIDField()
    evidence_anchor_id = models.UUIDField()
    confirmed_by = models.UUIDField()
    confirmed_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."archive_document_episode'


class ArchiveRelationVersion(ErasureModel, PatientEntity):
    relation_id = models.UUIDField()
    version = models.BigIntegerField()
    left_fact_version_id = models.UUIDField(null=True)
    right_fact_version_id = models.UUIDField(null=True)
    left_file_id = models.UUIDField(null=True)
    right_file_id = models.UUIDField(null=True)
    relation_type = models.TextField(null=True)
    status = models.TextField(null=True)
    evidence_ref = models.TextField(null=True)
    reason = models.TextField(null=True)
    author_id = models.UUIDField()
    created_at = models.DateTimeField()
    verification_id = models.UUIDField(null=True)
    supersedes_version = models.BigIntegerField(null=True)

    class Meta:
        db_table = 'sjia"."archive_relation_version'


class PurposeCopy(ErasureModel, PatientEntity):
    original_file_id = models.UUIDField()
    derived_file_id = models.UUIDField()
    version = models.BigIntegerField()
    purpose = models.TextField()
    environment = models.TextField()
    use_authorization_id = models.UUIDField()
    processing_version = models.TextField()
    processing_evidence = models.JSONField(null=True)
    date_strategy = models.TextField()
    clinical_integrity_status = models.TextField()
    status = models.TextField()
    created_by = models.UUIDField()
    created_at = models.DateTimeField()
    disposition_ref = models.UUIDField(null=True)

    class Meta:
        db_table = 'sjia"."purpose_copy'


class PurposeCopyReview(ErasureModel, PatientEntity):
    copy_id = models.UUIDField()
    copy_version = models.BigIntegerField()
    reviewer_id = models.UUIDField()
    reviewed_at = models.DateTimeField()
    evidence_ref = models.TextField(null=True)
    checked_scopes = models.JSONField(null=True)
    identity_result = models.TextField(null=True)
    clinical_result = models.TextField(null=True)
    original_checksum = Sha256HexField()
    derived_checksum = Sha256HexField()
    revoked_at = models.DateTimeField(null=True)

    class Meta:
        db_table = 'sjia"."purpose_copy_review'


class ObservationPeriodVersion(ErasureModel, PatientEntity):
    period_id = models.UUIDField()
    version = models.BigIntegerField()
    owner_id = models.UUIDField()
    start_time_id = models.UUIDField()
    end_time_id = models.UUIDField()
    label = models.TextField(null=True)
    label_source = models.TextField(null=True)
    episode_id = models.UUIDField(null=True)
    created_at = models.DateTimeField()
    supersedes_version = models.BigIntegerField(null=True)

    class Meta:
        db_table = 'sjia"."observation_period_version'


class DeltaSummary(ErasureModel, PatientEntity):
    owner_id = models.UUIDField()
    batch_id = models.UUIDField()
    baseline_refs = models.JSONField(null=True)
    current_refs = models.JSONField(null=True)
    data_epoch = models.BigIntegerField()
    authorization_version = models.BigIntegerField()
    visibility_policy_id = models.UUIDField()
    validity = models.TextField()
    generated_at = models.DateTimeField()
    task_id = models.UUIDField()

    class Meta:
        db_table = 'sjia"."delta_summary'


class DeltaSummaryItem(ErasureModel, PatientEntity):
    summary_id = models.UUIDField()
    ordinal = models.IntegerField()
    item_kind = models.TextField(null=True)
    item_source_refs = models.JSONField(null=True)
    limitations = models.JSONField(null=True)
    content = models.JSONField(null=True)

    class Meta:
        db_table = 'sjia"."delta_summary_item'


class EvaluationDatasetVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    dataset_id = models.UUIDField()
    version = models.BigIntegerField()
    purpose = models.TextField()
    environment = models.TextField()
    created_by = models.UUIDField()
    created_at = models.DateTimeField()
    frozen_at = models.DateTimeField(null=True)
    protocol_ref = models.TextField()
    coverage = models.JSONField()
    checksum = Sha256HexField()
    status = models.TextField()

    class Meta:
        db_table = 'sjia"."evaluation_dataset_version'


class EvaluationPatientPartition(ErasureModel, PatientEntity):
    dataset_version_id = models.UUIDField()
    partition = models.TextField()
    use_authorization_id = models.UUIDField()
    source_refs = models.JSONField(null=True)

    class Meta:
        db_table = 'sjia"."evaluation_patient_partition'


class EvaluationLabel(ErasureModel, PatientEntity):
    partition_id = models.UUIDField()
    observation_period_id = models.UUIDField()
    label_state = models.TextField(null=True)
    reference_answer_ref = models.TextField(null=True)
    reference_recognition_at = models.DateTimeField(null=True)
    confirmed_by = models.UUIDField(null=True)
    confirmed_at = models.DateTimeField(null=True)
    limitations = models.JSONField(null=True)

    class Meta:
        db_table = 'sjia"."evaluation_label'


class EvaluationRun(ErasureModel, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    dataset_version_id = models.UUIDField()
    operator_id = models.UUIDField()
    implementation_sha256 = Sha256HexField()
    rules_refs = models.JSONField()
    input_refs = models.JSONField(null=True)
    result_refs = models.JSONField(null=True)
    denominators = models.JSONField(null=True)
    limitations = models.JSONField(null=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True)
    status = models.TextField()
    mode = models.TextField()

    class Meta:
        db_table = 'sjia"."evaluation_run'


class PersonalRecordTask(ErasureModel, PatientEntity):
    owner_id = models.UUIDField()
    version = models.BigIntegerField()
    title = models.TextField(null=True)
    description = models.TextField(null=True)
    status = models.TextField()
    retention_policy = models.ForeignKey('retention.RetentionPolicy', on_delete=models.RESTRICT,
        db_column='retention_policy_id', related_name='+')
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True)

    class Meta:
        db_table = 'sjia"."personal_record_task'


class MaterialReminder(ErasureModel, PatientEntity):
    owner_id = models.UUIDField()
    version = models.BigIntegerField()
    target_type = models.TextField()
    target_ref = models.UUIDField()
    target_version = models.BigIntegerField()
    schedule_at = models.DateTimeField(null=True)
    timezone_name = models.TextField()
    quiet_start = models.TimeField(null=True)
    quiet_end = models.TimeField(null=True)
    status = models.TextField(default='disabled')
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."material_reminder'


class MaterialReminderNotice(ErasureModel, PatientEntity):
    reminder_id = models.UUIDField()
    reminder_version = models.BigIntegerField()
    scheduled_at = models.DateTimeField()
    channel = models.TextField()
    deduplication_key = models.TextField()
    status = models.TextField()
    permit_refs = models.JSONField(null=True)
    provider_request_id = models.TextField(null=True)
    receipt_refs = models.JSONField(null=True)
    delivered_at = models.DateTimeField(null=True)
    read_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."material_reminder_notice'
