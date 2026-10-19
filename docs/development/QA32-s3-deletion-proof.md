# QA32 S3处置证明与真实并发验证

组件专项检查PASS，last_verified为2026-10-19 07:45（Asia/Shanghai）。当前开发位置为codex/s3-private-storage，关联FR-02、FR-09、NFR-07、NFR-09、T12、T26、T33、T40、T56和T57。完整应用接入继续IN_PROGRESS，现有main验收服务保持。

Given 用户授权的真实原件、准确S3登记及独立PostgreSQL17，When 准备处置、准确删除、恢复实际未知阶段、终止数据库事务并执行真实并发读取和删除，Then journal先于删除请求持久保存，普通原件读取停止，准确原生版本缺失由真实查询确认，同项重试保留相同凭证和时间，原件保持完整。

## 实现与字段传播

| identifier | 实际行为 |
| --- | --- |
| S3DeletionJournal | 固定operation_id、registration_id、完整S3ObjectReference及实际准备时间 |
| S3RegisteredDeletion.prepare | 核验准确登记、Bucket、HEAD及单一原生版本；0600证明完整写入及fsync，通过独占link发布并同步目录 |
| S3RegisteredStore.get、get_range、exists | 使用与写入及删除相同的事务级advisory锁，持续核查登记和journal |
| S3RegisteredDeletion.delete | journal持久发布后执行准确VersionId与ETag删除，随后实际查询同键版本和标记 |
| S3DeletionReceipt、read_receipt | 保存实际S3DeleteResult与journal摘要；重新读取核查准确身份、时间、字节、权限及当前实际缺失 |
| 数据库处置状态 | 保存准确凭证verified_at，拒绝已经停止的登记重新启用 |
| 三份正式JSON Schema | 对提供方结果、journal及receipt逐项验证，准确引用复用正式对象Schema |

字段来源、状态、持久性、权限和证明范围见[处置证明规范](../../data-design/s3-deletion-proof.md)。准备证明位于独立控制目录；原始内容继续保存于私有S3。组件没有将文件系统device或inode作为S3身份。

## 实际执行

核验沿用QA31独立数据库sjia_qa31_storage及非超级用户，Python3.13、PostgreSQL17.11。没有启动新的API、Worker、调度器或模型请求。输入继续使用已核验的63份原件，共968528634字节；技术验证副本使用其中一份真实图片的准确字节。

| 检查 | 实际结果 |
| --- | --- |
| 准备后数据库事务终止 | journal继续存在，提供方版本仍在，数据库登记仍为available，普通读取及写入被拒绝 |
| 准确删除及重试 | 真实DeleteObject响应及实际缺失查询保存，重复执行返回相同凭证字节 |
| 实际外部阶段恢复 | journal已准备且提供方已实际删除、receipt尚不存在；恢复通过实际版本缺失查询完成，DeleteObject回执字段保持空值 |
| 删除后数据库事务终止 | 实际receipt及停止读取条件持续存在；相同操作使用原始凭证恢复登记，verified_at保持 |
| 两个真实删除进程 | 两个独立进程返回相同receipt_key、完整SHA256和实际凭证，单一原生版本已缺失 |
| 读取与准备竞争 | PostgreSQL实际观察到读取进程等待advisory锁；准备证明发布后，读取进程取得锁并拒绝原件读取 |
| 21项拒绝 | 非法操作身份、准确引用不一致、缺少凭证、停止读取及写入、其他处置身份、证明权限扩大，以及登记约束、重新启用和控制目录检查通过 |
| 63份原件 | 全部重新采用并完整读取，登记UUID、实际VersionId、时间及完整快照保持；最终189处实际范围读取与原文件逐字节相同 |
| 技术副本 | 本次五份副本均实际删除；累计71份登记包括63份可用原件及八份已处置技术登记，八份原生版本均已缺失 |

实际核验程序执行68.17秒。准确报告deletion.json及最终七项综合复核final.json保存于忽略目录QA32/reports；实际并发进程、读取等待观察、五份技术引用和日志分别保存。正式Schema和Pydantic共同核验实际journal、receipt及提供方响应。没有使用生成的临床资料或提供方替代响应。

原始登记快照SHA256为79001882cbab64eb96367e0583f6f5ab149756f65ea3b0b9d6c1c9296d56e0e9，来源清单SHA256为dc7e0d330646a0aaf7e8439e047e4a292c9437ca7f1270b111e69dd23bbac70b。临床记录、专业核对、评估及模型任务均为零。

## 准确验证制品

| 文件 | SHA256 |
| --- | --- |
| files/s3_storage.py | 630adf22b359c1fbf4b785834c478bce66bbfdfecda13d900c9570599ff556dc |
| files/s3_registered.py | c4999b896966f6c3911e8fafe4faaac078f9932d55d444384f5bcbd130cc2b2d |
| files/s3_deletion.py | 2cf2db16d361503c2cbbc7beea3a2e94b353b75515065b907796a6ceba2d72a5 |
| tests/verify_s3_deletion.py | 436d7a7f5bfd118181f215d6e6d6967fc96d7f83fa8ba7b9802edec8297eff18 |

实际验证报告SHA256为73f790a13924c92cd299fb7adb8257165fc27323dae15ad4cf70ded001431777。三份正式Schema及模型的准确摘要与本次验证制品共同保存于final.json，Django check、103项迁移状态及文件检查通过。

## 应用集成与完整验收

现有main仍使用LocalFileStore及f406制品，两套验收服务ready及admitted_current，两个前端可访问。原63份原件、290个页面、249项事实、当前权限及两笔未知Ark请求的2 CNY预留保持。图像处理四项输入与现有main逐项相同。

完整业务FileObject匹配、factory、全部用途副本、处置任务、内容清除、失败协调、全量迁移、控制目录备份、独立恢复证明、readiness和实际部署继续实施。当前组件证明限定准确原生版本及持久操作身份，备份、物理介质、外部处理系统和完整临床验收保持各自实际条件。V12、V26、V33、V40、V56、V57及原始全部功能要求保持。
