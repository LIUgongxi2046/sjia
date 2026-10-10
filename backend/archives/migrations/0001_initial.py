import hashlib
import uuid
from pathlib import Path

from django.db import migrations, models

from archives.fields import NumericField
from core.fields import Sha256HexField


SQL_PATH = Path(__file__).resolve().parents[3] / 'data-design/v2-schema.sql'
SQL_SHA256 = 'b26a85c30b1f4d757187d8e74be3a0157ac579860563f2b78b0ae7823d24d117'
SQL_BYTES = SQL_PATH.read_bytes()
if hashlib.sha256(SQL_BYTES).hexdigest() != SQL_SHA256:
    raise ValueError('DATA V2.0 migration 的 SQL 版本不匹配')


def entity(name, table, fields, patient=True):
    base = [('id', models.UUIDField(primary_key=True, default=uuid.uuid4, serialize=False))]
    if patient:
        base.append(('patient_id', models.UUIDField()))
    return migrations.CreateModel(name=name, fields=base + fields, options={'db_table': f'sjia"."{table}'})


STATE = [
    entity('Archive', 'archive', [('created_at', models.DateTimeField())]),
    entity('ArchiveBatch', 'archive_batch', [
        ('archive_id', models.UUIDField()), ('submitted_by', models.UUIDField()),
        ('logical_request_id', models.UUIDField()), ('version', models.BigIntegerField()),
        ('received_at', models.DateTimeField()), ('quota_policy_id', models.UUIDField()),
        ('budget_status', models.TextField()), ('status', models.TextField()),
    ]),
    entity('ArchiveDocument', 'archive_document', [
        ('current_version', models.BigIntegerField()), ('created_at', models.DateTimeField()),
    ]),
    entity('ArchiveDocumentVersion', 'archive_document_version', [
        ('document_id', models.UUIDField()), ('version', models.BigIntegerField()),
        ('batch_id', models.UUIDField()), ('document_type', models.TextField()),
        ('organization', models.TextField(null=True)), ('proposed_metadata', models.JSONField(null=True)),
        ('confirmed_text', models.TextField(null=True)), ('grouping_status', models.TextField()),
        ('documented_time_id', models.UUIDField(null=True)), ('available_at', models.DateTimeField()),
        ('confirmed_by', models.UUIDField(null=True)), ('confirmed_at', models.DateTimeField(null=True)),
        ('supersedes_version', models.BigIntegerField(null=True)), ('reason', models.TextField(null=True)),
    ]),
    entity('ArchivePage', 'archive_page', [
        ('batch_id', models.UUIDField()), ('file_page_id', models.UUIDField()), ('file_id', models.UUIDField()),
        ('file_object_version', models.TextField()), ('checksum', Sha256HexField()),
        ('version', models.BigIntegerField()), ('status', models.TextField()),
        ('status_version', models.BigIntegerField()), ('current_task_id', models.UUIDField(null=True)),
        ('available_at', models.DateTimeField()),
    ]),
    entity('ArchiveDocumentPage', 'archive_document_page', [
        ('document_version_id', models.UUIDField()), ('page_id', models.UUIDField()), ('page_order', models.IntegerField()),
    ]),
    entity('ArchivePageAttempt', 'archive_page_attempt', [
        ('page_id', models.UUIDField()), ('page_version', models.BigIntegerField()),
        ('task_id', models.UUIDField()), ('task_attempt_id', models.UUIDField()),
        ('attempt_number', models.IntegerField()), ('input_sha256', Sha256HexField()),
        ('model_version', models.TextField()), ('candidate_set_id', models.UUIDField(null=True)),
        ('provider_request_id', models.TextField(null=True)), ('started_at', models.DateTimeField()),
        ('finished_at', models.DateTimeField(null=True)), ('duration_ms', models.BigIntegerField(null=True)),
        ('cost', NumericField(null=True)), ('currency', models.TextField(null=True)),
        ('usage_evidence', models.JSONField(null=True)), ('status', models.TextField()),
        ('error_code', models.TextField(null=True)),
    ]),
    entity('ArchiveFact', 'archive_fact', [
        ('current_version', models.BigIntegerField()), ('created_at', models.DateTimeField()),
    ]),
    entity('ArchiveFactVersion', 'archive_fact_version', [
        ('fact_id', models.UUIDField()), ('version', models.BigIntegerField()),
        ('record_version_id', models.UUIDField()), ('field_id', models.UUIDField()),
        ('document_version_id', models.UUIDField(null=True)), ('page_id', models.UUIDField(null=True)),
        ('source_anchor_id', models.UUIDField(null=True)), ('context_type', models.TextField()),
        ('assertion_state', models.TextField()), ('episode_id', models.UUIDField(null=True)),
        ('medical_time_id', models.UUIDField(null=True)), ('raw_time_text', models.TextField(null=True)),
        ('time_origin', models.TextField()), ('documented_time_id', models.UUIDField(null=True)),
        ('available_at', models.DateTimeField(null=True)), ('availability_status', models.TextField()),
        ('confirmation_id', models.UUIDField(null=True)), ('created_at', models.DateTimeField()),
        ('supersedes_version', models.BigIntegerField(null=True)), ('reason', models.TextField(null=True)),
    ]),
    entity('ArchiveDocumentEpisode', 'archive_document_episode', [
        ('document_version_id', models.UUIDField()), ('episode_id', models.UUIDField()),
        ('evidence_anchor_id', models.UUIDField()), ('confirmed_by', models.UUIDField()),
        ('confirmed_at', models.DateTimeField()),
    ]),
    entity('ArchiveRelationVersion', 'archive_relation_version', [
        ('relation_id', models.UUIDField()), ('version', models.BigIntegerField()),
        ('left_fact_version_id', models.UUIDField(null=True)), ('right_fact_version_id', models.UUIDField(null=True)),
        ('left_file_id', models.UUIDField(null=True)), ('right_file_id', models.UUIDField(null=True)),
        ('relation_type', models.TextField()), ('status', models.TextField()), ('evidence_ref', models.TextField()),
        ('reason', models.TextField()), ('author_id', models.UUIDField()), ('created_at', models.DateTimeField()),
        ('verification_id', models.UUIDField(null=True)), ('supersedes_version', models.BigIntegerField(null=True)),
    ]),
    entity('PurposeCopy', 'purpose_copy', [
        ('original_file_id', models.UUIDField()), ('derived_file_id', models.UUIDField()),
        ('version', models.BigIntegerField()), ('purpose', models.TextField()), ('environment', models.TextField()),
        ('use_authorization_id', models.UUIDField()), ('processing_version', models.TextField()),
        ('processing_evidence', models.JSONField()), ('date_strategy', models.TextField()),
        ('clinical_integrity_status', models.TextField()), ('status', models.TextField()),
        ('created_by', models.UUIDField()), ('created_at', models.DateTimeField()),
        ('disposition_ref', models.UUIDField(null=True)),
    ]),
    entity('PurposeCopyReview', 'purpose_copy_review', [
        ('copy_id', models.UUIDField()), ('copy_version', models.BigIntegerField()),
        ('reviewer_id', models.UUIDField()), ('reviewed_at', models.DateTimeField()),
        ('evidence_ref', models.TextField()), ('checked_scopes', models.JSONField()),
        ('identity_result', models.TextField()), ('clinical_result', models.TextField()),
        ('original_checksum', Sha256HexField()), ('derived_checksum', Sha256HexField()),
        ('revoked_at', models.DateTimeField(null=True)),
    ]),
    entity('ObservationPeriodVersion', 'observation_period_version', [
        ('period_id', models.UUIDField()), ('version', models.BigIntegerField()), ('owner_id', models.UUIDField()),
        ('start_time_id', models.UUIDField()), ('end_time_id', models.UUIDField()), ('label', models.TextField(null=True)),
        ('label_source', models.TextField()), ('episode_id', models.UUIDField(null=True)),
        ('created_at', models.DateTimeField()), ('supersedes_version', models.BigIntegerField(null=True)),
    ]),
    entity('DeltaSummary', 'delta_summary', [
        ('owner_id', models.UUIDField()), ('batch_id', models.UUIDField()),
        ('baseline_refs', models.JSONField(null=True)), ('current_refs', models.JSONField()),
        ('data_epoch', models.BigIntegerField()), ('authorization_version', models.BigIntegerField()),
        ('visibility_policy_id', models.UUIDField()), ('validity', models.TextField()),
        ('generated_at', models.DateTimeField()), ('task_id', models.UUIDField()),
    ]),
    entity('DeltaSummaryItem', 'delta_summary_item', [
        ('summary_id', models.UUIDField()), ('ordinal', models.IntegerField()),
        ('item_kind', models.TextField()), ('item_source_refs', models.JSONField()),
        ('limitations', models.JSONField()), ('content', models.JSONField()),
    ]),
    entity('EvaluationDatasetVersion', 'evaluation_dataset_version', [
        ('dataset_id', models.UUIDField()), ('version', models.BigIntegerField()),
        ('purpose', models.TextField()), ('environment', models.TextField()),
        ('created_by', models.UUIDField()), ('created_at', models.DateTimeField()),
        ('frozen_at', models.DateTimeField(null=True)), ('protocol_ref', models.TextField()),
        ('coverage', models.JSONField()), ('checksum', Sha256HexField()), ('status', models.TextField()),
    ], patient=False),
    entity('EvaluationPatientPartition', 'evaluation_patient_partition', [
        ('dataset_version_id', models.UUIDField()), ('partition', models.TextField()),
        ('use_authorization_id', models.UUIDField()), ('source_refs', models.JSONField()),
    ]),
    entity('EvaluationLabel', 'evaluation_label', [
        ('partition_id', models.UUIDField()), ('observation_period_id', models.UUIDField()),
        ('label_state', models.TextField()), ('reference_answer_ref', models.TextField(null=True)),
        ('reference_recognition_at', models.DateTimeField(null=True)), ('confirmed_by', models.UUIDField(null=True)),
        ('confirmed_at', models.DateTimeField(null=True)), ('limitations', models.JSONField()),
    ]),
    entity('EvaluationRun', 'evaluation_run', [
        ('dataset_version_id', models.UUIDField()), ('operator_id', models.UUIDField()),
        ('implementation_sha256', Sha256HexField()), ('rules_refs', models.JSONField()),
        ('input_refs', models.JSONField()), ('result_refs', models.JSONField()),
        ('denominators', models.JSONField()), ('limitations', models.JSONField()),
        ('started_at', models.DateTimeField()), ('finished_at', models.DateTimeField(null=True)),
        ('status', models.TextField()), ('mode', models.TextField()),
    ], patient=False),
    entity('MaterialReminder', 'material_reminder', [
        ('owner_id', models.UUIDField()), ('version', models.BigIntegerField()),
        ('target_type', models.TextField()), ('target_ref', models.UUIDField()),
        ('target_version', models.BigIntegerField()), ('schedule_at', models.DateTimeField(null=True)),
        ('timezone_name', models.TextField()), ('quiet_start', models.TimeField(null=True)),
        ('quiet_end', models.TimeField(null=True)), ('status', models.TextField(default='disabled')),
        ('created_at', models.DateTimeField()), ('updated_at', models.DateTimeField()),
    ]),
    entity('MaterialReminderNotice', 'material_reminder_notice', [
        ('reminder_id', models.UUIDField()), ('reminder_version', models.BigIntegerField()),
        ('scheduled_at', models.DateTimeField()), ('channel', models.TextField()),
        ('deduplication_key', models.TextField()), ('status', models.TextField()),
        ('permit_refs', models.JSONField()), ('provider_request_id', models.TextField(null=True)),
        ('receipt_refs', models.JSONField()), ('delivered_at', models.DateTimeField(null=True)),
        ('read_at', models.DateTimeField(null=True)), ('created_at', models.DateTimeField()),
    ]),
]


class Migration(migrations.Migration):
    initial = True
    dependencies = [('core', '0004_case_view'), ('tasks', '0003_task_type_check')]
    operations = [migrations.SeparateDatabaseAndState(
        database_operations=[migrations.RunSQL(SQL_BYTES.decode('utf-8'))],
        state_operations=STATE,
    )]
