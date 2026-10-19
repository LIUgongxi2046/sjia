# QA36 S3备份与独立恢复

## 任务身份

task_id为QA36，需求为FR-02、FR-09、NFR-07／09及OPS，唯一任务状态来源为本记录，当前IN_PROGRESS。文档日期2026-10-19，代码位于codex/s3-private-storage，基于f05dbcf106e70d890a5574385fd63b3ddd502146且存在未提交修改；main保持既有发布。

## 检索与依据

依据[QA34](QA34-storage-selection.md)、[QA35](QA35-s3-content-disposition.md)、[备份恢复规范](../../backend-design/s3-backup-recovery.md)、retention.s3_backup、retention.s3_restore、retention.content_recovery及deploy/recovery.py的当前内容。已核验真实源快照、完整备份、独立数据库恢复、对象映射及四项重放报告。输入采用用户授权的63份原件及准确元数据，临床事实、专业核对及提供方回执均不新增。

## 当前功能

S3BackupSource保存源数据库、物理集群、binding、全部文件及准确S3登记。创建备份要求实际SQL写入锁、完整数据库dump、逐对象完整读取、配置依据、独立内容checkpoint与全部SHA256。备份位于仓库以外的受保护目录。

S3ContentRestoreState保存preparing与mapped状态、完整源/目标身份、备份与映射摘要。SQL限制固定身份和映射修改、记录删除、源目标相同及本地/S3恢复记录共存。准备阶段拒绝运行环境采用。[QA40](QA40-s3-restore-lineage.md)通过迁移0011增加有序代次与父引用，实际迁移保持全部既有业务内容和准确映射；完整第二代流程继续验收。

恢复要求不同数据库、不同物理集群和不同binding。seal_mapping实际复制可用对象，保存源与目标的准确VersionId，核验缺失对象并保存独立控制证明。内容重放按映射处理真实目标版本；已存在于备份和备份后新增对象分别核验，重复执行保持准确身份。没有目标版本时只保存缺失证明。

| 文件 | identifier | 职责与调用方 |
| --- | --- | --- |
| backend/retention/s3_recovery_schemas.py | S3BackupSource、S3RestoreMapping、S3RestoreFence、S3BackupPackage | 严格来源、映射、缺失及备份文档；备份与恢复服务使用 |
| backend/retention/s3_backup.py | capture_source、create_package、verify_package | SQL锁、真实字节、完整性与范围核验 |
| backend/retention/s3_restore.py | begin_restore、seal_mapping、S3RestoreContext、load_context | 目标登记、准确映射、内容重放与引用核验 |
| backend/retention/content_storage.py | assert_manifest_storage | 原处置清单接入明确恢复上下文 |
| backend/retention/content_engine.py | replay_manifest、verify_manifest | 数据库先保存处置身份，随后处理物理对象；重复执行按实际变化更新epoch |
| backend/retention/content_recovery.py | configured_target、replay_restore、verify_restore | 当前目标、可信checkpoint和独立运行采用 |

## 真实验证

| check_id | Given／When／Then | 当前结果 | 私有原始证据 |
| --- | --- | --- | --- |
| QA36-01 | 源独立PostgreSQL增加恢复状态迁移，现有文件与登记保持 | PASS | source-migrated.json |
| QA36-02 | 实际SHARE锁核验源快照与五项拒绝 | PASS | source-snapshot-verified.json |
| QA36-03 | 创建不同物理集群的目标库，采用不同私有Bucket与binding | PASS | cluster-prepared.json、target-storage-prepared.json |
| QA36-04 | 真实数据库dump及64份对象完整备份，检查点为2 | PASS | backup-created.json |
| QA36-05 | 备份之后实际删除技术副本，可信检查点变为3 | PASS | source-probe-deleted.json |
| QA36-06 | 空目标恢复真实dump并登记preparing，再复制64份对象和五项缺失 | PASS | database-restored.json、objects-mapped.json |
| QA36-07 | 三项真实内容重放与重复执行，63份原件保持，epoch重复执行不增加 | PASS | content-replayed.json |
| QA36-08 | 备份后新增对象实际批准与删除，目标真实查询缺失，旧证明失效及重复重放保持 | PASS | post-backup-disposition.json、post-backup-replayed.json |
| QA36-09 | 十一项严格映射输入拒绝，原始映射保持 | PASS | mapping-models-verified.json |
| QA36-10 | 九份Schema、六份实际控制证明、十四项结构拒绝、十五项SQL不可变检查、两个实际重放进程、63份目标完整读取及189处范围读取 | PASS | formal-schemas-verified.json、target-integrity-verified.json |
| QA36-11 | 完整S3操作CLI、目标批准、配置采用及部署 | NOT_RUN | 待实施与验证 |

原始证据位于原项目忽略目录.qa-work/QA36/reports，包含私人标识与运行身份，不提交Git。Python3.13.13与PostgreSQL17.11用于本次真实验证；S3服务为既有本地HTTPS SeaweedFS4.48，采用真实版本、AES256及私有ACL。没有目标API或Worker。

真实新增副本在备份中不存在，目标重放之后保持67份FileObject、132份S3登记和63份可用原件，仅增加准确ContentErasure及独立缺失证明；处置记录共四项，重复执行后epoch保持。完整读取968528634字节及189处范围比较通过。数据库十五项拒绝涵盖固定身份、checkpoint、映射、状态、删除和本地状态共存；当前迁移检查返回No changes detected。

原main保持报告于北京时间14:00:00核验两套API继续ready和admitted_current，两个前端可访问，249项完整事实、63份原件、290个页面、原有权限和2CNY预留保持。对应私有证据为.qa-work/QA30/reports/main-preservation-qa36.json。完整业务或临床状态没有根据本次检查提升。

当前范围审核current-scope-final.json再次读取源与目标，确认源68份文件、69份S3登记，目标67份文件、132份登记及四份ContentErasure；源和目标各63份原件可用，双方290个页面保持。104项迁移、四项准确内容证明、九份Schema及当前源码摘要分别核验，图像处理三份源码与应用锁文件和main逐字节相同。操作入口的后续实施及真实CLI证据由[QA38](QA38-s3-operator-recovery.md)维护。

## 约束及关联

内容重放证据不能替代权限撤销、配置资格、外部影响核对及独立运行批准。ADOPTION_ADMITTED尚未建立，运行采用保持BLOCKED。四项图像制品输入保持，原main数据库和两笔待核对Ark预留保持既有状态。

QA37的20项Codex离线模拟仅覆盖明确模拟组件与当前函数分支；它不能提升本记录中的真实集成状态。完整22项功能、39页、328项状态及60项任务继续保留。

文档有效性为CURRENT，核验范围为本记录明确引用的来源与真实报告，尚未验收部分以对应check_id为准。后续持续维护同一记录及索引，完成全部关联验收后再登记任务完成。
