# QA03 页面与功能验收

日期：2026-10-04。文档有效性：CURRENT。本地功能检查与实际 Ark 检查的范围见下表；生产发布判定保持 **NO_GO**，临床 Gold、专业审批、真实移动设备、通知渠道与容量条件仍按 T30—T33 验收。

## 环境与数据

用户明确允许在独立本地测试数据库中创建合成账号、病例及不含患者信息的图片，并用于实际 Ark 调用和写入测试。业务测试使用 PostgreSQL 5433 的 `sjia_qa03`、Django 8001、Vite 5174、Redis database 3、独立 Celery Worker 与 Beat；原图目录为 `.qa-work/QA03/object-store`。Django 回归使用独立 `test_sjia_dev`。已有资料的页面检查使用 5173/8000 与 `sjia_dev`。

模型结果来自实际 Ark。测试脚本保留输入、独立预期、实际响应、模型名称、请求标识、计量及耗时；不替代 Provider 返回。合成资料不能作为临床准确率或专业核对依据。项目没有 Git，源码与证据版本使用 SHA-256，见 [manifest.json](evidence/QA03/manifest.json)。

## 已执行结果

| 检查范围 | 当前结果 | 证据 |
| --- | --- | --- |
| 后端单元及集成回归 | 258/258 PASS | [backend-tests.log](evidence/QA03/backend-tests.log) |
| 登录、档案、登记、四类草稿、更正、历史、摘要、游标、提示失效及患儿切换 | 11/11 PASS | [local-business.json](evidence/QA03/local-business.json) |
| 图片数量与尺寸拒绝、排序、替换、移除、恢复、实际 OCR 保存、离页保护 | 5/5 PASS | [photos.json](evidence/QA03/photos.json) |
| 语音候选实际调用、逐项采用、草稿恢复、写入回读、取消、切换患儿及响应布局 | 4/4 PASS | [voice-ui.json](evidence/QA03/voice-ui.json) |
| 实际语音字段评估 | 50/50 PASS | [voice-evals.json](evidence/QA03/voice-evals.json) |
| 实际 OCR：数字、限定符、零值、未知单位、文本结果、无法辨认、提示注入、旋转、空白 | 10/10 PASS | [ocr-evals.json](evidence/QA03/ocr-evals.json) |
| 多页识别任务、重复提交、四种候选决定、确认写入、原图失效、显式重试、取消 | 5/5 PASS | [recognition-jobs.json](evidence/QA03/recognition-jobs.json) |
| 实际调用期间取消、原图失效、权限撤销、Provider 拒绝及空白图无候选 | 5/5 PASS | [job-failures.json](evidence/QA03/job-failures.json) |
| 趋势方向、冲突与缺失区间、实际曲线点位和点击来源提示 | 2/2 PASS | [trends.json](evidence/QA03/trends.json) |
| 22 个已有资料页面、三种宽度、200% 文字及只读交互 | 99/99 PASS | [readonly-results.json](evidence/QA03/readonly-results.json) |
| 已有提示正文、首次阅读时间及布局 | 3/3 PASS | [alert-results.json](evidence/QA03/alert-results.json) |
| 23 个匿名页面结构与交互 | 190/190 PASS | [design-check.json](evidence/QA03/design-check.json) |
| 匿名接口、CSRF、缓存及身份边界 | 106/106 PASS | [http-security.json](evidence/QA03/http-security.json) |
| 识别输入格式、空白、类型、标识及 JSON 拒绝 | 35/35 PASS | [recognition-inputs.json](evidence/QA03/recognition-inputs.json) |
| 原图完整读取、分段字节及无效范围 | 13/13 PASS | [existing-files.json](evidence/QA03/existing-files.json) |
| 17 页标题位置、宽度、患儿归属与首页首屏 | PASS | [ui-inspection.json](evidence/QA03/ui-inspection.json) |
| TypeScript、Vite、Django、Python 依赖 | PASS | [build.log](evidence/QA03/build.log)、[django-check.log](evidence/QA03/django-check.log)、[dependency-check.log](evidence/QA03/dependency-check.log) |

