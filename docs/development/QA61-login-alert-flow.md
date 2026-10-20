# QA61 独立登录与预警流程验收

状态为 IN_PROGRESS，文档有效性 CURRENT，日期为 2026-10-20。当前范围依据用户要求，仅处理独立登录、会话恢复，以及用户选择的“预警计算 → 提示展示”。关联 FR-01、FR-06、FR-09、AC-H5-05、T08、T09、T50、T51、QA27、QA59。完整临床评价仍由原任务维护。

## 当前依据

基于 main 已发布的 1ad8c9f8626b6880179c18295b033308441e1827，使用 codex/current-design-reception。已读取 App、LoginPage、UI2FamilyFrame、UI2Frame、AccessProvider、SessionBoundary、client、useFamilyResource、UI2ReadState、UI2AccountPage、assessmentText、UI2AlertsPage，以及相关任务记录。项目已有登录表单与会话版本检查，家庭业务路由需要统一认证检查。

## 行为与验证范围

| case_id | Given | When | Then |
| --- | --- | --- | --- |
| QA61-01 | 匿名会话 | 进入首页、根路径或业务详情 | 进入 /login，业务页面不显示，未发起患儿资料读取 |
| QA61-02 | 独立登录页面 | 读取导航与表单 | 展示账号、密码和登录操作，没有家庭导航或返回账号页面的循环 |
| QA61-03 | 匿名会话 | 查看资料使用说明 | 公开说明可以阅读 |
| QA61-04 | 现有真实测试账号 | 提交实际登录凭据 | 服务端登录成功，首页与实际身份相符 |
| QA61-05 | 有效登录 | 刷新业务页面 | 服务端会话恢复，当前患儿与权限相符 |
| QA61-06 | 有效登录 | 退出账号 | 进入 /login，当前患儿资料清除 |
| QA61-07 | 服务端会话已经失效 | 页面发起资料请求并返回 401 | 清除当前身份与患儿资料，进入 /login |
| QA61-08 | 两个窗口共享登录 | 另一个窗口退出 | 当前窗口清除患儿资料并进入 /login |
| QA61-09 | 家庭及支持入口 | 切换受众与重新登录 | 当前权限重新读取，前一个范围的资料清除 |
| QA61-10 | 当前真实病例、规则与资格 | 发起现有预警计算 | 依据实际资格执行，结果与任务持久保存并读取；资格不足时保留准确拒绝 |
| QA61-11 | 当前预警结果与发布配置 | 读取首页、提示列表及详情 | 页面与服务端当前状态一致，未启用或不足状态明确呈现 |

使用现有真实账号和已授权病例。实际模型、专业核对、发布资格、家庭提示与工程状态分别记录。不得补造专业证明、临床答案、服务响应或费用回执。浏览器验证采用 DOM、ARIA、HTTP 与实际会话，不执行视觉检查。私有输入与报告保存于 .qa-work/QA61，不进入 Git。

## 实施与证据

家庭业务路由采用统一会话检查，公开保留 /login 和 /account/data-use。匿名登录页面没有返回账号页面的操作。资料请求返回当前会话的 401 时，立即清除身份、患儿范围和私有页面状态。旧页面的参数清理仅在浏览器地址仍匹配时执行。退出成功后进入 /login。现有会话版本、受众检查与授权期限继续执行。

修复前实际 Chrome 读取独立构建，匿名 /home 未进入 /login；准确 index SHA256 为 2102e95cf3cf28b415fd82095b0b54d1ec99acaeb2a9be58782a2e4ffff9ec0f，证据为 .qa-work/QA61/login-red.json。生产构建与类型检查由 build-green.json 保存，后续验证继续核查当前源码摘要。

现有 5176／5177、8002／8003 与数据库保持各自运行范围，验证使用独立前端入口。QA44 的运行切换批准范围继续遵守原记录。

## 当前验证

独立登录及会话处理范围已通过：真实 Chrome 六项登录检查与七项共享会话检查，共十三项，运行错误为零。匿名五个入口没有发起患儿读取；实际账号登录、刷新、两个窗口退出、当前资料请求 401、患儿切换、支持受众切换、退出请求发出前关闭窗口及实际会话恢复均已验证。原件文书的关闭、后台及跨窗口退出后，Blob URL 与服务端访问分别核验。原件专项完成四项后触发 2 GiB 空间检查，其余原件专项继续待验收，不计入本专项十三项通过结果。

五个病例首页与详情的 configuration_pending 状态完成真实 Chrome 检查。当前岗位、提示权限及目标配置状态由 [QA62](QA62-literature-alert-runtime.md) 维护，QA61 的 alert-display.json 不用于当前岗位范围的通过结论。实际计算与家庭提示采用继续由 QA62 执行。

TypeScript、生产构建、四份专项脚本语法及 git diff --check 通过。实际路由归属为家庭 39 条、支持 13 条，Rolldown AST 检查通过。构建核验 227 份源文件，index SHA256 为 f0f92724f90584ff9773b161dcc27f2d033c3ad2df15a721beeece4732bbcf08。

准确证据为 .qa-work/QA61/build-green2.json、login-green2.json、shared-session-observed.json、alert-display.json、route-ownership.json 和 audit.json。原件专项为 .qa-work/QA60/qa61-session-images.json，状态为部分执行并由资源检查中断。共享会话报告保留实际 API 耗时；本记录不登记性能验收通过。审计确认十张临床表、四十七条原权限、原任务数量、102 项迁移及两笔未知 OCR 的 2 CNY 预留保持。

## 真实预警条件

.qa-work/QA62/staged-audit.json 从当前数据库与实际服务核验，83 项 ArchiveFact 与十张临床表的摘要保持；Verification、Assessment、AlertEvaluation、AlertEvent 和评估任务数量均为零，启用的 ReleaseBundle 数量为零。QA62 的 execution-scope-green.json 核验当前账号能够从真实接口选择一个获准病例；对应执行与提示权限已经登记。

实际预警任务与家庭结果展示保持 NOT_RUN。依据用户最新授权，采用 [2016 原始研究与 2024 指南核查记录](QA61-guideline-review.md) 开展 AI 规则判断，保留病例字段的实际核对状态。十九份目标配置已经登记为 approved，尚未启用；采用操作等待明确批准。没有增加专业证明、启用发布记录、模型调用或临床原值更新。
