# 跨层接口接收规范

版本：HANDSHAKE V1.0。日期：2026-10-01。能力：DESIGN。S005-2 维护接口和领域接收规则；S005-1 维护对应 SQL；S005-3 和 S005-4 分别接收编排与页面定义。正文引用 `protocol.schema.json`、`runtime.schema.json`、`api-catalog.csv`、`../data-design/schema.sql` 及前端类型；业务运行状态独立记录。

## 1. 首页与患儿上下文

`HomeView.assessment_state` 必填，枚举为 `not_evaluated/configuration_pending/insufficient/current/stale`。`assessed_at` 和 `current_alert_id` 必填且允许 null。

| 条件 | assessment_state | assessed_at | 页面行为 |
| --- | --- | --- | --- |
| 当前没有评估结果 | not_evaluated | null | 显示未评估 |
| 必需配置没有有效批准 | configuration_pending | null | 显示配置尚未生效 |
| 当前评估输入不足或关键结果 unknown | insufficient | 当前允许展示的评估时间，否则 null | 显示资料不足及获准原因 |
| 最新 current 评估完成，时效、全部输入、epoch、用途和配置仍有效 | current | 必须为准确 assessment_at | 无当前事件时可以显示 clear |
| 已有评估过期、失效或被新上下文替代 | stale | 当前允许展示的原评估时间，否则 null | 显示评估需要更新 |

选择结果按 `(assessment_at, context_sequence)` 递减，禁止跳过最新失败或失效记录而采用更早结果。任务失败通过 `processing_state=failed` 表达；已有成功评估不再具备当前资格时同时为 stale，没有任何完成结果时为 not_evaluated。能力 disabled/unavailable 时不返回临床内容及评估时间。

`alert_state=current` 必须同时返回 `current_alert_id` 和获准 `clinical`；其余状态 `current_alert_id=null`。`current_alert_id` 位于 HomeView，准确引用 AlertView.alert_id。提示仍有效但新资料不足时可以同时存在 current 事件与 insufficient 评估；前端分别展示。首页只按此 ID 打开提示详情，不按列表位置推断事件。

FamilyRecord、AlertView、SummaryView 的 `patient_id/display_id` 是当前权限核验后的资源归属。直接链接根据服务端归属建立页面上下文，并与本人有效 Relation 核验；跨患儿页面必须清理当前资源、请求和表单状态后采用新结果。无权限返回统一资源不可用错误，不暴露资源所属身份。

## 2. 登记配置与 OCR 候选

`field_config_read` 按患儿和 `kind` 返回 FieldConfigView。配置来源为获准 `config_version(kind=content, schema_version=field-config-1)`；payload 使用 FieldConfigDefinition。未指定版本时返回当前生效定义，指定版本时用于草稿和历史表单回读；已退出写入资格的版本返回 read_only。无获准配置时返回 disabled、原因和空字段集合。

FieldDefinition 规定字段标识、标签、value_type、必填、可写、枚举、单位、字典类别、十进制上下限、文字长度、重复数量及时间要求。服务端检查 field_code 唯一、上下限顺序、类型与限制适配、watch_field_codes 属于当前可读字段。`writable` 为配置允许与当前字段授权的交集；前端不能通过修改该值取得权限。

`allowed_values` 对 categorical 必须非空；`allowed_units` 对 decimal 明确允许的原单位，空集合表示无量纲且 raw_unit=null；minimum/maximum 只适用于 decimal，其余类型为空。max_length 约束原文及文本；未知值不得变为零值。领域校验继续检查医学时间、字段来源和跨字段业务规则。登记配置管理输入约束；临床阈值由 D04 和 rule/window 配置管理。

提交时 `RecordInput.field_config_version` 必须保持当前写入资格；不符返回 409 CONFIG_VERSION_CONFLICT，前端重新读取并要求确认变更。服务端拒绝未登记、不可写或不满足限制的字段。

CandidateField 的 `proposed_field` 为 FieldInput 或 null，`field_config_version` 固定候选解释使用的配置版本。`field_path` 的唯一形式为 `/fields/{client_field_id}`；client_field_id 由候选创建服务生成并在同一候选版本内稳定。proposed_field.candidate_field_id 必须等于候选 ID，anchor_id 必须等于当前 anchor.anchor_id 或同为 null。

recognized 必须提供 proposed_field；无法取得可靠字段类型或映射时保持 uncertain/missing/unlocated 并允许 null。数值、单位、时间分别保留对应原文及未知状态；有字段映射但数值不明确时使用 value_state=unknown。医学时间使用 MedicalTimeInput，instant 由正式保存服务计算。报告级时间依据准确报告头来源复制到对应条目，并登记 sample_time_origin=report；无来源时保持 unknown。

确认表单采用 proposed_field 的属性，禁止从 raw_text 再次解析医学数值。每个候选必须有 accepted/modified/unknown/excluded 决定；原文、候选、本人决定与正式输入分别保存。配置变更或文件组版本改变时原候选不可继续采用。本人确认不改变专业核对状态。

## 3. 来源解析、历史目录与原件

