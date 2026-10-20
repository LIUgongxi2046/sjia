# QA61 独立登录与预警流程验收

状态为 ACCEPTED，文档有效性 CURRENT，last_verified 为 2026-10-20。本记录为本专项唯一状态来源。验收范围为独立登录、会话恢复及用户选择的“预警计算 → 提示展示”，关联 FR-01、FR-06、FR-09、AC-H5-05、T08、T09、T50、T51、QA27、QA59。完整临床评价继续归属 T59。

## 当前依据与实现

工作版本为 main e9ef845 及 QA62／QA63 的当前修改，开发目录为 codex/current-design-reception。已检索 App、LoginPage、UI2FamilyFrame、AccessProvider、SessionBoundary、client、useFamilyResource、UI2ReadState、UI2AccountPage、assessmentText、UI2AlertsPage 及关联任务记录。当前运行和验收明细见 [QA62](QA62-literature-alert-runtime.md) 与 [QA63](QA63-local-test-account.md)。

家庭业务路由执行统一会话检查，公开提供 /login 和 /account/data-use。匿名登录页面不显示家庭导航；退出成功进入 /login。业务资料请求返回当前会话的 401 时，清除身份、患儿范围及私有页面状态。页面参数清理核验浏览器当前地址。Web Locks、会话版本、受众、授权期限及来源资格继续执行。

本地测试登录在服务端认证后，通过 /api/patient-selection 保存获准患儿选择，再读取会话和关联。选择请求使用登录回执中的 CSRF，只有准确患儿选择和访问范围有效时才进入首页。支持入口会话核验期间，登录操作不可提交。

## 行为与验证

| case_id | 前置与操作 | 预期及当前结果 |
| --- | --- | --- |
| QA61-01 | 匿名进入首页、根路径及业务详情 | PASS：进入 /login，未读取患儿资料 |
| QA61-02 | 读取独立登录页面 | PASS：账号、密码及登录可用，没有家庭导航或循环返回 |
| QA61-03 | 匿名查看资料使用说明 | PASS：公开说明可读取 |
| QA61-04 | 实际测试账号提交凭据 | PASS：服务端认证成功，身份及患儿范围正确 |
| QA61-05 | 登录后刷新业务页面 | PASS：会话和患儿选择恢复 |
| QA61-06 | 退出账号 | PASS：进入 /login，患儿资料清除 |
| QA61-07 | 当前业务资料请求实际返回 401 | PASS：清除身份与患儿资料，进入 /login |
| QA61-08 | 两个窗口共享登录后退出 | PASS：两个窗口均进入 /login |
| QA61-09 | 支持身份退出后登录家庭入口 | PASS：重新读取身份，支持详情拒绝家庭受众 |
| QA61-10 | 从当前页面创建静态及动态任务 | PASS：真实 worker 完成，准确任务及结果可以重读 |
| QA61-11 | 读取家庭首页与当前提示详情并刷新 | PASS：评估时点一致，保持实际资料不足状态 |

## 当前证据与限制

Chrome 155 运行 .qa-work/QA63/login-regression.json 的六项会话检查，.qa-work/QA62/warning-flow-green3.json 的六项预警流程检查，以及 QA63 的五项测试账号检查和一项普通构建检查，共十八项，页面错误为零。实际输入来自用户已授权的脱敏资料，登录与业务响应均来自本地真实服务。

类型检查及两类构建通过，准确源码摘要见 .qa-work/QA63/build-green3.json、build-production2.json。当前本地验收 index SHA256 为 61080a8a5e66bc5f6a8e7b63cb610c5e669e3622942d51510395d445a71ec758，入口为 5178，代理实际 API 8003。

.qa-work/QA63/audit.json 确认十张原始临床表摘要保持、七个进程与运行配置一致、102 项迁移保持、两笔未知 OCR 的 2 CNY 预留保持。没有新增 Ark 调用。真实病例的静态结果为 insufficient，动态结果为 insufficient／unknown，没有 AlertEvent。

本次使用 DOM、ARIA、HTTP 和数据库检查；视觉、性能及临床准确性评价均未运行。原件媒体专项维持其独立验收范围，本任务不登记该专项全部通过。真实资料、密码、会话及报告位于 Git 忽略目录。
