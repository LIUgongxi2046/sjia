# 当前 ORM 数据字典

doc_id：SJIA-ORM-DICTIONARY；status：CURRENT；last_verified：2026-10-21（Asia/Shanghai）。

生成依据：源码 `f05dbcf106e70d890a5574385fd63b3ddd502146` 的 Django Model 元数据；本次仅导入模型，没有连接数据库或读取业务资料。

字段类型通过 PostgreSQL backend 的 `db_type()` 获取；自定义 DOMAIN、复合外键、触发器及原生 SQL 约束须结合正式 migrations 与 SQL 查阅。表内约束仅列 ORM 元数据，SQL 约束未在此重复生成。

关联说明见[数据字典导读](data-dictionary.md)；完整数据库结构证据见[QA28](../development/QA28-target-runtime-compatibility.md)。

## `sjia.access_grant`

源码：`authorization.models.AccessGrant`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| principal_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| relation_id | uuid | True | — | — |
| action | text | False | — | — |
| field_codes | text[] | False | — | — |
| record_scope | varchar(16) | False | — | own, all, assigned |
| assigned_record_id | uuid | True | — | — |
| purpose | text | False | — | — |
| granted_by | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| evidence_ref | text | False | — | — |
| valid_from | timestamp with time zone | False | — | — |
| valid_until | timestamp with time zone | True | — | — |
| revoked_at | timestamp with time zone | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：('django.db.models.Index', (), {'name': 'access_lookup', 'fields': ['principal_id', 'patient_id', 'action']})。

## `sjia.alert_decision`

源码：`alerting.models.AlertDecision`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| evaluation_id | uuid | False | — | — |
| patient_id | uuid | False | — | — |
| rule_key | text | True | — | — |
| state | varchar(16) | True | — | met, not_met, unknown |
| action | varchar(16) | True | — | create_event, continue_episode, close_episode, no_event, blocked |
| reason_codes | text[] | True | — | — |
| episode_id | uuid | True | — | — |
| sequence | bigint | False | — | — |
| previous_decision_id | uuid | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'alert_decision_id_patient_id_rule_key_uniq', 'fields': ('id', 'patient_id', 'rule_key')})；('django.db.models.UniqueConstraint', (), {'name': 'alert_decision_evaluation_id_rule_key_uniq', 'fields': ('evaluation_id', 'rule_key')})；('django.db.models.UniqueConstraint', (), {'name': 'alert_decision_patient_id_rule_key_sequence_uniq', 'fields': ('patient_id', 'rule_key', 'sequence')})。

ORM 组合索引：无登记。

## `sjia.alert_episode`

源码：`alerting.models.AlertEpisode`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| rule_family_key | text | True | — | — |
| status | varchar(16) | False | — | open, closed, invalid |
| opened_at | timestamp with time zone | True | — | — |
| last_evaluation_id | uuid | False | — | — |
| last_event_id | uuid | True | — | — |
| state_version | bigint | False | — | — |
| reset_evaluation_id | uuid | True | — | — |
| last_assessment_at | timestamp with time zone | True | — | — |
| last_context_sequence | bigint | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'alert_episode_id_patient_uniq', 'fields': ('id', 'patient_id')})；('django.db.models.UniqueConstraint', (), {'name': 'episode_one_open', 'fields': ('patient_id', 'rule_family_key'), 'condition': <Q: (AND: ('status', 'open'))>})。

ORM 组合索引：无登记。

## `sjia.alert_evaluation`

源码：`alerting.models.AlertEvaluation`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| task_id | uuid | False | UNIQUE | — |
| environment | text | False | — | — |
| policy_id | uuid | True | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| window_policy_id | uuid | True | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| assessment_at | timestamp with time zone | True | — | — |
| known_at | timestamp with time zone | True | — | — |
| context_sequence | bigint | False | — | — |
| mode | varchar(16) | False | — | current, replay |
| status | varchar(24) | False | — | configuration_pending, insufficient, completed, failed, superseded |
| decision | varchar(16) | True | — | met, not_met, unknown |
| data_epoch | bigint | False | — | — |
| verification_epoch | bigint | False | — | — |
| publication_epoch | bigint | False | — | — |
| authorization_version | bigint | False | — | — |
| lease_generation | bigint | False | — | — |
| use_authorization_id | uuid | False | — | — |
| input_digest | sjia.sha256_hex | False | — | — |
| missing_items | jsonb | True | — | — |
| computed_at | timestamp with time zone | False | — | — |
| validity | varchar(16) | False | — | valid, invalid |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'alert_evaluation_id_patient_uniq', 'fields': ('id', 'patient_id')})；('django.db.models.UniqueConstraint', (), {'name': 'alert_evaluation_patient_id_context_sequence_uniq', 'fields': ('patient_id', 'context_sequence')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'evaluation_current', 'fields': ['patient_id', '-assessment_at', '-context_sequence'], 'condition': <Q: (AND: ('mode', 'current'))>})。

## `sjia.alert_evaluation_assessment`

源码：`alerting.models.AlertEvaluationAssessment`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| evaluation_id | uuid | False | — | — |
| assessment_id | uuid | False | — | — |
| patient_id | uuid | False | — | — |
| role | text | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'alert_evaluation_assessment_pk_uniq', 'fields': ('evaluation_id', 'assessment_id', 'role')})。

ORM 组合索引：无登记。

## `sjia.alert_evaluation_input`

源码：`alerting.models.AlertEvaluationInput`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| evaluation_id | uuid | False | — | — |
| patient_id | uuid | False | — | — |
| signal_id | text | True | — | — |
| position | integer | False | — | — |
| variable_code | text | True | — | — |
| input_role | varchar(16) | True | — | baseline, current, series, context |
| record_version_id | uuid | False | — | — |
| field_id | uuid | False | — | — |
| verification_id | uuid | False | — | — |
| normalization_id | uuid | True | — | — |
| medical_time_id | uuid | True | — | — |
| source_revision_id | uuid | False | INDEX; REFERENCES sources.SourceRevision.id | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'alert_evaluation_input_pk_uniq', 'fields': ('evaluation_id', 'signal_id', 'position')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'evaluation_input_field', 'fields': ['field_id', 'evaluation_id']})。

## `sjia.alert_event`

源码：`alerting.models.AlertEvent`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| evaluation_id | uuid | False | — | — |
| release_id | uuid | False | INDEX; REFERENCES family_content.ReleaseBundle.id | — |
| trigger_key | text | True | — | — |
| trigger_policy_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| input_snapshot | jsonb | True | — | — |
| validity | varchar(16) | False | — | valid, invalid |
| created_at | timestamp with time zone | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'alert_event_id_patient_uniq', 'fields': ('id', 'patient_id')})；('django.db.models.UniqueConstraint', (), {'name': 'alert_event_patient_trigger_policy_trigger_key_uniq', 'fields': ('patient_id', 'trigger_policy_id', 'trigger_key')})。

ORM 组合索引：无登记。

## `sjia.alert_preset_choice`

源码：`alerting.models.AlertPresetChoice`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| environment | text | False | — | — |
| purpose | text | False | — | — |
| version | bigint | False | — | — |
| assignment_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| schedule_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| selected_by_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| selected_at | timestamp with time zone | False | — | — |
| authorization_context | jsonb | False | — | — |
| write_request_id | uuid | True | INDEX; REFERENCES requests.WriteRequest.id | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'preset_choice_scope_version_uniq', 'fields': ('patient', 'environment', 'purpose', 'version')})；('django.db.models.CheckConstraint', (), {'name': 'preset_choice_positive_version', 'condition': <Q: (AND: ('version__gte', 1))>})。

ORM 组合索引：无登记。

## `sjia.alert_recipient`

源码：`alerting.models.AlertRecipient`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| alert_id | uuid | False | — | — |
| relation_id | uuid | False | — | — |
| channel | text | False | — | — |
| delivery_status | varchar(16) | False | — | not_sent, pending, accepted, delivered, failed, uncertain |
| read_at | timestamp with time zone | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'alert_recipient_id_patient_uniq', 'fields': ('id', 'patient_id')})；('django.db.models.UniqueConstraint', (), {'name': 'alert_recipient_alert_id_relation_id_channel_uniq', 'fields': ('alert_id', 'relation_id', 'channel')})。

ORM 组合索引：无登记。

## `sjia.alert_signal_result`

源码：`alerting.models.AlertSignalResult`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| evaluation_id | uuid | False | — | — |
| patient_id | uuid | False | — | — |
| signal_id | text | True | — | — |
| schema_version | text | False | — | — |
| state | varchar(16) | True | — | met, not_met, unknown |
| algorithm_version | text | False | — | — |
| input_field_ids | uuid[] | False | — | — |
| reason_codes | text[] | True | — | — |
| pair_results | jsonb | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'alert_signal_result_pk_uniq', 'fields': ('evaluation_id', 'signal_id')})。

ORM 组合索引：无登记。

## `sjia.archive`

源码：`archives.models.Archive`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| created_at | timestamp with time zone | False | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.archive_batch`

源码：`archives.models.ArchiveBatch`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| archive_id | uuid | False | — | — |
| submitted_by | uuid | False | — | — |
| logical_request_id | uuid | False | — | — |
| version | bigint | False | — | — |
| received_at | timestamp with time zone | False | — | — |
| quota_policy_id | uuid | True | — | — |
| intake_mode | varchar(8) | False | — | — |
| budget_status | text | False | — | — |
| status | text | False | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.archive_document`

源码：`archives.models.ArchiveDocument`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| current_version | bigint | False | — | — |
| created_at | timestamp with time zone | False | — | — |
| retired_at | timestamp with time zone | True | — | — |
| retired_by | uuid | True | — | — |
| retirement_id | uuid | True | — | — |
| retirement_reason | text | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.archive_document_episode`

源码：`archives.models.ArchiveDocumentEpisode`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| document_version_id | uuid | False | — | — |
| episode_id | uuid | False | — | — |
| evidence_anchor_id | uuid | False | — | — |
| confirmed_by | uuid | False | — | — |
| confirmed_at | timestamp with time zone | False | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.archive_document_page`

源码：`archives.models.ArchiveDocumentPage`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| document_version_id | uuid | False | — | — |
| page_id | uuid | False | — | — |
| page_order | integer | False | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.archive_document_version`

源码：`archives.models.ArchiveDocumentVersion`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| document_id | uuid | False | — | — |
| version | bigint | False | — | — |
| batch_id | uuid | False | — | — |
| document_type | text | True | — | — |
| organization | text | True | — | — |
| proposed_metadata | jsonb | True | — | — |
| confirmed_text | text | True | — | — |
| grouping_status | text | False | — | — |
| documented_time_id | uuid | True | — | — |
| available_at | timestamp with time zone | False | — | — |
| confirmed_by | uuid | True | — | — |
| confirmed_at | timestamp with time zone | True | — | — |
| supersedes_version | bigint | True | — | — |
| reason | text | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.archive_fact`

