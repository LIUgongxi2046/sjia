# API 与集成指南

doc_id：SJIA-API-INTEGRATION；status：CURRENT；last_verified：2026-10-21（Asia/Shanghai）。适用源码：`f05dbcf106e70d890a5574385fd63b3ddd502146`。维护责任：路由、Schema、身份、错误或请求恢复变化时同步。

## 接口依据与覆盖

| 文件 | 覆盖与使用方式 |
| --- | --- |
| [OpenAPI 3.0.3](openapi.json) | 本次核查的家庭会话、支持会话与原件读取七个路径；仅包含该明确范围 |
| [实际路由目录](api-route-inventory.json) | 从当前 Django URLResolver、DRF 元数据及内部探针的 require_GET 生成的全部路径和方法，包含 `/api`、`/api/v1`、`/api/v2` 及内部探针 |
| [原接口目录](../../backend-design/api-catalog.csv)、[v2 目录](../../backend-design/v2-api-catalog.csv)、[语音目录](../../backend-design/voice-api-catalog.csv) | 接口业务名称、输入输出 Schema、权限和任务关系；其中设计条目需结合当前路由和具体 handler 核实 |
| [基础协议](../../backend-design/protocol.schema.json)、[v2 协议](../../backend-design/v2-protocol.schema.json) | 完整字段与 JSON Schema 2020-12 定义；具体领域还使用其独立 Schema |
| [后端 LLD](../../sJIA合并MAS预警平台后端LLD.md)、[v2 服务行为](../../backend-design/v2-service-behavior.md) | 权限、事务、任务和版本设计依据 |

当前全业务接口尚未汇总为一份完整 OpenAPI。现有 JSON Schema 2020-12 中的 `$defs`、`const`、联合类型等继续由项目校验器处理，接入时使用准确领域 Schema。OpenAPI 的七个路径不包含文书、观察范围、专业核对和评估的全部请求定义。

`backend/config/urls.py` 将原接口同时注册在 `/api/` 与 `/api/v1/`，新增接口注册在 `/api/v2/`。v2 使用自己的写入意图与任务 handler；调用方必须配对相同版本的业务操作及恢复接口。路径没有统一追加尾部斜线。

## 会话与权限

1. 同源请求 `GET /api/auth/bootstrap`，保存 Cookie 并读取 `csrf_token`。
2. 家庭账号调用 `POST /api/auth/login`；支持账号调用 `POST /api/v2/support/session/login`。正文使用 `username`、`password`，携带 `X-CSRFToken` 与同一 Cookie。支持登录的 `username` 长度为 1—128、包含非空白字符，`password` 长度为 1—4096，禁止额外字段。
3. 登录成功后更新响应中的 `csrf_token`。会话与 CSRF 在登录时轮换。家庭 `GET /api/session` 返回 `principal_id`、`account_status`、`session_binding`、`expires_at`、`audience`、`capabilities`、`csrf_token`、`selected_patient_id`。支持 `GET /api/v2/support/session` 返回对应支持身份及岗位能力。
4. 后续写入携带当前 Cookie、`X-CSRFToken` 与该操作要求的写入意图头。退出使用 `POST /api/session/logout`。

生产 Cookie 名称为 `__Host-sjia_session`，包含 Secure、HttpOnly、SameSite=Lax，基础有效期 12 小时。明确的本地 DEBUG 环境使用 `sjia_session`。实际有效期以服务端会话及响应 `expires_at` 为准；主体状态与 `auth_epoch` 仍会使会话失效。

家庭关联、支持受众、岗位、患儿、字段、记录、原件及用途分别核验。知道资源 UUID 不会取得访问权限。家庭 `capabilities` 数组不能代替具体业务授权。外部身份入口 `/auth/start`、`/auth/complete` 当前返回 `503 AUTH_PROVIDER_UNAVAILABLE`。

## 写入、任务与原请求

项目客户端见 `guardian-h5/src/api/client.ts`。通用写入头为 `X-Write-Intent`、`Idempotency-Key`、`X-Intent-Generation`。正文、operation、target、schema_version 与原请求保持准确绑定。

