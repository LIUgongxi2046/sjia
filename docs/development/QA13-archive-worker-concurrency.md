# QA13 档案任务并发与容量

当前状态为 ACCEPTED，范围为真实 PostgreSQL 并发约束、实际 PDF 容量计算和未知费用预留。关联 QA12、FR-13—FR-17 以及后端 LLD 2.4 的锁顺序要求。last_verified 为 2026-10-18。模型字段质量及外部错误回执验收由 QA12 维护。

普通业务使用环境共享锁；环境资格变更继续使用排他锁。档案写入按照环境、患儿、身份与授权、业务对象的顺序取得锁。输入副本及模型回执采用相同的环境读取方式。模型额度预留继续由运行配置行锁串行保护，配置版本与证明在环境共享锁保护下读取。

同一原件的多页容量按唯一 FileObject 累计，页数逐页累计。批次预算与提供方账号预算共用实际费用计算；已发出而计量未知的请求继续预留原金额，保留未知状态，额度允许的其他请求可以继续处理。

| 检查 | 真实输入与结果 | 证据 |
| --- | --- | --- |
| 不同来源分组并发 | 250ms 锁期限下，批次身份与用途授权事务同时完成 | `.qa-work/QA12/reports/gate-concurrency.json` |
| 相同患儿写入顺序 | 实际 PostgreSQL 行锁继续串行保护 | 同上 |
| 配置资格变更 | 排他锁等待正在执行的环境共享事务 | 同上 |
| 多页 PDF 容量 | 428254903 bytes 的实际 PDF 选择两页，按一份原件计算，通过 600 MiB 范围检查 | `.qa-work/QA12/reports/pdf-pages.json` |
| 未知费用 | 实际 uncertain OCR 任务继续预留 1 CNY，actual_cost 保持 null | `.qa-work/QA12/reports/unknown-budget.json` |

测试入口为 `.qa-work/QA12/check_gate_concurrency.py` 与 `check_archive_limits.py`，没有调用模型或创建临床事实。实际副本与模型执行由 QA12 记录。当前 5176 与 5177 服务均已更新配置证明、重新启动并通过 ready、admitted_current 检查；Django check 通过。
