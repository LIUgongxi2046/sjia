# QA02 本地功能续接与运行验证

## 任务身份

用户 2026-10-04 要求继续完成未开发功能并提供本地验收地址，随后授权没有主办方输入的部分使用模拟资料。范围为 GAP01—GAP08、UI04、OB02、RP01、RP02、TH01、DR01、RE02、CO01、AL02 和 SU01。

本任务状态：COMPLETE，适用范围为本地模拟功能验收。既有任务状态以 planning/delivery-ledger.json 为唯一来源。文档有效性：CURRENT；last_verified：2026-10-04。没有 Git，源码和构建使用 SHA-256 绑定证据。

## 检索与输入依据

已读取 PRD、UI 3.0、开发索引、T10—T16、T21、T22、T24、T25、T29、验收矩阵、前端类型、后端协议及当前代码。检索重点为 FileGroup、RecordAttachment、Draft、RecordVersion、SourceAnchor、FieldVerificationState 和 ReleaseBundle。现有数据模型能够保存本次字段、原图、版本和草稿，不需要新增数据库迁移。

账号 acceptance-family、模拟患儿A/B、模拟字段配置和提示由 seed_local_acceptance 建立。命令仅允许 DEBUG 且数据库为 sjia_dev。图片由 create_simulation_report.py 生成，只有软件测试声明和两项示例数值，不含患者信息。没有导入或修改用户现有病例包。

## 当前功能与实现

| 路径与 identifier | 当前行为 | 后续读取方 |
| --- | --- | --- |
| HomePage、HealthArchive | 四类资料、医学时间和后续读取；关闭恢复焦点 | 家庭健康档案 |
| ObservationsPage、read_field_config | 按类型读取有效症状定义，分别保存选择、未知状态、单位、方法和医学时间 | 记录、摘要 |
| UploadedPhotos、PhotoReview | 相机入口、相册多选、10张和20MB限制、排序、替换、确认移除；完成文件组再保存附件 | 报告、治疗 |
| ReportsPage、records.service | 候选本人确认；保留原值、比较符、单位、方法、参考区间、报告时间及逐项目原图来源 | 详情、历史、摘要 |
| TherapiesPage | 原药名、状态、起止时间、来源及原图保存回读 | 记录、摘要 |
| useFormDraft、DraftsPage | 四类服务端草稿，串行版本保存、刷新恢复、提交消费；未保存链接、后退及页面关闭保护 | 四类表单 |
| record_search | 医学时间排序；签名游标绑定患儿、权限字段、条件及资料版本 | 记录、健康档案 |
| record_version_read、RecordVersionFields | 每个版本的全部字段、核对状态、报告来源、原图和更正原因 | 历史、提示来源 |
| alert_content、AlertDetailPage | 校验发布、资料和核对版本后展示正文；获准来源、有效联系及首次阅读 | 首页、提示详情 |
| generate_summary | 已知治疗日期与范围相交时纳入药名和说明；未知结束时间不推断持续使用 | 就医摘要 |

## 决策与约束

使用真实本地服务执行模拟资料提交，不使用 mock 或替代返回。源图只保存页级来源；没有坐标信息时不生成定位框。更正保留原始来源，新字段需要重新专业核对。个人确认不授予临床计算资格。

草稿版本冲突和过期提交在同一事务内拒绝，正式版本保持原值。签名游标在资料、核对或查询条件变化后失效。内容配置停用或提示依赖变化后不返回正文。照片错误保留已存在的输入。

临床 Gold、实际设备、生产配置和通知送达不使用模拟证据登记通过。禁止主动视觉操作和访问 /tmp；中间结果保存在已忽略的 .qa-work。

## 验证与证据

| 验证 | 实际操作 | 当前结果 | 证据 |
| --- | --- | --- | --- |
| 单元与集成 | manage.py test tests --keepdb | 244/244 PASS；草稿事务、来源传播、治疗范围和游标失效 | ../testing/evidence/QA02/backend-tests.log |
| 模拟业务 | acceptance-local.mjs | 11/11 PASS；保存、刷新、回读、更正、历史、摘要、分页与患儿切换 | ../testing/evidence/QA02/local-business.json |
| 照片及 OCR | acceptance-photos.mjs | 5/5 PASS；限制、整理、字节回读、真实 Ark、来源及离页保护 | ../testing/evidence/QA02/photos.json |
| 匿名页面 | acceptance-design.mjs | 190/190 PASS；23页 DOM、尺寸、品牌与导航 | ../testing/evidence/QA02/design-check.json |
| 接口安全 | acceptance_http.py | 106/106 PASS；匿名拒绝、CSRF、no-store | ../testing/evidence/QA02/http-security.json |
| 工程 | npm run build；manage.py check；migrate --check | PASS | ../testing/evidence/QA02/build.log、django-check.log、migrations.log |

输入来源、执行时间、版本及证据摘要保存在 [manifest.json](../testing/evidence/QA02/manifest.json)。复现命令与测试边界见 [QA02 报告](../testing/QA02-report.md)。

## 关联影响与后续使用

关联 T10、T11、T12、T13、T14、T16、T21、T22、T24、T25、T29。主记录、验收矩阵、开发索引、H5 README 与交付记录描述当前实现。服务运行于本机5173/8000，PostgreSQL使用5433，Redis使用6379。

## 未完成内容与继续条件

本任务列出的本地模拟功能和检查已完成。T30—T33 的实际手机、独立临床 Gold、专业核对、容量指标、生产环境和外部通知需要相应真实条件；视觉证据保持 NOT_RUN。
