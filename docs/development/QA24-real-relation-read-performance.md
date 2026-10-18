# QA24 真实重复资料目录读取

当前状态为ACCEPTED，限于本地真实重复资料目录读取的软件专项。本记录为该专项唯一状态来源，文档有效性CURRENT，last_verified为2026-10-19 03:40（Asia/Shanghai）。需求为FR-16、NFR-15，关联T44、T57、QA18、QA23与QA14；完整V44与V57继续由planning/delivery-ledger.json维护。检索依据为既有任务、PRD对应要求、relation_queries、relations、fact_read、authorization.policy、正式接口Schema与真实保存状态。

Given 当前获准真实原件、83项事实和249项事实版本，When 读取重复资料目录及继续读取，Then 来源、历史、候选、当前关系、权限和分页条件保持；仅减少重复查询，不改变候选及临床采样规则。实际SQL数量、响应内容和耗时分别测量，性能观测不代替临床准确性或完整容量验收。

诊断使用独立本地端口、相同真实数据库和当前后端代码，通过实际HTTP、现有账号、Django查询记录及cProfile取得结果。私有结果位于忽略目录.qa-work/QA24。本次没有模型调用或患者事实变更，权限验证使用正式撤销、实际期限和同范围恢复；没有扩大原有三个档案字段范围。

## 当前测量与依据

2026-10-19 03:03（Asia/Shanghai），原服务单次读取为11.121秒。独立服务经过相同运行准入，状态为ready、admitted_current；诊断请求实际执行7486条SQL，查询记录累计3.670秒，含cProfile的完整读取为19.337秒。诊断开销与原服务耗时分别记录。首份响应包含100项来源并提供继续读取标识；忽略会话绑定的分页标识具体值后，两份响应的规范化SHA256均为beb7dc3fad984a1fe7aea28ddeb0f0bc030545302a7a59b1e6d56f068e063db3。

修改前全部249个版本在实际授权下完成来源投影保存，基准SHA256为ebc7f5159ad4c1febf97b60d3087bc1a86d07352ae5096802f0495899601eb22，对应data_epoch为16。批量读取后的249项来源及完整事实投影逐项一致；资料加载与来源投影共13条SQL，完整目录请求为2718条SQL，满足本专项少于3000条的条件。原服务未附加诊断的单次读取为3.350秒，响应规范化SHA256保持一致。before-profile.json、after-profile.json、baseline-http.json、current-http.json与fact-reference.json分别保留准确观测。

authorize_case、source_scope和matching_case_grants的实际调用次数分别保持266、265和1066。每项原有授权及当前期限继续执行，资料复用没有缓存权限判定。独立诊断服务已退出，8004端口没有监听；8002与8003均采用当前代码，状态为ready、admitted_current，5176与5177入口均可实际访问。

本专项的软件改进条件为：相同真实输入每次目录读取少于3000条SQL，全部249项事实来源、准确历史、候选、关系及分页语义保持，每项原有授权及实际到期条件继续核验。批量读取资料仅在当前请求内使用；写入操作继续采用原有来源检查和锁。常规软件决定依据用户已明确委托的开发授权，正式P95、并发容量及临床验收继续保留原要求。

## 当前实现与身份

| 位置 | 当前职责 |
| --- | --- |
| backend/archives/relation_context.py；ArchiveRelationReadContext | 同一请求内批量加载全部事实版本、字段、记录、原始作者、锚点、原件、原页、文书及核对来源；缺少资料立即报告错误 |
| backend/archives/queries.py；checked_fact_source、fact_read | 保留来源一致性、准确页关系、文件版本与核对证明检查；明确读取上下文与原有默认读取分别采用相同投影规则 |
| backend/archives/relation_queries.py；relation_directory、source_granted | 保留事实及版本顺序、当前关系、候选、签名分页和逐项权限；采用请求内资料读取 |
| backend/archives/relations.py；source_scope、fact_source_view | 明确限制读取上下文用途，原有写入来源与锁继续独立执行；准确原始记录作者及来源字段保持 |

本次软件决定QA24-D01为相同真实输入少于3000条SQL并保留全部来源和授权，依据用户明确委托的常规软件决定。数据库结构、正式接口、候选规则、临床采样资格与OCR额度均保持。

本专项测量采用main基准commit 8ca48b9abb6395284a2c2ee360fbc6545d7ae763及当时工作区内容。测量采用的后端制品SHA256为cc805753fbcbf7db94423f453dd50780083e5e2a885758b92b6a22073152a68b，runtime为38487adc-a41d-4cb8-a339-b172ba2c6704。当时313份真实环境配置按各自实际制品逐项核验：290份图像处理配置采用b8c9f9233fb4ec77cb520bde843a933f34f5c0baf1e8935ba5ab80f32bc38ea5，23份后端配置采用本专项测量制品；26份原验收配置采用相同后端制品。原批准用途、audience及配置正文保持，没有新增临床批准；当前软件采用见QA27。

