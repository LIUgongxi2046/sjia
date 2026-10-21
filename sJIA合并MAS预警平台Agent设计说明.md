# sJIA 合并 MAS 预警平台 Agent 设计说明

| 文档信息 | 内容 |
| --- | --- |
| doc_id | SJIA-AGENT-DESIGN |
| 适用对象 | 产品、研发、测试与临床协作人员 |
| 说明范围 | Prompt、Workflow/SOP、Knowledge Base、LLM/DSLM、Tools、Evaluation |
| 源码基线 | `f05dbcf106e70d890a5574385fd63b3ddd502146` |
| 文档有效性 | CURRENT，限于本次核查的源码与引用材料 |
| last_verified | 2026-10-21，Asia/Shanghai；核查源码和文档 |
| 运行证据日期 | 真实 OCR 与候选采用引用 QA12 的 2026-10-19 记录 |
| 维护责任 | 随预警编排、模型调用或评测实现变更更新本文 |

风险判定由固定 Workflow 和确定性规则执行，LLM 负责医疗资料提取。模型生成候选信息，后端负责结构校验、用户确认、专业核对、评估计算和发布检查。该分工与 [预警编排设计](alert-design/v2-orchestration.md) 一致。

| 组成 | 在系统中的作用 | 当前实现 |
| --- | --- | --- |
| Prompt | 规定模型任务、输出格式和处理边界 | 已实现报告、治疗资料、语音文字、医疗文书的独立 Prompt |
| Workflow / SOP | 规定处理顺序、进入条件、失败处理和人工参与 | 已实现，由 Django 领域服务、Celery Worker 和任务状态控制 |
| Knowledge Base | 提供医学规则、术语、单位和证据来源 | 已有结构化规则、字典、单位映射与来源管理；当前设计未采用 RAG |
| LLM / DSLM | 执行资料理解和信息提取 | 已有 Ark 模型调用与本地语音转写；当前源码未发现 sJIA/MAS 专用 DSLM 的训练或微调实现 |
| Tools | 读取资料、处理文件、计算评分、保存结果和发送通知 | 已实现固定服务调用，工具选择由后端代码控制 |
| Evaluation | 检查提取质量、流程正确性和临床效果 | 已有软件检查与临床指标计算代码；独立临床效果验证尚未完成 |

## 1. Prompt：限定模型任务和输出内容

### 1.1 按资料类型设置任务

| 资料类型 | 模型执行的任务 | 主要输出 |
| --- | --- | --- |
| 检验报告 | 识别明确可见的检验项目 | 项目原名、原始数值、比较符、单位、参考范围、检测方法和项目原文 |
| 治疗资料 | 转录清楚可见的药名与对应内容 | 原药名和包含规格、剂量、频率、时间等信息的原文 |
| 语音转写文字 | 提取监护人明确描述的观察 | 体温、单位、症状、原始时间描述和观察说明 |
| 医疗文书 | 逐项提取事实，保留原文与语境 | 页面文字、逐项原文引用、数值、单位、时间文字、否定状态、计划或实际语境、位置及文书信息 |

Prompt 对资料中的命令、角色声明和输出要求进行明确限制：这些文字只属于待处理资料，不能改变模型的提取职责。模型不能生成诊断、治疗建议、权限或专业核对资格。

未知内容使用 `null` 或 `unknown`。数值保留原始精度和末尾零，时间保留原文明示的信息；文书日期、上传日期和当前日期不能自动成为各项检验的采样时间。计划治疗与已经实施的治疗分别保留其语境。

### 1.2 输出校验与版本记录

模型输出接受 Pydantic 或 JSON Schema 校验。文书提取同时检查每个 `source_quote` 是否存在于本次 `page_text`，并由后端建立字段与原件页面的关联。

该引用检查能够验证模型转录文字与候选引用的内部一致性。原图是否被准确读取、项目是否遗漏、否定含义是否正确，仍需独立来源核验。

`contract_snapshot()` 记录 Prompt 和 Schema 的 SHA-256；`invoke_model()` 继续记录实际提交消息与生成参数的 SHA-256。调用记录能够关联本次任务使用的提取规则和输入版本。

当前 Prompt 以 Python 常量维护。检验报告的时间候选版本通过 Provider 配置显式选择；没有通用 Prompt 在线编辑中心的实现证据。

