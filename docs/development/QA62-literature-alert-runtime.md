# QA62 文献规则采用与真实预警运行

状态为 ACCEPTED，文档有效性 CURRENT，last_verified 为 2026-10-20。本记录为任务唯一状态来源。任务承接用户选择的“预警计算 → 提示展示”及依据文献指南进行 AI 规则判断的授权，范围对应 FR-06、AC-DYN-01—05、T49、T50、T51，关联 [QA61](QA61-login-alert-flow.md)、[QA63](QA63-local-test-account.md) 与 [文献核查](QA61-guideline-review.md)。

## 当前输入与运行范围

开发基于 main 的 e9ef845，位于 codex/current-design-reception。当前 8003 使用原有后端源码与 102 项迁移，制品摘要为 f40674cd99096eeb58bb4c506745f0b50bcf125800c87178d9bdd78861f8d023。本任务采用现有评估、任务及家庭投影能力，运行变更依据用户明确回复“批准本次运行变更”。

真实资料、准确身份、现有配置与输入清单来自 .qa-work/QA61/alert-readiness.json、alert-input-inventory.json、current-lab-items.json 和 audit.json。资料来源继续采用用户已授权目录，正文与原件不进入 Git。当前保存的检验字段没有 concept_code；分类所需的多项输入不足，血小板原始记录缺少明确单位。原值、单位及采样时间保持实际状态。

## 采用原则

AI 文献核查记录实际方法和依据，不登记不存在的专业人员、核对证明或临床答案。既有字段的核对状态保持实际状态，未达到计算资格的字段保留排除原因。研究与软件流程验证分别记录，缺少资料时不能给出确定的 MAS 结论。

规则、用途、时间窗口、输入政策及发布内容使用当前 Schema 和配置资格检查，证据绑定准确版本与实际运行制品。配置中存在相互引用，本地初始化通过同一事务建立具有预先分配 UUID 的草稿，随后使用现有 config_submit、approve_config 和 validate_config 服务登记状态。此操作不表示配置创建 HTTP 接口已验收。每份验证的范围明确保存在实际数据集来源中，结构检查未登记为临床准确性评价。

当前采用的十九份配置包含文献分类、单位映射、四类上下文、资格、时间窗口、动态输入、周期处理、家庭内容、可见范围、站内通知、软件测试联系账号、触发规则、验证、事件计划和运行参数。十八份配置为 version 2，运行参数沿用现有 qa12-ocr-runtime 标识的 version 9，以保持通知及资料处理的有效关联。临床原值、字段身份、核对状态及未知单位保持。静态适用前提由 sjia／fever 独立项目判定；时间窗口与周期处理参数只登记为本地软件运行参数。

当前测试账号仅对一个获准病例增加 execute_assessment、read_assessment 和 receive_alert 范围，岗位增加前两项动作。没有增加 verify_field、其他病例或其他人员权限。批准方法记录为 user_delegated_ai_literature_review_local，不登记实际专业人员证明。任务数据集实际读取当前 RecordField，七项分类输入没有可采用项目；确定性分类返回 insufficient。

## 行为与验收

| case_id | Given | When | Then |
| --- | --- | --- | --- |
| QA62-01 | 已核查文献与当前代码 | 检查规则及比较符 | 文献、准确配置和计算实现一致，各自依据可追溯 |
| QA62-02 | 当前真实病例及原始字段 | 收集评估输入 | 字段、版本、来源和时间来自实际记录，未知状态完整保留 |
| QA62-03 | 当前用途、实际岗位与配置 | 从现有入口创建预警计算 | 按现有任务协议保存请求、Task、实际执行及结果 |
| QA62-04 | 计算输入不足 | 完成实际计算并读取结果 | 保存不足项目与具体原因，不生成确定风险结论 |
| QA62-05 | 当前计算结果与获准家庭内容 | 进入提示页面并刷新 | 实际展示与服务端结果一致，来源和状态可重读 |
| QA62-06 | 合法支持结果及准确规则版本 | 切换家庭身份并请求支持详情 | 当前规则版本准确，支持详情拒绝家庭受众，家庭投影继续按获准范围展示 |

