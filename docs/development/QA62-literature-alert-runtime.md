# QA62 文献规则采用与真实预警运行

状态为 BLOCKED，原因是等待准确运行变更的明确批准，文档有效性 CURRENT，日期为 2026-10-20。当前任务承接用户选择的“预警计算 → 提示展示”，以及用户授权依据文献指南进行 AI 规则判断。范围对应 FR-06、AC-DYN-01—05、T49、T50、T51，关联 [QA61](QA61-login-alert-flow.md) 与 [文献核查](QA61-guideline-review.md)。不扩展其他业务功能。

## 当前输入与运行范围

开发位于 codex/current-design-reception，独立登录的 main 发布版本为 889d662d61045e5323b75cb07ec2cf417b668441。当前 8003 实际运行服务及 102 项迁移保持原有版本。本任务采用现有评估、任务及家庭投影能力；QA44 的进程与数据库切换批准范围继续遵守原记录。

真实资料、准确身份、现有配置与输入清单来自 .qa-work/QA61/alert-readiness.json、alert-input-inventory.json、current-lab-items.json 和 audit.json。资料来源继续采用用户已授权目录，正文与原件不进入 Git。当前保存的检验字段没有 concept_code；分类所需的多项输入不足，血小板原始记录缺少明确单位。原值、单位及采样时间保持实际状态。

## 采用原则

AI 文献核查记录实际方法和依据，不登记不存在的专业人员、核对证明或临床答案。既有字段的核对状态保持实际状态，未达到计算资格的字段保留排除原因。研究与软件流程验证分别记录，缺少资料时不能给出确定的 MAS 结论。

规则、用途、时间窗口、输入政策及发布内容使用当前 Schema 和配置资格检查，证据绑定准确版本与实际运行制品。配置中存在相互引用，本地初始化通过同一事务建立具有预先分配 UUID 的草稿，随后使用现有 config_submit、approve_config 和 validate_config 服务登记状态。此操作不表示配置创建 HTTP 接口已验收。每份验证的范围明确保存在实际数据集来源中，结构检查未登记为临床准确性评价。

目标配置为十九份 version 2，包含文献分类、单位映射、四类上下文、资格、时间窗口、动态输入、周期处理、家庭内容、可见范围、站内通知、软件测试联系账号、触发规则、验证、事件计划和运行参数。临床原值、字段身份、核对状态及未知单位保持。静态适用前提由 sjia／fever 独立项目判定；没有完整用药解释时保留治疗影响限制。时间窗口与周期处理参数只登记为本地软件运行参数，未登记临床准确性通过。

当前测试账号仅对一个获准病例增加 execute_assessment、read_assessment 和 receive_alert 范围，岗位增加前两项动作。没有增加 verify_field、其他病例或其他人员权限。批准方法记录为 user_delegated_ai_literature_review_local，不登记实际专业人员证明。任务数据集实际读取当前 RecordField，七项分类输入没有可采用项目；确定性分类返回 insufficient。

## 行为与验收

| case_id | Given | When | Then |
| --- | --- | --- | --- |
| QA62-01 | 已核查文献与当前代码 | 检查规则及比较符 | 文献、准确配置和计算实现一致，各自依据可追溯 |
| QA62-02 | 当前真实病例及原始字段 | 收集评估输入 | 字段、版本、来源和时间来自实际记录，未知状态完整保留 |
| QA62-03 | 当前用途、实际岗位与配置 | 从现有入口创建预警计算 | 按现有任务协议保存请求、Task、实际执行及结果 |
| QA62-04 | 计算输入不足 | 完成实际计算并读取结果 | 保存不足项目与具体原因，不生成确定风险结论 |
| QA62-05 | 当前计算结果与获准家庭内容 | 进入提示页面并刷新 | 实际展示与服务端结果一致，来源和状态可重读 |
| QA62-06 | 当前授权或准确配置变化 | 重新读取原结果 | 原权限与有效性检查继续生效，不能采用其他版本结果 |

## 实际检查与采用状态

