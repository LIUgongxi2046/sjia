# QA20 真实观察时段与未知采样时间

当前专项状态为ACCEPTED，文档CURRENT，last_verified为2026-10-19。需求为FR-19、GRAC-07，关联T16、T47、QA12、QA14。验收范围为已获准真实来源分组中的32项十进制事实读取、未知医学时间及准确来源，以及新增内容整理的受影响验证。用户已委托本次软件开发与批准；完整V47由planning/delivery-ledger.json及T47维护，继续保持NOT_RUN。

本人观察资料明确引用当前环境的observation_period_visibility_config_id。配置要求visibility-1、family_observation及空clinical_fields，继续逐项核验原始字段和原件权限；家庭提示与就医摘要执行各自发布资格。范围选择、专业核对、采样频率、医学时间和可得时间分别核验，没有依据时显示准确不足原因。

Given 当前真实未核对且时间未知的32项检验事实，When 查询观察时段和实际来源，Then 原值、单位及准确字段保持，医学时间保持未知，没有采样间隔、临床比较或安全结论。Given 正式撤销及重新授权当前资料权限，When 读取接口及恢复页面，Then 不返回无权限采样正文，恢复后重新读取实际来源。

检索依据为开发索引、T16、T47、QA12、QA19、PRD第8.19.7节、observation-period.schema.json及当前接口、读取和页面源码。当前实现与配置要求见 [观察资料运行设计](../../backend-design/observation-period-runtime.md)。本专项测量采用main基准commit 8ca48b9abb6395284a2c2ee360fbc6545d7ae763及当时工作区内容，27项检查对应后端制品为e06e53583d3ccdb95718c46338609ad0eff9cee37265626ebe9f0daea33453af。实际保存条件由QA21维护；当前共享读取的32项准确采样与运行采用由QA24再次核验。

| 位置 | 当前职责 |
| --- | --- |
| backend/records/periods.py；observation_visibility、period_view | 明确可见字段、当前权限、采样原值与独立时间语义 |
| backend-design/runtime.schema.json | observation_period_visibility_config_id的准确UUID定义 |
| guardian-h5/src/components/factPresentation.ts | 语境、发生状态、核对状态和比较符的统一中文显示 |
| guardian-h5/src/features/records/UI2ObservationPeriodPage.tsx | 实际采样、未知医学时间、可得时间、当前可访问范围及来源 |
| guardian-h5/src/features/archives/UI2DeltaPage.tsx | 采用相同中文核对状态，继续读取真实保存结果 |

| 实际检查 | 结果 | 私有证据 |
| --- | --- | --- |
| 明确配置、缺少引用及错误类别拒绝 | 3项通过，保留原环境、用途及三个档案字段 | configuration.json |
| 真实资料读取、内容、未知时间、重复读取及来源 | 5项检查、5次实际接口响应通过 | reads.json；period.json |
| 正式撤权、范围信息、正文隐藏、恢复及权限关闭 | 4项通过；原页事实返回403，采样正文为空，恢复后32项原值一致 | permission-audit.json |
| 观察页面中文显示、原值、独立时间、刷新与来源 | 6项Chrome DOM检查通过，页面运行错误为0 | browser.json |
| 新增内容整理的失效、原回执、实际生成及重复请求 | 3项检查、9次实际接口响应通过，252项内容保持 | delta-current.json |
| 新增内容整理页面及共享中文组件 | 6项Chrome DOM检查通过，83项事实及准确来源保持，页面运行错误为0 | delta-browser.json |

27项专项及受影响检查通过。TypeScript、生产构建和Django系统检查通过；当前313份真实环境配置及26份原验收配置按原批准范围通过代码检查，8002和8003均为ready、admitted_current。实际命令与本专项证据保存在忽略目录.qa-work/QA20，原始证据校验值由reports/final.json维护；当前代码、实际范围保存及后续状态见 [QA21](QA21-real-observation-period-save.md)。

32项医学时间全部未知，32项实际可得时间与保存版本一致，专业状态全部为unverified；没有获准频率或采样间隔。页面完整显示原值、未知原单位、准确比较符及中文状态，明确平台取得资料的时间不能代替检查时间。字段撤权后，仍获准的范围信息保持可读，全部采样正文隐藏；原页事实接口拒绝返回正文。正式新授权保持原有三个字段范围，原授权继续撤销，限时管理权限全部关闭。

临床记录保持3份RecordVersion、249项RecordField、83项ArchiveFact及249项ArchiveFactVersion，没有新增临床记录、评估或模型调用。当前有效新增内容整理结果包含252项，全部九个实际结果保存2268项历史内容，其中八个结果失效，一个结果有效，准确状态由[QA27](QA27-real-clinical-access-scope.md)再次核验。提醒的两个实际签名回执、完成和取消状态通过复验。两项uncertain OCR请求继续占用2 CNY预留及并发容量，实际费用未知，意图检查返回PROVIDER_CONCURRENCY_LIMIT，OCR任务仍为3项。

本专项没有扩展原有字段权限，也没有创建观察时段版本。当前接口提供observation_period_change，实际范围保存与历史版本由 [QA21](QA21-real-observation-period-save.md) 继续验证。完整高频与稀疏采样、独立参考答案、有效专业核对、实际后台恢复、手机、微信和视觉验收继续待验证。63份原件与290份输入副本的完整校验证据继续引用QA12；输入完整性不表示全部页面识别完成或临床准确性成立。
