from django.urls import path
from api.views_document_inputs import archive_document_inputs

from api import views_purpose as purpose
from api import views_family3 as family3
from api import views_disposition as disposition
from api import views_family_dispositions as family_dispositions
from api import views_archive_facts as facts
from api import views_fact_writes as fact_writes
from api import views_archive_documents as documents
from api import views_archive_regrouping as regrouping
from api import views_archive_relations as archive_relations
from api import views_archive_search as archive_search
from api import views_archive_delta as archive_delta
from api import views_observation_periods as observation_periods
from api import views_archive_writes as document_writes
from api import views_archive_batches as batches
from api import views_archive_batch_writes as batch_writes
from api import views_archive_page_actions as page_actions
from api import views_evaluation_datasets as datasets
from api import views_evaluation_runs as evaluation_runs
from api import views_assessments as assessments
from api import views_dynamic as dynamic
from api import views_publication as publication
from api import views_notifications as notifications
from api import views_verifications as verifications
from api import views_v2_requests as requests
from api import views_support2 as support2
from api import views_reminders as reminders
from api import views_reminder_writes as reminder_writes
from api import views_personal_record_tasks as personal_tasks
from api import views_material_notices as material_notices
from api import views_voice_inputs as voice_inputs
from api.views_support_session import support_login, support_session_read


from api import views_content_disposition as content_disposition
from api import views_family_content_disposition as family_content_disposition