代码依据：[ark.py](backend/recognition/ark.py) 中的 `OCR_SYSTEM`、`OCR_PROMPT`、`THERAPY_SYSTEM`、`VOICE_SYSTEM`；[archive_extraction.py](backend/recognition/archive_extraction.py) 中的 `SYSTEM`、`PROMPT`、`parse_archive_result()`；[invocation.py](backend/recognition/invocation.py) 中的 `contract_snapshot()`。

## 2. Workflow / SOP：控制信息进入评估的过程

### 2.1 从原件到评估结果

以报告识别与后续评估为例，后端通过明确业务步骤连接各项能力：

1. **接收和校验资料。** 检查原件、当前患儿、访问权限、资料用途及当前配置；需要外部处理的资料继续检查相应用途副本。
2. **登记识别任务。** 固定输入版本、Provider 配置、Prompt/Schema 摘要、任务期限与预算。
3. **执行模型调用。** 保存实际请求依据、原始响应与用量信息，完成格式检查后创建候选字段。
4. **采用候选信息。** 用户逐项接受、修改、标记未知或排除，后端保存确认记录、字段版本和原件出处。
5. **检查专业核对资格。** 临床计算读取对应字段与用途的有效核对证明，同时核查医学时间、单位、语境、来源和适用条件。
6. **计算静态与动态结果。** 使用合格输入执行分类、评分或动态条件组合，保存输入引用、规则版本、逐项结果及资料不足原因。
7. **控制展示与通知。** 家庭展示和通知继续核查当前发布配置、权限与发送条件。

本人确认与专业核对承担不同职责。本人确认决定如何保存候选信息；专业核对证明决定字段是否满足相应用途的评估要求。接受候选不会自动产生专业核对资格。

### 2.2 由事件和任务状态控制执行

| 事件 | 执行入口 | 作用 |
| --- | --- | --- |
| `archive_page.requested` | `execute_page()` | 执行档案页面识别 |
| `recognition.requested` | `execute_recognition()` | 执行登记的识别任务 |
| `voice_input.requested` | `execute_voice_input()` | 执行确认文字的结构化提取 |
| `assessment.requested` | `execute_static_assessment()` | 执行静态评估 |
| `alert_evaluation.requested` | `execute_dynamic_evaluation()` | 执行动态评估 |
| `verification.changed` 等变更事件 | `fanout_dynamic_change()` | 按当前条件触发相关动态处理 |
| `evaluation_run.requested` | `execute_evaluation_run()` | 执行评价集合任务 |

任务使用期限、执行租约、心跳、尝试记录和消费回执控制运行。执行阶段重新核查权限与输入版本；静态或动态任务的上下文变化后，可以进入 `superseded`，其结果不能继续作为原任务的有效输出。

外部调用受理情况不明时保留 `uncertain`。取消或网络超时不能证明提供方已经停止处理或计费，已开始且费用未知的调用继续保留对应预算记录。

代码依据：[worker.py](backend/tasks/worker.py) 的 `consume()`、[registry.py](backend/tasks/registry.py) 的 `WORKER_EVENTS`、[confirmation.py](backend/recognition/confirmation.py) 的 `prepare_confirmed_fields()`、[qualified_facts.py](backend/assessment/qualified_facts.py) 的 `verification_proof()`、[assessment/service.py](backend/assessment/service.py) 和 [alerting/service.py](backend/alerting/service.py)。

## 3. Knowledge Base：管理规则、术语与证据来源

### 3.1 知识内容的保存位置

| 知识内容 | 保存或执行位置 | 使用方式 |
| --- | --- | --- |
| 2016 分类条件 | `classification.py` | 固定比较条件与组合判定 |
| MS 评分公式 | `ms.py` | 使用 Decimal 计算各项贡献与总分 |
| HLH-2004 参考支持 | `hlh.py` 及对应政策类型 | 依据明确输入、适用条件与政策计算参考结果，保留用途边界 |
| 动态触发条件与时间窗口 | `ConfigVersion` 及政策 Schema | 读取指定版本，检查资格后参与计算 |
| 术语与同义名称 | `DictionaryEntry`、`DictionaryAlias` | 查找项目及其标准概念 |
| 单位映射 | `UnitMapping` | 按获准映射和检测方法范围执行单位换算 |
| 来源及版本 | `SourceRegistry`、`SourceRevision` | 保存来源位置、校验和、版本、允许用途与证明引用 |
| 配置审批与验证 | `ConfigApproval`、`ConfigValidation` | 关联配置内容、环境、验证数据来源和程序制品 |

当前知识主要供确定性服务使用。分类条件和 MS 系数位于 Python 代码中，动态条件、时间窗口、字典和单位映射通过版本化配置管理。修改公式与更新配置分别遵循各自的程序和配置发布过程。

