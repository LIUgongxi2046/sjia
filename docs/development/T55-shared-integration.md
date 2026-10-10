# T55 共享语音接口与运行记录

主窗口维护共享 API、Schema、类型、模拟配置和迁移。窗口三维护语音 service、consumer、H5 交互、独立答案及专用业务检查。开发完成与验收通过分别登记于 [交付记录](../../planning/delivery-ledger.json)。T55 已接收为 IMPLEMENTED；实际模型候选、五项本人决定、采用及保存重读完成，真实验收 V55 仍为 NOT_RUN。

## 正式接口

所有接口核验当前家庭 Session、患儿关系及准确用途；写入同时核验 CSRF。确认文字原件与外部识别用途副本分别保存，批准的用途副本才允许进入模型请求。

| 方法与路径 | 输入与输出 | 保存及重新读取 |
| --- | --- | --- |
| GET `/api/v2/patients/{patient_id}/voice-input-options` | OptionsView | 当前 FieldConfig、Provider、双用途授权、本人原件及用途审批状态 |
| POST `/api/v2/patients/{patient_id}/voice-inputs` | CreateInput → VoiceInputView | request_id、确认全文与 SHA256、原件、本人、配置、输入模式同事务保存；HTTP 201 |
| GET `/api/v2/patients/{patient_id}/voice-inputs?request_id={request_id}` | request_id → VoiceInputView | 刷新及响应中断查询原请求；没有新建模型任务 |
| GET `/api/v2/patients/{patient_id}/voice-inputs/{voice_id}` | VoiceInputView | 当前状态、版本、Task、候选、决定、保存版本及 Provider 证明 |
| POST `/api/v2/patients/{patient_id}/voice-inputs/{voice_id}/process` | ProcessInput → VoiceInputView | 固定用途副本、模型、配置、预算预留、Task 与唯一 Outbox；HTTP 202 |
| POST `/api/v2/patients/{patient_id}/voice-inputs/{voice_id}/adopt` | AdoptInput → AdoptionView | 本人逐项决定、WriteIntent、WriteRequest、RecordVersion、原件来源及完成 Task 同事务保存 |
| POST `/api/v2/patients/{patient_id}/voice-inputs/{voice_id}/cancel` | CancelInput → VoiceInputView | 当前版本取消；已开始但结果未知的提供方调用保留未知，不自动重发 |

完整目录见 [voice-api-catalog.csv](../../backend-design/voice-api-catalog.csv)，准确路径见 [urls_v2.py](../../backend/api/urls_v2.py)，完整投影见 [views_voice_inputs.py](../../backend/api/views_voice_inputs.py)。正式定义见 [voice-input.schema.json](../../backend-design/voice-input.schema.json)，AdoptionView 复用 [家庭 RecordView](../../backend/records/search.schema.json)。H5 在 [voice.ts](../../guardian-h5/src/api/voice.ts) 验证完整正文与患儿、原件及保存版本一致。

OriginalOption 返回 not_requested、processing、pending_review、approved、rejected、revoked、disposed、failed 八类用途状态，准确请求、Task、副本标识及拒绝原因。家庭处理按钮仅在 available 时启用；支持端入口使用原件及 external_recognition 用途，重新读取当前审批结果后继续。

本地音频转写使用 `/api/patients/{patient_id}/voice-transcription`、明确来源授权及本地模型资产。音频 SHA256、资产版本及转写收据能够追溯，音频仅进入本地内存处理。实际录音、设备与转写质量未执行，保持 NOT_RUN。模拟运行只使用 SIMULATION_TEXT_ONLY，不能计为录音或临床验收。

## 用途与预算

准备材料和处理产物分别核验实际 FileObject、来源关系、版本、摘要及完整确认文字。已保存的派生输出不改变原件；外部识别和家庭观察采用分别核验当前授权。当前两份模拟文字处理已经实际重新执行，产物与保存副本全文一致，当前用途证明保存重读成功。

