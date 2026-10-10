# BACK V2.0 服务行为

日期：2026-10-07。接口目录为 [v2-api-catalog.csv](v2-api-catalog.csv)，载荷为 [v2-protocol.schema.json](v2-protocol.schema.json)，实体与未知语义引用 [DATA V2.0](../data-design/v2-data-specification.md)。业务 URL 前缀为 `/api/v2`；现有 `/api/v1` 继续服务已发布调用方。V2 新增写入不通过 V1 接口混用输入，schema_version 固定为 `2.0.0`。T38 维护逐页面使用位置，T39—T53 实施接口；目录存在不表示运行路由已注册。

## 授权与合法前置

support_session 使用现有账号密码及当前环境的有效 ServiceRoleGrant 建立 support audience，登录提交同时校验 CSRF。授权编号、人员状态和有效期决定岗位能力，账号注册不生成岗位授权。刷新读取再次检查当前授权；具体资料仍按患儿、文件和用途范围核验。

家庭操作依当前 Session、account_status、auth_epoch、EnvironmentGate、Relation、AccessGrant、字段范围、来源用途与保留规则取得资格。authorize_case 的 purpose 和 environment 必须来自当前运行配置及实际动作；不采用请求声明。家庭写入只保存本人采用事实；专业评估同时取得 verify_field 证明与 execute_assessment 授权。岗位 UI 位于独立 support audience，导航不可代替服务端权限。

本人申请关联、受控人员核验、明确授予患儿动作后可上传。文书归组保存本人说明及未知就诊；创建 professional clinical_episode 必须通过专业记录流程。关系判断中的 same_sampling、formal_correction 对计算集合的影响需要指定专业核对；document_copy 与 same_bytes 的结构判断不授予医学资格。manage_data_use 负责用途副本和评价集合；verify_field 标签操作还逐患儿检查字段范围。manage_notifications 只允许运行元数据；准确目标患儿的病例读取需要另行授权。

所有读取、总数、游标、子项和附件执行同一 Policy Gate；家庭 projection 不包括受控全文、内部计算、标签答案、Provider 身份或费用。本人提醒过滤 owner_id，支持角色不能修改另一主体的个人设置。资料使用说明返回当前真实用途、保留与 Provider 能力，未配置值保持未知。

manage_data_use 与 manage_notifications 为新增 ServiceRoleGrant 动作，由对应实施任务加入登记清单；另行逐患儿核验 read_case 与字段范围，专业标签还核验 verify_field。运行身份没有此类授权时拒绝受控入口，不能因岗位页面可见取得权限。

## 请求、版本与读取

新增读接口使用安全 JSON 投影，search 为只读 POST，敏感过滤留在正文。继承 V1 Error、WriteIntent、WriteReceipt、TaskView 和 Page 的格式，V2 与 V1 definition 的版本冲突由 V2 显式替换。版本、epoch 均为十进制整数字符串；Decimal 保留文字；来源文字不被 normalized 字段替代。required 与 null 各自保持业务含义；所有 object 禁止 additionalProperties。

每项新增业务写入携带 Idempotency-Key、X-Write-Intent、X-Intent-Generation；意图准备登记准确 V2 operation_id、target_key 和 schema_version。目标键不含 API 前缀。唯一身份 `(actor_id,operation_id,target_key,request_id)` 保持至允许重试与资料保留窗口结束；同键异内容拒绝。更新还必须提供 expected_version 或逐页 expected_status_version。业务、意图、write_request、版本、epoch、Task、Outbox、审计同事务提交。响应中断按原意图查询，unknown 不允许新键提交。

用途意图保存 patient_id、original_file_id、purpose 和 environment 的授权范围，查询和重试重新核验同一范围。副本的人工复核同时保存准确 ServiceRoleGrant、AccessGrant 及人员 auth_epoch；撤权、失效或人员状态改变后，旧复核不能授予后续外发。临床完整性或日期一致性未确认时保存 unknown，副本保持待复核。复核为明确失败时保存 failed，并拒绝采用副本。

