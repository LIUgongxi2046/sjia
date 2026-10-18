# QA23 真实相同文件关系

当前状态为ACCEPTED，限于真实相同字节原件的本人软件整理、确认、解除和请求恢复专项；需求为FR-16、GRAC-04、FD-64，关联T44、QA11、QA12与QA14。文档有效性为CURRENT，last_verified为2026-10-19 02:51（Asia/Shanghai）。本记录只核验现有获准原件在软件测试中形成的相同字节副本，不判定同次临床采样、独立检查或专业核对。完整V44继续由planning/delivery-ledger.json维护。

检索依据为T44、PRD对应重复资料要求、implementation-tasks.md、archives.relations、archives.relation_queries、requests.archive_relations、archive-relations.schema.json和UI2DuplicatesPage。现有用户授权包括五个来源分组的本地读取与保存，以及常规软件决定。真实资料与请求证据继续保存在忽略目录.qa-work/QA23。

Given 两个既有获准原件对象的完整实际字节相同，When 通过当前来源目录确认相同文件关系、刷新、查询原请求及解除，Then 准确来源、保存回执、历史和版本保持，原件字节及临床事实不变；字节不同的文件不能确认相同文件关系。待确认关系不自动产生临床采样结论。

## 当前实现与决定

初次准备明确返回422 ARCHIVE_RELATION_BYTES_MISMATCH时，服务未建立意图。页面显示“文件内容不同”，保留两个准确来源、关系依据和原因，清除该次未成立的请求身份并允许继续编辑；重新选择来源后必须重新明确关系类别。已建立、已提交及受理未知请求继续使用原身份，不生成替代提交。仅该准确错误代码与初次准备条件触发恢复规则，服务的文件一致性和授权规则保持。

| 位置 | 当前职责 |
| --- | --- |
| guardian-h5/src/features/archives/UI2DuplicatesPage.tsx | 明确拒绝的中文说明及输入恢复，准确拒绝身份与恢复读取相互隔离 |
| backend/archives/relations.py；relation_queries.py | 实际来源、完整字节、当前关系、确认与解除；本次没有修改后端 |
| backend/requests/archive_relations.py | 现有原正文、准确意图、幂等回执与版本条件 |
| .qa-work/QA23/reports/final.json | 唯一专项结果来源及全部证据、源码校验值 |

测试使用两个既有原件对象，每份605388字节，完整字节相同，SHA256为5c6385a4b73922be4398ef5a099ec782466c219335a14c5fc9ff44a19f5b56fe。实际页面保存本人确认版本1，随后明确解除并保存版本2。当前关系为released，原版本及原件继续可读；verification_id及两端临床事实引用均为空。实际依据和原因明确软件测试范围。

测量采用基准commit 8ca48b9abb6395284a2c2ee360fbc6545d7ae763及当时工作区内容。UI2DuplicatesPage.tsx的SHA256为faa503c3c2e9507cc426a27730605f4d4853acf7a2fdec6472b63b27e3fe52fc；后端制品SHA256保持15a7a08f54e5565a9163e0fc36147762a01b43747d34e9eeac1d62e227ef7e86，runtime保持38487adc-a41d-4cb8-a339-b172ba2c6704。现有313份真实环境配置和26份原验收环境配置继续适用于该后端。批准范围继承用户明确委托的软件决定，没有增加临床权限。

## 实际验证

| check_id | 行为与断言 | 结果 | 私有证据 |
| --- | --- | --- | --- |
| QA23-01 | 内容不同的真实文件明确拒绝，中文提示、来源和原文保持可编辑，重新选择后必须明确类别，没有关系提交或无效请求查询 | 2项PASS | mismatch-browser.json |
| QA23-02 | Chrome确认相同文件、准确重新读取、原请求、重复提交、刷新及页面运行状态 | 6项PASS | create-browser.json |
| QA23-03 | Chrome解除关系、准确版本2、原回执、重复提交、刷新及版本1历史来源 | 7项PASS | release-browser.json |
| QA23-04 | 解除后恢复原创建请求仍保留版本1正文及回执；实际断网时保留原身份和选择，没有替代提交 | 4项PASS | transport-guards.json |
| QA23-05 | 资料序号变化后旧整理失效，历史保持；重新生成252项有效整理，全部来源、原值和未知时间保持 | 2项PASS | delta-refresh.json |
| QA23-06 | 关系版本、真实字节、原意图、审计、依赖、临床数量、另一来源文书、OCR额度、旧版本拒绝、原正文冲突、跨分组拒绝及解除后原回执 | 11项PASS | final.json |

共32项专项及受影响检查通过。实际命令为项目内TMPDIR下运行Node与现有Playwright的check_mismatch_browser.mjs、check_relation_browser.mjs create、check_relation_browser.mjs release、check_transport_guards.mjs，以及backend/.venv/bin/python运行的audit_refusal.py、refresh_delta.py和audit_final.py；脚本均位于.qa-work/QA23。TypeScript和生产构建通过，构建输出为frontend-build.log。Chrome页面运行错误为0，没有截图或视觉检查。

旧版本更正准备返回409 VERSION_CONFLICT；改变已提交原请求正文返回409 IDEMPOTENCY_CONFLICT；在另一来源分组目标下提交原分组文件返回通用404 RESOURCE_NOT_FOUND。这些检查没有建立新意图。解除后再次提交版本1的原创建请求，仍取得原回执，当前关系继续released，没有第三份关系版本。

两次本人关系保存提升资料序号，旧关系依赖失效。当前代码与权限下九个实际整理结果保留2268项历史内容，其中八个失效、一个有效；有效结果252项与QA21的内容逐项一致，准确状态由[QA27](QA27-real-clinical-access-scope.md)维护。临床RecordVersion、RecordField、ArchiveFact和ArchiveFactVersion分别保持3、249、83和249，ObservationPeriodVersion保持2，另一来源的1208字符文书及其唯一版本保持。Assessment、AlertEvaluation和AlertEvent均为0。OCR仍为3项，其中2项uncertain并保留2 CNY预留；没有新增模型调用。两个服务均为ready、admitted_current。

## 关联影响与继续条件

T44、T46、QA14及QA19—QA22引用当前关系和整理状态，原专项证据继续保持其测量范围。当前读取实现、准确制品、实际权限与单次目录测量由 [QA24](QA24-real-relation-read-performance.md) 维护，正式P95及容量继续按T57核验。

完整V44需要真实病史引用、独立采样、报告更正、准确专业核对及连续次数计算。当前文件字节一致性不提供这些临床答案。实际手机、微信、后台恢复及视觉验收继续为NOT_RUN；本次断网证据只覆盖未取得准备响应时的原身份保留，不表示联网后受理结果已经取得。
