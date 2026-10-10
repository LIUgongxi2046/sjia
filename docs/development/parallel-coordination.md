# 并行开发协调记录

## 共同范围

2026-10-08。用户在聊天 01a11918-9632-7981-b7e9-05420330f7b6 明确要求与同项目另一聊天分工，双方共享当前目录。目标继续包含 PRD 2.0 全部功能、参赛要求、全部 39 个 UI2 页面和 328 项状态。

视觉操作继续禁止；DOM、构建和资产字节检查不能证明一比一还原。真实业务验收、手机、文字放大、键盘、后台恢复及微信保持未通过状态，只有实际证据能够更新验收。

禁止使用模拟账号、模拟 Provider、伪造原文或临床资料。已有 QA 数据只用于技术读取、资料保留和拒绝检查。文件编辑使用文件编辑工具；中间文件保存在已经忽略的 .planning-work，禁止使用 /tmp。原件及既有业务内容不删除，代码恢复按用户要求使用文件编辑工具。

## 聊天 01a115ca-3c69-7333-8f6e-90bce2f5cf10

- 当前任务：T42 批次、页面、尝试证明、配额消费与完整恢复；T43 文书读取、创建、归组版本及提交恢复；后续 T41、T43、T44、T45、T46、T47 的档案、事实与相关 UI2 操作，T54 全部前端页面接入。
- 当前业务文件：backend/archives/documents.py、backend/archives/inputs.py、backend/archives/grouping.py、backend/requests/archive_documents.py、backend/api/views_archive_documents.py、backend/api/views_archive_writes.py、backend/api/views_v2_requests.py、两项文书检查、guardian-h5/src/api/archiveDocuments.ts、guardian-h5/src/api/useArchiveDocument.ts、guardian-h5/src/features/archives/UI2GroupingPage.tsx、docs/development/T43.md。
- 共同能力更新：backend/api/views_archive_facts.py 的 fact_history_limit 增加可选查询错误名称，事实历史原行为保持。
- 提交恢复共同定义：backend/api/urls.py 七项联系及发布写入路由增加准确名称；RequestView.controlled_input 与 frontend-design/types.ts 同步四项正式输入；请求目录接收 family_support。V2 家庭请求按本人意图或准确 operation 分派文书及资料提醒，提醒两项写入路由、严格输入、原 request_id 查询、正文恢复与标准回执已登记；读取与写入恢复各六项技术检查通过，完整正向业务保持未验收。
- 当前正式运行路由：文书详情、历史、创建、归组及 V2 提交恢复。两项读取和六项写入与恢复匿名拒绝通过，70项已有时间符合正式输入，111个业务表保持。UI2-28 接入已存文书和原 request_id 查询，当前数据库零正式批次、页面及文书，正向读取、保存和恢复仍未验收。
- T42 当前功能：backend/archives/batch_creation.py、batches.py、requests/archive_batches.py、api/views_archive_batch_writes.py、views_archive_batches.py 及 V2 路由维护创建、读取和原请求恢复。六项创建技术检查、四项实际读取检查及三项当前迁移回读检查通过，111张业务表和29份原件共876210字节保持。archives.0004_page_order 保存独立页面順序，BatchView.pages 按实际 ordinal 读取。PageView 保留原件版本、摘要、实际状态及全部历史尝试，未知费用和耗时保持空值。严格 archive-quota-1 登记由协作聊天完成，唯一当前运行引用及接收限制由本聊天核验；费用预留与结算、执行、取消、重试、纯文本及 UI2-25／26 继续实施。当前正式批次及页面为0，合法账号下的创建、恢复和全部正向业务继续待验收。
- 前端文件归属：guardian-h5/src、guardian-h5/scripts；UI2 设计、Token 与资产继承现有文件，视觉比较保持未执行。
- T48 共同接入：DatasetInput/View 保存持续目录、准确版本、摘要、分组、标签、覆盖与逐来源证明；受控 V2 提交按本人意图和准确操作恢复原正文及回执。六项集合路由已经登记并加载。workflow_evaluation 的 Python、JSON Schema、TypeScript、来源用途说明和数据库约束一致，选择控件继承 UI2 原稿；archives.0005_workflow_evaluation 已完成实际备份、独立恢复和当前应用，111表与29份原件保持。当前接口身份检查通过，非空合法保存、冻结、UI2-37及评价运行继续未验收。准确范围见 docs/development/T48-shared-integration.md。
- 当前证据：文书、联系、发布、提交恢复、配置登记、配置采用、家庭与摘要读取已按当前服务复验。五份 T51 检查共29个测试方法通过；五份摘要停止返回正文，178个原始项目保持。当前正式路径、摘要和验收范围由交付记录登记，完整业务及视觉仍待验收。
- 当前浏览器检查：27个家庭页面、五种宽度和后续交互共150项实际匿名 DOM 检查通过，逐项超时与进度记录保留。UI2-32 连接准确本人目录、目标、设置、原请求查询与重新提交、保存重读及草稿目标入口；另有五种宽度的聚焦检查和六项工程时间检查。视觉、真实手机、微信、文字放大及正向保存仍待验收；共同协议后续更新按当前输入重新核查证据。

