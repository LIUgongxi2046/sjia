# 开发记录索引

2026-10-20的当前HLD与职责接收见[T34](T34.md)：全部22项需求、135项实际symbol、44项代表性持久字段，以及D01—D10后续任务分别核验；39页及328项状态保持。原main运行与独立候选的源码、制品和迁移身份分别记录，QA44运行切换仍在等待明确批准。设计修改维护于codex/current-design-reception，冻结候选0e6c79c及原验收服务保持各自范围，验收状态继续以交付记录为准。

本索引登记稳定任务身份和主记录路径。T01—T60 状态以 [交付记录](../../planning/delivery-ledger.json) 为准，专项 QA 状态以各自主记录为准；工单内容见 [实施计划](../../implementation-tasks.md)。已有主记录持续更新；尚无正文的路径在执行对应任务时创建。T34 的工程接收与 HLD 2.0 依据见 [主记录](T34.md)，T35 的数据规范与 SQL 校验见 [主记录](T35.md)，T36 的接口、字段与任务规范见 [主记录](T36.md)，本期实施要求见 [知识说明](../knowledge/prd2-delivery.md)。路径登记不代表实现或验收完成。当前顺序见 [2.0 迭代计划](../../2.0迭代计划.md)。

检索规则：按 task_id、需求/验收 ID、模块或 identifier 查询；同时阅读实际代码与当前设计。每项验收前核对唯一主记录、有效引用、真实证据及受影响记录。关联记录为该任务 depends_on 的主记录以及第七列的消费方记录。

当前资料迁移、实际备份恢复与新旧字段读取见 [T39 主记录](T39.md)，其验收范围与准确输入由 CHECK-T39 保存。

逐事实模型范围、专业资格、当前和历史编排见 [T37 主记录](T37.md)；设计检查证据由 CHECK-T37 保存，实际业务采用分别归属 T41、T49、T50。

专业资格、静态规则、单位映射、文书页面引用、候选选择、上下文政策、任务执行及保存读取见 [T49 主记录](T49.md)。UI2-34 接入实际字段与人员目录、请求创建、准确政策与依据、核对提交、撤销、受控原件及原请求恢复；UI2-35 接入准确配置、病例、观察区间、专业选择、静态任务创建和保存结果读取。明确选择固定真实事实、原因、依据与人员授权，历史回放核查截止之前的原选择、任务、回执和审计。五项实际检查保持111张业务表内容；12项核对及23项评估协议、TypeScript、构建通过，两个页面各9项 DOM 检查通过。T49 独立开发登记 IMPLEMENTED，继续 T50；合格资料正向计算、worker 执行、临床及视觉验收保持 NOT_RUN，V49 保持 NOT_RUN。

逐项提取、本人确认、医学时间、事实更正、历史及记录引用见 [T41 主记录](T41.md)，开发登记 IMPLEMENTED。完整模型回执、逐项原文、五种语境、否定、日期精度、正式保存、原请求恢复、准确原件、权限撤销及重新读取已经通过模拟业务28项检查。Chrome完成本人保存及更正；保存后7项复验、五种宽度及实际200%文字、12项Schema、TypeScript和构建通过。真实业务、手机、微信和视觉继续NOT_RUN。

2026-10-09主窗口进度：全部60项模拟开发已完成，开发剩余0项；验收剩余57项。T29的scope04来源、分类、MS、动态评估、唯一通知送达及本人阅读、30项Summary实际保存重读，14项来源组合通过。T56通知与摘要页面四项、PostgreSQL逐项交叉读取及正式前端修复已经通过并接收，见 [共享运行记录](T56-shared-integration.md)。T55实际语音辅助更正保留旧版本并保存版本2，未产生独立新Record，见 [运行记录](T55-shared-integration.md)。T60最终37份源码与36份输入证据清单核验通过，86项来源索引作为交付输出独立生成并保存重读，全部输入的字节与摘要保持一致，见 [最终清单](../../.planning-work/parallel/window1/t60-delivery-manifest-verification-main-final-delivery-03.json)及 [证据索引](../../.planning-work/parallel/window1/t60-simulation-evidence-index.json)。当前构建交付11项及39页入口检查通过。V34、V36、V38、V39当前输入需要复验，开发保持完成。共享Worker/Beat未启动；真实提供方账单、共享授权撤销、设备、微信、临床、家庭与39页视觉继续NOT_RUN，临床指标NOT_MEASURED。