源码：`archives.models.ArchiveFact`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| current_version | bigint | False | — | — |
| created_at | timestamp with time zone | False | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.archive_fact_version`

源码：`archives.models.ArchiveFactVersion`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| fact_id | uuid | False | — | — |
| version | bigint | False | — | — |
| record_version_id | uuid | False | — | — |
| field_id | uuid | False | — | — |
| document_version_id | uuid | True | — | — |
| page_id | uuid | True | — | — |
| source_anchor_id | uuid | True | — | — |
| context_type | text | True | — | — |
| assertion_state | text | True | — | — |
| episode_id | uuid | True | — | — |
| medical_time_id | uuid | True | — | — |
| raw_time_text | text | True | — | — |
| time_origin | text | True | — | — |
| documented_time_id | uuid | True | — | — |
| available_at | timestamp with time zone | True | — | — |
| availability_status | text | False | — | — |
| confirmation_id | uuid | True | — | — |
| created_at | timestamp with time zone | False | — | — |
| supersedes_version | bigint | True | — | — |
| reason | text | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.archive_page`

源码：`archives.models.ArchivePage`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| batch_id | uuid | False | — | — |
| ordinal | integer | False | — | — |
| file_page_id | uuid | False | — | — |
| file_id | uuid | False | — | — |
| file_object_version | text | False | — | — |
| checksum | sjia.sha256_hex | False | — | — |
| version | bigint | False | — | — |
| status | text | False | — | — |
| status_version | bigint | False | — | — |
| current_task_id | uuid | True | — | — |
| available_at | timestamp with time zone | False | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.archive_page_attempt`

源码：`archives.models.ArchivePageAttempt`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| page_id | uuid | False | — | — |
| page_version | bigint | False | — | — |
| task_id | uuid | False | — | — |
| task_attempt_id | uuid | False | — | — |
| attempt_number | integer | False | — | — |
| input_sha256 | sjia.sha256_hex | False | — | — |
| model_version | text | False | — | — |
| candidate_set_id | uuid | True | — | — |
| provider_request_id | text | True | — | — |
| started_at | timestamp with time zone | False | — | — |
| finished_at | timestamp with time zone | True | — | — |
| duration_ms | bigint | True | — | — |
| cost | numeric | True | — | — |
| currency | text | True | — | — |
| usage_evidence | jsonb | True | — | — |
| status | text | False | — | — |
| error_code | text | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.archive_regrouping`

源码：`archives.models.ArchiveRegrouping`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| batch_id | uuid | False | — | — |
| actor_id | uuid | False | — | — |
| request_id | uuid | False | — | — |
| input_sha256 | sjia.sha256_hex | False | — | — |
| source_refs | jsonb | True | — | — |
| result_refs | jsonb | True | — | — |
| result_sha256 | sjia.sha256_hex | False | — | — |
| created_at | timestamp with time zone | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'archive_regrouping_request_unique', 'fields': ('actor_id', 'patient_id', 'request_id')})。

ORM 组合索引：无登记。

## `sjia.archive_relation_version`

源码：`archives.models.ArchiveRelationVersion`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| relation_id | uuid | False | — | — |
| version | bigint | False | — | — |
| left_fact_version_id | uuid | True | — | — |
| right_fact_version_id | uuid | True | — | — |
| left_file_id | uuid | True | — | — |
| right_file_id | uuid | True | — | — |
| relation_type | text | True | — | — |
| status | text | True | — | — |
| evidence_ref | text | True | — | — |
| reason | text | True | — | — |
| author_id | uuid | False | — | — |
| created_at | timestamp with time zone | False | — | — |
| verification_id | uuid | True | — | — |
| supersedes_version | bigint | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.assessment`

源码：`assessment.models.Assessment`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| kind | varchar(16) | False | — | classification, ms, hlh |
| assessment_at | timestamp with time zone | True | — | — |
| rule_id | uuid | True | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| window_policy_id | uuid | True | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| task_id | uuid | False | — | — |
| data_epoch | bigint | False | — | — |
| verification_epoch | bigint | False | — | — |
| computation_status | varchar(32) | True | — | not_computed, configuration_pending, insufficient, completed, failed |
| validity | varchar(16) | False | — | valid, invalid |
| missing_items | jsonb | True | — | — |
| computed_at | timestamp with time zone | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'assessment_id_patient_uniq', 'fields': ('id', 'patient_id')})；('django.db.models.CheckConstraint', (), {'name': 'assessment_reference_kind_valid', 'condition': <Q: (AND: ('kind__in', ['classification', 'ms', 'hlh']))>})。

ORM 组合索引：无登记。

## `sjia.assessment_input`

源码：`assessment.models.AssessmentInput`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| assessment_id | uuid | False | — | — |
| patient_id | uuid | False | — | — |
| variable_code | text | True | — | — |
| record_version_id | uuid | False | — | — |
| field_id | uuid | False | — | — |
| verification_id | uuid | False | — | — |
| normalization_id | uuid | True | — | — |
| input_snapshot | jsonb | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'assessment_input_pk_uniq', 'fields': ('assessment_id', 'variable_code')})。

ORM 组合索引：无登记。

## `sjia.assessment_output`

源码：`assessment.models.AssessmentOutput`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| assessment_id | uuid | False | PRIMARY KEY; INDEX; REFERENCES assessment.Assessment.id | — |
| schema_version | text | False | — | — |
| classification_conditions | jsonb | True | — | — |
| classification_result | varchar(16) | True | — | meets, not_meets, insufficient, not_applicable |
| ms_contributions | jsonb | True | — | — |
| ms_total | sjia.finite_decimal | True | — | — |
| ms_threshold_result | boolean | True | — | — |
| hlh_result | jsonb | True | — | — |

ORM 组合约束：('django.db.models.CheckConstraint', (), {'name': 'assessment_hlh_output_separate', 'condition': <Q: (OR: ('hlh_result__isnull', True), (AND: ('classification_conditions__isnull', True), ('classification_result__isnull', True), ('ms_contributions__isnull', True), ('ms_total__isnull', True), ('ms_threshold_result__isnull', True)))>})。

ORM 组合索引：无登记。

## `sjia.audit_event`

源码：`audit.models.AuditEvent`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| actor_id | uuid | True | INDEX; REFERENCES identity.Principal.id | — |
| patient_id | uuid | True | INDEX; REFERENCES identity.Patient.id | — |
| action | text | False | — | — |
| object_type | text | False | — | — |
| object_id | uuid | True | — | — |
| object_version | text | True | — | — |
| request_id | uuid | True | — | — |
| occurred_at | timestamp with time zone | False | — | — |
| outcome | varchar(16) | False | — | allowed, denied, committed, failed |
| reason_code | text | True | — | — |
| retention_policy_id | uuid | False | INDEX; REFERENCES retention.RetentionPolicy.id | — |

ORM 组合约束：无登记。

ORM 组合索引：('django.db.models.Index', (), {'name': 'audit_object', 'fields': ['object_type', 'object_id', '-occurred_at']})；('django.db.models.Index', (), {'name': 'audit_actor_action_time', 'fields': ['actor_id', 'action', '-occurred_at']})；('django.db.models.Index', (), {'name': 'audit_provider_state', 'fields': ['object_id', 'action', '-occurred_at'], 'condition': <Q: (AND: ('object_type', 'provider_config'))>})。

## `sjia.candidate_confirmation`

源码：`recognition.models.CandidateConfirmation`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| candidate_field_id | uuid | False | — | — |
| confirmed_by | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| confirmed_text | text | True | — | — |
| decision | varchar(16) | True | — | accepted, modified, unknown, excluded |
| confirmed_at | timestamp with time zone | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'candidate_confirmation_id_patient_uniq', 'fields': ('id', 'patient_id')})。

ORM 组合索引：无登记。

## `sjia.candidate_field`

源码：`recognition.models.CandidateField`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| candidate_set_id | uuid | False | — | — |
| field_path | text | True | — | — |
| raw_text | text | True | — | — |
| field_config_version | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| proposed_field | jsonb | True | — | — |
| anchor_id | uuid | True | — | — |
| recognition_status | varchar(16) | True | — | recognized, uncertain, missing, unlocated |
| confidence | sjia.finite_decimal | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'candidate_field_id_patient_uniq', 'fields': ('id', 'patient_id')})；('django.db.models.UniqueConstraint', (), {'name': 'candidate_field_set_path_uniq', 'fields': ('candidate_set_id', 'field_path')})。

ORM 组合索引：无登记。

## `sjia.candidate_set`

源码：`recognition.models.CandidateSet`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| file_group_id | uuid | False | — | — |
| file_group_version | bigint | False | — | — |
| task_id | uuid | False | — | — |
| provider | text | False | — | — |
| model_version | text | False | — | — |
| provider_request_id | text | False | — | — |
| schema_version | text | False | — | — |
| raw_response_file_id | uuid | True | — | — |
| status | varchar(32) | False | — | pending_confirmation, confirmed, superseded, cancelled |
| created_at | timestamp with time zone | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'candidate_set_id_patient_uniq', 'fields': ('id', 'patient_id')})。

ORM 组合索引：无登记。

## `sjia.clinical_episode`

源码：`case_details.models.ClinicalEpisode`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| record_version_id | uuid | False | — | — |
| source_field_id | uuid | False | — | — |
| episode_type | varchar(16) | True | — | onset, hospitalization, icu, consultation, transfer, relapse, outpatient, other |
| label | text | True | — | — |
| start_time_id | uuid | True | — | — |
| end_time_id | uuid | True | — | — |
| organization_text | text | True | — | — |
| department_text | text | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'clinical_episode_id_patient_uniq', 'fields': ('id', 'patient_id')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'episode_patient_time', 'fields': ['patient_id', 'start_time_id', 'id']})。

## `sjia.clinical_statement`

源码：`case_details.models.ClinicalStatement`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| record_version_id | uuid | False | — | — |
| source_field_id | uuid | False | — | — |
| statement_type | varchar(24) | True | — | history, physical_exam, diagnosis, differential, tolerance, symptom, laboratory_summary, followup_mode |
| subject | varchar(16) | True | — | patient, family |
| category | text | True | — | — |
| concept_code | text | True | — | — |
| label | text | True | — | — |
| assertion_status | varchar(32) | True | — | present, absent, normal, abnormal, source_diagnosis, historical_consideration, unsupported, requires_differentiation, unknown |
| status_raw_text | text | True | — | — |
| medical_time_id | uuid | True | — | — |
| body_site | text | True | — | — |
| laterality | varchar(16) | True | — | left, right, bilateral, midline |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'clinical_statement_id_patient_uniq', 'fields': ('id', 'patient_id')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'statement_patient_kind', 'fields': ['patient_id', 'statement_type', 'medical_time_id', 'id']})。

## `sjia.config_approval`

源码：`configurations.models.ConfigApproval`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| config_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| approved_by_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| approval_role | text | False | — | — |
| approved_at | timestamp with time zone | False | — | — |
| evidence_ref | text | False | — | — |
| effective_scope | jsonb | False | — | — |
| valid_until | timestamp with time zone | True | — | — |
| revoked_at | timestamp with time zone | True | — | — |
| revocation_reason | text | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.config_validation`