## 聊天 01a11918-9632-7981-b7e9-05420330f7b6

- 当前任务：只继续整个 T48 来源清单与评价集合。用户明确要求整个 T48 完成后暂停本聊天，聊天 01a115ca-3c69-7333-8f6e-90bce2f5cf10 继续开发；本聊天不再开展后续任务。账号由用户自行注册，业务验收继续需要实际用途、独立答案和当前授权。
- 当前文件：backend/family_content/service.py、backend/family_content/qualification.py、backend/api/views_releases.py、backend/contacts、backend/api/views_contacts.py、backend/configurations、backend/api/views_configs.py、backend/requests/controlled.py、backend/api/views_family.py 的字段入口、backend/api/views_family2.py 的受控分派及摘要读取、backend/summaries/service.py、五项 T51 技术检查、docs/development/T51.md。
- 已执行验证：共同服务重新加载后，发布三项、联系七项、提交恢复六项、配置八项及配置消费五项检查的五份正式捕获全部通过，输入保持一致；15 条配置、10 条批准与10 条验证内容摘要保持一致。无当前批准证明的摘要读取不返回正文，数据库原有 178 条摘要内容保持完整。完整正向发布、联系创建与激活、配置证明读取、受控正文恢复与并发重试、临床采用仍未验收。
- T42 配额配置协作：provider/archive-quota-1 已加入 configurations/schemas.py 和 service.py，并调用 archives.quota.quota_reason。新增四项配置拒绝及资料保留检查，与原有十三项配置检查共同捕获，17 项通过；当前 Provider 配置为 0，未创建额度或批准。正式证据及限制见 T51 主记录；批次消费、准确引用和实际计价证据由另一聊天负责。
- T52 文件归属：backend/notifications、backend/api/views_support2.py 的 provider_callback 与对应导入、docs/development/T52.md；共同错误映射及路由由另一聊天维护。准确 Provider 协议、获准渠道和明确发送授权尚未取得，外部发送保持未执行。
- T52 当前证据：三项技术检查通过，覆盖 /api、/api/v1、/api/v2 未配置回调拒绝、许可拒绝及 attempt 引用检查；7 条接收记录、0 条发送尝试和0 条回执完整保留，正式捕获见 docs/development/T52.md。真实 Provider、发送资格、签名、查询、并发及实际送达继续未验收。
- T53 文件归属：backend/notifications/reminders.py、reminder_schemas.py、reminder_schedule.py、reminder_lifecycle.py、reminder_notices.py、management/commands/reconcile_material_reminders.py、backend/requests/material_reminders.py、backend/api/views_reminders.py、views_reminder_writes.py、四项提醒检查和 docs/development/T53.md。创建及修改、准确目标目录、原请求恢复、同事务审计、时间计算与生命周期处理已经实施；持久调度、独立发送许可、record_task 和真实发送继续未完成。共同协议、类型、路由和错误由另一聊天维护。
- T53 当前证据：读取六项、写入恢复七项、时间及状态规则八项、本人目标目录与生命周期检查八项通过。39 份草稿、0 条提醒、0 条提醒通知、23 条意图、23 条请求与0 条审计内容摘要保持一致；正式捕获及当前输入摘要见 docs/development/T53.md。空生命周期扫描与工程时钟检查不证明实际提醒保存、并发和发送，完整 V53 继续未验收。
- T48 文件归属：backend/case_details/evaluation_*.py、management/commands/inspect_evaluation_sources.py、backend/requests/evaluation_datasets.py、backend/api/views_evaluation_datasets.py、三项 verify_evaluation 检查文件和 docs/development/T48.md。19 项实际清单及输入拒绝检查、3 项实际来源证明检查通过；70 条医疗时间的精度与内容保持，已解析及未来时点均为 0。正式回执见 T48 主记录；当前来源没有对应 FileObject／FilePage，评价集合、分组、标签和运行均为 0。来源、固定方案、对象、独立答案、版本保存、分组、冻结、准确读取和请求恢复代码已保存；非空获准资料及完整业务尚未验证。共同协议、六项路由、support 请求分派和 UI2-37 由另一聊天接入；评价运行仍待实现，完整 V48 继续未验收。
- 公共路由及任务登记由另一聊天维护；本聊天提供接口映射、实际证据和文件摘要。