## 实际检查与采用状态

| check_id | 实际检查与证据 | 当前结果及范围 |
| --- | --- | --- |
| QA62-01 | prepare_plan.py；proposed-plan-py312-v2.json；原始指南与 classification.py | PASS：十八份配置正文的 Schema、文献五项阈值及比较符一致；临床准确性未运行 |
| QA62-02 | preflight.json；staged-configuration-v2.json；adoption-completed.json | PASS：来源及缺项保持，准确配置已启用并由当前进程采用 |
| QA62-03 | warning-flow-green3.json | PASS：真实页面创建静态及动态任务，实际 worker 完成，Task 与结果能够重新读取 |
| QA62-04 | warning-flow-green3.json | PASS：静态 insufficient，动态 insufficient／unknown，资格不足原因完整保存，没有 AlertEvent |
| QA62-05 | warning-flow-green3.json | PASS：家庭首页、提示详情及刷新读取同一实际评估时点和不足状态 |
| QA62-06 | warning-flow-green3.json | PASS：结果准确绑定目标规则，家庭身份读取支持详情返回 403 |
| QA62-R01 | prepare_activation.py；activation-prepared.json；before-activation.dump | PASS：五个准确进程身份、四个空闲队列及实际数据库备份；备份 3453063 字节 |
| QA62-R02 | .qa-work/QA63/audit.json | PASS：十张临床表摘要、评估以外任务、102 项迁移及两笔未知 OCR 保持，七个进程身份准确，8002／8003 ready 且 admitted_current，5176／5177／5178 可访问 |
| QA62-R03 | adoption-completed.json | PASS：8003 API、三个原有 worker、原 dispatcher、assessment worker 及 clinical_dispatcher 共七个进程采用当前配置；gate 为 351，publication_epoch 为 350，readiness 为 ready／admitted_current |
| QA62-R04 | check-real-warning-flow.mjs；warning-flow-green3.json | PASS：真实 Chrome 155 执行六项业务检查，页面错误为零，报告绑定脚本及准确构建摘要 |

浏览器验收脚本位于 guardian-h5/scripts/check-real-warning-flow.mjs，使用实际账号、获准病例、当前构建与版本配置清单。预期依据 PRD BR-D05／BR-D07 及 T49／T50：静态分类内部状态为 insufficient 时，动态分类依赖保持 unknown，保留 DYNAMIC_COMPATIBLE_STATIC_RESULT_REQUIRED。六项检查均已实际执行，覆盖任务持久化、刷新和家庭使用方。请求读取保留六十秒时限，任务轮询保留一百二十秒总时限；本任务没有登记性能验收。

采用证据位于本地忽略目录 .qa-work/QA62/adoption-completed.json，记录准确配置、发布记录、七个进程及运行身份。没有切换后端源码、执行数据库迁移、更新临床原值或新增模型调用。配置批准方法为 user_delegated_ai_literature_review_local，independent_professional_review 为 false，clinical_accuracy_validation 为 NOT_RUN。

用户已在对应确认项明确回复“批准本次运行变更”，范围为启用十九份配置及家庭提示发布记录、重启 8003 API 和三个现有 worker 与 dispatcher，并新增仅处理 assessment 队列的 worker 与 dispatcher。源码、数据库结构、临床原值及未知 OCR 请求保持；仅对一个获准病例执行本次计算。运行采用与后续业务验收依据这份明确批准继续执行。

当前前端制品来自 .qa-work/QA63/dist-green3，index SHA256 为 61080a8a5e66bc5f6a8e7b63cb610c5e669e3622942d51510395d445a71ec758。QA63 完成登录、患儿资料及会话回归。最终运行审计 .qa-work/QA63/audit.json 为 PASS。本任务的软件流程验收完成，完整临床准确性及独立专业复核保持未验证。
