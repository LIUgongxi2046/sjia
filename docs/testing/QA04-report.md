# QA04 输入与页面反馈验收

日期：2026-10-05。范围：独立本地测试环境。入口：http://127.0.0.1:5174/home。Django8001、数据库sjia_qa03，使用既有测试账号 acceptance-family。

## 当前功能

- 语音通过浏览器 MediaRecorder 采集，由项目服务中的 faster-whisper 转写。文字核对、Ark候选提取、本人确认与记录保存相互独立。支持取消、失败重试、录音文件输入和文字输入；不保存录音文件。
- 语音文本框占满卡片内容宽度，标签和按钮独立排列，确认按钮具有完整样式。首页人物保持素材比例，展示高度随页面宽度在164—230像素之间变化。
- 报告与治疗照片直接展示缩略图，支持全屏查看、逐页状态、空结果提示及重新识别。报告候选优先展示，来源与时间按需展开。重新识别替换对应页候选，其他页内容保持对应。
- 治疗照片提取全部清楚可见的药名及原文；每次选择一种药物登记。实际使用状态与时间由本人确认，保存原图来源；修改字段后需要重新确认。
- 无法连接、解码失败、格式或容量无效、处理繁忙和识别超时均有明确响应。报告和语音失败保留已经取得的内容，重新识别由本人主动触发。

## 数据与方法

用户已授权独立本地合成账号、病例、无患者信息图片和实际 Ark 调用及写入。中文报告、治疗图片、空白和异常图片通过 Pillow 生成，输入文字及 SHA-256 见 inputs.json 与 manifest.json。普通话音频使用系统语音生成，并经 PyAV 转换；浏览器测试使用真实 Web Audio 和 MediaRecorder 编码，不读取设备麦克风、不替换转写或模型响应。

实际 Ark 调用检查返回字段、页面内容、本人确认及正式记录重新读取。断网使用浏览器真实离线状态。超时验证进程将实际 Ark 请求期限设置为2秒，接口返回504；常规配置保持120秒，页面照片识别等待最多130秒。

## 验收结果

| 范围 | 实际结果 | 证据 |
| --- | --- | --- |
| 完整后端回归 | 268项通过 | backend-tests.log |
| 语音、首页、报告、治疗、草稿、保存及来源 | 7组通过，页面异常0项 | feedback.json / feedback.log |
| 真实 MediaRecorder WebM 转写与取消 | 2组通过 | audio-browser.json |
| 多照片整理、替换、删除、恢复、报告字段来源 | 5组通过 | photo-regression.json |
| 真实断网、保留内容及重新识别 | 2组通过 | network.json |
| 治疗识别多药、未知、否定、图片指令、旋转、空白 | 6项实际 Ark 评测通过 | therapy-evals.json |
| 实际 Ark 超时响应 | 504、UPSTREAM_TIMEOUT、允许本人重试 | timeout.json |
| 新增输入边界与权限 | 10项通过 | input-tests.log |
| TypeScript 与 Vite 构建 | 通过 | build.log |
| Python 依赖检查 | 通过，无依赖冲突 | pip-check.log |

前端排版验证覆盖320、390、580、1044像素宽度和200%文字；图片通过可见性、实际解码及尺寸断言核查。来源验证重新读取 record fields、capture_method、source_anchor 与附件；截图检查不作为本次证据。

后端完整回归结果与最终源码绑定在 [证据目录](evidence/QA04/manifest.json)。本地功能判断为 GO。实际手机麦克风、权限设置、微信环境及生产交付仍由 T30—T33 按设备和环境分别验收；本次未声称这些范围通过。

## 重复执行

后端：`manage.py test --settings=config.settings.qa --keepdb --noinput`。浏览器：`scripts/qa04-feedback.mjs`、`scripts/qa04-audio-browser.mjs`、`scripts/qa04-network.mjs`。图片回归使用 `scripts/acceptance-photos.mjs` 并将 SJIA_TEST_URL 设置为5174，输出目录设置为QA04。实际治疗评测与超时验证分别为 `verification/qa04_therapy_evals.py`、`verification/qa04_timeout.py`。

运行配置见 [本地语音服务](../knowledge/local-speech.md)，任务依据与矩阵见 [QA04开发记录](../development/QA04-input-feedback.md)。本地功能结果不作为临床识别准确率或真实家庭使用批准。
