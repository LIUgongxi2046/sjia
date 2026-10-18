# QA28 目标运行版本与空库迁移

当前状态ACCEPTED，文档有效性CURRENT，last_verified为2026-10-19 05:14（Asia/Shanghai）。关联OPS、V01、V02、T01、T02、T35、T39、T57和QA14。本记录为目标版本兼容性专项唯一状态来源，完整既有数据迁移、恢复及生产验收继续由原任务维护。

当前运行制品、真实资料与整理状态由[QA29](QA29-real-original-range-reads.md)维护；本记录保留05:14目标版本专项的准确检查范围及证据。

当前planning/environment.json的目标版本为Python 3.13及PostgreSQL 17，现有本地验收服务使用Python 3.12.7及PostgreSQL 18.4。现有服务的实际版本和已通过检查保持原范围，目标版本需要独立验证。

Given 当前全部正式迁移、依赖锁文件及目标版本，When 在项目内独立目录安装实际运行程序并迁移全新空库，Then 当前正式Schema及约束可检查，现有真实资料及服务继续保持。安装物、缓存、构建日志、数据目录与报告均位于已忽略的.qa-work/QA28；没有临床样本生成、提供方调用或现有数据库更换。

采用PostgreSQL官方源码和现有uv的受管Python安装方式，所有下载保持TLS校验，安装及缓存位置明确设置。Python、数据库与系统工具的来源、版本、准确摘要、命令及结果按实际执行登记。软件安装与局部验证依据用户持续开发及软件决定授权，不产生临床批准。

## 目标版本及正式迁移

| 检查 | 实际结果 | 本地证据 |
| --- | --- | --- |
| Python与依赖 | CPython 3.13.13；完整锁文件80个包实际安装，uv一致性检查通过；数据库、Django、Pydantic、PDF、音频及识别模块直接导入成功 | .qa-work/QA28/reports/python.json |
| PostgreSQL来源与构建 | 官方17.11源码，SHA256为dd27f2b3c59e73ed14aa3324901242bf69a032a6347805f274e6260322d42979；原生configure、make world-bin及install-world-bin通过，包含OpenSSL、ICU、LZ4及Zstd支持 | .qa-work/QA28/reports/postgresql-build.json |
| 独立实例身份 | SCRAM认证、127.0.0.1:55432、独立物理集群，关闭Unix socket；未变更现有5433端口数据库 | .qa-work/QA28/reports/target-start.json |
| 正式迁移 | Django系统检查通过；102项正式迁移完成，后续迁移计划为空，重复执行没有新增操作 | .qa-work/QA28/reports/migrations.json及准确命令日志 |
| Schema比较 | 135张表、一个视图的目录身份，1554个字段、940项约束、522个索引、81个触发器、33个函数、两个DOMAIN与102项迁移记录逐项一致 | .qa-work/QA28/reports/catalog-comparison.json |
| 补充目录比较 | 一个视图的完整定义、八个序列的结构、零项RLS策略及两个数据库扩展逐项一致；源环境全部1879项目录约束均为enforced | .qa-work/QA28/reports/additional-catalog-comparison.json |
| 实际字段参数传输 | 既有本人三个字段、三个记录版本共249项，包含96个数值；中文原文、数值、限定符、单位、状态与空值经只读参数传输逐项保持 | .qa-work/QA28/reports/target-fields.json |
| 目标业务表 | 126张业务表全部为空；八张Django框架表分别登记，其中auth_permission为524行、django_content_type为131行，其余为零 | .qa-work/QA28/reports/target-fields.json |
| V35完整设计检查 | Python 3.13.13及PostgreSQL17.11；71组字段映射、82张基础及病例表之后的22张新增表、218项新增约束通过；没有插入病例 | .qa-work/QA28/reports/design.json及check-t35/receipt.json |
| 实例清理及最终审计 | 仅停止准确独立物理集群，55432端口关闭；七项最终来源、回执、依赖、范围及服务审计通过 | .qa-work/QA28/reports/final.json |

原生目录读取使用只读事务。PostgreSQL17与18的表级非空约束目录表示不同，本次逐列完整比较attnotnull，其他约束定义逐项比较，原始目录条目分别保留。具体目录依据为[PostgreSQL17 pg_constraint](https://www.postgresql.org/docs/17/catalog-pg-constraint.html)、[PostgreSQL18 pg_constraint](https://www.postgresql.org/docs/18/catalog-pg-constraint.html)及[pg_attribute](https://www.postgresql.org/docs/17/catalog-pg-attribute.html)。

Python安装使用[uv受管Python](https://docs.astral.sh/uv/guides/install-python/)，PostgreSQL使用[官方17.11源码](https://www.postgresql.org/ftp/source/v17.11/)及[官方构建要求](https://www.postgresql.org/docs/17/install-requirements.html)。所有安装物、缓存和临时目录均位于项目忽略目录，没有更改系统程序。

data-design/check_v2.py提供明确的数据库名称及报告路径参数，保留默认调用方式，依据实际server_version登记目标检查结果。使用markdown-it-py解析完整字段表，SQL交由PostgreSQL执行。设计检查补充安装markdown-it-py 4.2.0和mdurl 0.1.2，原有80个锁定包的版本保持。

## 当前资料、服务及完整验收

现有真实资料的完整249项事实投影SHA256保持c842805778a23c4228d1d7bbd98cdf7249979a94742dcbe69f0f141f53cf98e6。三个RecordVersion、249个RecordField、83个ArchiveFact、249个ArchiveFactVersion、九份整理及2268项历史保持；专业核对与评估数量均为零。当前本人字段范围保持，没有新增病例核对权限。三项OCR中两项未知结果继续保留2 CNY，没有新增模型调用。

两套后端实际返回ready及admitted_current，两个前端入口可访问，当前后端制品保持4834cf5b390e0c3741e7153d84de859849c6d18a5de71bc22bbb6755e8f4eefc。六项实际保持检查见.qa-work/QA28/reports/live-runtime.json。

交付记录覆盖30项原承诺、60项验收和60项实施任务，全部原承诺继续IN_SCOPE。当前验收为PASS两项、STALE九项、NOT_RUN四十九项；60项任务保持IMPLEMENTED，完整交付complete为false。V35使用本次准确设计回执，V37准确摘要保持匹配，V27待当前输入的完整导入及回读验证。交付结构、依赖、覆盖及当前PASS证据校验通过，见.qa-work/QA28/reports/delivery-current.json。

本次参数检查没有持久化真实临床记录，没有恢复既有资料，也没有建立专业核对或独立临床答案。V01、V02、V39、V57及其他全部业务验收保留完整条件，真实设备、微信、音频、视觉、正式容量、RTO／RPO及临床有效性仍需对应实际证据。06:00提交目标为main，提交内容继续排除原件、临床原文、账号、数据库、对象副本、配置证据及本地运行报告。

本次限定审查覆盖目标安装、完整正式迁移、十三类目录比较、真实参数传输、设计检查、当前证据绑定及服务保持，结论为该专项GO。完整交付继续IN_PROGRESS，原有全量验收条件保持。最终审计回执时间为2026-10-19 05:14:03（Asia/Shanghai），证据文件分别保留准确SHA256。
