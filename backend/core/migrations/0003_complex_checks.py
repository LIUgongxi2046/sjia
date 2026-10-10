from django.db import migrations

# 关键跨字段与 PostgreSQL 专属函数 CHECK 约束。
COMPLEX_CHECKS_SQL = """
ALTER TABLE sjia.retention_policy ADD CONSTRAINT retention_policy_rules_jsonb CHECK (jsonb_typeof(rules) = 'object');
ALTER TABLE sjia.source_revision ADD CONSTRAINT source_revision_allowed_use_jsonb CHECK (jsonb_typeof(allowed_use) = 'array');
ALTER TABLE sjia.config_version ADD CONSTRAINT config_version_payload_jsonb CHECK (jsonb_typeof(payload) = 'object');
ALTER TABLE sjia.config_approval ADD CONSTRAINT config_approval_scope_jsonb CHECK (jsonb_typeof(effective_scope) = 'object');
ALTER TABLE sjia.record_version ADD CONSTRAINT record_version_quality_jsonb CHECK (jsonb_typeof(quality_reasons) = 'array');
ALTER TABLE sjia.record_field ADD CONSTRAINT record_field_quality_jsonb CHECK (jsonb_typeof(quality_reasons) = 'array');
ALTER TABLE sjia.assessment ADD CONSTRAINT assessment_missing_jsonb CHECK (jsonb_typeof(missing_items) = 'array');
ALTER TABLE sjia.alert_evaluation ADD CONSTRAINT alert_evaluation_missing_jsonb CHECK (jsonb_typeof(missing_items) = 'array');
ALTER TABLE sjia.alert_event ADD CONSTRAINT alert_event_snapshot_jsonb CHECK (jsonb_typeof(input_snapshot) = 'object');
ALTER TABLE sjia.summary_item ADD CONSTRAINT summary_item_snapshot_jsonb CHECK (jsonb_typeof(snapshot) = 'object');
ALTER TABLE sjia.task ADD CONSTRAINT task_input_refs_jsonb CHECK (jsonb_typeof(input_refs) = 'object');
ALTER TABLE sjia.outbox_event ADD CONSTRAINT outbox_resource_refs_jsonb CHECK (jsonb_typeof(resource_refs) = 'object');
ALTER TABLE sjia.disposition_request ADD CONSTRAINT disposition_scope_jsonb CHECK (jsonb_typeof(scope) = 'object');
ALTER TABLE sjia.assessment_input ADD CONSTRAINT assessment_input_snapshot_jsonb CHECK (jsonb_typeof(input_snapshot) = 'object');
ALTER TABLE sjia.alert_signal_result ADD CONSTRAINT signal_result_pair_jsonb CHECK (jsonb_typeof(pair_results) = 'array');
ALTER TABLE sjia.candidate_field ADD CONSTRAINT candidate_field_proposed_jsonb CHECK (jsonb_typeof(proposed_field) = 'object');
ALTER TABLE sjia.draft ADD CONSTRAINT draft_payload_jsonb CHECK (jsonb_typeof(payload) = 'object');
ALTER TABLE sjia.medical_time ADD CONSTRAINT medical_time_partial_shape CHECK (
  (precision = 'year' AND local_year IS NOT NULL AND local_month IS NULL AND end_year IS NULL AND end_month IS NULL AND relative_text IS NULL)
  OR (precision = 'month' AND local_year IS NOT NULL AND local_month IS NOT NULL AND end_year IS NULL AND end_month IS NULL AND relative_text IS NULL)
  OR (precision = 'month_range' AND local_year IS NOT NULL AND local_month IS NOT NULL AND end_year IS NOT NULL AND end_month IS NOT NULL AND relative_text IS NULL AND (local_year, local_month) <= (end_year, end_month))
  OR (precision = 'relative' AND relative_text IS NOT NULL AND length(trim(relative_text)) > 0 AND local_year IS NULL AND local_month IS NULL AND end_year IS NULL AND end_month IS NULL)
  OR (precision IN ('unknown','date','minute','second') AND local_year IS NULL AND local_month IS NULL AND end_year IS NULL AND end_month IS NULL AND relative_text IS NULL)
);
ALTER TABLE sjia.record_version ADD CONSTRAINT record_version_correction CHECK (
  (version = 1 AND supersedes_version IS NULL AND correction_reason IS NULL AND corrected_at IS NULL)
  OR (version > 1 AND supersedes_version IS NOT NULL AND supersedes_version = version - 1 AND correction_reason IS NOT NULL AND corrected_at IS NOT NULL)
);
ALTER TABLE sjia.draft ADD CONSTRAINT draft_correction_shape CHECK (
  (form_type = 'correction' AND correction_record_id IS NOT NULL AND correction_version IS NOT NULL)
  OR (form_type <> 'correction' AND correction_record_id IS NULL AND correction_version IS NULL)
);
ALTER TABLE sjia.write_intent ADD CONSTRAINT write_intent_status_shape CHECK (
  (status = 'prepared' AND completed_at IS NULL AND write_request_id IS NULL AND error_code IS NULL)
  OR (status = 'committed' AND completed_at IS NOT NULL AND write_request_id IS NOT NULL AND error_code IS NULL)
  OR (status = 'failed' AND completed_at IS NOT NULL AND write_request_id IS NULL AND error_code IS NOT NULL)
);
ALTER TABLE sjia.disposition_item ADD CONSTRAINT disposition_retained CHECK (status <> 'retained' OR (retention_evidence_ref IS NOT NULL AND recheck_at IS NOT NULL));
ALTER TABLE sjia.disposition_item ADD CONSTRAINT disposition_deleted CHECK (status <> 'deleted' OR (executed_at IS NOT NULL AND receipt_ref IS NOT NULL));
ALTER TABLE sjia.alert_decision ADD CONSTRAINT decision_sequence_shape CHECK ((sequence = 1) = (previous_decision_id IS NULL));
ALTER TABLE sjia.alert_decision ADD CONSTRAINT decision_action_state CHECK (
  (action NOT IN ('create_event','continue_episode') OR (state = 'met' AND episode_id IS NOT NULL))
  AND (action <> 'close_episode' OR (state = 'not_met' AND episode_id IS NOT NULL))
  AND (action <> 'blocked' OR cardinality(reason_codes) > 0)
);
ALTER TABLE sjia.alert_signal_result ADD CONSTRAINT signal_result_unknown_reason CHECK (state <> 'unknown' OR cardinality(reason_codes) > 0);
ALTER TABLE sjia.medication_event_item ADD CONSTRAINT medication_item_dose_unit CHECK ((dose IS NULL) = (dose_unit IS NULL));
ALTER TABLE sjia.medication_event_item ADD CONSTRAINT medication_item_interval CHECK ((interval_value IS NULL) = (interval_unit IS NULL));
ALTER TABLE sjia.imaging_asset_link ADD CONSTRAINT imaging_asset_evidence_shape CHECK ((evidence_field_id IS NULL) = (evidence_record_version_id IS NULL));
ALTER TABLE sjia.data_quality_issue ADD CONSTRAINT dq_issue_resolution_shape CHECK ((resolution_field_id IS NULL) = (resolution_record_version_id IS NULL));
ALTER TABLE sjia.data_quality_issue_reference ADD CONSTRAINT dq_reference_field_shape CHECK ((field_id IS NULL) = (record_version_id IS NULL));
ALTER TABLE sjia.followup_reference ADD CONSTRAINT followup_reference_exactly_one CHECK (num_nonnulls(statement_id, group_id, score_id, medication_event_id) = 1);
ALTER TABLE sjia.data_quality_issue_reference ADD CONSTRAINT dq_reference_exactly_one CHECK (num_nonnulls(field_id, file_id) = 1);
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_composite_constraints"),
    ]

    operations = [
        migrations.RunSQL(COMPLEX_CHECKS_SQL, reverse_sql=migrations.RunSQL.noop),
    ]
