"""Bounded erasure contracts. Policies cannot name SQL, columns or JSON paths."""
from typing import Annotated, Literal
from uuid import UUID
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from files.s3_storage import S3ObjectReference

Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Text = Annotated[str, Field(min_length=1, max_length=256, pattern=r'.*\S.*')]


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class DatabaseTarget(Contract):
    kind: Literal['database']
    adapter: Text
    object_id: UUID
    version: Text


class LocalObjectTarget(Contract):
    kind: Literal['local_object']
    file_id: UUID
    object_version: Text


class S3ObjectTarget(Contract):
    kind: Literal['s3_object']
    file_id: UUID
    object_version: Text


LocalTarget = Annotated[DatabaseTarget | LocalObjectTarget, Field(discriminator='kind')]
S3Target = Annotated[DatabaseTarget | S3ObjectTarget, Field(discriminator='kind')]
Target = Annotated[DatabaseTarget | LocalObjectTarget | S3ObjectTarget, Field(discriminator='kind')]


class ContentPolicyRules(Contract):
    schema_version: Literal['content-disposition-policy-1']
    mode: Literal['retain', 'revoke_access', 'erase_selected_content']
    profile: Literal['relationship-skeleton-1']
    registry_sha256: Digest
    # This list must equal the registry's exact preservation contract, including
    # identities, provenance/configuration IDs, retry hashes and operational time.
    preservation_sha256: Digest
    allowed_adapters: list[Text] = Field(min_length=1, max_length=100)
    access_purpose: Text
    retention_seconds: int = Field(ge=0, strict=True)
    clock: Literal['recorded_at', 'created_at', 'saved_at', 'generated_at', 'started_at', 'confirmed_at']
    receipt_keep_until: AwareDatetime
    metadata_keep_until: AwareDatetime
    recheck_at: AwareDatetime
    local_objects: bool
    all_copies_required: bool
    @model_validator(mode='after')
    def unique_adapters(self):
        if len(set(self.allowed_adapters)) != len(self.allowed_adapters):
            raise ValueError('duplicate adapters')
        return self


class ContentPlanInput(Contract):
    schema_version: Literal['content-disposition-request-1']
    targets: list[Target] = Field(min_length=1, max_length=5000)
    hold_id: UUID
    hold_version: int = Field(ge=1, strict=True)


class S3ContentPolicyRules(ContentPolicyRules):
    schema_version: Literal['content-disposition-policy-2']
    local_objects: Literal[False]
    s3_objects: bool
    allowed_file_purposes: list[Literal['original', 'thumbnail', 'deidentified', 'ocr_response']] = Field(min_length=1, max_length=4)

    @model_validator(mode='after')
    def unique_file_purposes(self):
        if len(set(self.allowed_file_purposes)) != len(self.allowed_file_purposes):
            raise ValueError('CONTENT_S3_POLICY_PURPOSES_INVALID')
        return self


PolicyRules = Annotated[ContentPolicyRules | S3ContentPolicyRules, Field(discriminator='schema_version')]


def parse_policy_rules(value):
    return TypeAdapter(PolicyRules).validate_python(value)


class DatabaseProof(DatabaseTarget):
    row_sha256: Digest


class LocalObjectProof(LocalObjectTarget):
    object_key: Text
    checksum: Digest
    size_bytes: int = Field(ge=0, strict=True)
    operation_id: UUID


Proof = Annotated[DatabaseProof | LocalObjectProof, Field(discriminator='kind')]


class S3ObjectProof(S3ObjectTarget):
    object_key: Text
    checksum: Digest
    size_bytes: int = Field(ge=0, strict=True)
    operation_id: UUID
    storage_reference: S3ObjectReference

    @model_validator(mode='after')
    def reference_identity(self):
        if (self.operation_id.int == 0 or self.object_key != self.storage_reference.object_key
                or self.checksum != self.storage_reference.checksum or self.size_bytes != self.storage_reference.size_bytes):
            raise ValueError('CONTENT_S3_OBJECT_IDENTITY_MISMATCH')
        return self


S3Proof = Annotated[DatabaseProof | S3ObjectProof, Field(discriminator='kind')]


