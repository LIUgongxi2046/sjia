from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ApplicationDeclaration(BaseModel):
    """Applicant statements only; these are never patient identity or D02 proof."""
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)
    schema_version: Literal['1.0.0']
    declared_patient_name: Annotated[str, Field(min_length=1, max_length=100)]
    declared_relationship: Literal['mother', 'father', 'other_guardian']
    guardian_scope_acknowledged: bool

    @field_validator('declared_patient_name')
    @classmethod
    def meaningful_name(cls, value):
        if not value.strip():
            raise ValueError('请填写申请人所述的患儿称呼。')
        return value

    @field_validator('guardian_scope_acknowledged')
    @classmethod
    def explicit_acknowledgement(cls, value):
        if value is not True:
            raise ValueError('请明确确认申请资料属于本人监护范围。')
        return value


class ApplicationInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)
    evidence_ref: Annotated[str, Field(min_length=1, max_length=10000)]
    note: Annotated[str, Field(max_length=10000)] | None
    requested_access: Literal['read', 'read_write', 'review_decides']
    applicant_declaration: ApplicationDeclaration | None = None

    @field_validator('applicant_declaration')
    @classmethod
    def complete_declaration_if_supplied(cls, value):
        if value is None:
            raise ValueError('提交申请人声明时必须填写完整内容。')
        return value

    @field_validator('evidence_ref')
    @classmethod
    def meaningful_evidence(cls, value):
        if not value.strip():
            raise ValueError('申请说明不能为空。')
        return value


class ApplicationAction(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_state: Literal['pending']
    expected_version: Annotated[str, Field(pattern=r'^[1-9][0-9]*$', max_length=19)]
    reason: Annotated[str, Field(min_length=1, max_length=10000)]

    @field_validator('expected_version')
    @classmethod
    def database_version(cls, value):
        if int(value) > 9_223_372_036_854_775_807:
            raise ValueError('申请版本超出数据库范围。')
        return value

    @field_validator('reason')
    @classmethod
    def meaningful_reason(cls, value):
        if not value.strip():
            raise ValueError('撤回说明不能为空。')
        return value


class ApplicationIntentInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    request_id: Annotated[str, Field(min_length=36, max_length=36)]
    operation_id: Literal['application_create', 'application_withdraw']
    target_key: Annotated[str, Field(min_length=1, max_length=256)]
    schema_version: Literal['1.0.0']
    payload: dict[str, Any]

    @field_validator('request_id')
    @classmethod
    def canonical_uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError('请求标识格式无效。')
        return value


class ApplicationWriteHeaders(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    intent_id: Annotated[str, Field(min_length=36, max_length=36)]
    request_id: Annotated[str, Field(min_length=36, max_length=36)]
    generation: Annotated[str, Field(pattern=r'^[1-9][0-9]*$', max_length=19)]

    @field_validator('intent_id', 'request_id')
    @classmethod
    def canonical_uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError('提交标识格式无效。')
        return value


class ApplicationRetryInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_generation: Annotated[str, Field(pattern=r'^[1-9][0-9]*$', max_length=19)]


class ApplicationDecisionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_version: Annotated[str, Field(pattern=r'^[1-9][0-9]*$', max_length=19)]
    decision: Literal['approved', 'rejected']
    patient_id: UUID | None = Field(default=None, strict=False)
    reason: Annotated[str, Field(min_length=1, max_length=10000)]
    action: Literal['read_raw', 'write_record', 'read_history', 'read_file', 'view_request',
        'manage_draft', 'create_summary', 'read_summary', 'request_correction', 'correct_record',
        'receive_alert', 'request_disposition'] | None = None
    field_codes: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list)

    @field_validator('expected_version')
    @classmethod
    def bounded_version(cls, value):
        if int(value) >= 9_223_372_036_854_775_807:
            raise ValueError('申请版本超出范围。')
        return value

    @field_validator('field_codes')
    @classmethod
    def distinct_codes(cls, value):
        if len(set(value)) != len(value) or any(not code.strip() for code in value):
            raise ValueError('字段范围无效。')
        return value

    @model_validator(mode='after')
    def decision_scope(self):
        if self.reason is not None and not self.reason.strip():
            raise ValueError('处理原因不能为空。')
        if self.decision == 'rejected' and (self.action is not None or self.field_codes or self.patient_id is not None):
            raise ValueError('拒绝申请不能同时授予权限或指定新的患儿。')
        return self
