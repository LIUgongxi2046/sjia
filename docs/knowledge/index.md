# 项目知识与文档索引

doc_id：SJIA-KNOWLEDGE-INDEX；status：CURRENT；last_verified：2026-10-21（Asia/Shanghai）。适用源码：`b094448d40d53ba675dd5e4a278f3e2deb107a43` 及当前文档。维护责任：S012；需求、源码、运行或证据变化时核查关联页面。

文档 ID、路径、受众、来源、生成方式、维护责任与有效性见[文档资产索引](../document-inventory.csv)。

## 当前入口

| 文档 | 受众与内容 | 来源、维护与有效范围 |
| --- | --- | --- |
| [项目 README](../../README.md) | 项目组成、开发命令与入口 | 当前源码、package.json 与本地检查；2026-10-21 |
| [当前交接](../../CURRENT-DELIVERY-20261021.md) | 版本、全部剩余验收、运行身份与证据 | Git、交付记录、QA12—QA29；历史证据日期单独标明 |
| [测试账号资料](../../test-data/README.md) | 两位模拟患儿的记录、历史与生成附件 | QA65／QA68 的既有测试资料及用户明确提交授权；排除真实资料与秘密 |
| [PRD 2.0 实施依据](prd2-delivery.md) | 业务概念、前端入口、API、数据与任务关系 | 当前需求、领域代码及对应主记录；2026-10-21 |
| [API 指南](../reference/api-integration.md) | 会话、权限、原请求、原件范围及接口目录 | Django 路由、具体 handler、JSON Schema；2026-10-21 |
| [OpenAPI](../reference/openapi.json) | 七个身份及原件路径 | SJIA-API-INTEGRATION 的明确覆盖范围；2026-10-21 |
| [实际路由目录](../reference/api-route-inventory.json) | 全部注册路径、允许方法与 handler | 当前 Django URLResolver 与 DRF 元数据；2026-10-21 |
| [数据字典](../reference/data-dictionary.md)、[ORM 字段](../reference/orm-data-dictionary.md) | 实体语义、字段、类型、枚举、关联和约束 | 当前 Model、正式 migrations 与 QA28；2026-10-21 |
| [操作说明](../operations.md) | 配置、页面操作、FAQ、健康与恢复 | 实际路由、配置、任务及 deploy 工具；2026-10-21 |
| [版本说明](../release-notes.md) | 已提交行为、测量与迁移事项 | f05dbcf 相对 8ca48b9 的变更、对应 QA 记录；2026-10-21 |
| [Agent 设计说明](../../sJIA合并MAS预警平台Agent设计说明.md) | Prompt、固定编排、结构化知识、模型与评价 | 文件自身登记的源码、来源及 2026-10-21 核查范围 |
| [开发记录索引](../development/index.md) | task_id、需求、identifier 与唯一主记录 | T01—T60 状态引用唯一交付记录，专项状态引用主记录 |
| [本地语音](local-speech.md) | 即时转写、持久 VoiceInput、模型与参数 | 2026-10-21 源码核查与原 QA04 证据分别记录；真实音频及设备仍待验收 |
| [临床批准策略](clinical-approval-strategy.md) | D04 规则来源与批准说明 | 专业内容需结合预警编排、当前规则和独立批准；本次未重审医学依据，NEEDS_REVIEW |

新建文档的 doc_id、适用版本、来源、维护责任与 last_verified 由各自正文维护。未完整复核的需求和设计文件继续保留原文状态与日期；本次导航更新不会改变其批准或验收状态。

## 状态与证据

[交付记录](../../planning/delivery-ledger.json)管理 T01—T60 的实施与验收；[开发索引](../development/index.md)负责检索；[DOCS01](../development/DOCS01-current-documentation.md)记录本次文档检查范围与结果。业务概念、页面、API、数据实体及实际验证关系见[实施依据](prd2-delivery.md)。

当前文档结构有效性、历史业务结果和运行可用性分别维护。源码或环境变化后，根据受影响输入决定证据是否仍适用；仅通过链接或构建检查不能刷新业务证据时间。
