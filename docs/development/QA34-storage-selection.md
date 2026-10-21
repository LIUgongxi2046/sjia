# QA34 应用存储选择与运行身份

日期：2026-10-19。需求范围为FR-02、FR-09、NFR-07／09与OPS，设计依据为[存储选择规范](../../backend-design/s3-storage-selection.md)。独立分支codex/s3-private-storage继续承接完整应用接入，main保持已有发布。

## 当前实现

files.service.object_store通过SimpleLazyObject解析明确的local或s3选择。全部现有文件生产者及读取方使用同一服务入口；实际S3负载使用S3RegisteredStore，控制证明使用独立本地目录。未配置、未登记类别、冲突选择及非法私有配置立即拒绝。

S3私有配置由Pydantic及正式JSON Schema定义。文件只允许当前进程身份访问，核验普通文件、拥有者、权限、大小及读取前后的准确身份；秘密字段保持隐藏。管理命令和URL导入可以在没有S3配置时完成检查，不产生网络连接。

backend-runtime-1可以声明明确存储身份。S3声明必须包含物理binding、访问配置、控制目录与私有配置文件四项摘要；实际准入比较准确声明及真实Bucket配置。配置文件内容改变要求明确重启和相应配置采用。core.errors.CodedRuntimeError保存可传递的reason_code。

已有本地恢复状态、恢复标记或明确恢复路径不能直接用于S3恢复采用；当前实现拒绝缺少S3恢复目标的操作。完整S3版本映射、内容清除及恢复继续实施。

## 实际验证

使用目标Python3.13.13、独立PostgreSQL17.11非超级用户、已有本地HTTPS S3及用户已授权真实资料。独立数据库仅包含五个资料分组所需的382份准确来源元数据、63份原件及290个页面，不包含专业核对、医学评估、运行准入或Task。

| 检查 | 当前结果 |
| --- | --- |
| 63份实际业务原件通过全局存储服务读取准确范围 | PASS |
| 原有63份物理引用与处置证明保持准确VersionId | PASS |
| 实际业务文件服务完整读取一份原件 | PASS |
| 全局S3服务实际写入技术副本、相同内容重试及准确版本删除 | PASS |
| 两份技术副本实际缺失查询及持久删除凭证重读 | PASS |
| 原有本地服务使用真实字节写入、重试、读取及删除 | PASS |
| 不提供S3配置时完成Django和URL导入，网络访问审计保持零次 | PASS |
| 缺失、未登记、冲突、相对路径、权限、符号链接、FIFO、大小及Schema等十四项拒绝 | PASS |
| 当前底层组件完整重读63份968528634字节、189处范围及十三项拒绝 | PASS |
| 来源只读连接与目标382份必要元数据逐项一致 | PASS |
| Django check、103项迁移与Git diff --check | PASS |

正式验证程序为[verify_storage_selection.py](../../backend/tests/verify_storage_selection.py)，全局服务验证耗时172.583秒，底层组件验证耗时68.291秒。独立数据库保留64份FileObject、290份FilePage与66份S3登记，其中63份登记available、三份登记disposed；技术副本的证明持续保存。

准确私有证据保存于原项目忽略目录.qa-work/QA34/reports/verified-current.json与final.json，最终检查时间为北京时间09:12:22；底层组件证据为QA30/reports/verified-qa34.json。运行保持报告QA30/reports/main-preservation-qa34.json于08:45:29核验原有两个API均ready和admitted_current、两个前端可访问、f406制品及原始资料和权限保持。真实资料、凭据和运行证据没有写入Git。

用户明确暂时跳过两笔Ark未知请求的供应商核对。两笔请求继续保留2CNY与两个并发位置，本轮新增模型调用零次，没有推断最终费用或制造提供方回执。

## 完整接入条件

完整Runtime采用、真实申请和批准、实际队列执行、内容清除、S3恢复映射、部署与生产目标仍需对应完整证据。当前检查不表示完整应用、专业资格、独立临床答案、真实设备、微信及视觉验收完成；全部60项原始任务及其验收要求保持。
