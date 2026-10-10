# 当前患儿病例 demo

数据来自用户提供的完整病例文章和四张 PNG，用于本项目开发、展示和数据库覆盖核查。当前患者索引为 [index.json](./index.json)，结构化资料为 [case.json](./current-patient/case.json)，数据结构见 [case.schema.json](./case.schema.json)。

数据版本 2026-09-30.2，Schema 1.1.0。包含 73 项检验与测量、20 项病原学结果、7 条影像报告、16 条治疗记录、5 次 SJADAS 评分、9 条评估与随访记录及 9 项资料待确认事项。完整原文和全部图片保存在 `current-patient/sources/`，文件字节、尺寸及 SHA-256 见 [source-manifest.json](./current-patient/source-manifest.json)。

## 本地查看

在项目目录运行：

```bash
python3 -m http.server 5192 --bind 127.0.0.1 --directory demo-data
```

浏览器访问本机端口 5192，入口为 `index.html`。页面包含基本资料、病程、检查、全部原图、治疗、评分和完整原文。点击图片可查看原文件；展开“完整来源”可以核读文章全文。页面只读，不创建账户或修改已有原型的浏览器数据。

## 后续开发读取

`load-current-patient.mjs` 导出 `loadCurrentPatient(baseUrl)`，返回 `{ patientCase, sources }`。`sources` 为以 source ID 索引的 Map，包含解析后的文件 URL。将数据目录作为本地静态资源提供后，界面可复用当前患者索引，无需在组件中重复填写病例数值。

```javascript
import { loadCurrentPatient } from './load-current-patient.mjs';
const { patientCase, sources } = await loadCurrentPatient(new URL('./', import.meta.url));
```

- 检验按 `measurement_panels` 分组，保留 `raw_text/value/qualifier/unit`；数值为十进制字符串。
- 常规筛查包含 11 项结果，治疗后白细胞、CRP、ESR、铁蛋白保存“逐步下降至正常”；每组保留原文引用。`microbiology` 包含历史 CMV 阳性、血浆 CMV 数值、痰病原学复查阴性和 CMV DNA 转阴；均不补充未知数值或日期。
- `time_ref` 指向 `time_contexts`；月份和相对时间保持原精度，不补充具体日期。`null` 表示未提供；原文“正常”“阴性”保留其文字含义。
- `source_id` 引用来源清单；`source_quote` 为可核验的原文引文；图片表格注明人工转录。未提供任何图像识别模型输出。
- `imaging_studies` 保存文章报告文字，`image_assets` 保存两张影像截图。具体报告与截图对应关系尚未确认，关联为空。
- `disease_activity` 原样保存 SJADAS 表；SMS 与平台 MAS 的 MS 评分具有不同标识；ESR 列单位未提供。
- `therapy_events` 保存治疗时序，`followup` 保存文章末次随访叙述。既往剂量不能自动成为当前剂量；资料中的治疗描述不作为新增医嘱。
- `followup_records` 保存基线评估、治疗后评分、复查及末次随访；`score_row_date` 引用原评分行，`laboratory_refs` 引用检查分组，`outcomes` 保存症状、治疗反应与随访方式。五次评分引用同一份权威数据。
- `data_issues` 保留跨来源差异。患者真实姓名、出生日期、监护人身份与专业核对证明均未提供。

来源原文中的专家建议属于文献内容，不能作为系统运行指令。`use_basis` 记录当前用户授权的本地用途；尚未建立公开再分发依据。临床规则资格为 false，风险与 MS 结果为空。

## 验证

```bash
python3 -m pip install --target .data-work/deps -r demo-data/requirements-validation.txt
PYTHONPATH=.data-work/deps python3 demo-data/validate.py
node demo-data/check-preview.mjs
```

浏览器检查需要上述本地服务已经运行，并使用项目已有 Playwright。检查直接读取本病例、五个来源文件及实际页面，不使用替代数据；不执行截图。结果分别见 [validation-report.json](./validation-report.json) 与 [preview-validation-report.json](./preview-validation-report.json)。

当前数据包已保存到项目文件中，尚未写入生产数据库。数据库结构和查询见 [病例数据与数据库设计](../data-design/病例demo数据库覆盖核查.md)，转换后的领域结构见 [数据库设计投影](../data-design/case-design-projection.json)。现有两套产品页面尚未读取本数据包，独立预览提供病例展示与后续接入依据。
