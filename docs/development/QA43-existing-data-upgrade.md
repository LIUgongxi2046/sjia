# QA43 现有资料升级与完整性

task_id为QA43，唯一状态来源为本记录，状态ACCEPTED，文档CURRENT。日期2026-10-19，分支codex/s3-private-storage，基于f05dbcf106e70d890a5574385fd63b3ddd502146及未提交修改。承接QA14的main集成、T39现有资料保护及T57真实运行验证；完整V39、V57与全部功能验收继续保留各自条件。

## 输入及当前决定

已经读取开发索引、QA14、T39、QA28、QA38、QA42、planning/environment.json及当前迁移与文件响应代码。原项目实际运行使用Python3.12与PostgreSQL18，计划目标为Python3.13与PostgreSQL17。QA28提供目标空库及结构验证，QA42提供当前文件响应与独立恢复验证；带有主项目现有资料的完整升级需要本专项提供实际依据。

用户已委托软件开发、软件决定及必要验证。输入为当前本地主项目的实际数据库、对象和配置证据，业务读取按已获准63份原件的准确清单限定。本专项保存原有全部数据库内容；未知OCR、预留、未核验事实及原权限保持原状态，没有临床批准或模型调用。

使用实际PostgreSQL exported snapshot绑定数据库内容与pg_dump。对象和证据采用macOS已安装cp的clonefile能力建立独立文件，逐项验证摘要、大小与独立inode，不使用符号链接或共享文件名称替代对象副本。文件清单在准备前后必须一致。数据保存在原项目忽略目录.qa-work/QA43，保留至少2 GiB磁盘余量。

目标为独立创建的PostgreSQL18物理集群及既有PostgreSQL17集群中新建的准确空库。两处目标使用专属非Superuser角色，保留既有服务与数据库；目标不启动API、Worker、Beat或模型。迁移后逐表核验旧数据，框架权限及迁移记录仅允许正式迁移产生的准确新增内容。

## 验收矩阵

| check_id | Given／When | 必需结果与重新读取 | 状态 |
| --- | --- | --- | --- |
| QA43-01 | 当前主项目、已批准原件清单及原有服务 | 核验准确数据库、版本、目录、原件、事实、权限和未知OCR状态 | PASS：当前PostgreSQL18.4、102项迁移、83项事实、249项版本、零项专业核验及两项未知OCR准确核验 |
| QA43-02 | 当前真实数据执行一致快照及独立文件复制 | pg_dump与逐表内容绑定同一snapshot，所有可用对象及配置证据保持摘要，原服务继续可用 | PASS：135张表、419个对象共2135341897字节及1429个证据文件，全部来源与副本保持 |
| QA43-03 | 新建两处准确空库及专属角色 | 目标PostgreSQL18／17身份正确，非Superuser，没有修改既有数据库或角色 | PASS：PostgreSQL18.4独立物理集群及PostgreSQL17.11专属空库；两处均使用专属非Superuser角色 |
| QA43-04 | 实际恢复全部资料并执行当前正式迁移 | 原有表和记录逐项保持，仅新增正式结构与框架元数据，重复迁移没有新增动作 | PASS：两处完整恢复后分别执行准确三项正式迁移，达到105项；重复执行没有新增动作 |
| QA43-05 | 当前代码读取两处目标中的实际资料 | ORM与migration state一致，83项事实及249项版本保留准确原值，63份原件完整文件响应及来源正确 | PASS：两处ORM状态一致，各自63份原件完整读取968528634字节及资源关闭通过 |
| QA43-06 | 检查权限、任务与环境隔离 | 原权限、未知OCR及2 CNY预留保持，没有专业核验、模型调用或目标业务进程 | PASS：全部原有表准确保持，只有正式框架元数据增加；没有目标API、Worker或模型调用 |
| QA43-07 | 完成后停止专属PostgreSQL18并复核所有环境 | 准确停止自有集群，保留两个目标与全部原资料；原有服务、数据库、原件和磁盘余量保持 | PASS：自有进程正常退出且55435端口关闭；两处目标保留，原有六处数据库及两套服务正常，余量超过2 GiB |

## 代码、证据与继续

正式迁移位于backend/files/migrations及backend/retention/migrations，公开验证入口为backend/tests/verify_existing_data_upgrade.py及backend/tests/verify_file_response_runtime.py。前者核验准确来源、非Superuser角色、全部原有表、正式框架新增内容、未知OCR与ORM状态；迁移状态检查沿用项目已有的Django no_translations方式。后者通过准确SourceManifest限制获准原件，实际读取完整响应并核验摘要、大小、响应头、资源关闭与内存范围。原始过程、私有凭据、数据库、完整清单及输出保存在.qa-work/QA43；公开记录不保存患者内容或秘密。

source-captured.json记录135张表绑定实际exported snapshot的完整pg_dump及独立文件复制。两处target-restored.json分别证明全部旧表与来源一致；target-migrate-verified.json记录files.0003_s3storedobject、retention.0010_s3contentrestorestate及retention.0011_s3_restore_generations的实际执行及重复执行。迁移后137张表中，全部旧业务数据保持准确内容，框架旧记录全部保持，仅增加三项迁移、两个ContentType和八项Permission，新建两张存储表为空。

两处target-migrated-verified.json记录Python3.13.13、实际PostgreSQL18.4／17.11、全部旧表内容摘要与当前ORM状态。target-files-verified.json分别记录63份原件共968528634字节，其中最大PDF为489686822字节；PostgreSQL18副本读取进程内存峰值91164672字节，PostgreSQL17副本为91258880字节，均处于384 MiB范围。当前后端制品为c65ed4d5b602906e7a2013e9704237d1164fa9db8ca16cf0bc0bc1a65e32ba93。

final-scope-reviewed.json重新核验原项目全部135张表、全部对象和配置证据、两个目标、既有六处数据库、Redis、对象服务及Linux网络规则。原API为ready、admitted_current，两个前端入口正常。postgresql18-stopped.json保存准确启动身份、正常停止结果与端口关闭证明；数据及所有副本继续保留。clonefile副本与来源共享APFS物理存储，本次结果限定为完整资料升级核验。

当前没有宣告main已经集成，没有以数据库升级证明临床效果、全部页面、实际手机、微信或生产RTO／RPO。完整Local CLI回归继续由QA38维护，完整承诺由planning/delivery-ledger.json和QA14维护。
