# QA41 S3运行配置、HTTP与用途处置队列

task_id为QA41，唯一任务来源为本记录，状态COMPLETED，日期2026-10-19。需求为FR-02、FR-09、NFR-07／08／09及OPS；代码位于codex/s3-private-storage，基于f05dbcf106e70d890a5574385fd63b3ddd502146并包含未提交修改。

## 检索与依据

检索QA34、QA35、QA38、QA40、T28、T57及当前存储、配置资格、会话、HTTP、Outbox、Worker与用途处置实现。QA34证明全局S3选择及实际读取；QA35证明直接业务函数的批准、技术副本删除与持久结果；QA40证明两代恢复和可信内容重放。当前专项补充完整运行配置、实际认证HTTP及真实队列消费，恢复环境的独立运行采用继续归属QA38。

输入使用用户已经授权的63份原件及必要元数据。软件配置和测试账号依照现有委托登记准确范围，实际操作仅涉及明确的技术副本。原始需求、22项功能、39页、328状态及60项任务的完整验收要求保持。

## 验收矩阵

| check_id | 角色、入口及输入 | 操作和预期业务结果 | 当前状态 |
| --- | --- | --- | --- |
| QA41-01 | 独立软件测试环境、实际S3声明、当前后端制品及明确配置依据 | 登记配置、批准与实际验证结果；进程核验准确数据库、Broker、配置、保留政策及S3身份，实际运行采用成功 | PASS |
| QA41-02 | 具有准确软件岗位的本地账号、真实HTTPS服务 | 密码登录、CSRF及会话轮换、当前岗位读取、退出和退出后拒绝分别验证；会话与Cookie实际保存 | PASS |
| QA41-03 | 获准技术副本、当前权限和准确目标版本 | HTTP创建、批准并提交处置；实际Outbox投递至独立Redis范围，由真实Worker保存完成尝试、唯一消费回执及准确S3删除证明，重新读取一致 | PASS |
| QA41-04 | 未认证、错误范围、失效权限、未批准及重复操作 | 合法成功与明确拒绝分别检查；身份、原请求、准确版本和处置终态符合既有规范 | PASS |
| QA41-05 | 来源原件、当前事实、其他测试环境及原项目服务 | 全部63份原件和必要元数据保持；原项目服务、事实版本及待核清OCR保持，技术操作没有新增模型调用或临床资格 | PASS |

## 当前入口与依赖

| 文件 | identifier或职责 | 使用方 |
| --- | --- | --- |
| backend/files/storage_factory.py | 准确S3配置、控制目录与运行声明 | 全部文件服务及运行采用 |
| backend/configurations/qualification.py | 配置批准、来源、制品和真实报告核验 | 运行配置及请求边界 |
| backend/config/runtime_admission.py | inspect_runtime、admit_process_runtime | API与Worker |
| backend/identity/service.py、identity/session.py | 密码认证、Session及CSRF轮换 | 实际HTTP登录与退出 |
| backend/api/views_support_session.py | session_read_metadata、软件岗位登录及当前会话 | 本地测试账号 |
| backend/api/views_family_dispositions.py、views_disposition.py | 两端处置读取及各自身份范围 | 家庭申请人与支持账号 |
| backend/tasks/dispatcher.py、tasks/worker.py | 明确事件投递与真实消费 | 用途处置任务 |
| backend/retention/jobs.py | disposition_tasks、submit_local_disposition、execute_disposition | 准确S3处置、任务引用及保存结果 |

## 当前实现与字段传播

支持端登录和会话读取使用identity.session.session_read_metadata返回已保存的expires_at。实际Session读取保持期限和内容一致；登录轮换Session及CSRF，退出移除当前Session。

disposition_tasks按patient_id、task_type、schema_version及准确处置标识查询任务，并按created_at、id排序。local-delete-2与s3-delete-1读取顶层disposition_id；simulation-provider-deletion-1读取disposition_ref.disposition_id。家庭读取保持申请人、当前关系及授权核验，支持读取保持当前授权和actor过滤。

| 字段 | 查询与服务 | API及类型 | 使用方与实际断言 |
| --- | --- | --- | --- |
| expires_at | 已保存Session期限；session_read_metadata | 当前支持登录、会话及公共会话响应 | 三次读取与Session记录一致；20个当前制品HTTPS场景通过 |
| task_ids | disposition_tasks；支持端追加actor条件 | DispositionView；frontend-design/v2-types.ts；正式v2-protocol.schema.json | 已保存Outbox独立确定期望任务；家庭端读取完整请求历史，支持端读取本人执行任务 |
| items、receipt_sha256 | 已保存DispositionItem及准确S3删除证明 | 两端既有DispositionView | 真实Worker完成后重读一致；五份实际HTTP响应通过正式Schema |

