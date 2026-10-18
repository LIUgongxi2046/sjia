# QA26 支持端访问状态与恢复

当前状态ACCEPTED，范围为本地支持端共享访问说明与恢复操作。文档有效性CURRENT，last_verified为2026-10-19 04:17（Asia/Shanghai）。需求为FR-01、FR-09、AC-H5-05，关联T09、T54、T56、QA09与QA14。本记录为支持端共享访问提示专项唯一状态来源，完整岗位、设备及视觉验收继续由原任务维护。

实际Chrome使用获准本地家庭账号访问支持端评价集合及用途页面，接口均返回403与SUPPORT_AUDIENCE_REQUIRED。该代码表示当前会话使用家庭端受众；页面分别说明登录、会话受众与岗位权限，并提供准确恢复操作。

Given 当前实际家庭会话、匿名会话及获准支持账号，When 访问支持页面及重新核验身份，Then 页面说明与实际错误一致，登录入口、返回家庭入口及手动核验有效，未授权内容保持隐藏，输入和密码按既有规则保护，不产生岗位授权或临床写入。

验收采用真实Chrome、现有本地账号、实际401／403及获准200响应；不修改权限、不伪造响应、不读取截图。修改范围为共享SupportReadStatus及PurposeCopyPage的手动核验回调。保留原错误代码供展开查看，原始诊断位于.qa-work/QA26/reports/support-before.json。

## 检查及证据

| check_id | 实际操作及准确断言 | 结果 |
| --- | --- | --- |
| QA26-01 | 当前家庭会话依次访问专业核对、评估、发布、评价集合、用途及通知六个支持页面，实际403显示受众说明及准确入口 | 6项PASS |
| QA26-02 | 手动核验重新读取权限、返回家庭入口保持原会话、用途页面重新核验保持当前页面 | 3项PASS |
| QA26-03 | 同一账号通过支持端登录后取得support会话；缺少评估岗位时明确受限，更换账号入口有效且不保留密码 | 2项PASS |
| QA26-04 | 匿名实际401显示支持端登录要求，入口到达准确登录表单 | 1项PASS |
| QA26-05 | 现有软件支持账号通过真实登录和会话重读，获准发布页面保持正常访问 | 2项PASS |
| QA26-06 | 浏览器没有临床写入、意图准备或运行错误，检查期间准确产品源码保持 | 2项PASS |
| QA26-07 | 来源清单、临床数量、原岗位及字段、识别预留、后端身份、两套服务和当前生产产物核验 | 7项PASS |

共23项专项与受影响检查通过，其中16项实际Chrome检查记录14份支持会话及登录响应，浏览器运行错误为0。`npm run build`包含TypeScript检查与Vite生产构建，结果PASS；`git diff --check`通过。原始完整证据位于.qa-work/QA26/reports/browser-final.json与final.json，浏览器报告SHA256为9e626fdc823d713a464772be831c4ebceb7c5c7167768f6c4f54b2c7ba98c8c1。

## 当前实现及复验

| 文件 | 当前职责与SHA256 |
| --- | --- |
| guardian-h5/src/features/support/SupportApp.tsx | 共享访问说明、准确受众及登录状态、明确登录与返回链接、独立手动核验；23953f4e907560017034c00162b70294cfe360e66899a5702d0a313e40964b50 |
| guardian-h5/src/features/support/PurposeCopyPage.tsx | 权限受限时手动核验调用当前会话读取；effacc34ce24dc3c8c6b81f461ee799a49af707533ce666d6e1630587a08f606 |
| guardian-h5/scripts/check-support-read-status-dom.mjs | 使用参数指定的现有账号、实际本地服务与Chrome执行16项检查，私有报告不包含密码；839b1c9b8bb4344568ba5789a73614d72f20e625a43a88c66a7517fd1646eba5 |

在guardian-h5执行以下命令，凭据及报告继续位于忽略目录。脚本要求现有家庭账号具备manage_data_use及缺少read_assessment，现有软件支持账号具备manage_config，实际岗位不符合要求时立即报告错误；脚本不会修改任何岗位。

```sh
TMPDIR="$PWD/../.qa-work/QA11/tmp" node scripts/check-support-read-status-dom.mjs \
  --base-url http://127.0.0.1:5177 \
  --family-credentials ../.qa-work/QA11/credentials.json \
  --operator-credentials ../.qa-work/QA17/operator-credentials.json \
  --report ../.qa-work/QA26/reports/browser-final.json
```

当前生产产物为SupportApp-BKawYkny.js，SHA256为7b06a6a23ec7fc10831c091c39b3d88364df93effb1f137de6f5919a32d2772a。本专项测量的后端制品为cc805753fbcbf7db94423f453dd50780083e5e2a885758b92b6a22073152a68b，runtime为38487adc-a41d-4cb8-a339-b172ba2c6704。当前后端采用及16项共享支持入口复验由[QA27](QA27-real-clinical-access-scope.md)维护，8002和8003均为ready、admitted_current，5176和5177实际提供当前组件。

## 限定范围评审及关联

haonan-s006-review的PARTIAL_AUDIT结论为GO，范围限定为本次共享访问提示及恢复。正式401、403、SupportSessionView、岗位判定和接口路由保持；页面不根据受众拒绝推断账号没有岗位，重新登录继续交由真实接口判定。准确会话受众、当前岗位、三个原字段范围及获准页面已经实际核验。手动核验按钮明确使用type=button，登录与返回使用本人操作的准确链接。

83项事实、249项事实版本及两份观察范围保持，当前2268项整理历史及252项有效结果由QA27核验。两项uncertain识别及2 CNY预留保持，没有新增模型调用、临床计算或专业授权。设备、微信、视觉及完整业务状态继续按原任务验收。本次软件决定QA26-D01依据用户明确委托的开发及软件决定授权，支持端接口的受众与岗位判定保持。

T54、T56和QA14引用本专项23项准确证据；QA25来源清单与QA27当前运行状态保持。完整岗位、临床及设备验收没有由本专项替代。
