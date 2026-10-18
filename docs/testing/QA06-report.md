# QA06 示例资料验收报告

日期：2026-10-16。需求来源：[QA06任务记录](../development/QA06-simulation-acceptance.md)，修复验收来源：[QA07任务记录](../development/QA07-concurrent-read-repair.md)。代码基线为`8ca48b9abb6395284a2c2ee360fbc6545d7ae763`与当前工作区修改。

## 环境与资料

独立验收使用前端`127.0.0.1:5176`、API `127.0.0.1:8002`和数据库`test_sjia_qa06_20261016`。原有5173实例使用自己的数据库，两套API均已加载当前修复。

用户授权的示例资料包含两位患儿、观察、检验报告、治疗、草稿、历史版本、原件、长期档案文书和就医摘要。全部输入标明软件验收用途，接口、数据库、文件存储和worker均执行项目服务。账号凭证保存在本地受限文件中。

软件配置通过实际Schema、来源和代码摘要检查；OCR、语音、外部通知没有启用，临床评估尚未执行。照片上传过程中，识别请求按当前授权返回`403 CASE_ACTION_DENIED`，页面保留原件并显示手动登记入口。没有替代HTTP响应或模型输出。

## 修复验收

| 编号 | 状态 | 当前行为与依据 |
| --- | --- | --- |
| QA06-P01 | FIXED | 首页、字段配置、上传选项、摘要及本地任务在同一只读快照内检查权限和读取资料。实际worker持有锁定时，七项并发HTTP查询全部200，审计成功保存。 |
| QA06-P02 | FIXED | 运行配置检查读取完整的已提交身份；摘要模板被锁定时readiness仍为200、admitted_current，已加载身份与当前身份相同。持续worker完成实际文件校验和摘要任务。 |
| QA06-P03 | VERIFIED | 操作内共享代码校验作用域，作用域结束时重新检查完整代码摘要。配置检查调用分析确认两次完整计算。 |
| QA06-P04 | VERIFIED | 示例附件通过SJIA_DEMO_FONT_PATH读取明确的中文TTC字体，附件生成及环境限制检查通过。 |
| QA06-P05 | FIXED | 相机和相册入口等待上传来源核验完成；系统选择照片期间保持输入可用，实际上传开始后显示忙碌状态。上传、后台校验、草稿恢复及原件SHA-256核对通过。 |

变更账号与权限时继续检查当前状态；业务写入、配置启用和任务执行保留互斥保护。普通家庭账号没有增加服务岗位权限。

## 业务检查

| 检查ID | 场景与断言 | 当前结果 |
| --- | --- | --- |
| QA06-01 | 两位患儿的记录、无效值、未知值和更正前版本重新读取 | PASS |
| QA06-02 | 首页三项症状入口按字段打开记录；切换患儿后读取对应资料 | PASS |
| QA06-03 | 草稿保存、恢复、更新、刷新、正式提交和重读 | PASS |
| QA06-04 | 华氏体温、独立医学时间、未知时间、正式更正和两份历史版本 | PASS |
| QA06-05 | 手动报告保留86.50、比较符、ng/mL、参考区间及未知时间 | PASS |
| QA06-06 | 治疗保留2.5 mL、使用间隔、本人确认和未知结束时间 | PASS |
| QA06-07 | 已有原件通过受控接口读取，内容完整 | PASS |
| QA06-08 | 实际选择照片、上传、文件校验、保存草稿、刷新和原件摘要相同 | PASS |
| QA06-09 | 时间及类别筛选、日期顺序校验、长期档案文书读取 | PASS |
| QA06-10 | 页面提交摘要任务，持续worker完成处理，页面重读准确来源 | PASS |
| QA06-11 | 无关联账号被拒绝访问；退出后会话401 | PASS |

数据库回归11项通过，包含并行授权、审计外键、只读事务要求、账号状态、auth_epoch、worker范围及首页投影。配置并发检查4项通过，实际worker锁定下的HTTP检查7项通过。TypeScript、前端构建、Python语法与git diff --check通过。

当前数据库有2位示例患儿、34条记录、37个版本、9份草稿、8份原件和5份摘要。最新摘要任务为`6bbd3629-feb2-49fb-97f8-0e8cf1a1fe49`，实际重读确认completed、108条来源引用、needs_regeneration为false。持续worker已完成本次页面摘要、文件校验及再次提交的摘要，服务继续运行。

## 证据与适用范围

本地证据位于`.qa-work/QA06/reports`：

- `qa07-regression-release.log`：11项数据库回归。
- `qa07-concurrency-final.log`：4项配置及读取并发检查。
- `qa07-snapshot-http.json`、`qa07-snapshot-http-accepted.log`：7项实际HTTP并发结果。
- `qa07-readiness-lock-final.log`、`qa07-readiness-under-lock.json`：摘要模板锁定期间的完整运行身份。
- `qa07-e2e-final.tap`、`qa07-e2e-final.json`：页面及接口记录。
- `qa07-retest-final.tap`、`qa07-retest-final.json`：照片上传、后台校验及原件重读。
- `qa07-draft-accepted.tap`、`qa07-draft-accepted.json`：当前草稿流程。
- `qa07-worker-current.log`、`qa07-api-current.log`：当前服务与持续任务处理。
- `qa07-build-final.log`：当前前端构建及TypeScript检查。
- `qa07-summary-check-result.json`、`qa07-summary-task-result.json`：最新有效摘要及任务终态。
- `qa07-database-final.json`：对象数量、全部任务状态及代码身份。

页面验证使用DOM、语义控件和实际接口，没有执行截图或图像比对。上述通过范围为本地软件流程；临床指标、外部提供方效果、手机和微信尚未验收。照片关联正式记录的完整提交及趋势图专项检查继续保持未执行状态。
