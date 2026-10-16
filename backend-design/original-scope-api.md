# 原始需求补充接口（2026-10-11）

本文件补充主办方 V1 需求第 4.2、4.3、5 节与现有衍生目录的差距，不表示临床验证或上线批准。API 路径以下均含 `/api` 前缀。

## 家属选择已批准方案

- `GET /api/patients/{patient_id}/alert-presets`：`alert_preset_directory`，200；响应由 `alert-preset-view.schema.json` 定义。仅允许具有 `select_alert_preset` 的有效家属关联，当前用途为 `family_observation`。不支持任意查询参数。
- `POST /api/patients/{patient_id}/alert-preset-selection`：`alert_preset_select`，200 保存回执。请求由 `alert-preset-selection.schema.json` 定义，只接受 assignment ID/checksum、预期选择版本与已批准 schedule ID，不接受家属提交医学阈值、数字权重或核心指标定义。
- 选择复用真实写入意图：`POST /api/write-intents`，operation 为 `alert_preset_select`，schema version 为 `1.0.0`；提交携带 `X-Write-Intent`、`Idempotency-Key`、`X-Intent-Generation`。正文与原请求保持冻结。
- 原请求可经 `GET /api/requests/{intent_id}` 恢复；丢失意图响应时使用 request ID 与 `lookup=request_id`、准确 operation/target 查询。允许重试时，通过 `POST /api/write-intents/{intent_id}/retry` 提交 `expected_generation`。不能因超时就生成另一业务选择。
- 保存回执沿用 `WriteReceipt`，resource type 为 `alert_preset_choice`。选择版本、审计、旧依赖失效与重新评估事件在同一事务内更新。
- 明确区分 `professional_default`、`selected`、`requires_reselection`。默认方案由专业清单明确给出；失效选择不能被前端悄悄替换为另一个方案。
- 专业人员通过现有配置创建、审批、验证与发布流程维护 `patient-alert-presets-1` 清单及 `alert-trigger-2` 规则。家属选择不会产生审批、扩大用途或字段权限；计算入队与执行仍检查配置、发布资格和当前选择。

前端 `/alert-presets` 的补充登记见 `frontend-design/original-scope-routes.csv`，不是旧39页目录中的已有完整设计稿。

## HLH 与权重计算

HLH 通过当前 API v2 支持评估接口加入独立 `kind=hlh`，使用独立输入策略和结果结构，不复用 MS 数值分数。精确输入、规则与结果分别由 `hlh-input-policy.schema.json`、`hlh-2004-rule.schema.json`、`hlh-2004-inputs.schema.json`、`hlh-2004-result.schema.json` 维护。旧 v1 assessment handler 明确拒绝计算并要求 v2；不能将旧 `protocol.schema.json` 的历史简化 DTO 当作新运行契约。

`alert-trigger-2` 增加显式核心/一般分组与加权条件，同时保留旧规则。未知值、覆盖范围与贡献解释须保存，不能将未知医学事实折算为正常。数值权重与阈值需由专业批准版本提供；工程测试和引用论文不构成该产品的临床效果验证。

本说明只登记已实现的契约和治理边界。完整运行、异步竞争、临床适用性与视觉验收结果另记，不在本文件预先标为通过。
