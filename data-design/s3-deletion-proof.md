# S3准确版本处置证明

本规范承接FR-09、NFR-07、NFR-09及S3版本登记规范，管理单一登记对象的准确提供方版本。实现为files/s3_deletion.py，独立控制目录保存证明，原始字节继续由S3保存。

## 身份、权限与状态

每项处置显式接收非空operation_id及完整S3ObjectReference。对象必须已经登记，binding、对象键、VersionId、ETag、SHA256及大小必须逐项相同。调用者的业务权限、保留政策与整体任务批准继续由应用处置入口检查；存储组件不会推断临床身份或代替业务授权。

同一binding及对象键使用登记适配的PostgreSQL事务级advisory锁和登记行锁，写入、采用和处置取得相同锁。每份不可变登记只允许一项处置操作；不同operation_id无法采用已有证明。

准备操作必须核验Bucket配置、准确HEAD及单一当前原生版本。成功后将journal持久保存至.s3-lifecycle/{binding}/{SHA256(object_key)}/journal.json，所有普通原件读取及写入通过相同journal位置停止访问。准备操作不会删除提供方版本。

## 正式证明字段

| 结构 | 字段与来源 |
| --- | --- |
| S3DeletionJournal | schema_version固定s3-deletion-journal-1；operation_id来自处置请求；registration_id来自实际登记；object_reference为准确物理引用；prepared_at为实际准备时间 |
| S3DeletionReceipt | schema_version固定s3-deletion-receipt-1；operation_id、registration_id、prepared_at与journal一致；journal_sha256来自准确已保存字节；result来自实际S3DeleteResult |
| S3DeleteResult | 准确operation_id及object_reference；真实DeleteObject请求身份与204状态，或已缺失恢复时空值；实际版本查询请求身份及verified_at |

模型使用严格字段及不可变值。UUID、完整摘要、准确引用及时间关系由Pydantic核验，读取证明时同时核验数据库登记与原始journal字节。停止状态的disposed_at与实际凭证verified_at相同。

正式JSON Schema分别为s3-delete-result.schema.json、s3-deletion-journal.schema.json及s3-deletion-receipt.schema.json，使用Draft202012及准确引用Schema。实际提供方结果、持久journal和读取凭证同时验证正式Schema与Pydantic模型；跨字段身份及时间顺序由运行模型和凭证读取检查执行。

## 持久性、重试与实际证明

控制目录沿用独立所有者、0700权限和固定文件系统身份要求。证明文件使用0600权限，完整写入及fsync后通过同目录link独占发布，随后fsync所有相关目录。提供方删除只有在准确journal已持久发布之后执行；保存失败直接抛出错误。

实际删除限定VersionId及ETag。返回之后重新查询全部同键原生版本及删除标记，仍有内容或标记时停止，不能生成成功凭证。receipt保存真实提供方结果及journal完整摘要，读取凭证时重新查询提供方缺失状态。

数据库事务终止不会撤销控制目录的journal或receipt，已经发布journal的对象持续停止原件读取。准确同项重试读取已有journal；提供方版本已经缺失时，结果明确记录恢复后的缺失查询，不生成DeleteObject回执。已有receipt使用原始准确字节和时间恢复登记停止状态。

journal与receipt的保留继续遵守整体处置与恢复政策。证明仅表达所列准确原生版本缺失，不独立证明备份、物理介质或其他系统副本清除。文件系统证明恢复、应用处置执行器、运行准入及生产环境继续完整集成验证。