`source_resolve` 输入准确 record_version_id 与 field_id。服务端在同一读取快照中核验记录归属、字段授权、用途和版本可读性；非当前版本同时要求 read_history。返回 SourceResolution，包含 `record_id/version` 及原始 SourceReference。前端通过 record_version_read 读取该版本，并核对返回 record_version_id。文件与每次 Range 请求继续单独授权，来源解析结果不授予后续权限。

`history_search` 按当前患儿的获准资料生成分页 HistoryList；每个 HistoryEntry 保留准确 medical_time_id、完整 MedicalTime 和该时间关联的获准字段来源。按精度分组，已知时间按时间区间和 medical_time_id 排序，未知及相对时间保持原文并独立列示。日期过滤需要 scope_timezone 且同时提供 from_date/to_date，按闭合日期范围对应的半开时间区间检查交集；year/month/month_range 使用实际精度表达区间，不生成 instant。unknown/relative 不满足日期区间时不进入该区间列表，仍可通过 precision 单独查询。

选择 HistoryEntry 后使用 `record_search.medical_time_id`；服务端只返回具有准确时间引用的获准字段，记录级来源仍保留。游标绑定主体、患儿、过滤参数及当前版本，相关版本变化返回 CURSOR_EXPIRED。HomeQuery.at 继续表示准确同秒选择；年月资料通过目录及 medical_time_id 查询。

FamilyRecord 返回当前获准 `correction_reason` 与 AuthorView。AuthorView.display_id 为当前策略允许的家庭成员或专业人员显示标识，无法展示时为 null；不得暴露 principal_id、身份 Provider 标识或联系电话。is_self 由服务端当前主体计算，role 根据登记事实投影。

`RecordInput.original_file_ids` 为有序、唯一的完整原件集合；无原件时为空。服务端验证每个文件为同患儿、用途获准、状态 available 的 original 文件，并确认属于当前提交的获准上传组。报告的 file_group_id 与原件清单必须一致；治疗记录同样保存明确清单。`record_attachment` 按不可变记录版本及 ordinal 保存文件关联。更正事务创建新版本的完整集合，历史关联保持不变。

FamilyRecord.original_files 按 ordinal 返回获准文件，original_files_complete 表示能否完整返回该版本全部原件。任何文件没有读取资格时返回 false，前端显示资料未完整取得并禁止据此替换整组附件。文件 unavailable/failed/disposed 保留准确状态，禁止声称原件可用。草稿保存 original_file_ids，提交及更正重新验证；字段 anchor 引用不作为完整原件清单。

## 4. 关联申请与请求恢复

`application_read` 只读取当前主体本人申请，返回 application_id、version、状态与获准原因。申请版本初始为 1；withdraw 和受控 decide 在同一事务锁定申请，检查 expected_version 与 pending 状态，成功后 version 增加。状态或版本不符返回 409 VERSION_CONFLICT。申请详情在关联页面使用 application_id 展开；Relation 详情使用 relation_id。

`IntentInput.target_key` 为 api-catalog 中对应操作路径完成 UUID 参数替换后的准确路径：使用小写标准 UUID，不含 `/api/v1` 前缀、查询、片段、末尾斜线、百分号编码或外部地址。服务端依据框架路由反向生成并比较，禁止前端拼接身份摘要。无路径参数时使用目录固定路径。operation_id 必须具有 idempotency=intent，目标对象和 patient_id 由当前授权资源解析；payload 另按准确 input_schema 验证。

`request_recent_account` 只查询当前主体、patient_id=null 的申请意图，允许 operation_id 为 application_create/application_withdraw；不可返回其他主体的申请。申请匹配到患儿之后，原意图仍保持创建时的本人申请范围，receipt 对象重新检查本人申请归属。关联患儿请求继续使用 request_recent。

RequestView 返回 operation_id、target_key、prepared_at，使刷新后的恢复列表能够识别对应操作。查询可返回尚未提交的 prepared 意图；提交状态未知时持续查询原 intent_id。原键和原 payload 才能重用原意图，服务端仅对确定失败且允许重试的意图增加 generation。没有可确认归属的 intent_id 时返回统一 404；已登记意图的 write_request 尚不存在时按 prepared/过期事实映射 pending/not_found，不创建新业务对象。

## 5. 身份与 CSRF

`auth_bootstrap` 为同源 GET，调用 Django CSRF 组件建立匿名会话中的 CSRF secret，返回当前 masked token 和批准 return_paths。`CSRF_USE_SESSIONS=true`；会话 Cookie 为 `__Host-sjia_session`，Secure、HttpOnly、Path=/，不设置 Domain；SameSite 来自批准 runtime。CSRF secret 保存在会话，客户端只在页面内存保留 masked token。

auth_start、auth_complete 和所有业务写入使用 `X-CSRFToken`，服务器验证 CSRF、Origin/Referer 与同源配置。完成登录后轮换会话身份及 CSRF token，由 SessionView 返回新 token；session_read 也返回当前 token。退出或失效清理页面内存。全部身份响应 no-store/private，token 不进入日志、URL、持久浏览器存储或遥测。

