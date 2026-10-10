# T48 来源登记与集合版本

## 来源引用

方案、清单、单位依据、独立答案、分组和冻结证明使用 `urn:sjia:source-revision:<uuid>`，引用实际 SourceRevision。SourceRegistry 和 SourceRevision 都需要 approved 状态，来源拥有授权依据、license、有效保留策略及准确 SHA256。登记时间不得晚于当前时间。

证明文件保存在 `CONFIG_EVIDENCE_ROOT` 的既有受控证据目录，大小受 `CONFIG_EVIDENCE_MAX_BYTES` 限制。该目录只读取已登记的元数据证明；病例原件和用途副本由文件服务检查当前许可后读取。原件路径不会从证明内容直接执行。

| 证明 | 额外 allowed_use | 内容 |
| --- | --- | --- |
| 固定方案及测量依据 | evaluation_protocol | EvaluationProtocol、独立测量方案和统计依据 |
| 来源及单位映射 | evaluation_manifest | SourceManifest、EvaluationUnitManifest 和逐单位来源依据 |
| 标签版本 | evaluation_labels | EvaluationLabels，与映射内完整标签声明一致 |
| 事件确认 | evaluation_event | SamplingUnit 或 MasUnit 的准确 JSON 内容 |
| 独立答案 | evaluation_answer | 对应 LabelUnit 或 MasUnit 的准确 JSON 内容 |
| 患儿分组 | evaluation_partition | DatasetPartitionEvidence |
| 集合冻结 | evaluation_freeze | DatasetFreezeEvidence |

每项证明同时需要实际评价用途。格式、流程和临床用途分别为 format_evaluation、workflow_evaluation、clinical_evaluation；类别之间不会相互授予使用资格。

## 固定方案与清单

`evaluation-protocol-1` 保存 purpose、evaluation_kind、measurement_plan_ref、available_before、required_units、criteria_refs。测量依据必须在固定方案登记时已经存在；资料可得截止时间不得晚于当前时间。格式评价要求文件、患儿及页面范围，流程评价要求文件、患儿及文书范围，临床评价要求文件、患儿、观察范围和标签。

`evaluation-unit-manifest-1` 保存原来源清单的准确 SHA256，以及 raw_source_manifest_ref。原清单引用可以在仅检查本地文件的诊断资料中保持 null；采用为业务集合时必须提供实际批准引用。

每类单位分别记录 complete、partial 或 null。部分范围必须说明局限；声明完整的空范围仍需要独立依据。文件、页面、文书、患儿、就诊、采样事件、MAS 事件、观察区间与标签分别统计；相同字节不增加临床事件资格。

文件映射必须覆盖准确原件和用途副本。用途副本的 target 必须等于 protocol_ref；复制用于其他方案的副本不能直接进入当前集合。登记的用途副本集合必须与输入完全一致。逐项核对当前主体、患儿、用途、环境、授权、人工复核、版本、摘要以及实际字节。

页面关联 FilePage；文书关联准确 ArchiveDocumentVersion 和完整页面集合；就诊关联 ClinicalEpisode。采样事件和 MAS 事件登记实际来源字段、时间、就诊、确认人员、确认时间及独立确认文件，不根据数值或页面数量生成事件。观察范围关联准确 ObservationPeriodVersion 及其 MedicalTime。

资料字段、文书、原件及观察记录的可得时间受固定方案限制。观察时间未解析时，人时保持 null；精确时间区间需要正向排列，同患儿观察区间不得重复计时。人时由实际持续微秒数换算，不从时间精度不明的文字推断。

## 独立答案

`evaluation-labels-1` 绑定 protocol_ref、source_manifest_sha256 与完整 UnitCoverage。单位映射中的标签与该版本逐项一致。完整标签范围覆盖全部已登记观察版本。

positive 和 negative 必须有独立答案、确认人员和确认时间。确认人员在当时及当前拥有对应岗位许可和患儿范围；实际参考文件与声明逐项相同。unknown 保留 limitations，不生成阴性答案。来源、答案和用途证明需要在读取及冻结时继续有效。

## 集合保存与读取

dataset_id 表示持续目录身份，version_id 表示准确数据库版本。初次创建时 DatasetInput 的 dataset_id 和 expected_version 同时为 null；更新来源时同时提供已有身份和当前版本。来源更新形成新的 draft 版本，重新登记分组和标签。

分组、标签和冻结也产生新版本，历史版本及其子记录保持完整。当前读取返回最新版本，指定 version 时返回准确历史版本。来源内容、业务对象摘要、用途许可、批准证明及版本完整性在读取时重新检查。

同一方案的 SourceRegistry 身份用于跨版本检查；同一患儿在该方案的全部历史版本中不能跨 development 和 validation。分组证明绑定患儿、方案、来源清单、分组和全部实际用途授权。

冻结证明绑定准确 dataset_id、version_id、version、checksum 和 protocol_ref。冻结要求方案规定的统计范围完整、全部患儿已经分组、映射内标签已经登记，以及对应时间分母可计算。冻结不会执行评价，也不会产生临床表现结论。

## 当前验证范围

本地来源完整性、严格输入和未批准来源拒绝已有实际检查。现有五份来源文件没有对应正式 FileObject；当前评价集合、分组、标签和评价运行均为零。非空获准集合、完整写入恢复、冻结以及临床评价仍需实际资料和业务验证。

账号由用户通过现有注册流程创建；注册本身不产生岗位许可、患儿访问权限或资料用途批准。本聊天按照用户要求，在整个 T48 完成后暂停，另一聊天继续开发。