源码：`configurations.models.ConfigValidation`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| config_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| config_checksum | sjia.sha256_hex | False | — | — |
| environment | varchar(64) | False | INDEX; REFERENCES family_content.EnvironmentGate.environment | — |
| artifact_sha256 | sjia.sha256_hex | False | — | — |
| dataset_revision_id | uuid | False | INDEX; REFERENCES sources.SourceRevision.id | — |
| evidence_ref | text | False | — | — |
| evidence_sha256 | sjia.sha256_hex | False | — | — |
| outcome | varchar(8) | False | — | pass, fail |
| executed_by_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| executed_at | timestamp with time zone | False | — | — |
| revoked_at | timestamp with time zone | True | — | — |
| revocation_reason | text | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：('django.db.models.Index', (), {'name': 'config_validation_lookup', 'fields': ['config_id', 'environment', 'executed_at']})。

## `sjia.config_version`

源码：`configurations.models.ConfigVersion`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| config_key | text | False | — | — |
| kind | varchar(16) | False | — | dictionary, mapping, verification, window, rule, visibility, content, summary, notification, provider, validation |
| version | bigint | False | — | — |
| schema_version | text | False | — | — |
| payload | jsonb | False | — | — |
| checksum | sjia.sha256_hex | False | — | — |
| source_revision_id | uuid | True | INDEX; REFERENCES sources.SourceRevision.id | — |
| status | varchar(16) | False | — | draft, pending, approved, enabled, disabled |
| created_by_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| created_at | timestamp with time zone | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'config_version_key_version_uniq', 'fields': ('config_key', 'version')})。

ORM 组合索引：无登记。

## `sjia.consumer_receipt`

源码：`tasks.models.ConsumerReceipt`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| consumer | text | False | — | — |
| event_id | uuid | False | INDEX; REFERENCES tasks.OutboxEvent.id | — |
| processed_at | timestamp with time zone | False | — | — |
| effect_resource_id | uuid | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'consumer_receipt_pk_uniq', 'fields': ('consumer', 'event_id')})。

ORM 组合索引：无登记。

## `sjia.contact_version`

源码：`contacts.models.ContactVersion`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| contact_id | uuid | False | — | — |
| version | bigint | False | — | — |
| contact_name | text | False | — | — |
| purpose | text | False | — | — |
| channel | text | False | — | — |
| channel_value | text | False | — | — |
| owner_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| source_revision_id | uuid | False | INDEX; REFERENCES sources.SourceRevision.id | — |
| approval_config_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| verified_at | timestamp with time zone | True | — | — |
| updated_at | timestamp with time zone | False | — | — |
| valid_until | timestamp with time zone | False | — | — |
| status | varchar(16) | False | — | draft, approved, active, disabled |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'contact_version_contact_id_version_uniq', 'fields': ('contact_id', 'version')})；('django.db.models.CheckConstraint', (), {'name': 'contact_verified_state_requires_time', 'condition': <Q: (OR: (NOT (AND: ('status__in', ['approved', 'active']))), ('verified_at__isnull', False))>})。

ORM 组合索引：无登记。

## `sjia.content_access_revocation`

源码：`retention.models.ContentAccessRevocation`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| plan_id | uuid | False | — | — |
| patient_id | uuid | False | — | — |
| adapter | text | False | — | — |
| object_id | uuid | False | — | — |
| object_version | text | False | — | — |
| revoked_at | timestamp with time zone | False | — | — |
| proof_sha256 | sjia.sha256_hex | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'content_access_object_once', 'fields': ('adapter', 'object_id')})。

ORM 组合索引：无登记。

## `sjia.content_disposition_family_request`

源码：`retention.models.ContentDispositionFamilyRequest`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| requested_by_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| requested_auth_epoch | bigint | False | — | — |
| client_request_id | uuid | False | — | — |
| policy_id | uuid | False | INDEX; REFERENCES retention.ContentDispositionPolicy.id | — |
| policy_version | bigint | False | — | — |
| policy_sha256 | sjia.sha256_hex | False | — | — |
| targets | jsonb | False | — | — |
| request_sha256 | sjia.sha256_hex | False | — | — |
| created_at | timestamp with time zone | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'content_family_request_once', 'fields': ('requested_by', 'client_request_id')})。

ORM 组合索引：无登记。

## `sjia.content_disposition_hold`

源码：`retention.models.ContentDispositionHold`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| source_revision_id | uuid | False | INDEX; REFERENCES sources.SourceRevision.id | — |
| state | text | False | — | — |
| reviewed_by_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| reviewed_at | timestamp with time zone | False | — | — |
| valid_until | timestamp with time zone | False | — | — |
| version | bigint | False | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.content_disposition_plan`

源码：`retention.models.ContentDispositionPlan`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| family_request_id | uuid | True | UNIQUE; INDEX; REFERENCES retention.ContentDispositionFamilyRequest.id | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| policy_id | uuid | False | INDEX; REFERENCES retention.ContentDispositionPolicy.id | — |
| requested_by_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| manifest | jsonb | False | — | — |
| manifest_sha256 | sjia.sha256_hex | False | — | — |
| status | text | False | — | — |
| created_at | timestamp with time zone | False | — | — |
| approved_by_id | uuid | True | INDEX; REFERENCES identity.Principal.id | — |
| approved_at | timestamp with time zone | True | — | — |
| approval | jsonb | True | — | — |
| completed_at | timestamp with time zone | True | — | — |
| journal_sequence | bigint | True | — | — |
| receipt | jsonb | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.content_disposition_policy`

源码：`retention.models.ContentDispositionPolicy`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| version | bigint | False | — | — |
| environment | text | False | — | — |
| rules | jsonb | False | — | — |
| checksum | sjia.sha256_hex | False | — | — |
| source_revision_id | uuid | False | INDEX; REFERENCES sources.SourceRevision.id | — |
| approved_by_id | uuid | True | INDEX; REFERENCES identity.Principal.id | — |
| approved_at | timestamp with time zone | True | — | — |
| enabled | boolean | False | — | — |
| created_at | timestamp with time zone | False | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.content_disposition_review`

源码：`retention.models.ContentDispositionReview`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| plan_id | uuid | False | INDEX; REFERENCES retention.ContentDispositionPlan.id | — |
| version | bigint | False | — | — |
| reviewed_by_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| reviewed_at | timestamp with time zone | False | — | — |
| next_recheck_at | timestamp with time zone | False | — | — |
| source_revision_id | uuid | False | INDEX; REFERENCES sources.SourceRevision.id | — |
| attestation | jsonb | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'content_review_plan_version_once', 'fields': ('plan', 'version')})。

ORM 组合索引：无登记。

## `sjia.content_erasure`

源码：`retention.models.ContentErasure`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| plan_id | uuid | False | — | — |
| patient_id | uuid | False | — | — |
| adapter | text | False | — | — |
| object_id | uuid | False | — | — |
| object_version | text | False | — | — |
| generation | bigint | False | — | — |
| erased_at | timestamp with time zone | False | — | — |
| proof_sha256 | sjia.sha256_hex | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'content_erasure_object_once', 'fields': ('adapter', 'object_id')})。

ORM 组合索引：无登记。

## `sjia.content_restore_state`

源码：`retention.models.ContentRestoreState`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| singleton | boolean | False | UNIQUE | — |
| database_name | text | False | — | — |
| cluster_id | text | False | — | — |
| object_root | text | False | — | — |
| backup_manifest_sha256 | sjia.sha256_hex | False | — | — |
| content_checkpoint | jsonb | True | — | — |
| created_at | timestamp with time zone | False | — | — |

ORM 组合约束：('django.db.models.CheckConstraint', (), {'name': 'content_restore_singleton_true', 'condition': <Q: (AND: ('singleton', True))>})。

ORM 组合索引：无登记。

## `sjia.correction_request`

源码：`verification.models.CorrectionRequest`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| record_version_id | uuid | False | — | — |
| requested_by | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| field_ids | uuid[] | False | — | — |
| reason | text | True | — | — |
| proposed_text | text | True | — | — |
| status | varchar(16) | False | — | pending, in_review, resolved, rejected, withdrawn, superseded |
| version | bigint | False | — | — |
| created_at | timestamp with time zone | False | — | — |
| resolved_by | uuid | True | INDEX; REFERENCES identity.Principal.id | — |
| resolved_at | timestamp with time zone | True | — | — |
| resolution_record_version_id | uuid | True | — | — |
| resolution_reason | text | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：('django.db.models.Index', (), {'name': 'correction_request_scope', 'fields': ['patient_id', 'status', 'created_at', 'id']})。

## `sjia.data_quality_issue`

源码：`case_details.models.DataQualityIssue`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| record_version_id | uuid | False | — | — |
| source_field_id | uuid | False | — | — |
| category | text | True | — | — |
| description | text | True | — | — |
| handling | text | True | — | — |
| status | varchar(16) | True | — | open, needs_review, resolved |
| resolution_text | text | True | — | — |
| resolution_field_id | uuid | True | — | — |
| resolution_record_version_id | uuid | True | — | — |
| resolved_by | uuid | True | INDEX; REFERENCES identity.Principal.id | — |
| resolved_at | timestamp with time zone | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'data_quality_issue_id_patient_uniq', 'fields': ('id', 'patient_id')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'quality_patient_status', 'fields': ['patient_id', 'status', 'id']})。

## `sjia.data_quality_issue_reference`

源码：`case_details.models.DataQualityIssueReference`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| issue_id | uuid | False | — | — |
| field_id | uuid | True | — | — |
| record_version_id | uuid | True | — | — |
| file_id | uuid | True | — | — |
| role | varchar(16) | True | — | source, comparison, affected |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.data_use_authorization`

源码：`sources.models.DataUseAuthorization`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| source_revision_id | uuid | False | INDEX; REFERENCES sources.SourceRevision.id | — |
| purpose | text | False | — | — |
| environment | text | False | — | — |
| evidence_ref | text | False | — | — |
| deidentification_ref | text | True | — | — |
| approved_by_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| approved_at | timestamp with time zone | False | — | — |
| valid_until | timestamp with time zone | True | — | — |
| revoked_at | timestamp with time zone | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'data_use_authorization_id_patient_uniq', 'fields': ('id', 'patient_id')})。

ORM 组合索引：无登记。

## `sjia.delta_summary`

源码：`archives.models.DeltaSummary`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| owner_id | uuid | False | — | — |
| batch_id | uuid | False | — | — |
| baseline_refs | jsonb | True | — | — |
| current_refs | jsonb | True | — | — |
| data_epoch | bigint | False | — | — |
| authorization_version | bigint | False | — | — |
| visibility_policy_id | uuid | False | — | — |
| validity | text | False | — | — |
| generated_at | timestamp with time zone | False | — | — |
| task_id | uuid | False | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.delta_summary_item`

源码：`archives.models.DeltaSummaryItem`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| summary_id | uuid | False | — | — |
| ordinal | integer | False | — | — |
| item_kind | text | True | — | — |
| item_source_refs | jsonb | True | — | — |
| limitations | jsonb | True | — | — |
| content | jsonb | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.dependency_edge`

