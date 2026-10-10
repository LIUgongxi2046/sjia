# T55 模拟报告与语音质量结果

所有新语音运行都使用清楚标注的 `SIMULATION_TEXT_ONLY` 原件、模拟家庭账号和 `sjia_simulation`。独立答案由 `simulation_manual_independent` fixture 人工维护，`derived_from_model_output=false`，临床审核 `NOT_RUN`。

## 语音实际运行

模拟家庭 `simulation-voice`、患儿 `e91ce806-49b2-4e9a-97ed-ade303a27cd2`、VoiceInput `6a85134c-3bdd-4d87-b161-5455d038bc0a` 的 process API 返回 HTTP 202，实际调用 Task `26a9a814-350f-4495-bfd9-d2f87085bf19` 和单一 Outbox Event `ff1d9570-d245-42a7-8646-1486db99fa44`。TaskAttempt 保存 Provider request `0217915366138523472b452131530d7d1d28285be3fe78b19cdf5`，Task 输入摘要为 `2c2df15419e1ff16a34ed2c49227b6942d152efde611ab6615747da30c4d35d8`。

Provider 为 `44c922c1-9497-424a-9db9-ff9960ac065f`，模型 `deepseek-v4-flash-ga-260731`。调用 usage 为 prompt/completion `437/338` tokens；数据库 Attempt 起止时长 `5.164184` 秒，Provider duration `4991.37` ms。准确费率来源为 `urn:sjia:source-revision:d81af98c-9f90-4230-acb1-174e6e2b8cec`，输入每百万 tokens 1 CNY、输出每百万 tokens 2 CNY，模拟推算费用 `0.001113 CNY`。账单核验 `NOT_RUN`。

候选包含体温 `38.5 C`、症状 `发热`、`没有皮疹`、`精神比平时差` 和原始时间文字 `2026年10月9日早上7点`。五项决定为体温、单位和症状 `accepted`，观察时间与额外说明 `unknown`。首次采用 HTTP 200，Record `7a673e4a-7707-4474-a749-4880e32ebb89`，RecordVersion `20914aff-6328-4b90-a3a1-459701323e93`；相同 request 幂等重放 HTTP 200，Attempt 没有增加。保存记录含 `temp`、`observation_note` 两个字段，并从 API 与数据库重新读取；source anchor 都指向原件 `05006cfa-750e-4ac9-9fd8-899702363fb6` 和完整确认文字。

三层评分逐项统计如下，分母为四项独立 Gold 字段：

| 字段维度 | 模型候选正确／错误／未覆盖 | 本人采用正确／错误／未覆盖 | 保存重读正确／错误／未覆盖 |
| --- | ---: | ---: | ---: |
| 名称 | 4／0／0 | 4／0／0 | 4／0／0 |
| 比较符 | 0／0／4 | 0／0／4 | 1／0／3 |
| 数值 | 4／0／0 | 4／0／0 | 4／0／0 |
| 单位 | 1／0／3 | 1／0／3 | 1／0／3 |
| 否定 | 3／0／1 | 3／0／1 | 3／0／1 |
| 医学时间 | 1／0／3 | 0／0／4 | 0／0／4 |
| 语境 | 0／0／4 | 0／0／4 | 0／0／4 |
| 来源 | 0／0／4 | 0／0／4 | 4／0／0 |

时间未确认写入正式记录，因此采用与保存层医学时间均未覆盖；没有独立模型 source span，因此候选和采用来源未覆盖；正式记录的 source anchor 可定位全部四条 Gold 原文片段。原始候选、决定、记录回读和字段映射见 [.planning-work/parallel/window3/T55-voice-adoption-roundtrip-evidence.json](../../.planning-work/parallel/window3/T55-voice-adoption-roundtrip-evidence.json)。

## 取消与撤权

独立 `ready` VoiceInput `f2127e78-e262-48c8-8f6e-a15d00a17710` 由 API 创建 HTTP 201，取消 HTTP 200，GET HTTP 200。数据库回读状态 `cancelled`，无关联 Task 或新增 RecordVersion。证据位于 [.planning-work/parallel/window3/T55-voice-cancel-evidence.json](../../.planning-work/parallel/window3/T55-voice-cancel-evidence.json)。

授权撤销场景在 `sjia_simulation:5433` 单个事务内新建授权 `1ee6364a-6d59-4bf3-b608-85b55de716d1` 与 VoiceInput `9d3270df-7db6-49e7-a414-dc11d96f766c`，调用真实撤销 API 后，实际 `prepare_voice_processing` 服务及 process API 均以 `PURPOSE_AUTHORIZATION_UNAVAILABLE` 拒绝，API 返回 HTTP 403。VoiceInput 保持 `ready`，Task、Outbox Event 和 TaskAttempt 新增数均为 0。事务回滚后新授权和 VoiceInput 不存在，相关全局行数与运行前相同；原共享授权 `4d484dc2-0f54-408d-b03a-84d6d4d6bde0` 保持未撤销。证据 `.planning-work/parallel/window3/T55-authorization-revocation-service-roundtrip.json` SHA256 为 `eb925cf99f64926a84f521440301d0ba896a7319bd676de36cdb229b2a9f6774`，程序已保存并重新读取。

## T55/T41 语音辅助更正回归准备

主窗口已完成页面字段筛选：`ConfirmedVoice.description` 仅包含本人接受或修改的症状与说明；description 为空时更正表单保留现有说明；医学时间只写入本人选定字段。`persistRecord=false` 路径取消 VoiceInput 后重新读取 `cancelled`，然后才把选定值交给表单。

