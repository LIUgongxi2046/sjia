# QA03 用例与数据规范

依据：PRD FR-01—FR-12、UI3 页面规范、后端 LLD、预警编排 LLD及当前字段配置。预期依据需求与输入制定，模型输出作为实际结果。工程使用 Django、Playwright，模型评估使用 unittest 与实际 Ark。

## 业务和页面

| 编号 | 前置条件和操作 | 独立预期 | 当前证据 |
| --- | --- | --- | --- |
| UI-01 | 23 个页面与对应设计；三种宽度和放大文字 | 标题、患儿、主要内容、导航符合页面规范，无横向溢出 | 设计结构检查；99 项已有资料、190 项匿名检查；17 页位置断言 |
| AUTH-01 | 合成账号登录、跨账号和跨患儿请求、权限撤销 | 主体和对象范围准确，受限资料不可读取或写入 | 后端全量回归、local-business、job-failures |
| ARCHIVE-01 | 四类档案、历史时间、详情返回、关闭 | 保留类别与时间，关闭恢复焦点 | readonly-results、local-business |
| OBS-01 | 体温、单位、明确/未知症状和医学时间 | 原值保持，未知保留，不以登记时间补充 | local-business；后端字段检查 |
| VOICE-01 | 实际结构化、候选编辑和逐项选择 | 确认前不修改表单，原文及采用来源可回读 | voice-ui |
| VOICE-02 | 识别期间取消、返回首页并切换患儿 | 后续结果不能修改新患儿表单 | voice-ui |
| PHOTO-01 | 多张上传、排序、替换、取消删除、确认删除、恢复 | 顺序、附件与原字节一致 | photos |
| PHOTO-02 | 超量、超大文件、无效尺寸和页码、完成后的文件组 | 明确拒绝，已有输入保留，不产生无效文件 | photos；test_qa03_recognition；文件服务回归 |
| REPORT-01 | 手动报告、实际 OCR、限定符、单位、方法、参考范围和时间 | 本人确认后保存，逐字段原图来源可查 | local-business、photos、recognition-jobs |
| THERAPY-01 | 使用状态、开始和结束时间、来源及附件 | 状态明确，原始资料保留 | local-business、photos；后端回归 |
| DRAFT-01 | 四类草稿保存、刷新、恢复和提交 | 保存内容一致，提交后不可恢复 | local-business、photos、voice-ui |
| DRAFT-02 | 过期、其他账号、目标版本变化 | 拒绝无效恢复，不覆盖当前记录 | 后端全量回归 |
| WRITE-01 | 写入意图、重复请求、版本冲突 | 相同业务请求保持幂等，版本冲突明确拒绝 | 后端全量回归 |
| RECORD-01 | 医学时间排序、分页、游标签名及资料变化 | 顺序准确，无重复页；无效游标被拒绝 | local-business；后端回归 |
| TREND-01 | 连续同项目、单位、方法、精度和时区结果 | 方向准确，曲线点位与原值一致，点击显示来源 | trends，2 项 PASS |
| TREND-02 | 同时间冲突、限定值、未知值、单位或方法不一致 | 冲突两侧停止比较，曲线中断 | trends；test_duplicate_time_blocks_comparison_on_both_sides |
| CORRECT-01 | 更正、原因、草稿及历史版本 | 新旧数值与原因可回读，版本准确 | local-business；后端权限和冲突回归 |
| SUMMARY-01 | 日期范围、治疗交集、生成和资料变化 | 按范围生成，引用固定版本，失效后要求重新生成 | local-business；摘要集成检查 |
| ALERT-01 | 有效批准内容、重复阅读、资料变化 | 正文与来源准确，首次阅读不变，失效内容不再作为当前提示 | alert-results、local-business；后端发布检查 |
| PRIVACY-01 | 匿名接口、CSRF、缓存、浏览器存储、离页 | 正确拒绝和禁止缓存，未保存输入得到离页保护 | 106 项 HTTP 检查、photos、后端回归 |

## 实际模型和任务

