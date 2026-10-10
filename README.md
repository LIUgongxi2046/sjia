# sJIA 并发症预警平台

面向全身型幼年特发性关节炎（sJIA）患儿家庭的健康记录与并发症风险提示项目。项目围绕症状观察、检验报告、治疗记录、病程时间线和就诊摘要整理提供支持。

平台用于辅助家庭整理资料与查看提示，不能替代医生诊断、治疗建议或急诊服务。临床内容审核、真实患者使用和生产环境交付状态，请以项目验收文档及正式批准记录为准。

## 项目组成

| 目录 | 内容 |
| --- | --- |
| `guardian-h5/` | 面向监护人的 React、TypeScript、Vite 移动端网页 |
| `backend/` | Django 服务端、接口、业务模块和后台任务 |
| `data-design/` | 数据结构与数据库设计资料 |
| `alert-design/` | 风险提示规则及呈现设计 |
| `frontend-design/` | 前端方案与页面设计资料 |
| `backend-design/` | 服务端设计资料 |
| `ui-delivery/` | 页面原型与设计交付文件 |
| `deploy/` | 容器构建及部署相关配置 |
| `docs/` | 开发记录、验收记录、知识说明和项目证据 |
| `tests/` | 集成、安全、性能及其他质量检查代码 |

## 功能范围

- 患儿健康档案、症状观察和医学时间记录。
- 检验报告与治疗资料整理，保留来源和更正信息。
- 病程时间线、风险提示及就诊摘要。
- 前端连接 Django 接口，支持本地开发与项目验收流程。

具体功能状态和验收边界见 [监护人 H5 说明](guardian-h5/README.md)、[验收矩阵](docs/testing/acceptance-matrix.md) 与 [QA04 验收记录](docs/development/QA04-input-feedback.md)。

## 本地开发

前端使用 Node.js `>=22.12.0`。可在 `guardian-h5/` 目录安装项目依赖并启动 Vite：

```bash
cd guardian-h5
npm install
npm run dev
```

前端默认地址为 `http://127.0.0.1:5173`。前端构建和类型检查命令为 `npm run build`。后端需要 Python 依赖、PostgreSQL、Redis 及本地环境配置；配置变量示例见 [backend/.env.example](backend/.env.example)，开发验收步骤见 [监护人 H5 说明](guardian-h5/README.md)。

## 项目文档

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

请勿将密钥、患者身份信息或未获授权的病例资料提交到版本库。环境变量文件 `backend/.env` 不应提交；病例演示数据和本地验收产物按仓库忽略规则管理。部署前请根据目标环境设置密钥、允许访问的主机、数据库、Redis、存储目录和外部服务配置，并核实数据授权与临床审批要求。
