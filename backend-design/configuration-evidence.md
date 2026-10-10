# 配置批准与验证证明

## 来源引用与读取

`evidence_ref` 使用 `urn:sjia:source-revision:<UUID>`，UUID 必须采用规范小写形式。服务读取对应 SourceRevision 和 SourceRegistry；两者均需 approved，来源版本需要准确 checksum、非空 license、rights_evidence_ref、已生效保留策略及对应用途。

批准证据的 allowed_use 包含 `configuration_approval`。验证报告和参与验证的数据来源版本的 allowed_use 包含 `configuration_validation`。证据文件由独立来源登记及批准流程管理；配置接口不创建来源批准，不生成验证报告，不填写验证结果。

SourceRegistry.location 指向实际绝对文件路径。部署配置 `SJIA_CONFIG_EVIDENCE_ROOT` 指定允许读取的目录，`SJIA_CONFIG_EVIDENCE_MAX_BYTES` 指定单个文件字节上限；路径解析后必须位于该目录内。未提供目录或上限时返回 `CONFIG_EVIDENCE_UNAVAILABLE`。文件内容摘要必须与 SourceRevision.checksum 一致；验证报告还必须与 ValidationInput.evidence_sha256 一致。

## 验证报告结构

报告为 UTF-8 JSON，严格使用 `ConfigValidationReport`，不允许附加字段。结构由 `backend/configurations/evidence.py` 的 Pydantic 模型维护。

| 字段 | 类型与约束 |
| --- | --- |
| schema_version | `config-validation-1` |
| config_id | 实际配置 UUID |
| config_checksum | 准确配置正文 SHA256 |
| environment | 当前运行环境 |
| artifact_sha256 | 实际执行制品 SHA256 |
| dataset_revision_id | 实际获准数据来源版本 UUID |
| outcome | 实际运行结果 `pass` 或 `fail` |
| executed_by | 实际执行人员 UUID |
| executed_at | 包含时区的实际执行时间 |

服务器读取报告中的 outcome、executed_by、executed_at，ValidationInput 不接收客户端 outcome。执行时间不得早于配置创建和数据来源采集，不得晚于报告来源采集或当前时间。实际执行人员必须为 active，具有对应环境的 register_validation 岗位证明；该证明在执行时间内有效，当前没有撤销。

普通配置的制品摘要由 `deployed_artifact_checksum` 计算：对本项目已安装 Django app 的 Python／JSON 文件、backend/config 的 Python 文件、manage.py、requirements.lock.txt，以及 backend-design、alert-design、data-design 的 Schema 文件分别计算摘要，再对按路径排序的映射计算 canonical JSON SHA256。`purpose-processing-2` 使用实际 `backend/files/purpose_processing.py` 文件摘要。启用时重新计算并比对，代码变化需要相应真实验证报告。

## 权限与状态

保存、读取和提交需要 manage_configs；批准与撤销需要 approve_config；登记验证需要 register_validation；启用与停用需要 publish_config。每次写入绑定本人提交意图、准确正文、岗位授权、环境和保存策略，并在同一事务保存业务结果、WriteRequest 和 AuditEvent。

配置状态依次为 draft、pending、approved、enabled、disabled。批准明确 environment、config_checksum、audience、approval_role、evidence_ref 和 valid_until。启用需要当前有效批准、准确验证报告、当前制品摘要和期望环境版本。停用和撤销批准更新环境版本及 publication_epoch，并使已经登记的依赖失效。

purpose-processing-2 的创建、读取、提交、批准、验证、启用及请求恢复继续核查准确原件、病例、用途、目标、来源和当前权限。批准角色为 data_processing，audience 为 support；effective_scope 由实际处理方案生成。验证数据集必须等于原件文件组的 source_revision_id。启用后调用 lock_processing_config 再次核查准确处理条件。

archive-quota-1 使用 provider 配置类别，结构定义为 `backend/archives/quota.schema.json`，跨字段检查由 `archives.quota.quota_reason` 执行。全部额度、期限与准确引用必须由请求提供，费用需要实际测量及计价证据；保存配置不证明这些证据已获准或费用可以采用。配置操作继续核查现有 manage_configs、approve_config、register_validation、publish_config 岗位；approval_role 需要真实批准依据，系统不创建角色、岗位许可或批准。批次消费还需核查当前 runtime 与 Provider 引用、模型、测量和计价证明，以及当前配置验证制品。

## 家庭读取资格

字段配置、家庭发布、联系信息以及摘要模板和可见策略通过 configuration_qualification_reason 核查当前数据库版本、准确环境、具体 Schema、正文摘要、批准范围中的 family audience、来源及保留状态、实际报告、执行人员和制品摘要。证据读取与写入共用 inspect_approved_evidence 和 inspect_validation_report；读取使用纯查询，写入锁定相关记录。

当前字段定义只采用唯一具备资格的启用版本。明确读取历史版本时，只有仍具备有效证明的历史定义可以返回 read_only，全部 writable 为 false。家庭入口按当前 read_raw 许可过滤字段，按 write_record 许可限制写入；字段名称、限制和预警关注字段均不越过同一允许集合。摘要资格无效时返回 needs_regeneration=true 和空 items，原始数据库项目保持不变。

## 当前验收范围

现有配置记录仅用于技术读取、协议、状态拒绝及内容摘要保留检查。来源证明目录、真实批准材料、实际验证报告和正向人员提交尚未验证；完整配置生命周期业务验收保持 NOT_RUN。