源码：`dependencies.models.DependencyEdge`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| dependent_id | uuid | False | INDEX; REFERENCES dependencies.DependencyNode.id | — |
| prerequisite_id | uuid | False | INDEX; REFERENCES dependencies.DependencyNode.id | — |
| edge_type | varchar(16) | False | — | source, verification, mapping, policy, input, publication, contact |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'dependency_edge_pk_uniq', 'fields': ('dependent_id', 'prerequisite_id', 'edge_type')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'dependency_reverse', 'fields': ['prerequisite_id', 'dependent_id']})。

## `sjia.dependency_node`

源码：`dependencies.models.DependencyNode`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | True | INDEX; REFERENCES identity.Patient.id | — |
| resource_type | text | False | — | — |
| resource_id | uuid | False | — | — |
| resource_version | text | False | — | — |
| validity | varchar(16) | False | — | valid, invalid |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'dependency_node_resource_version_uniq', 'fields': ('resource_type', 'resource_id', 'resource_version')})。

ORM 组合索引：无登记。

## `sjia.dictionary_alias`

源码：`dictionaries.models.DictionaryAlias`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| entry_id | uuid | False | — | — |
| dictionary_version_id | uuid | False | — | — |
| original_alias | text | False | — | — |
| normalized_alias | text | False | — | — |
| source_revision_id | uuid | False | INDEX; REFERENCES sources.SourceRevision.id | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'dictionary_alias_entry_normalized_uniq', 'fields': ('entry_id', 'normalized_alias')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'dictionary_alias_lookup', 'fields': ['dictionary_version_id', 'normalized_alias']})。

## `sjia.dictionary_entry`

源码：`dictionaries.models.DictionaryEntry`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| dictionary_version_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| concept_code | text | False | — | — |
| category | text | False | — | — |
| label | text | False | — | — |
| definition_ref | text | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'dictionary_entry_version_concept_uniq', 'fields': ('dictionary_version_id', 'concept_code')})；('django.db.models.UniqueConstraint', (), {'name': 'dictionary_entry_id_version_uniq', 'fields': ('id', 'dictionary_version_id')})。

ORM 组合索引：无登记。

## `sjia.disposition_item`

源码：`retention.models.DispositionItem`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| request_id | uuid | False | INDEX; REFERENCES retention.DispositionRequest.id | — |
| target_type | text | False | — | — |
| target_id | text | False | — | — |
| target_version | text | False | — | — |
| status | varchar(32) | False | — | pending, blocked, retained, deleted, failed, awaiting_provider |
| reason | text | True | — | — |
| retention_evidence_ref | text | True | — | — |
| recheck_at | timestamp with time zone | True | — | — |
| executed_by | uuid | True | INDEX; REFERENCES identity.Principal.id | — |
| executed_at | timestamp with time zone | True | — | — |
| receipt_ref | text | True | — | — |
| receipt_sha256 | sjia.sha256_hex | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'disposition_item_target_version_uniq', 'fields': ('request_id', 'target_type', 'target_id', 'target_version')})。

ORM 组合索引：无登记。

## `sjia.disposition_request`

源码：`retention.models.DispositionRequest`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | True | INDEX; REFERENCES identity.Patient.id | — |
| requested_by | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| policy_id | uuid | False | INDEX; REFERENCES retention.RetentionPolicy.id | — |
| evidence_ref | text | False | — | — |
| scope | jsonb | False | — | — |
| status | varchar(16) | False | — | pending, approved, running, blocked, completed, rejected |
| approved_by | uuid | True | INDEX; REFERENCES identity.Principal.id | — |
| approved_at | timestamp with time zone | True | — | — |
| created_at | timestamp with time zone | False | — | — |
| completed_at | timestamp with time zone | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.draft`

源码：`drafts.models.Draft`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| owner_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| form_type | varchar(16) | False | — | observation, report, therapy, correction |
| version | bigint | False | — | — |
| schema_version | text | False | — | — |
| payload | jsonb | True | — | — |
| correction_record_id | uuid | True | — | — |
| correction_version | bigint | True | — | — |
| status | varchar(16) | False | — | editing, submitted, abandoned, expired |
| submitted_request_id | uuid | True | INDEX; REFERENCES requests.WriteRequest.id | — |
| saved_at | timestamp with time zone | False | — | — |
| expires_at | timestamp with time zone | True | — | — |
| retention_policy_id | uuid | False | INDEX; REFERENCES retention.RetentionPolicy.id | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'draft_id_patient_uniq', 'fields': ('id', 'patient_id')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'draft_lookup', 'fields': ['owner_id', 'patient_id', 'form_type', 'status', '-saved_at']})。

## `sjia.environment_gate`

源码：`family_content.models.EnvironmentGate`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| environment | varchar(64) | False | PRIMARY KEY | — |
| version | bigint | False | — | — |
| publication_epoch | bigint | False | — | — |
| enabled | boolean | False | — | — |
| active_release_id | uuid | True | INDEX; REFERENCES family_content.ReleaseBundle.id | — |
| runtime_config_id | uuid | True | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| updated_at | timestamp with time zone | False | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.evaluation_dataset_version`

源码：`archives.models.EvaluationDatasetVersion`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| dataset_id | uuid | False | — | — |
| version | bigint | False | — | — |
| purpose | text | False | — | — |
| environment | text | False | — | — |
| created_by | uuid | False | — | — |
| created_at | timestamp with time zone | False | — | — |
| frozen_at | timestamp with time zone | True | — | — |
| protocol_ref | text | False | — | — |
| coverage | jsonb | False | — | — |
| checksum | sjia.sha256_hex | False | — | — |
| status | text | False | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.evaluation_label`

源码：`archives.models.EvaluationLabel`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| partition_id | uuid | False | — | — |
| observation_period_id | uuid | False | — | — |
| label_state | text | True | — | — |
| reference_answer_ref | text | True | — | — |
| reference_recognition_at | timestamp with time zone | True | — | — |
| confirmed_by | uuid | True | — | — |
| confirmed_at | timestamp with time zone | True | — | — |
| limitations | jsonb | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.evaluation_patient_partition`

源码：`archives.models.EvaluationPatientPartition`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| dataset_version_id | uuid | False | — | — |
| partition | text | False | — | — |
| use_authorization_id | uuid | False | — | — |
| source_refs | jsonb | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.evaluation_run`

源码：`archives.models.EvaluationRun`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| dataset_version_id | uuid | False | — | — |
| operator_id | uuid | False | — | — |
| implementation_sha256 | sjia.sha256_hex | False | — | — |
| rules_refs | jsonb | False | — | — |
| input_refs | jsonb | True | — | — |
| result_refs | jsonb | True | — | — |
| denominators | jsonb | True | — | — |
| limitations | jsonb | True | — | — |
| started_at | timestamp with time zone | False | — | — |
| finished_at | timestamp with time zone | True | — | — |
| status | text | False | — | — |
| mode | text | False | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.external_score_component`

源码：`case_details.models.ExternalScoreComponent`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| score_id | uuid | False | — | — |
| patient_id | uuid | False | — | — |
| record_version_id | uuid | False | — | — |
| field_id | uuid | False | — | — |
| component_code | text | True | — | — |
| ordinal | integer | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'external_score_component_pk_uniq', 'fields': ('score_id', 'component_code')})；('django.db.models.UniqueConstraint', (), {'name': 'external_score_component_score_id_ordinal_uniq', 'fields': ('score_id', 'ordinal')})。

ORM 组合索引：无登记。

## `sjia.external_score_observation`

源码：`case_details.models.ExternalScoreObservation`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| record_version_id | uuid | False | — | — |
| source_field_id | uuid | False | — | — |
| instrument | text | True | — | — |
| instrument_version | text | True | — | — |
| medical_time_id | uuid | False | — | — |
| total_field_id | uuid | False | — | — |
| context_text | text | True | — | — |
| note | text | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'external_score_observation_id_patient_uniq', 'fields': ('id', 'patient_id')})；('django.db.models.UniqueConstraint', (), {'name': 'external_score_observation_id_record_version_id_patient_uniq', 'fields': ('id', 'record_version_id', 'patient_id')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'external_score_patient_time', 'fields': ['patient_id', 'instrument', 'medical_time_id', 'id']})。

## `sjia.field_verification_state`

源码：`verification.models.FieldVerificationState`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| field_id | uuid | False | — | — |
| record_version_id | uuid | False | — | — |
| patient_id | uuid | False | — | — |
| purpose | text | False | — | — |
| version | bigint | False | — | — |
| request_id | uuid | True | — | — |
| current_verification_id | uuid | True | — | — |
| status | varchar(16) | False | — | unverified, pending, verified, needs_correction, revoked |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'field_verification_state_pk_uniq', 'fields': ('field_id', 'purpose')})。

ORM 组合索引：无登记。

## `sjia.file_group`

源码：`files.models.FileGroup`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| owner_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| draft_id | uuid | True | — | — |
| version | bigint | False | — | — |
| status | varchar(16) | False | — | initiated, uploaded, validating, available, failed, superseded, disposed |
| source_revision_id | uuid | False | INDEX; REFERENCES sources.SourceRevision.id | — |
| use_authorization_id | uuid | False | — | — |
| created_at | timestamp with time zone | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'file_group_id_patient_uniq', 'fields': ('id', 'patient_id')})。

ORM 组合索引：无登记。

## `sjia.file_object`

源码：`files.models.FileObject`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| file_group_id | uuid | False | — | — |
| derived_from_id | uuid | True | — | — |
| object_key | text | False | — | — |
| object_version | text | False | — | — |
| checksum | sjia.sha256_hex | False | — | — |
| size_bytes | bigint | False | — | — |
| media_type | text | False | — | — |
| purpose | varchar(16) | False | — | original, thumbnail, deidentified, ocr_response |
| processing_version | text | True | — | — |
| status | varchar(16) | False | — | quarantined, available, failed, disposed |
| retention_policy_id | uuid | False | INDEX; REFERENCES retention.RetentionPolicy.id | — |
| created_at | timestamp with time zone | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'file_object_id_patient_uniq', 'fields': ('id', 'patient_id')})；('django.db.models.UniqueConstraint', (), {'name': 'file_object_key_version_uniq', 'fields': ('object_key', 'object_version')})。

ORM 组合索引：无登记。

## `sjia.file_page`

源码：`files.models.FilePage`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| file_group_id | uuid | False | — | — |
| file_id | uuid | False | — | — |
| ordinal | integer | False | — | — |
| width | integer | False | — | — |
| height | integer | False | — | — |
| rotation | integer | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'file_page_id_patient_uniq', 'fields': ('id', 'patient_id')})；('django.db.models.UniqueConstraint', (), {'name': 'file_page_group_ordinal_uniq', 'fields': ('file_group_id', 'ordinal')})。

