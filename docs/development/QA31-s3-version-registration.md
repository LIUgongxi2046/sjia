# QA31 S3版本登记与真实持久性验证

当前组件专项检查PASS，last_verified为2026-10-19 07:37（Asia/Shanghai），关联FR-02、FR-09、NFR-07、NFR-09、T12、T26、T33、T40、T56和T57。开发位置继续使用codex/s3-private-storage独立分支；完整应用接入继续IN_PROGRESS，原main验收服务保持。

Given QA30已经逐份核验的63个真实S3版本和全新独立PostgreSQL17数据库，When 登记、重新读取、并发写入、处理事务终止及核验准确处置结果，Then 相同来源和实际版本保留，同键登记唯一，完整字节一致，已停止版本无法重新使用，原始资料保持。

## 实现与字段传播

| 文件及identifier | 当前实现 |
| --- | --- |
| files/models.py；S3StoredObject | 独立保存binding、对象键、实际VersionId、ETag、完整SHA256、大小及登记状态，FileObject列和业务版本语义保持 |
| files/migrations/0003_s3storedobject.py | 新增独立技术表、联合唯一及三个业务约束；数据库触发器拒绝身份修改、直接删除及已处置登记重新启用 |
| files/s3_registered.py；S3RegisteredStore | 明确接收已配置的S3驱动和独立控制目录；按binding与对象键执行事务级锁，接入现有put_immutable、get、get_range、exists方法形式 |
| adopt_reference、reference_for | 显式采用前完整读取及核验准确单一版本；登记状态、控制目录和已有FileObject内容一致性检查贯穿引用 |
| S3StoredObjectRecord、registration_for | 严格表达已保存UUID、准确版本、状态及操作时间；重复采用保持登记身份和创建时间 |
| 两份正式JSON Schema | 与实际Pydantic模型共同核验准确引用及登记快照，复用同一规范对象键校验 |
| tests/verify_s3_registered_storage.py | 使用真实来源、独立实际数据库及真实S3，分别执行登记和验证，子进程观察实际拒绝及并发结果 |

字段、来源、保留边界及迁移设计见[数据规范](../../data-design/s3-storage-registration.md)。数据表提供登记生命周期；实际提供方结果和完整业务处置证明分别核验。应用factory、原件接口、全部用途副本、处置journal及恢复适配仍需完整接入。

## 实际环境和迁移

独立数据库为sjia_qa31_storage，PostgreSQL17.11，监听127.0.0.1:55432。使用新建的数据库所有者账号，该账号没有超级用户、创建数据库或创建角色权限。该环境与已有真实资料数据库使用不同数据库、运行账号和应用密钥，没有启动API、Worker或调度器。

目标Python3.13执行Django check、模型与迁移状态检查、103项正式迁移、再次应用和触发器检查。结果均通过，再次应用没有待执行迁移，makemigrations检查没有产生文件。准确原始结果保存于忽略目录QA31/reports/migrations.json及迁移日志。

关系字段和时间均来自真实S3引用或实际软件操作。当前环境临床记录为零，没有生成患儿、医学原值、专业核对、评估、通知或模型任务。

## 真实输入与结果

来源沿用QA30正式清单，63份原件共968528634字节，来源清单SHA256为dc7e0d330646a0aaf7e8439e047e4a292c9437ca7f1270b111e69dd23bbac70b。准确对象引用SHA256为26d0180c7f078d7886414268fbeec05e9ac08b477f26a4e40383db46d302f28f。现有S3版本保持，完整数据和私有凭据继续保存在忽略目录。

| 检查 | 实际结果 |
| --- | --- |
| 63份登记 | 每份完整读取、来源摘要、单一实际版本、保存重读和正式Schema通过，执行8.06秒 |
| 63份持久引用 | 全量重新采用、准确版本读取、范围读取及快照序列化通过，原登记UUID、创建时间、SHA256和VersionId保持 |
| 事务终止及恢复 | 实际S3写入成功后撤销数据库事务，登记为空且外部版本仍在；明确重试建立一份登记，准确外部VersionId保持 |
| 两个并发进程 | 同一真实输入和技术对象键，两个实际进程返回相同登记与版本，数据库一份登记，S3一个版本 |
| 实际处置状态 | 技术验证副本由提供方实际删除，登记停止使用；后续读取、写入及数据库重新启用均被拒绝 |
| 十二项拒绝 | 身份修改、直接删除、重复键、非法路径、非法版本、非法状态、负数大小、不同字节、重新启用、已处置读取、已处置写入、不可访问控制目录分别通过 |
| 数据保持 | 原有63份登记完整快照及原件全量SHA256保持；三份技术副本均已准确删除并保留停止状态，临床记录保持零 |

验证阶段执行48.12秒，准确报告为QA31/reports/registered-storage.json，采用阶段报告为adopted.json。并发进程及实际提供方删除响应分别保存，报告不使用生成的医疗资料或提供方替代响应。

技术副本清理结果保存于probe-cleanup.json，最终七项综合复核保存于final.json。当前登记共66份，包括63份可用原件及三份已停止技术登记。最终复核检查准确源码、十项数据库列及类型、103项迁移、63份引用、三份版本缺失、图像处理输入和原有验收环境，均通过。

## QA31准确验证制品

| 文件 | SHA256 |
| --- | --- |
| files/models.py | 8dc7d79d72dc526d1a0d99a1b148bb0ad36e84d2c4e2b0259a0fc1d2ceb9516d |
| files/migrations/0003_s3storedobject.py | 71b14e7b1e872e8236941d3dacf39c05395cbde9f9ef10211fb688af7ea07c02 |
| files/s3_registered.py | e4727d908f9268b2c1414623228c251ecf8d5b165349c5941c0e4069abdb610d |
| files/s3-object-reference.schema.json | 3eaebe304021d8f4c6ed6a1f37adf74341a10d89949c9c952f16461268debb99 |
| files/s3-stored-object.schema.json | 9d7bbf50d4889e96954dfb2f0f2b91edbac879a289919a4c8e98967aa6a4e725 |
| tests/verify_s3_registered_storage.py | beae6d4f179344a17107cbfeab1744fc74a450ca1b6fc110cd45e0511ead15e8 |

读取锁、持久处置journal、凭证及相应准确验证制品继续由[QA32](QA32-s3-deletion-proof.md)维护。

## 审查与完整验收

本专项验证了实际存储登记、持久引用、Schema、事务、并发及状态拒绝。已有FileObject内容匹配分支、完整家庭和支持权限、实际删除前journal、未知结果恢复、全部原件迁移、应用readiness及生产恢复继续实施和验证。当前程序没有通过新增存储配置改变main运行，也没有把技术组件结果登记为完整业务通过。

原有63份原件、290个页面、249项事实和专业状态继续由原验收环境维护。未知Ark结果仍按既有2 CNY与两个并发位置保留；本专项没有新增模型请求。V12、V26、V33、V40、V56、V57和全部原始要求保持完整条件。