class ContentManifest(Contract):
    family_request_id: UUID | None = None
    schema_version: Literal['content-disposition-manifest-1']
    patient_id: UUID
    policy_id: UUID
    policy_version: int = Field(ge=1, strict=True)
    policy_sha256: Digest
    registry_sha256: Digest
    preservation_sha256: Digest
    mode: Literal['retain', 'revoke_access', 'erase_selected_content']
    environment: Text
    hold_id: UUID
    hold_version: int = Field(ge=1, strict=True)
    roots: list[LocalTarget] = Field(min_length=1, max_length=5000)
    items: list[Proof] = Field(min_length=1, max_length=5000)
    obligations: list[Literal['physical_database_storage', 'external_provider_copies', 'backup_copies']]
    created_at: AwareDatetime


class S3ContentManifest(ContentManifest):
    schema_version: Literal['content-disposition-manifest-2']
    storage_binding_sha256: Digest
    roots: list[S3Target] = Field(min_length=1, max_length=5000)
    items: list[S3Proof] = Field(min_length=1, max_length=5000)

    @model_validator(mode='after')
    def binding_identity(self):
        if any(item.kind == 's3_object' and item.storage_reference.binding_sha256 != self.storage_binding_sha256
                for item in self.items):
            raise ValueError('CONTENT_S3_MANIFEST_BINDING_MISMATCH')
        return self


Manifest = Annotated[ContentManifest | S3ContentManifest, Field(discriminator='schema_version')]


def parse_manifest(value):
    return TypeAdapter(Manifest).validate_python(value)


class ContentApproval(Contract):
    schema_version: Literal['content-disposition-approval-1']
    manifest_sha256: Digest
    policy_sha256: Digest
    approver_id: UUID
    auth_epoch: int = Field(ge=0, strict=True)
    service_grant_id: UUID
    case_grant_ids: list[UUID] = Field(min_length=1)
    source_revision_id: UUID
    approved_at: AwareDatetime


class RetainedReceipt(Contract):
    schema_version: Literal['content-disposition-receipt-1']
    logical_payload_erased: Literal[False]
    all_copies_erased: Literal[False]
    retained_until: AwareDatetime
    recheck_at: AwareDatetime


class RevokedReceipt(Contract):
    schema_version: Literal['content-disposition-receipt-1']
    logical_payload_erased: Literal[False]
    all_copies_erased: Literal[False]
    access_revoked: Literal[True]
    journal_sequence: int = Field(ge=1)


class ErasedReceipt(Contract):
    schema_version: Literal['content-disposition-receipt-1']
    manifest_sha256: Digest
    journal_sequence: int = Field(ge=1)
    logical_payload_erased: Literal[True]
    all_copies_erased: Literal[False]
    unverified_copy_obligations: list[Literal['physical_database_storage', 'external_provider_copies', 'backup_copies']]
    proof_scope: Literal['current_database_payload_and_controlled_local_path_absence']


class S3ErasedReceipt(ErasedReceipt):
    schema_version: Literal['content-disposition-receipt-2']
    storage_binding_sha256: Digest
    proof_scope: Literal['current_database_payload_and_controlled_s3_version_absence']


class ContentPlanView(Contract):
    schema_version: Literal['content-disposition-plan-view-1']
    plan_id: UUID
    patient_id: UUID
    status: Literal['preview', 'approved', 'retained', 'access_revoked', 'erasing', 'logical_erasure_complete']
    manifest: Manifest
    manifest_sha256: Digest
    approved_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    receipt: RetainedReceipt | RevokedReceipt | ErasedReceipt | S3ErasedReceipt | None
    runtime_execution_enabled: bool


class ContentHoldAttestation(Contract):
    schema_version: Literal['content-disposition-hold-1']
    hold_id: UUID
    patient_id: UUID
    hold_version: int = Field(ge=1, strict=True)
    environment: Text
    state: Literal['clear', 'held', 'unresolved']
    reviewed_by: UUID
    reviewed_at: AwareDatetime
    valid_until: AwareDatetime


class ContentPolicyAttestation(Contract):
    schema_version: Literal['content-disposition-policy-approval-1']
    policy_id: UUID
    policy_version: int = Field(ge=1, strict=True)
    environment: Text
    rules_sha256: Digest
    approved_by: UUID
    approved_at: AwareDatetime
    execution_enabled: bool


class ContentReviewAttestation(Contract):
    schema_version: Literal['content-disposition-review-1']
    plan_id: UUID
    manifest_sha256: Digest
    policy_sha256: Digest
    review_version: int = Field(ge=1, strict=True)
    reviewed_by: UUID
    reviewed_at: AwareDatetime
    next_recheck_at: AwareDatetime
    reason_code: Literal['approved_retention_continues', 'copy_obligations_unresolved',
        'hold_requires_review', 'metadata_retry_window_unproven']