患儿记录、原件和逐项事实提供当前病例的上下文；来源登记与政策配置提供知识及资格依据。保存了一份病例文书，不会使其自动成为通用医学规则。

### 3.2 知识采用条件

配置读取会检查启用状态、内容校验和、当前环境、审批范围、来源状态、验证报告和程序制品。字典与单位映射也需要满足相应配置资格。

当前设计明确未采用 RAG。代码没有将医学指南全文检索结果提供给 LLM、再由模型生成临床回答的调用过程；资料查找功能服务于病例档案读取与来源定位。

代码依据：[configurations/models.py](backend/configurations/models.py)、[configurations/qualification.py](backend/configurations/qualification.py) 的 `configuration_qualification_reason()`、[sources/models.py](backend/sources/models.py)、[dictionaries/service.py](backend/dictionaries/service.py)、[classification.py](backend/assessment/classification.py)、[ms.py](backend/assessment/ms.py)、[hlh.py](backend/assessment/hlh.py)。设计依据：[预警编排 V2](alert-design/v2-orchestration.md)。

## 4. LLM / DSLM：执行资料提取，保留模型身份与调用依据

### 4.1 当前模型能力

| 能力 | 当前实现 | 模型身份来源 |
| --- | --- | --- |
| 图像报告及文书提取 | Ark Adapter 调用 `chat/completions` | 获准 Provider 配置中的 `model_version` |
| 确认后的语音文字结构化 | 固定语音提取 Prompt 与受控调用 | 获准 Provider 配置中的 `model_version` |
| 音频转写 | 本地 `faster-whisper` | 本地模型目录及转写记录中的模型身份信息 |
| sJIA/MAS 专用 DSLM | 当前源码未发现专用训练、微调或专用推理服务实现 | 当前证据不足以确认已经采用疾病领域专用模型 |

`invoke_model()` 检查 Provider 配置中的端点和模型标识，`assert_authorized_result()` 检查实际响应中的模型标识。返回身份需要与配置一致；当前实现不隐式接受模型别名或名称前缀匹配。

调用记录包含 Provider 配置 ID、配置校验和、请求模型、响应模型、请求标识、输入摘要和 Token 用量。费用根据实际用量与已登记价格计算；缺少用量信息时费用保持未知。

### 4.2 风险判定的执行方式

静态评估通过 `classify_2016()`、`compute_ms()` 及 HLH 参考计算执行。动态评估通过明确条件判断趋势、时间间隔、采样关系、单位可比性和组合结果。

规则计算使用 `met`、`not_met`、`unknown` 等状态。资料不足时保留不确定性；MS 缺少必要变量时不生成完整总分。动态计算支持逻辑组合及已定义的加权组合，权重与阈值来自明确政策。

LLM 没有直接产生最终评分、专业核对状态或发布许可的权限。当前系统的疾病相关能力由规则代码、结构化知识、合格输入和人工核对共同提供。

代码依据：[invocation.py](backend/recognition/invocation.py) 的 `invoke_model()`、`assert_authorized_result()`、`actual_cost()`；[speech.py](backend/recognition/speech.py) 的 `speech_model()`；[local_transcription.py](backend/recognition/local_transcription.py)；[static_computation.py](backend/assessment/static_computation.py) 的 `compute_static()`；[alerting/computation.py](backend/alerting/computation.py)。

## 5. Tools：由固定流程调用业务服务

### 5.1 服务能力与职责

| 业务能力 | 对应模块 | 主要职责 |
| --- | --- | --- |
| 文件和模型输入处理 | `files`、`archives` | 校验原件、读取指定页面、管理用途副本与来源 |
| 候选提取 | `recognition` | 调用模型、校验输出、保存候选与调用证据 |
| 记录与事实保存 | `records`、`archives` | 保存确认内容、版本、出处和医学语境 |
| 术语和单位处理 | `dictionaries` | 提供术语查找与获准单位换算 |
| 专业核对 | `verification` | 核查人员与字段范围，保存核对证明 |
| 静态和动态评估 | `assessment`、`alerting` | 选择合格输入并执行规则计算 |
| 家庭展示与通知 | `family_content`、`notifications` | 核查发布资格、可见范围及通知条件 |
| 评价任务 | `case_details` | 固定评价输入，保存指标与限制 |

这些能力由后端服务按注册事件调用。发给 LLM 的请求包含 `model`、`messages`、`max_tokens` 等参数，当前调用入口没有提供自主选择工具的 `tools` 定义，也没有持续处理 `tool_calls` 的执行循环。

