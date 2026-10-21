# PRD 2.0 新增数据规范

日期：2026-10-20。版本：DATA V2.0。能力：DESIGN。维护责任：T35。上游为PRD V2.0.0及当前HLD V2.0；物理规范为[v2-schema.sql](v2-schema.sql)，当前完整模型变更通过Django migrations执行。71个字段组的实际持久字段与使用入口由[field-ownership.json](field-ownership.json)维护，原有语义由[数据LLD](../sJIA合并MAS预警平台数据LLD.md)第4.4节接收。

## 字段、实体与使用方

新增表使用 UUID，患儿对象具有 patient_id，引用采用同患儿复合外键，删除使用 RESTRICT。版本内容不可变，当前指针或最大有效版本在事务中读取。跨表资格在领域写入位置核验。字符串未知为 null 或明确 unknown；时间使用 MedicalTime 的原文、精度和时区，十进制保持 numeric 与字符串交换。

| 字段 | 持久实体、字段与约束 | 生产方 → 使用方／验收 |
| --- | --- | --- |
| FD-58 | archive 每名患儿一个；archive_batch 的 submitted_by、received_at、logical_request_id、quota_policy_id、version | T42 实际接收 → 批次和目录／GRAC-02 |
| FD-59 | archive_document／version、archive_page、archive_document_page；页序、FileObject 准确版本及 checksum；文书当前指针延迟外键 | T42／T43 → 候选、详情、摘要／GRAC-01／02／03 |
| FD-60 | document_type 八类、organization 可空、proposed_metadata、grouping_status、confirmed_by／at；document_episode 多对多 | T43 分类和本人确认 → T45 目录、T49 选值／GRAC-03 |
| FD-61 | archive_fact／version 的 RecordField 精确引用、source_anchor、context_type、assertion_state、episode_id；field_id 唯一 | T41 本人采用 → 核对、归组、资格／GRAC-01 |
| FD-62 | fact_version 的 medical_time_id、raw_time_text、time_origin、documented_time_id、available_at、availability_status；MedicalTime 精度及时区，Verification 的专业时间分别保留 | T41 逐项确认 → 时间轴、摘要及 replay／GRAC-01／07 |
| FD-63 | archive_page_attempt 的 page_version、Task／TaskAttempt、模型、Provider 请求、candidate_set、duration_ms、cost、currency、usage_evidence | T42 Worker → 逐页状态及预算／GRAC-02 |
| FD-64 | archive_relation_version 的两个事实或文件、五类关系、version、verification、status、evidence、reason、author 及历史 | T44 整理或专业核对 → 去重、趋势、摘要／GRAC-04 |
| FD-65 | delta_summary／item 的 baseline_refs、current_refs、item_source_refs、limitations、task、policy、epochs、validity | T46 结构化比较 → 批次反馈、记录／GRAC-06 |
| FD-66 | observation_period_version 的 start／end MedicalTime、owner、label_source、episode 及历史 | T47 本人设置 → 查询和评估窗口／GRAC-07 |
| FD-67 | evaluation_dataset_version、patient_partition、label、run；独立答案、用途、观察期、固定集合、覆盖、分母、运行版本分别保存 | T48 受控清单 → T55 格式评价及 T59 临床统计／GRAC-08 |
| FD-68 | purpose_copy／review 的原件、副本、用途、环境、处理、校验和、reviewer、scopes、identity_result、clinical_result、disposition | T40 获准处理 → 识别、评价、展示和获准导出／GRAC-09 |
| FD-69 | 既有 AlertEvaluation／Input／SignalResult、Assessment／Input；fact refs、available_at、verification、known_at、assessment_at、epochs、sequence、mode | T49／T50 受控评估 → 当前提示或独立 replay／AC-DYN-01—05 |
| FD-70 | material_reminder／notice 的 owner、target_ref／version、schedule_at、timezone、quiet_start／end、status、permit_refs、真实送达与 read_at | T53 本人设置 → 到期调度和本人任务／GRAC-10 |
| FD-71 | archive_batch 的 quota_policy_id、budget_status；archive_page 状态聚合；attempt 汇总实际尝试和费用 | T42 → 家庭进度及 T57 容量／NFR-14、NFR-15 |

每个事实引用既有 RecordField，数值和单位只有该字段为权威来源。医学时间、报告出具、文书形成、平台取得、系统登记和专业核对分别保存。available_at 表示平台取得准确来源版本的时间；旧对象没有证据则 availability_status=unknown。共同时间仅用于明确选择且原文支持的项目，已有明确不同时间保持。日期及月份精度按当地完整区间处理，无法严格排序或不完整满足窗口时不得用于对应趋势。计划、既往、否定或未知不映射为实际专业状态。