通知许可、原请求未知查询、签名回执及撤销竞争对应 [T23 主记录](T23.md) 与已完成的 [T52 实现](T52.md)。独立 PostgreSQL 两项并发检查通过。scope04唯一通知Attempt已保存accepted与delivered回执及本人阅读，家庭端、支持端和数据库重读相符；正式Provider、设备与完整V23继续NOT_RUN。

原件格式、用途副本、准确原页外发、复核、撤销、处置申请及逐项结果见 [T40 主记录](T40.md)。成熟PDF与UTF-8文本分页、完整原件、准确页序、幂等保存和上传恢复已接入；五份副本实际处理、独立模拟复核及准确PDF第一页模型调用分别保存证据。65项模拟业务、18项Chrome检查和8项解析检查通过；当前输入与权限的5项检查通过。指定模拟副本实际删除回执、继续保存证明和原件完整性已核验；21项Schema与68次实际响应通过，TypeScript和构建通过。隔离提供方适配器9项、申请绑定3项、失效复核拒绝、正向模拟处置7项与准确保存重读8项分别通过。单项Task、TaskAttempt、家庭申请、支持批准及删除回执持续保存，模拟提供方对象已删除，原件与用途副本完整。T40登记IMPLEMENTED；V40、真实提供方、设备及视觉保持NOT_RUN。

全部 UI2 页面、状态来源、正式路由、DTO 与准确资产接收见 [T38 主记录](T38.md)，其检查证据为 CHECK-T38；一比一视觉验收与业务采用分别由 T54／T56／T58 继续维护。

UI2 全部39个正式页面的当前实施见 [T54 主记录](T54.md)，开发登记IMPLEMENTED。五种宽度与两种文字尺寸共390组布局检查通过；原稿CSS、准确资产、页面标题及适用导航分别核验。模拟实际登录、关联申请提交、保存重读、刷新与退出通过；草稿保存、刷新恢复、断网失败及同一草稿重试通过四项完整行为检查。实际正文与版本持续保存，浏览器持久存储没有保存家庭资料。功能业务证据由 [T40](T40.md)至 [T53](T53.md)的主记录维护；结构预览与正式就医摘要分别登记。328项状态、真实业务、手机、微信和视觉验收保持待验收，DOM与构建检查不代表一比一视觉验收通过。

PRD 2.0 需求：参见 [当前 PRD](../../sJIA合并MAS预警平台PRD.md) 第 8.6、8.7、8.19、9.2—9.3 节的动态预警、真实通知、FR-13—FR-22 与评价要求；第 13.4 节维护 OPT／GR／FR／验收及 T 任务关系。来源自查见 [赛事群资料问题自查与优化需求](../../赛事群资料问题自查与优化需求.md)，工作顺序见 [参赛优化迭代方案](../../参赛优化迭代方案.md)。执行时读取当前 PRD 和对应主记录；本索引引用不改变任务完成状态。

文书创建、分类、多页顺序、多次就诊、逐事实来源、版本和原请求恢复见 [T43 主记录](T43.md)，开发登记 IMPLEMENTED。模拟业务35项检查、Chrome8项检查、15项Schema与41次实际响应通过。新事实保存独立版本，原值和医学时间保持，原回执与当前文书版本分别显示；本人说明和已有专业来源分别保存，其他原页引用拒绝。真实V43、设备、完整状态及视觉继续NOT_RUN。

原生通知的当前资格、许可、消息保存、签名回执、原请求恢复、管理与家庭详情集成见 [T52 主记录](T52.md)，独立开发登记 IMPLEMENTED。本人任务、提醒设置、原请求恢复、到期调度、独立站内消息、签名回执、阅读、停止与撤权见 [T53 主记录](T53.md)，独立开发登记 IMPLEMENTED。模拟实际HTTP流程40项和Chrome业务12项检查通过；实际保存、刷新、正文显示确认、五种宽度和键盘分别保留证据。真实业务、设备及视觉继续待验收，完整V53保持NOT_RUN。

批次接收、逐页任务、额度、费用预留、实际模型请求、完整回执、失败重试、取消、撤权及原请求恢复见 [T42 主记录](T42.md)，独立开发登记 IMPLEMENTED。49项模拟HTTP及数据库检查、9项Chrome业务检查、在途取消和完整回执保存、独立后台正常完成、11项Schema及五种宽度的200%文字复验分别保存证据。模拟计价明确标识，未知结果禁止自动再次调用；完整V42真实业务、设备及视觉保持NOT_RUN。评价集合见 [T48 主记录](T48.md)，独立开发登记IMPLEMENTED。非空模拟集合、准确来源、分组、独立标签、冻结、任务执行、九类结果、撤权与重新读取通过30项业务及10项权限检查；11项Chrome检查、21项Schema及80次实际响应、TypeScript和生产构建通过。完整V48真实批准、独立临床指标、设备和视觉继续NOT_RUN；临床指标由T59完成，共同接口见 [集成记录](T48-shared-integration.md)。

