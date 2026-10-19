# S3备份、准确映射与内容重放

本规范关联FR-02、FR-09、NFR-07／09、OPS及[QA36](../docs/development/QA36-s3-backup-restore.md)。适用于当前明确的私有S3存储选择，使用实际PostgreSQL和准确对象版本。完整运行采用仍由独立发布条件控制。

## 备份与目标身份

capture_source要求事务内存在file_object和s3_stored_object的实际SHARE锁，并读取current_database与pg_control_system。源快照保存全部FileObject及当前binding的全部S3登记，包含处置历史。可用文件必须有准确登记，已处置文件与登记的状态必须一致。

create_package在SQL写入冻结范围内核验相同源快照。数据库dump、全部可用登记的真实完整字节、配置依据和独立内容checkpoint共同构成备份。对象字节使用登记UUID作为文件名，保存SHA256与大小；已处置对象执行真实版本缺失查询。完成记录绑定manifest的完整摘要，verify_package逐项重新核验范围与字节。

begin_restore要求不同数据库、不同物理集群及不同binding。恢复根为当前用户所有的私有目录，控制目录准确位于s3-control；准备标记必须绑定目标、备份摘要和内容checkpoint。数据库恢复后的文件与来源登记必须逐项相符。

S3ContentRestoreState按generation保存恢复记录，parent与准确摘要关联上一代。preparing允许空mapping_sha256与mapped_at；mapped要求两者存在且时间有效。SQL检查及触发器限制相同binding、身份变更、映射变更、删除与本地恢复状态共存，后续记录另外要求准确父代及不同物理目标。[QA40](../docs/development/QA40-s3-restore-lineage.md)完成三处真实数据库迁移、第二版备份和实际第二代恢复。具有历史的来源使用s3-consistent-backup-2，完整restore_lineage同时绑定文档、SQL和当前目标；操作文档明确格式与历史摘要。恢复旧结构之后，在读取当前ORM字段之前执行准确批准版本的向前迁移。

## 映射与证明

| 对象状态 | 映射内容 | 重放与核验 |
| --- | --- | --- |
| 备份中可用的S3登记 | 准确源登记与实际复制后的目标登记 | 目标VersionId、ETag、摘要、大小及登记身份逐项核验；使用当前恢复专属operation_id删除准确目标版本 |
| 备份中已处置的S3登记 | 准确源登记、目标版本清单请求与零版本观察 | 保存不可变S3MappedAbsenceFence，并重新查询目标缺失 |
| 已处置且没有当前S3登记的文件 | 准确源文件元数据、目标缺失观察 | 保存来源文件与缺失证明；按相同映射重新核验 |
| 备份之后才产生的文件 | 当前快照、数据库及登记均没有对应身份和对象键 | 核验来源binding，查询真实缺失并保存S3RestoreFence；重复请求核验全部身份 |

S3ObjectReference完整包含binding、object_key、VersionId、ETag、SHA256、大小及AES256。源与目标引用分别保存。operation_id通过uuid5生成，命名空间关联原处置操作及本次restore_id。

缺失观察只记录实际版本查询请求、观察时间、零版本和零删除标记。控制证明绑定恢复、映射、文件、清单及原始引用。已映射缺失与备份后新增对象采用独立文档类型；当前对象重新出现时拒绝重放或核验。

load_context核验数据库与集群、源目标binding、控制目录身份、源快照摘要、备份摘要、全部历史状态、保存映射及准备标记。原始内容清单依据来源binding定位准确历史快照，核验原始文件和reference，再通过当前映射访问当前登记或缺失证明。数据库处置身份先保存，物理处理随后执行；已有处置身份必须同时符合计划、版本、资料分组及证明摘要。epoch只随实际状态变化更新。

## 文档与验证职责

| 正式Schema | 内容 |
| --- | --- |
| [s3-backup-source](../data-design/s3-backup-source.schema.json) | 源身份、文件及准确登记 |
| [s3-object-absence](../data-design/s3-object-absence.schema.json) | 实际目标缺失观察 |
| [s3-restore-mapping](../data-design/s3-restore-mapping.schema.json) | 完整复制与缺失范围 |
| [s3-restore-target](s3-restore-target.schema.json) | 已映射目标及preparing定义 |
| [s3-backup-package](../data-design/s3-backup-package.schema.json) | 完整备份、对象、配置依据与checkpoint |
| [s3-restore-lineage](../data-design/s3-restore-lineage.schema.json) | 完整代次、父映射、原始引用与历史摘要 |
| [s3-lineage-backup-package](../data-design/s3-lineage-backup-package.schema.json) | 具有完整恢复历史的第二版备份 |
| [s3-mapped-absence-fence](../data-design/s3-mapped-absence-fence.schema.json) | 已知来源的准确缺失证明 |
| [s3-restore-fence](../data-design/s3-restore-fence.schema.json) | 备份后新增对象的缺失证明 |

JSON Schema核验字段、类型、格式、引用和状态结构；Pydantic另外核验数据库/集群/binding相异、完整范围、唯一身份、引用内容及时间关系。执行路径继续核验实际SQL、S3和独立文件，不能只凭文档结构完成恢复。

正式文档测试使用backend/tests/requirements-schema.txt中的jsonschema[format-nongpl]。date-time检查要求完整格式依赖；测试入口缺少相应checker时立即抛出RECOVERY_SCHEMA_FORMAT_DEPENDENCIES_REQUIRED。依赖安装方式依据[jsonschema官方文档](https://python-jsonschema.readthedocs.io/en/stable/validate/#validating-formats)。应用的requirements.lock.txt保持原有内容。

## 运行采用与完整交付

replay_restore在独立可信journal锁内重放全部清单并再次核验，保存准确最新checkpoint。新增意图使旧证明失效。内容重放完成之后仍需权限撤销、配置停用、外部影响、发布资格及独立运行批准；本模块没有启动服务或启用外发网络。

当前真实函数检查覆盖首次独立恢复、备份后删除、备份后新增、重复执行和两个实际进程。deploy/recovery.py的明确S3备份入口已通过真实SQL冻结、pg_dump、63份原件完整备份及十一项拒绝检查。[QA39](../docs/development/QA39-linux-recovery-isolation.md)维护实际Linux双地址族网络限制与对应版本的目标拒绝证据。[QA40](../docs/development/QA40-s3-restore-lineage.md)完成第一版和第二版备份的完整当前CLI恢复：前者由104项迁移推进至105项，后者保留完整父代历史；两处目标各自63份原件完整读取、189处范围比较、四项内容证明及两个重放进程通过，分别通过19项和22项SQL拒绝。六处数据库、原项目服务和独立checkpoint保持。目标运行采用保持BLOCKED，应用与Worker没有启动。完整Local流程、当前CLI拒绝复验及独立运行采用继续归属QA38。[QA41](../docs/development/QA41-s3-runtime-http-queue.md)已验证独立S3软件环境的配置、真实HTTP认证、队列及撤权重试；设备、微信、视觉、临床和生产指标继续保留原验收要求。
