# 当前代码与文档交接（2026-10-21）

doc_id：SJIA-CURRENT-DELIVERY；status：CURRENT；last_verified：2026-10-21（Asia/Shanghai）。受众：研发、测试、交接人员。维护责任：源码或证据变化时核查本页与[知识索引](docs/knowledge/index.md)。

## 版本、授权与环境

| 项目 | 当前依据 |
| --- | --- |
| 源码 | 已接收远程 `b094448d40d53ba675dd5e4a278f3e2deb107a43` 的全部改动，包含本次文档及测试资料；实际交付提交以 main 的 Git 记录为准 |
| 本次授权 | 用户于 2026-10-21 要求更新文档，并明确 commit、push 到 main，包含测试账号测试资料、排除真实数据 |
| 约束 | 使用文件编辑工具；中间产物保存在项目忽略目录；不创建模拟业务资料，不主动执行视觉操作 |
| 工作目录 | `/Users/liuhaoxian/Downloads/我的/AI项目/sJIA并发症预警平台` |
| 本次检查环境 | Node 24.13.0；既有 Python 3.12.7；文档元数据检查另使用 QA28 的 Python 3.13.13 环境 |
| 目标版本 | Python 3.13、PostgreSQL 17；QA28 验证 3.13.13／17.11，独立目标实例已在当次检查结束时停止 |
| 历史运行身份 | QA29 于 2026-10-19 核验两套环境；原验收前端 5176／API 8002，真实资料前端 5177／API 8003；其余准确身份由私有报告保存 |
| 当前运行结论 | 2026-10-21 本地 API 8002／8003 均返回 ready、admitted_current；真实资料 API 的 liveness 和 H5 5177 入口返回 HTTP 200；准确证据见 [DOCS01](docs/development/DOCS01-current-documentation.md) |
| 发布范围 | 本次更新本地文档及派生资料；生产部署、外部发送和真实业务变更没有在本次执行 |

`planning/environment.json` 描述 2026-10-01 的计划基线，由交付记录引用。当前 Git 与目标版本专项采用本页及 QA28 的明确日期，不能将计划基线中的历史观察值用于推断当前运行状态。两份此前的 CURRENT-DELIVERY 文件保留各自日期范围。

## 当前需求与任务来源

[planning/delivery-ledger.json](planning/delivery-ledger.json) 为 T01—T60 的唯一状态来源。本次读取包含 30 项承诺、60 项实施任务及 60 项验收，任务均为 `IMPLEMENTED`；验收为 `STALE` 11 项、`NOT_RUN` 49 项，当前没有 `PASS` 项，60 项完整验收均未通过。完整范围继续覆盖 FR-01—FR-22、对应 AC／GRAC 与非功能要求。

专项 QA 的状态由[开发索引](docs/development/index.md)中对应主记录维护。专项通过不会自动关闭完整 V 验收。本次文档检查也不修改业务验收状态。

## 已有真实证据

源码还包含 QA30—QA43 的私有 S3、准确版本处置、恢复引用及既有数据升级能力，具体执行环境与证明见对应主记录。当前测试账号、两位独立模拟患儿、历史时间与实际计算提示分别由 [QA63](docs/development/QA63-local-test-account.md)、[QA65](docs/development/QA65-complete-simulation-account.md)、[QA68](docs/development/QA68-historical-data-calculated-alerts.md)维护；发布到仓库的资料范围见[测试资料快照](test-data/README.md)。

| 范围 | 已有结果与日期 | 来源 |
| --- | --- | --- |
| 模型候选与本人保存 | 83 项真实候选经本人确认、保存、刷新及数据库核验；专业状态保持 unverified；证据日期见主记录 | [QA12](docs/development/QA12-real-ocr-validation.md) |
| 资料提醒、准确来源与权限恢复 | 本地站内提醒、文书原文入口、正式撤权与恢复分别通过专项检查 | [QA15](docs/development/QA15-real-material-reminders.md)、[QA16](docs/development/QA16-document-source-entry.md)、[QA17](docs/development/QA17-real-access-revocation.md) |
| 档案整理、观察范围与文书 | 真实新增内容整理、32 项检验事实读取、观察范围保存与历史、PDF 第一页手工文书、相同原件关系分别验证 | [QA19—QA23](docs/development/index.md) |
| 关系目录读取 | 2026-10-19 单次本地读取从 11.121 秒变为 3.350 秒，SQL 从 7486 条变为 2718 条；保留 266 次授权与 265 次来源核验 | [QA24](docs/development/QA24-real-relation-read-performance.md) |
| 完整资料来源 | 63 份原件，共 968528634 字节及 290 个数据库页面；五个资料分组没有登记为患者数量 | [QA25](docs/development/QA25-real-evaluation-source-inventory.md) |
| 支持访问与专业权限 | 支持受众、岗位、恢复入口与未授权核对／评估读取拒绝分别验证 | [QA26](docs/development/QA26-support-access-recovery.md)、[QA27](docs/development/QA27-real-clinical-access-scope.md) |
| 目标版本 | 2026-10-19 完成 102 项正式迁移、完整目录比较与真实数值参数传输；没有恢复既有业务资料 | [QA28](docs/development/QA28-target-runtime-compatibility.md) |
| 原件 Range 与制品 | 2026-10-19，489686822 字节 PDF 的 1024 字节读取，Python 分配峰值从 489936904 字节变为 264158 字节；63 份原件字节及响应头保持 | [QA29](docs/development/QA29-real-original-range-reads.md) |

