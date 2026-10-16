# 当前证据索引与确证勘误（2026-10-13）

任务：SJIA-C01-002。状态：文档候选，待集成审查；本文件是下列四项当前事实的权威入口，不是全项目验收签收。基于 1761 文件冻结源码清单 SHA-256 `0439f5d6f344d71e799930bd3d6db9cfbe922a5d62e6d540422ed1f6f65ff2e3`；原冻结源码不变。

本轮只做文档和静态一致性检查，没有运行产品测试、数据库、服务或 Provider，也没有切换运行实例。其他独立候选的工具检查、读取修复及启动检查结果没有集成进此源码，不能从其通过数推定本候选的运行状态。

## 1. 阅读顺序与分母

1. 当前 API 追溯看[21 条补链表](CURRENT-API-TRACEABILITY-20261013.csv)。它补充既有 [planning/coverage.csv](../../planning/coverage.csv)，不删除或改写其 260 行。
2. [总 API 目录](../../backend-design/api-catalog.csv)有 162 个操作，旧 coverage 的 API 标识覆盖其中 141 个，差集恰为本次 21 个。它们是已存在操作的文档漏链，不是新开发的接口。
3. [V2 目录](../../backend-design/v2-api-catalog.csv)的 109 个操作与总目录重叠，不相加；旧规划的 60 张工单、260 行关系也不是通过项数量。
4. [历史开发矩阵](DEVELOPMENT_MATRIX.md)、[机器矩阵](DEVELOPMENT_MATRIX.json)、[阶段修复报告](../../REPAIR_REPORT.md)和[原始需求增量](ORIGINAL-REQUIREMENT-DELTA-20261011.md)保留原始日期和正文；读当前状态时先应用本索引的勘误。

2026-10-13 C01 完整逐票/原始输入矩阵单独交付，以下来源采用文件名与 SHA-256 定位，不伪造仓库内链接。原始产品材料、患者正文、审批资料和运行日志不随本修订打包。

|来源|SHA-256|范围|
|---|---|---|
|工单覆盖_60.json|`10f86736ff011e5a5a09bfb08641f69850644b7d839ceaaf99ef00b8973d9aa0`|60 张既有工单的静态路径、测试入口和限制|
|原始需求覆盖_O01-O15.json|`557c0f0a073760850c2d93eef73d41d16dd063bb4057d811f6e9a3047fa0a12a`|原始需求主题的当前证据与未验证项|
|当前接口增量_21.json|`43ed20f1d631d3e82be39dcdf34d8be7d98841bb5fd3275de232ee1a9fd39850`|本次补链输入；本修订继续解析其中 4 个尚未定位的共用处理器|
|验证证据索引.json|`70ba95c87aeb65c847592048aeb3479b0cb94cea96d8eb0022a49eb62e1c171d`|已交付证据的包内位置和哈希|

## 2. 确证勘误

### 2.1 历史矩阵与 T26

2026-10-11 矩阵的 54/5/T26 汇总、735 项及 T26 的 executor/read/restore 未实现结论属于历史记录，不能继续代表冻结源码的当前状态。也不简单换成“60 项全部完成”。