ORM 组合索引：无登记。

## `sjia.followup_encounter`

源码：`case_details.models.FollowupEncounter`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| record_version_id | uuid | False | — | — |
| source_field_id | uuid | False | — | — |
| kind | varchar(24) | True | — | baseline_assessment, treatment_assessment, followup_assessment, treatment_response, latest_followup |
| label | text | True | — | — |
| medical_time_id | uuid | False | — | — |
| channel_text | text | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'followup_encounter_id_patient_uniq', 'fields': ('id', 'patient_id')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'followup_patient_time', 'fields': ['patient_id', 'medical_time_id', 'id']})。

## `sjia.followup_reference`

源码：`case_details.models.FollowupReference`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| encounter_id | uuid | False | — | — |
| statement_id | uuid | True | — | — |
| group_id | uuid | True | — | — |
| score_id | uuid | True | — | — |
| medication_event_id | uuid | True | — | — |
| relation_kind | varchar(32) | True | — | outcome, laboratory, score, treatment_context, reported_current_treatment |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.imaging_asset_link`

源码：`case_details.models.ImagingAssetLink`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| record_version_id | uuid | False | — | — |
| source_field_id | uuid | False | — | — |
| file_id | uuid | False | — | — |
| study_id | uuid | True | — | — |
| link_status | varchar(24) | True | — | requires_confirmation, confirmed, rejected |
| evidence_field_id | uuid | True | — | — |
| evidence_record_version_id | uuid | True | — | — |
| confirmed_by | uuid | True | INDEX; REFERENCES identity.Principal.id | — |
| confirmed_at | timestamp with time zone | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'imaging_asset_link_id_patient_uniq', 'fields': ('id', 'patient_id')})；('django.db.models.UniqueConstraint', (), {'name': 'imaging_asset_link_record_version_id_file_id_uniq', 'fields': ('record_version_id', 'file_id')})。

ORM 组合索引：无登记。

## `sjia.imaging_study`

源码：`case_details.models.ImagingStudy`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| record_version_id | uuid | False | — | — |
| source_field_id | uuid | False | — | — |
| report_id | uuid | False | — | — |
| modality | varchar(16) | True | — | CT, MRI, ultrasound, radiograph, other, unknown |
| contrast | varchar(16) | True | — | noncontrast, enhanced |
| study_time_id | uuid | True | — | — |
| external_study_ref | text | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'imaging_study_id_patient_uniq', 'fields': ('id', 'patient_id')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'imaging_patient_time', 'fields': ['patient_id', 'modality', 'study_time_id', 'id']})。

## `sjia.imaging_study_site`

源码：`case_details.models.ImagingStudySite`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| study_id | uuid | False | — | — |
| patient_id | uuid | False | — | — |
| ordinal | integer | False | — | — |
| body_site | text | True | — | — |
| laterality | varchar(16) | True | — | left, right, bilateral, midline |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'imaging_study_site_pk_uniq', 'fields': ('study_id', 'ordinal')})。

ORM 组合索引：无登记。

## `sjia.ingestion_run`

源码：`sources.models.IngestionRun`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| source_revision_id | uuid | False | INDEX; REFERENCES sources.SourceRevision.id | — |
| dataset_version | text | False | — | — |
| checksum | sjia.sha256_hex | False | — | — |
| schema_version | text | False | — | — |
| operator_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| environment | text | False | — | — |
| status | varchar(16) | False | — | validating, blocked, importing, completed, failed |
| started_at | timestamp with time zone | False | — | — |
| finished_at | timestamp with time zone | True | — | — |
| validation_report_ref | text | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'ingestion_run_source_dataset_uniq', 'fields': ('source_revision_id', 'dataset_version')})。

ORM 组合索引：无登记。

## `sjia.material_reminder`

源码：`archives.models.MaterialReminder`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| owner_id | uuid | False | — | — |
| version | bigint | False | — | — |
| target_type | text | False | — | — |
| target_ref | uuid | False | — | — |
| target_version | bigint | False | — | — |
| schedule_at | timestamp with time zone | True | — | — |
| timezone_name | text | False | — | — |
| quiet_start | time | True | — | — |
| quiet_end | time | True | — | — |
| status | text | False | — | — |
| created_at | timestamp with time zone | False | — | — |
| updated_at | timestamp with time zone | False | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.material_reminder_notice`

源码：`archives.models.MaterialReminderNotice`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| reminder_id | uuid | False | — | — |
| reminder_version | bigint | False | — | — |
| scheduled_at | timestamp with time zone | False | — | — |
| channel | text | False | — | — |
| deduplication_key | text | False | — | — |
| status | text | False | — | — |
| permit_refs | jsonb | True | — | — |
| provider_request_id | text | True | — | — |
| receipt_refs | jsonb | True | — | — |
| delivered_at | timestamp with time zone | True | — | — |
| read_at | timestamp with time zone | True | — | — |
| created_at | timestamp with time zone | False | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.medical_time`

源码：`medical_time.models.MedicalTime`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| raw_text | text | True | — | — |
| precision | varchar(16) | True | — | unknown, year, month, month_range, relative, date, minute, second |
| local_year | integer | True | — | — |
| local_month | smallint | True | — | — |
| end_year | integer | True | — | — |
| end_month | smallint | True | — | — |
| relative_text | text | True | — | — |
| local_date | date | True | — | — |
| local_datetime | timestamp | True | — | — |
| timezone_name | text | True | — | — |
| utc_offset_minutes | smallint | True | — | — |
| instant | timestamp with time zone | True | — | — |
| resolution_status | varchar(16) | True | — | unknown, local_only, resolved, ambiguous, invalid |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'medical_time_id_patient_uniq', 'fields': ('id', 'patient_id')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'time_ordering', 'fields': ['patient_id', 'precision', '-instant', 'id']})。

## `sjia.medication_event`

源码：`case_details.models.MedicationEvent`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| record_version_id | uuid | False | — | — |
| source_field_id | uuid | False | — | — |
| action | varchar(24) | True | — | historical_treatment, stopped_by_family, restart, stop, start, regimen_record, taper, dose_route_change, interval_change, ongoing |
| medical_time_id | uuid | True | — | — |
| previous_event_id | uuid | True | — | — |
| reason_statement_id | uuid | True | — | — |
| note | text | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'medication_event_id_patient_uniq', 'fields': ('id', 'patient_id')})；('django.db.models.UniqueConstraint', (), {'name': 'medication_event_id_record_version_id_patient_uniq', 'fields': ('id', 'record_version_id', 'patient_id')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'medication_patient_time', 'fields': ['patient_id', 'medical_time_id', 'id']})。

## `sjia.medication_event_item`

源码：`case_details.models.MedicationEventItem`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| event_id | uuid | False | — | — |
| patient_id | uuid | False | — | — |
| record_version_id | uuid | False | — | — |
| source_field_id | uuid | False | — | — |
| ordinal | integer | False | — | — |
| medication_name | text | True | — | — |
| medication_code | text | True | — | — |
| dose | sjia.finite_decimal | True | — | — |
| dose_unit | text | True | — | — |
| concentration_text | text | True | — | — |
| route_text | text | True | — | — |
| frequency_text | text | True | — | — |
| interval_value | sjia.finite_decimal | True | — | — |
| interval_unit | varchar(16) | True | — | hour, day, week, month |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'medication_event_item_id_patient_uniq', 'fields': ('id', 'patient_id')})；('django.db.models.UniqueConstraint', (), {'name': 'medication_event_item_event_id_ordinal_uniq', 'fields': ('event_id', 'ordinal')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'medication_item_lookup', 'fields': ['patient_id', 'medication_name', 'event_id']})。

## `sjia.normalized_measurement`

源码：`records.models.NormalizedMeasurement`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| field_id | uuid | False | — | — |
| mapping_id | uuid | False | INDEX; REFERENCES dictionaries.UnitMapping.id | — |
| normalized_value | sjia.finite_decimal | True | — | — |
| normalized_unit | text | True | — | — |
| created_at | timestamp with time zone | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'normalized_measurement_id_field_patient_uniq', 'fields': ('id', 'field_id', 'patient_id')})；('django.db.models.UniqueConstraint', (), {'name': 'normalized_measurement_field_mapping_uniq', 'fields': ('field_id', 'mapping_id')})。

ORM 组合索引：无登记。

## `sjia.notification_attempt`

源码：`notifications.models.NotificationAttempt`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| recipient_id | uuid | False | — | — |
| attempt_number | integer | False | — | — |
| send_permit_id | uuid | False | UNIQUE | — |
| provider_idempotency_key | text | False | — | — |
| authorized_at | timestamp with time zone | False | — | — |
| permit_expires_at | timestamp with time zone | False | — | — |
| principal_auth_epoch | bigint | False | — | — |
| relation_auth_epoch | bigint | False | — | — |
| publication_epoch | bigint | False | — | — |
| status | varchar(16) | False | — | authorized, started, accepted, failed, uncertain, cancelled |
| provider_request_id | text | True | — | — |
| started_at | timestamp with time zone | True | — | — |
| finished_at | timestamp with time zone | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'notification_attempt_recipient_id_attempt_number_uniq', 'fields': ('recipient_id', 'attempt_number')})。

ORM 组合索引：无登记。

## `sjia.notification_receipt`

源码：`notifications.models.NotificationReceipt`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| attempt_id | uuid | False | INDEX; REFERENCES notifications.NotificationAttempt.id | — |
| provider | text | False | — | — |
| provider_event_id | text | False | — | — |
| status | varchar(16) | False | — | accepted, delivered, failed |
| occurred_at | timestamp with time zone | False | — | — |
| received_at | timestamp with time zone | False | — | — |
| evidence_sha256 | sjia.sha256_hex | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'notification_receipt_provider_provider_event_id_uniq', 'fields': ('provider', 'provider_event_id')})。

ORM 组合索引：无登记。

## `sjia.observation_period_version`

源码：`archives.models.ObservationPeriodVersion`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| period_id | uuid | False | — | — |
| version | bigint | False | — | — |
| owner_id | uuid | False | — | — |
| start_time_id | uuid | False | — | — |
| end_time_id | uuid | False | — | — |
| label | text | True | — | — |
| label_source | text | True | — | — |
| episode_id | uuid | True | — | — |
| created_at | timestamp with time zone | False | — | — |
| supersedes_version | bigint | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.outbox_event`

