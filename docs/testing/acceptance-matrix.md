# 功能验收矩阵

本表保存 2026-10-04 的 QA02 范围与结果。2026-10-21 的完整项目状态见[当前交接](../../CURRENT-DELIVERY-20261021.md)及[唯一交付记录](../../planning/delivery-ledger.json)，后续真实专项见[开发索引](../development/index.md)。本表的历史通过状态仅适用于当次明确范围。

日期：2026-10-04。预期来自PRD与UI 3.0。用户授权没有主办方输入的部分使用模拟资料。本地模拟判定GO；实际设备、临床与生产证据独立管理。原始结果见 [QA02报告](QA02-report.md)。

| ID | 依据／操作 | 当前证据与状态 |
| --- | --- | --- |
| UI01、UI02 | 23页标题、三栏目导航与正式品牌 | STRUCTURE PASS，190项匿名检查 |
| UI03 | 首页读取当前患儿与有效提示 | SIMULATION PASS，local-business.json |
| UI04 | 四类健康档案、医学时间、关闭恢复焦点 | SIMULATION PASS，UI04 |
| UI05 | 320/390/580宽度及200%文字 | STRUCTURE PASS，匿名页面 |
| UI06 | 实际iOS、Android、微信浏览器 | NOT_RUN，实际设备未提供 |
| UI07 | 图稿像素比较 | NOT_RUN，禁止主动视觉检查 |
| AU01 | 登录、刷新、账号关联、退出 | SIMULATION PASS，浏览器登录恢复和后端会话测试 |
| AU02 | 匿名受限接口拒绝 | SECURITY PASS，106项HTTP检查 |
| AU03 | 无有效关联、申请与权限 | INTEGRATION PASS，模拟关系与授权测试 |
| AU04 | 患儿切换及资料隔离 | SIMULATION PASS，AU04；实际多设备未验收 |
| AU05 | 撤权、跨患儿、跨账号与过期会话 | INTEGRATION PASS，后端授权测试；真实账号复验NOT_RUN |
| OB01、OB02 | 体温、单位、时间、方式、结构化症状及未知状态保存回读 | SIMULATION PASS，OB02 |
| OB03 | 语音开始、结束、本人确认与追加 | DEVICE NOT_RUN，实际录音未提供 |
| RP01 | 多选、限制、排序、替换、确认移除、原图回读 | SIMULATION PASS，photos.json；实际相机NOT_RUN |
| RP02 | 真实OCR、本人确认、原值、单位、来源、时间 | SIMULATION PASS，真实Ark识别模拟图；临床逐字段评价NOT_RUN |
| RP03 | 超限拒绝保留输入、移除候选和未确认禁止提交 | SIMULATION PASS，photos.json；外部识别失败及设备取消全分支NOT_RUN |
| TH01 | 药名、状态、起止时间、来源与照片 | SIMULATION PASS，TH01 |
| TH02 | 未知状态、起止约束与不推断持续使用 | INTEGRATION PASS，治疗及摘要范围测试 |
| DR01 | 四类草稿保存恢复、消费、权限与离页保护 | SIMULATION PASS，DR01；事务与过期检查PASS |
| WR01 | 写入意图、重复提交、原请求与版本冲突 | INTEGRATION PASS，后端写入测试；实际网络中断NOT_RUN |
| RE01 | 筛选、查询及条件恢复 | STRUCTURE/INTEGRATION PASS，页面及后端查询测试 |
| RE02 | 医学时间排序、未知状态、核对与分页 | SIMULATION PASS，RE02；游标变化与签名测试PASS |
| RE03 | 详情、原图、来源、时间和当前版本 | SIMULATION PASS，字段与原件回读 |
| RE04 | 历次值、限定符和比较限制 | INTEGRATION PASS，T16；真实映射评价NOT_RUN |
| CO01 | 完整更正历史、原因、新旧字段与版本冲突 | SIMULATION/INTEGRATION PASS，CO01、草稿事务与来源传播 |
| CO02 | 依赖摘要及提示失效 | SIMULATION/INTEGRATION PASS，CO02和失效逻辑 |
| VE01 | 专业核对及撤销权限 | INTEGRATION PASS，核对逻辑；真实专业账号NOT_RUN |
| CL01、CL02 | 分类和MS独立临床评价 | EVAL NOT_RUN，独立Gold未提供；逻辑测试PASS |
| AL01 | 不同评估与失效状态 | INTEGRATION PASS，家庭提示逻辑 |
| AL02 | 正文、来源、联系、首次阅读与失效 | SIMULATION PASS，AL02、CO02与配置停用测试 |
| AL03 | 通知受理、送达和阅读 | INTEGRATION PASS，模拟通知逻辑；真实渠道送达NOT_RUN |
| SU01 | 日期范围、生成、快照与来源 | SIMULATION PASS，SU01 |
| SU02 | 空范围、反向日期与失效 | INTEGRATION PASS，摘要范围及失效测试 |
| PO01 | 发布、批准配置、停用及联系有效性 | INTEGRATION PASS，模拟配置；真实临床审批NOT_RUN |
| DE01 | 删除申请、审批与处置状态 | INTEGRATION PASS，模拟处置；实际备份和Provider删除NOT_RUN |
| DA01 | 授权病例包Schema与来源引用 | STATIC PASS，QA01 dataset.log；本次未导入现有病例 |
| EN01 | TypeScript、构建、Django和迁移状态 | STATIC PASS，QA02工程证据 |
| SE01 | 浏览器存储、no-store、CSRF和所有权 | SECURITY/INTEGRATION PASS，已执行路径；真实多设备NOT_RUN |
| PE01 | 实际负载、P95/P99和资源观测 | NOT_RUN，容量指标与实际设备未提供 |
| EV01 | OCR／语音模型的独立逐字段评价 | 模拟图两项OCR原值及单位PASS；临床Gold、语音设备和综合评价NOT_RUN |

## QA03 当前复验

2026-10-04：23页设计结构比对、17页位置断言、已有资料页面及只读交互99项、提示3项、匿名页面190项、接口安全106项、识别输入拒绝35项、原图读取13项通过。独立合成数据库完成258项后端回归、11项业务、5项图片流程、4项语音UI、50项实际语音、10项实际OCR、5项任务流程、5项实际执行异常及2项趋势检查。全部证据与外部条件见[QA03报告](QA03-report.md)和[QA03用例矩阵](QA03-test-matrix.md)。生产发布仍按真实设备、临床Gold、专业审批、通知及容量条件判定NO_GO。前面的QA02数量保留对应执行时间与范围。

## 数据与版本

模拟账号、患儿与字段由seed_local_acceptance生成，只允许本地DEBUG及指定的sjia_dev/sjia_qa03数据库。QA03写入与实际模型测试使用独立sjia_qa03。图片没有患者信息；Ark结果来自实际外部调用。没有替代Provider返回。

244项后端测试、11项模拟业务、5项照片与离页保护、190项匿名页面、106项接口安全属于不同检查层次，不相加为临床验收数量。每次保存执行时间、输入依据、环境、结果与源码SHA-256。证据及文件版本见 [manifest.json](evidence/QA02/manifest.json)。

## 外部条件

实际患者、专业核对、独立Gold、实际手机、录音设备、通知渠道、容量及生产运行条件仍由T30—T33管理。本地模拟资料不能授予专业核对或临床计算资格。
