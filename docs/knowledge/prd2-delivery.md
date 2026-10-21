# PRD 2.0 实施与验证依据

doc_id：SJIA-PRD2-DELIVERY；status：CURRENT；last_verified：2026-10-21（Asia/Shanghai）。代码包含远程 `b094448` 的全部改动及当前文档。受众：产品、研发、测试与交接人员。维护责任：需求、代码、权限及验证变化时由当前执行者同步关联资料。

测试账号、两个模拟患儿、历史时间与实际计算提示见 [QA63](../development/QA63-local-test-account.md)、[QA65](../development/QA65-complete-simulation-account.md)及 [QA68](../development/QA68-historical-data-calculated-alerts.md)。本次用户授权提交测试资料，准确导出范围见[测试资料说明](../../test-data/README.md)。

## 设计、存储与升级的专项依据

以下专项说明保留各自执行日期及环境，当前源码与运行身份见[交接文件](../../CURRENT-DELIVERY-20261021.md)。

2026-10-20的当前系统设计接收见[T34](../development/T34.md)。独立分支codex/current-design-reception以候选应用代码0e6c79c为依据，全部22项FR拥有明确产出、持久位置、使用方及后续任务；135项实际symbol和44项代表性持久字段通过Python AST及Rolldown核验。D01—D10分别维护已知范围、必要未知、受影响动作和责任，完整39页及328项状态保持。原main的f406制品、Python3.12.7／PostgreSQL18.4及102项迁移，与独立候选c65、Python3.13.13／PostgreSQL17.11及105项迁移分别通过只读身份检查。设计接收、实际业务、完整字段传播、临床、设备和视觉分别按原任务验收；QA44运行切换仍待明确批准。

2026-10-19的现有资料升级见[QA43](../development/QA43-existing-data-upgrade.md)。主项目全部135张表、419个文件对象共2135341897字节及1429个证据文件完成一致快照和独立复制，PostgreSQL18.4与17.11两处目标分别完整恢复、执行准确三项迁移至105项并通过重复执行。全部旧业务值和框架旧记录保持，ORM状态一致，两处各自63份原件完整读取968528634字节通过；专属PostgreSQL18正常停止，来源、原服务和未知OCR预留保持。当前修改继续保存在开发分支，main集成尚未完成。

2026-10-19的独立恢复运行采用及完整文件响应见[QA42](../development/QA42-restored-runtime-adoption.md)。当前制品c65ed4d5b602906e7a2013e9704237d1164fa9db8ca16cf0bc0bc1a65e32ba93完成独立软件配置与正式CLI采用，21项HTTPS身份、63份原件及189处Range比较通过。真实HTTP、Outbox与Worker完成准确技术副本处置，双端读取一致；本地和S3的完整文件响应均通过摘要、资源关闭及内存范围验证。专项API、Worker、Redis与SSH已经停止，原有网络规则、六处数据库和主项目服务保持。

2026-10-19的独立S3配置、HTTP与处置队列见[QA41](../development/QA41-s3-runtime-http-queue.md)。当前制品通过实际配置登记、软件批准及API／Worker运行采用，20项HTTPS认证检查通过；家庭申请、支持批准、真实Outbox、Worker、准确版本删除、撤权拒绝和重新授权重试均通过，两端读取保持各自任务范围。两份技术副本已处置，63份原件完整读取968528634字节，原项目事实及费用预留保持，新增模型调用为零。修改保存在开发分支，原main继续使用已发布版本。

2026-10-19的S3操作入口见[QA38](../development/QA38-s3-operator-recovery.md)，实际Linux隔离见[QA39](../development/QA39-linux-recovery-isolation.md)。[QA40](../development/QA40-s3-restore-lineage.md)完成第一版和第二版备份的完整当前CLI恢复，来源在整个操作期间持有SQL SHARE锁与独立journal锁。旧结构明确升级至105项迁移，第二版保留完整父代历史；两处目标各自63份原件共968528634字节完整读取、189处范围比较、四项内容证明及两个重放进程通过，第一代19项、第二代22项SQL拒绝通过。六处实际数据库、原项目83项事实、249项事实版本、两项未知OCR与2 CNY预留保持。QA38已完成两种格式共37项当前CLI拒绝，QA42已完成第二版完整CLI目标的独立软件运行采用；完整Local流程继续归属QA38。