purpose_copy_options 按准确原件身份与目标用途执行当前岗位、病例、环境、文件及保留检查；读取实际原件核查校验和。返回当前有效用途授权及同一原件、用途、环境的启用处理方案，方案再次核验批准、验证、代码摘要及处理材料；检查失败立即拒绝读取。空集合保持真实缺少状态。读取登记原件版本审计。界面由本人选择授权和批准方案，提交沿用准确身份，处理任务回执重新读取 Task 后才显示登记结果；completed 仍需要实际副本引用及独立人工复核。

201 表示资源保存且重新读取可见；202 表示持久任务已受理；200 写入表示当前设置或动作已保存。任务完成需要真实产物及当前资格，不能依据 HTTP 受理或模型请求返回码。目录 read_operation 为每项写入提供重新读取入口，响应携带资源与 task_ref；读取时再次核验权限。当前允许动作由服务状态决定，不由客户端推断。

## 事务与当前状态

| transaction | 同事务保护和产物 | 重新读取与失败 |
| --- | --- | --- |
| archive_batch | 当前患儿、主体、意图、配额；批次与已验证上传页引用唯一，页清单有序 | BatchView 数量分别统计；超额拒绝整项受理，原件保持 |
| archive_process | 固定页版本、原件摘要、用途副本复核、模型与预算；逐页 Task 和 Outbox | 原件 unavailable 拒绝；uncertain 只查询原请求；partial 保持成功页面 |
| archive_cancel | 页 current_task、status_version、租约；禁止后续采用并登记取消请求 | 已开始外部调用的停止状态依据真实回执；取消不能清除原件 |
| archive_confirm | 候选集合与原页、原文位置、逐项决定、时间和语境；建立 RecordVersion、Field、ArchiveFactVersion、确认、epoch | 完整决定才能提交；字段初态未核对；source_anchor 与 page/document 同患儿且一致 |

archive_fact_read 与 archive_fact_history 读取准确事实、记录版本及字段，逐项执行当前家庭权限。事实读取保留历史缺少状态；FactFieldSnapshot 的 quality_reasons_snapshot_state 为 not_recorded 时，仅允许 unknown／invalid 字段及原有空原因集合。新资料输入继续使用 clinicalField 的完整性约束。历史分页读取批准的 backend-runtime-1 的 api.max_page_size，绑定人员、身份版本、患儿、事实当前版本、数据及核对版本、授权字段与运行配置；游标到期时间不得超过当前会话有效期。历史每项重新检查当前记录及字段授权。
| archive_grouping | document 当前版本、ordered page IDs、documented_time、组织、类型及真实就诊关联；新版本与当前指针 | 跨文书复用页需明确副本关系；就诊未知保持 proposed_metadata；任何缺少引用拒绝 |
| archive_relation | 患儿、两个准确事实或原件版本、类型、依据、原因；关系不可变版本 | disputed/released 不产生有效去重；患者列表锁后复核，禁止循环更正和关联自引用 |
| archive_delta | 固定 comparison baseline、全部输入版本、当前权限和 epoch；DeltaSummary 与逐条来源 | unknown baseline 不输出“新增”；输入改变标 superseded；更正与重复分别呈现 |
| observation_period | expected_version、明确范围和 timezone、目标采样频率来源；新版本、epoch 和实际间隔投影 | 观察设置不能补造采样；未知单位、时间、方法不输出可比较方向 |
| purpose_copy / purpose_review | 原件版本、用途、目标、复制文件、checksum、处理版本、复核版本及全部 scopes | 一项未通过即禁止外发；撤销或新版本使旧资格失效；原件字节保持 |
| evaluation_dataset | exact dataset version、protocol、患者集合、用途授权与副本；分组唯一性 | 同患儿和全部更正版本在同协议中不得跨 tuning/acceptance；current 计算与 evaluation run 分开 |
| evaluation_label | 专业 scope、答案来源、独立标注人与参考版本；分歧状态显式保存 | unknown 不补答案；unresolved 不计入已裁定分母；服务结果不能作为其自身答案 |
| evaluation_run / assessment / alert_evaluation | 固定数据集或字段、核对、可得时间、规则、观察期、relation版本及全部 epoch；Task、Outbox | 计算细节引用 T37；replay 不产生家庭事件或通知；未执行不返回 completed |
| material_reminder | 本人、准确 target_type/ref、版本、enabled/paused/snoozed/disabled、真实渠道订阅与定时条件 | 初始 disabled；填写正式目标后完成关闭；个人文字不生成临床分级 |
| notification_reconcile / notification_receipt | 准确 attempt/provider_request、签名原字节摘要、event_id 与许可；回执及结果去重 | accepted、delivered、read 独立；未知先查询，禁止跨渠道自动重复；不能用手动结果标送达 |