专用执行工具 `tests/integration/window3_voice_correction_execute.py` 在 `sjia_simulation:sjia:5433` 新建 VoiceInput `bc746922-fc06-499d-a8e7-3ce05f4b4cf2`、request `c27564d0-5630-42e3-82f3-4f5c116a119d`。创建 API HTTP 201、创建后读取 HTTP 200、process HTTP 202、模型执行后读取 HTTP 200。准确 Task `ae3189bb-6c44-49ae-85c5-425f81a111de` 与单一 Event `8fcac6e7-de3f-43f8-adcb-1c13b1da3e24` 均由数据库重读确认；仅直接执行此 Event。实际 Provider request `0217915403424833749365b55318faa75db47eb4778acb3b69b94`、模型 `deepseek-v4-flash-ga-260731`。Attempt `80c24400-a6ee-4d56-b0db-f1d19b16b0de` 完成，ConsumerReceipt `7fcc301f-da63-4201-88c8-3871318ba0f0` 已保存并读取。Usage 为 prompt/completion `437/774` tokens；Provider duration `8952.064` ms，Attempt 时长 `16.759262` 秒。按准确 source revision `d81af98c-9f90-4230-acb1-174e6e2b8cec` 计算费用 `0.001985 CNY`，外部账单 `NOT_RUN`。输入 SHA256 `99f5a5ade68e263d646e759234cb654916369fefe067369a25658e3241012819`；模型候选及 API 读回见 `T55-correction-voice-execution.json`，SHA256 `4ef722047defe9a2714af9c1444bd9cee28e97c6f2480b4b1ddaa1aa4b7551dd`，其 RecordVersion 数量前后均为 1。

非视觉 H5/API 回归脚本通过模拟权限核对，并复用 VoiceInput `bc746922-fc06-499d-a8e7-3ce05f4b4cf2` 的现有候选完成确认。API 重新读取状态 `cancelled`，没有保存候选 RecordVersion；症状 `unknown`、发生时间及补充说明 `excluded` 未改变 `observation_note`。随后将既有 Record `7a673e4a-7707-4474-a749-4880e32ebb89` 从版本 1 更正为版本 2，RecordVersion `7d2ad3b2-f040-409e-b47a-fac2883a9540`。API 重新读取到新版本与可读取的旧版本；Record 数量前后均为 1，浏览器错误数为 0。没有再次调用模型。完整报告 `T55-correction-browser-roundtrip.json` SHA256 `61c4bd7c75fc5ececd30de7e231025f27b549ffd3545ee7ab43c43ced30cd11a`，状态 `SIMULATION_VOICE_CORRECTION_ROUNDTRIP_PASS`。临床效能、真实家庭理解、真实音频与视觉验收保持 `NOT_RUN`。

## 既有 OCR 与历史语音

QA03 十次 OCR 历史调用共 203.603 秒，prompt/completion `14,920/11,035` tokens；缺少每次价格依据，费用 `NOT_MEASURED`。11 个候选字段名称、数值、单位各 11 正确；比较符 5 正确、6 错误；否定、医学时间、语境、字段来源各 11 未覆盖。另一次模拟 OCR 有 Ferritin、CRP 两字段；名称、比较符、数值、单位、医学时间和来源各 2 正确，否定与语境各 2 未覆盖。耗时 13.894089 秒；按模拟配置计价 `0.002638 CNY`，不代表实际账单。

QA03 五十次语音历史调用累计 307.619 秒，其中 49 条有候选、1 条截断。数值与单位各 49 正确、1 未覆盖；名称、比较符、否定、医学时间、语境和字段级来源各 50 未覆盖；费用 `NOT_MEASURED`。本轮没有重新请求历史模型。

## 命令与结果

Python 3.12.7、Django 5.2.17、Node.js 24.13.0、npm 11.6.2、Vite 8.3.1。`py_compile` 覆盖 `tests/integration/window3_voice_correction_execute.py` 和既有 T55 工具，退出码 0；H5 专用脚本 `node --check guardian-h5/scripts/check-window3-voice-correction-roundtrip.mjs` 退出码 0。专用执行工具实际运行一条模拟模型任务并保存、重新读取 Task、Event、Attempt、ConsumerReceipt 和 VoiceInput。H5 浏览器脚本连接 Vite 5187 与模拟 API 8001，状态 `SIMULATION_VOICE_CORRECTION_ROUNDTRIP_PASS`；同一 VoiceInput 已取消重读，unknown/excluded 字段过滤、Record 版本保存和旧版本读取均通过。没有运行视觉检查。

质量命令还读取取消证据：`backend/.venv/bin/python tests/clinical/window3_voice_evaluation.py --evidence .planning-work/parallel/window3/T55-voice-adoption-roundtrip-evidence.json --process-response .planning-work/parallel/window3/T55-voice-process-api-response-ready.json --cancel-evidence .planning-work/parallel/window3/T55-voice-cancel-evidence.json`。结果 JSON SHA256 `aba3d2c20c6eed4faf5bd284f322d9866f3c9ff2b5b0ad783270eebc7c2ca8e2`。RecordVersion API 与只读数据库准确字段、五项决定和来源重读另见主窗口 `voice-adoption-strict-readback.json`，SHA256 `5970d3d462db539e903e3eaadf31c07f7f9576b363ca1f85100ec27e7ff2577e`。语音录音、真实临床答案复核、真实家庭理解、实际 Provider 账单、手机操作与视觉验收均为 `NOT_RUN`。