QA29 时点：三个 RecordVersion、249 个 RecordField、83 个 ArchiveFact、249 个 ArchiveFactVersion、两个 ObservationPeriodVersion；十份整理保留 2520 项历史，一个有效结果包含 252 项。专业核对、静态与动态评估及临床事件数量为零。两项外部 OCR 结果为 `uncertain`，保留 2 CNY 与两个并发位置。上述数值引用当次私有证据，本次没有重新处理资料或调用模型。

单次本地耗时与内存测量具有准确原件、请求及环境范围。P95、P99、容量与临床效果仍由原任务要求验证。

## 全部剩余验收与解除条件

当前未通过的验收 ID 及对应任务、依赖、阻塞均以[交付记录](planning/delivery-ledger.json)为准。[文档核查记录](docs/development/DOCS01-current-documentation.md)保存本次完整 ID 快照，以下分类保留其共同条件：

| 剩余范围 | 关联任务 | 必需条件与继续入口 |
| --- | --- | --- |
| 当前证据失效或完整条件未复验 | 所有 STALE 项；T01—T60 的其余 V 项 | 核对准确源码、环境、输入与回执，按原验收断言复验，保持原状态来源 |
| OCR 未知结果及其余页面 | T40—T42、T55；QA12 | 查询原外部请求、真实计量与处理结果；未知结果确定前继续保留预留，不能通过重新调用替代核验 |
| 文书识别、保存与来源传递 | T41—T47、T54、T56 | 使用获准原页完成未验证的实际路径；51 页 PDF 当前只保存第 1 页 1208 字符的手工正文，其余 50 页正文仍需处理 |
| 专业核对、静态与动态计算 | T17—T20、T49、T50 | 真实专业身份、准确字段与核对依据、获准政策、任务执行和最终采用；现有本人确认保留原资格 |
| 发布、摘要与临床通知 | T21—T23、T25、T51、T52 | 当前内容、联系、配置批准与证据、完整生成及实际阅读；资料提醒的局部验证不代替临床通知 |
| 真实评价集合与临床效果 | T27、T32、T48、T59 | 正式用途、独立患者分组与答案、固定观察期和统计分母；临床指标保持 NOT_MEASURED |
| 完整页面与家庭任务 | T29、T30、T54、T56、T58 | 39 页、328 项适用状态、保存重读、并发与后台恢复；真实手机、微信、音频及视觉分别验证 |
| 正式容量与完整恢复 | T28、T31、T33、T57、T60 | 目标环境、真实对象存储、P95／P99、费用、RTO／RPO、完整备份恢复、运行责任与正式交付证据 |
| 参赛交付 | T60 | 核实适用官方要求、资料授权、演示与准确制品证据；现有构建不能代表比赛或生产接收 |

继续工作从唯一交付记录中核对可执行任务与全部依赖。专项软件核查可依据现有准确资料推进；专业证明、独立答案、实际接收和设备结果需要真实证据。

## 文档与交接资料

[API 与集成](docs/reference/api-integration.md)、[数据字典](docs/reference/data-dictionary.md)、[操作说明](docs/operations.md)、[Release Notes](docs/release-notes.md)及[知识索引](docs/knowledge/index.md)关联当前源码、主记录与验证入口。OpenAPI 当前覆盖身份和原件读取；完整业务接口通过路由目录、现有接口 CSV 与 JSON Schema 查询，覆盖边界在集成指南中登记。

本次前端 TypeScript 与生产构建、Django check、OpenAPI、JSON Schema、链接及交付记录检查通过。原始结果、源码摘要和未重新执行范围见 [DOCS01](docs/development/DOCS01-current-documentation.md)。原始资料、账号、模型结果及私有检查报告继续保存在忽略目录。