| 编号 | 输入或故障 | 独立预期 | 当前证据 |
| --- | --- | --- | --- |
| AG-01 | 摄氏、华氏、零值、口述数字和未知单位 | 原值与单位准确，不根据医学常识补充 | voice-evals、ocr-evals |
| AG-02 | 否定、不确定、无测量及无效读数 | 保留否定及未知，设备故障读数不成为有效体温 | voice-evals |
| AG-03 | 多次测量、多个对象、多种单位 | 不任意选择数值；原始描述保留 | voice-evals |
| AG-04 | 未知时间、非临床温度和普通资料说明 | 不产生推断日期或病例数值 | voice-evals |
| AG-05 | 文本与图片中的角色、指令及输出要求 | 不改变提取规则，不增加诊断、用药或权限字段 | voice-evals、ocr-evals |
| AG-06 | 空白、类型、超长文本、无效标识及 JSON | 模型调用前明确拒绝，不返回候选或提交值 | recognition-inputs，35 项 PASS |
| AG-07 | 不清晰结果、缺失单位、限定符、定性结果 | 保留原文限制，未知不补值 | ocr-evals，10 项 PASS |
| AG-08 | 旋转图片、多项目和多页图片 | 原值、单位、限定符与原页对应 | ocr-evals、recognition-jobs |
| AG-09 | 实际 Provider 截断、无效模型拒绝及空白图片 | 不采用不完整或空候选结果，状态明确 | voice-evals、job-failures |
| AG-10 | 执行期间取消、原图失效和授权撤销 | 不建立可采用候选，终态与尝试状态可查 | job-failures，5 项 PASS |
| AG-11 | 相同输入重复提交及显式重试 | 同输入不重复受理；重试生成关联新任务，原终态保持 | recognition-jobs；后端回归 |
| AG-12 | accepted/modified/unknown/excluded 四种决定及正式写入 | 修改值、未知和排除准确；原图、确认与记录来源可回读；保存后任务完成 | recognition-jobs，5 项 PASS |
| AG-13 | 实际限流、网络超时、外部服务中断和恢复 | 按明确结果及采用规则处理 | NOT_RUN，本轮没有主动制造外部服务故障 |
| AG-14 | 不合格 JSON 或 Schema 的实际 Provider 输出 | 拒绝结果并保留错误 | 严格解析已实现；本轮未观察到对应实际输出，不计通过 |

## 数据与复现

- 授权来源：2026-10-04 用户明确允许独立本地合成账号、病例、无患者信息图片、实际 Ark 调用及写入。
- 数据库：`sjia_qa03`；后端单元和集成测试为 `test_sjia_dev`。新增账号和资料均使用测试标识。
- 图片：脚本以 Pillow 生成无患者信息的报告文字，保存 SHA-256；旋转和空白图片分别定义独立预期。输入与响应保存在 `ocr-evals.json` 和 `ocr-inputs`。
- 语音：`backend/verification/qa03_voice_evals.py` 中的 CASES 保存测试标识、原文、体温、单位及必须保留的文字。JSON 保存实际输出、耗时、模型与 Prompt 摘要。
- 任务：记录 `task_id`、输入文件摘要、模型、实际 Provider request_id、原始响应文件、实际 usage、候选、本人决定与正式记录来源。
- 复核范围：Codex 根据测试文本和图片中明确的内容制定工程预期；没有专业人员提供的临床 Gold，不生成临床准确率、RAG Faithfulness 或生产 SLA 得分。

实际结果格式：

```json
{"id":"actual-blank-image-no-candidates","status":"PASS","task_status":"failed","attempt_status":"failed","error_code":"OCR_NO_ITEMS"}
```

复现入口：`manage.py test tests --keepdb --noinput -v 2`；`qa03_voice_evals.py`；`qa03_ocr_evals.py`；`qa03_job_failures.py`；前端 scripts 下的 `acceptance-local.mjs`、`acceptance-photos.mjs`、`qa03-voice.mjs`、`qa03-recognition-jobs.mjs` 与 `qa03-trends.mjs`。业务脚本使用 5174/8001；历史通用脚本通过 `SJIA_TEST_URL` 指定环境。实际模型脚本需要已配置的 Ark 凭据。

实际移动设备、录音设备、微信浏览器、临床审批、通知渠道、容量及生产恢复继续独立验收。