响应丢失或连接中断时，通过同一 API 版本的 `/requests/{intent_id}` 查询；存在 request ID 查询能力的流程使用其对应领域规范。只有明确允许重试时才调用 `/write-intents/{intent_id}/retry`，并沿用准确正文、请求与 generation。任务返回后继续核对 Task 与最终资源，HTTP 202 仅表示相应处理状态。

| 业务 | 请求响应规范 | 实现与使用方 |
| --- | --- | --- |
| 观察范围及历史 | [observation-period.schema.json](../../backend-design/observation-period.schema.json) | `api/views_observation_periods.py`、`records/periods.py` → `/records/period`；[QA21](../development/QA21-real-observation-period-save.md) |
| 新增内容整理 | [archive-delta.schema.json](../../backend-design/archive-delta.schema.json) | `api/views_archive_delta.py`、`summaries/delta.py` → `/archives/changes`；[QA19](../development/QA19-real-archive-delta.md) |
| 文书、原页与目录 | [archive-search.schema.json](../../backend-design/archive-search.schema.json)、[v2-protocol.schema.json](../../backend-design/v2-protocol.schema.json) | 文书 handler 与 `archives` 领域 → `/archives`、`/archives/grouping`、`/archives/source` |
| 原件信息 | [original-read.schema.json](../../backend/files/original-read.schema.json) | `api/views_originals.py`；记录版本原件信息与文件内容权限分别核验 |
| 已批准预警方案 | [原始需求补充接口](../../backend-design/original-scope-api.md) | `views_alert_presets.py` → `/alert-presets`；选择使用原 `/api/write-intents` |

## 原件内容与 Range

`GET /api/files/{file_id}/content` 与 `/api/v1/files/{file_id}/content` 使用 `views_family3.file_content`。该接口禁止读取 `purpose=deidentified` 的用途副本，先核验准确原件及当前授权。

| 条件 | 返回与处理 |
| --- | --- |
| 没有 Range | 200，完整原始字节及准确媒体类型 |
| 单个合法 bytes 范围 | 206，准确 `Content-Range` 与原始字节；支持明确区间、开放结束及后缀范围 |
| 无法满足、多范围或非法单位 | 416，`Content-Range: bytes */{size}`；该响应正文为空 |
| 原件不可用、字节缺失或范围长度不符 | 409；具体 `code` 分别表达当前原因 |
| 未登录或权限不足 | 401／403；Range 继续要求完整原件权限 |
| 已按批准范围处置 | 410，`CONTENT_DISPOSED` |

正常响应包含 `Accept-Ranges: bytes`，私人响应设置 `Cache-Control: no-store, private`。Range 使用登记的不可变大小与 `LocalFileStore.get_range`，长度不符返回 `FILE_OBJECT_SIZE_MISMATCH`。QA29 的内存和字节检查范围见 [主记录](../development/QA29-real-original-range-reads.md)。本地存储与对象存储能力以 [存储说明](../../deploy/runtime-storage.md)为准。

## 错误与接入边界

`api/base.py` 的常用 JSON 错误字段为 `code`、`message`、`retryable`、`recovery`、`field_errors`。不能要求所有错误都具有旧设计中的 `trace_id` 或 `schema_version`。HTTP 416、框架方法拒绝和内部探针具有各自响应格式；调用方还需读取 HTTP 状态与媒体类型。

当前仓库通过 Fetch 客户端接入，没有独立发布的外部 SDK。任务状态采用请求查询与任务读取；当前交接没有 SSE 接口。`/integrations/{provider_id}/receipts` handler 当前拒绝外部提供方回调，返回 `NOTIFICATION_EXTERNAL_CALLBACK_UNSUPPORTED`；原生站内通知使用本人会话及签名票据，实际要求见[通知说明](../../deploy/native-notifications.md)。

接口文档和结构验证不能证明真实提供方、专业核对、临床效果或生产接入通过。对应剩余工作见 [当前交接](../../CURRENT-DELIVERY-20261021.md)。
