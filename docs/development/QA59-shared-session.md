# QA59 共享会话与跨窗口资料清理

## 任务身份

| 字段 | 内容 |
| --- | --- |
| task_id | QA59 |
| 需求与验收 | PRD 8.18.6；AC-H5-05；前端 LLD 2.3；关联 T08、T09、T54 |
| task_source | 本记录管理此次修复；完整 V08、V09、V54 继续采用 planning/delivery-ledger.json |
| 状态 | ACCEPTED；范围为当前Chrome的共享会话软件专项；7项真实检查、当前构建及5项设计检查通过 |
| 代码与环境 | codex/current-design-reception；77e6cea4e997fc53371dcbdfe345ffbec354f4f5 之后的当前修改；独立前端 5178、现有真实 API 8003 |
| 文档有效性 | CURRENT；2026-10-20；依据当前源码和实际双窗口复现 |
| 检索字段 | AccessProvider；SupportApp；SessionBoundary；fetchJson；Web Locks；storage |

## 检索与输入依据

读取 T08、T09、T54、前端 LLD 2.3、v2-interaction.md；检查家庭与支持的真实会话读取、登录、退出和原件读取代码。实际复现使用已授权本地账号和五个资料分组，不创建临床输入，不修改权限，不增加模型请求。

## 当前实现与约束

家庭与支持的登录、注册、退出及患儿选择使用同源独占 Web Lock 处理 Cookie 操作。Session 与 CSRF 读取取得共享锁，等待正在执行的账号操作完成。正在操作时其他窗口清除全部页面组件及受限内存；操作结束后重新读取服务端身份和访问范围。fetchJson、图片、文书和通用语音读取保存发出时的会话同步版本，读取正文完成后再次检查；版本变化时拒绝采用结果。VoiceEntry 继续使用实际 AccessProvider 的当前版本检查及取消读取。

localStorage 仅保存固定键 sjia.session.boundary.v1，其字段为随机 version、随机 owner 和 changing/settled 状态，不包含账号、患儿、资料、凭据、请求正文或 Cookie。记录不提供访问授权。关闭操作窗口后，通过取得已释放的 Web Lock 和实际 Session 请求恢复；401 AUTH_REQUIRED 表示实际匿名会话，其他错误明确显示并允许重新核验。

| 文件 | identifier | 职责 |
| --- | --- | --- |
| guardian-h5/src/api/sessionBoundary.ts | runSessionMutation；assertSessionEpoch | 浏览器互斥、非敏感同步版本、异步结果检查 |
| guardian-h5/src/state/SessionBoundary.tsx | SessionBoundary | 其他窗口清除、重新挂载、操作中断后的实际核验 |
| guardian-h5/src/state/access.tsx | AccessProvider | 当前版本绑定、实际身份及患儿选择 |
| guardian-h5/src/features/support/SupportApp.tsx | SupportLogin；signOut | 支持受众共用会话操作与清理 |
| guardian-h5/src/api/client.ts | fetchJson；fetchPrivateDocument；postAudio | 全部传输结果采用前检查版本 |

## 验证与证据

双窗口复现原始证据位于私有 .qa-work/QA59/shared-session-observed.json，程序 .qa-work/QA59/observe_shared_session.mjs；结果 FAIL，真实服务端退出成功且 Session 返回 401，另一个持续可见窗口 15 秒后仍有文书和患儿上下文。文书与账号标识保存在私有目录。

2026-10-20 08:00—08:01 北京时间，Chrome 155.0.8059.85 实际执行 guardian-h5/scripts/check-real-shared-session.mjs，结果 PASS，7项检查、0个页面错误。实际制品 index.html SHA256 为 dd6ea972b30e2e6ebc3c090615288436dd1ff019bd3e944e1d3741e24dcd3964；源码摘要及 TypeScript、生产构建记录位于私有 .qa-work/QA59/build-4.json。原始业务报告为 .qa-work/QA59/shared-session-green-4.json。

| 场景 | 真实结果 |
| --- | --- |
| 两个独立且可见的窗口共享实际账号，一个窗口退出 | 服务端退出200、Session401，另一个窗口约572ms内清除文书和患儿上下文 |
| 实际重新登录 | 其他窗口直接重新取得当前服务会话，无需手动刷新 |
| 通过患儿管理选择其他已授权资料分组 | 服务端保存准确选择，其他窗口清除原分组文书并采用实际新范围 |
| 家庭与支持受众切换 | 实际支持登录200，家庭窗口清除临床内容 |
| 支持端退出 | 权限不足时仍有退出入口，实际Session401同步至家庭窗口 |
| 关闭正在处理退出的窗口 | 原生Web Lock释放后，使用实际服务Session重新核验并恢复准确资料 |
| 页面刷新及浏览器存储 | 刷新读取真实会话，localStorage仅有随机同步元数据，sessionStorage为空 |

执行条件通过参数指定 report、dist、credentials、scope、source；凭据与资料分组清单保存在获准私有目录，不能加入Git。测试使用真实HTTP、Cookie、原生双窗口、Web Locks及CDP网络延迟，不建立替代服务或临床输入。截图和真实手机验收未执行。

只读审计 .qa-work/QA59/state-observed.json 通过：10张临床表、63份原件元数据、5个分组的47条既有授权、原任务数量、两笔未知OCR及2 CNY预留保持。原有8002、8003、5176、5177均正常；独立5178测试预览已经正常停止。没有新增模型请求和临床写入。

系统、数据、接口、Agent及前端的当前检查由 .qa-work/QA59/run_rechecks.py 逐项执行，实际报告保存 t34—t38-preflight.json；前端当前正文由 capture_final_frontend.py 核验。正式证据通过交付记录 EVID-T34—T38 定位，继续维护原30项承诺、60项任务和55项未完成验收。软件专项证据由 CHECK-QA59-SHARED-SESSION 保存，范围限定当前Chrome、账号及获准资料分组。

## 关联影响与继续条件

T08、T09、T54和T38的会话与传输说明已经同步。完整业务、岗位授权、真实设备及临床核对继续保留原验收要求。原有服务切换、数据库迁移及 main 集成继续等待现有批准。
