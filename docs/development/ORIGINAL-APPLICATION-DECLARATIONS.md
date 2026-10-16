# S02-B：关联申请人声明

日期：2026-10-11。范围：原始 S02-B 缺少的结构化自述与监护范围确认。

## 依据与边界

- 原始交互及视觉依据：`ui-delivery/screens/png/S02-B.png`，`ui-delivery/preview/design.jsx` 的 `Association`。
- PRD FR-01、AC-01、D02：申请、登录、知道标识均不产生病例访问权；关联证明与权限须按获准流程核验。
- “患儿称呼”“与患儿关系”作为申请人未经核验的自述保存，永不写入 `Patient`、病历或当前关联的显示姓名，也不用于患儿目录搜索或自动匹配。
- 勾选只记录申请人的范围声明，不证明身份、法定监护资格、临床同意或访问授权，不能替代正式 D02 证明。

## 原子实现

1. 现有申请新增可空 `applicant_declaration` JSON 字段；迁移 `relations/0003_application_declaration.py` 不补造历史声明。
2. 新声明必须同时包含 `schema_version: 1.0.0`、最长 100 字的非空 `declared_patient_name`、`declared_relationship`（mother/father/other_guardian）和严格布尔真 `guardian_scope_acknowledged`，不得包含其他字段。
3. 整个声明对象可省略以兼容旧接口和旧提交意图；显式 null、不完整、false、数字/文本布尔、未知版本或申请人注入 `patient_id` 均拒绝。
4. 旧请求规范化保留原始键形状，不加入空声明，旧正文摘要不变。新声明纳入原提交意图与正文摘要，重试不能更换。
5. 申请人和获准核验人员读取带有 `declaration_verification: unverified`；历史缺省为 `not_provided`。即使关联批准，自述仍标记“申请人自述（未经核验）”。核验人仍独立填写准确患儿标识。
6. 表单按原图排列三个字段、确认框和提交按钮。新表单不预填、不预勾；编辑自述、申请说明或权限清除确认。权限选择与正式核验说明保留在提交按钮之后的可展开区域。
7. 原提交正文冻结到结果核验结束。丢失响应后读取原请求，回执之后重读申请并比较声明；不一致不显示成功。临时授权刷新隐藏自述并保留原请求锁；换账号清除表单。
8. 无新增通知、日志正文、自动授权或患儿身份变更。现有元数据审计边界不变，错误响应不回显自述值。

## 验证

- `backend/tests/test_application_declarations.py`：11 项合成契约/HTTP 测试。中央不可变快照运行通过，连同 T09、既有核验 HTTP 和同期摘要拒绝测试共 24/24。
- `guardian-h5/scripts/check-application-declarations-dom.mjs`：8 组生产组件合成 DOM 检查通过，覆盖新/旧契约、显式确认、范围变更、提交前/后丢失响应、授权重查、详情/核验标签和回读不一致。
- `npm run typecheck`、既有 `check-application-review-dom.mjs`、`check-response-contracts.mjs` 通过。
- 未启动数据库、构建正在使用的浏览器产物、提交、推送或部署。真实 D02 证明验收和浏览器像素比对不能由上述合成测试替代。