源码：`tasks.models.OutboxEvent`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| event_type | text | False | — | — |
| schema_version | text | False | — | — |
| aggregate_id | uuid | False | — | — |
| aggregate_version | bigint | False | — | — |
| occurred_at | timestamp with time zone | False | — | — |
| causation_id | uuid | True | — | — |
| correlation_id | uuid | False | — | — |
| task_id | uuid | True | INDEX; REFERENCES tasks.Task.id | — |
| resource_refs | jsonb | True | — | — |
| traceparent | varchar(55) | False | — | — |
| available_at | timestamp with time zone | False | — | — |
| lease_generation | bigint | False | — | — |
| lease_owner | text | True | — | — |
| lease_until | timestamp with time zone | True | — | — |
| published_at | timestamp with time zone | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：('django.db.models.Index', (), {'name': 'outbox_dispatch', 'fields': ['available_at', 'id'], 'condition': <Q: (AND: ('published_at__isnull', True))>})。

## `sjia.patient`

源码：`identity.models.Patient`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| display_id | text | False | UNIQUE | — |
| data_epoch | bigint | False | — | — |
| verification_epoch | bigint | False | — | — |
| evaluation_sequence | bigint | False | — | — |
| status | varchar(16) | False | — | active, restricted, disposed |
| created_at | timestamp with time zone | False | — | — |
| current_profile_id | uuid | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.patient_profile_version`

源码：`case_details.models.PatientProfileVersion`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| record_version_id | uuid | False | UNIQUE | — |
| source_field_id | uuid | False | — | — |
| sex | varchar(16) | True | — | male, female, other, unknown |
| reported_age_years | integer | True | — | — |
| reported_age_months | smallint | True | — | — |
| age_raw_text | text | True | — | — |
| age_reference_time_id | uuid | True | — | — |
| birth_date | date | True | — | — |
| patient_name | text | True | — | — |
| chief_complaint | text | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'patient_profile_version_id_patient_uniq', 'fields': ('id', 'patient_id')})。

ORM 组合索引：无登记。

## `sjia.personal_record_task`

源码：`archives.models.PersonalRecordTask`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| owner_id | uuid | False | — | — |
| version | bigint | False | — | — |
| title | text | True | — | — |
| description | text | True | — | — |
| status | text | False | — | — |
| retention_policy_id | uuid | False | INDEX; REFERENCES retention.RetentionPolicy.id | — |
| created_at | timestamp with time zone | False | — | — |
| updated_at | timestamp with time zone | False | — | — |
| finished_at | timestamp with time zone | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.principal`

源码：`identity.models.Principal`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| account_status | varchar(16) | False | — | active, disabled, closing, closed |
| auth_epoch | bigint | False | — | — |
| created_at | timestamp with time zone | False | — | — |
| updated_at | timestamp with time zone | False | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.principal_credential`

源码：`identity.models.PrincipalCredential`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| principal_id | uuid | False | UNIQUE; INDEX; REFERENCES identity.Principal.id | — |
| username | text | False | UNIQUE | — |
| password_hash | text | False | — | — |
| created_at | timestamp with time zone | False | — | — |
| updated_at | timestamp with time zone | False | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.principal_identity`

源码：`identity.models.PrincipalIdentity`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| principal_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| provider | text | False | — | — |
| provider_subject | text | False | — | — |
| verified_at | timestamp with time zone | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'principal_identity_provider_subject_uniq', 'fields': ('provider', 'provider_subject')})。

ORM 组合索引：无登记。

## `sjia.procedure_event`

源码：`case_details.models.ProcedureEvent`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| record_version_id | uuid | False | — | — |
| source_field_id | uuid | False | — | — |
| procedure_name | text | True | — | — |
| medical_time_id | uuid | True | — | — |
| related_medication_event_id | uuid | True | — | — |
| body_site | text | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'procedure_event_id_patient_uniq', 'fields': ('id', 'patient_id')})。

ORM 组合索引：无登记。

## `sjia.product_event`

源码：`audit.models.ProductEvent`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| event_code | varchar(16) | False | — | EV-01, EV-02, EV-03, EV-04, EV-05, EV-06, EV-07 |
| request_id | uuid | True | — | — |
| resource_id | uuid | True | — | — |
| resource_version | text | True | — | — |
| outcome | text | False | — | — |
| duration_ms | bigint | True | — | — |
| occurred_at | timestamp with time zone | False | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.purpose_copy`

源码：`archives.models.PurposeCopy`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| original_file_id | uuid | False | — | — |
| derived_file_id | uuid | False | — | — |
| version | bigint | False | — | — |
| purpose | text | False | — | — |
| environment | text | False | — | — |
| use_authorization_id | uuid | False | — | — |
| processing_version | text | False | — | — |
| processing_evidence | jsonb | True | — | — |
| date_strategy | text | False | — | — |
| clinical_integrity_status | text | False | — | — |
| status | text | False | — | — |
| created_by | uuid | False | — | — |
| created_at | timestamp with time zone | False | — | — |
| disposition_ref | uuid | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.purpose_copy_review`