事实读取使用 FactFieldSnapshot 表达历史质量原因缺少状态。quality_reasons_snapshot_state=not_recorded 仅适用于 value_state=unknown／invalid 且保存原因集合为空的字段，原始空集合保持。clinicalFieldBase 维护全部原值、字段、来源及时间约束；新资料输入和资料交换继续由 clinicalField 要求未知或无效字段具有质量原因。读取的缺少标记不改变数据库内容或临床资格。

DocumentPage 保存文书边界和页序；事实 page／anchor 必须属于该 document_version，完整出处在写入位置核验。一个文书允许多个就诊。已有 ClinicalEpisode 依赖专业来源字段；本人归组候选存于 proposed_metadata，待核对归属保持未知并可以查找；没有专业依据不自行创建已核对 ClinicalEpisode。事实归组、文书归组和专业资格分别保存。

fact_id 是稳定业务身份，fact_version 绑定一个记录字段。RecordVersion 更正产生新字段及新 FactVersion，通过 supersedes_version 保留关系；旧专业证明仅继续指向原字段。手工观察也有事实引用，document／page 可空，来源由原始记录管理；上传文书事实必须有页和原文定位。

same_bytes、document_copy、same_sampling、independent_sampling、formal_correction 分别表示字节、文书、采样和更正关系。数值相同没有同采样资格，相似度只能产生候选。relation_id 的最高版本为当前判断；解除形成 released 新版本，不删除两侧原件。专业采样关系必须有准确报告、时间、项目和核对证据；未决继续保留冲突。

## 持久状态、事务与数量

页对象绑定不可变文件版本和校验和，替换创建新页，旧页 superseded。completed attempt 必须有真实 candidate_set；archived 页必须存在本人确认和记录版本。页状态遵循 PRD 5.4，系统不能由模型完成直接推定归档。

| 操作 | 原子读取与保存 | 幂等、并发和失败 |
| --- | --- | --- |
| 接收批次 | 锁定本人请求及配额，校验患儿、实际页数、来源、预算；保存批次、页及 Outbox | 同 logical_request_id 返回原结果，同标识不同内容拒绝；超预算不截断 |
| 页级执行 | 固定 page_version、checksum、model、Schema；持有 Task 租约和准确 TaskAttempt；记录真实请求、耗时、费用证据 | failed 显式新尝试；uncertain 核对原请求；已完成有效结果可以复用，失败不标成功 |
| 取消 | 未开始页进入 cancelled；在途页保存 cancel_requested | Provider 实际结果继续记录；撤权阻止新处理与采用；次数和费用不改写 |
| 候选采用 | 锁定页和候选，重新核验文件、用途、权限，保存本人决定、记录、事实及出处 | 未知可保存；专业资格不产生；原件、时间、语境一致才提交 |
| 归组更正 | 锁定文书当前指针和 expected_version，验证页归属及理由，新增版本、依赖失效与 epoch | 并发冲突明确拒绝；多就诊及历史出处保留 |
| 关系确认 | 锁定稳定 relation_id、两侧当前事实及专业证明，保存新版本并失效依赖 | 同次采样不增加连续次数，独立采样保留；解除重新评价 |
| 摘要生成 | 固定 baseline／current、可比较性、来源及可见策略，逐项保存引用与限制 | 无基准只显示新增和待处理；更正、关系或权限变化使依赖失效 |
| 评价冻结 | 锁定 dataset_version 和 patient_partition，核验协议、用途、答案及覆盖 | 冻结内容不可变；同患儿全部文书和就诊属于同一集合；未知不能标阴性 |
| 提醒调度 | 锁定当前 reminder、owner 关系、目标状态、schedule，保存唯一 deduplication_key 和发送许可 | 默认 disabled；暂停、关闭、完成、取消或撤权不开始发送；在途状态保留 |

accepted_count 只统计实际接收且 checksum 已验证的页；completed_count 只统计 archived；failed_count、cancelled_count 统计准确页，其他状态逐项显示。全部页状态互斥，状态数量之和等于实际接收页数，attempt 不增加页数。文件、页面、文书、患儿、就诊、采样、MAS 事件及观察区间分别统计。

current 固定获准 baseline／current／series／context、专业证明、策略、规则、窗口、known_at、assessment_at 和 epochs；采用前重新核查权限、执行权及 context_sequence，较早任务不得覆盖当前结果。replay 独立存储，只有当时已可得及有效资格参与，没有家庭事件、发送或周期更新路径。无法重建历史资格时保存不足。

## 用途、处理与保留

流程评价使用独立 workflow_evaluation 用途。当前用途副本约束通过 [workflow-evaluation-purpose.sql](workflow-evaluation-purpose.sql)及 archives.0005_workflow_evaluation 更新；原始创建 SQL 保持不可变。Python、JSON Schema 和 TypeScript 使用同一明确枚举，格式、流程及临床资格分别核验。实际迁移备份、恢复和资料保持证据见 [T48 共同接口记录](../docs/development/T48-shared-integration.md)。

