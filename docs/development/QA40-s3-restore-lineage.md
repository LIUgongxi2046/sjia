# QA40 S3恢复历史引用链

task_id为QA40，唯一任务来源为本记录，需求FR-02、FR-09、NFR-07／09及OPS，状态COMPLETED，日期2026-10-19。产品目录为codex/s3-private-storage，基于f05dbcf106e70d890a5574385fd63b3ddd502146并包含未提交修改，main保持已发布内容。

## 检索与依据

读取QA36、QA38、QA39、retention.s3_backup、retention.s3_restore、retention.content_recovery、迁移0010／0011及当前正式Schema。已恢复环境使用完整引用历史保存多代状态，原始VersionId、映射、缺失证明及可信journal继续保持。

本任务使用用户已批准的真实资料及既有独立测试环境。新元数据仅描述已经发生的恢复与登记，不生成临床事实、提供方回执或模型结果。数据与服务设计采用[历史引用链规范](../../backend-design/s3-restore-lineage.md)。

## 验收与证据

| check_id | 必需行为 | 当前状态 |
| --- | --- | --- |
| QA40-01 | 实际第一代恢复记录转为严格历史文档，摘要、字段和时间分别校验，错误输入拒绝 | PASS：真实68项映射、67份来源文件、64项当前登记、19项文档拒绝及六项正式Schema拒绝 |
| QA40-02 | SQL保存有序父子关系，历史身份与已完成映射不可修改或删除，已有资料迁移保持 | PASS：三处数据库迁移保持业务内容；实际第二代追加并保留父代，两代环境22项SQL拒绝通过 |
| QA40-03 | 已恢复来源实际执行完整备份，保存全部历史状态、映射及当前原件 | PASS：正式CLI生成第二版备份，63份原件共968528634字节，完整第一代历史及checkpoint4保持 |
| QA40-04 | 不同集群与binding实际再次恢复，原始处置清单通过历史链访问当前准确对象或缺失证明 | PASS：第二代保存两项状态、64项映射、三项旧对象缺失及195项登记；四项可信内容重放通过 |
| QA40-05 | 重复、并发、错误父引用、缺失历史和全部受影响第一代流程分别验证 | PASS：第一版和第二版完整CLI恢复分别通过，各自63份完整读取、189处范围比较及两个重放进程通过；第一代19项、第二代22项SQL拒绝通过 |

恢复入口保留准确批准、不同目标、完整历史和独立运行采用条件。QA39证据证明其记录时间对应的第一代恢复与隔离；QA40证据分别记录当前实现的再次备份、再次恢复和内容核验范围。

## 当前实现与验证

retention.s3_lineage_schemas定义S3RestoreGeneration、S3RestoreLineage及S3LineageBackupPackage，使用严格Pydantic判别联合读取完整备份。历史状态时间规范为UTC，映射保存原有内容与摘要。八份关联Schema、第一版备份、第二版备份及真实两代完整历史均通过正式检查；19项文档拒绝和六项正式Schema拒绝通过。

retention.s3_lineage连接capture_source、create_package、verify_package、begin_restore、seal_mapping、load_context、原始清单解析及configured_restore_root。全部历史SQL登记、各代来源与目标身份、父映射、文件版本、处置变化和独立checkpoint逐项核验。具有历史的来源使用s3-consistent-backup-2；明确操作文档必须绑定该格式及准确restore_lineage_sha256。CLI分别核验第一版和第二版备份，错误格式拒绝。

迁移0011为S3ContentRestoreState保存generation、parent、parent_mapping_sha256与ancestor_sha256，数据库保护不可变身份、已完成映射、父代来源、时间和不同集群条件。已在sjia_qa33_business_storage、sjia_qa36_restore与sjia_qa38_cli_restore实际执行，三处各135张业务表摘要保持，既有restore_id与mapping_sha256保持。迁移前分别保存完整数据库dump；当前三个测试数据库均有105项迁移。该迁移仅支持向当前结构推进，环境恢复继续使用完整备份和独立目标。

再次备份来源为sjia_qa36_restore，67份文件、132项全部历史登记、64项当前登记及63份可用原件。正式CLI在SQL冻结、导出snapshot和独立journal锁期间完成第二版备份，实际测量24.526秒。备份manifest摘要为b5d2d098af9522df6772809c50fc610973fc3ca9b49729797d54980bdce58f4b。来源快照、完整历史及checkpoint4在备份前后保持。

