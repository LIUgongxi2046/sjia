# QA21 真实观察范围保存与历史版本

当前专项状态为ACCEPTED，文档CURRENT，last_verified为2026-10-19。需求为FR-19、GRAC-07，关联T47、QA20、QA19及QA14；完整V47继续由planning/delivery-ledger.json维护。实际接口提供observation_period_change，当前账号依据原有权限保存本人观察范围。本专项使用实际北京时间2026-10-19选择软件验收查询范围，标签与原因明确说明软件测试用途；这些日期不作为任何临床事实的医学时间。

检索依据为T47、QA20、observation-period.schema.json、records.periods、requests.observation_periods及UI2ObservationPeriodPage。当前32项真实检验事实的原值、未知医学时间、实际可得时间与未专业核对状态保持。用户已委托软件开发与批准，专业资格和临床答案继续依据真实证据。

Given 当前可访问真实资料及本人实际查询范围，When 页面保存、刷新、原请求读取、更新范围说明并读取历史版本，Then 日期、本人标签、原正文、准确版本与回执保持，重复提交没有重复效果，当前资料仍保留全部未知医学时间。Given 保存后的资料版本变化，When 读取已有新增内容整理结果，Then 停用旧结果并保留原项目和原回执。

保存操作要求准确、唯一且有效的observation类保留策略与本人写入权限同时具备。读取接口的allowed_actions使用相同条件；没有策略时保留获准资料读取，明确返回保存限制，保存准备继续立即拒绝。当前本地策略沿用已有retain规则，没有启用删除、扩大字段权限或增加专业资格。完整要求见 [观察资料运行设计](../../backend-design/observation-period-runtime.md)。

| 位置 | 当前职责 |
| --- | --- |
| backend/records/periods.py；period_retention_policies、period_retention、period_view | 准确保留策略、当前保存条件、版本及实际资料读取 |
| backend/requests/observation_periods.py | 原正文、准确意图、回执、更正后重放和版本冲突 |
| guardian-h5/src/features/records/UI2ObservationPeriodPage.tsx | 当前范围、历史范围、独立原请求、刷新与具体保存限制 |
| backend-design/observation-period-runtime.md | 可见字段、保存策略、权限、运行及迁移规范 |

| 实际检查 | 结果 | 私有证据 |
| --- | --- | --- |
| 当前保留策略及准确引用 | 3项通过，沿用既有本地保留规则，原OCR限制保持 | configuration.json |
| 缺少策略与完整配置的操作条件 | 3项通过；缺少策略时32项采样可读，保存操作隐藏，实际保存检查返回503 | save-policy.json |
| 保存前有效整理结果 | 2项检查、6次实际接口响应通过，252项来源内容保持 | baseline-delta.json |
| 实际页面保存、连续点击、刷新、历史、原回执及冲突 | 7项Chrome检查通过，保存两份版本，页面运行错误为0 | browser.json |
| 数据库版本、原正文、四个时间、审计及依赖 | 4项通过，没有重复保存或替代临床医学时间 | persistence.json |
| 历史地址、独立原请求及读取当前范围 | 3项Chrome检查通过，整个读取流程没有写入，页面运行错误为0 | history-browser.json |
| 保存后的当前有效整理结果 | 2项检查、6次实际接口响应通过，252项内容保持 | current-delta.json |
| 当前整理页面、共享中文显示及来源 | 6项Chrome检查通过，83项事实保持，页面运行错误为0 | delta-browser.json |

30项专项及受影响检查通过。当前范围保存为同一period_id的两份ObservationPeriodVersion，第一版本保留原标签，第二版本保留更新说明。两份WriteIntent、两份WriteRequest及对应审计逐项一致；原请求重放返回原回执，实际旧版本意图返回VERSION_CONFLICT，没有增加版本。历史页面明确已有新版本并禁用保存，原请求刷新保持准确历史正文，实际操作能够重新读取当前范围。

四个MedicalTime只作为两份查询范围的边界，未被任何RecordField或ArchiveFactVersion采用。32项检验的原值、原单位、未知医学时间、实际可得时间及unverified状态保持；临床记录仍为3份RecordVersion、249项RecordField、83项ArchiveFact及249项ArchiveFactVersion。两次保存更新实际资料版本，旧范围依赖失效并保存准确来源事件，旧整理结果停用且历史252项保留。本专项测量时六个实际整理结果共保存1512项，其中五个失效、一个有效；当前九个结果及2268项历史由[QA27](QA27-real-clinical-access-scope.md)维护，实际观察接口的范围版本2及32项采样的专项复验见QA24。当前有效结果继续为252项，没有比较基准或临床变化结论。

TypeScript、生产构建和Django系统检查通过。313份真实环境配置及26份原验收配置按原批准范围通过当前代码检查，8002与8003均为ready、admitted_current。测量采用main基准commit 8ca48b9abb6395284a2c2ee360fbc6545d7ae763及当时工作区内容；实际后端制品校验值为15a7a08f54e5565a9163e0fc36147762a01b43747d34e9eeac1d62e227ef7e86。终态、代码身份及全部证据校验值保存在忽略目录.qa-work/QA21/reports/final.json，真实正文、凭据及私有证据不进入Git。

提醒的两个实际签名回执与终态通过复验；两项uncertain OCR请求继续占用2 CNY预留及并发容量，实际费用未知，检查前后OCR任务均为3项，没有新增模型调用、临床事实或评估。完整页面识别、真实高频与稀疏采样、有效专业核对、独立参考答案、实际后台恢复、手机、微信和视觉验收继续待验证。当前本人软件查询范围不证明患者在该日期发生过任何临床事件。
