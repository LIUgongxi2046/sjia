# 全量历史测试基线对照

## 历史基线记录边界（2026-10-11 15:45 UTC）

以下原始/最终对照、失败数和原因描述仅属于早期检查点及其当时的检出范围，原样保留供追溯；不是本轮源码当前仍有这些失败，也不是当前完整测试结果。后续已补齐检出文件、重建有明确来源的合成夹具并修复多轮问题，相关新结论不能从这份旧报告直接推断。

本轮新增能力、最终冻结版本、完整回归的真实通过/失败/跳过/未运行项，统一以新交付包中的独立验证摘要、源码清单和原始测试日志为准。本文件不包含或批准真实患者输入、真实临床政策或生产处置。


使用相同依赖与 PostgreSQL 17，在分别新建的测试库运行；未添加历史缺失证据或批准配置。

- 原始基线：282 项，6 failures、92 errors（98 个失败测试 ID）
- 最终修复版：354 项，32 failures、51 errors（83 个失败测试 ID）
- 最终目标回归：88 项通过，其中 72 项新增、16 项原有目标测试
- 83 个最终失败 ID 都在原始失败集中；其中 33 个错误文本变化。15 个原失败不再出现。
- 这不是全量通过或“没有回归”的证明。修复会话表可见性后，一些原先被数据库错误挡住的测试开始触及后面的权限/配置断言。

## 最终失败原因汇总

- 14 项：`family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.`
- 13 项：`FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'`
- 9 项：`AssertionError: 422 != 201`
- 8 项：`notifications.errors.NotificationRejected: NOTIFICATION_PROVIDER_UNAVAILABLE`
- 7 项：`AssertionError: 403 != 201`
- 6 项：`configurations.service.ConfigRejected: CONFIG_PAYLOAD_INVALID`
- 3 项：`FileNotFoundError: [Errno 2] No such file or directory: '<repo>/data-design/case-design-projection.json'`
- 2 项：`configurations.service.ConfigRejected: CONFIG_SCHEMA_UNAVAILABLE`
- 2 项：`AssertionError: 503 != 201`
- 2 项：`AssertionError: 409 != 202`
- 2 项：`AssertionError: 403 != 200`
- 2 项：`AssertionError: 'TRANSCRIPTION_AUTHORIZATION_REQUIRED' != 'AUDIO_INVALID'`
- 1 项：`KeyError: 'evaluation_id'`
- 1 项：`KeyError: 'group_id'`
- 1 项：`records.correction.CorrectionRejected: CORRECTION_FIELD_IDENTITY_CHANGED`
- 1 项：`configurations.service.ConfigRejected: CONFIG_EVIDENCE_REFERENCE_INVALID`
- 1 项：`summaries.service.SummaryRejected: SUMMARY_TASK_INPUT_INVALID`
- 1 项：`AssertionError: 500 != 200`
- 1 项：`AssertionError: 422 != 200`
- 1 项：`AssertionError: 'unavailable' != 'available'`
- 1 项：`AssertionError: 'unavailable' != 'configuration_pending'`
- 1 项：`AssertionError: 123 != 89`
- 1 项：`AssertionError: 'disabled' != 'available'`
- 1 项：`AssertionError: 'TRANSCRIPTION_AUTHORIZATION_REQUIRED' != 'AUDIO_TOO_LONG'`
- 1 项：`AssertionError: 'TRANSCRIPTION_AUTHORIZATION_REQUIRED' != 'AUDIO_TOO_SHORT'`

缺失的模拟图片与 case-design-projection.json 是仓库原有忽略文件；没有重新编造这些历史证据来让测试变绿。配置审批、通知、环境 gate 等旧 fixture 也未被自动补批准。

## 各项对照

状态 PASS 表示本次对应全量运行中未再失败。以下不包含所有通过项。

### tests.integration.test_flow.FamilyFlowIntegrationTest.test_field_config_read
- 原始：FAIL · AssertionError: 500 != 200
- 最终：FAIL · AssertionError: 500 != 200

### tests.integration.test_flow.FamilyFlowIntegrationTest.test_record_create_and_read
- 原始：FAIL · AssertionError: 422 != 201
- 最终：FAIL · AssertionError: 422 != 201