正式CLI的第一版目标为sjia_qa40_v1_cli_restore，第二版目标为sjia_qa40_v2_cli_restore。两者分别使用新的空数据库、空S3命名范围和准确binding，物理集群与对应来源不同。Linux恢复身份、内核网络规则和独立可信journal保持。来源在整个CLI执行期间持有全部业务表SHARE锁与journal锁，恢复完成后来源快照及checkpoint保持。

| 完整CLI范围 | 结构迁移 | 当前历史与登记 | 实际原件与重放 | 拒绝检查 |
| --- | --- | --- | --- | --- |
| 第一版备份 | 104项推进至105项，仅执行明确批准的0011 | 一代状态、68份文件、132项登记、290页、四项处置 | 63份共968528634字节完整读取、189处范围比较、四项内容证明及两个实际重放进程通过 | 六项历史拒绝、19项SQL拒绝 |
| 第二版备份 | 保持105项，没有新增迁移 | 两代状态、67份文件、195项登记、290页、四项处置；原父代内容与映射保持 | 63份共968528634字节完整读取、189处范围比较、四项内容证明及两个实际重放进程通过 | 八项历史拒绝、22项SQL拒绝 |

两份恢复均由当前deploy/recovery.py从pg_restore、结构核验、begin_restore、seal_mapping持续执行至可信内容重放完成。目标epoch、文件、登记和控制文件在复验前后保持。原有sjia_qa40_lineage_restore及其他恢复环境继续保留，各自已有证明按实际范围维护。

执行入口为backend/tests/verify_s3_restore_lineage.py、verify_s3_restore_lineage_sql.py和verify_s3_restore_lineage_runtime.py。文档检查禁止网络连接；运行检查使用实际SQL、完整原件和可信journal。运行入口明确接收expected-generations，分别核验第一版根历史及第二版完整父代历史。当前核心报告为full-cli-v1-executed.json、full-cli-v2-executed.json及各自runtime-verified、sql-verified报告；full-cli-scope-final.json核验当前源码、八份正式Schema、六处实际数据库、隔离规则、存储进程与原项目服务。全部报告保存在原项目忽略目录.qa-work/QA40。检查没有新增临床事实、专业核对、模型请求或运行采用。

存储服务共64个volume槽位，新增服务仅监听本机地址，现有服务保持运行，minimum free space继续为2GiB。容量检查按实际原件、数据库及操作余量执行。通过npm的cacache接口清理当前用户拥有的2.14 GB下载缓存，受保护条目、已安装依赖及全部数据保留；两套前端TypeScript检查通过。完成恢复后实际磁盘余量仍满足2GiB要求。

旧结构备份在完整CLI执行期间通过upgrade_s3_restore_schema由104项迁移推进至105项迁移。独立结构检查另外证明缺少明确升级批准时拒绝、批准后仅执行0011及全部受检表内容保持。完整CLI报告和legacy-schema-upgrade-verified.json分别保存各自实际范围。两份原始备份摘要、来源状态及独立checkpoint保持。

## 未完成工作

完整Local文件存储回归及当前操作入口的拒绝复验继续归属QA38；权限撤销、配置停用、外部影响、发布资格和恢复环境独立运行采用继续按QA38及既有规范执行。[QA41](QA41-s3-runtime-http-queue.md)已验证独立S3环境的配置、真实HTTP认证、队列、撤权和重试，其证据范围与恢复环境运行采用分别维护。各项本地耗时只描述本次环境，生产RTO／RPO继续需要对应证据。

## 关联影响

字段传播包括数据库、ORM、严格Pydantic文档、正式JSON Schema、备份完整性读取、恢复映射、内容清单核验、运行采用及CLI。前端、模型请求与提供方协议没有新增字段。既有备份格式保持准确读取，新引用链格式明确版本；全部真实结果和当前源码摘要保存到原项目忽略目录.qa-work/QA40。

文档有效性CURRENT，核验日期2026-10-19，覆盖本记录明确报告的设计、文档、SQL、真实备份和两种格式的完整CLI恢复。QA40任务状态COMPLETED，完整功能验收INCOMPLETE。
