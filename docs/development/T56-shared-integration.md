# T56 主窗口共享实现

日期：2026-10-09。范围为明确模拟账号、模拟资料和模拟配置；数据库为 `sjia_simulation`、Schema `sjia`、端口 `5433`。完整任务状态由 [交付台账](../../planning/delivery-ledger.json)维护，原始验收责任见 [T56](T56.md)。全部39页、328项状态、原有内容、Token、资产和交互继续保留。

## 正式实现

| 文件 | 当前行为与验证 |
| --- | --- |
| `guardian-h5/src/api/client.ts` | 请求发送前检查 AbortSignal，组件同步取消的读取直接终止；真实 Chrome 检查取得 `AbortError`，指定私有请求次数为0 |
| `guardian-h5/src/api/familyReadSchema.ts` | HomeView 使用已有严格 `protocol.schema.json` 定义，完整验证正式首页的字段、能力和资料内容 |
| `guardian-h5/src/features/alerts/useNotificationDelivery.ts` | 已送达通知读取原来保存的证明；accepted通知保留签名消息与送达确认流程；已送达通知不重复确认 |
| `guardian-h5/src/features/support/NotificationPage.tsx` | 当前 effect 生命周期确认后读取准确通知记录，刷新继续读取原历史和指定接收人详情 |

TypeScript与生产构建已经实际通过，准确日志为 `.planning-work/coordination/root/notification-refresh-build.log`。使用 Node `24.13.0`、npm `11.6.2`、TypeScript `7.0.2`、Vite `8.3.1`；最新构建耗时 `1.75` 秒。构建结果不代表视觉验收通过。

## 实际业务检查

专用入口为 `guardian-h5/tests/devices/check-t56-notification-summary-ui.mjs`。它通过真实 Chrome `155.0.8059.39`、Playwright `1.63` 操作实际页面和接口，使用 `390×844`、`Asia/Shanghai`。没有截图或视觉比较。

家庭账号为 `simulation-family-first-alert`，患儿为 `dff4286d-b457-43b1-b53d-64bc40b224a2`。支持端使用 `simulation-support`，正式退出后重新登录家庭。资料查询读取实际家庭会话的 `csrf_token` 并发送 `X-CSRFToken`，严格核对 `principal_id` 和 `audience`。

通知对象为 AlertEvent `a69b91f5-f5db-470b-981a-5ec740305111`、recipient `145a74a9-c9d1-4d35-9386-8dfd9b0bbb0c`、唯一 Attempt `9bba761f-86cc-4a60-adbf-115f2cbf24a2`。家庭正文、来源、联系、本人阅读和刷新重读，支持端授权范围、两份回执及刷新读取均已实际执行。原通知继续保存，检查不再次发送通知。

摘要通过实际原请求恢复入口保存。原 request `efff8ba3-27a7-4f27-9cd7-eff7cac058b9`、原 intent `07a53ff1-52e5-4a68-a8a0-1d372e19e8fc` 保持，保存 Summary `f93cca98-07ad-440c-a5ea-16169794d8b8`、Task `4b828b99-3732-4af9-8c66-d23447a3bae2`。Task为completed，30项来源、日期、资料类别、未知时间选项及刷新后的内容逐项核对通过。

实际页面报告为 [通知与摘要页面报告](../../.planning-work/parallel/window2/evidence/t56-notification-summary-ui-2026-10-09T113159-389Z.json)，四项PASS，page_errors为空，SHA-256为 `4725a8ff85ae9e7d1dccce7d283ffddb71e507d7a154ff80d4e031de1928b171`。准确命令为 `TMPDIR="$PWD/../.planning-work/tmp" node tests/devices/check-t56-notification-summary-ui.mjs`，工作目录guardian-h5，退出码0，日志为 `.planning-work/coordination/root/t56-ui-original-summary-recovery-run.log`。

数据库交叉读取程序为 `deploy/read_t56_ui_saved_results.py`，执行 `REPEATABLE READ, READ ONLY`，核对数据库身份、摘要保存请求、Task、逐项 ordinal／field_id／record_version_id、通知唯一 Attempt、准确 accepted／delivered 回执及其 SHA-256、本人阅读时间。报告保存后重新读取比较。

实际命令为 `TMPDIR="$PWD/.planning-work/tmp" PYTHONPATH="$PWD/.data-work/deps:$PWD/backend" backend/.venv/bin/python deploy/read_t56_ui_saved_results.py --ui-report .planning-work/parallel/window2/evidence/t56-notification-summary-ui-2026-10-09T113159-389Z.json`，退出码0。[PostgreSQL报告](../../.planning-work/coordination/root/t56-ui-database-readback.json)为PASS、saved_and_read为true，SHA-256为 `4035815a4944ded62853ca4a65fc222b4b656c699c58989770d842da541747ea`。Python `3.12.7`、Django `5.2.17`、PostgreSQL `18.4`。完整临床及其他未执行验收继续保留。

## 验收范围

临床、真实家庭、实际手机、微信、生产环境及全部39页视觉保持 `NOT_RUN`。这些开发和模拟检查不替代 V56，也不改变其他任务的原始验收责任。共享 API、Schema、Backend 与发布资格本次保持当前版本；没有启动共享 Worker／Beat，没有消费整个共享队列。