### 5.2 工具权限与执行边界

操作者、患儿、字段、资料用途、配置版本和任务状态由服务端检查。模型不能自行取得其他患儿数据、签发核对证明、修改临床规则或授权发送通知。

当前应用采用固定 Workflow 编排，模型在指定提取节点执行任务。生产业务流程没有注册自主 Subagent、MCP 工具循环或模型会话长期记忆；对应边界在编排设计中明确登记。

代码依据：[ark.py](backend/recognition/ark.py) 的 `_completion()`、[worker.py](backend/tasks/worker.py) 的 `consume()`、[family_content/service.py](backend/family_content/service.py) 的发布服务。设计依据：[预警编排 V2](alert-design/v2-orchestration.md)。

## 6. Evaluation：分别验证软件、提取质量与临床表现

### 6.1 软件行为验证

软件检查覆盖模型输出结构、Prompt/Schema 版本、模型身份、预算、取消、调用回执、字段权限、任务状态和持久化。

仓库中的 `evaluate_model_contracts.py` 是离线软件回归入口，使用已标明的合成测试资料。其结果只适用于相应软件行为，不能作为真实模型准确率或临床效果依据。本文编写期间未执行该入口。

### 6.2 提取质量与采用验证

提取质量需要检查项目名称、数值、比较符、单位、原文、时间、否定语境、来源位置和遗漏情况。候选保存、刷新读取和重复提交处理属于业务采用验证。

候选内部引用一致、用户成功保存与独立准确性分别记录。`source_quote` 存在于模型生成的 `page_text`，只提供内部一致性证据；独立准确性仍需要与原件或独立标注核验。

### 6.3 临床评测实现

| 指标或控制 | 当前计算能力 |
| --- | --- |
| 敏感度 | 统计可评估阳性事件中被识别的比例 |
| 漏报事件 | 保存未被识别的阳性事件引用 |
| PPV | 在结局已知的提示中统计阳性提示比例 |
| 提示负担 | 统计全部及阴性观察时间内的每人时提示数量 |
| 首次提示时间差 | 比较首次提示与独立参考时点 |
| 无法评估比例 | 单独登记资料不足或结果不可评估的事件 |
| 静态与动态比较 | 对相同评价范围分别计算两类结果 |
| 数据分组 | 分别输出开发集、验证集和指定亚组 |
| 时间与来源控制 | 校验事实可得时间、核对时间、固定输入和来源引用 |
| 置信区间 | 按明确评价协议执行患者分组重采样，并记录方法、输入与计算状态 |

评价任务读取固定集合与输入绑定。缺少必要临床结果、执行步骤未完成或必要置信区间未完成时，评价状态可以为 `insufficient`。任务执行完成与临床评价充分分别保存。

### 6.4 已有真实运行证据

QA12 的最近核验日期为 2026-10-19。该记录中的实际执行范围如下：

| 事项 | 记录中的结果 | 能够支持的判断 |
| --- | --- | --- |
| 实际 OCR 请求 | 三次请求中，一次取得 83 项候选；另外两项保持 `uncertain` | 已有真实提供方调用和候选保存证据，完整识别范围尚未完成 |
| 候选确认 | 83 项候选经过页面逐项确认、保存、刷新及原请求读取 | 对应确认和持久化流程已在记录范围内运行 |
| 字段质量 | 自动文字检查已执行，82 项名称需要复核，原图独立准确性尚未核验 | 尚不能给出独立提取准确率 |
| 专业资格 | 已保存事实保持 `unverified` | 本人确认没有自动授予专业核对资格 |
| 临床效果 | 独立临床准确性与效能尚未建立 | 尚不能声明临床预警效果通过验证 |

代码依据：[evaluate_model_contracts.py](backend/tests/evaluate_model_contracts.py)、[clinical_metrics.py](backend/case_details/clinical_metrics.py) 的 `mode_metrics()` 和 `evaluate()`、[clinical_metrics_v2.py](backend/case_details/clinical_metrics_v2.py)、[clinical_intervals.py](backend/case_details/clinical_intervals.py)、[evaluation_execution.py](backend/case_details/evaluation_execution.py)。运行证据：[QA12 真实 OCR 与字段验证](docs/development/QA12-real-ocr-validation.md)。

本文的核查范围为源码、设计与已有验证记录。本次文档编写未重新提交模型请求、运行临床评测或核验在线服务状态；运行证据继续使用各自记录的日期、输入与环境范围。