本次没有修改API响应结构或前端类型。原有存储、任务创建、授权核验、审核、重试和处置证明继续由既有服务处理。

## 实际运行与验收结果

运行环境为qa41-s3-runtime，使用PostgreSQL 17实际数据库、SeaweedFS实际S3服务、Gunicorn TLS服务和Celery Worker。Redis使用DB13及sjia-qa41:前缀，Worker只消费purpose队列。配置登记、软件批准、当前制品验证和API／Worker运行采用均保存准确来源。当前后端制品摘要为93e3492345a3ac18157c0650158f1d51aa0d22539c0545a5b46220d7be9513e1。

| 场景 | 实际结果 | 忽略目录中的证据 |
| --- | --- | --- |
| 当前配置、制品与进程身份 | API、Worker采用当前配置；105项迁移；Broker、平台及S3声明通过核验 | runtime-prepared-3.json；runtime-services-started-3.json |
| 支持会话、CSRF、Cookie及角色边界 | 20个实际HTTPS场景通过；Secure、HttpOnly、Lax、__Host-、会话轮换、CSRF轮换和退出均核验 | http-authentication-current-artifact-verified.json；support-session-expiry-verified.json |
| 家庭申请、支持批准、HTTP提交及真实队列 | 第一份技术副本完成准确版本删除；实际重复投递两次，只保存一次执行尝试和一份消费回执 | queue-happy-submitted.json；queue-happy-executed.json |
| 两端重读与任务关联 | 两端完成状态、Task及删除回执一致；独立依据已保存Outbox核验 | queue-happy-readback-2.json；disposition-outbox-readback-verified.json |
| 原件范围、未批准及错误批准 | 原件使用技术副本政策遭到拒绝；未批准申请没有可执行项目；错误范围批准遭到拒绝 | queue-happy-submitted.json及实际HTTP captures |
| 提交后撤销执行账号授权 | HTTP拒绝读取；Worker保存失败任务与尝试；技术副本保持quarantined，准确字节和版本保持完整，没有删除journal或receipt | queue-revoked-permission-observed.json；queue-revocation-retry-verified.json |
| 同范围重新授权及重试 | 新授权保留原撤销记录；HTTP重试创建关联原失败任务的新Task，重复提交返回同一Task；真实Worker完成删除 | queue-revocation-retry-verified.json |
| 家庭任务历史与支持账号范围 | 家庭端读取失败与成功任务；执行账号读取两项任务；独立审批账号的任务列表为空 | disposition-retry-outbox-readback-verified.json；disposition-approver-scope-readback-verified.json |
| 正式响应Schema | 五份实际HTTPS响应通过两个正式Schema文件的核验 | disposition-response-schemas-verified.json |
| 原件及其他环境 | 63份原件全部读取，968,528,634字节及摘要一致；111张未涉及表的数量和10组原有配置元数据保持；三个恢复数据库和原项目服务正常 | final-preservation-verified.json |

独立环境保存三项s3-delete-1任务：两项完成、一项因权限撤销失败，三项均有实际执行尝试；两份技术副本均已处置，保留两份唯一消费回执和准确删除证明。原项目保持83项事实、249个版本、零项专业核验、两项未核清OCR以及2 CNY预留。本次新增模型调用为零。

公共复验入口为backend/tests/verify_support_session_expiry.py、verify_s3_runtime_http.py及verify_disposition_http_readback.py。使用实际Django配置、正在运行的本地HTTPS服务、明确CA文件和既有授权账号文件；输出路径必须尚不存在。处置读取复验接收已保存的disposition-id，不重新提交删除。

## 数据、环境与证据

实际数据库、进程、队列、账号授权、配置来源、制品摘要与命令由原项目忽略目录.qa-work/QA41保存。私有原件、密码、密钥、Session、个人身份及运行报告不进入Git。运行配置的批准和验证结果分别保存实际依据；临床规则、专业资格及提供方回执使用各自独立要求。

本专项没有新增模型、Prompt或RAG检索流程；AI效果评价继续保持原任务要求。视觉、手机、微信、临床Gold与生产P95／P99、RTO／RPO分别等待对应真实证据。软件检查不代替这些验收。

文档有效性CURRENT，覆盖当前制品在独立S3环境中的配置、认证HTTP和用途处置队列验收。专项验收服务在任务全部终止后正常停止，原项目服务继续运行。

全部功能验收仍为INCOMPLETE。QA40已完成第一版和第二版备份的完整CLI恢复；QA38的完整Local备份恢复、当前入口拒绝复验和恢复环境独立采用，以及临床Gold、设备、微信、视觉和生产指标继续保留各自的真实验证要求。
