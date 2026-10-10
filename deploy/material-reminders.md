# 本人资料补充提醒运行要求

资料提醒使用本人保存的 MaterialReminder、独立 PersonalRecordTask 和 MaterialReminderNotice。到期仅产生本人资料消息。Clinical Record、Assessment、AlertEvaluation、AlertEvent 与评分均不会由资料提醒创建或修改。

## 运行配置与批准

- `backend-runtime-1` 需要当前环境的有效批准、来源、验证报告及制品证明，登记准确 API 限额、notification Task 规则、Provider 和保留政策。
- `notification-in-app-2` 引用 runtime_config_key、实际执行主体、环境密钥引用、通知保留政策以及可选 material_reminder_policy_id。实际主体需要有效 manage_notifications 岗位授权，以及相同用途的完整 read_case 权限。签名密钥仅由当前进程环境提供，至少32字节。
- `material-reminder-channel-2` 使用 notification 类别，引用 provider_config_key 与 runtime_config_key，purpose 固定 family_observation，channel 固定 in_app。notification_text、max_due_per_tick 和 evidence_refs 需要实际批准。证据 SourceRevision 必须允许 material_reminder_policy 用途；代码不生成批准文字或批准记录。
- Provider、runtime 和提醒政策通过稳定 config_key 指定归属，执行时绑定当前 runtime 中的准确 Provider UUID、版本及校验值。完成提醒政策登记后，Provider 的获准版本引用其准确 UUID；全部配置仍需正式验证及启用。
- 本人记录任务的保留政策 data_class 必须为 personal_record_task，具有有效批准主体、时间与证据，并在当前 runtime.retention_policy_ids 中登记。创建页面由本人明确选择准确政策。

## 持久保存与执行

本人创建、完成和取消记录任务使用 WriteIntent、WriteRequest 与 AuditEvent。完成或取消同事务处理关联提醒。设置、时区、安静时段及开关共享准确版本和保存回执；目标目录读取独立本人任务，不使用临床 Task 作为资料目标。

`notifications.material_scheduler.schedule_due` 登记于 Celery Beat，运行队列为 notification。每次处理数量受获准提醒政策与 API 页大小限制。仅领取 enabled、已到期且当前版本没有 notice 的设置；重新核查本人授权、原提交证明、目标状态、版本、保留政策、执行权限和当前安静时段。同一提醒版本、保存时刻与渠道具有唯一 deduplication_key，旧 notice 会阻止同一计划重复授权。

许可保存于 schema_version 为 material-reminder-notification-task-2 的 Task，具有准确输入摘要、短期签名和稳定 Provider request ID。原生发送在同一数据库事务保存实际正文、TaskAttempt 与签名 accepted 回执。accepted 表示站内消息已经保存。送达使用当前本人会话的短期签名票据；界面正文进入可见区域以后提交 delivered。本人主动打开通知后，实际送达与阅读分别保存。

通知失败保留原 Task 和 Provider request ID。再次执行重新核查原签名、期限、当前全部权限和目标、尝试次数与间隔；uncertain 状态要求查询原结果。已过期许可需要明确更正提醒设置。暂停、关闭、目标完结或撤权后不会开始新发送；尚未执行的消息可按本人停止权限取消，已受理消息的原事实保持。

Outbox 类型为 notification.material_requested；Dispatcher 和 Worker 已接入 notification 队列。原事件恢复使用：

```bash
python manage.py reconcile_material_notification --event-id ACTUAL_EVENT_UUID --actor-id ACTUAL_ACTOR_UUID --auth-epoch ACTUAL_EPOCH
```

命令只允许符合当前环境及病例权限的真实运行主体继续准确原事件，不创建替代消息。`reconcile_material_reminders --limit COUNT --dry-run` 可执行已有生命周期的只读扫描。

## 当前验收条件

真实环境 Worker 与 Beat 保持停止。sjia_dev 没有本人记录任务、提醒设置或提醒通知。真实发送需要本人设置、获准提醒文字与渠道、运行主体、完整病例范围以及明确发送授权。真实后台服务启用需要处理原有处置队列的执行许可。

2026-10-09 用户授权全部开发使用模拟数据。独立 sjia_simulation 已运行实际 HTTP 创建、回读、重复请求、到期调度、暂停取消、消息受理、签名回执、阅读和撤权检查，共 40 项通过。证据为 `.planning-work/t53-simulation.json`，仅支持模拟开发范围。真实业务、真实设备、文字放大、微信浏览器和视觉比对分别待验收。

## 独立模拟运行

在项目根目录使用 `backend/.venv/bin/python deploy/initialize_simulation.py`建立独立数据库、文件目录、会话密钥和消息命名空间。使用 `DJANGO_SETTINGS_MODULE=config.settings.simulation`运行 `manage.py seed_simulation`与 `manage.py runserver 127.0.0.1:8001 --noreload`。模拟家庭及支持账号分别为 simulation-family 和 simulation-support，密码由初始化程序随机生成，保存在已忽略且权限为 0600 的 `.simulation-work/environment.env`，不写入日志。

前端使用 `SJIA_API_TARGET=http://127.0.0.1:8001`运行 Vite，模拟开发端口为 5183。模拟资料保存于 `.simulation-work/object-store`，配置依据保存在 `.simulation-work/config-evidence`；Redis 使用独立数据库及 `sjia-simulation:`命名空间。模拟 Celery Beat 默认没有计划，不消费已有真实处置队列；到期检查由独立调度入口执行。模拟报告只登记实际运行的检查，不作为临床批准或真实验收证明。
