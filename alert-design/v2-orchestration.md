# ALERT V2.0 识别、资格与结果采用

日期：2026-10-07。维护责任：T37。PRD 2.0、DATA V2.0、BACK V2.0 为准确输入。原有 N00—N11、工具、隔离、预算与发布要求保持；[节点目录](v2-nodes.csv)规定当前动作与实施责任。

## 识别身份与采用

| 能力 | 输入身份与模型 | 输出、采用和业务结果 | 验收 |
| --- | --- | --- | --- |
| 文书逐事实提取 | 单页 page_id/version、准确原件或用途副本、checksum、Provider 配置、model、Prompt／Schema、用途、TaskAttempt | [模型 Schema](v2-recognition.schema.json) → 原文、位置、逐事实时间语境、否定、未知 → 本人逐项决定 → 不可变 RecordField 与 ArchiveFactVersion | GRAC-01、AC-AI-01；T41／T55 |
| 文书与就诊归组 | 全部有序实际页面、原文边界、来源机构、形成时间、当前患儿与版本 | 模型提出 document_type、organization、episode_text；本人归组保存新文书版本；专业 episode 依据准确来源另行核验 | GRAC-03；T43 |
| 静态评估 | 精确结构化事实、字段及专业证明、规则、窗口、映射、known_at、assessment_at | 服务端取得合格字段，独立分类和 MS，保存全部输入及排除原因；不接受客户端数值作为内部计算输入 | AC-04／05、AC-DYN-01；T49 |
| 动态评估 | 合格 baseline/current/series/context、独立采样关系、治疗与观察期、规则族、sequence、epochs | 相邻变化 → 三值信号 → 嵌套组合 → 内部结果 → 当前采用检查 → 发布资格及周期 → 准确家庭事件 | AC-DYN-01—04；T50 |
| 历史评价 | 独立用途集合、冻结版本、当时可得资料和资格、独立答案及完整分母 | replay 专用结果；不创建家庭事件、不发送、不修改当前周期；不足与覆盖缺口保留 | AC-DYN-05、AC-EVAL；T48／T59 |

Ark Adapter 已有实际源码；本项不调用 Provider。实际 model_id、返回 model、endpoint、Prompt 摘要、Schema 摘要、输入摘要、原始响应对象、request_id、usage 由 TaskAttempt 与 ArchivePageAttempt 保存。配置名称不能代替实际返回模型身份；供应商 deployment alias 需准确绑定预期返回身份，未登记时拒绝采用。费用无实际依据为 null，身份和临床正文不写运行日志。

模型只有固定提取调用，没有工具循环、外部检索、任意 SQL、专业核对或发布能力。稳定 System 说明、固定 Schema／任务说明、准确页输入依次加载；报告内的命令仅为资料。Prompt 版本与批准 Provider 配置固定到每项尝试；不得因失败静默换模型。Prompt Cache 效果需要实际 usage，当前 NOT_RUN。

模型返回 original_name、原值文字、Decimal 字符串、限定符、原单位、方法、原参考范围、source_quote、locator、原文时间、context_type 与 assertion_state。服务端分配 UUID，不采用模型生成 patient/field/verification ID。字典只有唯一准确匹配且当前有效才能赋 concept_code；歧义保持未匹配。多项检验不共用推测日期，explicit_common 必须有原文明确共同时间，逐项本人确认保持差异。

source_quote 必须能在本次 page_text 中定位，source_anchor 绑定准确 FilePage、文件版本和 checksum。模型 page_text 与内部引用一致只支持结构检查；实际原图正确性由本人及专业核对验证。bbox 必须在准确解码尺寸内；不能定位时 anchor_kind=page、bbox=null，不制造坐标。跨页、表格行列、单位、时间或文书边界不能证明时保存 partial 或 unknown；拒绝未声明页引用。候选原文与人工更正分别保存。

本人采用逐项 accepted/modified/unknown/excluded，decision 完整且 source/version 一致后在一个事务保存候选决定、正式记录、事实、来源引用与 Outbox。本人确认时间由服务器产生，available_at 来自实际来源版本取得证据；专业状态保持 unverified。重复采用复用原逻辑请求；原件更正、撤权、取消或候选 superseded 后拒绝采用。unknown 不被空数组或数值零替代。

## 专业资格与固定上下文