### tests.integration.test_flow2.FamilyFlow2IntegrationTest.test_config_create_and_approve
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 403 != 201

### tests.integration.test_flow2.FamilyFlow2IntegrationTest.test_draft_flow
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：PASS

### tests.integration.test_flow2.FamilyFlow2IntegrationTest.test_history_and_analyte
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：PASS

### tests.integration.test_flow2.FamilyFlow2IntegrationTest.test_intent_flow
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：PASS

### tests.integration.test_flow2.FamilyFlow2IntegrationTest.test_summary_flow
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：ERROR · configurations.service.ConfigRejected: CONFIG_PAYLOAD_INVALID

### tests.integration.test_flow3.FamilyFlow3IntegrationTest.test_assessment_classification
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 403 != 201

### tests.integration.test_flow3.FamilyFlow3IntegrationTest.test_case_import
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 403 != 201

### tests.integration.test_flow3.FamilyFlow3IntegrationTest.test_correction_request_and_disposition
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 422 != 201

### tests.integration.test_flow3.FamilyFlow3IntegrationTest.test_dictionary_search
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 422 != 200

### tests.integration.test_flow3.FamilyFlow3IntegrationTest.test_file_upload_and_content
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 503 != 201

### tests.integration.test_flow4.EndpointCoverageIntegrationTest.test_alert_evaluation_create_and_read
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 409 != 202

### tests.integration.test_flow4.EndpointCoverageIntegrationTest.test_application_and_relation_read
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 422 != 201

### tests.integration.test_flow4.EndpointCoverageIntegrationTest.test_approved_content_seeded
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：PASS

### tests.integration.test_flow4.EndpointCoverageIntegrationTest.test_auth_flow_and_capabilities
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：PASS

### tests.integration.test_flow4.EndpointCoverageIntegrationTest.test_case_search_and_import
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 403 != 201

### tests.integration.test_flow4.EndpointCoverageIntegrationTest.test_config_lifecycle_and_validation
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 403 != 201

### tests.integration.test_flow4.EndpointCoverageIntegrationTest.test_contact_disable_and_runtime
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 403 != 201

### tests.integration.test_flow4.EndpointCoverageIntegrationTest.test_file_group_revise_and_recognition
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 503 != 201

### tests.integration.test_flow4.EndpointCoverageIntegrationTest.test_grants_create_and_revoke
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 403 != 201

### tests.integration.test_flow4.EndpointCoverageIntegrationTest.test_in_app_message_stats
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：ERROR · KeyError: 'evaluation_id'

### tests.integration.test_flow4.EndpointCoverageIntegrationTest.test_provider_callback
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 409 != 202

### tests.integration.test_flow4.EndpointCoverageIntegrationTest.test_record_history_and_correct
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 422 != 201

### tests.integration.test_flow4.EndpointCoverageIntegrationTest.test_request_recent
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：PASS

### tests.integration.test_flow4.EndpointCoverageIntegrationTest.test_self_built_register_and_login
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：PASS

### tests.integration.test_flow4.EndpointCoverageIntegrationTest.test_source_revoke_and_retention_activate
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 403 != 200

### tests.integration.test_flow4.EndpointCoverageIntegrationTest.test_use_authorization_revoke
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 403 != 200

### tests.integration.test_flow4.EndpointCoverageIntegrationTest.test_verification_request_and_read
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 422 != 201

### tests.test_QA02.AlertContentTest.test_current_content_and_contact
- 原始：FAIL · AssertionError: 'unavailable' != 'available'
- 最终：FAIL · AssertionError: 'unavailable' != 'available'

### tests.test_QA02.AlertContentTest.test_disabled_content_does_not_return_text
- 原始：FAIL · AssertionError: 'unavailable' != 'configuration_pending'
- 最终：FAIL · AssertionError: 'unavailable' != 'configuration_pending'

### tests.test_QA02.DraftTransactionTest.test_current_draft_correction_consumes_same_transaction
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 422 != 201

### tests.test_QA02.DraftTransactionTest.test_expired_draft_cannot_create_correction
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 422 != 201

### tests.test_QA02.DraftTransactionTest.test_record_change_invalidates_cursor
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 422 != 201

