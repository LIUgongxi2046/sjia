# PRD 2.0 实施与验证依据

doc_id：SJIA-PRD2-DELIVERY；status：CURRENT；last_verified：2026-10-21（Asia/Shanghai）。适用源码：`f05dbcf106e70d890a5574385fd63b3ddd502146`。受众：产品、研发、测试与交接人员。维护责任：需求、代码、权限及验证变化时由当前执行者同步关联资料。

## 需求与状态来源

[PRD](../../sJIA合并MAS预警平台PRD.md)维护 FR-01—FR-22、对应 AC／GRAC 及非功能要求；[交付记录](../../planning/delivery-ledger.json)为 T01—T60 的唯一状态来源。[开发索引](../development/index.md)登记主记录与 identifier；专项检查引用各自 QA 记录。

2026-10-21 读取交付记录：30 项承诺、60 项任务均保留范围，60 项任务为 IMPLEMENTED；验收 PASS 2 项、STALE 9 项、NOT_RUN 49 项。完整剩余项与环境见[当前交接](../../CURRENT-DELIVERY-20261021.md)。本次核实源码与文档，历史业务证据保留原始日期、输入及环境。

系统职责由 [HLD](../../sJIA合并MAS预警平台HLD.md)描述；当前实现与需求之间的差距继续引用原任务，文档更新不关闭业务验收。

## 业务、页面、接口与实体

| 业务与验收 | 页面或调用入口 | API 与权限 | 实现、查询与保存 | 最终使用方及验证 |
| --- | --- | --- | --- | --- |
| 登录与访问；FR-01、FR-09、AC-H5-05 | `/login`、`/associations`、`/support/*` | `/api/session`、`/api/v2/support/session`；家庭关联、支持受众与岗位分别核查 | `identity/session.py`、`identity/support_session.py`、`authorization/policy.py`；Session 与当前授权读取 | `AccessProvider`、`SupportApp`、`UI2ReadState`；[QA26](../development/QA26-support-access-recovery.md)、[QA27](../development/QA27-real-clinical-access-scope.md) |
| 本人候选确认；FR-02、FR-13、GRAC-01 | 三类登记、`/archives/confirm` | v2 逐事实与采用接口；准确用途副本、本人字段与写入权限 | `recognition`、`records`、`archives`；RecordVersion／RecordField 与 ArchiveFactVersion 保存，专业状态独立 | 家庭记录、时间轴、后续专业核对；[T41](../development/T41.md)、[QA12](../development/QA12-real-ocr-validation.md) |
| 批次与文书；FR-14、FR-15、GRAC-02／03 | `/archives/batches`、`/archives/grouping`、`/archives/regroup` | v2 文书与批次接口；当前原件、用途、配额与请求身份 | `archives/documents.py`、`archives/grouping.py`、`requests/archive_documents.py`；文书与事实版本、页序、原请求及回执保存 | 档案目录、准确来源与受控评估；[T42](../development/T42.md)、[T43](../development/T43.md)、[QA22](../development/QA22-real-native-pdf-document.md) |
| 重复资料；FR-16、GRAC-04 | `/archives/duplicates` | v2 archive-relations；本人文件关系与专业采样分别授权 | `archives/relations.py`、`archives/relation_queries.py`；保存关系新版本与解除历史，来源完整保留 | 档案关系与评估来源；[T44](../development/T44.md)、[QA23](../development/QA23-real-duplicate-files.md)、[QA24](../development/QA24-real-relation-read-performance.md) |
| 目录与原件；FR-17、GRAC-05 | `/archives`、`/archives/source` | v2 archives/search、archive-document-versions/{version_id}/source；原 files/{file_id}/content | `views_archive_delta.archive_document_version_source` 固定读取快照并核查历史与原文；`views_family3.file_content` 核查完整原件权限及处置，再读取准确范围 | `useDocumentSource` 与原文页面；[T45](../development/T45.md)、[QA16](../development/QA16-document-source-entry.md)、[QA29](../development/QA29-real-original-range-reads.md) |
| 新增内容整理；FR-18、GRAC-06 | `/archives/changes` | v2 archive-batches/{batch_id}/delta-summaries、archive-delta-summaries/{delta_id}；本人身份、当前来源与可见字段 | `summaries/delta.py`、`requests/archive_delta.py`；保存准确输入、Task、原回执和 DeltaSummary；变更后保留历史并核查 validity | 本人整理正文与准确来源；[T46](../development/T46.md)、[QA19](../development/QA19-real-archive-delta.md) |
| 观察范围；FR-19、GRAC-07 | `/records/period` | v2 observation-period 与 versions；明确可见字段、本人权限及准确保留策略 | `records/periods.py`、`requests/observation_periods.py`；读取医学时间，保存 ObservationPeriodVersion 和依赖失效 | 观察资料与后续采样判断；[T47](../development/T47.md)、[QA20](../development/QA20-real-observation-period.md)、[QA21](../development/QA21-real-observation-period-save.md) |
| 专业核对与评估；AC-04／05／10、AC-DYN | 支持端核对、静态与动态页面 | v2 support/verification、assessment、dynamic 相关接口；专业岗位及准确字段资格 | `verification`、`assessment`、`alerting`；核对、current／replay、来源、任务与采用分别执行 | 当前家庭提示与独立 replay；[T49](../development/T49.md)、[T50](../development/T50.md)、[QA27](../development/QA27-real-clinical-access-scope.md) |
| 真实评价；FR-20、GRAC-08、AC-EVAL-01 | 支持端评价目录与运行 | v2 evaluation-datasets／evaluation-runs；准确用途、固定集合与独立标签 | `case_details/evaluation_inventory.py` 与评价领域；来源清单和临床统计分别管理 | T55 格式评价与 T59 临床效果；[T48](../development/T48.md)、[T59](../development/T59.md)、[QA25](../development/QA25-real-evaluation-source-inventory.md) |
| 用途与内容处置；FR-21、GRAC-09 | `/account/data-use` 与支持用途页面 | purpose-copies、content-disposition 与 disposition 接口；准确用途、复核、批准与原件权限 | `files/purpose.py`、`retention`；独立副本、复核、处置计划与逐项回执 | 获准识别、评价、家庭资料与恢复采用；[T40](../development/T40.md)、[处置恢复](../../deploy/content-disposition-recovery.md) |
| 提醒与发布；FR-22、GRAC-10、AC-12 | `/account/reminders`、`/alerts`、`/summaries` 与支持发布页面 | 原生通知、本人资料提醒与发布接口；本人目标、签名许可、当前内容与联系资格 | `notifications/material_scheduler.py`、`notifications/native.py`、`family_content`；设置、实际送达、阅读与发布各自保存 | 家庭提醒、提示与摘要；[T51](../development/T51.md)、[T52](../development/T52.md)、[T53](../development/T53.md)、[QA15](../development/QA15-real-material-reminders.md) |

