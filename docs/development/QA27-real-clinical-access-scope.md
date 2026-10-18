# QA27 真实临床入口权限范围

当前状态ACCEPTED，范围为本地真实核对及评估入口的未授权读取拒绝。文档有效性CURRENT，last_verified为2026-10-19 04:35（Asia/Shanghai）。需求为FR-09、FR-10、AC-10，关联T49、T50、T56、QA12和QA14。本记录为本专项唯一状态来源，完整临床正向业务继续由原任务维护。

当前运行制品、资料与整理状态由[QA29](QA29-real-original-range-reads.md)维护；本记录保留04:35核对范围专项的准确检查范围及证据。

实际当前账号拥有软件测试所需的既有全局岗位，未取得当前病例的verify_field字段授权。当前专业目录返回空范围，核对范围明确返回403 VERIFICATION_FIELD_SCOPE_DENIED；静态及动态评估入口返回403 SERVICE_ACTION_DENIED。准确字段、版本、来源及资格核验继续执行。

Given 当前真实记录及既有账号，没有对应病例核对字段授权，When 读取当前核对范围及评估创建入口，Then 返回准确权限拒绝、不返回原始字段内容，不创建核对证明、评估或模型任务；获准本人读取、原件、历史及整理保持。

本专项没有新增岗位、病例核对权限、临床答案或专业证明。修改限于核对范围读取的空授权检查，数据库、正式写入及评估计算规则保持。

## 检查及证据

| check_id | 准确输入、操作及断言 | 结果 | 私有证据 |
| --- | --- | --- | --- |
| QA27-01 | 采用前249项完整事实、临床数量、原权限、任务预留及313份配置Schema核验 | 5项PASS | pre-adoption.json |
| QA27-02 | 实际目录为空，当前及两份历史记录核对范围403，静态及动态current／replay均拒绝无执行岗位 | 4项PASS、9次接口响应 | after.json |
| QA27-03 | 六个支持页面、家庭受众、匿名、支持登录、岗位不足、重新核验、返回和更换账号 | 16项Chrome检查PASS | support-browser.json |
| QA27-04 | 准确记录参数不扩大空病例目录，字段与人员读取及创建核对操作禁用，实际刷新保持，无业务写入 | 4项Chrome检查PASS、2次目录响应 | verification-browser.json |
| QA27-05 | 原整理失效且252项历史保持，当前代码生成的252项有效整理逐项相同 | 2项PASS | delta-refresh.json |
| QA27-06 | 源码、准确资格、全部事实、文书、观察范围、文件关系、原授权、OCR、整理和两套服务核验 | 8项PASS | final.json |

共39项专项及受影响检查通过，Chrome运行错误为0，Django系统检查和git diff --check通过。前端源码继续保持QA26类型检查与生产构建通过的准确版本，本次20项实际Chrome检查使用当前后端服务。全部原始映射、接口正文及报告保存在.qa-work/QA27/reports，真实正文与私有证据不进入Git。

## 当前实现与采用

verification_creation_scope继续从实际记录读取字段并调用permitted_fields。记录包含字段且没有任何获准字段时立即抛出VerificationRejected，reason_code为VERIFICATION_FIELD_SCOPE_DENIED。取得获准字段后继续原有version_scope、准确字段、用途、来源、保留策略及人员范围检查。接口沿用既有DENIED错误分类返回403，客户端沿用403权限处理；正式Schema与写入流程保持。

backend/verification/workflow.py的准确SHA256为7a7db46f6e94bb625c2fad6497e3892fb35056d993aff94d23f268e7dfbe14c1。当前后端制品为4834cf5b390e0c3741e7153d84de859849c6d18a5de71bc22bbb6755e8f4eefc，runtime为38487adc-a41d-4cb8-a339-b172ba2c6704。313份真实配置中290份图像处理配置继续采用准确图像制品b8c9f9233fb4ec77cb520bde843a933f34f5c0baf1e8935ba5ab80f32bc38ea5，23份后端配置采用当前后端制品；26份原验收配置采用当前后端制品。所有配置正文、批准用途、audience与联系期限保持。

两套后端经过当前软件资格核验并完成进程采用，8002与8003均为ready、admitted_current；5176与5177入口可访问。进程归属由实际命令及工作目录核验，重启前没有在途任务；没有新增OCR消费或外部请求。

软件决定QA27-D01依据用户委托的开发与软件决定授权，准确区分病例字段权限拒绝与输入版本检查。配置资格的采用前证据明确记录其真实资料及Schema范围，采用后的权限行为由实际接口和Chrome另行验证；没有形成临床批准。

## 数据与后续条件

当前RecordVersion、RecordField、ArchiveFact、ArchiveFactVersion分别为3、249、83、249，完整事实投影SHA256继续为c842805778a23c4228d1d7bbd98cdf7249979a94742dcbe69f0f141f53cf98e6。83项事实没有成为专业已核对状态，Verification、VerificationRequest、Assessment、AlertEvaluation及AlertEvent均为0。两份观察范围、另一来源1208字符文书及唯一版本、文件关系两个版本且当前released、63份来源清单均保持。

当前九份整理共保留2268项历史，八份失效、一份有效；有效结果ef91f7da-d904-48f1-a05f-7e81d6d2e4d4仍为252项，与QA24逐项相同。两项uncertain OCR及2 CNY、两个并发位置保持，实际用量与费用没有新回执，本次模型调用为0。

## 限定范围评审

haonan-s006-review的PARTIAL_AUDIT结论为GO，限于本次未授权核对范围的准确拒绝。前后端继续使用既有错误正文、403权限状态和SupportSessionView；字段筛选顺序、准确RecordVersion身份、病例与作者范围保持。当前记录和两份历史的实际拒绝、空目录、禁用提交及刷新已核验，没有返回原始字段或创建请求。共享来源、配置身份、依赖失效及当前整理完成受影响验证。

T49、T50、T56、QA14及后端设计引用当前拒绝规范与真实证据，QA19—QA26继续保留各自原专项范围。完整临床资格、静态及动态正向计算、独立答案、专业批准、实际设备、微信及视觉继续保留原要求，V49和V50保持NOT_RUN。