2026-10-19的S3备份与独立恢复见[QA36](../development/QA36-s3-backup-restore.md)及[恢复规范](../../backend-design/s3-backup-recovery.md)。不同数据库、物理集群及binding完成真实dump、64份对象复制、准确缺失和四项处置重放；备份后删除与备份后新增对象分别通过，重复执行及两个实际进程保持epoch。目标63份原件完整读取968528634字节、189处范围比较、十五项SQL拒绝、九份Schema及十四项结构拒绝通过。当前完整CLI恢复由QA40维护，独立S3环境的HTTP和队列由QA41维护；恢复环境运行采用、完整Local流程、设备和临床继续保留完整验收条件。两笔未知OCR及2CNY预留保持。

2026-10-19的S3内容处置检查见[QA35](../development/QA35-s3-content-disposition.md)及[准确清单规范](../../backend-design/s3-content-disposition.md)。政策明确允许的文件用途、准确S3版本清单、实际批准、独立journal、真实删除与凭证已经贯通，十五项拒绝通过；本地真实字节副本完成原有流程，正式前端正确读取保存结果并拒绝两项引用不一致输入。63份原件保持，当前完整恢复见QA40，独立S3配置、HTTP及队列验收见QA41，恢复环境独立采用继续归属QA38。

2026-10-19 09:12的应用存储选择检查见[QA34](../development/QA34-storage-selection.md)及[运行身份规范](../../backend-design/s3-storage-selection.md)。现有全局文件服务接入明确的local或s3配置，运行声明保存准确物理、访问、控制目录及私有配置身份；真实63份原件的范围读取、实际业务读取、技术副本写入与准确删除、两项正常流程和十四项拒绝通过。382份来源元数据与103项迁移保持，完整Runtime采用、内容清除、S3恢复及实际队列仍需验证。用户暂时跳过两笔Ark供应商核对，2CNY及两个并发预留继续保持。

2026-10-19 08:16的真实业务对象与S3处置接入见[QA33](../development/QA33-s3-business-disposition.md)及[后端规范](../../backend-design/s3-disposition-storage.md)。五个已授权资料分组的382份必要元数据准确保存重读；63份原件按实际业务键保存私有S3，批准及任务字段分别完整保留63份物理引用，九项拒绝、实际技术副本删除与凭证重读及189处范围检查通过。完整申请、批准、队列、恢复和应用采用继续实施，原main及原有权限、临床状态保持。

2026-10-19 07:45的S3处置证明检查见[QA32](../development/QA32-s3-deletion-proof.md)及[证明规范](../../data-design/s3-deletion-proof.md)。真实journal、准确版本删除、持久receipt、事务终止恢复、两个实际删除进程、读取锁竞争和21项拒绝通过；63份原件完整重新读取及189处范围检查通过，五份新增技术副本均实际删除。原有应用、权限与临床状态保持，完整应用接入继续实施。

2026-10-19 07:18的S3版本登记检查见[QA31](../development/QA31-s3-version-registration.md)及[数据规范](../../data-design/s3-storage-registration.md)。独立PostgreSQL17使用非超级用户通过103项迁移；63份真实对象完成登记和重新读取，事务恢复、两个实际进程并发及12项拒绝检查通过。已有应用和临床数据保持，完整应用接入继续实施。

2026-10-19 06:52的独立S3组件检查见[QA30](../development/QA30-private-s3-storage.md)。codex/s3-private-storage分支使用63份授权原件，核验准确版本、完整读取、189处范围读取、13项拒绝及准确版本删除；应用接入继续实施。当前main和两套验收服务仍使用QA29记录的LocalFileStore及f406制品，完整业务和临床验收条件保持。

2026-10-19当前实施与真实验证以[QA14](../development/QA14-full-function-delivery.md)、[QA29](../development/QA29-real-original-range-reads.md)及交付记录为准。当前测试使用用户已授权真实资料，不创建模拟临床资料、独立答案或外部回执。当前实际来源为63份原件及290个数据库页面，完整临床及业务验收继续保留原条件；下列2026-10-09记录按其明确时间和模拟范围保留。

## 需求与状态来源

[PRD](../../sJIA合并MAS预警平台PRD.md)维护 FR-01—FR-22、对应 AC／GRAC 及非功能要求；[交付记录](../../planning/delivery-ledger.json)为 T01—T60 的唯一状态来源。[开发索引](../development/index.md)登记主记录与 identifier；专项检查引用各自 QA 记录。

2026-10-21 读取交付记录：30 项承诺、60 项任务均保留范围，60 项任务为 IMPLEMENTED；验收 STALE 11 项、NOT_RUN 49 项，当前没有 PASS 项。完整剩余项与环境见[当前交接](../../CURRENT-DELIVERY-20261021.md)。本次核实源码与文档，历史业务证据保留原始日期、输入及环境。

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