class ContentLifecycle(Contract):
    schema_version: Literal['content-disposition-lifecycle-1']
    plan_id: UUID
    retention_basis_source_revision_id: UUID
    next_recheck_at: AwareDatetime
    review_due: bool
    review_evidence_status: Literal['not_reviewed','valid','invalid_or_revoked']
    review_version: int = Field(ge=0)
    receipt_keep_until: AwareDatetime
    metadata_keep_until: AwareDatetime
    receipt_expiry_review_due: bool
    metadata_expiry_review_due: bool
    expiry_disposal_status: Literal['blocked_pending_backup_and_retry_window_proof']
    unresolved_copy_obligations: list['ContentCopyObligation']
    last_review: ContentReviewAttestation | None


class ContentCopyObligation(Contract):
    kind: Literal['physical_database_storage','external_provider_copies','backup_copies']
    reason_code: Literal['NO_VERIFIED_COPY_ERASURE_PROTOCOL']
    review_due_at: AwareDatetime
    all_copies_erased: Literal[False]


class ContentPatientChoice(Contract):
    patient_id: UUID
    display_id: Text


class ContentPatientsView(Contract):
    schema_version: Literal['content-disposition-patients-1']
    patients: list[ContentPatientChoice]


class ContentPolicyChoice(Contract):
    policy_id: UUID
    version: int = Field(ge=1)
    mode: Literal['retain','revoke_access','erase_selected_content']
    execution_enabled: bool
    retention_seconds: int = Field(ge=0)
    clock: Text
    access_purpose: Text
    recheck_at: AwareDatetime


class ContentHoldChoice(Contract):
    hold_id: UUID
    version: int = Field(ge=1)
    state: Literal['clear','held','unresolved']
    valid_until: AwareDatetime
    eligible: bool
    reason_code: Text | None


class ContentTargetChoice(Contract):
    target: Target
    label: Text
    record_id: UUID | None


class ContentOptionsView(Contract):
    schema_version: Literal['content-disposition-options-1']
    patient_id: UUID
    display_id: Text
    policies: list[ContentPolicyChoice]
    hold: ContentHoldChoice | None
    targets: list[ContentTargetChoice]
    default_policy: None


class ContentPlanSummary(Contract):
    plan_id: UUID
    status: Literal['preview','approved','retained','access_revoked','erasing','logical_erasure_complete']
    created_at: AwareDatetime
    mode: Literal['retain','revoke_access','erase_selected_content']
    item_count: int = Field(ge=1)
    lifecycle: ContentLifecycle


class ContentHistoryView(Contract):
    schema_version: Literal['content-disposition-history-1']
    patient_id: UUID
    plans: list[ContentPlanSummary]


ContentLifecycle.model_rebuild()


class ContentFamilyTarget(Contract):
    record_version_id: UUID
    version: Text


class ContentFamilyRequestInput(Contract):
    schema_version: Literal['content-disposition-family-request-1']
    request_id: UUID
    policy_id: UUID
    targets: list[ContentFamilyTarget] = Field(min_length=1,max_length=100)
    @model_validator(mode='after')
    def unique_targets(self):
        if len({item.record_version_id for item in self.targets})!=len(self.targets):
            raise ValueError('duplicate targets')
        return self


class ContentFamilyRequestView(Contract):
    schema_version: Literal['content-disposition-family-view-1']
    client_request_id: UUID
    request_id: UUID
    patient_id: UUID
    policy_id: UUID
    created_at: AwareDatetime
    targets: list[ContentFamilyTarget]
    status: Literal['requested','preview','approved','retained','access_revoked','erasing','logical_erasure_complete']
    linked_plan_id: UUID | None
    logical_payload_erased: bool
    all_copies_erased: Literal[False]


class ContentFamilyChoice(ContentFamilyTarget):
    record_id: UUID
    recorded_at: AwareDatetime


class ContentFamilyOptions(Contract):
    schema_version: Literal['content-disposition-family-options-1']
    patient_id: UUID
    policies: list[ContentPolicyChoice]
    targets: list[ContentFamilyChoice]
    default_policy: None


class ContentFamilyRequests(Contract):
    schema_version: Literal['content-disposition-family-requests-1']
    patient_id: UUID
    requests: list[ContentFamilyRequestView]
