# 模拟构建制品运行与检查

用户授权范围为明确 SIMULATION 的本地开发与实际检查。共享数据库为 `sjia_simulation`，保留现有资料；密钥仅加载 `.simulation-work/environment.env`，权限0600。开发服务5183与构建预览5184连接同一个8001模拟API，5184没有独立数据库或消息消费服务。

## 已有服务与操作归属

主窗口负责8001 API、共享5433 PostgreSQL、6379 Redis及5183开发服务。其他窗口只操作自己的隔离资源。需要更新共享后端时，把准确文件、用途、对象和命令交给主窗口；不能自行重启服务或消费全部队列。

当前5184预览由主窗口工具会话89887维护。正常读取地址为 `http://127.0.0.1:5184`。停止该预览仅结束其准确工具会话，不终止其他Node/Python进程。重新启动前核对端口和当前构建；没有地址或目标变化时不重复启动。

## 构建与预览

在 `guardian-h5` 执行已授权构建：

```bash
TMPDIR="$PWD/../.planning-work/tmp" npm run build
```

在5184没有现有预览时，主窗口执行：

```bash
SJIA_API_TARGET=http://127.0.0.1:8001 TMPDIR="$PWD/../.planning-work/tmp" npm run preview -- --host 127.0.0.1 --port 5184 --strictPort
```

预览供本地模拟构建使用，正式HTTPS、生产服务器和持续运行验收保持NOT_RUN。

## 实际业务检查

在项目根目录执行：

```bash
TMPDIR="$PWD/.planning-work/tmp" backend/.venv/bin/python deploy/check_simulation_delivery.py
```

检查执行真实HTTP登录与CSRF、关联、三类字段配置、已保存治疗记录和确认、模拟草稿保存和重新读取、放弃、摘要列表及退出。它同时读取准确数据库、迁移、runtime资格和构建资产，保存报告后再次读取比较。错误在断言处终止，不产生PASS报告。

检查会新建明确SIMULATION的资料草稿，重读后按当前身份放弃，保留实际记录。没有模型调用或外部发送，不修改原件或 `sjia_dev`，不消费队列。既有治疗记录由 T14 共享确认检查真实生成；资料不可读时检查终止，不能生成替代结果。

报告写入 `.planning-work/coordination/root/t33-delivery.json`，准确版本、资产摘要、配置清单和39页入口范围均保留。39页SPA入口检查不证明视觉或逐页业务完成。真实设备、微信、临床与视觉保持NOT_RUN。

当前前端构建已经实际运行上述检查，退出码0，11项检查与39个入口通过；报告SHA-256为`81bb5e4ed11c8b0d0d9eafd0cb9819e7aa19e171eee35325724e8b049356932c`，日志为`.planning-work/coordination/root/t33-current-build-delivery.log`。共享Worker与Beat均未启动，准确单项任务执行证明分别保存在任务主记录中。

## 数据和任务续用

当前首次提示模拟家庭账号为`simulation-family-first-alert`，支持账号为`simulation-support`；密码读取`.simulation-work/environment.env`的`SJIA_SIMULATION_PASSWORD`，不得写入报告。准确患儿、来源和核对身份保存在`.simulation-work/t29-first-alert-business-fixture.json`。当前分类、MS、动态评估、有效提示、唯一通知送达阅读和30项Summary已实际保存并重新读取，完整对象范围见T29与T56主记录。

当前正向API只读检查已通过；复查时使用准确清单，不重新提交任务或通知：

```bash
TMPDIR="$PWD/.planning-work/tmp" PYTHONPATH="$PWD/.data-work/deps:$PWD/backend" backend/.venv/bin/python deploy/run_t60_simulation_demo.py --positive-check --positive-manifest .planning-work/parallel/window1/t60-first-alert-positive-manifest.json --fixture-file .simulation-work/t29-first-alert-business-fixture.json --positive-output .planning-work/parallel/window1/t60-first-alert-positive-verification.json
```

当前来源索引与交付清单记录各报告自己的准确版本。已保存数据持续位于PostgreSQL、模拟对象目录及实际请求记录中；刷新页面或重新登录能够读取。独立模型请求结果unknown时保留原费用预留和并发占用，查询原请求；生产、真实临床、设备、微信和39页视觉验收保持NOT_RUN。

当前家庭通知、支持端回执及摘要页面实际四项检查通过，完整报告为`.planning-work/parallel/window2/evidence/t56-notification-summary-ui-2026-10-09T113159-389Z.json`；数据库交叉读取报告为`.planning-work/coordination/root/t56-ui-database-readback.json`。摘要页面恢复原请求`efff8ba3-27a7-4f27-9cd7-eff7cac058b9`，保存Summary `f93cca98-07ad-440c-a5ea-16169794d8b8`，Task `4b828b99-3732-4af9-8c66-d23447a3bae2`为completed。30项字段、顺序和来源版本、唯一通知Attempt、准确两份回执及本人阅读时间经实际PostgreSQL核对并保存重读；前述正向清单中的Summary仍保留原有对象身份。

已有初始化入口为 `deploy/initialize_simulation.py`，当前环境已初始化，交付检查不调用该入口或执行迁移。需要首次初始化时由主窗口明确目标后运行，现有共享环境继续使用原数据、对象、依据与秘密引用。

用途副本、通知和资料提醒只运行准确单项任务或事件。原请求、代次、来源版本、签名和回执继续保存；未知提供方结果查询原请求，不能自动重新发送。恢复与性能使用 T57/T31 专用程序，完整命令和资源身份由对应主记录维护。
