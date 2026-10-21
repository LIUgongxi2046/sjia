# 监护人 H5 与支持页面

doc_id：SJIA-H5-README；status：CURRENT；last_verified：2026-10-21（Asia/Shanghai）。适用源码：`b094448d40d53ba675dd5e4a278f3e2deb107a43` 及当前文档、测试资料。维护责任：前端路由、请求与运行配置变化时更新。

使用 React 18.3.1、TypeScript 7.0.2、Vite 8.3.1、antd-mobile 5.43.0 与 React Router 7.18.4，准确依赖见 [package.json](package.json) 与 [package-lock.json](package-lock.json)。完整交接见 [当前交付说明](../CURRENT-DELIVERY-20261021.md)，任务与验收继续引用 [交付记录](../planning/delivery-ledger.json)。

## 安装与构建

Node.js `>=22.12.0`。在本目录执行：

```bash
npm ci
npm run dev
npm run typecheck
npm run build
```

`dev` 使用 `127.0.0.1:5173`，`preview` 使用 `127.0.0.1:4173`，均设置 `strictPort` 与 `Cache-Control: no-store`。`/api` 默认代理至 `127.0.0.1:8000`；连接准确后端时设置 `SJIA_API_TARGET`。启动命令不能证明账号、患儿关联或业务配置已经可用。

后端依赖与环境准备见 [项目 README](../README.md)及[操作说明](../docs/operations.md)。账号通过正常注册和授权流程取得，凭据由实际使用者管理。

`local-validation` 构建从四项显式环境变量读取测试账号，配置与验证见 [QA63](../docs/development/QA63-local-test-account.md)。普通 production 构建中的测试凭据为 null。已授权提交的模拟资料见[测试账号资料快照](../test-data/README.md)，现有本地模拟入口与功能范围见 [QA65](../docs/development/QA65-complete-simulation-account.md)、[QA68](../docs/development/QA68-historical-data-calculated-alerts.md)。

## 页面与使用关系

| 功能 | 路由入口 | 实现与验证依据 |
| --- | --- | --- |
| 首页、观察、报告与治疗登记 | `/home`、`/register/observations`、`/register/reports`、`/register/therapies` | `src/app/App.tsx`；[T10—T15](../docs/development/index.md)、[T41](../docs/development/T41.md) |
| 长期档案、批次、逐项确认、文书与重复关系 | `/archives`、`/archives/batches`、`/archives/confirm`、`/archives/grouping`、`/archives/duplicates`、`/archives/regroup` | `src/features/archives/`；[QA16—QA24](../docs/development/index.md) |
| 新增内容、来源与观察范围 | `/archives/changes`、`/archives/source`、`/records/period` | [QA19](../docs/development/QA19-real-archive-delta.md)、[QA21](../docs/development/QA21-real-observation-period-save.md)、[QA29](../docs/development/QA29-real-original-range-reads.md) |
| 记录、历史、更正与摘要 | `/records`、`/records/:recordId/history`、`/records/:recordId/correct`、`/summaries` | [T24](../docs/development/T24.md)、[T25](../docs/development/T25.md) |
| 关联、预警方案与提醒 | `/associations`、`/alert-presets`、`/account/reminders` | [T09](../docs/development/T09.md)、[方案选择](../docs/development/ORIGINAL-PRESET-SELECTION.md)、[QA15](../docs/development/QA15-real-material-reminders.md) |
| 支持端登录与受控业务 | `/support/*` | `src/features/support/SupportApp.tsx`；[QA26](../docs/development/QA26-support-access-recovery.md)、[QA27](../docs/development/QA27-real-clinical-access-scope.md) |

UI2 设计范围为 39 页、328 项状态；正式路由还包括已批准方案选择与文书重新分组。页面数量须依据实际路由与设计目录分别查询。

## 请求、保存与恢复

`src/api/client.ts` 使用同源 Cookie、`X-CSRFToken`、`X-Write-Intent`、`Idempotency-Key` 与 `X-Intent-Generation`。当前会话、患儿选择、字段与原件权限由后端核验。家庭登录与支持登录具有各自受众，支持岗位能力须经当前接口核实。

保存结果未知时保留原始正文和请求身份，通过页面提供的查询与恢复操作核对原请求。各流程的恢复范围由对应实现决定；文书创建、观察范围、资料关系与批次分别引用其请求规范。跨刷新恢复、取消与后台恢复仍需按照具体任务验收。

资料读取使用准确版本、来源与当前权限。本人确认后的字段保持独立专业状态。原件和候选属于私人内容，后台清理、原件校验与权限变更处理见对应主记录。

## 检查入口

本次文档更新仅执行真实源码、结构与构建检查，结果见 [DOCS01](../docs/development/DOCS01-current-documentation.md)。已有业务证据的日期、来源、输入和运行环境见 QA12、QA15—QA29。完整 39 页状态、实际手机、微信、真实音频、视觉、临床及生产验收继续由 T54、T56—T60 维护。

`scripts/` 中包含历史模拟与浏览器检查脚本。使用前读取其数据来源、账号要求、写入行为及适用环境；当前用户禁止模拟资料与主动视觉操作，本次没有运行此类检查。构建结果仅证明前端能够通过类型检查并生成制品。