urlpatterns = [
    path('patients/<uuid:patient_id>/content-disposition/options', family_content_disposition.family_content_disposition_options),
    path('patients/<uuid:patient_id>/content-disposition/requests', family_content_disposition.family_content_disposition_requests),
    path('content-disposition/requests/<uuid:request_id>', family_content_disposition.family_content_disposition_read),
    path('support/patients/<uuid:patient_id>/content-disposition/requests', content_disposition.content_disposition_family_requests),
    path('support/content-disposition/patients', content_disposition.content_disposition_patients),
    path('support/patients/<uuid:patient_id>/content-disposition/options', content_disposition.content_disposition_options),
    path('support/patients/<uuid:patient_id>/content-disposition/history', content_disposition.content_disposition_history),
    path('support/content-disposition/plans/<uuid:plan_id>/review', content_disposition.content_disposition_review),
    path('support/content-disposition/catalog', content_disposition.content_disposition_catalog),
    path('support/patients/<uuid:patient_id>/content-disposition/plans', content_disposition.content_disposition_preview),
    path('support/content-disposition/plans/<uuid:plan_id>', content_disposition.content_disposition_read),
    path('support/content-disposition/plans/<uuid:plan_id>/approve', content_disposition.content_disposition_approve),
    path('support/content-disposition/plans/<uuid:plan_id>/execute', content_disposition.content_disposition_execute),
    path('patients/<uuid:patient_id>/archive-document-regroupings', regrouping.archive_documents_regroup, name='archive_documents_regroup'),
    path('archive-batches/<uuid:batch_id>/regroup-options', regrouping.archive_regroup_options, name='archive_regroup_options'),
    path('patients/<uuid:patient_id>/voice-inputs', voice_inputs.voice_inputs),
    path('patients/<uuid:patient_id>/voice-input-options', voice_inputs.voice_input_options),
    path('patients/<uuid:patient_id>/voice-inputs/<uuid:voice_id>', voice_inputs.voice_input_read),
    path('patients/<uuid:patient_id>/voice-inputs/<uuid:voice_id>/process', voice_inputs.voice_input_process),
    path('patients/<uuid:patient_id>/voice-inputs/<uuid:voice_id>/adopt', voice_inputs.voice_input_adopt),
    path('patients/<uuid:patient_id>/voice-inputs/<uuid:voice_id>/cancel', voice_inputs.voice_input_cancel),
    path('patients/<uuid:patient_id>/archive-upload-options', family3.archive_upload_options, name='archive_upload_options'),
    path('patients/<uuid:patient_id>/observation-period', observation_periods.observation_period_read, name='observation_period_read'),
    path('patients/<uuid:patient_id>/observation-period/versions', observation_periods.observation_period_change, name='observation_period_change'),
    path('observation-period-versions/<uuid:version_id>', observation_periods.observation_period_version_read, name='observation_period_version_read'),
    path('archive-document-versions/<uuid:version_id>/source', archive_delta.archive_document_version_source, name='archive_document_version_source'),
    path('archive-batches/<uuid:batch_id>/delta-summaries', archive_delta.archive_delta_create, name='archive_delta_create'),
    path('archive-delta-summaries/<uuid:delta_id>', archive_delta.archive_delta_read, name='archive_delta_read'),
    path('archive-delta-tasks/<uuid:task_id>', archive_delta.archive_delta_task, name='archive_delta_task'),
    path('patients/<uuid:patient_id>/archives/search', archive_search.archive_directory_search, name='archive_search'),
    path('patients/<uuid:patient_id>/archive-relations/search', archive_relations.archive_relation_search, name='archive_relation_search'),
    path('patients/<uuid:patient_id>/archive-relations', archive_relations.archive_relation_create, name='archive_relation_create'),
    path('archive-relations/<uuid:relation_id>', archive_relations.archive_relation_read, name='archive_relation_read'),
    path('archive-relations/<uuid:relation_id>/relationships', archive_relations.archive_relation_update, name='archive_relation_update'),
    path('support/archive-relations/<uuid:relation_id>/verification', archive_relations.archive_relation_verify, name='archive_relation_verify'),
    path('support/notification-scopes', notifications.notification_case_directory),
    path('support/notification-intents/<uuid:intent_id>/cancel', requests.v2_notification_intent_cancel),
    path('patients/<uuid:patient_id>/notification-records', notifications.family_notification_history),
    path('support/patients/<uuid:patient_id>/notification-records', notifications.support_notification_history),
    path('notification-records/<uuid:recipient_id>', notifications.family_notification_record),
    path('support/notification-records/<uuid:recipient_id>', notifications.support_notification_record),
    path('support/notification-records/<uuid:recipient_id>/send', notifications.notification_send, name='notification_v2_send'),
    path('notification-records/<uuid:recipient_id>/message', notifications.family_notification_message),
    path('notification-records/<uuid:recipient_id>/delivered', notifications.family_notification_delivered),
    path('alerts/<uuid:alert_id>/notification', notifications.family_notification_alert),
    path('support/publication-directory', publication.publication_directory_read, name='publication_directory'),
    path('support/publication-configs/<uuid:config_id>', publication.publication_config_read, name='publication_config_read'),
    path('support/publication-contacts/<uuid:contact_id>', publication.publication_contact_read, name='publication_contact_read'),
    path('support/publication-releases/<uuid:release_id>', publication.publication_release_read, name='publication_release_read'),
    path('support/publication-approvals/<uuid:approval_id>', publication.publication_approval_read, name='publication_approval_read'),
    path('support/patients/<uuid:patient_id>/alert-evaluation-options', dynamic.dynamic_options_read, name='alert_evaluation_options'),
    path('support/patients/<uuid:patient_id>/alert-evaluation-triggers', dynamic.dynamic_triggers_read, name='alert_evaluation_triggers'),
    path('support/patients/<uuid:patient_id>/alert-evaluations', dynamic.dynamic_create, name='alert_evaluation_v2_create'),
    path('support/alert-evaluations/<uuid:evaluation_id>', dynamic.dynamic_read, name='alert_evaluation_v2_read'),
    path('support/alert-evaluation-tasks/<uuid:task_id>', dynamic.dynamic_task_read, name='alert_evaluation_task_read'),
    path('support/clinical-scopes', assessments.clinical_directory_read, name='clinical_directory_read'),
    path('support/patients/<uuid:patient_id>/assessment-options', assessments.assessment_creation_options_read, name='assessment_creation_options'),
    path('support/verification-resources/<str:resource_kind>/<uuid:resource_id>/originals', verifications.verification_originals_read, name='verification_v2_originals'),
    path('support/verification-resources/<str:resource_kind>/<uuid:resource_id>/originals/<uuid:file_id>/content', verifications.verification_original_content_read, name='verification_v2_original_content'),
    path('support/record-versions/<uuid:record_version_id>/verification-scope', verifications.verification_creation_scope_read, name='verification_v2_creation_scope'),
    path('support/verification-requests', verifications.verification_request_create, name='verification_v2_request_create'),
    path('support/verification-requests/<uuid:verification_request_id>', verifications.verification_request_read, name='verification_v2_request_read'),
    path('support/verification-requests/<uuid:verification_request_id>/options', verifications.verification_options_read, name='verification_v2_options'),
    path('support/verifications', verifications.verification_submit, name='verification_v2_submit'),
    path('support/verifications/<uuid:verification_id>', verifications.verification_read, name='verification_v2_read'),
    path('support/verifications/<uuid:verification_id>/revoke', verifications.verification_revoke, name='verification_v2_revoke'),
    path('support/patients/<uuid:patient_id>/assessments', assessments.assessment_create, name='assessment_v2_create'),
    path('support/assessments/<uuid:assessment_id>', assessments.assessment_read, name='assessment_v2_read'),
    path('support/assessment-tasks/<uuid:task_id>', assessments.assessment_task_read, name='assessment_task_read'),
    path('support/datasets/search', datasets.dataset_search, name='dataset_search'),
    path('support/datasets', datasets.dataset_create, name='dataset_create'),
    path('support/datasets/<uuid:dataset_id>', datasets.dataset_read, name='dataset_read'),
    path('support/datasets/<uuid:dataset_id>/partitions', datasets.dataset_partition, name='dataset_partition'),
    path('support/datasets/<uuid:dataset_id>/labels', datasets.dataset_label, name='dataset_label'),
    path('support/datasets/<uuid:dataset_id>/freeze', datasets.dataset_freeze, name='dataset_freeze'),
    path('support/datasets/<uuid:dataset_id>/runs', evaluation_runs.dataset_run_create, name='dataset_run_create'),
    path('support/evaluation-runs/<uuid:run_id>', evaluation_runs.dataset_run_read, name='dataset_run_read'),
    path('support/evaluation-runs/<uuid:run_id>/denominators', evaluation_runs.dataset_run_denominators, name='dataset_run_denominators'),
    path('support/evaluation-runs/<uuid:run_id>/results', evaluation_runs.dataset_run_results, name='dataset_run_results'),
    path('patients/<uuid:patient_id>/archive-batches', batch_writes.archive_batch_create, name='archive_batch_create'),
    path('patients/<uuid:patient_id>/archive-batches/search', batches.archive_batch_search, name='archive_batch_search'),
    path('patients/<uuid:patient_id>/archive-batch-inputs', batches.archive_batch_inputs, name='archive_batch_inputs'),
    path('archive-batches/<uuid:batch_id>', batches.archive_batch_read, name='archive_batch_read'),
    path('archive-pages/<uuid:page_id>', batches.archive_page_read, name='archive_page_read'),
    path('archive-batches/<uuid:batch_id>/process', page_actions.archive_batch_process, name='archive_batch_process'),
    path('archive-pages/<uuid:page_id>/retry', page_actions.archive_page_retry, name='archive_page_retry'),
    path('archive-pages/<uuid:page_id>/cancel', page_actions.archive_page_cancel, name='archive_page_cancel'),
    path('archive-pages/<uuid:page_id>/candidates', fact_writes.archive_candidates_read, name='archive_candidates_read'),
    path('archive-pages/<uuid:page_id>/facts', fact_writes.archive_page_facts_read, name='archive_page_facts_read'),
    path('archive-pages/<uuid:page_id>/original', fact_writes.archive_page_original_read, name='archive_page_original_read'),
    path('archive-pages/<uuid:page_id>/original/content', fact_writes.archive_page_original_content, name='archive_page_original_content'),
    path('archive-pages/<uuid:page_id>/confirm', fact_writes.archive_candidates_confirm, name='archive_candidates_confirm'),
    path('archive-facts/<uuid:fact_id>/corrections', fact_writes.archive_fact_correct, name='archive_fact_correct'),
    path('patients/<uuid:patient_id>/personal-record-tasks', personal_tasks.personal_record_task_create, name='personal_record_task_create'),
    path('patients/<uuid:patient_id>/personal-record-tasks/search', personal_tasks.personal_record_task_search, name='personal_record_task_search'),
    path('patients/<uuid:patient_id>/personal-record-task-retention', personal_tasks.personal_record_task_retention, name='personal_record_task_retention'),
    path('personal-record-tasks/<uuid:task_id>', personal_tasks.personal_record_task_read, name='personal_record_task_read'),
    path('personal-record-tasks/<uuid:task_id>/status', personal_tasks.personal_record_task_change, name='personal_record_task_change'),
    path('personal-record-task-intents/<uuid:intent_id>/cancel', personal_tasks.personal_record_task_intent_cancel, name='personal_record_task_intent_cancel'),
    path('patients/<uuid:patient_id>/material-reminder-targets/search', reminders.material_reminder_target_search, name='material_reminder_target_search'),
    path('patients/<uuid:patient_id>/material-reminder-notices/search', material_notices.material_notice_search, name='material_notice_search'),
    path('material-reminder-notices/<uuid:notice_id>', material_notices.material_notice_read, name='material_notice_read'),
    path('material-reminder-notices/<uuid:notice_id>/message', material_notices.material_notice_message, name='material_notice_message'),
    path('material-reminder-notices/<uuid:notice_id>/delivered', material_notices.material_notice_delivered, name='material_notice_delivered'),
    path('material-reminder-notices/<uuid:notice_id>/read', material_notices.material_notice_mark_read, name='material_notice_mark_read'),
    path('material-reminder-notices/<uuid:notice_id>/retry', material_notices.material_notice_retry, name='material_notice_retry'),
    path('patients/<uuid:patient_id>/material-reminders', reminder_writes.material_reminder_create, name='material_reminder_create'),
    path('material-reminders/<uuid:reminder_id>/settings', reminder_writes.material_reminder_change, name='material_reminder_change'),
    path('material-reminder-intents/<uuid:intent_id>/cancel', reminder_writes.material_reminder_intent_cancel, name='material_reminder_intent_cancel'),
    path('patients/<uuid:patient_id>/material-reminders/search', reminders.material_reminder_search, name='material_reminder_search'),
    path('material-reminders/<uuid:reminder_id>', reminders.material_reminder_read, name='material_reminder_read'),
    path('integrations/<str:provider_id>/receipts', support2.provider_callback, name='notification_receipt_receive'),
    path('patients/<uuid:patient_id>/archive-documents', document_writes.archive_document_create, name='archive_document_create'),
    path('archive-batches/<uuid:batch_id>/document-inputs', archive_document_inputs, name='archive_document_inputs'),
    path('archive-documents/<uuid:document_id>/grouping', document_writes.archive_document_group, name='archive_document_group'),
    path('archive-documents/<uuid:document_id>', documents.archive_document_read, name='archive_document_read'),
    path('archive-documents/<uuid:document_id>/versions', documents.archive_document_history, name='archive_document_history'),
    path('archive-facts/<uuid:fact_id>', facts.archive_fact_read, name='archive_fact_read'),
    path('archive-facts/<uuid:fact_id>/versions', facts.archive_fact_history, name='archive_fact_history'),
    path('support/session', support_session_read),
    path('support/session/login', support_login),
    path('write-intents', requests.v2_intent_prepare),
    path('write-intents/<uuid:intent_id>/retry', requests.v2_intent_retry),
    path('requests/<uuid:intent_id>', requests.v2_request_read),
    path('support/files/<uuid:file_id>/purpose-copies', purpose.purpose_copy_create, name='purpose_copy_create'),
    path('support/files/<uuid:file_id>/purposes/<str:purpose>/options', purpose.purpose_copy_options),
    path('support/purpose-copies/<uuid:copy_id>', purpose.purpose_copy_read),
    path('support/purpose-copies/<uuid:copy_id>/reviews', purpose.purpose_copy_review, name='purpose_copy_review'),
    path('support/purpose-copies/<uuid:copy_id>/revoke', purpose.purpose_copy_revoke, name='purpose_copy_revoke'),
    path('support/purpose-copies/<uuid:copy_id>/simulation-provider-copy', purpose.simulation_provider_copy_create, name='simulation_provider_copy_create'),
    path('support/purpose-copies/<uuid:copy_id>/content', purpose.purpose_copy_content),
    path('support/purpose-copies/<uuid:copy_id>/review-content', purpose.purpose_review_content),
    path('support/purpose-copies/<uuid:copy_id>/evidence', purpose.purpose_copy_evidence),
    path('tasks/<uuid:task_id>', purpose.purpose_task_read),
    path('support/patients/<uuid:patient_id>/disposition-coverage', disposition.support_disposition_coverage, name='disposition_coverage_read'),
    path('support/dispositions/<uuid:disposition_id>', disposition.support_disposition_read),
    path('patients/<uuid:patient_id>/disposition-options', family_dispositions.family_disposition_options),
    path('patients/<uuid:patient_id>/dispositions', family_dispositions.family_disposition_create, name='disposition_create'),
    path('dispositions/<uuid:disposition_id>', family_dispositions.family_disposition_read),
    path('support/dispositions/<uuid:disposition_id>/approve', disposition.disposition_approve, name='disposition_approve'),
    path('support/dispositions/<uuid:disposition_id>/items/<uuid:item_id>/execute', disposition.disposition_execute, name='disposition_execute'),
    path('support/dispositions/<uuid:disposition_id>/items/<uuid:item_id>/retain', disposition.disposition_retain, name='disposition_retain'),
    path('support/disposition-tasks/<uuid:task_id>/retry', disposition.disposition_retry, name='disposition_retry'),
]
