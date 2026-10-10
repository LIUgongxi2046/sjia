from django.db import migrations

# 复合外键与 DEFERRABLE 约束：Django ORM 不支持复合外键，集中原生 SQL 建立。
COMPOSITE_FKS_SQL = """
ALTER TABLE sjia.access_grant ADD CONSTRAINT access_grant_relation_fk FOREIGN KEY (relation_id, patient_id) REFERENCES sjia.relation(id, patient_id);
ALTER TABLE sjia.dictionary_alias ADD CONSTRAINT dictionary_alias_entry_fk FOREIGN KEY (entry_id, dictionary_version_id) REFERENCES sjia.dictionary_entry(id, dictionary_version_id);
ALTER TABLE sjia.file_group ADD CONSTRAINT file_group_draft_fk FOREIGN KEY (draft_id, patient_id) REFERENCES sjia.draft(id, patient_id);
ALTER TABLE sjia.file_group ADD CONSTRAINT file_group_use_auth_fk FOREIGN KEY (use_authorization_id, patient_id) REFERENCES sjia.data_use_authorization(id, patient_id);
ALTER TABLE sjia.file_object ADD CONSTRAINT file_object_group_fk FOREIGN KEY (file_group_id, patient_id) REFERENCES sjia.file_group(id, patient_id);
ALTER TABLE sjia.file_object ADD CONSTRAINT file_object_derived_fk FOREIGN KEY (derived_from_id, patient_id) REFERENCES sjia.file_object(id, patient_id);
ALTER TABLE sjia.file_page ADD CONSTRAINT file_page_group_fk FOREIGN KEY (file_group_id, patient_id) REFERENCES sjia.file_group(id, patient_id);
ALTER TABLE sjia.file_page ADD CONSTRAINT file_page_file_fk FOREIGN KEY (file_id, patient_id) REFERENCES sjia.file_object(id, patient_id);
ALTER TABLE sjia.candidate_set ADD CONSTRAINT candidate_set_group_fk FOREIGN KEY (file_group_id, patient_id) REFERENCES sjia.file_group(id, patient_id);
ALTER TABLE sjia.candidate_set ADD CONSTRAINT candidate_set_raw_file_fk FOREIGN KEY (raw_response_file_id, patient_id) REFERENCES sjia.file_object(id, patient_id);
ALTER TABLE sjia.source_anchor ADD CONSTRAINT source_anchor_page_fk FOREIGN KEY (page_id, patient_id) REFERENCES sjia.file_page(id, patient_id);
ALTER TABLE sjia.candidate_field ADD CONSTRAINT candidate_field_set_fk FOREIGN KEY (candidate_set_id, patient_id) REFERENCES sjia.candidate_set(id, patient_id);
ALTER TABLE sjia.candidate_field ADD CONSTRAINT candidate_field_anchor_fk FOREIGN KEY (anchor_id, patient_id) REFERENCES sjia.source_anchor(id, patient_id);
ALTER TABLE sjia.candidate_confirmation ADD CONSTRAINT candidate_confirmation_field_fk FOREIGN KEY (candidate_field_id, patient_id) REFERENCES sjia.candidate_field(id, patient_id);
ALTER TABLE sjia.report ADD CONSTRAINT report_sample_time_fk FOREIGN KEY (sample_time_id, patient_id) REFERENCES sjia.medical_time(id, patient_id);
ALTER TABLE sjia.report ADD CONSTRAINT report_result_time_fk FOREIGN KEY (result_time_id, patient_id) REFERENCES sjia.medical_time(id, patient_id);
ALTER TABLE sjia.report ADD CONSTRAINT report_file_group_fk FOREIGN KEY (file_group_id, patient_id) REFERENCES sjia.file_group(id, patient_id);
ALTER TABLE sjia.report ADD CONSTRAINT report_replaces_fk FOREIGN KEY (replaces_report_id, patient_id) REFERENCES sjia.report(id, patient_id);
ALTER TABLE sjia.record_version ADD CONSTRAINT record_version_record_fk FOREIGN KEY (record_id, patient_id) REFERENCES sjia.record(id, patient_id);
ALTER TABLE sjia.record_version ADD CONSTRAINT record_version_supersedes_fk FOREIGN KEY (record_id, patient_id, supersedes_version) REFERENCES sjia.record_version(record_id, patient_id, version);
ALTER TABLE sjia.record_version ADD CONSTRAINT record_version_report_fk FOREIGN KEY (report_id, patient_id) REFERENCES sjia.report(id, patient_id);
ALTER TABLE sjia.record_version ADD CONSTRAINT record_version_use_auth_fk FOREIGN KEY (use_authorization_id, patient_id) REFERENCES sjia.data_use_authorization(id, patient_id);
ALTER TABLE sjia.record_attachment ADD CONSTRAINT record_attachment_version_fk FOREIGN KEY (record_version_id, patient_id) REFERENCES sjia.record_version(id, patient_id);
ALTER TABLE sjia.record_attachment ADD CONSTRAINT record_attachment_file_fk FOREIGN KEY (file_id, patient_id) REFERENCES sjia.file_object(id, patient_id);
ALTER TABLE sjia.record_field ADD CONSTRAINT record_field_version_fk FOREIGN KEY (record_version_id, patient_id) REFERENCES sjia.record_version(id, patient_id);
ALTER TABLE sjia.record_field ADD CONSTRAINT record_field_medical_time_fk FOREIGN KEY (medical_time_id, patient_id) REFERENCES sjia.medical_time(id, patient_id);
ALTER TABLE sjia.record_field ADD CONSTRAINT record_field_anchor_fk FOREIGN KEY (anchor_id, patient_id) REFERENCES sjia.source_anchor(id, patient_id);
ALTER TABLE sjia.record_field ADD CONSTRAINT record_field_confirmation_fk FOREIGN KEY (confirmation_id, patient_id) REFERENCES sjia.candidate_confirmation(id, patient_id);
ALTER TABLE sjia.verification ADD CONSTRAINT verification_version_fk FOREIGN KEY (record_version_id, patient_id) REFERENCES sjia.record_version(id, patient_id);
ALTER TABLE sjia.verification_field ADD CONSTRAINT verification_field_verification_fk FOREIGN KEY (verification_id, record_version_id, patient_id) REFERENCES sjia.verification(id, record_version_id, patient_id);
ALTER TABLE sjia.verification_field ADD CONSTRAINT verification_field_field_fk FOREIGN KEY (field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.verification_request ADD CONSTRAINT verification_request_version_fk FOREIGN KEY (record_version_id, patient_id) REFERENCES sjia.record_version(id, patient_id);
ALTER TABLE sjia.field_verification_state ADD CONSTRAINT fvs_field_fk FOREIGN KEY (field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.field_verification_state ADD CONSTRAINT fvs_request_fk FOREIGN KEY (request_id, record_version_id, patient_id) REFERENCES sjia.verification_request(id, record_version_id, patient_id);
ALTER TABLE sjia.field_verification_state ADD CONSTRAINT fvs_current_verification_fk FOREIGN KEY (current_verification_id, record_version_id, patient_id) REFERENCES sjia.verification(id, record_version_id, patient_id);
ALTER TABLE sjia.field_verification_state ADD CONSTRAINT fvs_current_verification_field_fk FOREIGN KEY (current_verification_id, field_id) REFERENCES sjia.verification_field(verification_id, field_id);
ALTER TABLE sjia.normalized_measurement ADD CONSTRAINT normalized_measurement_field_fk FOREIGN KEY (field_id, patient_id) REFERENCES sjia.record_field(id, patient_id);
ALTER TABLE sjia.assessment_input ADD CONSTRAINT assessment_input_assessment_fk FOREIGN KEY (assessment_id, patient_id) REFERENCES sjia.assessment(id, patient_id);
ALTER TABLE sjia.assessment_input ADD CONSTRAINT assessment_input_field_fk FOREIGN KEY (field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.assessment_input ADD CONSTRAINT assessment_input_verification_fk FOREIGN KEY (verification_id, record_version_id, patient_id) REFERENCES sjia.verification(id, record_version_id, patient_id);
ALTER TABLE sjia.assessment_input ADD CONSTRAINT assessment_input_verification_field_fk FOREIGN KEY (verification_id, field_id) REFERENCES sjia.verification_field(verification_id, field_id);
ALTER TABLE sjia.assessment_input ADD CONSTRAINT assessment_input_normalization_fk FOREIGN KEY (normalization_id, field_id, patient_id) REFERENCES sjia.normalized_measurement(id, field_id, patient_id);
ALTER TABLE sjia.alert_evaluation ADD CONSTRAINT alert_evaluation_use_auth_fk FOREIGN KEY (use_authorization_id, patient_id) REFERENCES sjia.data_use_authorization(id, patient_id);
ALTER TABLE sjia.alert_evaluation_input ADD CONSTRAINT evaluation_input_evaluation_fk FOREIGN KEY (evaluation_id, patient_id) REFERENCES sjia.alert_evaluation(id, patient_id);
ALTER TABLE sjia.alert_evaluation_input ADD CONSTRAINT evaluation_input_field_fk FOREIGN KEY (field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.alert_evaluation_input ADD CONSTRAINT evaluation_input_verification_fk FOREIGN KEY (verification_id, record_version_id, patient_id) REFERENCES sjia.verification(id, record_version_id, patient_id);
ALTER TABLE sjia.alert_evaluation_input ADD CONSTRAINT evaluation_input_verification_field_fk FOREIGN KEY (verification_id, field_id) REFERENCES sjia.verification_field(verification_id, field_id);
ALTER TABLE sjia.alert_evaluation_input ADD CONSTRAINT evaluation_input_normalization_fk FOREIGN KEY (normalization_id, field_id, patient_id) REFERENCES sjia.normalized_measurement(id, field_id, patient_id);
ALTER TABLE sjia.alert_evaluation_input ADD CONSTRAINT evaluation_input_medical_time_fk FOREIGN KEY (medical_time_id, patient_id) REFERENCES sjia.medical_time(id, patient_id);
ALTER TABLE sjia.alert_evaluation_assessment ADD CONSTRAINT evaluation_assessment_evaluation_fk FOREIGN KEY (evaluation_id, patient_id) REFERENCES sjia.alert_evaluation(id, patient_id);
ALTER TABLE sjia.alert_evaluation_assessment ADD CONSTRAINT evaluation_assessment_assessment_fk FOREIGN KEY (assessment_id, patient_id) REFERENCES sjia.assessment(id, patient_id);
ALTER TABLE sjia.alert_signal_result ADD CONSTRAINT signal_result_evaluation_fk FOREIGN KEY (evaluation_id, patient_id) REFERENCES sjia.alert_evaluation(id, patient_id);
ALTER TABLE sjia.alert_episode ADD CONSTRAINT alert_episode_last_evaluation_fk FOREIGN KEY (last_evaluation_id, patient_id) REFERENCES sjia.alert_evaluation(id, patient_id);
ALTER TABLE sjia.alert_episode ADD CONSTRAINT alert_episode_reset_evaluation_fk FOREIGN KEY (reset_evaluation_id, patient_id) REFERENCES sjia.alert_evaluation(id, patient_id);
ALTER TABLE sjia.alert_decision ADD CONSTRAINT alert_decision_evaluation_fk FOREIGN KEY (evaluation_id, patient_id) REFERENCES sjia.alert_evaluation(id, patient_id);
ALTER TABLE sjia.alert_decision ADD CONSTRAINT alert_decision_episode_fk FOREIGN KEY (episode_id, patient_id) REFERENCES sjia.alert_episode(id, patient_id);
ALTER TABLE sjia.alert_decision ADD CONSTRAINT alert_decision_previous_fk FOREIGN KEY (previous_decision_id, patient_id, rule_key) REFERENCES sjia.alert_decision(id, patient_id, rule_key);
ALTER TABLE sjia.alert_event ADD CONSTRAINT alert_event_evaluation_fk FOREIGN KEY (evaluation_id, patient_id) REFERENCES sjia.alert_evaluation(id, patient_id);
ALTER TABLE sjia.alert_recipient ADD CONSTRAINT alert_recipient_alert_fk FOREIGN KEY (alert_id, patient_id) REFERENCES sjia.alert_event(id, patient_id);
ALTER TABLE sjia.alert_recipient ADD CONSTRAINT alert_recipient_relation_fk FOREIGN KEY (relation_id, patient_id) REFERENCES sjia.relation(id, patient_id);
ALTER TABLE sjia.notification_attempt ADD CONSTRAINT notification_attempt_recipient_fk FOREIGN KEY (recipient_id, patient_id) REFERENCES sjia.alert_recipient(id, patient_id);
ALTER TABLE sjia.summary_item ADD CONSTRAINT summary_item_summary_fk FOREIGN KEY (summary_id, patient_id) REFERENCES sjia.summary(id, patient_id);
ALTER TABLE sjia.summary_item ADD CONSTRAINT summary_item_field_fk FOREIGN KEY (field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.record ADD CONSTRAINT record_current_version_fk FOREIGN KEY (id, patient_id, current_version) REFERENCES sjia.record_version(record_id, patient_id, version) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE sjia.access_grant ADD CONSTRAINT grant_record_fk FOREIGN KEY (assigned_record_id, patient_id) REFERENCES sjia.record(id, patient_id);
ALTER TABLE sjia.draft ADD CONSTRAINT draft_correction_fk FOREIGN KEY (correction_record_id, patient_id, correction_version) REFERENCES sjia.record_version(record_id, patient_id, version);
ALTER TABLE sjia.candidate_set ADD CONSTRAINT candidate_task_fk FOREIGN KEY (task_id, patient_id) REFERENCES sjia.task(id, patient_id);
ALTER TABLE sjia.assessment ADD CONSTRAINT assessment_task_fk FOREIGN KEY (task_id, patient_id) REFERENCES sjia.task(id, patient_id);
ALTER TABLE sjia.alert_evaluation ADD CONSTRAINT evaluation_task_fk FOREIGN KEY (task_id, patient_id) REFERENCES sjia.task(id, patient_id);
ALTER TABLE sjia.summary ADD CONSTRAINT summary_task_fk FOREIGN KEY (task_id, patient_id) REFERENCES sjia.task(id, patient_id);
ALTER TABLE sjia.alert_episode ADD CONSTRAINT episode_event_fk FOREIGN KEY (last_event_id, patient_id) REFERENCES sjia.alert_event(id, patient_id);
ALTER TABLE sjia.patient_profile_version ADD CONSTRAINT patient_profile_source_fk FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.patient_profile_version ADD CONSTRAINT patient_profile_age_time_fk FOREIGN KEY (age_reference_time_id, patient_id) REFERENCES sjia.medical_time(id, patient_id);
ALTER TABLE sjia.patient ADD CONSTRAINT patient_current_profile_fk FOREIGN KEY (current_profile_id, id) REFERENCES sjia.patient_profile_version(id, patient_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE sjia.clinical_episode ADD CONSTRAINT clinical_episode_source_fk FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.clinical_episode ADD CONSTRAINT clinical_episode_start_fk FOREIGN KEY (start_time_id, patient_id) REFERENCES sjia.medical_time(id, patient_id);
ALTER TABLE sjia.clinical_episode ADD CONSTRAINT clinical_episode_end_fk FOREIGN KEY (end_time_id, patient_id) REFERENCES sjia.medical_time(id, patient_id);
ALTER TABLE sjia.record_field_group ADD CONSTRAINT record_field_group_source_fk FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.record_field_group ADD CONSTRAINT record_field_group_time_fk FOREIGN KEY (medical_time_id, patient_id) REFERENCES sjia.medical_time(id, patient_id);
ALTER TABLE sjia.record_field_group_member ADD CONSTRAINT group_member_group_fk FOREIGN KEY (group_id, record_version_id, patient_id) REFERENCES sjia.record_field_group(id, record_version_id, patient_id);
ALTER TABLE sjia.record_field_group_member ADD CONSTRAINT group_member_field_fk FOREIGN KEY (field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.clinical_statement ADD CONSTRAINT clinical_statement_source_fk FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.clinical_statement ADD CONSTRAINT clinical_statement_time_fk FOREIGN KEY (medical_time_id, patient_id) REFERENCES sjia.medical_time(id, patient_id);
ALTER TABLE sjia.statement_evidence ADD CONSTRAINT statement_evidence_statement_fk FOREIGN KEY (statement_id, patient_id) REFERENCES sjia.clinical_statement(id, patient_id);
ALTER TABLE sjia.statement_evidence ADD CONSTRAINT statement_evidence_field_fk FOREIGN KEY (evidence_field_id, evidence_record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.imaging_study ADD CONSTRAINT imaging_study_source_fk FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.imaging_study ADD CONSTRAINT imaging_study_report_fk FOREIGN KEY (report_id, patient_id) REFERENCES sjia.report(id, patient_id);
ALTER TABLE sjia.imaging_study ADD CONSTRAINT imaging_study_time_fk FOREIGN KEY (study_time_id, patient_id) REFERENCES sjia.medical_time(id, patient_id);
ALTER TABLE sjia.imaging_study_site ADD CONSTRAINT imaging_study_site_study_fk FOREIGN KEY (study_id, patient_id) REFERENCES sjia.imaging_study(id, patient_id);
ALTER TABLE sjia.imaging_asset_link ADD CONSTRAINT imaging_asset_source_fk FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.imaging_asset_link ADD CONSTRAINT imaging_asset_file_fk FOREIGN KEY (file_id, patient_id) REFERENCES sjia.file_object(id, patient_id);
ALTER TABLE sjia.imaging_asset_link ADD CONSTRAINT imaging_asset_study_fk FOREIGN KEY (study_id, patient_id) REFERENCES sjia.imaging_study(id, patient_id);
ALTER TABLE sjia.imaging_asset_link ADD CONSTRAINT imaging_asset_evidence_fk FOREIGN KEY (evidence_field_id, evidence_record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.medication_event ADD CONSTRAINT medication_event_source_fk FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.medication_event ADD CONSTRAINT medication_event_time_fk FOREIGN KEY (medical_time_id, patient_id) REFERENCES sjia.medical_time(id, patient_id);
ALTER TABLE sjia.medication_event ADD CONSTRAINT medication_event_previous_fk FOREIGN KEY (previous_event_id, patient_id) REFERENCES sjia.medication_event(id, patient_id);
ALTER TABLE sjia.medication_event ADD CONSTRAINT medication_event_reason_fk FOREIGN KEY (reason_statement_id, patient_id) REFERENCES sjia.clinical_statement(id, patient_id);
ALTER TABLE sjia.medication_event_item ADD CONSTRAINT medication_item_event_fk FOREIGN KEY (event_id, record_version_id, patient_id) REFERENCES sjia.medication_event(id, record_version_id, patient_id);
ALTER TABLE sjia.medication_event_item ADD CONSTRAINT medication_item_source_fk FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.procedure_event ADD CONSTRAINT procedure_event_source_fk FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.procedure_event ADD CONSTRAINT procedure_event_time_fk FOREIGN KEY (medical_time_id, patient_id) REFERENCES sjia.medical_time(id, patient_id);
ALTER TABLE sjia.procedure_event ADD CONSTRAINT procedure_event_medication_fk FOREIGN KEY (related_medication_event_id, patient_id) REFERENCES sjia.medication_event(id, patient_id);
ALTER TABLE sjia.external_score_observation ADD CONSTRAINT external_score_source_fk FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.external_score_observation ADD CONSTRAINT external_score_total_fk FOREIGN KEY (total_field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.external_score_observation ADD CONSTRAINT external_score_time_fk FOREIGN KEY (medical_time_id, patient_id) REFERENCES sjia.medical_time(id, patient_id);
ALTER TABLE sjia.external_score_component ADD CONSTRAINT external_score_component_score_fk FOREIGN KEY (score_id, record_version_id, patient_id) REFERENCES sjia.external_score_observation(id, record_version_id, patient_id);
ALTER TABLE sjia.external_score_component ADD CONSTRAINT external_score_component_field_fk FOREIGN KEY (field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.followup_encounter ADD CONSTRAINT followup_encounter_source_fk FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.followup_encounter ADD CONSTRAINT followup_encounter_time_fk FOREIGN KEY (medical_time_id, patient_id) REFERENCES sjia.medical_time(id, patient_id);
ALTER TABLE sjia.followup_reference ADD CONSTRAINT followup_reference_encounter_fk FOREIGN KEY (encounter_id, patient_id) REFERENCES sjia.followup_encounter(id, patient_id);
ALTER TABLE sjia.followup_reference ADD CONSTRAINT followup_reference_statement_fk FOREIGN KEY (statement_id, patient_id) REFERENCES sjia.clinical_statement(id, patient_id);
ALTER TABLE sjia.followup_reference ADD CONSTRAINT followup_reference_group_fk FOREIGN KEY (group_id, patient_id) REFERENCES sjia.record_field_group(id, patient_id);
ALTER TABLE sjia.followup_reference ADD CONSTRAINT followup_reference_score_fk FOREIGN KEY (score_id, patient_id) REFERENCES sjia.external_score_observation(id, patient_id);
ALTER TABLE sjia.followup_reference ADD CONSTRAINT followup_reference_medication_fk FOREIGN KEY (medication_event_id, patient_id) REFERENCES sjia.medication_event(id, patient_id);
ALTER TABLE sjia.data_quality_issue ADD CONSTRAINT dq_issue_source_fk FOREIGN KEY (source_field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.data_quality_issue ADD CONSTRAINT dq_issue_resolution_fk FOREIGN KEY (resolution_field_id, resolution_record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.data_quality_issue_reference ADD CONSTRAINT dq_reference_issue_fk FOREIGN KEY (issue_id, patient_id) REFERENCES sjia.data_quality_issue(id, patient_id);
ALTER TABLE sjia.data_quality_issue_reference ADD CONSTRAINT dq_reference_field_fk FOREIGN KEY (field_id, record_version_id, patient_id) REFERENCES sjia.record_field(id, record_version_id, patient_id);
ALTER TABLE sjia.data_quality_issue_reference ADD CONSTRAINT dq_reference_file_fk FOREIGN KEY (file_id, patient_id) REFERENCES sjia.file_object(id, patient_id);
ALTER TABLE sjia.upload_part ADD CONSTRAINT upload_part_group_fk FOREIGN KEY (file_group_id, patient_id) REFERENCES sjia.file_group(id, patient_id);
ALTER TABLE sjia.upload_part ADD CONSTRAINT upload_part_file_fk FOREIGN KEY (file_id, patient_id) REFERENCES sjia.file_object(id, patient_id);
ALTER TABLE sjia.correction_request ADD CONSTRAINT correction_request_version_fk FOREIGN KEY (record_version_id, patient_id) REFERENCES sjia.record_version(id, patient_id);
ALTER TABLE sjia.correction_request ADD CONSTRAINT correction_request_resolution_fk FOREIGN KEY (resolution_record_version_id, patient_id) REFERENCES sjia.record_version(id, patient_id);
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_schema_and_domains"),
        ("identity", "0001_initial"),
        ("retention", "0001_initial"),
        ("sources", "0001_initial"),
        ("configurations", "0002_initial"),
        ("dictionaries", "0002_initial"),
        ("relations", "0001_initial"),
        ("authorization", "0002_initial"),
        ("requests", "0002_initial"),
        ("drafts", "0002_initial"),
        ("medical_time", "0001_initial"),
        ("family_content", "0001_initial"),
        ("contacts", "0002_initial"),
        ("tasks", "0001_initial"),
        ("dependencies", "0001_initial"),
        ("audit", "0002_initial"),
        ("records", "0003_initial"),
        ("files", "0002_initial"),
        ("recognition", "0001_initial"),
        ("verification", "0001_initial"),
        ("assessment", "0002_initial"),
        ("alerting", "0003_initial"),
        ("notifications", "0001_initial"),
        ("summaries", "0001_initial"),
        ("case_details", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(COMPOSITE_FKS_SQL, reverse_sql=migrations.RunSQL.noop),
    ]
