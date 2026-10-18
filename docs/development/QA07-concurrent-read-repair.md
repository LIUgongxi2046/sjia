# QA07 并发读取、照片上传与摘要处理修复

任务来源：2026-10-16用户要求修复发现的问题。本记录管理QA07状态，当前为ACCEPTED，范围为本地软件修复。验收项目包括并发读取、运行配置检查、持续worker、权限拒绝、照片选择和任务查询。

代码基线为`8ca48b9abb6395284a2c2ee360fbc6545d7ae763`及工作区保留修改。资料来源包括QA06开发记录、QA06测试报告、authorization.policy、summaries.service、config.runtime_admission和core.demo_worker。检索当前开发索引中的读取、权限、摘要与运行配置记录，采用当前代码和独立数据库依据。文档有效性为CURRENT，last_verified为2026-10-16，适用本次软件修复范围。

读取授权须支持同一账号的并行请求，并继续检查环境、账号、auth_epoch、关联与字段范围。运行配置检查须读取完整的已提交配置身份。持续worker须执行实际文件校验与摘要任务，并保留配置变化和权限变化的拒绝行为。

| 文件或identifier | 职责与调用方 |
| --- | --- |
| backend/authorization/policy.py evaluate | HTTP读取与服务授权 |
| backend/summaries/service.py current_summary_config | 配置检查、摘要提交与任务执行 |
| backend/config/runtime_admission.py inspect_runtime | API与worker运行配置检查 |
| backend/core/demo_worker.py worker_scope | 独立家庭体验worker的账号及资料范围检查 |
| backend/records/snapshot.py authorize_record_snapshot | 同一只读事务内的权限及资料检查 |
| backend/api/views_family.py local_task_snapshot | 摘要和文件校验任务查询 |
| backend/files/validation.py scope | 文件校验采用当前授权；查询路径保留全部检查 |
| guardian-h5/src/components/UploadedPhotos.tsx select | 打开系统照片选择；上传开始时更新忙碌状态 |

照片入口等待当前上传来源及授权选择就绪；系统选择文件期间保持输入可用，实际上传及识别处理期间显示忙碌状态。草稿保留原件标识和文件名，刷新后通过受控文件接口读取同一原件。

首页、字段配置、上传选项、摘要及本地任务查询使用REPEATABLE READ READ ONLY事务。权限、关联、资料和任务状态在同一数据库快照内读取，后续请求采用当前已提交状态。无锁授权检查要求有效的只读事务。普通授权读取使用共享账号锁；变更账号和权限时继续检查当前状态。

摘要和文件任务使用共享环境锁及账号、患儿的FOR NO KEY UPDATE锁，保持变更互斥并允许审计外键检查。模板与运行配置的查询路径不取得排他锁。配置身份比较只采用完整检查结果，数据库查询异常继续报告不可用。worker范围检查读取已有资料，执行服务在写入前重新检查并锁定当前权限。

验证使用独立数据库`test_sjia_qa06_20261016`、API端口8002和前端端口5176。输入为用户授权的示例资料；HTTP、数据库及任务处理均使用项目服务。原始证据位于忽略目录`.qa-work/QA06/reports`，检查入口为`.qa-work/QA06/concurrency_check.py`、现有HTTP及浏览器脚本。当前5173实例资料保留。

| 验收ID | 操作与断言 | 当前结果 |
| --- | --- | --- |
| QA07-01 | 两个PostgreSQL连接同时读取同一账号权限，首页与登记页面并行读取成功 | PASS：数据库回归、7项HTTP并发查询和页面检查通过 |
| QA07-02 | 摘要模板被另一事务锁定时，检查完整的已提交配置身份 | PASS：HTTP 200、admitted_current，已加载身份与当前身份相同 |
| QA07-03 | 持续worker处理新摘要，重新读取completed任务与摘要条目 | PASS：页面完成摘要；再次提交任务完成，108条来源引用重读通过 |
| QA07-04 | 过期auth_epoch和配置变化继续拒绝访问或执行 | PASS：数据库及实际运行身份检查通过 |
| QA07-05 | 系统照片选择后上传示例原件，实际校验完成，草稿恢复后原件摘要相同 | PASS：原件上传、后台校验、刷新及SHA-256核对通过 |
| QA07-06 | worker处理期间读取摘要与文件校验任务，保持权限检查和状态一致 | PASS：任务轮询、持久终态及准确结果重读通过 |

`tests.test_read_permissions_concurrency`与`tests.test_home_read_snapshot`共11项实际PostgreSQL检查通过，覆盖并行授权、读取选项、外键保存、账号变更、auth_epoch、只读事务要求、worker范围和首页资料。命令为`TMPDIR="$PWD/.qa-work/QA06/tmp" backend/.venv/bin/python .qa-work/QA06/manage.py test tests.test_read_permissions_concurrency tests.test_home_read_snapshot --settings=qa07_test_settings --keepdb --noinput`，证据为`qa07-regression-release.log`。`npm run build`及TypeScript检查通过，证据为`qa07-build-final.log`。代码语法和`git diff --check`通过。

浏览器11个场景分别通过，实际断言包括患儿范围、记录原值和时间、更正历史、草稿版本、原件字节及任务终态。证据为`qa07-e2e-final.tap`、`qa07-e2e-final.json`、`qa07-retest-final.tap`、`qa07-retest-final.json`、`qa07-draft-accepted.tap`和`qa07-draft-accepted.json`；运行入口为`.qa-work/QA06/e2e.test.mjs`。页面验证仅使用DOM及实际HTTP。

最新摘要任务为`6bbd3629-feb2-49fb-97f8-0e8cf1a1fe49`，结果为`e85efaa2-444a-4e4c-8d76-61b62e76b63e`。实际接口确认completed、108条来源引用、范围及字段标识一致，needs_regeneration为false。证据为`qa07-summary-request.json`、`qa07-summary-task-result.json`、`qa07-summary-check-result.json`及`qa07-summary-read.log`。软件发布身份为`a569be32-ece1-46b0-9ddd-93c17a21791b`，数据库及代码身份见`qa07-database-final.json`。UploadedPhotos.tsx的SHA-256为`d207ea76ad3d10caeea441a8a2ddcc89e5d4965f9eaa50d3377fbdfaa987c315`。

当前gate_version为17，publication_epoch为16，软件校验代码摘要为`fdb3c840c1bcf6b93188c7020f819f2db23f8e23835d07a937d420e43c7bd6ed`。数据库包含2位示例患儿、34条记录、37个记录版本、9份草稿、8份原件和5份摘要，家庭账号服务岗位授权数量为0。

QA06当前问题状态及验收报告已同步。2026-10-18核验验收环境：5176前端、8002 API和持续worker使用独立后台进程运行。页面、API代理及readiness均返回HTTP 200，已有账号登录、摘要任务及108条摘要来源引用读取通过。数据库继续使用`test_sjia_qa06_20261016`，包含102项迁移及2位已有示例患儿。当前运行证据位于`.qa-work/recovery-20261018/verification.md`及同目录日志。外部模型、手机、微信、临床及视觉检查未包含在本次修复验收中，相关完整业务验收继续由QA06维护。