| check_id | 实际检查与证据 | 当前结果及范围 |
| --- | --- | --- |
| QA62-01 | prepare_plan.py；proposed-plan-py312-v2.json；原始指南与 classification.py | PASS：十八份配置正文的 Schema、文献五项阈值及比较符一致；临床准确性未运行 |
| QA62-02 | preflight.py；preflight.json；stage_configuration.py；staged-configuration-v2.json | PASS：真实字段清单、来源和缺项计算已保存；十九份目标配置具有软件验证记录且均为 approved，尚未启用 |
| QA62-03 | check_execution_scope.py red／green | NOT_RUN：实际任务创建与 worker 执行待批准；前置岗位检查 PASS，真实接口允许选择获准病例 |
| QA62-04 | stage_configuration.py 的真实缺项输入计算 | NOT_RUN：实际任务持久化与重读待批准；缺项分类的 UNIT 检查 PASS，结果为 insufficient |
| QA62-05 | 现有家庭首页和提示详情入口 | NOT_RUN：待采用配置后验证实际计算结果展示 |
| QA62-06 | 当前授权和配置变化后的结果重读 | NOT_RUN：待实际任务保存后验证 |
| QA62-R01 | prepare_activation.py；activation-prepared.json；before-activation.dump | PASS：五个准确进程身份、四个空闲队列及实际数据库备份；备份 3453063 字节 |
| QA62-R02 | audit_staged.py；staged-audit.json | PASS：十张临床表与原任务摘要保持，gate 为 325／324，8002／8003 ready 且 admitted_current，5176／5177／5178 可访问 |
| QA62-R03 | check-real-warning-flow.mjs；warning-flow-current.json | Red：真实 Chrome 登录与单病例范围通过，创建弹窗实际返回零个获准规则组合；在 CLASSIFICATION_OPTIONS 终止，没有创建任务，页面错误为零；报告绑定准确脚本及构建摘要 |

浏览器验收脚本位于 guardian-h5/scripts/check-real-warning-flow.mjs，使用实际账号、获准病例、当前构建与版本配置清单。完整断言包含现有页面创建静态及动态任务、真实 worker 完成、服务端结果重读、动态计算保留静态输入资格不足、家庭首页及提示详情刷新读取同一不足状态。预期依据 PRD BR-D05／BR-D07 以及 T49／T50：静态分类的内部状态为 insufficient 时，分类依赖保持 unknown，不能作为已完成的合格依据。当前仅运行至实际配置选择，后续断言尚未运行，不登记整个脚本通过。DOM 控件检查限定当前创建弹窗及准确标签标题；没有替代响应、固定可见状态或视觉检查。检查后 staged-audit-browser.json 确认十张临床表、原任务数量、gate 与服务进程保持。

采用脚本为本地忽略目录 .qa-work/QA62/activate_and_adopt.py，准确制品为 f40674cd99096eeb58bb4c506745f0b50bcf125800c87178d9bdd78861f8d023。操作范围为启用十九份配置及家庭发布记录，由现有 8003 API、三个 worker 和 dispatcher 加载同一源码的新配置，新增仅处理 assessment 队列的 worker 与 dispatcher。源码切换、数据库迁移、临床原值更新和新模型调用均不在本次操作范围。

自动审批拒绝执行这组操作，理由为“启用并发布多项持久配置、推进环境状态、停止并重启多个服务，还会启动新的 worker 与 dispatcher；影响超出单病例测试范围，现有授权不足以覆盖这一组高影响运行变更”。当前正在等待用户对准确操作范围的明确批准。拒绝后只读审计确认配置尚未启用、API 进程保持、实际预警计算与家庭结果采用均未执行。不得通过其他操作间接执行被拒绝范围。

当前业务执行、配置采用、实际任务保存与家庭展示继续待运行。staged-audit-blocked3.json 完成当前只读复核，十九份目标配置尚未启用，十张临床表与原任务保持，现有验收服务正常。相同批准条件已连续三个目标轮保持，准备与可执行检查已经完成，后续运行需要用户明确批准。病例原始内容、现有 OCR 未知状态及费用预留继续保持；不使用替代响应、补造检验值或模拟时钟。预警流程和整个临床范围均未登记验收通过。
