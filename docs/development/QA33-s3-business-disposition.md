# QA33真实业务对象与S3处置接入

组件专项检查PASS，last_verified为2026-10-19 08:16（Asia/Shanghai）。完整应用接入继续IN_PROGRESS，开发分支codex/s3-private-storage，关联FR-02、FR-09、NFR-07、NFR-09、T12、T26、T33、T40、T56及T57。

Given 已授权63份原件及其准确FileObject、FileGroup、页面和必要来源元数据，When 按实际业务对象键保存到独立私有S3、采用准确版本、传播处置证明及核验实际删除结果，Then 业务对象键、摘要、大小与提供方版本一致，批准与任务字段完整保留引用，不一致输入被拒绝，原始元数据及原件保持。

## 真实输入与独立环境

本次源数据包含五个资料分组。现有本地测试账号在这些分组中的read_raw与write_record有效范围已经重新读取核验。分组用于资料组织，数量不作为独立临床患者数量。

原数据库通过强制只读连接读取，目标为独立sjia_qa33_business_storage，PostgreSQL17.11，使用没有超级用户、创建数据库或创建角色权限的独立账号。103项正式迁移、再次应用及模型状态通过。

| 准确复制对象 | 数量 |
| --- | --- |
| Principal技术身份 | 2 |
| RetentionPolicy | 2 |
| SourceRegistry、SourceRevision、Patient资料容器、DataUseAuthorization、FileGroup | 每类5 |
| FileObject原件 | 63 |
| FilePage | 290 |
| 全部必要元数据 | 382 |

保存保持原有UUID、对象键、业务版本、摘要、大小、状态、来源及操作时间。382份元数据在目标保存重读及原数据库当前只读快照逐项相同。登录凭据、专业核对、临床记录版本、评估、任务和运行准入均未复制或创建。

## 实现与消费者

| identifier或文件 | 当前实现 |
| --- | --- |
| PurposeS3DispositionObjectProof | 在原有业务证明字段中增加必需storage_reference，逐项核验对象键、SHA256与大小 |
| PurposeDispositionApprovalContext.file_versions | 明确识别完整S3证明，序列化保留准确物理引用 |
| PurposeDispositionTaskRefs.file_identity | 同时保留业务身份和实际S3版本，缺少或不一致引用立即拒绝 |
| files/disposition_storage.py | 按明确存储类型产生证明，调用对应实际删除及凭证读取，分别检查控制证明与S3负载 |
| retention/disposition.py | 批准比较、实际执行、标记完成及请求完成核验共同采用完整证明 |
| retention/jobs.py | s3-delete-1与disposition.s3_requested保持单一对应关系；提交、验证、失败协调、超期协调及重试使用准确映射 |
| tasks/registry.py、dispatcher.py、worker.py | 登记S3事件，投递至purpose队列并交给准确执行器 |

字段、任务、失败状态与应用边界见[后端规范](../../backend-design/s3-disposition-storage.md)。正式证明Schema为files/disposition-object-proof.schema.json。现有本地证明保持六项原有字段；S3证明具有必需物理引用。

事件登记及执行源码通过编译与文件检查，完整真实申请、批准和队列消费仍待对应运行验证。

## 实际验证

63份原件共968528634字节，实际业务对象键均与QA30组件测试键不同。本次使用独立本地S3前缀qa33-business-originals，按准确业务键完成完整写入、重读及登记，耗时36.68秒。原S3前缀、原始文件及原数据库保持。

| 检查 | 实际结果 |
| --- | --- |
| 业务内容匹配 | 63份真实FileObject与完整摘要、大小、S3引用及登记逐项相符 |
| 批准字段序列化 | 63份完整S3证明通过实际字段类型的保存形式与重新解析；未创建业务批准记录 |
| 任务字段序列化 | 63份完整S3证明通过实际任务字段类型的保存形式与重新解析；未创建Task |
| 九项拒绝 | 业务摘要不一致的读取和写入、已处置业务对象的读取和写入、缺少引用、错误原生版本、证明内容不一致、存储类别不匹配及未登记存储分别通过 |
| 原始业务数据保持 | 各拒绝检查使用独立实际进程；技术输入引起的目标事务变更终止后，原382份元数据保持 |
| S3技术副本 | 使用一份已获准原图的完整字节，真实保存为明确技术派生副本，实际删除后登记与FileObject停止使用，准确journal及receipt重读通过 |
| 控制证明状态 | 删除前、准备后和完成后分别观察到准确journal及receipt状态，准备后普通读取停止 |
| 独立本地副本 | 同一真实输入在独立本地目录形成实际字节，删除和凭证重读通过，对应S3原件保持 |
| 最终原件核验 | 63份准确登记及物理版本保持，189处实际范围读取与原文件逐字节相同 |

完整专项核验执行77.61秒，准确报告为忽略目录QA33/reports/verified-current.json；最终七项复核为final.json。目标当前保存64份FileObject及64份S3登记，其中63份原件可用、一份技术副本已实际处置；290个原始页面保持。没有新增模型调用。

原件清单SHA256为dc7e0d330646a0aaf7e8439e047e4a292c9437ca7f1270b111e69dd23bbac70b。382份准确元数据SHA256为de91b03ddadc9b06e48db5c80342bdc889bcc11020216f658d33c5f96c0733b9。实际S3删除凭证SHA256为a5e7111e8cdea1eaf073eb8b1660368d4bb1d1c0a5c8cb7732c0c2cc2745eee8。原始字节、文件引用、来源元数据及凭据继续保存于忽略目录。

## 准确验证制品

| 文件 | SHA256 |
| --- | --- |
| files/purpose_schemas.py | ed43e526810cfef36f8441db89df91685d66d80234c46fdc94b248e10487c2f3 |
| files/disposition_storage.py | 701d2c4a8d2e0bd50fbf4d3c1ead1344cec3b93aa4b30247cd07783ad3752278 |
| files/disposition-object-proof.schema.json | 844296bf615d011958945d42473f6be5d8ea83427bd41ab4bc6f6656e11c4fe8 |
| retention/disposition.py | be20710fe06e6ea8a00d238b78b34134dfae73968d74ca1f2500261358cac07b |
| retention/jobs.py | bc40decdfe33ec4f2baab997693c4d9e5aecee269ac3e5b1d142600241416026 |
| tasks/registry.py | c9d14a1afd8c5a2e431b08264f83889822e205b7990fb1481c0579afcf653009 |
| tasks/dispatcher.py | 87b5a2bb3762513b7637852adda54ff719e8b72c8ec94e1475138cea1dbb600a |
| tasks/worker.py | fbb715447e0c9bdcca45963300f4f25fb9ff3962921667a118808521e2ef982d |
| tests/verify_disposition_storage.py | 40e912b4dda3c9f2f64fe83336fd42265c8339841ac9201e808ac76e7d95832c |

## 完整应用与验收

现有main仍使用LocalFileStore和f406制品，两套验收服务继续ready及admitted_current，两个前端可访问。原249项事实、290个页面、权限和两笔未知Ark请求的2 CNY预留保持。

factory、全部用途文件、内容清除manifest、恢复映射、控制目录备份、运行准入及实际部署继续完整接入。当前真实资料专项证明业务元数据、准确版本与存储处置适配，完整S3处置申请、批准、真实队列消费和恢复仍为NOT_RUN。临床Gold、专业确认、真实设备和视觉验收继续保留各自实际条件。
