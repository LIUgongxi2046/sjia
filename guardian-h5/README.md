# 监护人 H5

2026-10-04。React、TypeScript、Vite、antd-mobile；同源/api由本地Django处理。本地模拟功能验收通过，范围与证据见 [QA02报告](../docs/testing/QA02-report.md)。

## 本地验收

地址：http://127.0.0.1:5173/login。账号acceptance-family，密码Local-accept-2026。模拟患儿A提供健康档案、观察、报告、治疗、草稿、更正、摘要与提示；模拟患儿B用于核查患儿切换。

照片测试原件由scripts/create_simulation_report.py生成，没有患者信息。全部业务使用真实本地接口和数据库，OCR调用项目配置的Ark。模拟配置与提示明确标注用途。

## 运行与检查

后端在8000，前端在5173。数据库为本地PostgreSQL5433，Redis6379。项目根目录运行：

```bash
backend/.venv/bin/python guardian-h5/scripts/create_simulation_report.py
backend/.venv/bin/python backend/manage.py seed_local_acceptance
backend/.venv/bin/python backend/manage.py runserver 127.0.0.1:8000
```

本目录运行：

```bash
npm run dev
npm run build
TMPDIR="$PWD/../.qa-work/tmp" node scripts/acceptance-local.mjs
TMPDIR="$PWD/../.qa-work/tmp" node scripts/acceptance-photos.mjs
TMPDIR="$PWD/../.qa-work/tmp" node scripts/acceptance-design.mjs
```

每次重跑acceptance-local.mjs前刷新模拟种子。脚本会提交模拟资料。临时产物位于项目已忽略的.qa-work，固定证据位于docs/testing/evidence/QA02。

## 当前功能

QA04 预览入口为 http://127.0.0.1:5174/home，对应 Django8001 和独立数据库 sjia_qa03。语音通过 MediaRecorder 与项目本地 faster-whisper 服务转写，再由 Ark 提取候选。报告、治疗照片直接展示缩略图和逐页识别状态，支持重新识别。治疗可选择原药名及对应原文，实际使用情况由本人确认。部署配置见 [本地语音服务](../docs/knowledge/local-speech.md)，验收见 [QA04报告](../docs/testing/QA04-report.md)。

健康档案读取四类资料与医学时间。观察表单保存有效结构化症状、原始体温、单位、测量方式及未知状态。报告和治疗支持多照片整理、原图回读、来源确认和草稿恢复。报告逐项保存数值、限定符、单位、方法、参考区间和原图页级来源。

四类表单连接服务端草稿；未保存导航、后退及页面关闭受保护。记录按医学时间和签名游标查询，逐字段显示专业核对状态。历史版本保留全部字段、原图和更正原因。摘要固定原始字段快照，治疗范围保留未知时间。提示仅显示当前有效正文、获准来源和联系，并保存首次阅读。

## 验证范围

244项后端测试、16项登录后的模拟业务及照片检查、190项匿名页面检查、106项接口安全检查通过，TypeScript与构建通过。实际手机、微信浏览器、录音设备、真实患者、临床Gold、通知送达、容量及生产交付保持未验收；没有主动视觉检查。全部条件见 [验收矩阵](../docs/testing/acceptance-matrix.md)。
