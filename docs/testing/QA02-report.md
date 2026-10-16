# QA02 本地模拟验收报告

日期：2026-10-04。文档有效性：CURRENT。判定：**GO，适用范围为本地模拟功能验收**。

本地页面：http://127.0.0.1:5173/login。验收凭据须由执行者通过环境变量显式提供，文档不保存密码。

## 功能与可观察结果

| 范围 | 当前行为 | 证据 |
| --- | --- | --- |
| GAP01 健康档案 | 四类资料直接展示，医学时间、后续读取、关闭恢复焦点 | local-business.json：UI04 |
| GAP02 症状 | 有效症状定义连接表单，已观察、未观察、未知分别保存 | local-business.json：OB02 |
| GAP03 照片 | 多选、排序、替换、确认移除，10张及20MB限制；字节回读一致 | photos.json：RP01 |
| GAP04 治疗 | 原药名、使用状态、起止时间、来源及照片分别保存 | local-business.json：TH01 |
| GAP05 草稿 | 四类保存与刷新恢复；提交后不可恢复；未保存导航和后退保护 | local-business.json：DR01；photos.json：DR01 |
| GAP06 查询 | 医学时间排序和签名分页；未知时间保留；逐字段核对状态展示 | local-business.json：RE02；backend-tests.log |
| GAP07 更正与历史 | 原值和新值完整读取，方法、单位、来源图和原因保留；冲突保持原版本 | local-business.json：CO01；backend-tests.log |
| GAP08 提示 | 有效发布正文、获准来源、站内联系与本人首次阅读；资料变化后失效 | local-business.json：AL02、CO02 |
| 就医摘要 | 范围内字段与原记录一致；日期相交的治疗纳入；未知时间不补充 | local-business.json：SU01；backend-tests.log |
| 报告 OCR | 真实 Ark 识别模拟图的 Ferritin150ng/mL 和 CRP4.2mg/L；确认后保存原值与逐项原图来源 | photos.json：RP02 |

## 执行结果

| 层次 | 数量与结果 | 原始证据 |
| --- | --- | --- |
| 后端单元与集成 | 244/244 PASS | [backend-tests.log](evidence/QA02/backend-tests.log) |
| 登录后的模拟业务 | 11/11 PASS | [local-business.json](evidence/QA02/local-business.json) |
| 照片、限制、OCR与离页保护 | 5/5 PASS | [photos.json](evidence/QA02/photos.json) |
| 23个匿名页面结构与交互 | 190/190 PASS | [design-check.json](evidence/QA02/design-check.json) |
| 匿名访问、CSRF与缓存 | 106/106 PASS | [http-security.json](evidence/QA02/http-security.json) |
| TypeScript与生产构建 | PASS | [build.log](evidence/QA02/build.log) |
| Django检查与迁移状态 | PASS | [django-check.log](evidence/QA02/django-check.log)、[migrations.log](evidence/QA02/migrations.log) |

浏览器业务使用真实 Django、PostgreSQL、Redis 和 Vite，没有替换接口响应。图片由本地脚本生成，只有模拟声明和示例项目，没有姓名、身份和患者资料。预期数值来自生成脚本，代码输出没有作为自身预期答案。

没有新增数据库迁移。生产构建位于 guardian-h5/dist；代码、构建、图片与证据 SHA-256 见 [manifest.json](evidence/QA02/manifest.json)。后端全量回归包含原有 MedicalTime 测试输入的时区警告，不影响通过判定。

## 验收边界

真实患者资料、独立临床 Gold、实际 iOS/Android/微信浏览器、录音设备、通知送达、容量指标和生产交付保持未验收。没有主动截图或像素比较。模拟发布和联系渠道明确标记本地用途，不提供模拟电话号码。

## 重复执行

项目根目录运行，数据库和 Redis 保持可用：

```bash
backend/.venv/bin/python guardian-h5/scripts/create_simulation_report.py
backend/.venv/bin/python backend/manage.py seed_local_acceptance
backend/.venv/bin/python backend/manage.py test tests --keepdb
backend/.venv/bin/python backend/manage.py check
backend/.venv/bin/python backend/manage.py migrate --check
PYTHONPATH=backend backend/.venv/bin/python backend/verification/acceptance_http.py
```

guardian-h5目录运行，Django与Vite服务保持可用：

```bash
npm run build
TMPDIR="$PWD/../.qa-work/tmp" node scripts/acceptance-local.mjs
TMPDIR="$PWD/../.qa-work/tmp" node scripts/acceptance-photos.mjs
TMPDIR="$PWD/../.qa-work/tmp" node scripts/acceptance-design.mjs
```

业务脚本会提交模拟资料。每次重跑 acceptance-local.mjs 前运行 seed_local_acceptance，建立对应当前资料版本的模拟提示。服务和账号只用于本地验收。全部未验收条件见 [验收矩阵](acceptance-matrix.md)。