[EvaluationContextV2](v2-context.schema.json) 保存准确 FactVersion、RecordVersion、Field、SourceRevision、SourceAnchor、DocumentVersion、医学时间、专业证明、映射、采样关系、四类输入角色、排除原因与全部 epochs。字段原值仍从 RecordField 读取，禁止另建可独立修改的数值副本。

N02 按如下顺序判定，每项拒绝保存稳定 reason_code：

1. 当前访问和准确用途、环境、字段范围、原件与来源有效；同患儿和当前允许版本成立。
2. 本人采用已经提交；context_type 与 assertion_state 符合该规则批准语义。既往、计划、出院结论不能转换为当时实际专业状态。
3. 精确版本和用途的逐字段专业核对有效；证明范围、核对时间和撤销时间满足当前或历史模式。confidence、模型文本、本人决定均不授予资格。
4. 项目身份、单位与批准 mapping、方法、标本及来源可比较；限定值与未知值不能代入精确比较。
5. 逐事实医学时间的精度、时区、边界、窗口、跨项目间隔及事实可得时间满足该模式。未知可得时间不能重建历史；legacy 不追加历史取得或共同时间证明。
6. 按准确采样关系选择独立集合。same_bytes/document_copy 只提供来源关系，same_sampling 或 formal_correction 的临床采用还需要专业依据；未决冲突不自动选异常值或最新上传值。
7. 选择批准 current 与 baseline 策略，固定 series 全部合格采样和 context。分页未完整、预算不足或引用缺少即失败或不足，不能截取子集声明完整。

前提、症状与治疗状态分别需要准确专业证据。未知、否定、计划、实际使用保持原语义；结束时间 null 不推断持续。某个规则资料不足只限制对应依赖，其他已满足条件且发布有效的规则继续独立处理。

当前模式核验当前证明、来源、版本、配置与资格；known_at 为明确受控时点，不能把后来的证明或文书放入较早集合。replay 同时重建当时的取得、确认、专业核对、更正、撤销、规则与映射。历史资格无法重建保存不足；后续出院结论不提前进入 context。两种模式每次执行仍核验当前用途与访问权限。

观察期是明确查询范围及来源，label_source=self_report 不取得专业认可。当前采样间隔来自 MedicalTime 与独立采样；目标频率、有效窗口和规则前提来自准确批准配置，没有参数不填写临床默认值。

## 计算、组合与连续状态

BR-D01—BR-D10 为唯一动态业务规范。分类与 MS 使用 PRD 第 8.14 节的准确原始规则；本项不新增临床阈值、比例、时间窗或分值。独立不足、not_applicable 与 completed 分别保存，分类未满足不能终止独立动态规则。

N05 保留每对严格有序的采样：current−baseline 为绝对变化；相对变化为该差值除以 baseline 后乘 100，baseline 为零或规则不接受的非正值时为空。Decimal 有限值与批准精度检查在运算位置执行。仅日期等精度计算间隔范围；不能证明严格顺序或完整处于允许间隔则该条件 unknown。n 个独立采样形成 n−1 对变化，同次采样副本与正式更正不增加次数。原单位与 normalized 值分别引用。

N06 逐项执行所有信号和嵌套子组：all 存在 not_met 返回 not_met，全部 met 返回 met；any 存在 met 返回 met，全部 not_met 返回 not_met；其他为 unknown。at_least(k) 使用 S≥k 或 S+U<k，其余 unknown。各层仅对直接条件及子组计数，全局信号引用唯一；层数、操作数与 k 由配置及运行预算校验。不能用短路计算省略依据与未知原因。

N07 在固定锁顺序内核验准确输入、证明、关系、观察期、规则、用途、epochs 和 lease_generation，保存结果与依赖。当前采用顺序使用 assessment_at、患者锁分配的 context_sequence；较早结果可以追溯，不能覆盖首页或周期。mode=replay 的 N08/N09/N10 发布与通知动作全部禁止。

N08/N09 保存每个 rule_family 的实际 met/not_met/unknown、采用或阻止原因和 sequence。已读不关闭周期；unknown 不作为恢复。持续 met 是否再次提醒、等级提高、恢复时段、最少采样、规则版本切换及新周期均由准确批准 reset/retrigger policy 决定。同一次 evaluation/event 重复消费返回原结果，不增加 sequence 或发送。旧依据失效保存 invalidated，病情恢复需要独立批准条件。

全部有效家庭提示可查询，首页使用准确批准优先级与稳定 rule_key 选择主提示。完整依据、其他提示及不可见原因保留，首页单项选择不能删除其他有效事件。