各层分别统计，数量不相加为临床验收数量。具体执行时间、对象和断言以对应 JSON 与日志为准。Vite 生产构建存在 500 kB 分包建议，目前主脚本约 703 kB；构建成功。

## 页面设计检查

23 个业务页面已按对应设计图片检查结构，实际页面截图见 [screens](evidence/QA03/screens)。资料数量、身份、数值、临床提示和操作使用服务返回与有效权限。没有使用设计示例数据填充病例，也没有声明像素相似度。

| 设计页面 | 当前行为 |
| --- | --- |
| S01、S02-A/B/C | 品牌、账号登录、患儿卡片、申请入口、关联状态及本人访问范围 |
| S03 | 深蓝标题、儿童形象、批准提示正文、三项实际观察、三列快捷操作及底部导航 |
| S04 | 语音和文字入口；实际 Ark 候选；逐项选择、修改和确认；体温、单位、症状与时间 |
| S05、S06 | 拍照和相册入口、已有资料、完整登记字段、原图、草稿及本人确认 |
| S07、S08 | 资料筛选、记录卡片、内容/来源/时间分组、逐字段原始内容及对象操作权限 |
| S09 | 原值、单位、方法、医学时间、来源及核对状态；可比较曲线与中断原因 |
| S10-A/B | 当前版本、更正内容、原因、历史版本与完整字段；读取原记录不生成草稿 |
| S11-A/B | 批准内容、联系信息、来源和状态；首次阅读时间保持一致 |
| S12-A/B | 日期范围、实际资料数量、分类摘要、医学时间、来源与生成时版本 |
| S13-A/B、S14 | 账号、患儿管理、资料使用说明、退出及分类草稿 |
| S15-A/B/C | 对应访问、服务及地址状态，提供可执行入口 |
| 健康档案 | 四类资料在同一面板切换；选择实际医学时间；返回恢复类别与时间，关闭恢复焦点 |
| 原图面板 | ImageViewer 全屏、旋转、翻页、Escape 关闭与入口焦点恢复 |

## 当前修复行为

- 共用标题、三维图标、背景、卡片和表单样式；患儿归属位于标题下方，首页在 390×844 首屏完整显示主要内容。
- 语音文字通过实际结构化接口产生候选；未经本人选择和确认，当前表单保持原值。原文随观察保存，草稿保留采用来源；取消及离开页面终止浏览器接收。
- 图片识别使用严格 Schema，保留原值、比较符、单位和未知状态。上传校验文件组状态、页码、格式和尺寸；多页分别绑定来源。
- OCR 任务通过 Outbox、Celery 与实际 Ark 执行。候选就绪为 `awaiting_confirmation`，本人逐项 accepted/modified/unknown/excluded 后提交记录才进入 `completed`。实际模型、请求标识、原始响应和计量可追溯。
- 同一文件组及版本重复提交返回同一任务。显式重试建立关联的新任务，保留原终态。取消、文件组变化、期限和权限变化阻止后续结果采用；空白图片无项目时返回失败状态。
- 记录确认与正式写入处于同一事务，修改值、未知状态、确认标识及原页来源在回读中保持一致。
- 同一医学时间有多个结果时，相关比较均停止；限定值、未知值、单位或方法不满足条件时中断曲线。实际 Chart.js 数据与原值列表一致，点击点位显示来源。
- 更正页面使用读取内容作为初始状态；摘要依据生成时记录版本，资料变化后提示重新生成；详情操作入口依据对象权限。

## 证据范围与外部条件

已经实际触发 Provider 拒绝、输出截断及输入变化。真实 Provider 限流、网络超时、服务中断恢复和浏览器录音设备未被本轮主动触发，不能据此声明这些外部路径通过。实际手机相机、双指缩放、微信浏览器、临床 Gold、专业审批、真实通知送达、容量与生产恢复条件继续独立验收。

完整场景、预期与证据范围见 [QA03-test-matrix.md](QA03-test-matrix.md)。
