import json
from datetime import datetime, timezone

from django.db import transaction

from audit.service import write_audit
from authorization.models import AccessGrant
from authorization.policy import evaluate
from configurations.service import compute_checksum
from files.deletion import delete_local_object, digest, read_deletion_receipt
from files.purpose_dependencies import invalidate_resource
from files.purpose_schemas import PurposeDispositionApprovalContext, PurposeDispositionObjectProof, PurposeDispositionScope, PurposeLocalDispositionRule
from files.purpose import current_file_grants, lock_file_access, runtime_environment
from files.models import FileObject
from files.service import object_store
from archives.models import PurposeCopy
from identity.models import Principal
from family_content.models import EnvironmentGate
from relations.models import Relation
from tasks.models import Task
from retention.models import DispositionItem, DispositionRequest, RetentionPolicy
from retention.models import SimulatedProviderCopy


class DispositionRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _now():
    return datetime.now(timezone.utc)


def scope_sha256(scope):
    parsed = PurposeDispositionScope.model_validate(scope)
    return compute_checksum({'targets': [target.model_dump(mode='json') for target in parsed.targets]})


def lock_environment(*, require_enabled=True):
    gate = EnvironmentGate.objects.select_for_update().get(environment=runtime_environment())
    if require_enabled and not gate.enabled:
        raise DispositionRejected('DISPOSITION_ENVIRONMENT_UNAVAILABLE')
    return gate


def lock_grants(identities):
    grants = list(AccessGrant.objects.select_for_update().filter(id__in=identities))
    list(Relation.objects.select_for_update().filter(id__in=[grant.relation_id for grant in grants if grant.relation_id is not None]))
    return {grant.id for grant in grants}


def approved_policy(policy):
    policy = RetentionPolicy.objects.select_for_update().get(id=policy.id)
    if (policy.status != 'active' or policy.approved_at is None or policy.approved_by_id is None
            or policy.approved_at > _now() or not policy.evidence_ref or not policy.evidence_ref.strip()
            or Principal.objects.select_for_update().get(id=policy.approved_by_id).account_status != 'active'):
        raise DispositionRejected('DISPOSITION_POLICY_UNAVAILABLE')
    return policy


def local_rule(policy):
    if not isinstance(policy.rules, dict) or 'local_delete' not in policy.rules:
        raise DispositionRejected('DISPOSITION_POLICY_RULE_REQUIRED')
    return PurposeLocalDispositionRule.model_validate(policy.rules['local_delete'])


def target_file(target, patient_id):
    if patient_id is None:
        raise DispositionRejected('DISPOSITION_PATIENT_REQUIRED')
    copy = None
    if target.target_type == 'file_object':
        file = FileObject.objects.select_for_update().get(id=target.target_id, patient_id=patient_id)
        if file.object_version != target.target_version:
            raise DispositionRejected('VERSION_CONFLICT')
        authority_file = file
    elif target.target_type == 'purpose_copy':
        copy = PurposeCopy.objects.select_for_update().get(id=target.target_id, patient_id=patient_id)
        if str(copy.version) != target.target_version:
            raise DispositionRejected('VERSION_CONFLICT')
        file = FileObject.objects.select_for_update().get(id=copy.derived_file_id, patient_id=patient_id)
        authority_file = FileObject.objects.select_for_update().get(id=copy.original_file_id, patient_id=patient_id)
        if file.derived_from_id != authority_file.id or file.file_group_id != authority_file.file_group_id:
            raise DispositionRejected('DISPOSITION_COPY_MISMATCH')
    elif target.target_type == 'provider_copy':
        if runtime_environment() != 'simulation':
            raise DispositionRejected('DISPOSITION_PROVIDER_COPY_SIMULATION_ONLY')
        provider_copy = SimulatedProviderCopy.objects.select_for_update().get(
            provider_id='SIMULATION_PROVIDER', object_id=target.target_id,
            object_version=target.target_version, patient_id=patient_id, environment='simulation')
        copy = PurposeCopy.objects.select_for_update().get(id=provider_copy.purpose_copy_id,
            patient_id=patient_id, version=provider_copy.purpose_copy_version)
        file = FileObject.objects.select_for_update().get(id=copy.derived_file_id, patient_id=patient_id)
        authority_file = FileObject.objects.select_for_update().get(id=copy.original_file_id, patient_id=patient_id)
        if (provider_copy.processing_evidence.get('simulation') is not True
                or provider_copy.processing_evidence.get('derived_file_id') != str(file.id)
                or provider_copy.processing_evidence.get('derived_checksum') != file.checksum
                or file.derived_from_id != authority_file.id or file.file_group_id != authority_file.file_group_id):
            raise DispositionRejected('DISPOSITION_PROVIDER_COPY_MISMATCH')
    else:
        raise DispositionRejected('DISPOSITION_EXECUTOR_UNREGISTERED')
    return file, authority_file, copy


