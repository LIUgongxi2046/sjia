# T48 共同定义与接口集成

日期：2026-10-09。开发 `IMPLEMENTED`，V48 `NOT_RUN`；当前范围与证据见 [主记录](T48.md)。

## 输入与准确版本

DatasetInput 以 dataset_id、expected_version 明确创建与更新。DatasetView 返回准确 version_id、version、checksum、来源证明、统计覆盖、患儿分组及独立标签。DatasetArtifactProof 绑定 SourceRevision、SourceRegistry、实际摘要、登记时间和保留策略。未知统计保持 null；完整空集合需要范围证据。

EvaluationIntentInput、EvaluationRetryInput、EvaluationRequestLookup 与严格后端输入一致。RequestView.dataset_input 保存原正文，恢复仍核验准确受众、目标、当前主体、版本和权限。EvaluationRunIntentInput 固定集合、分组、规则、程序、独立答案与运行模式；RequestView.evaluation_run_input 保存准确运行正文。

## 接口与结果

| operation_id | 方法与路径 |
| --- | --- |
| dataset_search | POST /api/v2/support/datasets/search |
| dataset_create | POST /api/v2/support/datasets |
| dataset_read | GET /api/v2/support/datasets/{dataset_id} |
| dataset_partition | POST /api/v2/support/datasets/{dataset_id}/partitions |
| dataset_label | POST /api/v2/support/datasets/{dataset_id}/labels |
| dataset_freeze | POST /api/v2/support/datasets/{dataset_id}/freeze |
| dataset_run_create | POST /api/v2/support/datasets/{dataset_id}/runs |
| dataset_run_read | GET /api/v2/support/evaluation-runs/{run_id} |
| dataset_run_denominators | GET /api/v2/support/evaluation-runs/{run_id}/denominators |
| dataset_run_results | GET /api/v2/support/evaluation-runs/{run_id}/results |

运行创建保存 EvaluationRun、assessment Task、Outbox、回执及审计。实际消费者核验事件、执行权、准确固定输入与当前许可；完成结果包含严格九类 EvaluationUnitResult、单位身份、证据、局限、输入摘要及准确结果引用。读取核验保存结果与完成尝试摘要。临床指标由 T59 计算，当前临床范围明确 `clinical_metrics_not_run`。

当前 runtime 资格不足返回503；资料不存在返回404；权限拒绝返回403；版本或来源冲突返回409；无效输入返回422。错误响应禁止缓存，字段错误不携带原始输入。workflow_evaluation 为独立用途；增量迁移 archives.0005_workflow_evaluation 已在实际备份恢复及约束检查中通过，111 张原业务表与29份原件保持原内容。

## 页面与核验

UI2-37 通过 SupportApp 接入 DatasetPage、DatasetEditor、EvaluationRunPanel 与真实客户端。保存后读取准确版本；准备失败保留原正文和原 request_id，重新准备后保存一次；结果未知查询原请求。页面显示固定规则证明、九类结果、单位及引用，键盘能够展开详情。查询达到终止状态或五分钟期限停止，权限变化清理当前内容。

独立模拟运行完成30项业务、10项权限及版本、11项Chrome检查；21项正式Schema与80次实际响应通过。五种宽度、200%文字几何及当前TypeScript、生产构建通过。全部证据保留环境、准确版本及实际响应，不使用视觉工具。真实业务、实际设备、微信及视觉还原继续待验收。
