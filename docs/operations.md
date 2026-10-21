# 运行、业务操作与 FAQ

doc_id：SJIA-OPERATIONS；status：CURRENT；last_verified：2026-10-21（Asia/Shanghai）。适用源码：`b094448d40d53ba675dd5e4a278f3e2deb107a43` 及当前文档。维护责任：运行、权限、配置及操作入口变化时核查。

## 环境与启动

开发入口见[项目 README](../README.md)及 [H5 说明](../guardian-h5/README.md)。部署配置见[存储与运行](../deploy/runtime-storage.md)、[Compose](../deploy/compose.yaml)和[可观测性](../deploy/observability/README.md)。当前使用 Django、PostgreSQL、Redis、Celery，文件服务按准确配置选择 local 或私有 S3，见[存储选择](../backend-design/s3-storage-selection.md)。目标 Python／PostgreSQL 兼容证据见 [QA28](development/QA28-target-runtime-compatibility.md)，105 项迁移及既有资料升级见 [QA43](development/QA43-existing-data-upgrade.md)。

| 配置类别 | 变量与核查内容 |
| --- | --- |
| 身份 | `SJIA_ENVIRONMENT`、`SJIA_SECRET_KEY`、`SJIA_ALLOWED_HOSTS`；核实准确环境、秘密来源及主机 |
| 数据库 | `SJIA_DB_NAME`、`SJIA_DB_USER`、`SJIA_DB_PASSWORD`、`SJIA_DB_HOST`、`SJIA_DB_PORT`；读取准确身份与迁移计划 |
| Redis 与任务 | `SJIA_REDIS_URL`、`SJIA_CELERY_BROKER_URL`；API、Worker、Beat 使用准确队列与环境 |
| 文件与证据 | `SJIA_FILE_OBJECT_ROOT`、`SJIA_CONFIG_EVIDENCE_ROOT`、`SJIA_CONFIG_EVIDENCE_MAX_BYTES`；API 与 Worker 共享准确对象及证明目录 |
| 模型与语音 | `SJIA_ARK_API_KEY`、`SJIA_ARK_BASE_URL`、`SJIA_ARK_MODEL`、`SJIA_ARK_VISION_MODEL`、`SJIA_SPEECH_MODEL_DIR`；启用情况继续核查运行配置及能力状态 |
| 生产代理与观测 | `SJIA_TRUST_PROXY_TLS`、`SJIA_INTERNAL_PROBE_NETWORKS`、`SJIA_OTEL_EXPORT_ENABLED`、`SJIA_OTEL_TRACES_ENDPOINT`；按实际网络与收集目标配置 |

环境变量只提供程序配置，业务还需通过 EnvironmentGate、准确运行版本、来源、批准、当前制品验证及权限检查。配置文件不保存到 Git，说明文件只登记变量名。

开发检查在项目根目录执行：

```bash
backend/.venv/bin/python backend/manage.py check
backend/.venv/bin/python backend/manage.py showmigrations --plan
backend/.venv/bin/python backend/manage.py migrate --plan
```

目标数据库身份确认后，按实际维护授权执行正式迁移。API 与任务进程使用该环境配置。生产入口由 `deploy/start_api.py`、Gunicorn 与进程采用检查共同管理；任务入口与队列以 Compose 为准。数据库恢复后须满足恢复标记和重新采用条件。

## 健康与能力

| 内部入口 | 检查含义 |
| --- | --- |
| `/internal/liveness` | 当前进程能够响应 |
| `/internal/readiness` | 数据库、迁移、broker、运行配置与进程采用情况 |
| `/internal/capabilities` | 配置与能力状态，以及未由健康探针验证的临床资格 |
| `/internal/metrics` | Prometheus 业务任务与请求聚合指标 |

这些入口受实际 socket 来源限制，默认仅允许 loopback；nginx 的 `/internal/` 返回 404。ready 需要同时核实 `runtime_admission=admitted_current` 与准确版本；临床、通知接收及业务保存仍需各自证据。

`deploy/probe_runtime.py` 使用固定本地 8000 端口，适用于该部署拓扑。历史验收中的 8002／8003 地址只说明当次环境；检查其他端口应使用明确目标的本地只读请求。

## 家庭资料操作

