# 原生站内通知

正式配置使用 `provider / notification-in-app-2`。固定 endpoint 为 `urn:sjia:notification:in-app:2`，channel 为 `in_app`。运行配置的 enabled notification 条目准确引用 Provider 配置，并使用相同 endpoint、secret_ref、clinical_policy 依据；submit_retries 为零。

Provider 正文完整填写 environment、purpose、runtime_config_key、executor_id、signing_secret_ref、permit_seconds、delivery_ticket_seconds、max_daily_messages、max_recipients_per_event、timezone、retention_policy_id、evidence_refs。runtime_config_key 对应 backend-runtime-1 的 config_key，当前版本由环境 runtime_config_id 选择并保存准确证明。配置、批准、验证报告、当前代码摘要及 clinical_policy 依据都须通过现有配置资格检查。

签名秘密仅通过 `env:` 引用从进程环境取得，至少32字节，不提供默认值。API 与 notification Worker 使用同一获准秘密引用。需要查询历史签名证明时，保留对应配置引用的秘密；不能将新秘密替换到历史引用并宣称原证明有效。Task、TaskAttempt、NotificationAttempt、NotificationReceipt 和审计使用批准的实际保留策略，备份按 T59 要求处理。

executor_id 对应实际 active Principal，具有当前环境 manage_notifications 岗位、准确用途下的全部病例 read_case 范围。每名接收人具有当前 active Relation 及相同用途的 receive_alert 授权。当前临床评估、字段依赖、发布正文、联系与通知政策在取得许可和开始发送时重新检查。配置缺失、当前事件变化、过期、撤权或预算不足时明确拒绝；不会产生受理和送达回执。

通知消息投递到 `notification` queue，事件类型为 `alert.event_created` 和 `notification.native_requested`。Worker 消费前重读数据库，重复事件使用 ConsumerReceipt；原生受理和准确消息保存处于同一事务。运行已有 Dispatcher 和订阅 notification queue 的 Worker，需要项目对应环境的执行授权。当前本地 Worker／Beat 保持停止，未执行实际发送。

业务资料不足的事件保留在 Outbox。补齐批准条件以后，可以使用 `reconcile_native_notification` 管理命令处理准确事件，参数为实际 `--event-id`、`--actor-id`、`--auth-epoch`。命令重新核验操作者、用途、当前岗位和病例范围，处理同一事件并保存消费回执；重复处理不再次发送。命令不会创建账号、关系、批准或临床结果。

Provider 受理表示批准正文与发送证明已经持久保存。家庭提示详情从本人获准记录读取站内正文，正文与当前显示版本一致、实际浏览器可见以后，提交短期签名票据。后端验证会话、本人身份、关系、当前发布与票据，再保存 delivered 回执。read_at 由本人实际打开提示单独保存。历史回执保留原事实，当前正文失效时禁止重新采用。

SMS、WeChat 和 Web Push 需要准确供应商协议与批准配置，当前外部 callback 不接受客户端声明的送达状态。原生站内通知没有外部 callback 入口。