锁顺序沿用 EnvironmentGate → Patient → Principal／授权 → 意图 → 业务对象；多个 ID 排序。当前指针使用条件更新，旧版本不可覆盖；读取使用一次短 REPEATABLE READ 快照。网络请求与长文件处理位于事务外。原件、资料用途、授权、核对、映射、规则或观察期变化使对应采用资格失效，新请求即时核验，异步遍历只维护查询状态。

字段映射的唯一维护位置为 [v2-field-map.csv](v2-field-map.csv)。purpose_copy 创建任务尚无副本时只返回 Task；真实副本与 checksum 已保存后才能读取 PurposeCopyView。副本 status 使用 DATA 的 pending_review/approved/rejected/revoked/disposed；目标和处理配置保存在 processing_evidence。用途、环境、目标与精确副本复核共同校验。

评价集合分组使用 development/validation，分别对应调参和业务验收。positive/negative 标签只保存独立答案已经确认的结果；分歧及待裁定保留 unknown 与 limitations，实际裁定依据写入 reference_answer_ref。coverage 保存真实 source_manifest_ref、purpose_copy_ids、label_version_ref 与分母说明；这些字段不得由界面展示数量推断。资料目录需要不可变 dataset_version 的冻结动作，冻结前逐用途、分组、答案与checksum核验，完整检查后设置 frozen_at。

观察范围保存实际首尾医学时间、本人标签与真实 episode 引用；目标采样频率只从批准规则取得，未知返回 null。提醒延后使用 action=snooze 保存新 schedule_at 与 enabled；页面同一次保存全部设置时使用 configure，明确延后时间保存为新 due_at 且显式 enabled=true。quiet_start/quiet_end 和 IANA 时区决定实际可发送时段，next_due_at 仅对 enabled 且 pending 计算。创建省略 enabled 初始 disabled，configure 省略 enabled 保留当前状态；显式 boolean 状态与全部设置同事务保存，启用要求完整批准渠道。完成使用 completed，目标取消使用 cancelled。本人目标目录按当前完整权限、真实目标及准确版本分页，缺少 record_task 领域映射返回不可用原因。notice 与 Provider 回执维护真实渠道，不能根据 enabled 推断发送。

## 逐页任务、配额与恢复

local_disposition 使用当前批准保留策略和准确范围摘要准备 WriteIntent；批准、逐项执行、保留及任务重试分别登记操作身份。批准保存实际人员、岗位和病例授权、auth_epoch、策略摘要与全部对象版本。执行同事务保存 WriteRequest、Task 和 disposition.local_requested，文件进入 quarantined 并停止依赖使用。purpose 队列消费实际处置事件，TaskAttempt 在文件操作之前提交；实际字节删除和不可覆盖回执各自核验。每项 Task 完成指向处置申请，申请仍按全部项目的实际状态决定是否完成。

失败保存准确 TaskAttempt 错误；实际准备证明或回执已经存在时进入 awaiting_resolution。reconcile-local-dispositions 核查本地处置任务的排队期限和过期运行租约，记录准确队列检查身份，不发送 Provider 请求。环境停用后允许保存拒绝和失效任务的运行元数据，文件执行继续要求当前启用环境与全部批准证明。重试重新核验当前权限、原批准、文件身份及 status_version，登记新 Task 和 previous_task_id，保留原失败及原尝试。Worker 根据同一 item_id 重新读取实际准备证明和回执；未取得实际证明时拒绝登记成功。外部副本继续等待实际提供方协议和回执；本地完成不能代表外部处置完成。

页状态为 declared/receiving/uploaded/queued/processing/awaiting_confirmation/confirmed/archived/failed/uncertain/cancel_requested/cancelled/superseded。Batch 的 receiving/processing/awaiting_confirmation/partial/archived/cancelled/failed 由实际页状态推导；不得用一项成功代表整批完成。Task 状态沿用后端 LLD；每次尝试保存准确页版本、TaskAttempt、Provider request_id、输入摘要、model_version、起止、实际耗时、错误和实际计量。未知费用为 null，不能标零。

