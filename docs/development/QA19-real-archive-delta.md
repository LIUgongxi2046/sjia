# QA19 真实新增内容整理

当前专项状态为 ACCEPTED，验证日期为2026-10-19。需求为 FR-18、GRAC-06，关联 T46、T43、T45、QA12、QA14。验收范围为119页真实批次中一份已保存文书及83项未专业核对事实的本地确定性整理，没有选择比较基准。该批次其他118页尚未完成识别；完整V46及独立临床比较继续按原验收要求执行。用户已委托本次软件开发及批准。

新增内容整理明确引用archive_delta_visibility_config_id，采用当前环境、family_observation用途、clinical_fields为空的准确visibility-1配置，保留每项来源及全部资料访问检查。批次页数按runtime.limits.max_pages核验。summary任务政策规定执行、租约、并发及单次尝试范围；确定性整理不调用外部模型。完整访问、版本、失败和迁移要求见 [运行设计](../../backend-design/archive-delta-runtime.md)。这些软件批准不授予临床核对资格。

同次事务只复用已经完整核验的准确文书来源。全部涉及授权与关联均无结束时间时允许复用；存在结束时间时逐项重新核验。实际限时授权及真实时钟经过3秒的到期检查通过，来源复用没有延长授权期限。家庭提示与就医摘要继续执行各自的发布和联系资格检查。

| 检查范围 | 实际结果 | 私有证据 |
| --- | --- | --- |
| 生成、保存、重复提交、原请求及重复读取 | 5项检查、9次实际接口响应通过；相同请求返回原回执，252项内容保持 | execution.json |
| 页面分类、刷新及来源跳转 | 5项Chrome DOM检查通过，页面运行错误为0 | browser.json |
| 明确配置及有限期来源检查 | 配置3项、来源复用3项通过；错误配置拒绝，有限期授权按实际时间生效 | visibility-configuration.json；source-cache-control.json |
| 失效内容及实际重新生成 | 4项页面检查通过；失效结果不显示项目，连续点击只产生一次生成请求 | regeneration-browser.json |
| 正式撤销、重新授权及管理权限关闭 | 6项检查、17次实际接口响应通过；撤权后拒绝正文，重新授权后旧结果继续失效，原回执及历史项目保持 | permission-regression.json |
| 当前页面、中文显示及准确来源 | 6项Chrome DOM检查通过，页面运行错误为0；83项语境、77项原文记载存在、6项原文明确否定及比较符显示一致 | final-browser.json |

32项专项检查通过。当前有效结果包含文书1项、未知时间84项、冲突1项、未核对83项及事实83项，共252项；未知时间包括文书及事实时间。没有前后变化项目。原值、单位、比较符、语境、发生状态、未知时间、unverified状态、准确文书及原件引用全部保持。当前九个实际结果持久保存2268项历史内容，其中八个结果失效，一个结果有效，准确状态由[QA27](QA27-real-clinical-access-scope.md)再次核验。

当前代码的313份真实环境配置及26份原验收环境配置按既有批准范围通过检查，两套服务均为ready、admitted_current。TypeScript、生产构建和Django系统检查通过。临床记录保持3份RecordVersion、249项RecordField、83项ArchiveFact及249项ArchiveFactVersion，没有新增临床记录、评估或模型调用。QA15提醒的实际签名回执与终态复验通过，当前受影响范围的验证见 [QA21](QA21-real-observation-period-save.md)。

63份原件、290个来源页面、290份approved输入副本及实际字节再次通过完整校验和检查。两项uncertain OCR请求继续保留2 CNY预留及并发占用，实际费用保持未知；当前意图检查返回409与PROVIDER_CONCURRENCY_LIMIT，OCR任务检查前后均为3项，没有新增外部调用。

本专项原始证据及校验值保存在忽略目录.qa-work/QA19/reports/final.json；当前保存条件及受影响页面由QA21核验，当前代码与权限下的准确结果由.qa-work/QA27/reports/final.json维护。完整输入、提醒及额度复验分别由.qa-work/QA19/input-audit.log、reminders-audit.log、ocr-admission.log与django-check.log记录，真实正文及私有证据不进入Git。没有明确基准的当前结果不能证明临床比较准确性；完整页面识别、独立参考答案、专业核对、真实设备、微信及视觉验收继续保持未验证。