### tests.test_QA02.DraftTransactionTest.test_report_page_source_survives_correction_and_requires_attachment
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：ERROR · KeyError: 'group_id'

### tests.test_QA02.DraftTransactionTest.test_stale_draft_correction_preserves_record_and_draft
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 422 != 201

### tests.test_QA02.MedicalPaginationTest.test_corrected_version_preserves_source_type
- 原始：ERROR · records.correction.CorrectionRejected: CORRECTION_FIELD_IDENTITY_CHANGED
- 最终：ERROR · records.correction.CorrectionRejected: CORRECTION_FIELD_IDENTITY_CHANGED

### tests.test_QA02.TherapySummaryScopeTest.test_explicit_ongoing_therapy_includes_name
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：ERROR · configurations.service.ConfigRejected: CONFIG_PAYLOAD_INVALID

### tests.test_QA02.TherapySummaryScopeTest.test_therapy_spanning_scope_includes_name_with_unknown_field_time
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：ERROR · configurations.service.ConfigRejected: CONFIG_PAYLOAD_INVALID

### tests.test_QA02.TherapySummaryScopeTest.test_unknown_end_does_not_infer_ongoing_therapy
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：ERROR · configurations.service.ConfigRejected: CONFIG_PAYLOAD_INVALID

### tests.test_T02.TableStructureTest.test_89_business_tables_in_sjia_schema
- 原始：FAIL · AssertionError: 123 != 89
- 最终：FAIL · AssertionError: 123 != 89

### tests.test_T04.ConfigServiceTest.test_enable_requires_approval_and_validation
- 原始：ERROR · configurations.service.ConfigRejected: CONFIG_SCHEMA_UNAVAILABLE
- 最终：ERROR · configurations.service.ConfigRejected: CONFIG_SCHEMA_UNAVAILABLE

### tests.test_T04.ConfigServiceTest.test_register_config_immutable_creates_new_version
- 原始：ERROR · configurations.service.ConfigRejected: CONFIG_SCHEMA_UNAVAILABLE
- 最终：ERROR · configurations.service.ConfigRejected: CONFIG_SCHEMA_UNAVAILABLE

### tests.test_T04.FieldConfigReadTest.test_available_with_enabled_config
- 原始：FAIL · django.db.utils.OperationalError: database "test_sjia_baseline_clean" is being accessed by other users
- 最终：FAIL · AssertionError: 'disabled' != 'available'

### tests.test_T04.SourceServiceTest.test_register_and_approve_revision
- 原始：ERROR · configurations.service.ConfigRejected: CONFIG_EVIDENCE_REFERENCE_INVALID
- 最终：ERROR · configurations.service.ConfigRejected: CONFIG_EVIDENCE_REFERENCE_INVALID

### tests.test_T04.UnitConversionTest.test_convert_known_mapping
- 原始：ERROR · configurations.service.ConfigRejected: CONFIG_PAYLOAD_INVALID
- 最终：ERROR · configurations.service.ConfigRejected: CONFIG_PAYLOAD_INVALID

### tests.test_T04.UnitConversionTest.test_unknown_mapping_not_converted
- 原始：ERROR · configurations.service.ConfigRejected: CONFIG_PAYLOAD_INVALID
- 最终：ERROR · configurations.service.ConfigRejected: CONFIG_PAYLOAD_INVALID

### tests.test_T09.SessionBootstrapTest.test_bootstrap_and_session_lifecycle
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：PASS

### tests.test_T14.TherapyServiceTest.test_revoke_blocks_family_read_without_deleting_source_record
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：PASS

### tests.test_T21.ReleaseServiceTest.test_disable_release
- 原始：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.
- 最终：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.

### tests.test_T21.ReleaseServiceTest.test_enable_gate_version_mismatch
- 原始：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.
- 最终：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.

### tests.test_T21.ReleaseServiceTest.test_enable_requires_approval
- 原始：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.
- 最终：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.

### tests.test_T21.ReleaseServiceTest.test_enable_requires_components
- 原始：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.
- 最终：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.

### tests.test_T21.ReleaseServiceTest.test_enable_success
- 原始：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.
- 最终：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.

