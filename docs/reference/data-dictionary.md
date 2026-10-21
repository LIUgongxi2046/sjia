# 数据字典与来源关系

doc_id：SJIA-DATA-DICTIONARY；status：CURRENT；last_verified：2026-10-21（Asia/Shanghai）。适用源码：`b094448d40d53ba675dd5e4a278f3e2deb107a43` 及当前文档。维护责任：数据模型或正式 migrations 变更时同步核查。

## 字段与数据库结构

| 资料 | 内容与使用方法 |
| --- | --- |
| [当前 ORM 数据字典](orm-data-dictionary.md) | 从当前 Django Model 生成的物理字段名、PostgreSQL 类型、NULL、枚举、关联与 ORM 组合约束 |
| [基础 SQL](../../data-design/schema.sql)、[病例 SQL](../../data-design/case-schema.sql)、[新增 SQL](../../data-design/v2-schema.sql) | 基础领域、病例领域与档案事实领域的设计结构 |
| [新增数据规范](../../data-design/v2-data-specification.md)、[数据 LLD](../../sJIA合并MAS预警平台数据LLD.md) | FD-01—FD-71 的业务含义与使用关系 |
| `backend/*/migrations/` | 部署时执行的正式迁移，包含后续字段、原生 SQL、复合外键、DOMAIN、视图及触发器 |
| [QA28](../development/QA28-target-runtime-compatibility.md) | 2026-10-19 目标版本空库迁移与数据库目录逐项比较证据 |

字典中的 `patient_id`、`record_version_id`、`field_id` 保持数据库名称。API 字段以对应响应 Schema 为准。Django Model 中的 UUID 字段不一定声明 ORM ForeignKey，SQL 中的同患儿复合外键仍需查阅正式 migrations。

当前 ORM 字典包含 128 个模型、1525 个字段，源码中共有 105 项正式迁移。S3StoredObject、S3ContentRestoreState、恢复引用及准确存储结构见正式 migrations 与 [S3 数据规范](../../data-design/s3-storage-registration.md)。

## 主要实体与语义

| 实体 | 关键字段 | 含义与使用方 | 关联记录 |
| --- | --- | --- | --- |
| `sjia.record`、`sjia.record_version` | `current_version`、`version`、`supersedes_version`、`origin_type`、`capture_method` | 稳定记录身份与不可变历史版本；家庭记录、历史和更正共同引用 | [T16](../development/T16.md)、[T24](../development/T24.md) |
| `sjia.record_field` | `field_code`、`raw_text`、`raw_numeric`、`qualifier`、`raw_unit`、`medical_time_id` | 原文、数值、比较符、单位与医学时间的字段来源；API 十进制交换保留准确字符串 | [T41](../development/T41.md)、[T49](../development/T49.md) |
| `sjia.file_object`、`sjia.file_page` | `object_key`、`object_version`、`checksum`、`size_bytes`、`purpose` | 不可变文件字节与准确原页；Range 请求仍核验完整原件权限 | [T12](../development/T12.md)、[QA29](../development/QA29-real-original-range-reads.md) |
| `sjia.archive_document`、`sjia.archive_document_version` | `current_version`、`document_type`、`proposed_metadata`、`grouping_status` | 文书边界、分类和就诊说明；本人归组与专业核对分别保存 | [T43](../development/T43.md)、[QA22](../development/QA22-real-native-pdf-document.md) |
| `sjia.archive_fact`、`sjia.archive_fact_version` | `field_id`、`context_type`、`assertion_state`、`available_at`、`availability_status` | 每个事实版本引用准确 RecordField；本人确认不自动产生专业证明 | [T41](../development/T41.md)、[QA12](../development/QA12-real-ocr-validation.md) |
| `sjia.archive_relation_version` | `relation_type`、`version`、`status`、`verification_id` | 原件、文书、采样与更正关系；解除关系继续保留两侧资料与历史 | [T44](../development/T44.md)、[QA23](../development/QA23-real-duplicate-files.md) |
| `sjia.observation_period_version` | `period_id`、`start_time_id`、`end_time_id`、`label_source`、`supersedes_version` | 本人的查询范围与历史；范围边界不能替代来源中的医学时间 | [T47](../development/T47.md)、[QA21](../development/QA21-real-observation-period-save.md) |
| `sjia.delta_summary`、`sjia.delta_summary_item` | `baseline_refs`、`current_refs`、`item_source_refs`、`validity`、`content` | 新增内容整理、准确输入与失效状态；缺少比较基准时不形成临床变化结论 | [T46](../development/T46.md)、[QA19](../development/QA19-real-archive-delta.md) |
| `sjia.evaluation_dataset_version`、`sjia.evaluation_label`、`sjia.evaluation_run` | `purpose`、`partition_id`、`reference_answer_ref`、`denominators`、`implementation_sha256` | 用途、独立标签、固定集合与统计分母；文件分组数量不表示患者数量 | [T48](../development/T48.md)、[T59](../development/T59.md)、[QA25](../development/QA25-real-evaluation-source-inventory.md) |
| `sjia.material_reminder`、`sjia.material_reminder_notice` | `target_ref`、`schedule_at`、`timezone_name`、`quiet_start`、`status`、`delivered_at`、`read_at` | 提醒设置、实际送达和本人阅读分别保存 | [T53](../development/T53.md)、[QA15](../development/QA15-real-material-reminders.md) |

字段引用以 ORM 字典为准，业务关系还需结合原生 SQL 与领域服务核验。`available_at` 表示平台取得准确来源版本的时间，`recorded_at` 表示系统登记时间；未知时间保持未知。`label_source=self_report` 表示本人说明。本人操作、专业核对、临床评估与家庭发布资格各自具有独立依据。

## 知识与检索

当前知识来自结构化规则、字典、单位映射、来源批准与配置证据。当前工程没有使用 VectorDB collection、embedding 或 RAG Chunking；相关内容见 [Agent 设计说明](../../sJIA合并MAS预警平台Agent设计说明.md)。文书检索、来源和范围由现有 PostgreSQL 查询、权限与版本控制实现。

## 验证边界

QA28 在 Python 3.13.13／PostgreSQL 17.11 上完成 102 项正式迁移，比较 135 张表、1554 个字段、940 项约束、522 个索引、81 个触发器、33 个函数及两个 DOMAIN。上述数量来自该次数据库目录检查。ORM 字典仅描述当前源码元数据，不能代替数据库迁移、真实备份恢复或临床验收。
