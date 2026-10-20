# QA63 本地登录凭据与患儿资料验收

状态为 ACCEPTED，文档有效性 CURRENT，last_verified 为 2026-10-21。本记录为本任务唯一状态来源。需求来自用户“测试账号密码放在登录页，测试患儿数据也注入对应的测试账号”，关联 FR-01、FR-09、QA61、QA62。

## 当前依据与实现范围

已检索开发索引、QA61、QA62、LoginPage、AccessProvider、patient_selection、家庭记录接口及实际构建脚本。登录入口支持明确配置的 deidentified 与 simulation 资料模式，分别绑定其实际服务、账号及患儿授权。当前模拟资料与家庭功能验收由 QA65 维护。

登录页仅在 local-validation 构建模式显示本地账号、密码和填写操作。凭据从被 Git 忽略的本地文件读取，通过构建进程环境提供；源码与文档不保存凭据值。普通 production 构建不包含测试凭据。登录仍由真实服务认证，关系及用途权限仍由服务端执行。

脱敏资料模式的验收账号具有五组有效关联，其中一组保存一份当前版本记录和 83 项字段。模拟模式的当前账号关联“林知夏”和“沈星辰”，默认选择“林知夏”。登录后由服务端保存选择并重新核验会话。relation-read Schema 接受实际返回的 execute_assessment、read_assessment 标识；这些标识不授予新的访问权限。

| 文件 | identifier 或接口 | 当前职责 |
| --- | --- | --- |
| guardian-h5/vite.config.ts；src/localTestAccount.ts | __SJIA_LOCAL_TEST_ACCOUNT__ | 按构建模式注入账号、密码、资料模式及已授权患儿标识，缺少配置时终止构建 |
| guardian-h5/src/features/account/LoginPage.tsx | localTestAccount；submit | 显示凭据、填写表单，传递准确默认患儿 |
| guardian-h5/src/state/access.tsx；src/api/endpoints.ts | authenticate；savePatientSelection | 实际认证后保存患儿选择并重读会话，保留版本及并发检查 |
| backend/relations/read.schema.json | Action | 核验真实关系动作范围 |
| guardian-h5/src/features/support/SupportApp.tsx | checkingSession | 初始会话核验期间禁止提交支持登录 |
| guardian-h5/scripts/check-local-test-account.mjs | 实际 Chrome 验收 | 验证凭据、账号、默认患儿、字段、来源、刷新与退出 |

## 脱敏资料模式验收矩阵

| check_id | 前置与动作 | 预期及证据类型 | 状态 |
| --- | --- | --- | --- |
| QA63-01 | 匿名访问本地登录页 | DOM 展示准确账号与密码，填写按钮更新表单；BUSINESS | PASS：account-green3.json |
| QA63-02 | 使用展示的凭据登录 | 实际家庭身份正确，默认患儿及五组关联可读取；API／BUSINESS | PASS：account-green3.json |
| QA63-03 | 进入真实患儿记录并刷新 | 实际展示 83 项字段，当前版本及来源与服务端一致；BUSINESS | PASS：account-green3.json |
| QA63-04 | 切换注册与退出 | 注册状态不展示凭据，退出返回登录页；BUSINESS | PASS：account-green3.json |
| QA63-05 | 普通构建 | 构建不包含本地凭据，页面无测试账号；STATIC／BUSINESS | PASS：build-production2.json；production-green.json |
| QA63-06 | 变更后的构建运行预警流程 | 实际计算、持久保存和家庭提示继续通过；INTEGRATION | PASS：QA62/warning-flow-green3.json |

验证使用真实 Chrome 的 DOM、ARIA、HTTP；视觉检查 NOT_RUN。私有报告保存 .qa-work/QA63，真实原件和凭据继续排除于 Git。当前评估输入不足时保留不足状态，临床准确性评价 NOT_RUN。

## 关联与继续条件

本地构建使用 VITE_LOCAL_TEST_USERNAME、VITE_LOCAL_TEST_PASSWORD、VITE_LOCAL_TEST_PATIENT_ID、VITE_LOCAL_TEST_DATA_MODE 四项环境变量及 npm run build -- --mode local-validation。资料模式必须明确填写 deidentified 或 simulation。实际构建调用分别由 .qa-work/QA63/build.py 与 .qa-work/QA65/build.py 读取已授权本地文件。当前本地与普通构建均通过 TypeScript 检查；普通构建的凭据值检查结果均为 false。

五项真实账号检查、一项普通构建检查、六项登录回归及六项预警流程检查全部通过，共十八项，页面错误为零。准确命令及输入路径保存在 .qa-work/QA63/remaining_checks.py 和 run_check.py，结果位于各自原始 JSON。audit.json 确认十张原始临床表、七个服务进程、102 项迁移及未知 OCR 预留保持。

本地入口为 5178/login，当前采用 .qa-work/QA65/dist-demoFinal，index SHA256 为 9c1c1e1c8cbac63cf93b583bdf083dc47613baa6db2a23d1b8d08320314f5462。模拟资料范围、逐项功能与实际运行状态以 QA65 为准。临床准确性、完整性能和视觉检查继续保持 NOT_RUN。
