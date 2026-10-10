# sJIA合并MAS预警平台握手审查报告

日期：2026-10-01。模式：`DESIGN_REVIEW`。设计决议：**CONDITIONALLY_APPROVED（有条件通过）**。业务运行证据：`INSUFFICIENT_EVIDENCE`。

当前依据为 PRD V1.5.0、HLD V1.0、DATA V1.3、BACK V1.1、ALERT V1.1、FRONT V1.1，以及 SQL、API 目录、JSON Schema 和前端类型。设计接收的准确字段、权限、事务、迁移及映射规则见 [跨层接口接收规范](backend-design/handshake.md)。输入校验和与实际命令结果见 [握手检查证据](review-design/handshake-check.json)。

## 1. 前端与后端协议握手审查

| 接收项 | 当前正式定义 | 设计状态 |
| --- | --- | --- |
| HS-01 首页评估与事件 | HomeView.assessment_state、assessed_at、current_alert_id；只有 current 且无有效事件允许 clear；支持有效事件与新资料不足同时展示 | SATISFIED |
| HS-02 OCR 候选 | CandidateField.proposed_field 使用 FieldInput；固定 field_config_version 与准确字段路径；数值、单位、医学时间及未知状态具有正式表达 | SATISFIED |
| HS-03 登记配置 | field_config_read、FieldConfigDefinition、FieldConfigView、FieldDefinition；字段、枚举、单位、限制、可写范围和关注清单按当前批准返回 | SATISFIED |
| HS-04 来源跳转 | source_resolve 返回准确 record_id/version、字段与来源；历史版本和文件继续单独授权 | SATISFIED |
| HS-05 详情归属 | FamilyRecord、AlertView、SummaryView 返回当前授权的 patient_id/display_id；直接链接据此建立患儿上下文 | SATISFIED |
| 历史时间目录 | history_search 返回完整精度和来源；record_search 使用 medical_time_id；年月资料保留区间语义 | SATISFIED |
| 关联申请 | application_read、ApplicationView.version、ApplicationAction.expected_version；withdraw/decide 同事务检查版本和 pending 状态 | SATISFIED |
| 更正历史与治疗原件 | FamilyRecord 返回原因、获准作者、original_files 和完整性；record_attachment 保存准确版本的有序原件 | SATISFIED |
| 请求恢复 | target_key 使用目录准确路径；request_recent_account 查询本人无患儿申请意图；RequestView 返回操作、目标和准备时间 | SATISFIED |
| CSRF 与身份接口 | auth_bootstrap、SessionView.csrf_token、会话内 CSRF secret、X-CSRFToken、固定 Cookie 和批准 return_paths | 设计 SATISFIED；真实身份服务 PENDING |

API 目录包含 106 项操作，其中家庭调用范围为 56 项，接口绑定全部覆盖。59 个 DTO、398 个属性通过顶层属性、必填、空值、直接枚举与 TaskStatus 检查，TypeScript 严格编译通过。24 项路由登记与当前 H5 路由保持一致，新增读取能力已登记到对应页面的 API 引用。

家庭接口的应用状态保持 PENDING，表示尚待真实服务接入。原型临床能力依配置保持禁用。嵌套 Schema 和跨字段业务条件需要在服务实现中执行；类型检查不代表完整业务验收。

## 2. 后端与预警编排边界握手审查

HS-06 的正式任务类型为 `alert_evaluation`，DATA/BACK SQL、runtime、TaskView、result_type 及前端类型一致。受控接口为 alert_evaluation_create 与 alert_evaluation_read，分别使用既有 execute_assessment/read_assessment 权限。

完成任务的内部结果使用 evaluation_id；家庭仅能取得当前授权的进度，内部结果引用为 null、allowed_actions 为空，完成后重新读取 HomeView。家庭不能创建、取消或重试临床评估。受控调用继续检查对象范围、状态、版本、租约和用途。

编排保留确定性规则与服务器 Worker。HTTP 202 表示受理；任务、结果采用、事件发布、渠道受理、送达和本人阅读分别记录。页面读取不触发临床事件，replay 不发布事件和通知。Schema、临床批准与 runtime 配置分别核验，未知版本立即拒绝。

当前不存在产品自主 Agent、SSE、向量检索和 Prompt Caching 接收任务；相关模板检查为 NOT_APPLICABLE。规则与文献版本失效由准确依赖、epoch 和当前读取资格控制。