当前代码已具备政策门控的逻辑内容处置：[预览](../../backend/retention/content_engine.py#L257)、[批准](../../backend/retention/content_engine.py#L351)、[执行](../../backend/retention/content_engine.py#L513)，以及[家庭申请](../../backend/retention/content_family.py#L83)、[固定适配器](../../backend/retention/content_registry.py#L45)和[恢复重放](../../backend/retention/content_recovery.py#L55)、[恢复验证](../../backend/retention/content_recovery.py#L87)、[恢复准入](../../backend/retention/content_recovery.py#L110)。

这些是静态路径证据。政策模式、目标版本、保留条件、批准和准入仍须满足；[逻辑擦除结果](../../backend/retention/content_engine.py#L560)保持 `all_copies_erased=False`，[到期处理状态](../../backend/retention/content_lifecycle.py#L60)仍要求备份及重试窗口证明。不能承诺物理数据库/WAL、备份、真实 Provider 副本已全部擦除。

存在的定向测试入口为 [backend/tests/test_content_disposition_engine.py](../../backend/tests/test_content_disposition_engine.py)、[backend/tests/test_family_content_disposition.py](../../backend/tests/test_family_content_disposition.py)、[backend/tests/test_content_restore_admission.py](../../backend/tests/test_content_restore_admission.py) 和 [guardian-h5/scripts/check-family-content-disposition-dom.mjs](../../guardian-h5/scripts/check-family-content-disposition-dom.mjs)。本轮未运行这些测试；历史全量与前端脚本证据见第 3 节，不能从整套数字反推每个测试方法均已执行。

### 2.2 模型身份与工作流绑定

[REPAIR_REPORT.md](../../REPAIR_REPORT.md) 的历史“模型别名映射”支持表述错误。[assert_authorized_result](../../backend/recognition/invocation.py#L98)要求 `result['model'] == provider.payload['model_version']`；不匹配抛出 `MODEL_RETURNED_IDENTITY_UNAUTHORIZED`。调用证据的 `model_authorized` 同样只按严格相等计算。

[recognition README](../../backend/recognition/README.md)中的 `workflow_field_config_ids` 是 report/therapy 工作流到字段配置的显式绑定，仍受配置资格和批准约束；它不是模型别名授权。不同返回标识、部署 ID 和家族前缀不会被隐式接受。任何别名能力需另行评审契约；本修订没有实现或授权它。

### 2.3 O15 实现目录与真实效果

旧增量文件的 `backend/evaluations` 是失效引用，正确软件路径在 `backend/case_details`：[评价方案](../../backend/case_details/evaluation_protocol.py#L13)、[源清单检查](../../backend/case_details/evaluation_inventory.py#L61)、[版本化评价任务](../../backend/case_details/evaluation_runs.py#L22)、[受控执行](../../backend/case_details/evaluation_execution.py#L48)、[指标分派](../../backend/case_details/clinical_metrics.py#L325)、[临床捕获采用](../../backend/case_details/clinical_adoption.py#L227)。

[采用结果](../../backend/case_details/clinical_adoption.py#L277)仍是 `clinical_validation=False`。[T59](T59.md) 和 [T32](T32.md) 的真实效能、Gold、家庭理解及设备验收缺口仍保留。软件路径或合成测试不证明临床漏报/误报、早诊早治或家庭行动效果；本次没有修改医学规则、阈值或临床结论。

## 3. 每个执行数字的版本与证据边界

本节引用已保存结果，不表示 2026-10-13 重跑。下列证据路径均相对已有 2026-10-12 候选交付包；文件 SHA-256 在下表标识。摘要未附的原日志不会被补造。

|记录|准确版本与分母|不能推出的结论|
|---|---|---|
|历史矩阵/报告 735 项|报告日期 2026-10-11；报告基线 `4767c6b50f3582738287a202c45a0b51ebf823a9`；732 通过、3 跳过。该基线不是本次已独立核验的精确执行清单，保留为历史报告声明|不是当前 1761 版重新执行，也不能与后来切片相加|
|历史后端 1227 项|执行 manifest `53c41a62c34940f5ef9ba915f92d43d3a577c845035c1d16da6919f0ab4348ba`；2026-10-12；1224 通过、3 个外部真实病例跳过；摘要含原日志哈希|后端/设计/部署 878 文件未变是复用边界，不是本候选重新全量运行；跳过项不是通过|
|候选前端 65 次|冻结 manifest `0439f5d6f344d71e799930bd3d6db9cfbe922a5d62e6d540422ed1f6f65ff2e3`；50 个断言脚本 + 1 次 helper 导入 + 12 次额外时区执行 + 类型检查/构建 2 次；全部 exit code 0|不是 65 条浏览器流程，也不是像素或真机验收|
|候选追加工具 11 项|同一 `0439f5d6f344d71e799930bd3d6db9cfbe922a5d62e6d540422ed1f6f65ff2e3`；独立合成 PostgreSQL/Redis 原生测试；11 通过|不是全后端重跑、当前账号已追加资料或真实患者验收|

|交付包内证据位置|SHA-256|
|---|---|
|evidence/historical-round9/FINAL_BACKEND_VERIFICATION.json|`361e62155a75c8648103f7c4fd3dbca0fa6c4853f922076da3fdb4b4a6874305`|
|evidence/frontend/result.json|`920f8216b78a206db2e5bac86da0f974b2705a2e61e23ae72cb0303537fab24e`|
|evidence/native-enrichment-self1-source-proof.json|`4eca28f5019af92014e2688030783dbe443f44d3f1053ccab69a118e23b797f4`|
|evidence/CANDIDATE_SOURCE_VERIFICATION.json|`2ede338f0769511b05cdff3f597ac0542ae20d73646da917a37f1d9d570cd08b`|
|VERIFICATION_SUMMARY.json|`ea8b84e9c3649799d1e4b26e490fbf92bb99035ebb0b99e46c02d0b838a17642`|

## 4. 21 条当前 API 补链导航

路径沿用总 API 目录（未含 `/api` 前缀）；标准实际 HTTP 路径和路由/方法定位见 [CSV](CURRENT-API-TRACEABILITY-20261013.csv)。总目录保留旧 FR 分类，V2 的 FR 分类另列，不能静默相互覆盖。工单绑定来自 V2 显式字段或 C01 的静态范围核对，不等于该接口已逐项执行验收。

|operation_id|方法与目录路径|工单|实现入口|
|---|---|---|---|
|application_review|GET `/support/relation-applications/{application_id}`|T09|[application_review](../../backend/api/views_support.py#L33)|
|correction_review_queue|GET `/support/correction-requests`|T11;T54|[correction_review_queue](../../backend/api/views_corrections.py#L20)|
|correction_review_read|GET `/support/correction-requests/{correction_request_id}`|T11;T54|[correction_review_read](../../backend/api/views_corrections.py#L26)|
|task_reconcile_read|GET `/support/reconciliation-tasks/{task_id}`|T06|[task_reconcile_read](../../backend/api/views_support2.py#L136)|
|alert_preset_directory|GET `/patients/{patient_id}/alert-presets`|T20;T21;T22;T49;T51|[alert_preset_directory](../../backend/api/views_alert_presets.py#L19)|
|alert_preset_select|POST `/patients/{patient_id}/alert-preset-selection`|T20;T21;T22;T49;T51|[alert_preset_select](../../backend/api/views_alert_presets.py#L28)|
|family_content_disposition_options|GET `/v2/patients/{patient_id}/content-disposition/options`|T26|[family_content_disposition_options](../../backend/api/views_family_content_disposition.py#L38)|
|family_content_disposition_requests_read|GET `/v2/patients/{patient_id}/content-disposition/requests`|T26|[family_content_disposition_requests](../../backend/api/views_family_content_disposition.py#L69)|
|family_content_disposition_requests_create|POST `/v2/patients/{patient_id}/content-disposition/requests`|T26|[family_content_disposition_requests](../../backend/api/views_family_content_disposition.py#L69)|
|family_content_disposition_read|GET `/v2/content-disposition/requests/{request_id}`|T26|[family_content_disposition_read](../../backend/api/views_family_content_disposition.py#L90)|
|content_disposition_family_requests|GET `/v2/support/patients/{patient_id}/content-disposition/requests`|T26|[content_disposition_family_requests](../../backend/api/views_content_disposition.py#L241)|
|content_disposition_patients|GET `/v2/support/content-disposition/patients`|T26|[content_disposition_patients](../../backend/api/views_content_disposition.py#L124)|
|content_disposition_options|GET `/v2/support/patients/{patient_id}/content-disposition/options`|T26|[content_disposition_options](../../backend/api/views_content_disposition.py#L148)|
|content_disposition_history|GET `/v2/support/patients/{patient_id}/content-disposition/history`|T26|[content_disposition_history](../../backend/api/views_content_disposition.py#L200)|
|content_disposition_review_read|GET `/v2/support/content-disposition/plans/{plan_id}/review`|T26|[content_disposition_review](../../backend/api/views_content_disposition.py#L227)|
|content_disposition_review_record|POST `/v2/support/content-disposition/plans/{plan_id}/review`|T26|[content_disposition_review](../../backend/api/views_content_disposition.py#L227)|
|content_disposition_catalog|GET `/v2/support/content-disposition/catalog`|T26|[content_disposition_catalog](../../backend/api/views_content_disposition.py#L43)|
|content_disposition_preview|POST `/v2/support/patients/{patient_id}/content-disposition/plans`|T26|[content_disposition_preview](../../backend/api/views_content_disposition.py#L60)|
|content_disposition_read|GET `/v2/support/content-disposition/plans/{plan_id}`|T26|[content_disposition_read](../../backend/api/views_content_disposition.py#L71)|
|content_disposition_approve|POST `/v2/support/content-disposition/plans/{plan_id}/approve`|T26|[content_disposition_approve](../../backend/api/views_content_disposition.py#L80)|
|content_disposition_execute|POST `/v2/support/content-disposition/plans/{plan_id}/execute`|T26|[content_disposition_execute](../../backend/api/views_content_disposition.py#L89)|

两组共用处理器已沿路由和 `api_view` 方法解析，不能假定 operation_id 等于函数名：家庭 requests 的 GET/POST 都由 `family_content_disposition_requests` 分派；支持端 review 的 GET/POST 都由 `content_disposition_review` 分派。这补齐了原增量表的 4 个待定位项。

## 5. 本修订的完成边界

允许宣称完成：本候选中的历史范围标记、模型身份说明更正、O15 路径勘误和 21 个操作的静态补链；配套静态校验另记录基线哈希、路由/方法、链接、历史内容保全和补丁应用一致性。

不得宣称完成：C01 全部复合验收条件、产品功能/安全验收、真实账号读写、C03 现场启动、持久保存、真实 Provider 联调、视觉或临床效果。其他候选仍需独立审查、组合验证及明确运行采用；本次没有任何运行采用。
