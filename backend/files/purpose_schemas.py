from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from authorization.actions import CASE_ACTIONS, SERVICE_ACTIONS


Text = Annotated[str, Field(min_length=1, pattern=r'\S')]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Version = Annotated[str, Field(pattern=r'^[1-9][0-9]*$', max_length=19)]
Purpose = Literal['external_recognition', 'format_evaluation', 'workflow_evaluation', 'clinical_evaluation', 'competition_display', 'export']
Scope = Literal['image_text', 'confirmed_text', 'model_input', 'log', 'presentation']


class PurposeModel(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', allow_inf_nan=False)


class PurposeCopyInput(PurposeModel):
    purpose: Purpose
    target: Text
    authorization_id: UUID = Field(strict=False)
    processing_config_id: UUID = Field(strict=False)


class PurposeOptionsInput(PurposeModel):
    purpose: Purpose


class ScopeResult(PurposeModel):
    scope: Scope
    result: Literal['passed', 'failed', 'not_applicable']


class PurposeReviewInput(PurposeModel):
    expected_version: Version
    checksum: Digest
    scope_results: Annotated[list[ScopeResult], Field(min_length=1)]
    clinical_integrity: Literal['passed', 'failed', 'unknown']
    date_shift_consistency: Literal['passed', 'failed', 'not_applicable', 'unknown']
    evidence_ref: Text

    @model_validator(mode='after')
    def unique_scopes(self):
        if len({item.scope for item in self.scope_results}) != len(self.scope_results):
            raise ValueError('检查范围必须唯一')
        return self


class PurposeRevokeInput(PurposeModel):
    expected_version: Version
    reason: Text


class PurposeWriteHeaders(PurposeModel):
    intent_id: UUID = Field(strict=False)
    request_id: UUID = Field(strict=False)
    generation: Version


class PurposeIntentInput(PurposeModel):
    operation_id: Literal['purpose_copy_create', 'purpose_copy_review', 'purpose_copy_revoke', 'simulation_provider_copy_create',
        'disposition_approve', 'disposition_execute', 'disposition_retain', 'disposition_retry']
    target_key: Text
    request_id: UUID = Field(strict=False)
    schema_version: Literal['2.0.0']
    payload: dict


class PurposeRetryInput(PurposeModel):
    expected_generation: Version


class PurposeProviderCopyInput(PurposeModel):
    expected_version: Version
    review_id: UUID = Field(strict=False)


class PurposeDispositionTarget(PurposeModel):
    target_type: Text
    target_id: Text
    target_version: Text

    @model_validator(mode='after')
    def file_identity(self):
        if self.target_type in ('file_object', 'purpose_copy', 'provider_copy'):
            UUID(self.target_id)
        return self


class PurposeLocalDispositionRule(PurposeModel):
    schema_version: Literal['local-disposition-2']
    target_types: Annotated[list[Literal['file_object', 'purpose_copy', 'provider_copy']], Field(min_length=1)]
    access_purpose: Text
    receipt_policy_id: UUID = Field(strict=False)

    @model_validator(mode='after')
    def unique_types(self):
        if len(set(self.target_types)) != len(self.target_types):
            raise ValueError('删除对象类别必须唯一')
        return self


class PurposeDispositionFileGrant(PurposeModel):
    file_id: UUID = Field(strict=False)
    grant_ids: Annotated[list[Annotated[UUID, Field(strict=False)]], Field(min_length=1)]


class PurposeDispositionObjectProof(PurposeModel):
    file_id: UUID = Field(strict=False)
    object_version: Text
    object_key: Text
    checksum: Digest
    size_bytes: Annotated[int, Field(ge=0)]
    retention_policy_id: UUID = Field(strict=False)


class PurposeDispositionApprovalContext(PurposeModel):
    approver_id: UUID = Field(strict=False)
    auth_epoch: Annotated[int, Field(ge=0)]
    service_grant_id: UUID = Field(strict=False)
    environment: Text
    policy_version: Version
    policy_sha256: Digest
    scope_sha256: Digest
    evidence_ref: Text
    file_grants: list[PurposeDispositionFileGrant]
    file_versions: Annotated[list[PurposeDispositionObjectProof], Field(min_length=1)]


class PurposeDispositionApprovalInput(PurposeModel):
    policy_version: Version
    expected_scope_sha256: Digest
    evidence_ref: Text


class PurposeDispositionExecuteInput(PurposeModel):
    policy_version: Version
    expected_scope_sha256: Digest
    expected_target_version: Text


class PurposeDispositionRetainInput(PurposeDispositionExecuteInput):
    retention_evidence_ref: Text
    recheck_at: AwareDatetime = Field(strict=False)


class PurposeDispositionRetryInput(PurposeModel):
    expected_status_version: Version


class PurposeDispositionTaskRefs(PurposeModel):
    disposition_id: UUID = Field(strict=False)
    item_id: UUID = Field(strict=False)
    environment: Text
    auth_epoch: Annotated[int, Field(ge=0)]
    policy_version: Version
    policy_sha256: Digest
    scope_sha256: Digest
    approval_sha256: Digest
    target_version: Text
    file_identity: PurposeDispositionObjectProof
    previous_task_id: UUID | None = Field(strict=False)


class PurposeDispositionScope(PurposeModel):
    targets: Annotated[list[PurposeDispositionTarget], Field(min_length=1)]
    approval_context: PurposeDispositionApprovalContext | None = None

    @model_validator(mode='after')
    def unique_targets(self):
        keys = {(item.target_type, item.target_id, item.target_version) for item in self.targets}
        if len(keys) != len(self.targets):
            raise ValueError('处置对象必须唯一')
        return self


class PurposeDispositionInput(PurposeModel):
    targets: Annotated[list[PurposeDispositionTarget], Field(min_length=1)]
    policy_id: UUID = Field(strict=False)
    evidence_ref: Text

    @model_validator(mode='after')
    def caller_scope(self):
        PurposeDispositionScope(targets=self.targets)
        return self


class PurposeDispositionReceiptInput(PurposeModel):
    item_id: UUID = Field(strict=False)
    receipt_ref: Text
    receipt_sha256: Digest


class PurposeConfigScope(PurposeModel):
    patient_id: UUID = Field(strict=False)
    original_file_id: UUID = Field(strict=False)
    purpose: Purpose
    target: Text
    environment: Text
    config_checksum: Digest


class PurposeConfigApprovalInput(PurposeModel):
    config_checksum: Digest
    approval_role: Literal['data_processing']
    evidence_ref: Text
    effective_scope: PurposeConfigScope
    valid_until: AwareDatetime | None = Field(strict=False)


class PurposeConfigValidationInput(PurposeModel):
    config_checksum: Digest
    environment: Text
    artifact_sha256: Digest
    dataset_revision_id: UUID = Field(strict=False)
    evidence_ref: Text
    evidence_sha256: Digest
    outcome: Literal['pass', 'fail']


class PurposeConfigEnableInput(PurposeModel):
    config_checksum: Digest
    environment: Text


class PurposeUseAuthorizationInput(PurposeModel):
    source_revision_id: UUID = Field(strict=False)
    purpose: Text
    environment: Text
    evidence_ref: Text
    deidentification_ref: Text | None
    valid_until: AwareDatetime | None = Field(strict=False)


class PurposeCaseGrantInput(PurposeModel):
    patient_id: UUID = Field(strict=False)
    principal_id: UUID = Field(strict=False)
    relation_id: UUID | None = Field(strict=False)
    action: Text
    field_codes: list[Text]
    record_scope: Literal['own', 'all', 'assigned']
    assigned_record_id: UUID | None = Field(strict=False)
    purpose: Text
    evidence_ref: Text
    valid_from: AwareDatetime = Field(strict=False)
    valid_until: AwareDatetime | None = Field(strict=False)

    @model_validator(mode='after')
    def grant_scope(self):
        if self.action not in CASE_ACTIONS or len(set(self.field_codes)) != len(self.field_codes):
            raise ValueError('病例动作或字段范围无效')
        if (self.record_scope == 'assigned') != (self.assigned_record_id is not None):
            raise ValueError('分配范围必须与记录身份一致')
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError('授权有效期必须晚于开始时间')
        return self


class PurposeServiceGrantInput(PurposeModel):
    principal_id: UUID = Field(strict=False)
    environment: Annotated[str, Field(min_length=1, max_length=64)]
    action: Annotated[str, Field(min_length=1, max_length=64)]
    evidence_ref: Text
    valid_from: AwareDatetime = Field(strict=False)
    valid_until: AwareDatetime | None = Field(strict=False)

    @model_validator(mode='after')
    def grant_scope(self):
        if self.action not in SERVICE_ACTIONS:
            raise ValueError('服务动作没有登记')
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError('授权有效期必须晚于开始时间')
        return self


class Redaction(PurposeModel):
    category: Literal['body_text', 'header_footer', 'image_identity', 'barcode', 'qrcode']
    box: Annotated[list[int], Field(min_length=4, max_length=4)]


class IdentityCoverage(PurposeModel):
    category: Literal['body_text', 'header_footer', 'image_identity', 'barcode', 'qrcode', 'file_name', 'metadata']
    action: Literal['redacted', 'removed', 'not_present']
    evidence_ref: Text


class DateTransformation(PurposeModel):
    shift_days: int
    evidence_ref: Text
    applicability: Literal['replay_preserved', 'replay_requires_review']


class PurposeProcessingPlan(PurposeModel):
    schema_version: Literal['purpose-processing-2']
    original_file_id: UUID = Field(strict=False)
    original_checksum: Digest
    original_version: Text
    patient_id: UUID = Field(strict=False)
    environment: Text
    purpose: Purpose
    target: Text
    file_page_id: UUID | None = Field(default=None, strict=False)
    mode: Literal['redact_regions', 'prepared_image']
    redactions: list[Redaction]
    prepared_file_id: UUID | None = Field(strict=False)
    prepared_checksum: Digest | None
    date_strategy: Literal['unchanged', 'consistent_shift']
    date_transformation: DateTransformation | None
    identity_coverage: list[IdentityCoverage]
    required_scopes: Annotated[list[Scope], Field(min_length=1)]
    clinical_preservation_evidence_ref: Text

    @model_validator(mode='after')
    def complete_plan(self):
        categories = {'body_text', 'header_footer', 'image_identity', 'barcode', 'qrcode', 'file_name', 'metadata'}
        if {item.category for item in self.identity_coverage} != categories or len(self.identity_coverage) != len(categories):
            raise ValueError('身份处理范围必须完整且唯一')
        coverage = {item.category: item.action for item in self.identity_coverage}
        if coverage['file_name'] != 'removed' or coverage['metadata'] != 'removed':
            raise ValueError('文件名与元数据必须移除')
        if len(set(self.required_scopes)) != len(self.required_scopes) or not {'image_text', 'log'} <= set(self.required_scopes):
            raise ValueError('检查范围必须包含图片文字和日志且保持唯一')
        if self.purpose == 'external_recognition' and 'model_input' not in self.required_scopes:
            raise ValueError('外部识别必须检查模型输入')
        if self.purpose == 'competition_display' and 'presentation' not in self.required_scopes:
            raise ValueError('比赛展示必须检查展示材料')
        if self.date_strategy == 'consistent_shift' and (self.mode != 'prepared_image' or self.date_transformation is None):
            raise ValueError('日期调整需要批准材料和一致性证明')
        if self.date_strategy == 'unchanged' and self.date_transformation is not None:
            raise ValueError('保留日期时不得声明日期调整')
        if self.mode == 'prepared_image':
            if self.prepared_file_id is None or self.prepared_checksum is None:
                raise ValueError('处理材料必须明确文件和校验和')
        elif self.prepared_file_id is not None or self.prepared_checksum is not None:
            raise ValueError('区域处理不得指定其他输入文件')
        if self.mode == 'redact_regions':
            for category in categories - {'file_name', 'metadata'}:
                boxes = [item for item in self.redactions if item.category == category]
                if coverage[category] == 'redacted' and not boxes:
                    raise ValueError('每项区域处理必须提供坐标')
                if coverage[category] != 'redacted' and boxes:
                    raise ValueError('区域处理和证明必须一致')
        return self


class ConfirmedTextProcessingPlan(PurposeModel):
    schema_version: Literal['purpose-processing-text-1']
    original_file_id: UUID = Field(strict=False)
    original_checksum: Digest
    original_version: Text
    patient_id: UUID = Field(strict=False)
    environment: Text
    purpose: Purpose
    target: Text
    file_page_id: UUID = Field(strict=False)
    mode: Literal['prepared_text']
    redactions: Annotated[list, Field(max_length=0)]
    prepared_file_id: UUID = Field(strict=False)
    prepared_checksum: Digest
    date_strategy: Literal['unchanged']
    date_transformation: None
    identity_coverage: Annotated[list[IdentityCoverage], Field(min_length=7, max_length=7)]
    required_scopes: Annotated[list[Scope], Field(min_length=3)]
    clinical_preservation_evidence_ref: Text

    @model_validator(mode='after')
    def complete_scope(self):
        coverage = {row.category: row.action for row in self.identity_coverage}
        required = {'body_text', 'header_footer', 'image_identity', 'barcode', 'qrcode', 'file_name', 'metadata'}
        if set(coverage) != required or len(coverage) != len(self.identity_coverage):
            raise ValueError('文字处理身份范围必须完整且唯一')
        if coverage['file_name'] != 'removed' or coverage['metadata'] != 'removed':
            raise ValueError('文件名称及元数据必须具有明确移除依据')
        if any(coverage[name] != 'not_present' for name in ('image_identity', 'barcode', 'qrcode')):
            raise ValueError('纯文字资料不能包含图像身份处理声明')
        if len(set(self.required_scopes)) != len(self.required_scopes) or not {
                'confirmed_text', 'model_input', 'log'} <= set(self.required_scopes):
            raise ValueError('文字、模型输入与日志需要独立审核')
        return self