保留策略在有效 PostgreSQL 事务内使用 FOR SHARE，两个读取事务允许并发，修改策略的写入锁等待全部读取结束。原件仍具有准确 FOR UPDATE 锁；保留策略更新没有提前取得锁。实际并发检查未修改保留策略内容。

计费账号按 billing_scope_key 汇总 OCR、语音及全部配置版本。已知费用采用实际保存计量；未知费用保持 null，原预留继续占用额度，未知调用继续计入并发。预算总额分别记录已有费用与预留、未知预留明细和本次预留；超预算及超并发拒绝新任务。未知原调用不自动重发，也不登记成功。

## 当前真实检查

| 检查 | 结果与证据 |
| --- | --- |
| 两份确认文字实际处理与保存副本全文核验 | PASS；`.planning-work/coordination/root/text-processing-current.json` |
| 保留策略并发读取、写入等待、内容保持及报告重读 | PASS；`.planning-work/coordination/root/retention-policy-shared.json` |
| 两条未知调用原证明保持、预留与并发计数、超额拒绝 | PASS；`.planning-work/coordination/root/provider-budget-reservations.json` |
| 24 项正式语音、采用回执、Session、Task 与 Home Schema 编译 | PASS；`.planning-work/coordination/root/voice-session-schemas.json` |
| 当前 VoiceInput 与用途选项实际 HTTP 200、Schema、全文 SHA256、保存重读 | PASS；`.planning-work/coordination/root/voice-budget-current-http.log` |
| 当前 H5 TypeScript 与生产构建 | PASS；`.planning-work/coordination/root/voice-complete-schema-build.log` |
| 实际模型候选、五项决定、采用及 RecordVersion 重读 | PASS；`.planning-work/coordination/root/voice-adoption-strict-readback.json` 与窗口三往返报告 |
| 独立 ready 输入创建、取消及重新读取 | PASS；`.planning-work/parallel/window3/T55-voice-cancel-evidence.json` |
| 三层字段质量、独立模拟答案、耗时和模拟计价 | SIMULATION_EVALUATED；`.planning-work/parallel/window3/T55-voice-simulation-evaluation.json`；真实账单 NOT_RUN |
| 共享授权撤销后拒绝语音处理 | NOT_RUN；自动审查拒绝持续改变共享授权，授权保持原状 |
| 本次专属授权实际撤销、服务及API拒绝、事务清理 | PASS；`.planning-work/parallel/window3/T55-authorization-revocation-service-roundtrip.json`；既有共享授权保持原状 |
| 语音辅助更正、原Record新版本与未知字段过滤 | PASS；`.planning-work/parallel/window3/T55-correction-browser-roundtrip.json`；实际页面及API，视觉NOT_RUN |
| 临床、家庭参与、录音设备、微信及39页视觉 | NOT_RUN |

共享数据库仅为 sjia_simulation、schema sjia、端口5433。当前 API 工具会话3089、端口8001，不启动共享 Worker 或 Beat，不消费整个队列。当前资格时间为2026-10-09T09:01:27.522321Z，发布证据时间为09:01:32.928549Z；release 为8b44a175-5809-4566-a859-a5d62ca83e6e，validation 为71719f27-ea0d-47c8-8032-4358d8fa91c9。

实际命令使用 `TMPDIR="$PWD/.planning-work/tmp"`。本轮执行 `backend/.venv/bin/python tests/security/root_retention_policy_shared.py`、`backend/.venv/bin/python deploy/refresh_simulation_text_processing.py`、`backend/.venv/bin/python tests/security/root_provider_budget_reservations.py`、`backend/.venv/bin/python deploy/check_simulation_voice_options.py --voice-id 6a85134c-3bdd-4d87-b161-5455d038bc0a` 均退出码0。H5目录执行 `node scripts/check-shared-voice-session-schemas.mjs` 和 `npm run build` 均退出码0。检查没有主动视觉操作，不能证明一比一视觉验收通过。