def provider_copy_for_item(item, patient_id):
    if item.target_type != 'provider_copy' or runtime_environment() != 'simulation':
        raise DispositionRejected('DISPOSITION_PROVIDER_COPY_SIMULATION_ONLY')
    provider_copy = SimulatedProviderCopy.objects.select_for_update().get(
        provider_id='SIMULATION_PROVIDER', object_id=item.target_id,
        object_version=item.target_version, patient_id=patient_id, environment='simulation')
    target_file(item, patient_id)
    return provider_copy


def lock_request_access(request, actor, auth_epoch, *, require_approval):
    lock_environment()
    request = DispositionRequest.objects.select_for_update().get(id=request.id)
    policy = approved_policy(request.policy)
    rule = local_rule(policy)
    if 'provider_copy' in rule.target_types and runtime_environment() != 'simulation':
        raise DispositionRejected('DISPOSITION_PROVIDER_COPY_SIMULATION_ONLY')
    receipt_policy = approved_policy(RetentionPolicy.objects.get(id=rule.receipt_policy_id))
    parsed = PurposeDispositionScope.model_validate(request.scope)
    environment = runtime_environment()
    decision = evaluate(principal=actor, patient=request.patient, action='manage_disposition',
        purpose=rule.access_purpose, environment=environment, session_auth_epoch=auth_epoch)
    file_grants = {}
    target_files = set()
    file_versions = []
    for target in parsed.targets:
        if target.target_type not in rule.target_types:
            raise DispositionRejected('DISPOSITION_POLICY_SCOPE_DENIED')
        file, authority_file, copy = target_file(target, request.patient_id)
        if file.id in target_files:
            raise DispositionRejected('DISPOSITION_TARGET_ALIAS')
        target_files.add(file.id)
        file_versions.append(PurposeDispositionObjectProof(file_id=file.id, object_version=file.object_version,
            object_key=file.object_key, checksum=file.checksum, size_bytes=file.size_bytes, retention_policy_id=file.retention_policy_id))
        if file.retention_policy_id != policy.id or authority_file.retention_policy_id != policy.id:
            raise DispositionRejected('DISPOSITION_POLICY_SCOPE_DENIED')
        actor, patient, gate, group = lock_file_access(authority_file, actor, auth_epoch,
            purpose=rule.access_purpose, service_action='manage_disposition', require_available=False)
        grants = list(current_file_grants(authority_file, actor, rule.access_purpose, _now()))
        locked_ids = lock_grants([grant.id for grant in grants])
        current_ids = {grant.id for grant in current_file_grants(authority_file, actor, rule.access_purpose, _now())}
        if not locked_ids or not locked_ids <= current_ids:
            raise DispositionRejected('DISPOSITION_FILE_SCOPE_DENIED')
        file_grants[str(authority_file.id)] = [str(identity) for identity in sorted(locked_ids)]
    if require_approval:
        proof = parsed.approval_context
        if (request.status not in ('approved', 'running', 'completed') or request.approved_at is None
                or request.approved_at > _now() or proof is None or request.approved_by_id != proof.approver_id
                or proof.environment != environment or proof.policy_version != str(policy.version)
                or proof.policy_sha256 != compute_checksum(policy.rules) or proof.scope_sha256 != scope_sha256(request.scope)
                or sorted([item.model_dump(mode='json') for item in proof.file_versions], key=lambda item: item['file_id'])
                    != sorted([item.model_dump(mode='json') for item in file_versions], key=lambda item: item['file_id'])):
            raise DispositionRejected('DISPOSITION_APPROVAL_UNAVAILABLE')
        approver = Principal.objects.select_for_update().get(id=proof.approver_id)
        approval = evaluate(principal=approver, patient=request.patient, action='manage_disposition',
            purpose=rule.access_purpose, environment=environment, session_auth_epoch=proof.auth_epoch)
        if approval.service_grant.id != proof.service_grant_id:
            raise DispositionRejected('DISPOSITION_APPROVAL_UNAVAILABLE')
        expected_files = set(file_grants)
        if {str(item.file_id) for item in proof.file_grants} != expected_files or len(proof.file_grants) != len(expected_files):
            raise DispositionRejected('DISPOSITION_APPROVAL_SCOPE_DENIED')
        for item in proof.file_grants:
            authority_file = FileObject.objects.get(id=item.file_id, patient_id=request.patient_id)
            lock_file_access(authority_file, approver, proof.auth_epoch,
                purpose=rule.access_purpose, service_action='manage_disposition', require_available=False)
            locked_ids = lock_grants(item.grant_ids)
            current_ids = {grant.id for grant in current_file_grants(authority_file, approver, rule.access_purpose, _now())}
            if len(set(item.grant_ids)) != len(item.grant_ids) or locked_ids != set(item.grant_ids) or not locked_ids <= current_ids:
                raise DispositionRejected('DISPOSITION_APPROVAL_SCOPE_DENIED')
    return request, policy, receipt_policy, rule, decision, file_grants, file_versions