运行配置逐项规定 max_pages/max_bytes/max_inflight_pages、daily_budget、per_batch_budget、queue_wait_seconds、execution_seconds、lease_seconds、heartbeat_seconds、max_attempts。启用前校验正整数、期限关系及实际 Provider 接口支持。预算在 PostgreSQL 共同锁内预留，只有真实计量和任务终态释放或结算；不明受理保持 reserved/unknown。failure 与 confirmed 页面数量、原件数量、候选数量、正式事实数量分别查询。

重试只创建失败或已终止原任务的新 Task；引用同一页与版本，尝试次数递增，原失败不覆盖。uncertain 页进入有限 query_attempts 与 query_delays_seconds，耗尽后 awaiting_resolution。失败页重试不重识别其他已完成页。租约过期由 Reconciler 核查实际提交再推进；旧 lease_generation 和旧 status_version 提交影响行为零。权限撤销、文件更正、用途撤销、预算不足及用户取消均禁止新调用或采用。

前台按 poll_after_ms 轮询；隐藏页面停止轮询，后台任务持续按当前权限运行。重新前台读取 Session、Relation、batch/page/task 与原请求结果；禁止依据内存成功状态自动重复提交。浏览器失败只表达网络状态，不能覆盖数据库任务事实。

## Provider、通知及运行配置

识别沿用实际 Ark Adapter，上传原件之前核验外部处理用途；用途要求副本时 only approved copy/version/checksum 与 target 匹配才能发送。原图处理与语音转写分别维护 Provider 权限。文本与图片只作为资料，不改变系统指令、工具范围或临床规则。逐事实输出必须通过成熟 Schema 与出处关系校验，failure 不生成替代结果。

工程期限沿用版本化 runtime：OCR 连接 5 秒、读取 60 秒、任务 180 秒；通知连接 3 秒、读取 10 秒、许可 15 秒；外部提交额外重试零次；查询最多 3 次，间隔 5/15/30 秒。对应 Provider 配置还明确 model/region/retention/endpoint/secret_ref、每日额度与真实价格版本；全部数值为内部工程建议，正式批准与实际测量单独登记。配置缺少时对应能力 unavailable，其他已具备资格的业务继续使用。

60 秒内 5 次 Provider 技术失败停止新调用 60 秒；受理未知不随着停止期结束自动重发。稳定 Error 使用 PROVIDER_TIMEOUT、PROVIDER_UNAVAILABLE、RESULT_UNCERTAIN 与 query_task/query_request 恢复动作；所有领域错误在出错位置产生，统一 HTTP 边界只转换错误，不捕获后继续业务。health 区分进程、依赖、当前配置与业务能力，no-store/CSRF/Origin/TLS 按既有设计。

通知受控目录返回元数据与真实 evidence_ref；Provider 签名与签发窗口按所选渠道官方协议实现，缺少协议或密钥时 receipt route 不接收。事件去重作用域为 `(provider_id,provider_event_id)`，还复核 request/recipient/attempt 对应与当前 Provider 配置。delivered 仅来自验证后的送达回执或真实查询；read 来自本人打开页面。提醒有独立 send_permit 与 notice，不写 AlertEvent、不设临床级别。渠道真实账号、订阅、额度、文本批准与联系存在时才启用。

## 验收与交接

每项 write_operation 用合法账号操作、查询意图、读取资源和后续消费；相同键、异内容、旧版本、跨患儿、撤权和失败恢复分别检查。批次逐页选择正确任务与配额；数量守恒含拒绝、未知及取消项目。用途测试取得真实副本与人工复核；评价测试取得独立答案、分组与实际分母；通知取得实际接收对象和签名回执。没有这些输入时保留对应业务 NOT_RUN。

T36 的 STATIC 检查校验目录完整性、Schema 自身及引用、逐写入读取入口、权限、FR 与 UI2引用、事务与任务定义，并读取当前运行 View、URL 与 Policy Gate 的实际 identifier。静态证据不代替业务、设备、视觉、临床或真实发送验收。T37 接收候选及计算，T38 接收 UI2 与状态，T39 接收实体，T40—T53 分别实施并回读。