### tests.test_T21.ReleaseServiceTest.test_expired_contact_release_unavailable
- 原始：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.
- 最终：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.

### tests.test_T23.NotificationServiceTest.test_accepted_receipt
- 原始：ERROR · notifications.errors.NotificationRejected: NOTIFICATION_PROVIDER_UNAVAILABLE
- 最终：ERROR · notifications.errors.NotificationRejected: NOTIFICATION_PROVIDER_UNAVAILABLE

### tests.test_T23.NotificationServiceTest.test_authorize_creates_attempt
- 原始：ERROR · notifications.errors.NotificationRejected: NOTIFICATION_PROVIDER_UNAVAILABLE
- 最终：ERROR · notifications.errors.NotificationRejected: NOTIFICATION_PROVIDER_UNAVAILABLE

### tests.test_T23.NotificationServiceTest.test_cancel_prevents_send
- 原始：ERROR · notifications.errors.NotificationRejected: NOTIFICATION_PROVIDER_UNAVAILABLE
- 最终：ERROR · notifications.errors.NotificationRejected: NOTIFICATION_PROVIDER_UNAVAILABLE

### tests.test_T23.NotificationServiceTest.test_delivered_not_accepted_as_delivered
- 原始：ERROR · notifications.errors.NotificationRejected: NOTIFICATION_PROVIDER_UNAVAILABLE
- 最终：ERROR · notifications.errors.NotificationRejected: NOTIFICATION_PROVIDER_UNAVAILABLE

### tests.test_T23.NotificationServiceTest.test_failed_receipt
- 原始：ERROR · notifications.errors.NotificationRejected: NOTIFICATION_PROVIDER_UNAVAILABLE
- 最终：ERROR · notifications.errors.NotificationRejected: NOTIFICATION_PROVIDER_UNAVAILABLE

### tests.test_T23.NotificationServiceTest.test_mark_uncertain
- 原始：ERROR · notifications.errors.NotificationRejected: NOTIFICATION_PROVIDER_UNAVAILABLE
- 最终：ERROR · notifications.errors.NotificationRejected: NOTIFICATION_PROVIDER_UNAVAILABLE

### tests.test_T23.NotificationServiceTest.test_receipt_dedup_by_provider_event
- 原始：ERROR · notifications.errors.NotificationRejected: NOTIFICATION_PROVIDER_UNAVAILABLE
- 最终：ERROR · notifications.errors.NotificationRejected: NOTIFICATION_PROVIDER_UNAVAILABLE

### tests.test_T23.NotificationServiceTest.test_start_attempt
- 原始：ERROR · notifications.errors.NotificationRejected: NOTIFICATION_PROVIDER_UNAVAILABLE
- 最终：ERROR · notifications.errors.NotificationRejected: NOTIFICATION_PROVIDER_UNAVAILABLE

### tests.test_T25.SummaryServiceTest.test_read_summary_with_items
- 原始：ERROR · summaries.service.SummaryRejected: SUMMARY_TASK_INPUT_INVALID
- 最终：ERROR · summaries.service.SummaryRejected: SUMMARY_TASK_INPUT_INVALID

### tests.test_T26.DispositionServiceTest.test_approve_request
- 原始：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.
- 最终：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.

### tests.test_T26.DispositionServiceTest.test_awaiting_provider
- 原始：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.
- 最终：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.

### tests.test_T26.DispositionServiceTest.test_create_item_requires_approved
- 原始：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.
- 最终：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.

### tests.test_T26.DispositionServiceTest.test_create_request_pending
- 原始：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.
- 最终：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.

### tests.test_T26.DispositionServiceTest.test_deleted_requires_receipt
- 原始：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.
- 最终：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.

### tests.test_T26.DispositionServiceTest.test_deleted_with_receipt
- 原始：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.
- 最终：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.

### tests.test_T26.DispositionServiceTest.test_retained_requires_evidence
- 原始：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.
- 最终：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.

### tests.test_T26.DispositionServiceTest.test_retained_with_evidence
- 原始：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.
- 最终：ERROR · family_content.models.EnvironmentGate.DoesNotExist: EnvironmentGate matching query does not exist.

### tests.test_T27.CaseImportTest.test_import_case_counts
- 原始：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/data-design/case-design-projection.json'
- 最终：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/data-design/case-design-projection.json'

