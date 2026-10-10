# 本地语音服务

语音入口通过 MediaRecorder 采集音频，使用同源接口 `/api/patients/{patient_id}/voice-transcription`。请求验证会话、CSRF 和患儿 write_record 权限，返回真实转写文字。原始声音只在内存处理；文字由本人核对后发送到 Ark 提取候选，确认后才进入正式记录。

后端依赖见 `backend/requirements.lock.txt`。PyAV 负责解码；faster-whisper 1.2.1 使用本地多语言 small 模型、CPU int8、4个推理线程。部署时预先下载 Systran/faster-whisper-small 的 config.json、model.bin、tokenizer.json、vocabulary.txt，设置 SJIA_SPEECH_MODEL_DIR 指向该目录。运行请求不下载模型。当前 QA 配置目录为 `.qa-work/QA04/models/small`。

每段录音60秒、5MB。服务器允许1秒的停止延迟；低于0.5秒的录音直接拒绝。单进程最多同时处理一段音频，繁忙返回429。没有配置模型返回503，格式、空白、损坏、时长错误分别返回明确提示。页面等待120秒；失败允许本人重试，自动重试次数为零。

浏览器要求安全上下文与录音权限。localhost 测试入口为5174，API为8001，数据库sjia_qa03。录音权限、后台切换和目标手机兼容性由实际设备验收；本地自动化使用无患者信息音频，不采集操作者的环境声音。

验证入口为 `guardian-h5/scripts/qa04-feedback.mjs`、`guardian-h5/scripts/qa04-audio-browser.mjs` 和 `backend/tests/test_qa04_inputs.py`。实际结果见 [QA04报告](../testing/QA04-report.md)。
