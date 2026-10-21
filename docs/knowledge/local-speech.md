# 本地语音服务

doc_id：SJIA-LOCAL-SPEECH；status：CURRENT，限于本次源码核查；last_verified：2026-10-21（Asia/Shanghai）。适用源码：`b094448d40d53ba675dd5e4a278f3e2deb107a43`。维护责任：语音接口、模型或配置变化时更新。真实音频、设备和当前能力验收继续按 T55／T58 执行。

语音入口通过 MediaRecorder 采集音频，使用同源接口 `/api/patients/{patient_id}/voice-transcription`。请求验证会话、CSRF 和患儿 write_record 权限，返回真实转写文字。原始声音只在内存处理；文字由本人核对后发送到 Ark 提取候选，确认后才进入正式记录。

后端依赖见 `backend/requirements.lock.txt`。PyAV 负责解码；faster-whisper 1.2.1 使用 CPU int8 与 4 个推理线程，`local_files_only=True`。原 QA04 使用本地多语言 small 模型。部署时准备获准模型的完整文件，设置 `SJIA_SPEECH_MODEL_DIR` 指向准确目录；运行请求不下载模型。`.qa-work/QA04/models/small` 为 QA04 配置记录，当前启用情况须读取准确环境的能力状态。

每段录音60秒、5MB。服务器允许1秒的停止延迟；低于0.5秒的录音直接拒绝。单进程最多同时处理一段音频，繁忙返回429。没有配置模型返回503，格式、空白、损坏、时长错误分别返回明确提示。页面等待120秒；失败允许本人重试，自动重试次数为零。

浏览器要求安全上下文与录音权限。录音权限、后台切换和目标手机兼容性需要实际设备验证。当前开发端口、代理及环境检查见 [H5 说明](../../guardian-h5/README.md)与[当前交接](../../CURRENT-DELIVERY-20261021.md)。

v2 还提供 `/api/v2/patients/{patient_id}/voice-inputs` 及其 process、adopt、cancel 入口，使用持久 VoiceInput、准确原始文件、转写证明、用途与 Task 管理语音资料。字段规范见 [voice-input.schema.json](../../backend-design/voice-input.schema.json)，实现见 `backend/api/views_voice_inputs.py` 与 `backend/recognition/voice_input_service.py`。其保存和采用要求与即时转写分别查询对应接口和 [T55](../development/T55.md)。

历史验证入口为 `guardian-h5/scripts/qa04-feedback.mjs`、`guardian-h5/scripts/qa04-audio-browser.mjs` 和 `backend/tests/test_qa04_inputs.py`，实际结果见 [QA04 报告](../testing/QA04-report.md)。本次没有运行模拟音频、采集声音或执行视觉检查；代码核查与真实音频验收分别登记。