源码：`archives.models.PurposeCopyReview`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| copy_id | uuid | False | — | — |
| copy_version | bigint | False | — | — |
| reviewer_id | uuid | False | — | — |
| reviewed_at | timestamp with time zone | False | — | — |
| evidence_ref | text | True | — | — |
| checked_scopes | jsonb | True | — | — |
| identity_result | text | True | — | — |
| clinical_result | text | True | — | — |
| original_checksum | sjia.sha256_hex | False | — | — |
| derived_checksum | sjia.sha256_hex | False | — | — |
| revoked_at | timestamp with time zone | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.record`

源码：`records.models.Record`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| kind | varchar(16) | False | — | observation, report, therapy, professional |
| current_version | bigint | False | — | — |
| status | varchar(16) | False | — | active, withdrawn, disposed |
| created_at | timestamp with time zone | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'record_id_patient_uniq', 'fields': ('id', 'patient_id')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'record_patient_kind', 'fields': ['patient_id', 'kind', '-created_at', 'id']})。

## `sjia.record_attachment`

源码：`records.models.RecordAttachment`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| record_version_id | uuid | False | — | — |
| patient_id | uuid | False | — | — |
| file_id | uuid | False | — | — |
| ordinal | integer | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'record_attachment_pk_uniq', 'fields': ('record_version_id', 'ordinal')})；('django.db.models.UniqueConstraint', (), {'name': 'record_attachment_record_version_file_uniq', 'fields': ('record_version_id', 'file_id')})。

ORM 组合索引：无登记。

## `sjia.record_field`

源码：`records.models.RecordField`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| record_version_id | uuid | False | — | — |
| ordinal | integer | False | — | — |
| field_code | text | True | — | — |
| concept_code | text | True | — | — |
| dictionary_entry_id | uuid | True | INDEX; REFERENCES dictionaries.DictionaryEntry.id | — |
| original_name | text | True | — | — |
| value_type | varchar(16) | True | — | decimal, categorical, text, time |
| value_state | varchar(16) | True | — | known, unknown, invalid |
| raw_text | text | True | — | — |
| raw_numeric | sjia.finite_decimal | True | — | — |
| categorical_value | text | True | — | — |
| qualifier | varchar(16) | True | — | eq, lt, le, gt, ge |
| raw_unit | text | True | — | — |
| method | text | True | — | — |
| reference_interval | text | True | — | — |
| report_flag | text | True | — | — |
| medical_time_id | uuid | True | — | — |
| sample_time_origin | varchar(16) | True | — | report, item, observation, therapy, unknown |
| anchor_id | uuid | True | — | — |
| confirmation_id | uuid | True | — | — |
| quality_reasons | jsonb | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'record_field_id_patient_uniq', 'fields': ('id', 'patient_id')})；('django.db.models.UniqueConstraint', (), {'name': 'record_field_id_record_version_patient_uniq', 'fields': ('id', 'record_version_id', 'patient_id')})；('django.db.models.UniqueConstraint', (), {'name': 'record_field_record_version_ordinal_uniq', 'fields': ('record_version_id', 'ordinal')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'field_patient_concept', 'fields': ['patient_id', 'concept_code', 'medical_time_id']})。

## `sjia.record_field_group`

源码：`case_details.models.RecordFieldGroup`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| record_version_id | uuid | False | — | — |
| source_field_id | uuid | False | — | — |
| group_type | varchar(16) | True | — | laboratory, vitals, microbiology, physical_exam, other |
| label | text | True | — | — |
| specimen_text | text | True | — | — |
| method_text | text | True | — | — |
| medical_time_id | uuid | True | — | — |
| source_quote | text | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'record_field_group_id_patient_uniq', 'fields': ('id', 'patient_id')})；('django.db.models.UniqueConstraint', (), {'name': 'record_field_group_id_record_version_id_patient_uniq', 'fields': ('id', 'record_version_id', 'patient_id')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'group_patient_kind', 'fields': ['patient_id', 'group_type', 'medical_time_id', 'id']})。

## `sjia.record_field_group_member`

源码：`case_details.models.RecordFieldGroupMember`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| group_id | uuid | False | — | — |
| patient_id | uuid | False | — | — |
| record_version_id | uuid | False | — | — |
| field_id | uuid | False | — | — |
| ordinal | integer | False | — | — |
| role | varchar(16) | True | — | result, specimen, method, interpretation, context |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'record_field_group_member_pk_uniq', 'fields': ('group_id', 'field_id')})；('django.db.models.UniqueConstraint', (), {'name': 'record_field_group_member_group_id_ordinal_uniq', 'fields': ('group_id', 'ordinal')})。

ORM 组合索引：无登记。

## `sjia.record_version`

源码：`records.models.RecordVersion`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| record_id | uuid | False | — | — |
| version | bigint | False | — | — |
| supersedes_version | bigint | True | — | — |
| author_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| origin_type | varchar(32) | False | — | self_report, report_transcription, authorized_clinical |
| capture_method | varchar(32) | False | — | manual, ocr_confirmed, voice_confirmed, authorized_import |
| source_revision_id | uuid | True | INDEX; REFERENCES sources.SourceRevision.id | — |
| source_organization | text | True | — | — |
| report_id | uuid | True | — | — |
| use_authorization_id | uuid | False | — | — |
| quality_status | varchar(16) | True | — | pending, valid, needs_correction |
| quality_reasons | jsonb | True | — | — |
| correction_reason | text | True | — | — |
| corrected_at | timestamp with time zone | True | — | — |
| recorded_at | timestamp with time zone | False | — | — |
| retention_policy_id | uuid | False | INDEX; REFERENCES retention.RetentionPolicy.id | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'record_version_id_patient_uniq', 'fields': ('id', 'patient_id')})；('django.db.models.UniqueConstraint', (), {'name': 'record_version_record_patient_version_uniq', 'fields': ('record_id', 'patient_id', 'version')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'version_recorded', 'fields': ['patient_id', '-recorded_at', 'id']})。

## `sjia.relation`

源码：`relations.models.Relation`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| guardian_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| application_id | uuid | False | INDEX; REFERENCES relations.RelationApplication.id | — |
| status | varchar(16) | False | — | active, revoked, expired |
| version | bigint | False | — | — |
| auth_epoch | bigint | False | — | — |
| checked_by | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| checked_at | timestamp with time zone | False | — | — |
| valid_from | timestamp with time zone | False | — | — |
| valid_until | timestamp with time zone | True | — | — |
| revoked_at | timestamp with time zone | True | — | — |
| reason | text | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'relation_id_patient_uniq', 'fields': ('id', 'patient_id')})；('django.db.models.UniqueConstraint', (), {'name': 'relation_one_active', 'fields': ('patient_id', 'guardian_id'), 'condition': <Q: (AND: ('status', 'active'))>})。

ORM 组合索引：无登记。

## `sjia.relation_application`

源码：`relations.models.RelationApplication`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| version | bigint | False | — | — |
| applicant_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| patient_id | uuid | True | INDEX; REFERENCES identity.Patient.id | — |
| evidence_ref | text | False | — | — |
| applicant_declaration | jsonb | True | — | — |
| note | text | True | — | — |
| requested_access | varchar(24) | True | — | read, read_write, review_decides |
| status | varchar(16) | False | — | pending, approved, rejected, withdrawn |
| checked_by | uuid | True | INDEX; REFERENCES identity.Principal.id | — |
| checked_at | timestamp with time zone | True | — | — |
| reason | text | True | — | — |
| created_at | timestamp with time zone | False | — | — |

ORM 组合约束：('django.db.models.CheckConstraint', (), {'name': 'application_requested_access_valid', 'condition': <Q: (OR: ('requested_access__isnull', True), ('requested_access__in', ['read', 'read_write', 'review_decides']))>})。

ORM 组合索引：无登记。

## `sjia.release_bundle`

源码：`family_content.models.ReleaseBundle`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| environment | text | False | — | — |
| version | bigint | False | — | — |
| publication_epoch | bigint | False | — | — |
| status | varchar(16) | False | — | draft, approved, enabled, disabled |
| contact_version_id | uuid | False | INDEX; REFERENCES contacts.ContactVersion.id | — |
| validation_config_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| activated_at | timestamp with time zone | True | — | — |
| disabled_at | timestamp with time zone | True | — | — |
| valid_until | timestamp with time zone | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'release_bundle_environment_version_uniq', 'fields': ('environment', 'version')})；('django.db.models.UniqueConstraint', (), {'name': 'release_one_enabled', 'fields': ('environment',), 'condition': <Q: (AND: ('status', 'enabled'))>})。

ORM 组合索引：无登记。

## `sjia.release_component`

源码：`family_content.models.ReleaseComponent`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| release_id | uuid | False | INDEX; REFERENCES family_content.ReleaseBundle.id | — |
| component_kind | varchar(16) | False | — | rule, window, verification, mapping, visibility, content, notification |
| config_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'release_component_pk_uniq', 'fields': ('release_id', 'component_kind', 'config_id')})。

ORM 组合索引：无登记。

## `sjia.release_publication_group`

源码：`family_content.models.ReleasePublicationGroup`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| release_id | uuid | False | INDEX; REFERENCES family_content.ReleaseBundle.id | — |
| rule_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| window_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| verification_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| mapping_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| content_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| validation_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'release_publication_rule_unique', 'fields': ('release', 'rule')})。

ORM 组合索引：无登记。

## `sjia.report`

源码：`records.models.Report`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| source_revision_id | uuid | True | INDEX; REFERENCES sources.SourceRevision.id | — |
| external_ref | text | True | — | — |
| source_organization | text | True | — | — |
| report_status | varchar(16) | True | — | unknown, preliminary, final, corrected |
| sample_time_id | uuid | True | — | — |
| result_time_id | uuid | True | — | — |
| file_group_id | uuid | True | — | — |
| replaces_report_id | uuid | True | — | — |
| created_at | timestamp with time zone | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'report_id_patient_uniq', 'fields': ('id', 'patient_id')})。

ORM 组合索引：无登记。

## `sjia.retention_policy`

源码：`retention.models.RetentionPolicy`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| policy_key | text | False | — | — |
| version | bigint | False | — | — |
| data_class | text | False | — | — |
| rules | jsonb | False | — | — |
| schema_version | text | False | — | — |
| status | varchar(16) | False | — | draft, approved, active, disabled |
| approved_by_id | uuid | True | INDEX; REFERENCES identity.Principal.id | — |
| approved_at | timestamp with time zone | True | — | — |
| evidence_ref | text | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'retention_policy_key_version_uniq', 'fields': ('policy_key', 'version')})。

ORM 组合索引：无登记。

## `sjia.service_role_grant`

源码：`authorization.models.ServiceRoleGrant`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| principal_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| environment | varchar(64) | False | INDEX; REFERENCES family_content.EnvironmentGate.environment | — |
| action | varchar(64) | False | — | — |
| granted_by | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| evidence_ref | text | False | — | — |
| valid_from | timestamp with time zone | False | — | — |
| valid_until | timestamp with time zone | True | — | — |
| revoked_at | timestamp with time zone | True | — | — |
| revoked_by | uuid | True | INDEX; REFERENCES identity.Principal.id | — |
| revocation_reason | text | True | — | — |

ORM 组合约束：无登记。

ORM 组合索引：('django.db.models.Index', (), {'name': 'service_role_lookup', 'fields': ['principal_id', 'environment', 'action']})。

## `sjia.simulated_provider_copy`

源码：`retention.models.SimulatedProviderCopy`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| purpose_copy_id | uuid | False | INDEX; REFERENCES archives.PurposeCopy.id | — |
| purpose_copy_version | bigint | False | — | — |
| provider_id | text | False | — | — |
| object_id | uuid | False | — | — |
| object_version | text | False | — | — |
| checksum | sjia.sha256_hex | False | — | — |
| size_bytes | bigint | False | — | — |
| environment | text | False | — | — |
| status | text | False | — | — |
| deletion_request_id | uuid | True | — | — |
| receipt_ref | text | True | — | — |
| receipt_sha256 | sjia.sha256_hex | True | — | — |
| created_by | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| created_at | timestamp with time zone | False | — | — |
| deleted_at | timestamp with time zone | True | — | — |
| processing_evidence | jsonb | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'sim_provider_object_version_uniq', 'fields': ('provider_id', 'object_id', 'object_version')})；('django.db.models.UniqueConstraint', (), {'name': 'sim_provider_purpose_copy_uniq', 'fields': ('purpose_copy', 'purpose_copy_version', 'provider_id')})。

ORM 组合索引：无登记。

## `sjia.source_anchor`

源码：`recognition.models.SourceAnchor`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| page_id | uuid | False | — | — |
| anchor_kind | varchar(16) | True | — | page, bbox |
| coordinate_system | varchar(16) | True | — | decoded_pixel |
| x | sjia.finite_decimal | True | — | — |
| y | sjia.finite_decimal | True | — | — |
| width | sjia.finite_decimal | True | — | — |
| height | sjia.finite_decimal | True | — | — |
| row_label | text | True | — | — |
| column_label | text | True | — | — |
| original_text | text | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'source_anchor_id_patient_uniq', 'fields': ('id', 'patient_id')})。

ORM 组合索引：无登记。

## `sjia.source_registry`

源码：`sources.models.SourceRegistry`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| source_key | text | False | UNIQUE | — |
| owner | text | False | — | — |
| location | text | False | — | — |
| purpose | text | False | — | — |
| sensitivity | text | False | — | — |
| update_frequency | text | False | — | — |
| status | varchar(16) | False | — | pending, approved, disabled |

ORM 组合约束：无登记。

ORM 组合索引：无登记。

## `sjia.source_revision`

源码：`sources.models.SourceRevision`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| source_id | uuid | False | INDEX; REFERENCES sources.SourceRegistry.id | — |
| version | text | False | — | — |
| checksum | sjia.sha256_hex | False | — | — |
| license | text | False | — | — |
| allowed_use | jsonb | False | — | — |
| rights_evidence_ref | text | True | — | — |
| retention_policy_id | uuid | True | INDEX; REFERENCES retention.RetentionPolicy.id | — |
| captured_at | timestamp with time zone | False | — | — |
| status | varchar(16) | False | — | pending, approved, revoked |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'source_revision_source_version_uniq', 'fields': ('source_id', 'version')})。

ORM 组合索引：无登记。

## `sjia.statement_evidence`

源码：`case_details.models.StatementEvidence`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| statement_id | uuid | False | — | — |
| patient_id | uuid | False | — | — |
| evidence_field_id | uuid | False | — | — |
| evidence_record_version_id | uuid | False | — | — |
| role | varchar(16) | True | — | supports, contradicts, context |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'statement_evidence_pk_uniq', 'fields': ('statement_id', 'evidence_field_id', 'role')})。

ORM 组合索引：无登记。

## `sjia.summary`

源码：`summaries.models.Summary`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| requested_by | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| from_date | date | True | — | — |
| to_date | date | True | — | — |
| scope_timezone | text | True | — | — |
| include_unknown_time | boolean | True | — | — |
| template_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| visibility_policy_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| data_epoch | bigint | False | — | — |
| verification_epoch | bigint | False | — | — |
| validity | varchar(16) | False | — | valid, invalid |
| task_id | uuid | False | — | — |
| generated_at | timestamp with time zone | False | — | — |
| execution_snapshot | jsonb | True | — | — |
| execution_sha256 | varchar(64) | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'summary_id_patient_uniq', 'fields': ('id', 'patient_id')})。

ORM 组合索引：无登记。

## `sjia.summary_item`

源码：`summaries.models.SummaryItem`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| summary_id | uuid | False | — | — |
| patient_id | uuid | False | — | — |
| record_version_id | uuid | False | — | — |
| field_id | uuid | False | — | — |
| ordinal | integer | False | — | — |
| snapshot | jsonb | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'summary_item_pk_uniq', 'fields': ('summary_id', 'ordinal')})；('django.db.models.UniqueConstraint', (), {'name': 'summary_item_summary_id_field_id_uniq', 'fields': ('summary_id', 'field_id')})。

ORM 组合索引：无登记。

## `sjia.task`

源码：`tasks.models.Task`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | True | INDEX; REFERENCES identity.Patient.id | — |
| actor_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| request_row_id | uuid | True | INDEX; REFERENCES requests.WriteRequest.id | — |
| task_type | varchar(16) | False | — | file_validation, purpose_copy, ocr, assessment, alert_evaluation, summary, notification, disposition, reconcile |
| schema_version | text | False | — | — |
| input_refs | jsonb | True | — | — |
| input_sha256 | sjia.sha256_hex | False | — | — |
| status | varchar(32) | False | — | accepted, blocked, queued, running, awaiting_confirmation, completed, failed, uncertain, awaiting_resolution, cancel_requested, cancelled, superseded |
| status_version | bigint | False | — | — |
| lease_generation | bigint | False | — | — |
| lease_owner | text | True | — | — |
| lease_until | timestamp with time zone | True | — | — |
| created_at | timestamp with time zone | False | — | — |
| deadline_at | timestamp with time zone | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'task_id_patient_uniq', 'fields': ('id', 'patient_id')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'task_pending', 'fields': ['status', 'deadline_at', 'id'], 'condition': <Q: (NOT (AND: ('status__in', ['completed', 'failed', 'cancelled', 'superseded'])))>})。

## `sjia.task_attempt`

源码：`tasks.models.TaskAttempt`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| task_id | uuid | False | INDEX; REFERENCES tasks.Task.id | — |
| attempt_number | integer | False | — | — |
| lease_generation | bigint | False | — | — |
| provider_request_id | text | True | — | — |
| started_at | timestamp with time zone | False | — | — |
| finished_at | timestamp with time zone | True | — | — |
| error_code | text | True | — | — |
| status | varchar(16) | False | — | running, completed, failed, uncertain, superseded, cancelled |
| usage_evidence | jsonb | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'task_attempt_task_id_attempt_number_uniq', 'fields': ('task_id', 'attempt_number')})。

ORM 组合索引：无登记。

## `sjia.transcription_receipt`

源码：`recognition.voice_models.TranscriptionReceipt`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| actor_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| auth_epoch | bigint | False | — | — |
| use_authorization_id | uuid | False | INDEX; REFERENCES sources.DataUseAuthorization.id | — |
| retention_policy_id | uuid | False | INDEX; REFERENCES retention.RetentionPolicy.id | — |
| audio_sha256 | sjia.sha256_hex | False | — | — |
| audio_media_type | varchar(128) | False | — | — |
| audio_size_bytes | bigint | False | — | — |
| duration_seconds | sjia.finite_decimal | False | — | — |
| inference_duration_ms | bigint | False | — | — |
| model_name | varchar(256) | False | — | — |
| implementation_version | varchar(256) | False | — | — |
| transcript | text | True | — | — |
| transcript_sha256 | sjia.sha256_hex | False | — | — |
| confirmed_text | text | True | — | — |
| confirmed_text_sha256 | sjia.sha256_hex | True | — | — |
| confirmed_original_file_id | uuid | True | INDEX; REFERENCES files.FileObject.id | — |
| status | varchar(32) | False | — | — |
| status_version | bigint | False | — | — |
| created_at | timestamp with time zone | False | — | — |
| confirmed_at | timestamp with time zone | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'transcription_receipt_patient_uniq', 'fields': ('id', 'patient')})；('django.db.models.CheckConstraint', (), {'name': 'transcription_receipt_status_check', 'condition': <Q: (AND: ('status__in', ['completed', 'confirmed', 'revoked']))>})。

ORM 组合索引：无登记。

## `sjia.unit_mapping`

源码：`dictionaries.models.UnitMapping`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| config_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| concept_code | text | False | — | — |
| source_revision_id | uuid | False | INDEX; REFERENCES sources.SourceRevision.id | — |
| raw_unit | text | False | — | — |
| normalized_unit | text | False | — | — |
| method_scope | text | False | — | — |
| multiplier | sjia.finite_decimal | False | — | — |
| additive | sjia.finite_decimal | False | — | — |
| exact_values_only | boolean | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'unit_mapping_key_uniq', 'fields': ('config_id', 'concept_code', 'source_revision_id', 'raw_unit', 'method_scope')})。

ORM 组合索引：无登记。

## `sjia.upload_part`

源码：`files.models.UploadPart`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| file_group_id | uuid | False | — | — |
| client_upload_id | uuid | False | — | — |
| ordinal | integer | False | — | — |
| declared_checksum | sjia.sha256_hex | False | — | — |
| declared_size_bytes | bigint | False | — | — |
| declared_media_type | varchar(128) | False | — | — |
| status | varchar(16) | False | — | declared, receiving, stored, failed, uncertain |
| object_key | text | False | UNIQUE | — |
| object_version | text | True | — | — |
| file_id | uuid | True | — | — |
| intent_id | uuid | True | INDEX; REFERENCES requests.WriteIntent.id | — |
| created_at | timestamp with time zone | False | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'upload_part_group_ordinal_uniq', 'fields': ('file_group_id', 'ordinal')})；('django.db.models.UniqueConstraint', (), {'name': 'upload_part_group_client_upload_uniq', 'fields': ('file_group_id', 'client_upload_id')})。

ORM 组合索引：无登记。

## `sjia.verification`

源码：`verification.models.Verification`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| record_version_id | uuid | False | — | — |
| verifier_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| policy_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| purpose | text | False | — | — |
| evidence_ref | text | True | — | — |
| evidence_source_id | uuid | False | INDEX; REFERENCES sources.SourceRevision.id | — |
| verified_at | timestamp with time zone | False | — | — |
| revoked_at | timestamp with time zone | True | — | — |
| revoked_by | uuid | True | INDEX; REFERENCES identity.Principal.id | — |
| revocation_reason | text | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'verification_id_record_version_patient_uniq', 'fields': ('id', 'record_version_id', 'patient_id')})。

ORM 组合索引：无登记。

## `sjia.verification_field`

源码：`verification.models.VerificationField`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| verification_id | uuid | False | — | — |
| record_version_id | uuid | False | — | — |
| patient_id | uuid | False | — | — |
| field_id | uuid | False | — | — |
| result | varchar(16) | True | — | consistent, needs_correction, uncertain |
| reason | text | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'verification_field_pk_uniq', 'fields': ('verification_id', 'field_id')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'verification_field_reverse', 'fields': ['field_id', 'verification_id']})。

## `sjia.verification_request`

源码：`verification.models.VerificationRequest`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | — | — |
| record_version_id | uuid | False | — | — |
| requested_by | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| assigned_to | uuid | True | INDEX; REFERENCES identity.Principal.id | — |
| purpose | text | False | — | — |
| status | varchar(16) | False | — | pending, in_review, completed, cancelled, superseded |
| created_at | timestamp with time zone | False | — | — |
| completed_at | timestamp with time zone | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'verification_request_id_record_version_patient_uniq', 'fields': ('id', 'record_version_id', 'patient_id')})。

ORM 组合索引：无登记。

## `sjia.voice_input`

源码：`recognition.voice_models.VoiceInput`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| patient_id | uuid | False | INDEX; REFERENCES identity.Patient.id | — |
| actor_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| request_id | uuid | False | — | — |
| transcript | text | True | — | — |
| transcript_sha256 | sjia.sha256_hex | False | — | — |
| transcription_evidence | jsonb | True | — | — |
| input_mode | varchar(32) | False | — | — |
| original_file_id | uuid | False | INDEX; REFERENCES files.FileObject.id | — |
| purpose_copy_id | uuid | True | INDEX; REFERENCES archives.PurposeCopy.id | — |
| use_authorization_id | uuid | False | INDEX; REFERENCES sources.DataUseAuthorization.id | — |
| task_id | uuid | True | INDEX; REFERENCES tasks.Task.id | — |
| provider_config_id | uuid | True | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| field_config_id | uuid | False | INDEX; REFERENCES configurations.ConfigVersion.id | — |
| status | varchar(32) | False | — | — |
| status_version | bigint | False | — | — |
| candidates | jsonb | True | — | — |
| raw_response_file_id | uuid | True | INDEX; REFERENCES files.FileObject.id | — |
| decisions | jsonb | True | — | — |
| saved_record_version_id | uuid | True | INDEX; REFERENCES records.RecordVersion.id | — |
| confirmed_at | timestamp with time zone | False | — | — |
| created_at | timestamp with time zone | False | — | — |
| decided_at | timestamp with time zone | True | — | — |
| cancelled_at | timestamp with time zone | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'voice_input_actor_request_uniq', 'fields': ('actor', 'request_id')})；('django.db.models.UniqueConstraint', (), {'name': 'voice_input_id_patient_uniq', 'fields': ('id', 'patient')})；('django.db.models.CheckConstraint', (), {'name': 'voice_input_mode_check', 'condition': <Q: (AND: ('input_mode__in', ['LOCAL_AUDIO_CONFIRMED_TEXT', 'SIMULATION_TEXT_ONLY']))>})；('django.db.models.CheckConstraint', (), {'name': 'voice_input_status_check', 'condition': <Q: (AND: ('status__in', ['awaiting_review', 'ready', 'running', 'awaiting_confirmation', 'saved', 'failed', 'uncertain', 'cancelled', 'revoked']))>})。

ORM 组合索引：无登记。

## `sjia.write_intent`

源码：`requests.models.WriteIntent`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| actor_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| patient_id | uuid | True | INDEX; REFERENCES identity.Patient.id | — |
| operation_id | varchar(80) | False | — | — |
| target_key | varchar(256) | False | — | — |
| request_id | uuid | False | — | — |
| payload_sha256 | sjia.sha256_hex | False | — | — |
| normalization_version | varchar(32) | False | — | — |
| generation | bigint | False | — | — |
| status | varchar(16) | False | — | prepared, committed, failed |
| prepared_at | timestamp with time zone | False | — | — |
| expires_at | timestamp with time zone | False | — | — |
| completed_at | timestamp with time zone | True | — | — |
| write_request_id | uuid | True | INDEX; REFERENCES requests.WriteRequest.id | — |
| error_code | varchar(64) | True | — | — |
| authorization_context | jsonb | True | — | — |
| recovery_payload | jsonb | True | — | — |
| retention_policy_id | uuid | False | INDEX; REFERENCES retention.RetentionPolicy.id | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'write_intent_actor_operation_target_request_uniq', 'fields': ('actor_id', 'operation_id', 'target_key', 'request_id')})。

ORM 组合索引：('django.db.models.Index', (), {'name': 'write_intent_expiry', 'fields': ['expires_at', 'id'], 'condition': <Q: (AND: ('status', 'prepared'))>})；('django.db.models.Index', (), {'name': 'write_intent_owner', 'fields': ['actor_id', 'patient_id', '-prepared_at', 'id']})。

## `sjia.write_request`

源码：`requests.models.WriteRequest`。

| 字段名 | PostgreSQL 类型 | 允许 NULL | ORM 约束与关联 | 枚举 |
| --- | --- | --- | --- | --- |
| content_erased_at | timestamp with time zone | True | — | — |
| content_disposition_id | uuid | True | — | — |
| id | uuid | False | PRIMARY KEY | — |
| actor_id | uuid | False | INDEX; REFERENCES identity.Principal.id | — |
| patient_id | uuid | True | INDEX; REFERENCES identity.Patient.id | — |
| action | text | False | — | — |
| target_key | text | False | — | — |
| request_id | uuid | False | — | — |
| payload_sha256 | sjia.sha256_hex | False | — | — |
| normalization_version | text | False | — | — |
| expected_version | bigint | True | — | — |
| status | varchar(16) | False | — | processing, committed, failed, uncertain |
| resource_type | text | True | — | — |
| resource_id | uuid | True | — | — |
| error_code | text | True | — | — |
| created_at | timestamp with time zone | False | — | — |
| completed_at | timestamp with time zone | True | — | — |
| expires_at | timestamp with time zone | True | — | — |

ORM 组合约束：('django.db.models.UniqueConstraint', (), {'name': 'write_request_actor_action_target_request_uniq', 'fields': ('actor_id', 'action', 'target_key', 'request_id')})。

ORM 组合索引：无登记。
