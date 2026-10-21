# RELEASE01 main 代码、文档与测试资料

task_id：RELEASE01。授权来源：2026-10-21 用户要求 commit 与 push 代码和文档到 main，包含测试账号测试数据、排除真实数据。

## 源码与资料范围

已通过 GitHub API 核实远程 main 为 `b094448d40d53ba675dd5e4a278f3e2deb107a43`，当前代码接收其全部 24 个提交，并包含当前文档与测试资料。版本与验收状态继续引用 Git、[交付记录](../../planning/delivery-ledger.json)及[当前交接](../../CURRENT-DELIVERY-20261021.md)。

测试资料来源为独立数据库 `test_sjia_demo_20261020`，精确限定账号与两个模拟患儿的 UUID。只读一致性导出 47 张表、3201 条记录、6 份生成附件；附件逐份核对登记大小及 SHA256。归档解压读取与成员 SHA256 检查通过；与 63 份已知真实原件的身份及字节摘要均没有重合。快照没有写入现有数据库。

发布资料见 [test-data](../../test-data/README.md)及 [manifest](../../test-data/manifest.json)。数据库、真实患者资料、真实原件、密码、密码哈希、会话、密钥、环境文件、配置证明、私有报告和会议逐字稿保留本地。

## 检查与提交依据

当前源码完成锁定依赖安装、TypeScript 与生产构建。Django、文档来源、OpenAPI、JSON Schema、全部任务索引和数据快照分别执行检查，原始结果保存在 `.docs-work/`。暂存内容通过明确文件清单核对，合并不改变任何原任务验收状态。

Git HTTPS 当前无法连接，GitHub API 读取已经验证。提交使用准确 Blob、Tree、提交身份和父提交同步对象，main 更新要求基于已核验的远程版本并保持正常历史关系。实际交付 SHA 与同步结果以 Git 和远程 main 重新读取为准。
