> 当前组合候选请先阅读[2026-10-14交付说明](../CURRENT-DELIVERY-20261014.md)。以下内容为历史记录；旧端口、演示账号及seed命令不作为当前启动或登录依据，历史测试不代表本组合版通过。

# 监护人 H5

2026-10-04。React、TypeScript、Vite、antd-mobile；同源/api由本地Django处理。本地模拟功能验收通过，范围与证据见 [QA02报告](../docs/testing/QA02-report.md)。

## 本地验收

测试账号须通过正常注册流程创建，密码由使用者自行设置；不在仓库提供共享登录凭据。

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

## 登记回归测试

`npm run test:registration` 使用 Node 24+（本次验证版本 24.19.0），在 jsdom 中挂载实际 React 表单与草稿/提交逻辑，只使用合成数据和模拟 fetch，不连接后端或模型服务。覆盖治疗候选 A→B→A 的分项清空、双击、提交成功但回执丢失、10 秒自动草稿、同一请求重试、300 秒意图过期、明确校验失败后的编辑，以及患儿/账号切换。

`npm run test:registration:browser` 为同类 Playwright 浏览器检查，需要先启动 Vite（默认 127.0.0.1:5173，可用 `SJIA_TEST_URL` 覆盖）及可运行的 Chromium（可用 `SJIA_CHROMIUM_PATH` 指定）。DOM 测试通过不代表浏览器或视觉检查已通过。

提交结果不明时，表单暂停编辑和草稿保存，并提供“重试本次保存”。请求正文和幂等键只保留在当前表单内存；跨刷新/关闭的恢复尚未实现。离开提示要求先核对已有记录，不能把重新打开表单视为原请求重试。