## 实际验证

| check_id | 操作及准确断言 | 结果 | 私有证据 |
| --- | --- | --- | --- |
| QA24-01 | 249项来源与修改前基准逐项相同，249份完整事实投影及实际来源权限保持 | 3项PASS | source-integrity.json |
| QA24-02 | 真实HTTP完整响应、签名分页状态和快照保持，SQL为2718条 | 2项PASS | after-comparison.json |
| QA24-03 | 正式撤权、原页正文拒绝、旧分页失效、实际12秒期限、同范围恢复及临时管理权限关闭 | 7项PASS、20次实际接口响应 | permissions.json |
| QA24-04 | Chrome首份、继续读取、全部263项来源与顺序、当前版本选择、刷新和无写入 | 5项PASS、4次目录响应 | browser.json |
| QA24-05 | 旧整理失效且历史保留，当前代码及授权下实际生成252项有效整理，全部内容逐项相同 | 2项PASS | delta-refresh.json |
| QA24-06 | 共享读取的实际观察资料接口保留范围版本2与32项采样，原值、独立时间、来源和未核对状态保持 | 3项PASS | period-consumer.json |
| QA24-07 | 授权调用次数、准确配置制品、权限关闭、临床数量、原件字节、整理和运行状态 | 8项PASS | final.json |

共30项专项及受影响检查通过，Django系统检查与git diff --check通过。实际脚本为.qa-work/QA24中的capture_fact_reference.py、profile_client.py、check_profile.py、check_source_integrity.py、check_permissions.py、check_directory_browser.mjs、check_period_consumer.py、refresh_delta.py、verify_qualification.py及audit_final.py。Node使用现有Playwright与实际Chrome，Python使用backend/.venv，TMPDIR指向项目内.qa-work/QA11/tmp。全部原始证据及校验值由reports/final.json维护。

当前九个整理结果保留2268项历史内容，其中八个失效、一个有效；有效结果仍为252项，最新软件采用及准确整理状态由[QA27](QA27-real-clinical-access-scope.md)维护。本记录的性能测量及源码摘要继续对应本专项验收范围。RecordVersion、RecordField、ArchiveFact、ArchiveFactVersion分别保持3、249、83和249，ObservationPeriodVersion保持2。另一来源的1208字符文书及其唯一版本保持，本人文件关系继续为两个版本且当前released。Assessment、AlertEvaluation与AlertEvent均为0。OCR仍为3项，其中2项uncertain并保留2 CNY；实际未知费用继续未知，没有新增模型调用。

## 限定范围评审

使用haonan-s006-review的PARTIAL_AUDIT模式，决议为GO，范围限定为本次真实目录的软件读取调整。

| 对照范围 | 当前依据与结果 |
| --- | --- |
| 前端与后端 | RelationDirectory及RelationSource的id、record_version_id、SourceReference与候选结构保持；真实响应摘要、完整来源顺序及Chrome页面通过。接口继续使用JSON及签名分页，流式协议不适用于该读取入口 |
| 后端与工具 | 读取上下文只在relation_directory建立并显式传递；每项authorize_case与matching_case_grants继续调用，写入及require_current路径使用原有查询和锁。OCR与其他模型工具没有更改 |
| 数据与来源 | RelationSource.id继续引用准确ArchiveFactVersion；source.page_id继续引用FilePage，ArchivePage与文件页的对应关系、锚点、文件版本和文书页分别核验；医学时间及专业证明保持原有检查 |
| 身份与期限 | 上下文只保存当前请求的资料，每次来源授权按当前账号及真实时钟核验；正式撤权、到期、恢复及旧分页失效均有实际证据 |
| 共享使用方与失效 | fact_read默认使用方保留完整事实投影，32项实际观察资料及当前范围通过；原整理按真实代码和权限变化失效，重新生成后的252项逐项保持 |
| 记录及采用 | 本记录与索引为唯一专项入口，313及26份配置按实际制品核验，两个环境均已采用当前代码，全部30项断言与私有校验值可检索 |

没有以本专项替代完整发布、临床采样、独立答案或容量验收。完整V44、V57及其余真实临床与设备要求继续由原任务和QA14维护。

## 关联影响与继续条件

T44、T46、T47、T57、QA14及QA19—QA23引用当前代码、读取与整理状态。性能数值为同一真实输入的单次本地观测，P95、P99、并发容量与生产恢复仍未建立。其他页面OCR、独立临床答案、实际手机、微信、后台恢复及视觉验收继续保留未完成状态；本次没有创建这些外部证据。