def locked_item(item):
    lock_environment()
    request = DispositionRequest.objects.select_for_update().get(id=item.request_id)
    item = DispositionItem.objects.select_for_update().get(id=item.id, request=request)
    parsed = PurposeDispositionScope.model_validate(request.scope)
    identity = (item.target_type, item.target_id, item.target_version)
    if identity not in {(target.target_type, target.target_id, target.target_version) for target in parsed.targets}:
        raise DispositionRejected('DISPOSITION_ITEM_SCOPE_DENIED')
    return item, request


def finish_request(request):
    parsed = PurposeDispositionScope.model_validate(request.scope)
    targets = {(target.target_type, target.target_id, target.target_version) for target in parsed.targets}
    items = list(DispositionItem.objects.select_for_update().filter(request=request))
    if {(item.target_type, item.target_id, item.target_version) for item in items} != targets or len(items) != len(targets):
        raise DispositionRejected('DISPOSITION_ITEMS_INCOMPLETE')
    for item in items:
        if item.status == 'retained':
            if not item.retention_evidence_ref or not item.retention_evidence_ref.strip() or item.recheck_at is None or item.recheck_at <= _now():
                raise DispositionRejected('RETAINED_REQUIRES_EVIDENCE')
        elif item.status == 'deleted':
            file, authority_file, copy = target_file(item, request.patient_id)
            if item.target_type == 'provider_copy':
                provider_copy = provider_copy_for_item(item, request.patient_id)
                request_data = provider_copy.processing_evidence.get('deletion_request')
                if request_data is None:
                    raise DispositionRejected('EXTERNAL_DELETE_RESULT_REQUIRED')
                from retention.external_deletion import ExternalDeletionRequest, query_simulated_external_deletion

                deletion_request = ExternalDeletionRequest.model_validate_json(
                    json.dumps(request_data, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
                result = query_simulated_external_deletion(deletion_request)
                receipt = result['receipt']
                if (result['state'] != 'deleted' or receipt is None or provider_copy.status != 'deleted'
                        or item.receipt_ref != str(deletion_request.request_id)
                        or item.receipt_sha256 != receipt['evidence_sha256']
                        or provider_copy.receipt_sha256 != receipt['evidence_sha256']):
                    raise DispositionRejected('EXTERNAL_DELETE_NOT_VERIFIED')
            else:
                key, data, proof = read_deletion_receipt(object_store, operation_id=item.id,
                    object_key=file.object_key, checksum=file.checksum, size_bytes=file.size_bytes)
                if file.status != 'disposed' or (copy is not None and copy.status != 'disposed') or key != item.receipt_ref or digest(data) != item.receipt_sha256:
                    raise DispositionRejected('DELETION_NOT_VERIFIED')
        else:
            return False
    request.status = DispositionRequest.Status.COMPLETED
    request.completed_at = _now()
    request.save(update_fields=['status', 'completed_at'])
    return True


@transaction.atomic
def create_disposition_request(*, patient, requested_by, policy, evidence_ref, scope) -> DispositionRequest:
    lock_environment()
    if not evidence_ref or not evidence_ref.strip():
        raise DispositionRejected("EVIDENCE_REQUIRED")
    parsed = PurposeDispositionScope.model_validate(scope)
    if 'approval_context' in parsed.model_fields_set:
        raise DispositionRejected('DISPOSITION_APPROVAL_INPUT_DENIED')
    policy = approved_policy(policy)
    for target in parsed.targets:
        if target.target_type in ('file_object', 'purpose_copy', 'provider_copy'):
            file, authority_file, copy = target_file(target, patient.id if patient is not None else None)
            if file.retention_policy_id != policy.id or authority_file.retention_policy_id != policy.id:
                raise DispositionRejected('DISPOSITION_POLICY_SCOPE_DENIED')
    request = DispositionRequest.objects.create(
        patient=patient,
        requested_by=requested_by,
        policy=policy,
        evidence_ref=evidence_ref,
        scope=scope,
        status=DispositionRequest.Status.PENDING,
        created_at=_now(),
    )
    for target in parsed.targets:
        if target.target_type in ('file_object', 'purpose_copy'):
            invalidate_resource(target.target_type, target.target_id, target.target_version)
    return request


@transaction.atomic
def approve_disposition_request(*, request, approved_by, auth_epoch, payload) -> DispositionRequest:
    request, policy, receipt_policy, rule, decision, file_grants, file_versions = lock_request_access(request, approved_by, auth_epoch, require_approval=False)
    if request.status != DispositionRequest.Status.PENDING:
        raise DispositionRejected("REQUEST_NOT_PENDING")
    if payload.policy_version != str(policy.version) or payload.expected_scope_sha256 != scope_sha256(request.scope):
        raise DispositionRejected('VERSION_CONFLICT')
    proof = PurposeDispositionApprovalContext(approver_id=approved_by.id, auth_epoch=auth_epoch,
        service_grant_id=decision.service_grant.id, environment=runtime_environment(), policy_version=str(policy.version),
        policy_sha256=compute_checksum(policy.rules), scope_sha256=scope_sha256(request.scope), evidence_ref=payload.evidence_ref,
        file_grants=[{'file_id': identity, 'grant_ids': identities} for identity, identities in sorted(file_grants.items())],
        file_versions=file_versions)
    request.scope = {**request.scope, 'approval_context': proof.model_dump(mode='json')}
    request.status = DispositionRequest.Status.APPROVED
    request.approved_by = approved_by
    request.approved_at = _now()
    request.save(update_fields=["status", "approved_by", "approved_at", 'scope'])
    for target in PurposeDispositionScope.model_validate(request.scope).targets:
        create_disposition_item(request=request, target_type=target.target_type, target_id=target.target_id, target_version=target.target_version)
    write_audit(actor=approved_by, patient=request.patient, action='disposition_approved', object_type='disposition_request',
        object_id=request.id, object_version=proof.scope_sha256, outcome='allowed', retention_policy=receipt_policy)
    return request


@transaction.atomic
def create_disposition_item(*, request, target_type, target_id, target_version) -> DispositionItem:
    lock_environment()
    request = DispositionRequest.objects.select_for_update().get(id=request.id)
    if request.status != DispositionRequest.Status.APPROVED:
        raise DispositionRejected("REQUEST_NOT_APPROVED")
    parsed = PurposeDispositionScope.model_validate(request.scope)
    if parsed.approval_context is None or (target_type, target_id, target_version) not in {
            (target.target_type, target.target_id, target.target_version) for target in parsed.targets}:
        raise DispositionRejected('DISPOSITION_ITEM_SCOPE_DENIED')
    item, created = DispositionItem.objects.get_or_create(
        request=request,
        target_type=target_type,
        target_id=target_id,
        target_version=target_version,
        defaults={'status': DispositionItem.Status.PENDING},
    )
    return item


@transaction.atomic
def mark_item_retained(*, item, retention_evidence_ref, recheck_at, executed_by, auth_epoch) -> DispositionItem:
    item, request = locked_item(item)
    request, policy, receipt_policy, rule, decision, file_grants, file_versions = lock_request_access(request, executed_by, auth_epoch, require_approval=True)
    if item.status not in ('pending', 'failed', 'blocked') or request.status == 'completed':
        raise DispositionRejected('DISPOSITION_ITEM_NOT_PENDING')
    if Task.objects.filter(task_type='disposition', input_refs__item_id=str(item.id)).exclude(
            status__in=['completed', 'failed', 'cancelled', 'superseded']).exists():
        raise DispositionRejected('DISPOSITION_TASK_ALREADY_ACTIVE')
    file, authority_file, copy = target_file(item, request.patient_id)
    if not object_store.exists(file.object_key):
        raise DispositionRejected('RETAINED_OBJECT_REQUIRED')
    retained_bytes = object_store.get(file.object_key)
    if digest(retained_bytes) != file.checksum or len(retained_bytes) != file.size_bytes:
        raise DispositionRejected('RETAINED_OBJECT_CHANGED')
    if not retention_evidence_ref or not retention_evidence_ref.strip() or recheck_at is None or recheck_at <= _now():
        raise DispositionRejected("RETAINED_REQUIRES_EVIDENCE")
    item.status = DispositionItem.Status.RETAINED
    item.retention_evidence_ref = retention_evidence_ref
    item.recheck_at = recheck_at
    item.executed_by = executed_by
    item.executed_at = _now()
    item.save(update_fields=["status", "retention_evidence_ref", "recheck_at", "executed_by", "executed_at"])
    finish_request(request)
    write_audit(actor=executed_by, patient=request.patient, action='disposition_retained', object_type='disposition_item',
        object_id=item.id, object_version=item.target_version, outcome='allowed', retention_policy=receipt_policy)
    return item


@transaction.atomic
def mark_item_deleted(*, item, receipt_ref, receipt_sha256, executed_by, auth_epoch) -> DispositionItem:
    if not receipt_ref or not receipt_sha256:
        raise DispositionRejected("DELETED_REQUIRES_RECEIPT")
    item, request = locked_item(item)
    request, policy, receipt_policy, rule, decision, file_grants, file_versions = lock_request_access(request, executed_by, auth_epoch, require_approval=True)
    file, authority_file, copy = target_file(item, request.patient_id)
    provider_copy = None
    verified_receipt_ref = receipt_ref
    verified_receipt_sha256 = receipt_sha256
    if item.target_type == 'provider_copy':
        provider_copy = provider_copy_for_item(item, request.patient_id)
        request_data = provider_copy.processing_evidence.get('deletion_request')
        if request_data is None:
            raise DispositionRejected('EXTERNAL_DELETE_RESULT_REQUIRED')
        from retention.external_deletion import ExternalDeletionRequest, query_simulated_external_deletion

        deletion_request = ExternalDeletionRequest.model_validate_json(
            json.dumps(request_data, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
        result = query_simulated_external_deletion(deletion_request)
        receipt = result['receipt']
        if (result['state'] != 'deleted' or receipt is None or receipt_ref != str(deletion_request.request_id)
                or receipt_sha256 != receipt['evidence_sha256'] or provider_copy.checksum != receipt['checksum']
                or provider_copy.size_bytes != receipt['size_bytes']):
            raise DispositionRejected('EXTERNAL_DELETE_NOT_VERIFIED')
        verified_receipt_ref = str(deletion_request.request_id)
        verified_receipt_sha256 = receipt['evidence_sha256']
        if item.status == 'deleted':
            if (item.receipt_ref != verified_receipt_ref or item.receipt_sha256 != verified_receipt_sha256
                    or provider_copy.status != 'deleted' or provider_copy.receipt_sha256 != verified_receipt_sha256):
                raise DispositionRejected('EXTERNAL_DELETE_NOT_VERIFIED')
            return item
        if provider_copy.status not in ('deletion_requested', 'deleted'):
            raise DispositionRejected('EXTERNAL_DELETE_NOT_REQUESTED')
        provider_copy.status = 'deleted'
        provider_copy.receipt_ref = receipt_ref
        provider_copy.receipt_sha256 = receipt_sha256
        provider_copy.deleted_at = _now()
        provider_copy.save(update_fields=['status', 'receipt_ref', 'receipt_sha256', 'deleted_at'])
    else:
        if item.target_type in ('external_copy',):
            raise DispositionRejected('EXTERNAL_DELETE_RESULT_REQUIRED')
        key, data, proof = read_deletion_receipt(object_store, operation_id=item.id,
            object_key=file.object_key, checksum=file.checksum, size_bytes=file.size_bytes)
        if key != receipt_ref or digest(data) != receipt_sha256 or file.status != 'disposed' or (copy is not None and copy.status != 'disposed'):
            raise DispositionRejected('DELETION_NOT_VERIFIED')
    if item.status == 'deleted':
        if item.receipt_ref != verified_receipt_ref or item.receipt_sha256 != verified_receipt_sha256:
            raise DispositionRejected('DELETION_NOT_VERIFIED')
        return item
    if item.status not in ('pending', 'failed', 'blocked', 'awaiting_provider') or request.status == 'completed':
        raise DispositionRejected('DISPOSITION_ITEM_NOT_PENDING')
    item.status = DispositionItem.Status.DELETED
    item.reason = None
    item.receipt_ref = receipt_ref
    item.receipt_sha256 = receipt_sha256
    item.executed_by = executed_by
    item.executed_at = _now()
    item.save(update_fields=["status", "reason", "receipt_ref", "receipt_sha256", "executed_by", "executed_at"])
    finish_request(request)
    write_audit(actor=executed_by, patient=request.patient, action='disposition_deleted', object_type='disposition_item',
        object_id=item.id, object_version=item.target_version, outcome='allowed', retention_policy=receipt_policy)
    return item


@transaction.atomic
def execute_local_item(*, item, expected_target_version, policy_version, executed_by, auth_epoch):
    item, request = locked_item(item)
    if item.target_type == 'provider_copy':
        raise DispositionRejected('DISPOSITION_PROVIDER_EXECUTOR_REQUIRED')
    request, policy, receipt_policy, rule, decision, file_grants, file_versions = lock_request_access(request, executed_by, auth_epoch, require_approval=True)
    if item.target_version != expected_target_version or str(policy.version) != policy_version:
        raise DispositionRejected('VERSION_CONFLICT')
    if item.status not in ('pending', 'failed', 'blocked', 'deleted') or (request.status == 'completed' and item.status != 'deleted'):
        raise DispositionRejected('DISPOSITION_ITEM_NOT_PENDING')
    file, authority_file, copy = target_file(item, request.patient_id)
    # 文件准备证明先持久保存；实际字节删除及确认回执各自核验。
    key, data, proof = delete_local_object(object_store, operation_id=item.id,
        object_key=file.object_key, checksum=file.checksum, size_bytes=file.size_bytes)
    file.status = 'disposed'
    file.save(update_fields=['status'])
    invalidate_resource('file_object', file.id, file.object_version)
    if copy is not None:
        copy.status = 'disposed'
        copy.disposition_ref = request.id
        copy.save(update_fields=['status', 'disposition_ref'])
        invalidate_resource('purpose_copy', copy.id, str(copy.version))
    return mark_item_deleted(item=item, receipt_ref=key, receipt_sha256=digest(data), executed_by=executed_by, auth_epoch=auth_epoch)


@transaction.atomic
def mark_item_awaiting_provider(*, item) -> DispositionItem:
    item, request = locked_item(item)
    if request.status not in ('approved', 'running') or item.target_type != 'provider_copy':
        raise DispositionRejected('DISPOSITION_PROVIDER_SCOPE_DENIED')
    if item.status not in ('pending', 'failed', 'blocked'):
        raise DispositionRejected('DISPOSITION_ITEM_NOT_PENDING')
    item.status = DispositionItem.Status.AWAITING_PROVIDER
    item.save(update_fields=["status"])
    return item
