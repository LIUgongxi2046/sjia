# S3版本登记数据规范

本规范承接HLD的C09、FR-02、FR-09及NFR-07／09，维护原件及派生文件的物理版本登记。业务FileObject继续维护患儿、来源、用途、保留政策及业务版本；S3StoredObject维护准确存储地址和提供方版本。当前实现位于codex/s3-private-storage独立分支，应用整体采用仍需完整集成验证。

## 来源、权限与使用

| 对象 | 准确来源 | 用途与校验 | 保存位置 |
| --- | --- | --- | --- |
| 原始字节 | 用户已复核并授权的63份本地原件 | 完整SHA256、大小及正式SourceManifest | 用户原件目录与独立本地私有S3；原件目录保持 |
| 物理版本 | S3实际VersionId、ETag及准确版本读取 | HTTPS、owner、ACL、准确策略、AES256与完整字节校验 | S3StoredObject及实际提供方 |
| 版本样本 | 已有准确对象引用逐项重新核验 | 不推断患者数量、专业确认或独立临床答案 | 忽略目录中的adopted.json及实际验证报告 |
| 处置状态 | 准确版本删除响应及后续缺失查询 | 登记状态和提供方实际结果分别表达 | 当前组件结果；完整业务journal继续接入 |

数据使用承接本会话已确认的本地软件验证授权。数据内容不进入公开Git仓库，不创建新的医学原值或专业确认。PostgreSQL继续保存关系与技术元数据，S3保存准确字节；现有来源、关键词查询及依赖关系继续使用原规范。

## 字段与约束

表为sjia.s3_stored_object，ORM为files.S3StoredObject，字段在files/migrations/0003_s3storedobject.py维护。新增表不更改已有FileObject列及object_version语义。

| 字段 | PostgreSQL类型 | 空值 | 来源及语义 |
| --- | --- | --- | --- |
| id | uuid，主键 | 禁止 | 登记时生成的技术身份，重复采用保留 |
| binding_sha256 | sjia.sha256_hex | 禁止 | provider_id、HTTPS地址、region、Bucket、prefix、owner构成的物理地址身份 |
| object_key | text | 禁止 | 业务逻辑对象键，使用现有生产者的稳定键 |
| version_id | text | 禁止 | 实际提供方版本；拒绝空字符串与null版本标识 |
| etag | text | 禁止 | 实际提供方返回值，作为条件读取和删除条件 |
| checksum | sjia.sha256_hex | 禁止 | 实际完整字节的SHA256 |
| size_bytes | bigint，非负 | 禁止 | 实际完整字节数 |
| status | varchar(16) | 禁止 | available表示已登记且未停止使用；disposed表示禁止继续使用 |
| created_at | timestamptz | 禁止 | 软件登记时间，不表达医学发生时间 |
| disposed_at | timestamptz | 允许 | 登记停止使用时间，disposed状态必须具有准确值 |

binding_sha256与object_key具有联合唯一约束。对象键拒绝绝对路径、空字符串、重复分隔符、末尾分隔符和独立的句点路径段。available状态要求disposed_at为空；disposed状态要求时间不早于created_at。

数据库触发器保护登记身份、完整摘要、实际版本及创建时间，拒绝直接删除和重新启用已处置登记。元数据保留与经批准的清理政策仍需在完整应用处置流程中核验；当前登记状态不能独立证明物理介质、备份或全部副本清除。

## 字段传播与读取

| 生产者 | 持久结果 | 使用方 | 实际检查 |
| --- | --- | --- | --- |
| S3VersionedStore.put_immutable | 真实S3ObjectReference | S3RegisteredStore.put_immutable | 输入完整字节、准确版本及登记保存 |
| 显式迁移或接收操作 | 经完整重读的准确S3引用 | adopt_reference | 单一版本、当前配置、完整SHA256与幂等登记 |
| S3StoredObject | 精确版本及状态 | reference_for、get、get_range、exists | binding、对象键、登记状态、控制目录及已有FileObject内容一致性 |
| S3StoredObject | S3StoredObjectRecord | registration_for、核验及后续处置接口 | UUID、版本、时间、状态及正式Schema |
| 真实版本删除 | S3DeleteResult与实际缺失查询 | 后续处置和恢复适配 | 准确版本范围、未知外部结果与证明读取继续集成 |

内部结构使用files/s3-object-reference.schema.json与files/s3-stored-object.schema.json，运行时使用对应Pydantic模型。规范对象键由同一个canonical_key函数校验。状态和时间顺序同时由数据库及运行模型核验。登记元数据不直接进入家庭页面，家庭授权仍由现有病例、字段、原件与用途接口执行。

## 事务、并发与连续性

写入使用PostgreSQL事务和按binding、对象键取得的事务级advisory锁，再执行提供方的条件写入及登记。两个并发相同请求取得相同登记和准确版本。不同字节、额外提供方版本或已有停止状态立即拒绝。

提供方成功后，调用者事务终止会保留完整外部对象。明确重试使用原键和完整输入重新核验既有版本，重新建立登记，准确VersionId保持；系统不删除可能已经被其他操作引用的内容。未知响应继续依赖实际版本核验，不把异常改写为成功。

读取要求登记存在、状态允许且控制目录可核验。控制目录具有独立身份和所有者权限，目录身份变化或访问失败停止读取。后续完整处置将在提供方操作前持久保存准确journal，读取将使用相同对象身份停止访问。

FileObject业务内容和物理版本的匹配分支需要使用完整业务数据继续验证。当前独立数据库没有临床记录，本专项验证版本登记、实际存储和技术事务行为；应用factory、权限、全量迁移、处置和恢复继续保留原始验收条件。

## 迁移与验证

正式迁移在全新独立PostgreSQL17.11使用非超级用户执行，103项迁移、重复应用、模型状态、联合唯一约束、状态约束与身份触发器均通过。当前数据库sjia_qa31_storage只保存本次技术登记及框架元数据。

63个准确真实版本完成登记、重新读取、正式Schema校验及幂等采用；原有登记UUID、时间及版本保持。两个实际进程同时写入同一技术验证键，只产生一份登记和一个提供方版本。事务终止后的明确重试保留同一准确外部版本。实际删除的技术副本形成停止状态，后续读取、重新写入和数据库重新启用均被拒绝。12项拒绝检查及完整原件校验通过，准确结果由本地registered-storage.json保存。

实际来源、授权、完整字节、提供方版本及时间分别保存。该结果没有替代完整业务访问、内容处置、生产恢复、容量或临床验收。