修改本记录之前重新读取当前内容，仅更新本聊天对应部分。准备修改共同文件时登记需要变更的位置，并向另一聊天说明。

## 共同文件及服务

- planning/delivery-ledger.json、docs/development/index.md、docs/knowledge/prd2-delivery.md、backend/api/urls.py、backend/api/urls_v2.py 当前由聊天 01a115ca-3c69-7333-8f6e-90bce2f5cf10 维护。另一聊天提供任务状态、证据路径与 SHA256、接口导入和路由内容，由维护聊天编辑并验证。
- backend-design、frontend-design、data-design、authorization、requests、config、tasks 的共同接口和定义在修改前协调，新增领域文件可以独立维护。
- 本地 Django 8000 当前由聊天 01a115ca-3c69-7333-8f6e-90bce2f5cf10 管理，服务不自动加载源码，重新加载后的准确 session、PID 和启动时间由对应运行回执及聊天核查。文书保存与恢复、配置登记及 archive-quota-1、字段采用、摘要资格、通知拒绝、提醒目录、目标目录、同事务状态设置、写入、请求恢复、批次创建及三个读取接口已经登记；岗位动作已登记，没有创建授权记录。另一聊天负责 notifications 全目录、views_support2.py 中 provider_callback 及相应导入、T52／T53 文档；共用错误映射与路由继续由维护聊天登记。另一聊天需要运行新接口时，提供已完成文件及路由，由维护聊天注册和重新启动。
- contacts.0003_contact_verification 已经实际应用，111 个业务表迁移前后内容摘要一致，1 条原有联系资料保持；备份和当前核查报告见 .planning-work/contact-migration-current.json。增量约束允许未核验草稿保存空白时间，approved／active 必须具备 verified_at。
- PostgreSQL 5433、Redis、Vite 5173 及生产预览 4173 延续现有服务。运行身份及数据版本按当前检查重新核验。
- Worker 和 Beat 继续停止。自动审批曾拒绝启动当前 Worker，理由为消费任务可能删除原件；目前没有新的明确批准。任何启动或处置需求先核查现有授权及实际消费范围，不能通过更改启动方式消除审批要求。
- 本记录用于文件和服务协调，不替代逐任务主记录、交付来源或业务验收证据。