| 规则 | 节点与维护任务 | 验证要求 |
| --- | --- | --- |
| BR-D01 | N00／N01／N02；T49／T50 | 同患儿、四类输入、资格、时点、版本和排除依据固定 |
| BR-D02 | N05；T50 | Decimal 绝对与百分比变化，零及不接受的分母分别处理 |
| BR-D03 | N02／N05；T49／T50 | 方法单位核对与日期区间完整成立 |
| BR-D04 | N02／N05；T44／T50 | n−1 相邻变化，重复引用和更正不增加独立次数 |
| BR-D05 | N03／N04／N05；T49／T50 | 静态、趋势与专业状态按批准配置独立执行 |
| BR-D06 | N06；T50 | all、any、at_least 的 met／not_met／unknown 与完整依据 |
| BR-D07 | N06；T50 | 各层直接子项，引用唯一，预算超限拒绝 |
| BR-D08 | N08／N09／N10；T50／T51／T52 | 准确等级与正文，全部提示及稳定首页选择 |
| BR-D09 | N07／N08／N09／N11；T50 | epochs、租约与顺序检查，迟到结果不能覆盖 |
| BR-D10 | N00／N02／N07；T48／T50／T59 | 当时可得与当时资格，历史不足及全部发布禁止 |

## 控制点、错误与运行预算

| 控制点 | 实际执行位置 | 失败结果与恢复 |
| --- | --- | --- |
| ModelInputAuthorize | 页任务开始与实际请求之前 | PURPOSE_REVIEW_REQUIRED／DEPENDENCY_INVALID；不外发，取得准确资格后新任务 |
| ModelOutputValidate | Ark 原始响应读取后 | Schema、finish_reason、model身份、Decimal、页及原文检查；错误即 failed，无替代候选 |
| CandidateAdopt | 逐项确认事务 | VERSION_CONFLICT／PROTECTED_FIELD／SOURCE_UNAVAILABLE；保留本人输入，重新读取准确版本 |
| PreCompute | N00—N03 | 权限与技术错误为 failed/blocked；业务不足保存 reasons，不计算完整结果 |
| PostCompute | N04—N06 | 数值、结果 Schema、引用、组合与计数一致；错误在出错位置产生 |
| PreAdopt | N07 | epoch、租约、顺序或资格变化为 superseded；新上下文使用新 Task |
| PrePublish | N08／N09 | 未批准、联系、规则、验证及周期前提缺少禁止家庭事件 |
| PreSend／PreRead | N10 | 当前接收关系、许可、内容与准确事件有效；真实回执或拒绝分别保存 |
| Invalidate | N11 | 撤权、更正、停用等完成后新请求即阻止旧采用；异步遍历维护影响清单 |

任务期限、队列、租约、心跳、尝试、成本、Token 与并发引用 BACK V2.0 的 runtime。OCR 工程建议总期限 180 秒、单次读取 60 秒；确定性计算建议 30 秒。参数缺少或范围不合法时不受理该能力；外部提交额外重试零次，受理未知查询最多三次且不自动换模型／渠道。费用未知保持 null。取消禁止后续采用，Provider 实际停止依据回执；失败和原件保存后可以按当前权限创建新的任务。

资料提取、规则和通知使用独立进程及最少数据权限。规则 Worker 无外部发送密钥，识别进程无 verification/release 写权；单一家庭会话或页面状态不能替代独立服务资格。产品不注册自主 Subagent、RAG、MCP 或会话长期记忆，固定任务按真实领域状态执行。

## Verifier 与实际验收

T37 检查成熟 Schema、全部节点、接口及字段引用、输入角色、来源、未知与预算规范；不创建临床配置或候选，不调用 Provider。T41／T42 使用获准真实原件验证逐项时间、否定、计划与出处；T49／T50 使用准确核对、规则和独立答案运行四种静态／动态组合、连续关系、较早迟到结果与 replay；T55 检查真实模型及采用，T59 按冻结评价集合统计完整分母和限制。

格式评测按页与字段，流程按实际操作，临床按独立患儿、事件与观察人时；独立答案不能由本程序输出产生。未知标签不能计入阴性，稀疏采样不能补齐真实观察。所有结果保存来源、代码、规则、集合、实际命令与原始输出，未覆盖输入明确登记。软件成功、专业核对、家庭发布、送达与阅读分别验收；视觉限制继续有效。
