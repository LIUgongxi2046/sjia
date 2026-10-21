# S3处置对象、任务与凭证接入

本规范承接FR-09、NFR-07、NFR-09、现有用途处置协议及S3版本登记与证明规范。业务批准继续核查实际操作者、当前权限、准确保留政策、来源与任务版本，物理删除目标同时绑定提供方实际版本。

## 对象身份传播

| 来源 | 严格结构 | 持久或执行消费方 |
| --- | --- | --- |
| FileObject | file_id、object_version、object_key、checksum、size_bytes、retention_policy_id | 本地存储使用PurposeDispositionObjectProof |
| S3StoredObject | 完整S3ObjectReference，包含binding、VersionId、ETag、SHA256、大小及加密方式 | S3存储使用PurposeS3DispositionObjectProof，storage_reference为必需字段 |
| 已选择存储及FileObject | disposition_object_proof | PurposeDispositionApprovalContext.file_versions与PurposeDispositionTaskRefs.file_identity |
| 当前批准及任务 | 完整对象身份与批准摘要 | lock_request_access、task_refs、validate_refs、execute_local_item |
| 准确持久证明 | 实际journal及receipt | finish_request、mark_item_deleted与record_disposition_failure |

S3证明要求业务对象键、完整摘要与大小分别等于storage_reference。采用已登记准确版本；任务不能通过只提供业务摘要选择其他提供方版本。原本地结构保持原有字段，S3结构明确增加必需引用。联合类型优先识别完整S3结构，批准与任务序列化持续保存该引用。正式Schema为files/disposition-object-proof.schema.json。

批准对象发生存储身份变化时，原批准不能直接用于当前S3执行。任务执行前重新核验当前存储、准确版本、原批准、权限和业务字段。迁移期间的旧任务必须按原有冻结与协调流程处理，不能修改已保存任务输入继续执行。

## 任务与事件

| 任务Schema | 事件 | 队列 | 实际存储 |
| --- | --- | --- | --- |
| local-delete-2 | disposition.local_requested | purpose | 明确配置的LocalFileStore |
| s3-delete-1 | disposition.s3_requested | purpose | 明确配置的S3RegisteredStore |

DISPOSITION_EVENTS为Schema与事件的唯一映射。提交、执行、失败协调、超期协调与准确事件重新读取共同使用该映射。Worker事件登记、dispatcher及consumer接收两种明确事件，输入不包含病例正文或提供方密钥。

完成的S3处置核验实际提供方结果和当前缺失查询，usage_evidence保留准确凭证引用及摘要。S3失败可能发生在外部操作期间，provider_call_state记录unknown；本地流程保留provider_called=false。完成与失败状态均不生成提供方替代响应。

## 控制证明与存储分支

files/disposition_storage.py分别调用本地证明或S3准确版本证明。存储类别与已批准证明不匹配时立即抛出错误。S3的journal和receipt通过独立控制目录读取，禁止调用S3负载的exists查询证明文件。

准备证明或凭证已经存在时，失败任务进入awaiting_resolution；原TaskAttempt保存uncertain。准备证明不会随数据库事务终止消失，原件读取停止条件持续生效。控制目录不可核验或准确身份冲突时错误直接传播，不把无法核验解释为空目录。

现有操作名称execute_local_item和submit_local_disposition继续作为已发布调用入口，实际存储分支由完整证明与明确存储类型决定。提供方外部处理副本继续沿用其独立协议，当前S3接入不代替外部处理方的处置证明。

## 验证与整体集成

实际验证使用已获准原件和准确FileObject元数据，在独立数据库核验业务字段、物理引用、联合类型、真实读写、证明与拒绝条件。复制来源身份、原值与批准时间时保持其准确内容，不复制登录凭据，不生成临床事实或专业确认。技术副本的实际字节来源、创建时间、处置身份和提供方结果分别保存。

存储factory、内容清除manifest、恢复映射、控制目录备份、运行准入与部署继续完整接入。组件、Schema或语法检查分别表达各自范围；真实完整处置申请、批准、队列和恢复需要对应实际运行证据。
