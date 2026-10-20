# QA66 有效预警的首页读取

状态为 ACCEPTED，文档有效性 CURRENT，last_verified 为 2026-10-21。本记录为唯一状态来源，关联 QA65 与首页实际业务验收。

## 需求与依据

Given 独立模拟账号具有有效提示，When 首页通过 REPEATABLE READ READ ONLY 事务读取，Then 返回当前提示与获准记录，读取过程不修改业务内容。普通可写事务继续使用环境共享行锁。

已核实 records.snapshot、api.family_home、family_content.alert_detail、alerting.presets 与 family_content.gate_locks。发布核验在 REPEATABLE READ 或 SERIALIZABLE 只读事务中使用一致快照；普通可写事务保留环境共享行锁。证据位于 .qa-work/QA65 的实际 PostgreSQL 与 HTTP 检查记录。

## 验收

| check_id | 动作 | 预期 | 状态 |
| --- | --- | --- | --- |
| QA66-01 | 有效提示的只读快照读取 | 成功返回首页，保持业务内容 | PASS：home-snapshots-final.json |
| QA66-02 | 普通可写事务核验 | 保持环境共享行锁与授权检查 | PASS：home-snapshots-final.json |
| QA66-03 | 实际 HTTP 与浏览器 | 首页、详情及阅读操作保持一致 | PASS：browser-account-18.json；duplicate-reads-relations.json |

证据保存 .qa-work/QA65。两种事务模式均返回同一有效模拟提示及五项最近记录，SQL 观察未发现业务写入。浏览器在提示正文进入可见区域后取得真实阅读回执。模拟提示用于验证软件展示及授权，临床准确性评价保持 NOT_RUN。
