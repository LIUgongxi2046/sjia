# QA09 登录与访问状态恢复

## 任务身份

本记录为 QA09 的任务状态来源，当前状态为 ACCEPTED，范围为登录、关联与访问状态的页面恢复。来源为用户在 2026-10-18 要求优化已确认问题。代码基线为 `8ca48b9abb6395284a2c2ee360fbc6545d7ae763` 与工作区保留修改。关联 FR-01、FR-09、AC-01、T07、T54、QA07。文档有效性为 CURRENT，last_verified 为 2026-10-18。

## 检索与依据

读取开发索引、QA07、QA08 审查报告、当前 PRD 第 8.1 节、UI2ReadState、AccessProvider、useFamilyResource、LoginPage、ReportsPage 与当前路由。采用实际会话、关联及原始检查结果确定恢复入口的验收动作。

## 当前要求

未登录时清楚说明登录要求并提供登录入口；有效账号没有可访问患儿时说明关联状态；读取被拒绝时说明权限不足。重新检查访问状态必须读取真实会话与关联，不能只重复读取没有权限的业务资源。登录完成后保留正确的访问范围。共享组件的调用页面纳入回归。

## 验收

| ID | Given | When | Then | 状态 |
| --- | --- | --- | --- | --- |
| QA09-01 | 没有有效会话 | 打开受保护页面 | 显示明确登录提示与登录入口 | PASS：匿名会话 401，检查按钮实际请求 session |
| QA09-02 | 会话可重新读取 | 操作访问恢复入口 | 请求真实 session 与 relations，按有效状态恢复内容 | PASS：登录恢复及失效后的资料隐藏通过 |
| QA09-03 | 有效账号没有可访问患儿 | 打开需要患儿的页面 | 说明尚无可访问患儿，提供关联管理入口 | PASS：实际账号关联列表为空，重新读取 200，管理入口进入 associations |
| QA09-04 | 有效账号与关联 | 从登录入口完成登录 | 页面显示获准资料，刷新后归属一致 | PASS：会话 200，8 个登录后页面读取通过 |
| QA09-05 | 共享组件调用页面 | 执行页面读取及类型构建检查 | 登录、关联、权限与普通读取状态互不混淆 | PASS：22 个匿名页面均只有一个登录提示，类型检查与生产构建通过 |

## 实现与证据

`guardian-h5/src/components/UI2ReadState.tsx` 分别显示登录、患儿关联、资料处置和权限不足状态。恢复入口调用 AccessProvider.refresh，取得有效访问范围后才重新读取业务资料。`src/features/home/UI2HomePage.tsx` 的指标说明与管理入口使用对应状态文字。`src/features/registration/ReportsPage.tsx` 的确认页面使用同一份读取状态。

可重复检查入口为 `guardian-h5/scripts/check-access-recovery.mjs`。实际命令：`TMPDIR="$PWD/.qa-work/QA09" SJIA_BASE_URL=http://127.0.0.1:5176 SJIA_TEST_CREDENTIALS="$PWD/.qa-work/QA06/credentials.json" SJIA_TEST_UNASSOCIATED_CREDENTIALS="$PWD/.qa-work/QA09/unassociated-account.json" SJIA_TEST_OUTPUT="$PWD/.qa-work/QA09/access-recovery-final.json" node --test guardian-h5/scripts/check-access-recovery.mjs`。

最终运行完成 34 项业务断言，包括 22 个匿名页面、8 个登录后页面、实际会话与无关联账号检查，浏览器运行错误数量为 0。原始结果为 `.qa-work/QA09/access-recovery-final.json`；无关联账号专项结果为 `unassociated-result.json`。`npm run build` 包含 TypeScript 检查，当前构建通过。页面检查只使用 DOM 和真实接口。

## 约束与关联影响

服务端授权、账号与患儿范围继续使用 AccessProvider 和现有接口。测试使用真实服务及已有账号，不创建替代响应。UI2ReadState 被家庭页面共享，回归同时检查记录、摘要、档案、草稿、提醒与预警。真实资料测试由 QA10 管理。

本次没有改变服务端授权、临床资格或运行配置。完整临床业务、模型准确率和目标家庭使用评价继续采用各自验收状态。