### tests.test_T27.CaseImportTest.test_import_creates_entities
- 原始：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/data-design/case-design-projection.json'
- 最终：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/data-design/case-design-projection.json'

### tests.test_T27.CaseImportTest.test_value_round_trip
- 原始：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/data-design/case-design-projection.json'
- 最终：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/data-design/case-design-projection.json'

### tests.test_qa03_recognition.RecognitionAdmissionTest.test_cancelled_event_is_consumed_without_provider
- 原始：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'
- 最终：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'

### tests.test_qa03_recognition.RecognitionAdmissionTest.test_cancelled_retry_creates_one_new_task
- 原始：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'
- 最终：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'

### tests.test_qa03_recognition.RecognitionAdmissionTest.test_expired_task_is_rejected_before_provider
- 原始：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'
- 最终：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'

### tests.test_qa03_recognition.RecognitionAdmissionTest.test_incorrect_version_is_rejected
- 原始：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'
- 最终：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'

### tests.test_qa03_recognition.RecognitionAdmissionTest.test_other_actor_cannot_retry_task
- 原始：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'
- 最终：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'

### tests.test_qa03_recognition.RecognitionAdmissionTest.test_pending_task_cannot_be_retried
- 原始：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'
- 最终：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'

### tests.test_qa03_recognition.RecognitionAdmissionTest.test_provider_is_server_controlled
- 原始：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'
- 最终：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'

### tests.test_qa03_recognition.RecognitionAdmissionTest.test_quarantined_file_is_rejected
- 原始：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'
- 最终：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'

### tests.test_qa03_recognition.RecognitionAdmissionTest.test_registers_pending_task_and_durable_event
- 原始：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'
- 最终：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'

### tests.test_qa03_recognition.RecognitionAdmissionTest.test_same_group_is_idempotent
- 原始：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'
- 最终：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'

### tests.test_qa03_recognition.RecognitionAdmissionTest.test_superseded_group_is_rejected
- 原始：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'
- 最终：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'

### tests.test_qa03_recognition.RecognitionAdmissionTest.test_upload_rejects_completed_group
- 原始：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'
- 最终：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'

### tests.test_qa03_recognition.RecognitionAdmissionTest.test_upload_rejects_invalid_size_and_ordinal
- 原始：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'
- 最终：ERROR · FileNotFoundError: [Errno 2] No such file or directory: '<repo>/docs/testing/evidence/QA02/simulated-report.png'

### tests.test_qa04_inputs.SpeechInputTests.test_accepts_codec_parameter_before_decoding
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 'TRANSCRIPTION_AUTHORIZATION_REQUIRED' != 'AUDIO_INVALID'

### tests.test_qa04_inputs.SpeechInputTests.test_busy_does_not_queue_audio
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：PASS

### tests.test_qa04_inputs.SpeechInputTests.test_rejects_duration_before_inference
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 'TRANSCRIPTION_AUTHORIZATION_REQUIRED' != 'AUDIO_TOO_LONG'

### tests.test_qa04_inputs.SpeechInputTests.test_rejects_invalid_audio_and_releases_capacity
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 'TRANSCRIPTION_AUTHORIZATION_REQUIRED' != 'AUDIO_INVALID'

### tests.test_qa04_inputs.SpeechInputTests.test_rejects_other_patient
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：PASS

### tests.test_qa04_inputs.SpeechInputTests.test_rejects_size_and_type
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：PASS

### tests.test_qa04_inputs.SpeechInputTests.test_requires_session
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：PASS

### tests.test_qa04_inputs.SpeechInputTests.test_short_audio_is_rejected_before_model
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：FAIL · AssertionError: 'TRANSCRIPTION_AUTHORIZATION_REQUIRED' != 'AUDIO_TOO_SHORT'

### tests.test_qa04_inputs.SpeechInputTests.test_therapy_ocr_validates_request_and_session
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：PASS

### tests.test_qa04_inputs.SpeechInputTests.test_therapy_schema_preserves_original_and_rejects_advice
- 原始：ERROR · django.db.utils.ProgrammingError: relation "django_session" does not exist
- 最终：PASS
