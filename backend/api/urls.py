from django.urls import path

from api import views_alert_presets as presets
from api import views_family as family
from api import views_family2 as family2
from api import views_family3 as family3
from api import views_support as support
from api import views_support2 as support2
from api import views_originals as originals
from api import views_releases as releases
from api import views_contacts as contacts
from api import views_configs as configs
from api import views_sources as sources
from api.patient_selection import patient_selection_write
from api import views_corrections as correction_reviews

urlpatterns = [
    path('patients/<uuid:patient_id>/alert-presets', presets.alert_preset_directory, name='alert_preset_directory'),
    path('patients/<uuid:patient_id>/alert-preset-selection', presets.alert_preset_select, name='alert_preset_select'),
    path('record-versions/<uuid:record_version_id>/originals', originals.record_originals_read),
    # 会话与身份
    path("auth/bootstrap", family.auth_bootstrap),
    path("auth/start", family.auth_start),
    path("auth/complete", family.auth_complete),
    path("auth/register", family.auth_register),
    path("auth/login", family.auth_login),
    path("session", family.session_read),
    path("patient-selection", patient_selection_write),
    path("session/logout", family.session_logout),
    path("capabilities", family.capabilities_read),
    # 关联
    path("relations", family.relation_list),
    path("relations/<uuid:relation_id>", family.relation_read),
    path("relation-applications", family.application_list, name='application_create'),
    path("relation-applications/<uuid:application_id>", family.application_read),
    path("relation-applications/<uuid:application_id>/withdraw", family.application_withdraw, name='application_withdraw'),
    path("support/relation-applications/<uuid:application_id>", support.application_review),
    path("support/relation-applications/<uuid:application_id>/decide", support.application_decide),
    path("support/relations/<uuid:relation_id>/revoke", support.relation_revoke),
    # 记录与来源
    path("patients/<uuid:patient_id>/records", family.record_create),
    path("patients/<uuid:patient_id>/records/search", family.record_search),
    path("records/<uuid:record_id>", family.record_read),
    path("records/<uuid:record_id>/history", family.record_history),
    path("records/<uuid:record_id>/corrections", family.record_correct),
    path("record-versions/<uuid:record_version_id>", family.record_version_read_view),
    path("record-versions/<uuid:record_version_id>/fields/<uuid:field_id>/source", family.source_resolve_view),
    path("patients/<uuid:patient_id>/history/search", family2.history_search_view),
    path("patients/<uuid:patient_id>/analytes/<str:concept_code>/search", family2.analyte_search_view),
    path("patients/<uuid:patient_id>/field-config", family.field_config_read_view),
    # 草稿
    path("patients/<uuid:patient_id>/drafts", family2.draft_collection),
    path("drafts/<uuid:draft_id>", family2.draft_detail),
    path("drafts/<uuid:draft_id>/abandon", family2.draft_abandon),
    # 摘要
    path("patients/<uuid:patient_id>/summaries", family2.summary_collection, name='summary_create'),
    path("summaries/<uuid:summary_id>", family2.summary_read),
    # 意图与请求
    path("write-intents", family2.intent_prepare),
    path("write-intents/<uuid:intent_id>/retry", family2.intent_retry),
    path("write-intents/<uuid:intent_id>/cancel", family2.intent_cancel),
    path("requests/<uuid:intent_id>", family2.request_query),
    path("patients/<uuid:patient_id>/requests", family2.request_recent),
    path("requests", family2.request_recent_account),
    # 首页与提示
    path("patients/<uuid:patient_id>/home", family.home_read),
    path("patients/<uuid:patient_id>/alerts", family.alert_list),
    path("patients/<uuid:patient_id>/alerts/stats", family.alert_stats),
    path("alerts/<uuid:alert_id>/read", family.alert_mark_read),
    # 任务
    path("tasks/<uuid:task_id>", family.task_read),
    path("tasks/<uuid:task_id>/cancel", family.task_cancel),
    path("tasks/<uuid:task_id>/retry", support2.task_retry),
    path("support/tasks/<uuid:task_id>/reconcile", support2.task_reconcile, name="task_reconcile"),
    path("support/reconciliation-tasks/<uuid:task_id>", support2.task_reconcile_read, name="task_reconcile_read"),
    # 受控支持：权限
    path("support/access-grants", support.grant_create),
    path("support/access-grants/<uuid:grant_id>/revoke", support.grant_revoke),
    path("support/role-grants", support.role_grant_create),
    path("support/role-grants/<uuid:grant_id>/revoke", support.role_grant_revoke),
    path("support/principals/<uuid:principal_id>/disable", support2.principal_disable),
    # 受控支持：配置
    path("support/configs", configs.config_create, name="config_create"),
    path("support/configs/<uuid:config_id>", configs.config_read, name="config_read"),
    path("support/configs/<uuid:config_id>/submit", configs.config_submit, name="config_submit"),
    path("support/configs/<uuid:config_id>/approvals", configs.config_approve, name="config_approve"),
    path("support/config-approvals/<uuid:approval_id>/revoke", configs.config_approval_revoke, name="config_approval_revoke"),
    path("support/configs/<uuid:config_id>/validations", configs.config_validate, name="config_validate"),
    path("support/configs/<uuid:config_id>/enable", configs.config_enable, name="config_enable"),
    path("support/configs/<uuid:config_id>/disable", configs.config_disable, name="config_disable"),
    path("support/environments/<str:environment>/runtime", support.runtime_activate),
    # 受控支持：来源、保留与用途
    path('support/sources', sources.source_register, name='source_register'),
    path('support/sources/<uuid:source_id>/revisions', sources.source_revision_register, name='source_revision_register'),
    path('support/source-revisions/<uuid:source_revision_id>/approve', sources.source_revision_approve, name='source_revision_approve'),
    path('support/source-revisions/<uuid:source_revision_id>/revoke', sources.source_revision_revoke, name='source_revision_revoke'),
    path('support/retention-policies', sources.retention_policy_create, name='retention_policy_create'),
    path('support/retention-policies/<uuid:policy_id>/activate', sources.retention_policy_activate, name='retention_policy_activate'),
    path("support/patients/<uuid:patient_id>/use-authorizations", support.use_authorization_create),
    path("support/use-authorizations/<uuid:authorization_id>/revoke", support.use_authorization_revoke),
    # 受控支持：核对
    path("support/verification-requests", support.verification_request_create),
    path("support/verification-requests/<uuid:verification_request_id>", support.verification_request_read),
    path("support/verifications", support.verification_submit),
    path("support/verifications/<uuid:verification_id>", support.verification_read),
    path("support/verifications/<uuid:verification_id>/revoke", support.verification_revoke),
    # 受控支持：联系与发布
    path("support/contacts", contacts.contact_create, name="contact_create"),
    path("support/contacts/<uuid:contact_version_id>/activate", contacts.contact_activate, name="contact_activate"),
    path("support/contacts/<uuid:contact_version_id>/disable", contacts.contact_disable, name="contact_disable"),
    path("support/releases", releases.release_create, name="release_create"),
    path("support/releases/<uuid:release_id>", releases.release_read),
    path("support/releases/<uuid:release_id>/approve", releases.release_approve, name="release_approve"),
    path("support/releases/<uuid:release_id>/enable", releases.release_enable, name="release_enable"),
    path("support/releases/<uuid:release_id>/disable", releases.release_disable, name="release_disable"),
    # 受控支持：处置与审计
    path("support/dispositions/<uuid:disposition_id>/approve", support.disposition_approve),
    path("support/dispositions/<uuid:disposition_id>/execute", support.disposition_execute),
    path("support/audit/search", support.audit_search),
    # 文件与原件
    path("patients/<uuid:patient_id>/uploads", family3.file_group_create),
    path("uploads/<uuid:group_id>/files/<uuid:upload_id>", family3.file_upload),
    path("uploads/<uuid:group_id>/complete", family3.file_group_complete),
    path("uploads/<uuid:group_id>/revisions", family3.file_group_revise),
    path("uploads/<uuid:group_id>", family3.file_group_read),
    path("files/<uuid:file_id>/content", family3.file_content),
    # 识别候选
    path("recognition-jobs", family3.recognition_create),
    path("recognition/voice-structure", family3.voice_structure),
    path("recognition/report-ocr", family3.report_ocr),
    path("recognition/therapy-ocr", family3.therapy_ocr),
    path("patients/<uuid:patient_id>/voice-transcription", family3.voice_transcription),
    path("candidate-sets/<uuid:candidate_set_id>", family3.candidate_read),
    # 字典、更正申请、处置、联系、提示详情
    path("dictionaries/search", family3.dictionary_search),
    path("records/<uuid:record_id>/correction-requests", family3.correction_request_create, name="correction_request_create"),
    path("correction-requests/<uuid:correction_request_id>", family3.correction_request_read),
    path('support/correction-requests', correction_reviews.correction_review_queue, name="correction_review_queue"),
    path('support/correction-requests/<uuid:correction_request_id>', correction_reviews.correction_review_read, name="correction_review_read"),
    path("support/correction-requests/<uuid:correction_request_id>/resolve", support2.correction_request_resolve, name="correction_request_resolve"),
    path("patients/<uuid:patient_id>/dispositions", family3.disposition_create),
    path("dispositions/<uuid:disposition_id>", family3.disposition_read),
    path("patients/<uuid:patient_id>/contact", family3.contact_read),
    path("alerts/<uuid:alert_id>", family3.alert_read),
    # 受控支持：评估、病例与专业记录
    path("support/patients/<uuid:patient_id>/assessments", support2.assessment_create),
    path("support/assessments/<uuid:assessment_id>", support2.assessment_read),
    path("support/patients/<uuid:patient_id>/alert-evaluations", support2.alert_evaluation_create),
    path("support/alert-evaluations/<uuid:evaluation_id>", support2.alert_evaluation_read),
    path("support/patients/<uuid:patient_id>/case/search", support2.case_search),
    path("support/patients/<uuid:patient_id>/case/import", support2.case_import),
    path("support/patients/<uuid:patient_id>/records", support2.professional_record_create),
    path("support/records/<uuid:record_id>/corrections", support2.professional_record_correct),
    # 提供方回执
    path("integrations/<str:provider_id>/receipts", support2.provider_callback),
]
