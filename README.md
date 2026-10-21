# sJIA 并发症预警平台

面向全身型幼年特发性关节炎（sJIA）患儿家庭，支持症状观察、检验报告、治疗记录、长期档案、资料来源与风险提示。

平台用于辅助家庭整理资料与查看提示，不能替代医生诊断、治疗建议或急诊服务。临床内容审核、真实患者使用和生产环境交付状态，请以项目验收文档及正式批准记录为准。

文档核实日期：2026-10-21（Asia/Shanghai）。源码包含远程 `b094448d40d53ba675dd5e4a278f3e2deb107a43` 的全部改动及当前文档、测试资料。当前交接见 [CURRENT-DELIVERY-20261021.md](CURRENT-DELIVERY-20261021.md)，文档入口见 [知识索引](docs/knowledge/index.md)。

## 项目与验收

| 范围 | 当前依据 |
| --- | --- |
| 需求 | PRD 2.0 的 FR-01—FR-22、30 项承诺、60 项实施任务；UI2 设计目录包含 39 页及 328 项状态 |
| 任务状态 | [交付记录](planning/delivery-ledger.json)：60 项任务均为 IMPLEMENTED；验收 STALE 11 项、NOT_RUN 49 项，当前没有 PASS 项 |
| 真实资料专项 | [QA12](docs/development/QA12-real-ocr-validation.md)及 [QA15—QA29](docs/development/index.md)：真实候选保存、档案与来源、提醒、访问恢复、目标版本兼容及原件读取 |
| 最新软件检查 | 本次文档检查与构建结果见 [DOCS01](docs/development/DOCS01-current-documentation.md)；业务证据保留原日期和数据范围 |
| 完整验收 | 实际手机、微信、真实音频、独立临床答案、正式容量和生产备份恢复等仍需原任务验收 |

QA25／QA29 的资料来源记录包含 63 份原件和 290 个数据库页面；QA12 记录 83 项候选的本人确认及保存。本人确认后的专业状态保持 `unverified`。源码功能、专项通过和完整业务验收分别引用对应证据。

## 项目组成

| 目录 | 内容 |
| --- | --- |
| `guardian-h5/` | React、TypeScript、Vite、antd-mobile 家庭页面与支持页面 |
| `backend/` | Django、Django REST Framework、PostgreSQL、Celery、Redis 业务服务 |
| `data-design/` | 数据结构与数据库设计资料 |
| `alert-design/` | 风险提示规则及呈现设计 |
| `frontend-design/` | 前端方案与页面设计资料 |
| `backend-design/` | 服务端设计资料 |
| `ui-delivery/` | 页面原型与设计交付文件 |
| `deploy/` | 容器构建及部署相关配置 |
| `docs/`、`planning/` | 开发记录、唯一交付记录、验收记录、知识说明和项目证据 |
| `tests/` | 集成、安全、性能及其他质量检查代码 |

## 功能范围

- 患儿健康档案、症状观察和医学时间记录。
- 检验报告与治疗资料整理，保留来源和更正信息。
- 文书归组、重复资料关系、新增内容整理与观察范围。
- 病程时间线、风险提示、就诊摘要及本人资料提醒。
- 前端连接 Django 接口，支持本地开发与项目验收流程。

具体功能状态和验收边界见 [监护人 H5 说明](guardian-h5/README.md)、[当前交接](CURRENT-DELIVERY-20261021.md)与[交付记录](planning/delivery-ledger.json)。

## 本地开发

Node.js 要求 `>=22.12.0`；当前工作区 Node 为 `24.13.0`。后端依赖锁定于 [requirements.lock.txt](backend/requirements.lock.txt)。目标为 Python 3.13／PostgreSQL 17；[QA28](docs/development/QA28-target-runtime-compatibility.md) 已验证 Python 3.13.13／PostgreSQL 17.11 的空库迁移。已有本地验收环境使用 Python 3.12.7／PostgreSQL 18.4。

准备独立开发数据库、Redis、环境变量和批准配置。环境变量名称参考 [backend/.env.example](backend/.env.example)，具体配置与运行要求见 [操作说明](docs/operations.md)。已有环境应核对其配置和数据库身份；示例文件中的具体值不代表当前服务身份或有效凭据。

项目根目录执行：

```bash
backend/.venv/bin/python -m pip install -r backend/requirements.lock.txt
backend/.venv/bin/python backend/manage.py check
backend/.venv/bin/python backend/manage.py migrate --plan
backend/.venv/bin/python backend/manage.py runserver 127.0.0.1:8000
```

数据库迁移的实际执行须使用准确目标环境与备份依据。以上 `migrate --plan` 仅查看计划。业务可用还要求当前运行配置、来源、权限、用途与批准条件满足。

另一个终端执行：

```bash
cd guardian-h5
npm ci
npm run dev
```

Vite 默认端口为 `5173`，同源 `/api` 代理到 `127.0.0.1:8000`。已知后端地址可通过 `SJIA_API_TARGET` 指定。构建使用 `npm run build`，其中包含 TypeScript 检查。实时进程与可用端口须通过本地健康检查核实，历史报告中的地址仅表示当次环境。

## 项目文档

- [当前交接与完整剩余项](CURRENT-DELIVERY-20261021.md)
- [接口集成指南](docs/reference/api-integration.md)、[身份与原件 OpenAPI](docs/reference/openapi.json)、[实际路由目录](docs/reference/api-route-inventory.json)
- [数据字典](docs/reference/data-dictionary.md)、[全部 ORM 字段](docs/reference/orm-data-dictionary.md)
- [操作说明与 FAQ](docs/operations.md)、[版本说明](docs/release-notes.md)
- [Agent 设计说明](sJIA合并MAS预警平台Agent设计说明.md)
- [测试账号资料](test-data/README.md)：独立模拟账号、两位虚构患儿及已有测试记录 |
- [产品需求文档（PRD）](sJIA合并MAS预警平台PRD.md)
- [系统高层设计（HLD）](sJIA合并MAS预警平台HLD.md)
- [前端低层设计（LLD）](sJIA合并MAS预警平台前端LLD.md)
- [后端低层设计（LLD）](sJIA合并MAS预警平台后端LLD.md)
- [数据低层设计（LLD）](sJIA合并MAS预警平台数据LLD.md)
- [预警编排低层设计（LLD）](sJIA合并MAS预警平台预警编排LLD.md)
- [开发记录索引](docs/development/index.md)
- [验收矩阵](docs/testing/acceptance-matrix.md)
- [本地语音服务说明](docs/knowledge/local-speech.md)

## 数据与安全

密钥、账号、真实原件、患者原文、数据库、模型响应和私有检查结果保存在项目忽略目录或批准的秘密管理位置。会话使用 Cookie 与 CSRF；家庭关联、支持岗位、字段及完整原件权限分别检查。业务请求结果未知时查询原请求与外部结果，继续保留准确费用和并发预留。

## 作者与职业介绍

作者：刘浩南（刘浩贤）。拥有12年医疗健康与产业互联网产品经验，曾任金域医学产品与解决方案部总监，目前从事独立开发，关注广州的 AI 产品总监、AI 产品负责人与 AI 产品经理机会。

- [职业介绍与代表案例](https://doctor-graph.com.cn/about/)
- [sJIA 产品介绍与个人工作](https://doctor-graph.com.cn/products/sjia/)
