# QA68 历史资料与计算提示

## 任务身份

task_id 为 QA68，状态来源为本文件，当前状态 ACCEPTED，范围为本文件列明的本地软件验收。需求来源为 2026-10-21 用户要求：模拟资料具有明确历史顺序，提示由输入计算产生，页面顶部保留模拟资料标记。相关任务为 QA65、QA66、QA67、T41、T50。文档有效性 CURRENT，last_verified 为 2026-10-21。代码基于 a720033，当前实现与运行配置由实际任务的 implementation_sha256、构建源码摘要及进程身份共同绑定。

## 检索与设计依据

已读取 QA65、T50、开发记录索引，以及 records.service、records.correction、archives.record_facts、assessment.qualified_facts、assessment.selection、alerting.inputs、alerting.service、alerting.lifecycle。医学时间通过现有 create_medical_time 解析；更正使用不可变版本。

仅在明确登记的本地模拟数据库、来源、患儿、执行账号和输入规则范围内接收模拟输入。模拟来源保存准确事实、字段、记录版本、时间及用途证明；专业核对记录保持其原有含义。动态计算、任务执行、组合判定、家庭发布及读取使用现有流程。顶部模拟标记保持，提示正文描述实际计算条件。

## 当前实现与决策

| 文件或入口 | 当前职责 | 使用方 |
|---|---|---|
| backend/assessment/simulation_inputs.py | SJIA_SIMULATION_ASSESSMENT 明确登记数据库、来源、患儿、执行账号和输入规则；只接受已批准 synthetic 来源的家庭观察分类值，核对字段、时间、版本及用途资格 | collect_dynamic_inputs |
| backend/assessment/qualified_facts.py、selection.py | QualifiedFact 明确区分模拟输入；专业核对路径保持其原有资格条件 | 动态输入选择 |
| backend/alerting/inputs.py、service.py | 同一动态引擎计算逐项信号及组合，保存任务快照、字段依赖、TaskAttempt、消费回执及家庭事件；专业核对输入表只保存专业证明 | Worker、家庭首页与提示详情 |
| backend/records/correction.py | 报告时间更正建立新 Report 与 RecordVersion，原始报告与字段版本继续保留 | 本地历史资料维护 |
| backend/api/views_family3.py、views_notifications.py、backend/notifications/native.py | 提示详情使用一致只读快照；人员及患儿使用 FOR NO KEY UPDATE，保持事务和授权保护，允许审计外键并发引用 | 提示读取、站内送达与审计 |
| guardian-h5/src/features/alerts/UI2AlertsPage.tsx | 保存实际阅读记录后执行站内送达确认，保留正文可见性和签名回执检查 | 家庭提示详情 |
| guardian-h5/scripts/check-history-alerts.mjs | 实际浏览器登录、切换患儿、刷新、报告读取和已读记录；等待条件具有明确期限 | QA68-05 |

34 条已有记录通过版本更正补充明确历史时刻，新增两条当前观察。当前 107 项医学时间均解析为 resolved，并采用 Asia/Shanghai。报告字段采样时间、报告出具时间与登记时间分别保存。六份编辑草稿继续保留，当前两份就医摘要已重新生成。

本次计算规则为皮疹观察提示：最近一次明确观察为 yes 时产生 attention，no 时判定 not_met。提示正文为“最近一次观察记录包含皮疹，请查看记录并补充后续观察。”本规则只产生观察提醒；MAS 分类、MS 评分及其临床阈值保持原有配置和专业资格要求，未纳入本次正向临床验收。模拟计算依据标记为 simulation_record，并完整保存于不可变任务快照及字段依赖中。

## 验收矩阵

| check_id | Given / When | Then | 状态 |
|---|---|---|---|
| QA68-01 | 两位测试患儿读取历史记录 | 明确观察时刻、采样时刻、出具时刻及 Asia/Shanghai，首页最新结果顺序确定 | PASS：history-final.json 的 107 项解析检查；browser-5.json |
| QA68-02 | 获准模拟输入执行动态任务 | 保存真实 TaskAttempt、消费回执及逐项输入；条件满足产生提示 | PASS：computed-final.json，实际 met、not_met、站内接收人和消费回执 |
| QA68-03 | 条件不满足及资料不足执行计算 | 分别保存 not_met、unknown；不足资料不产生正向提示 | PASS：computed-final.json；unknown-final-result.json，观察时点早于全部已有资料，结果 unknown，事件数量零，已有当前结果保持 |
| QA68-04 | 未登记来源、账号、患儿或真实环境使用模拟资格 | 明确拒绝；专业核对记录数量保持 | PASS：computed-final.json 的四种范围拒绝与零专业证明断言；final-audit-current.json 的其他患儿 HTTP 403 |
| QA68-05 | 家庭账号重新登录、刷新、阅读与站内送达 | 顶部保留模拟标记；正文及日期与当前计算一致；实际保存已读和送达回执 | PASS：browser-5.json 九项检查；browser-receipt.json 两项检查；concurrent-reads.json 三项并发读取全部 HTTP 200 |

## 执行环境与约束

数据仅写入 test_sjia_demo_20261020，API 为 8004，前端为 5178。真实资料数据库、8003 服务、未知 OCR 请求和费用预留保持。数据库、模拟数值、凭据和私有运行报告使用 Git 忽略目录 .qa-work。外部模型及临床准确性评价不属于本次验收。

## 验证与继续条件

实际验证报告保存于 .qa-work/QA68。历史时间检查记录为 history-final.json；计算、执行来源及范围检查为 computed-final.json；不足资料场景为 unknown-final-result.json；当前摘要为 summaries.json；报告草稿时间为 draft-times.json；档案、观察时段、趋势及提醒读取为 .qa-work/QA65/read-probe-historyFinal.json，十项 HTTP 检查通过。Django check 与浏览器脚本语法检查通过。

browser-5.json 覆盖当前构建的登录、两个患儿、首页确定时间顺序、报告采样至出具再至登记的顺序、历史读取、当前计算结果、刷新与已读记录。browser-receipt.json 独立验证实际页面可见之后的签名送达确认及 delivered 状态。concurrent-reads.json 在真实 locked_scope 持有送达行锁时同时读取两个提示详情和一个首页，全部返回 HTTP 200。全部页面错误数量为零，检查未使用截图。

构建报告为 .qa-work/QA65/build-notification.json 和 build-notificationProduction.json，均通过 TypeScript 与构建检查；普通发布构建没有本地账号凭据。final-audit-current.json 核实十张原始临床表的内容摘要、七个原始进程和两个未知 OCR 请求的 2 CNY 预留保持。8003、8004 均 ready，当前 5178 制品与构建摘要一致。当前两个模拟患儿共有 36 条记录、71 个版本、294 项字段、六份文书和批次、27 份摘要及 15 份草稿。

来源及权限校验没有使用替代模型响应，外部模型调用和新增专业核对证明数量均为零。当前验收范围已完成。真实手机、视觉评价、性能容量和临床准确性分别保持 NOT_RUN；本任务不提供这些范围的通过结论。