## 资料语义与采用条件

每个事实版本通过 `field_id` 引用准确 RecordField。原文、数值、限定符、单位、医学时间、平台取得时间、专业核对与本人确认分别保存。未知时间和未核对状态继续保留，查询范围和上传时间不补充缺失的医学时间。

原件与用途副本固定准确版本、checksum、原页、用途、环境和复核。家庭读取、外部识别与专业核对使用各自权限。模型调用只产生候选，正式记录由本人确认或获准专业操作保存；分类、MS、HLH 与动态规则使用确定性计算，发布仍需当前配置及内容资格。

请求保存固定 operation、target、正文、request ID 与 generation。响应不明时查询原请求，任务和最终资源分别重新读取。外部处理结果未知时保留计量与并发预留；历史依据失效后停止作为当前结果使用。

## 设计与实际验证

设计资产由 [数据规范](../../data-design/v2-data-specification.md)、[后端服务行为](../../backend-design/v2-service-behavior.md)、[预警编排](../../alert-design/v2-orchestration.md)、[前端交互](../../frontend-design/v2-interaction.md)、[39 页清单](../../ui-delivery/v2/screen-design-map.csv)和[状态采用表](../../frontend-design/v2-state-adoption.csv)维护。

本地模拟开发的既有结果按 [T01—T60 主记录](../development/index.md)各自日期和环境查询。当前真实专项采用获准真实资料；63 份原件、290 个数据库页面和 83 项本人保存事实的证据见 QA12、QA25、QA29。文件数量、资料分组与患者分母分别登记。临床专业核对和独立答案尚未形成完整证据，临床指标保持 NOT_MEASURED。

真实手机、微信、音频、完整适用状态、视觉、正式容量、对象存储与生产恢复继续执行原验收条件。文档结构、DOM、类型检查、构建和目标版本空库迁移只证明其明确范围。准确当前待办见[交接文件](../../CURRENT-DELIVERY-20261021.md)，本次检查见 [DOCS01](../development/DOCS01-current-documentation.md)。
