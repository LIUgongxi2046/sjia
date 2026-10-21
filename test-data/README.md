# 测试账号资料快照

本目录保存用户于 2026-10-21 明确授权提交的测试账号资料，标记为 `SIMULATION_ONLY`。资料来自独立数据库 `test_sjia_demo_20261020`，仅包含测试账号 `local-real-data-validation` 关联的两位虚构患儿“林知夏”和“沈星辰”。其生成依据与软件验证分别见 [QA65](../docs/development/QA65-complete-simulation-account.md)及 [QA68](../docs/development/QA68-historical-data-calculated-alerts.md)。

| 文件 | 内容 |
| --- | --- |
| [qa65-snapshot.zip](qa65-snapshot.zip) | 47 张表的标准 JSON、6 份生成附件及包内 manifest |
| [manifest.json](manifest.json) | 账号标识、两位模拟患儿 UUID、逐表数量、逐文件 SHA256、归档 SHA256 与核查范围 |

快照包含 36 条记录、71 个记录版本、294 项字段、147 个事实、294 个事实版本、6 份文书、6 个批次、27 份摘要、15 份草稿及相关历史、观察范围、提示和提醒，共 3201 条记录。保留的数据值来自现有测试数据库的只读一致性查询。每份附件都与模拟文件对象的大小及 SHA256 一致，与已知 63 份真实原件的校验和没有重合。

## 查看方式

在项目根目录列出归档内容：

```bash
backend/.venv/bin/python -m zipfile -l test-data/qa65-snapshot.zip
```

需要展开时使用项目忽略目录：

```bash
backend/.venv/bin/python -m zipfile -e test-data/qa65-snapshot.zip .docs-work/test-data-preview
```

`tables/*.json` 使用 `mode`、`schema`、`table`、`rows` 四项字段。UUID、十进制和日期时间保持标准 JSON 字符串；NULL 保持 null。`objects/` 中的附件通过 manifest 的 file_id、patient_id、大小及 SHA256 关联相应 FileObject。

## 使用范围

本包用于读取、检查与迁移测试资料。目标环境需要独立配置数据库、账号认证、权限、来源、运行配置与版本证据。表中已有引用保持原标识；导入程序需要核验目标环境及引用依赖。运行中的测试账号与本快照分别管理，快照没有修改现有数据库。

登录密码、密码哈希、Session、CSRF、API 密钥、运行环境文件、配置证明、其他账号资料和真实病例均未包含。真实资料及会议逐字稿保留本地。模拟数值与本地功能结果不提供临床准确性的通过结论。