return_path 只允许 runtime 批准的 `/home`、`/records`、`/account`、`/associations` 子集；未登记路径立即拒绝。IdentityProvider 的具体产品、凭据、endpoint、处理依据和运行可用性由 D02/S011 提供；缺少时 identity_state=disabled，不启动外部认证。此项外部输入保持 PENDING。

## 6. 动态评估任务与结果

task_type、runtime 和 TaskView 统一登记 `alert_evaluation`。受控岗位通过 alert_evaluation_create 发起，服务器事件直接调用同一领域调度服务；页面读取不能创建评估任务。输入固定 mode、assessment_at、known_at 与批准 policy/window 版本；known_at 不得晚于服务器当前时刻。current 时间来自获准调度，replay 仅供有对应用途的专业岗位。

完成后 TaskView.result_type=alert_evaluation、result_ref=evaluation_id，仅具有 read_assessment 权限的受控调用方可以取得内部引用并调用 alert_evaluation_read；创建、取消及重试要求 execute_assessment。家庭 task_read 只允许查询与当前患儿评估进度有关的获准任务，result_type/result_ref 均为 null，allowed_actions 为空；家庭重读 HomeView 取得获准状态。家庭不能创建、取消或重试临床评估。受控取消或重试继续遵守租约、当前状态和后端版本检查。

配置不足与资料不足分别保存 configuration_pending/insufficient，decision=unknown；完成规则求值保存 completed，decision 可以为 met/not_met/unknown。前端 current 要求关键判断确定且全部采用资格有效。失败及 superseded 不得发布临床事件。

SignalResult 与 PairResult 使用 `alert-design/node.schema.json`；WindowPolicy 与 TriggerPolicy 使用 `alert-design/policy.schema.json`。后端注册精确 schema_version，并在提交事务之前验证 Schema 及语义；未知版本立即返回 CONFIG_SCHEMA_UNAVAILABLE。Task 生命周期、超时、租约及错误沿用 BACK 第 5 节。

## 7. 持久关系、事务与迁移

`alert_evaluation` 固定上下文、用途、全部 epoch、input_digest、准确配置及时间。patient.evaluation_sequence 在患儿锁下递增并作为 context_sequence；task_id 唯一，重复消费读取原结果。

`alert_evaluation_input` 以 `(evaluation_id, signal_id, position)` 保存有序多时点字段；position 从 0 连续增长。复合引用保证患儿及字段版本一致。领域服务同时验证 medical_time_id/source_revision_id 与字段来源相符，专业核对 purpose、current_verification_id 及 normalization_id 仍有效。

`alert_evaluation_assessment` 允许零个或多个专业 assessment。`alert_signal_result` 保留算法版本、输入引用、reason_codes 与全部 pair_results；input_field_ids 必须属于同一评估同一 signal 的输入，PairResult 中的引用必须属于该集合，有序采样不允许重复或跳号，精度和十进制结果通过专用 Schema 与领域规则共同检查。

`alert_decision` 对每个 evaluation/rule 唯一，保存 met/not_met/unknown、采用动作和阻止原因。sequence 在患儿与规则锁下递增，previous_decision_id 必须引用同规则的前一序号。`alert_episode` 约束同患儿同规则族至多一个 open 周期；时序比较采用 `(assessment_at, context_sequence)`，较早评估不得修改较新周期。unknown 不关闭周期；关闭需要批准 reset 条件和 reset_evaluation_id。

`alert_event.evaluation_id` 必填，assessment 通过评估关联表取得。create_event 决议、周期更新、事件、接收人、依赖、Outbox 与审计同事务提交；事件 trigger_key 由 policy 规定并具有唯一约束。replay 禁止执行事件发布和渠道通知。

依赖节点统一使用 alert_evaluation、alert_event、assessment、summary 资源类型，分别连接准确字段版本、核对、mapping、policy、用途和发布来源。Q24 读取最新评估候选，Q25 读取全部输入证据，Q26 递归传播失效，Q27 读取原件集合。查询候选不自动授予临床采用资格；最终提交和家庭读取仍执行当前用途、环境及全部 epoch 核验。

新环境按 DATA 基础 SQL、病例 SQL、BACK SQL 顺序建表，共 88 张表。有数据环境由 Django migration 执行受审迁移：暂停相关写入、取得已验证备份、扩充结构并检查当前真实数据；根据完整来源建立评估及事件引用，逐项回读后设置必填约束。没有完整依据的历史事件保留历史记录并停止当前采用，不生成评估结果。迁移完成前不得启用动态预警；当前项目没有已部署数据环境的迁移执行证据。

## 8. 接收与验收

设计接收检查覆盖 SQL 对象及约束、API 注册、Schema 引用、字段必填与空值、直接枚举、TypeScript 和全部家庭绑定。真实联调使用获准资料、真实账号和 Provider，核验登记、确认、核对、评估、当前投影、来源、任务取消与失效。临床结果需要独立 Gold 依据；没有批准的样本仅用于资料结构检查。

外部待提供项为 D01—D09 有效批准、真实 IdentityProvider/OCR/通知服务、目标数据库与设备环境。Schema 接收不代表这些条件已满足。