## 3. 预警编排与数据来源握手审查

HS-07 的六项结构为 alert_evaluation、alert_evaluation_input、alert_evaluation_assessment、alert_signal_result、alert_episode、alert_decision。alert_event 必须通过 evaluation_id 引用同患儿评估；专业 assessment 通过关联表引用，可以为零个或多个。

| 约束 | 当前定义及检查 |
| --- | --- |
| 多时点输入 | `(evaluation_id, signal_id, position)` 主键；字段、记录版本、核对和患儿使用复合引用 |
| 重复任务 | evaluation.task_id 唯一，并复合引用同患儿 task |
| 完整决议 | evaluation/rule 唯一；patient/rule/sequence 唯一；previous_decision_id 绑定同患儿同规则 |
| 持续周期 | 同患儿同规则族至多一个 open；关闭需要 reset_evaluation_id；unknown 不关闭周期 |
| 来源与结果 | 记录准确核对、归一化、医学时间、来源、算法版本及每对计算证据；领域服务校验这些引用的具体语义 |
| 失效传播 | Q26 递归更新依赖节点、评估、事件、专业量表、摘要与周期；当前采用继续检查全部资格 |
| 原件保存 | record_attachment 绑定准确记录版本、同患儿文件和顺序；更正形成新版本集合 |

数据侧 82 张表、后端合并 88 张表实际建表通过，查询准备通过。后端数据库检查核验 12 项关键引用或唯一约束、周期部分唯一索引以及事件评估引用的必填约束。数据库实际版本为 PostgreSQL 18.4，目标 PostgreSQL 17 仍为 NOT_RUN。

现有真实病例的 Schema 与引用检查通过，33 项医学时间完成数据库写入及回读；临床资格保持 clinical_rule_eligible=false。没有生成临床结果，没有调用外部识别或通知服务。真实权限、并发行为、独立临床 Gold 结果及有数据环境迁移尚待验收。

## 4. 综合决议与验收条件

**CONDITIONALLY_APPROVED（有条件通过）**：七项设计接收事项具备正式定义，前端其余接口定义已接收。当前条件为取得外部有效配置及真实运行证据。

| 项目 | 当前状态 | 责任与完成条件 |
| --- | --- | --- |
| FRONT-D01—D08、D10、D12、D13 | 设计 SATISFIED | S005-1/2/3/4 已维护正式字段、SQL、接口、类型和页面映射；S008/S009 继续验证实际传播 |
| FRONT-D09 | 设计 SATISFIED；外部 PENDING | D02、S011 提供 IdentityProvider、凭据、处理依据和获准运行配置，验证登录、CSRF、轮换与退出 |
| FRONT-D11 | PENDING | D01—D09 负责人提供环境、字段、规则、通知、联系和资料保存政策的生效证据 |
| PostgreSQL 17 | NOT_RUN | S011/S009 在目标环境执行建表、查询、迁移及并发约束验证 |
| 真实业务联调 | NOT_RUN | S008/S009 使用真实账号与获准资料验证登记、确认、核对、评估、发布、来源和失效 |
| 临床结果验收 | BLOCKED | 临床负责人提供批准规则、已核对输入及独立 Gold 结果 |
| 设备、性能与 Provider | NOT_RUN | S009/S011 验证真实手机、容量、外部未知结果和通知回执 |

需求追溯继续覆盖 FR-01—FR-12、FD-01—FD-57、AC-01—AC-12、AC-H5-01—AC-H5-06、NFR-01—NFR-13，前端保留 20 项业务验收用例。编号覆盖和结构通过只证明对应设计检查通过。此次未执行发布审查。

### 验证入口

命令在项目目录执行，中间结果使用已忽略的项目目录：

```bash
PYTHONPATH=.data-work/deps python3 data-design/validate.py
PYTHONPATH=.data-work/deps python3 backend-design/validate.py
PYTHONPATH=.data-work/deps python3 frontend-design/validate.py
PYTHONPATH=.data-work/deps python3 alert-design/validate.py
PYTHONPATH=.data-work/deps python3 review-design/check-handshakes.py
```

各设计检查报告保存输入 SHA-256、环境、真实命令及限定结论。[综合证据](review-design/handshake-check.json) 记录本次设计接收、数据库约束和外部待验收项。