| 场景 | 页面与操作 | 成功结果与限制 |
| --- | --- | --- |
| 登记家庭观察、报告或治疗 | `/register/observations`、`/register/reports`、`/register/therapies`；核对原值、时间、单位与出处后保存 | 重新读取准确记录版本；本人确认保留独立专业状态 |
| 查看长期档案 | `/archives`、`/archives/batches`；选择准确文书与原页 | 页面、版本及原件对应；手工正文与识别结果按来源分别呈现 |
| 核对提取候选 | `/archives/confirm`；逐项核对原文、时间与语境后确认 | 保存正式记录及准确字段来源；未知信息继续保留未知 |
| 文书和重复资料 | `/archives/grouping`、`/archives/duplicates`、`/archives/regroup`；按原页保存分类或关系 | 新版本和历史均可读取；字节关系不产生同次采样证明 |
| 查看新增内容 | `/archives/changes`；选择准确批次，查询原请求及结果 | 读取当前有效正文与原件来源；没有比较基准时保留相应限制 |
| 设置观察范围 | `/records/period`；在当前允许保存时设置查询起止范围与本人说明 | 当前版本、原回执及历史可读取；范围不能替代来源中的医学时间 |
| 设置资料提醒 | `/account/reminders`；启用、暂停、延后或关闭本人目标的提醒 | 核对时区、安静时段、实际到期与本人阅读；本地验证范围见 [QA15](development/QA15-real-material-reminders.md) |
| 查看摘要与提示 | `/summaries`、`/alerts` | 当前获准正文、来源与联系均满足资格；没有资格时按接口提示处理 |

本人保存、版本更正、专业核对、临床计算与家庭发布分别管理。QA22 的 51 页 PDF 当前保存第 1 页 1208 字符的手工正文，其余 50 页未完成识别。完整资料验证与剩余工作见[当前交接](../CURRENT-DELIVERY-20261021.md)。

## 支持端配置与发布

支持入口为 `/support/*`，准确页面与权限来自 `guardian-h5/src/features/support/SupportApp.tsx`。登录后核验当前支持受众及岗位；真实专业操作还需准确患儿和字段授权。

配置及内容采用流程使用已有配置、来源、批准、验证和启用接口，见 [API 指南](reference/api-integration.md)、[配置证明](../backend-design/configuration-evidence.md)及 [T51](development/T51.md)。修改 Prompt、规则、单位映射或模型配置时，重新核对来源、版本、实际制品、验证结果和使用方。当前没有已验证的自动 Prompt 热更新或每日临床评测服务。

本地站内消息与临床通知的条件分别见[原生通知](../deploy/native-notifications.md)及[资料提醒](../deploy/material-reminders.md)。真实发送、受理、送达和阅读分别保留准确回执，外部提供方回调当前不受支持。

## FAQ

| 现象 | 确认与处理 |
| --- | --- |
| 401，需要登录 | 返回对应家庭或支持登录入口，重新取得会话与 CSRF；核对当前账号与受众 |
| 403，当前操作未授权 | 查看当前患儿、关联、字段、完整原件及岗位范围；支持端使用手动核验入口，依据正式授权恢复 |
| 原件 409 或 410 | 核对原件状态、登记大小、存储字节与处置记录；内容已处置时按实际批准状态处理 |
| 原件 416 | 改用单个合法 bytes 范围，核对 `Content-Range` 中登记的大小；多范围请求会被拒绝 |
| OCR 返回 uncertain 或持续占用预算 | 查询原请求与实际提供方结果，保持原有费用和并发预留；只有结果确定且允许重试后继续原流程 |
| 保存响应未知 | 查询原写入请求并核对保存资源；保持相同正文、request ID 与 generation，依照该流程的恢复操作处理 |
| 观察范围没有保存操作 | 核对准确 observation 保留策略与本人写入权限；合法读取继续按当前字段范围执行 |
| 整理结果失效 | 查询准确来源、权限、配置与制品；历史正文保留，满足当前条件后通过原流程生成新结果 |
| 摘要正文为空或提示不可用 | 核查对应内容、联系、字段配置与批准资格；不能依据进程 ready 推断家庭内容可发布 |
| 语音入口不可用 | 核对 `SJIA_SPEECH_MODEL_DIR`、实际能力开关、安全上下文与录音权限；真实音频及手机操作仍需实际验收 |

## 备份、恢复与运行责任

正式流程见 [recovery.md](../deploy/recovery.md)及[内容处置恢复](../deploy/content-disposition-recovery.md)。`deploy/recovery.py` 默认只输出计划，真实执行需要明确环境、数据库集群、维护窗口、负责人、保存位置与准确授权。

一致性备份同时覆盖 PostgreSQL、完整文件对象与配置证明。恢复进入独立数据库和目录，保持外发隔离；恢复后继续核对字节、版本、权限、处置、撤权、禁用决定及未知外部结果。恢复标记、重新采用和实际 RTO／RPO 各自核验。QA28 的空库迁移没有验证完整既有数据恢复。

本次文档检查没有停止服务、执行迁移、创建账号、改变业务授权、发送消息或调用模型。生产接收和真实备份恢复仍由 T33／T57／T60 维护。