家庭内容、联系信息、配置及发布资格见 [T51 主记录](T51.md)。发布与配置采用核验准确批准、来源、验证报告、实际制品、联系信息及当前环境。联系、发布、提交恢复、配置登记与配置采用共29个测试方法通过，五份正式证据分别绑定当前输入；15条配置、10条批准和10条验证保持原有内容。字段配置资格不足时不返回字段，摘要资格不足时不返回正文，178个原始项目完整保存。release 路由使用 publish_config，contacts 路由使用 manage_contacts，未创建岗位授权；正向发布、联系创建与激活、配置批准与报告登记、并发及全部家庭出口仍待验收。提交恢复正文由 RequestView.controlled_input 表达，具体操作仍核查当前授权。

| task_id | 业务名称／模块 | 关键词与 identifier | 需求／验收 ID | development_record | 主要实现路径 | 关联任务 |
| --- | --- | --- | --- | --- | --- | --- |
| QA63 | 本地测试登录与患儿资料 | 登录凭据；local-validation；真实患儿；记录重读 | FR-01；FR-09；QA63-01—06 | docs/development/QA63-local-test-account.md | LoginPage；Vite；真实浏览器验收 | QA61；QA62 |
| QA62 | 文献规则采用与真实预警运行 | AI 文献核查；版本配置；执行范围；真实 Chrome；运行采用；不足状态；提示展示 | FR-06；AC-DYN-01—05 | docs/development/QA62-literature-alert-runtime.md | assessment；alerting；configurations；check-real-warning-flow；现有评估及家庭提示页面 | T49；T50；T51；QA61 |
| QA61 | 独立登录与预警流程验收 | /login；401；会话恢复；预警计算；提示展示 | FR-01；FR-06；FR-09；AC-H5-05 | docs/development/QA61-login-alert-flow.md | App；LoginPage；AccessProvider；client；alerting；家庭提示 | T08；T09；T50；T51；QA27；QA59 |
| QA60 | 原件显示与会话清理验证 | ArrayBuffer；Blob URL；真实原件；后台；实际字节 | FR-02；FR-09；AC-H5-05 | docs/development/QA60-original-lifecycle.md | api/client；ArchiveOriginals；PrivateSourceGallery；PrivateDocumentSource；SourceGallery | T12；T27；T45；T54；QA59 |
| QA59 | 共享会话与跨窗口资料清理 | Web Locks；storage；SessionBoundary；会话版本；受限内存 | AC-H5-05；T08；T09；T54 | docs/development/QA59-shared-session.md | api/sessionBoundary；state/SessionBoundary；AccessProvider；SupportApp | T08；T09；T38；T54 |
| QA44 | 当前运行版本集成 | Python3.12；313份配置；当前制品；实际采用；main | FR-01—22；T28；T39；T57；QA14 | docs/development/QA44-current-runtime-integration.md | configurations；runtime_admission；文件响应；现有服务 | QA14；QA38；QA42；QA43 |
| QA43 | 现有资料升级与完整性 | main集成；真实快照；PostgreSQL18／17；完整迁移；资料保持 | FR-01—22；T39；T57；QA14 | docs/development/QA43-existing-data-upgrade.md | 正式迁移；SourceManifest；文件响应 | QA14；QA28；QA38；QA42 |
| QA42 | 恢复环境独立运行采用 | 当前权限；配置；TLS；63份原件；文件响应；真实处置与资源复核 | FR-02；FR-09；NFR-07／08／09；QA38-06 | docs/development/QA42-restored-runtime-adoption.md | config.runtime_admission；retention.content_recovery；files.http_content；tasks | QA38；QA40；QA41 |
| QA41 | S3运行配置、HTTP与用途处置队列 | Session期限；任务引用；20项HTTPS；Outbox；Worker；撤权重试 | FR-02；FR-09；NFR-07／08／09；OPS | docs/development/QA41-s3-runtime-http-queue.md | config.runtime_admission；identity.session；disposition_tasks；tasks | QA34；QA35；QA38；QA40；T28；T57 |
| QA40 | S3恢复历史引用链 | generation；父映射；原始引用；再次备份与恢复 | FR-02；FR-09；NFR-07／09；OPS | docs/development/QA40-s3-restore-lineage.md | S3RestoreGeneration；S3RestoreLineage；S3ContentRestoreState | QA36；QA38；QA39；T28；T39；T57 |
| QA39 | Linux恢复隔离验证 | 官方Lima；内核网络规则；63份原件；189处范围；15项CLI拒绝 | FR-02；FR-09；NFR-07／09；OPS | docs/development/QA39-linux-recovery-isolation.md | QA38；恢复验证环境 | T28；T39；T57；QA36；QA38 |
| QA38 | S3操作入口与恢复采用 | 明确存储类别；操作批准；实际CLI；恢复回调 | FR-02；FR-09；NFR-07／09；OPS | docs/development/QA38-s3-operator-recovery.md | deploy/recovery.py；restored_callbacks；S3BackupPackage | T28；T39；T57；QA36 |
| QA36 | S3备份与独立恢复 | 完整备份；独立集群；准确映射；可信内容重放 | FR-02；FR-09；NFR-07／09；OPS | docs/development/QA36-s3-backup-restore.md | S3BackupSource；S3RestoreMapping；S3RestoreContext；S3ContentRestoreState | T28；T39；T40；T56；T57；QA35 |
| QA35 | S3内容处置与前端传播 | 准确清单；实际批准；真实删除；用途限制；本地流程 | FR-09；NFR-07／09；OPS | docs/development/QA35-s3-content-disposition.md | S3ContentManifest；S3ObjectProof；content_storage；contentDisposition | T26；T28；T33；T40；T56；T57；QA34 |
| QA34 | 应用存储选择与运行身份 | 全局文件服务；私有配置；Runtime声明；真实拒绝 | FR-02；FR-09；NFR-07／09；OPS | docs/development/QA34-storage-selection.md | storage_factory；CodedRuntimeError；runtime.schema.json | T12；T26；T28；T33；T40；T56；T57；QA33 |
| QA33 | 真实业务对象与S3处置接入 | 实际业务键；物理引用；批准字段；任务事件；真实元数据 | FR-02；FR-09；NFR-07／09；OPS | docs/development/QA33-s3-business-disposition.md | PurposeS3DispositionObjectProof；disposition_storage；s3-delete-1 | T12；T26；T33；T40；T56；T57；QA32 |
| QA32 | S3处置证明与真实并发 | 持久journal；准确receipt；事务终止；读取锁；真实删除 | FR-02；FR-09；NFR-07／09；OPS | docs/development/QA32-s3-deletion-proof.md | S3RegisteredDeletion；S3DeletionJournal；S3DeletionReceipt；正式Schema | T12；T26；T33；T40；T56；T57；QA31 |
| QA31 | S3版本登记与持久性 | PostgreSQL17；实际版本；advisory锁；事务；准确处置 | FR-02；FR-09；NFR-07／09；OPS | docs/development/QA31-s3-version-registration.md | S3StoredObject；S3RegisteredStore；正式Schema | T12；T26；T33；T40；T56；T57；QA30 |
| QA30 | 私有S3准确版本组件 | VersionId；ETag；SHA256；真实原件；独立分支 | FR-02；FR-09；NFR-07；OPS | docs/development/QA30-private-s3-storage.md | S3VersionedStore；S3ObjectReference；verify_s3_real_storage | T12；T33；T40；T46；T56；T57；QA14 |
| QA29 | 真实原件范围与准确制品 | Range；内存；63份原件；完整API；当前采用 | FR-02；FR-09；NFR-07；OPS | docs/development/QA29-real-original-range-reads.md | file_content；get_range；backend_artifact_manifest；实际HTTP及Chrome | T12；T28；T33；T46；T56；T57；QA14 |
| QA28 | 目标运行版本与空库迁移 | Python3.13；PostgreSQL17；正式迁移；结构；真实参数；设计回执 | OPS；V01；V02；V35；V39 | docs/development/QA28-target-runtime-compatibility.md | 正式migrations；data-design.check_v2；只读实际数据库 | T01；T02；T27；T35；T39；T57；QA14 |
| QA27 | 真实临床入口权限范围 | 核对字段403；空病例目录；current；replay；整理 | FR-09；FR-10；AC-10 | docs/development/QA27-real-clinical-access-scope.md | verification.workflow；assessment；实际Chrome | T49；T50；T56；QA12；QA14 |
| QA26 | 支持端访问状态与恢复 | 会话受众；岗位；实际登录；手动核验；返回 | FR-01；FR-09；AC-H5-05 | docs/development/QA26-support-access-recovery.md | SupportApp；PurposeCopyPage；实际Chrome检查 | T09；T54；T56；QA09；QA14 |
| QA25 | 真实评价来源清单 | 原件SHA256；准确页面；未知单位；用途资格 | FR-20；GRAC-08 | docs/development/QA25-real-evaluation-source-inventory.md | case_details.evaluation_inventory；FileObject；FilePage | T48；QA10；QA11；QA12；QA14 |
| QA24 | 真实重复资料目录读取 | SQL数量；准确来源；历史；权限；分页 | FR-16；NFR-15 | docs/development/QA24-real-relation-read-performance.md | archives.relation_queries；relations；authorization | T44；T57；QA18；QA23；QA14 |
| QA23 | 真实相同文件关系 | 相同字节；准确来源；确认；解除；历史 | FR-16；GRAC-04；FD-64 | docs/development/QA23-real-duplicate-files.md | archives.relations；relation_queries；UI2DuplicatesPage | T44；QA11；QA12；QA14 |
| QA22 | 真实PDF原生文字与手工文书 | 文字层覆盖；准确原页；未知元数据；原回执 | FR-13；FR-15；GRAC-03 | docs/development/QA22-real-native-pdf-document.md | UI2DocumentCreatePage；archives.grouping；requests.archive_documents | T40；T43；QA11；QA12；QA14 |
| QA21 | 真实观察范围保存与历史版本 | 当前查询范围；self_report；原回执；更正；依赖失效 | FR-19；GRAC-07 | docs/development/QA21-real-observation-period-save.md | records.periods；requests.observation_periods；UI2ObservationPeriodPage | T47；QA20；QA19；QA14 |
| QA20 | 真实观察时段与未知采样时间 | 明确可见字段；实际原值；未知间隔；来源 | FR-19；GRAC-07 | docs/development/QA20-real-observation-period.md | records.periods；UI2ObservationPeriodPage | T16；T47；QA12；QA14 |
| QA19 | 真实新增内容整理 | 独立可见字段；准确批次；确定性项目；来源 | FR-18；GRAC-06 | docs/development/QA19-real-archive-delta.md | summaries；requests；runtime.schema | T46；QA12；QA14 |
| QA18 | 真实文书读取性能 | 批量来源核验；准确权限范围；内容保持 | FR-15；FR-17；NFR-14 | docs/development/QA18-document-read-performance.md | archives；views_archive_documents | T43；T45；T56；T57；QA14 |
| QA17 | 真实资料权限撤销与恢复 | read_raw；manage_grant；原件校验和；版本 | FR-01；FR-15；GRAC-03／05 | docs/development/QA17-real-access-revocation.md | authorization；支持接口；UI2GroupingPage | T09；T43；T56；QA14 |
| QA16 | 文书归组原文入口 | document_id；version_id；原件来源 | FR-15；FR-17；GRAC-03／05 | docs/development/QA16-document-source-entry.md | UI2GroupingPage；DocumentOriginalPage | T43；T45；QA12；QA14 |
| QA15 | 本地真实资料提醒 | personal_record_task；in_app；delivered；read_at | FR-22；GRAC-10 | docs/development/QA15-real-material-reminders.md | backend/notifications；requests；tasks；UI2RemindersPage | T53；QA12；QA14 |
| QA14 | 全部功能持续开发与定时提交 | FR-01—22；当前授权；main；06:00 | 当前全部 AC／GRAC | docs/development/QA14-full-function-delivery.md | planning；backend；guardian-h5；docs | T01—T60；QA09—QA13 |
| QA13 | 档案任务并发与容量 | 环境共享锁；原件；预留；PostgreSQL | QA13 并发与容量验收 | docs/development/QA13-archive-worker-concurrency.md | backend/archives；recognition；family_content；authorization | QA12；QA10 |
| QA12 | 真实 OCR 与字段验证 | Ark；隐私复核；输入副本；字段；来源；费用 | QA12 识别验收 | docs/development/QA12-real-ocr-validation.md | .qa-work/QA12；backend现有服务 | QA10；QA11；T55 |
| QA11 | 真实资料本地验证环境 | 数据库；原件；PDF；配置；任务；来源 | QA11-01—06 | docs/development/QA11-real-data-workspace.md | .qa-work/QA11；backend现有服务 | QA10；QA07；T04；T06 |
| QA10 | 真实资料处理与功能验证 | 原件；OCR；比较符；时间；来源；任务；真实评价 | QA10-01—07 | docs/development/QA10-real-data-validation.md | backend；guardian-h5；.qa-work/QA10 | QA09；T41；T42；T50；T52；T55 |
| QA09 | 登录与访问状态恢复 | UI2ReadState；AccessProvider；会话；关联；恢复入口 | QA09-01—05 | docs/development/QA09-access-recovery.md | guardian-h5/src/components；features/account | QA07；T07；T54 |
| QA07 | 并发读取、照片上传与摘要处理 | Principal；只读快照；runtime；worker；UploadedPhotos | QA07-01—06 | docs/development/QA07-concurrent-read-repair.md | backend/authorization；summaries；config；core；guardian-h5 | QA06；T03；T25；T28 |
| QA06 | 模拟资料业务验收 | 模拟输入；实际接口；保存重读；任务；异常恢复 | QA06-01—09 | docs/development/QA06-simulation-acceptance.md | guardian-h5；backend；.qa-work/QA06 | QA05；T29；T54；T56 |
| QA05 | 首页形象与操作样式 | HomeCompanion；主操作；主题；尺寸 | QA05-01—05；AC-H5 | docs/development/QA05-ui-consistency.md | guardian-h5；ui-delivery | T07；T54；QA04 |
| QA04 | 输入与页面反馈修复 | 语音；缩略图；报告；处方；人物比例 | FR-02；AC-H5；T12—T15 | docs/development/QA04-input-feedback.md | guardian-h5；backend；docs/testing | QA03；T12；T13；T14；T15 |
| QA03 | 页面比对与Agent异常回归 | UI3；Ark；候选；异常；权限；恢复 | FR-01—FR-12；AC-H5；EV01 | docs/development/QA03-comprehensive-retest.md | guardian-h5；backend；docs/testing | QA01；QA02；T29 |
| QA02 | 本地功能续接 | 健康档案、症状、照片、草稿、医学时间、完整历史、提示、摘要 | GAP01—GAP08；SU01 | docs/development/QA02-local-completion.md | guardian-h5；backend | T10—T16；T21；T22；T24；T25；T29 |
| QA01 | 全功能与设计验收 | 页面、导航、品牌、上下文、表单、权限 | FR-01—FR-12；AC-H5 | docs/development/QA01-design-acceptance.md | guardian-h5；backend；docs/testing | T07—T29 |
| T01 | 工程环境 | runtime；数据库身份 | OPS；V01 | docs/development/T01.md | backend；deploy | T02；T07；T28 |
| T02 | 数据迁移 | migrations；finite_decimal | FD-01—FD-57；V02 | docs/development/T02.md | backend/models；migrations | T03；T27 |
| T03 | 授权与审计 | Policy Gate；epoch | FR-09/12；V03 | docs/development/T03.md | authorization；audit | T04；T05；T08 |
| T04 | 配置与来源 | field_config_read；loaded_config_version | FR-02/09/12；V04 | docs/development/T04.md | configurations；sources | T06；T10；T21 |
| T05 | 提交恢复 | write_intent；generation | AC-H5-04；V05 | docs/development/T05.md | requests | T06；T08；T10 |
| T06 | 异步任务 | Outbox；lease_generation | NFR-04/08；V06 | docs/development/T06.md | tasks | T12；T18—T28 |
| T07 | 公共界面 | Token；TaskHeader；BrandLockup | NFR-05/11；V07 | docs/development/T07.md | guardian-h5/src/components | T08；T30 |
| T08 | 前端上下文 | AccessProvider；TaskCoordinator | AC-H5-03/04/05；V08 | docs/development/T08.md | guardian-h5/src/api；state | T09—T16；T22—T26 |
| T09 | 登录与关联 | Session；ApplicationView | FR-01/09；AC-01/09；V09 | docs/development/T09.md | identity；relations；account | T29；T30 |
| T10 | 家庭观察 | RecordInput；MedicalTime | FR-02；AC-02；V10 | docs/development/T10.md | records；ObservationsPage | T11；T13—T17 |
| T11 | 草稿 | DraftCoordinator；saved_at | FD-57；AC-H5-03；V11 | docs/development/T11.md | drafts；DraftsPage | T13；T14；T24 |
| T12 | 原件与页组 | file_group；record_attachment | FR-02/09；V12 | docs/development/T12.md | files；PhotoReview | T13；T16；T17；T27 |
| T13 | OCR与报告 | proposed_field；CandidateField | FR-02/10；V13 | docs/development/T13.md | recognition；ReportsPage | T14；T17 |
| T14 | 治疗登记 | medication_event；therapy_status | FD-33/34；AC-02；V14 | docs/development/T14.md | records；TherapiesPage | T20；T25；T30 |
| T15 | 症状语音 | MediaRecorder；faster-whisper；Ark；IME | FRONT-Q16；V15 | docs/development/T15.md | ObservationsPage；语音组件 | T30；T55；T58 |
| T16 | 记录和来源 | medical_time_id；source_resolve | FR-03；AC-03；V16 | docs/development/T16.md | records；family_content | T22；T25 |
| T17 | 专业核对 | verification_field；purpose | FR-10；AC-10；V17 | docs/development/T17.md | verification；support | T18—T20；T24 |
| T18 | 分类 | classification；BR-C00 | FR-04；AC-04；V18 | docs/development/T18.md | assessment | T20；T32 |
| T19 | MS评分 | ms_contributions；Decimal | FR-05；AC-05；V19 | docs/development/T19.md | assessment | T20；T32 |
| T20 | 动态预警 | alert_evaluation；alert_episode | FR-06；AC-06；V20 | docs/development/T20.md | alerting | T22；T24；T32 |
| T21 | 内容和联系 | release_bundle；contact_version | FR-12；AC-12；V21 | docs/development/T21.md | family_content；contacts | T22；T25 |
| T22 | 家庭提示 | HomeView；current_alert_id；read_at | FR-06/07/12；V22 | docs/development/T22.md | home；alerts | T23；T29 |
| T23 | 通知 | permit；attempt；receipt | FR-07；AC-07；V23 | docs/development/T23.md | notifications | T29；T32 |
| T24 | 更正与历史 | expected_version；dependency | FR-08；AC-08；V24 | docs/development/T24.md | records；dependencies | T26；T29 |
| T25 | 就医摘要 | summary_item；summary_record_fields；needs_regeneration；CHECK-T25-SCOPE | FR-11；AC-11；V25 | docs/development/T25.md | summaries | T29；T30 |
| T26 | 资料处置 | disposition_item；awaiting_provider | FR-09；NFR-09；V26 | docs/development/T26.md | retention；DataUsePage | T28；T29 |
| T27 | 病例领域 | case_import；case_read_scope | CASE；V27 | docs/development/T27.md | case_details | T29 |
| T28 | 运行与恢复 | readiness；metrics；backup | NFR-07/08/09；V28 | docs/development/T28.md | deploy；observability | T29；T31；T33 |
| T29 | 全业务安全验收 | AC；BQA；字段传播 | AC-01—AC-12；V29 | docs/development/T29.md | tests/integration | T30；T31；T32 |
| T30 | 实际手机验收 | ENV-H5；pageshow；键盘 | AC-H5-01—06；V30 | docs/development/T30.md | tests/devices | T33 |
| T31 | 性能容量 | p95；p99；EXPLAIN | NFR-06；V31 | docs/development/T31.md | tests/performance | T33 |
| T32 | 临床评价 | known_at；Gold；EVAL | EVAL-01—05；V32 | docs/development/T32.md | tests/clinical | T33 |
| T33 | 指定环境交付 | manifest；运行责任 | OPS；V33 | docs/development/T33.md | deploy；docs/knowledge | 完整交接 |
| T34 | 当前依据与HLD | 2.0；证据；设计接收 | FR-01—22；V34 | docs/development/T34.md | HLD；planning | T01；T02；T07；T08；T35—T38 |
| T35 | 新增数据设计 | fact；episode；medical_time；副本 | FD-58—71；V35 | docs/development/T35.md | 数据LLD；data-design | T02；T27；T36；T39 |
| T36 | 新增后端设计 | Schema；任务；幂等；权限 | FR-13—22；V36 | docs/development/T36.md | 后端LLD；backend-design | T03—T06；T37；T38 |
| T37 | 识别与评估设计 | N00—N11；current；replay | AC-DYN；AC-AI-01；V37 | docs/development/T37.md | 编排LLD；alert-design | T13；T20；T41；T50 |
| T38 | UI2前端设计 | UI2-01—39；Token；状态 | AC-H5；V38 | docs/development/T38.md | 前端LLD；frontend-design | T07；T08；T54 |
| T39 | 新增实体迁移 | Migration；未知时间；旧证明 | FD-58—71；V39 | docs/development/T39.md | backend；data-design | T02；T27；T40 |
| T40 | 用途副本与外发 | original_ref；derived_ref；复核 | FR-21；GRAC-09；V40 | docs/development/T40.md | files；retention；api | T12；T26；T41；T48 |
| T41 | 逐项事实与时间 | fact_id；source_anchor；known_at | FR-13；GRAC-01；V41 | docs/development/T41.md | recognition；records；登记页面 | T13；T14；T15；T42—T50 |
| T42 | 批次及逐页恢复 | batch_id；page_id；attempt_id | FR-14；GRAC-02；V42 | docs/development/T42.md | files；tasks；批次页面 | T05；T06；T12；T45；T53 |
| T43 | 文书与就诊归组 | DocumentView；DocumentList；document_type；episode_refs | FR-15；GRAC-03；V43 | docs/development/T43.md | archives；case_details；recognition | T41；T27；T44；T49 |
| T44 | 重复资料关系 | relation_type；原请求恢复；专业采样；解除；历史 | FR-16；GRAC-04；V44 | docs/development/T44.md | archives；verification；assessment；dependencies；UI2DuplicatesPage | T13；T20；T24；T45—T50 |
| T45 | 档案目录与检索 | archive_search；确认片段；签名分页；文书原件；LocalTimestampField | FR-17；GRAC-05；V45 | docs/development/T45.md | archives；files；core；UI2ArchivePage；DocumentOriginalPage | T16；T27；T43；T44；T46；T57 |
| T46 | 新增内容摘要 | archive_delta；基准版本；原请求恢复；准确原件；采样核对 | FR-18；GRAC-06；V46 | docs/development/T46.md | summaries；requests；UI2DeltaPage；useDocumentSource | T24；T25；T47；T56；T57；T58；T60 |
| T47 | 观察时段与时效 | observation_period_version；原请求恢复；同日采样；时间精度；批准间隔 | FR-19；GRAC-07；V47 | docs/development/T47.md | records；requests；assessment；alerting；UI2ObservationPeriodPage | T16；T20；T41；T44；T50；T56；T58 |
| T48 | 真实资料及评价集合 | patient_partition；label_ref | FR-20；GRAC-08；V48 | docs/development/T48.md | data-design；case_details；受控页面 | T27；T32；T59 |
| T49 | 资格与静态计算 | verification；classification；MS | AC-04；AC-05；AC-10；V49 | docs/development/T49.md | verification；assessment；api | T17—T19；T50；T59 |
| T50 | 动态评估完整接入 | epoch；sequence；current；replay；dynamic-schedule-2；实际触发 | AC-DYN-01—05；V50 | docs/development/T50.md | alerting；tasks；api；DynamicTriggerRecords | T20；T24；T49；T51；T52；T59 |
| T51 | 家庭内容与启用 | release；contact；PublicationPage；cancel_controlled_intent；publication_epoch | AC-12；V51 | docs/development/T51.md | family_content；configurations；views_publication；PublicationEditor | T21；T22；T25；T50；T52 |
| T52 | 真实通知与回执 | authorize_native；delivery_ticket；accepted；delivered；read_at | AC-NOTIFY-01／02；V52 | docs/development/T52.md | notifications.native；requests.notifications；NotificationPage；useNotificationDelivery | T23；T53；T58；T59 |
| T53 | 本人资料提醒 | PersonalRecordTask；authorize_due；consume_material；delivery_ticket | FR-22；GRAC-10；V53 | docs/development/T53.md | notifications；requests；material_scheduler；UI2RemindersPage；PersonalRecordTaskTools；MaterialReminderNotices | T11；T23；T56 |
| T54 | UI2公共界面接入 | 三项导航；原件；语音；草稿 | UI2-01—39；V54 | docs/development/T54.md | guardian-h5；frontend-design | T07—T16；QA04；T56；T58 |
| T55 | AI真实字段评价 | 原文答案；候选；本人采用 | AC-AI-01；V55 | docs/development/T55.md | backend/tests；浏览器脚本 | T13—T15；T41；T59 |
| T56 | 全需求及权限回归 | AC；GRAC；当前版本；拒绝 | FR-01—22；V56 | docs/development/T56.md | tests/integration；docs/testing | T29；T39—T55；T58；T60 |
| T57 | 容量与运行恢复 | P95；P99；RTO；RPO；费用 | NFR-14—18；V57 | docs/development/T57.md | tests/performance；operations | T28；T31；T60 |
| T58 | 手机与家庭任务 | ENV-H5；IME；微信；任务观察 | AC-H5；AC-UX-01；V58 | docs/development/T58.md | tests/devices；docs/testing | T15；T30；T59；T60 |
| T59 | 独立临床评价 | known_at；Gold；分母；静态动态 | AC-EVAL-01；EVAL-01—05；V59 | docs/development/T59.md | tests/clinical；docs/testing | T32；T48；T49；T50；T60 |
| T60 | 参赛与指定交付 | 官方规则；演示；证据引用 | AC-COMP-01；V60 | docs/development/T60.md | 参赛材料；deploy；planning | T33；T56—T59 |

所有任务完成条件包含 Markdown 主记录、索引、受影响知识文档及唯一交付记录同步。原始证据独立保存并引用；正文维护当前有效内容。没有 Git 时以相关文件校验和及运行环境绑定证据。
