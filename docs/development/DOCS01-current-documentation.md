# DOCS01 当前项目文档维护

doc_id：SJIA-DOCS01；文档有效性：CURRENT；last_verified：2026-10-21（Asia/Shanghai）。本记录管理本次文档任务，T01—T60 状态继续引用 [planning/delivery-ledger.json](../../planning/delivery-ledger.json)。

## 请求与范围

用户于 2026-10-21 要求使用 haonan-s012-docs，根据最新项目情况更新相关文档。本次维护项目与 H5 入口、当前交接、API 与数据字典、操作说明、FAQ、Release Notes、知识关系及开发索引。没有授权发布站点或新增外部发送；本次没有执行此类操作。

来源检索包含 README、CURRENT-DELIVERY、PRD、HLD、LLD、交付记录、开发索引、QA12、QA14—QA29、当前 Django 路由与 handler、Model、正式 migrations、前端路由与请求、package.json、Vite 配置及 deploy 工具。采用当前源码及真实证据；旧模拟记录仅按自身范围引用。

## 版本与生成依据

| 项目 | 依据 |
| --- | --- |
| 源码 | b094448d40d53ba675dd5e4a278f3e2deb107a43 的全部改动及本次文档与测试资料；实际提交以 main 的 Git 记录为准 |
| 本次既有用户文件 | Agent 设计说明、主办方 V1 Markdown 与会议文书在任务开始前已经存在；保留其内容 |
| 当前运行资料 | QA28／QA29 的准确源码、环境与原始报告；历史 ready 状态不直接改写为本次运行结果 |
| ORM 字典 | Django Model 元数据、PostgreSQL backend db_type()、枚举和 ORM 约束；128 个模型、1525 个字段；未查询业务数据库 |
| 路由目录 | Django URLResolver 与 DRF 元数据；385 个注册路径，包含版本别名及内部探针 |
| OpenAPI | 七个身份及原件路径，逐项核查路由、HTTP 方法、实际 handler、会话、支持岗位与错误返回 |
| 当前任务来源 | 30 项承诺、60 项 IMPLEMENTED；验收 STALE 11 项、NOT_RUN 49 项，没有 PASS 项 |

## 文档产物

- [README](../../README.md)、[H5 说明](../../guardian-h5/README.md)与[当前交接](../../CURRENT-DELIVERY-20261021.md)。
- [API 指南](../reference/api-integration.md)、[OpenAPI](../reference/openapi.json)、[实际路由目录](../reference/api-route-inventory.json)。
- [数据字典](../reference/data-dictionary.md)与[ORM 字段](../reference/orm-data-dictionary.md)。
- [操作说明及 FAQ](../operations.md)、[版本说明](../release-notes.md)。
- [知识索引](../knowledge/index.md)、[业务与代码关系](../knowledge/prd2-delivery.md)及[开发索引](index.md)。

文档源码摘要、命令输出和核查报告保存于已忽略的 `.docs-work/`。生成程序读取标准库 JSON、Django 元数据与成熟格式校验库；文件编辑使用 apply_patch。

## 检查结果

当前文档任务状态：ACCEPTED，范围为本次更新的文档准确性、来源关系与结构检查；业务证据日期及原任务状态保持。

| 命令或检查 | 实际结果 | 原始输出与适用范围 |
| --- | --- | --- |
| `npm run build`，cwd 为 guardian-h5 | 退出码 0；包含 TypeScript 检查，Vite 8.3.1 完成当前代码构建，耗时 2.88 秒 | `.docs-work/merged-build.log`；构建文件保存在 guardian-h5/dist；存在大于 500 kB 的部分输出文件提示 |
| `backend/.venv/bin/python backend/manage.py check` | 退出码 0；System check identified no issues | `.docs-work/django-check.log`；Django 5.2.17，Python 3.12.7 |
| `.qa-work/QA28/venv/bin/python .docs-work/inspect_sources.py` | 退出码 0；385 个注册路径、265 个 handler、128 个 ORM 模型及 1525 个字段 | `.docs-work/merged-source-inspection.log`、`source-summary.json`；Python 3.13.13；导入模型与元数据，没有业务数据库查询 |
| `backend/.venv/bin/python .docs-work/audit_documents.py` | 文档链接、七个 OpenAPI 路径、当前 JSON Schema、60 项唯一任务索引及完整未通过 ID 校验 | `.docs-work/document-audit.json`、`document-audit.log`；openapi-spec-validator 0.9.0、markdown-it-py 4.2.0、jsonschema 4.26.0 |
| S000 `validate_delivery.py planning/delivery-ledger.json`，使用 QA28 Python 环境 | valid=true、complete=false；当前没有 PASS 验收回执 | `.docs-work/merged-delivery-validation.log`；没有修改交付记录 |
| 2026-10-21 本地 API 8002／8003 的 `/internal/readiness` | 两者 HTTP 200，status=ready、runtime_admission=admitted_current | `.docs-work/previous-readiness.json`、`real-readiness.json`；临床资格为 NOT_VERIFIED_BY_HEALTH_PROBE |
| 真实资料 API 8003 的 `/internal/liveness` 与 H5 5177 入口 | 两者 HTTP 200 | `.docs-work/real-liveness.json`、`frontend-response.html`；只证明当前进程与入口响应 |
| `git diff --check` | 退出码 0 | 文档空白与补丁格式检查 |

文档检查依赖安装于 `.docs-work/dependencies`，项目依赖文件保持原有内容。源码、文档输入摘要保存于 `.docs-work/document-audit.json`。原件、业务资料、私有配置、账号与模型响应没有加入本次文档产物。

## 全部未通过的业务验收

2026-10-21 读取交付记录的完整快照：V01、V02、V03、V04、V05、V06、V07、V08、V09、V10、V11、V12、V13、V14、V15、V16、V17、V18、V19、V20、V21、V22、V23、V24、V25、V26、V27、V28、V29、V30、V31、V32、V33、V34、V35、V36、V37、V38、V39、V40、V41、V42、V43、V44、V45、V46、V47、V48、V49、V50、V51、V52、V53、V54、V55、V56、V57、V58、V59、V60，共 60 项。

每项的 required_checks、任务依赖、阻塞及证据继续查询唯一交付记录。解除条件和全部共同剩余范围见[当前交接](../../CURRENT-DELIVERY-20261021.md)。本次没有重新执行临床、手机、微信、音频、视觉、正式容量、备份恢复或模型调用；OpenAPI 全业务汇总仍未形成，现有接口继续由完整路由目录、领域 Schema 和业务指南支持。