原件与用途副本独立保存，purpose_copy 精确关联 original、derived、version、purpose、environment、use_authorization、processing_version、evidence、date_strategy、clinical_integrity_status。purpose_copy_review 记录准确副本版本、身份和临床结果、reviewer、时间、原件及副本校验和、scopes 和撤销。approved 必须有有效完整复核，状态标签本身没有放行效力。

复核范围必须覆盖文字、页眉页脚、图片标识、条码／二维码、文件名、元数据，以及原值、单位、患儿内部关联、时序、精度和观察间隔。日期调整仅采用批准一致策略并记录 replay 影响。外发、测试、展示和导出分别核验当前用途、环境和准确版本；必要证明缺少即阻止对应处理。原件、副本、身份映射分别授权，处置遵循准确保留策略及供应商实际结果。

临床答案需要独立确认人、依据、时间及观察区间；评价运行绑定资料、实现和规则版本，记录输入、结果、完整分母及限制。没有稳定时段或明确阴性参考时保留覆盖缺口，不构造数据。不同集合的同患儿重复资料不能分别用于开发与验证。

## 查询、索引与存储

PostgreSQL 承担关系与元数据、确认文本关键词、来源和依赖关系；私有对象存储保存准确原件及用途副本。向量召回、语义重排及医学知识图谱推理没有当前需求。关键词语言、索引及匹配策略在 T45 用实际中文原件测量后确定，数据接口保留 confirmed_text 与精确片段位置，不新增 RAG。

批次按 patient_id／received_at／id 查询；页进度按 batch_id／status／id 查询；文书按 patient_id／organization／document_type 和当前版本筛选；事实按 patient_id／medical_time_id 查询；关系按两侧事实与最新 version 查询；提醒使用 status=enabled 的 schedule_at 部分索引。所有查询在 SQL 范围加入当前可见患儿、本人范围与有效版本，不能查询后再在页面隐藏。只返回允许的匹配片段、医学时间精度、出处及记录版本，原件按需重新鉴权。未知时间和归属有独立目录，空结果与读取失败分别表达。

查询分母、分页条件和稳定排序由 T36／T45 实施。游标绑定患儿、主体、过滤条件、当前授权及数据版本，更正和撤权不返回旧片段。容量及 P95／P99 在真实资料规模和网络中由 T57 测量，结构检查不能声明性能通过。

## 迁移、真实样本与验收

T39 统一创建成熟 Django migrations；执行前记录目标数据库身份、准确授权、源码、数据库备份与原件版本清单。迁移新增实体，保留 RecordVersion、RecordField、MedicalTime、Verification、SourceAnchor 原值。按 field_id 幂等建立历史事实；time_origin=legacy，原采样时间保持；context_type、availability_status 无依据时为 unknown，不生成新核对证明。

| 核查 | 查询预期 | 验收责任 |
| --- | --- | --- |
| 原值与来源 | 按 record_version_id／field_id 比较所有数值、单位、限定符、方法、医学时间及原件 checksum，数量和每项摘要一致 | T39／V39 |
| 核对 | 原证明字段、用途、版本、时间、撤销和范围完全保留 | T39；T49 资格消费 |
| 引用 | 事实→字段、文书→页面、事实→来源、副本→原件、批次→任务的孤立及跨患儿引用为零 | SQL 复合外键、领域核验；T39／T56 |
| 幂等 | 重复迁移及同历史字段导入不增加事实、文书或页面 | T39 成熟迁移记录及回读 |
| 时间 | unknown 不产生 instant；上传不变医学时间；共同时间不扩大资格 | T39／T41／T50 |
| 恢复 | 数据库、原件、核对、配置和依赖在获准隔离环境恢复后能够读取 | T39／T57；真实 RTO／RPO 另验收 |
| 页面与权限 | 实际本人账号重新读取，撤权后原链接、片段、摘要和任务拒绝 | T56／T58 |

T35对独立真实升级副本进行只读字段、类型、domain及可空性核验，并在另一明确为空的PostgreSQL17实例执行全部设计SQL及约束，71组字段对应433项实际持久字段。当前原件依准确私有清单、SourceRevision及DataUseAuthorization管理；63份原件的本地软件测试已获准，独立患者、临床标签、窗口及专业批准分别保留必要条件。字段与空结构检查不代表完整业务或临床评价通过。

恢复采用经过验证的数据库备份和对象版本，之后重新应用撤权、停用及处置记录；代码恢复使用文件编辑工具。失败停止受影响写入并保留原始证据，原始版本不删除。原件、身份映射、供应商副本与备份按实际保留依据分别处理，未取得删除结果不登记完成。
