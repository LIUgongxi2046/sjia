# QA22 真实PDF原生文字与手工文书

当前状态为ACCEPTED，限于真实PDF第1页手工保存、目录、原请求及明确拒绝恢复的软件专项；完整V43继续由planning/delivery-ledger.json维护。需求为FR-13、FR-15、GRAC-03，关联T40、T43、QA11、QA12与QA14。last_verified为2026-10-19 02:33（Asia/Shanghai），文档有效性为CURRENT。

使用已获准63份原件中的一份51页PDF及其既有手工整理批次。结构检查确认第1页具有正文文字层，其他50页包含图片且文字层不足以证明正文覆盖。软件保存检查仅选择第1页，并保持其他50页未完成识别的状态；原分类、医院、形成时间与就诊在没有核验依据时保持未知，不生成专业核对或临床事实。

检索依据为T43、UI2DocumentCreatePage、requests.archive_documents、archives.grouping、v2-protocol.schema.json及已有原件接收记录。用户已明确批准这63份原件的软件测试，源文件只读；实际正文与私有证据保存在忽略目录.qa-work/QA22。该来源分组不与其他分组建立临床身份关联。

Given 真实51页原件、准确第1页原生文字及已获准手工批次，When 选择该原页、明确填写原文、提交、刷新并查询原请求，Then 该页文字、准确页序、原件版本、回执与未知元数据保持，没有OCR调用或结构化临床事实。其他50页继续保留原件及未识别状态，其他来源分组的既有事实和有效整理结果继续保持独立。

初次准备收到明确的DOCUMENT_COPY_CONFIRMATION_REQUIRED时，服务尚未建立新意图。页面保留原文和原页选择，清除该次尚未成立的请求身份，显示具体中文冲突说明并恢复编辑。已建立的意图、已提交请求和受理状态未知的情况继续使用原身份核验，不自动创建替代请求。该规则使用现有接口语义，不更改授权或保存规则。

## 当前实现与依据

| 文件或接口 | 本次相关行为 |
| --- | --- |
| guardian-h5/src/features/archives/UI2DocumentCreatePage.tsx | 准确识别初次准备的重复拒绝，保留输入，恢复编辑并清除该次未成立的身份；恢复读取跳过同一已拒绝身份 |
| backend/requests/archive_documents.py；backend/archives/grouping.py | 使用现有实际意图、保存、来源、重复资料和回执规则，本次没有修改后端规则 |
| POST /api/v2/patients/{patient_id}/archives/search | 按准确来源分组、当前文书、关键词和未知形成时间检索已保存正文 |
| .qa-work/QA22/reports/final.json | 唯一专项结果来源；关联六份实际业务检查、输入及源码校验值 |

软件决定继承用户委托的常规开发批准；原文读取和本地保存继承已有63份原件及五个资料分组的明确授权。正文使用pypdf提取，并与PyMuPDF第1页文字去除空白后的内容比较，结果一致。第1页pypdf文字为1208字符，没有图片对象；其他50页均含图片。51页均有非空文字层这一计数不能证明51页正文已经提取。只读源文件为28542044字节，SHA256为cd87ee6d3eecb78fb21cb2d07fa030603865a30fd73f080500981f342c9ede51，保存后完整校验保持一致。

本次源码以基准commit 8ca48b9abb6395284a2c2ee360fbc6545d7ae763及测量时工作区内容标识。前端相关文件SHA256为21de4a1b95ef180d4abf91fce060694bc6ac11b552081b5bbd19df5c83e15f37；后端运行制品SHA256为15a7a08f54e5565a9163e0fc36147762a01b43747d34e9eeac1d62e227ef7e86，runtime为38487adc-a41d-4cb8-a339-b172ba2c6704。本次前端变更未改变后端制品、配置或Schema。真实环境313份配置及原验收环境26份配置继续使用QA21核实的当前后端。

## 验证与证据

| check_id | 实际操作及断言 | 检查数量与结果 | 私有证据 |
| --- | --- | --- | --- |
| QA22-01 | Chrome选择实际第1页、填写准确原生文字、提交、刷新、读取原请求及原件入口；相同请求保持原回执，其他来源整理结果保持 | 6项PASS | browser.json |
| QA22-02 | 重新读取数据库中的准确1208字符、原页、原件SHA、未知元数据、pending状态、依赖和审计；仅一份文书版本、一个写入意图与一个写入请求 | 5项PASS | persistence.json |
| QA22-03 | 实际原文关键词命中、未知形成时间的包含与排除、准确文书范围及其他来源分组隔离 | 3项PASS | directory.json |
| QA22-04 | 对同一实际原页重新准备，服务返回409 DOCUMENT_COPY_CONFIRMATION_REQUIRED，文书、意图与请求数量保持 | 1项PASS | duplicate.json |
| QA22-05 | Chrome实际触发明确重复拒绝，显示中文说明，原文与选择保留并允许继续编辑；没有文书提交或不存在请求的查询，页面运行错误为0 | 2项PASS | duplicate-browser.json |
| QA22-06 | 恢复已提交原请求并刷新；独立Chrome上下文实际断网后准备受理状态未知，保留原身份及正文，没有替代提交或自动查询 | 4项PASS | preparation-guards.json |

实际执行脚本为.qa-work/QA22下的check_document_browser.mjs、audit_saved_document.py、check_directory.py、check_duplicate.py、check_duplicate_browser.mjs、check_preparation_guards.mjs与audit_final.py。Node脚本由当前Node及guardian-h5的Playwright运行，Python脚本由backend/.venv/bin/python运行，原生文字提取使用已安装的文档Python运行环境。TMPDIR指向项目内.qa-work/QA11/tmp。最终21项业务检查通过，证据位于.qa-work/QA22/reports；TypeScript及npm run build通过，构建输出为.qa-work/QA22/frontend-build.log。

最终复验确认临床RecordVersion为3、RecordField为249、ArchiveFact为83、ArchiveFactVersion为249，ObservationPeriodVersion为2，Assessment、AlertEvaluation与AlertEvent均为0。全部83项事实继续未专业核对，本专项测量时有效新增内容整理的准确接口正文保持。没有新增结构化临床事实或模型调用。两个服务均为ready、admitted_current；两项uncertain OCR及2 CNY预留保持。当前代码、权限及整理状态由[QA27](QA27-real-clinical-access-scope.md)维护；本次第1页文书及唯一版本再次核验保持。

## 关联影响与未完成范围

T43、QA12和QA14引用本专项；开发索引登记唯一主记录。新文书只保存第1页本人确认原文，没有生成就诊、医学时间或专业证明。原页选择、原回执及请求不确定性规则继续用于后续文书操作。

其他50页正文识别、独立原文答案及临床准确性保持未完成；其余原件的完整识别继续由QA12维护。实际设备后台、微信浏览器、手机键盘与视觉比较为NOT_RUN。该专项没有执行受理未知请求恢复联网后取得最终受理结果的检查；本次证据只证明断网期间原身份与正文保持。完整V43、GRAC-03及专业临床验收继续保留。
