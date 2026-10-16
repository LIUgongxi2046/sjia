# C01-001 当前交付文件完整性核验

日期：2026-10-13。状态：离线文件级工具已验证，待主任务审核；未应用到冻结源码或运行实例。

## 范围和旧证据

新入口 `tools/delivery/verify_source.py` 延续已交付 `reproduce/verify_source.py` 的独立预期 SHA256/mode 比较，并补齐明确 root/manifest、字节数、完整文件集合、路径与清单校验。只用 Python 标准库，不导入或执行被检查包中的代码。

旧 `backend/tests/verify_t60_delivery_manifest.py` 服务 T60 SIMULATION_ONLY 历史清单；其 `verified_files` 是读取文件后计算的当时摘要，不逐项比较独立预期摘要，也不证明当前交付包完整。原脚本、原日期及原收据均保留不改；本工具的结果不继承 T60 PASS。旧补丁回放证明仍是其原版本/原日期的证据，新工具也不替代补丁适用性或功能测试。

本轮 PASS 只说明指定目录内全部普通文件与指定外部期望一致。它不证明应用测试、数据库可恢复、登录/权限、运行准入、浏览器效果、生产或临床有效性。没有执行这些步骤。

## 输入契约与命令

Python 3.10+，支持 POSIX 文件模式的文件系统。root 必须是已经解包、停止写入的独立交付目录；脚本只读 root 和 manifest，结果写到标准输出。报告应重定向到 root 外部，避免报告自身成为额外文件。

```sh
python3 tools/delivery/verify_source.py \
  --root /absolute/path/to/delivery-tree \
  --manifest /separate/trusted/expected-files.json \
  --manifest-sha256 <从独立可信交付记录取得的64位SHA256> \
  > /separate/reports/file-integrity.json
```

manifest 必须在 root 外部，精确格式如下。`mode` 是 POSIX permission bits 的十进制值，例如 420=0644、493=0755；不是字符串，也不是完整文件类型位。

```json
{
  "manifest_version": "sjia-delivery-files-v1",
  "files": {
    "path/to/file.txt": {
      "bytes": 123,
      "sha256": "独立预期的64位小写十六进制摘要",
      "mode": 420
    }
  }
}
```

上面是结构说明，摘要占位符必须替换为真实值。路径相对 root，统一 POSIX `/`，拒绝绝对路径、`..`、`.`、重复分隔符、反斜杠、冒号或控制字符。清单重复 JSON 键、空 files、缺少/多余字段、错误类型均失败。实际树不忽略隐藏文件、缓存或依赖目录；所有未列入期望的普通文件都会失败，符号链接和特殊文件也会失败且不会被遍历。空目录和目录模式不在此版本契约内。

退出码：0=全部预期文件、字节数、SHA256、模式及文件集合一致；1=文件核验失败；2=输入/清单无效。没有依赖 `assert`，因此 `python -O` 不能关闭检查。失败报告会给出路径、错误类型及期望/观察差异；不会返回文件内容。

## 期望来源与可信边界

捕获清单和核验清单是不同步骤。核验入口不能自生成期望、忽略不匹配文件、自动修复文件模式或重设 manifest pin。只允许读取已独立确认的预期文件。

pin 必须经已知交付记录、已审核来源或独立渠道保存；在待核验目录旁临时对其自带清单算 SHA256 再喂给脚本，不构成独立真实性证明。若攻击者能同时替换文件、外部期望和可信 pin，本工具不能识别；它不是数字签名或供应链认证系统。

此次 1,761 文件演示的独立 SHA256/mode 来自既有已固定摘要的两份清单：

- `SNAPSHOT_MANIFEST.json`: `0439f5d6f344d71e799930bd3d6db9cfbe922a5d62e6d540422ed1f6f65ff2e3`
- `SNAPSHOT_MODES.json`: `ce2d296ffcd0196cb91f52e5ca73a01eb58af097a31695de6414f5f73b7bf64c`

`evidence/prepare_controlled_source.py` 先固定比较上述清单摘要，再对冻结来源逐文件比较历史 SHA256/mode；全部一致后补充对应 bytes，保存外部期望，再复制到全新受控目录。该辅助程序只用于本版期望迁移，不能认可任意新版源码。新期望 SHA256 为 `4ab4c3ef328ec4890b0f7ac1364c69c74ad297523cabc8d5f8544bcbc922068c`。新验证器另外检查受控目录，不自行生成其期望。

冻结工作目录额外存在两个清单元数据文件和两个开发依赖符号链接，因此严格全树核验如实 FAIL；受控目录仅复制旧清单指定的 1,761 文件，逐文件 bytes/SHA256/mode 全部 PASS，总字节数 217,271,286。此 PASS 不能表述为冻结工作目录的全部内容、任意新候选包或旧完整业务测试均通过。

## 纯文件验收和复现

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tools/delivery/tests -p 'test_*.py' -v
```

28 个 unittest 方法通过（参数化子例另计，不冒充独立功能数量）。包含正确包、同长度单字节改变、长度改变、缺文件、额外源码/隐藏文件、mode 改变、独立 bytes 比较、清单遗漏、清单替换与 pin、越界/非规范路径、C0/DEL/C1 控制字符拒绝及普通中文/重音字符路径通过、文件/目录/断链符号链接、FIFO、重复 JSON、无效类型、缺少 root/manifest、路径搬迁以及 `python -O`。

交付目录的 `evidence/run_acceptance.py` 另外保存 DoD 正反例退出码和 JSON 报告；`evidence/source-tree-pass.json` 是本轮受控 1,761 文件的实际文件级证据。所有用例只操作临时普通文件或测试用链接/FIFO，没有应用服务和数据库依赖。

## 集成、兼容与回滚

本补丁仅新增工具、纯文件测试和本说明，不覆盖旧 T60 或旧交付工具；旧的两个位置参数调用继续使用原工具，不会被静默解释为本严格入口。接入新版入口应明确选择完整交付范围，独立保存清单及 pin，不给工作目录增加默认忽略规则。无需应用迁移、依赖安装或运行实例操作；撤销这三个新增文件即可回滚。

读取期间可检测普通文件大小、模式、标识和时间戳的变化，但不保证在主动并发修改或恶意竞态下得到原子快照。先在受控停止写入的目录核验；需要强快照语义时由文件系统快照/冻结过程提供。POSIX modes 无法可靠保留的平台应明确报告失败，不能降级比较后声称本契约通过。
