import uuid
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from archives.models import PurposeCopy, PurposeCopyReview
from audit.service import write_audit
from authorization.models import AccessGrant, ServiceRoleGrant
from authorization.policy import check_principal_session, evaluate, matching_case_grants
from configurations.models import ConfigApproval, ConfigValidation, ConfigVersion
from configurations.service import compute_checksum
from files.models import FileGroup, FileObject, FilePage
from files.purpose_plan import processing_plan, plan_processing_version, plan_artifact_checksum
from files.purpose_dependencies import current_copy_dependencies, invalidate_resource, register_review
from files.purpose_schemas import PurposeProcessingPlan
from files.service import FileRejected, object_store
from files.storage import digest_bytes
from family_content.models import EnvironmentGate
from family_content.gate_locks import read_environment_gate_shared
from identity.models import Patient, Principal
from records.models import RecordAttachment, RecordVersion
from relations.models import Relation
from retention.models import DispositionItem, DispositionRequest, RetentionPolicy
from retention.policy_locks import read_retention_policy_shared
from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision
from tasks.models import TaskAttempt


def runtime_environment():
    if not settings.SJIA_ENVIRONMENT:
        raise FileRejected('ENVIRONMENT_CONFIGURATION_REQUIRED')
    return settings.SJIA_ENVIRONMENT


def lock_file_access(file, actor, auth_epoch, *, purpose, service_action=None, require_available=True):
    environment = runtime_environment()
    gate = read_environment_gate_shared(environment)
    patient = Patient.objects.select_for_update().get(id=file.patient_id)
    actor = Principal.objects.select_for_update().get(id=actor.id)
    now = timezone.now()
    if not gate.enabled or patient.status != Patient.Status.ACTIVE:
        raise FileRejected('PURPOSE_ENVIRONMENT_UNAVAILABLE')
    check_principal_session(actor, auth_epoch)
    if service_action is not None:
        decision = evaluate(principal=actor, patient=patient, action=service_action, purpose=purpose,
            environment=environment, session_auth_epoch=auth_epoch)
        ServiceRoleGrant.objects.select_for_update().get(id=decision.service_grant.id)
        evaluate(principal=actor, patient=patient, action=service_action, purpose=purpose,
            environment=environment, session_auth_epoch=auth_epoch)
    group = FileGroup.objects.select_for_update().get(id=file.file_group_id, patient=patient)
    attachments = RecordAttachment.objects.filter(file_id=file.id, patient_id=patient.id)
    versions = list(RecordVersion.all_objects.only('id','record_id','patient_id','author_id').filter(id__in=attachments.values('record_version_id'), patient=patient))
    scopes = [(version.record_id, version.author_id) for version in versions]
    scopes.append((None, group.owner_id))
    for record_id, author_id in scopes:
        for grant in matching_case_grants(actor, patient, 'read_file', now, record_id=record_id, author_id=author_id):
            if grant.purpose != purpose or not grant.evidence_ref.strip() or grant.field_codes:
                continue
            grant = AccessGrant.objects.select_for_update().get(id=grant.id)
            if grant.relation_id is not None:
                Relation.objects.select_for_update().get(id=grant.relation_id, patient=patient)
            current = list(matching_case_grants(actor, patient, 'read_file', timezone.now(), record_id=record_id, author_id=author_id))
            if any(item.id == grant.id and item.purpose == purpose and item.evidence_ref.strip() and not item.field_codes for item in current):
                if require_available:
                    from retention.content_fences import assert_content_available
                    assert_content_available('files.fileobject', file.id)
                    if group.status != FileGroup.Status.AVAILABLE:
                        raise FileRejected('FILE_GROUP_NOT_AVAILABLE')
                return actor, patient, gate, group
    raise FileRejected('PURPOSE_FILE_SCOPE_DENIED')


def current_file_grants(file, actor, purpose, now):
    group = FileGroup.objects.get(id=file.file_group_id, patient_id=file.patient_id)
    attachments = RecordAttachment.objects.filter(file_id=file.id, patient_id=file.patient_id)
    versions = RecordVersion.objects.filter(id__in=attachments.values('record_version_id'), patient_id=file.patient_id)
    scopes = [(version.record_id, version.author_id) for version in versions]
    scopes.append((None, group.owner_id))
    ids = set()
    for record_id, author_id in scopes:
        for grant in matching_case_grants(actor, file.patient, 'read_file', now, record_id=record_id, author_id=author_id):
            if grant.purpose == purpose and not grant.field_codes and grant.evidence_ref.strip() and grant.id not in ids:
                ids.add(grant.id)
                yield grant


def pending_disposition(file_id, copy_id=None):
    targets = [str(file_id)]
    if copy_id is not None:
        targets.append(str(copy_id))
    items = DispositionItem.objects.filter(target_id__in=targets,
        request__status__in=['pending', 'approved', 'running', 'blocked'],
        status__in=['pending', 'blocked', 'awaiting_provider', 'failed']).exists()
    if items:
        return True
    file = FileObject.objects.get(id=file_id)
    requests = DispositionRequest.objects.select_for_update().filter(patient_id=file.patient_id,
        status__in=['pending', 'approved', 'running', 'blocked'])
    for request in requests:
        if not isinstance(request.scope, dict):
            return True
        requested_targets = request.scope.get('targets')
        if not isinstance(requested_targets, list) or any(not isinstance(item, dict) for item in requested_targets):
            return True
        if any(item.get('target_id') in targets for item in requested_targets):
            return True
    return False


def lock_original(file_id):
    file = FileObject.objects.select_for_update().get(id=file_id)
    if file.purpose != FileObject.Purpose.ORIGINAL or file.status != FileObject.Status.AVAILABLE:
        raise FileRejected('ORIGINAL_UNAVAILABLE')
    policy = read_retention_policy_shared(file.retention_policy_id)
    if policy.status != 'active' or not policy.evidence_ref or policy.approved_at is None or policy.approved_by_id is None:
        raise FileRejected('PURPOSE_RETENTION_UNAVAILABLE')
    if pending_disposition(file.id):
        raise FileRejected('PURPOSE_DISPOSITION_PENDING')
    return file


def lock_use_authorization(authorization_id, original, purpose, environment):
    auth = DataUseAuthorization.objects.select_for_update().get(id=authorization_id, patient_id=original.patient_id)
    now = timezone.now()
    revision = SourceRevision.objects.select_for_update().get(id=auth.source_revision_id)
    source = SourceRegistry.objects.select_for_update().get(id=revision.source_id)
    approver = Principal.objects.select_for_update().get(id=auth.approved_by_id)
    group = FileGroup.objects.get(id=original.file_group_id, patient_id=original.patient_id)
    if (auth.purpose != purpose or auth.environment != environment or not auth.evidence_ref.strip()
            or auth.approved_at > now or auth.revoked_at is not None
            or (auth.valid_until is not None and auth.valid_until <= now)
            or revision.status != 'approved' or source.status != 'approved'
            or not revision.rights_evidence_ref or approver.account_status != 'active'
            or group.source_revision_id != revision.id):
        raise FileRejected('PURPOSE_AUTHORIZATION_UNAVAILABLE')
    if not isinstance(revision.allowed_use, list) or purpose not in revision.allowed_use:
        raise FileRejected('PURPOSE_SOURCE_USE_DENIED')
    return auth


def lock_processing_config(config_id, original, purpose, target, environment):
    config = ConfigVersion.objects.select_for_update().get(id=config_id)
    if (config.status != 'enabled' or config.kind != 'provider'
            or config.schema_version not in ('purpose-processing-2', 'purpose-processing-text-1')
            or compute_checksum(config.payload) != config.checksum):
        raise FileRejected('PURPOSE_CONFIGURATION_UNAVAILABLE')
    plan = processing_plan(config)
    if (plan.original_file_id != original.id or plan.patient_id != original.patient_id
            or plan.original_checksum != original.checksum or plan.original_version != original.object_version
            or plan.purpose != purpose or plan.target != target or plan.environment != environment):
        raise FileRejected('PURPOSE_PLAN_MISMATCH')
    now = timezone.now()
    scope = {'patient_id': str(original.patient_id), 'original_file_id': str(original.id),
        'purpose': purpose, 'target': target, 'environment': environment, 'config_checksum': config.checksum}
    approvals = list(ConfigApproval.objects.select_for_update().filter(config=config, approval_role='data_processing',
        revoked_at__isnull=True, approved_at__lte=now).exclude(valid_until__lte=now))
    approvals = [item for item in approvals if item.effective_scope == scope and item.evidence_ref.strip()
        and Principal.objects.select_for_update().get(id=item.approved_by_id).account_status == 'active']
    validations = list(ConfigValidation.objects.select_for_update().filter(config=config, environment=environment,
        config_checksum=config.checksum, outcome='pass', revoked_at__isnull=True, executed_at__lte=now,
        dataset_revision_id=FileGroup.objects.get(id=original.file_group_id).source_revision_id))
    artifact_checksum = plan_artifact_checksum(plan)
    validations = [item for item in validations if item.evidence_ref.strip() and item.artifact_sha256 == artifact_checksum
        and Principal.objects.select_for_update().get(id=item.executed_by_id).account_status == 'active']
    if not approvals or not validations:
        raise FileRejected('PURPOSE_PLAN_PROOF_REQUIRED')
    processing_page(plan, original)
    return config, plan, approvals, validations


def processing_page(plan, original):
    pages = list(FilePage.objects.select_for_update().filter(file_id=original.id,
        patient_id=original.patient_id, file_group_id=original.file_group_id).order_by('ordinal'))
    if plan.file_page_id is None:
        if not original.media_type.startswith('image/') or len(pages) != 1:
            raise FileRejected('PURPOSE_PAGE_SELECTION_REQUIRED')
        return pages[0], 1
    selected = next(((page, index) for index, page in enumerate(pages, start=1) if page.id == plan.file_page_id), None)
    if selected is None:
        raise FileRejected('PURPOSE_PAGE_SOURCE_MISMATCH')
    if plan.schema_version == 'purpose-processing-text-1' and (
            original.media_type != 'text/plain' or selected[1] != 1):
        raise FileRejected('PURPOSE_TEXT_PAGE_SOURCE_MISMATCH')
    return selected


def lock_plan_scope(config, actor, auth_epoch):
    if config.schema_version not in ('purpose-processing-2', 'purpose-processing-text-1') or config.kind != 'provider':
        raise FileRejected('PURPOSE_CONFIGURATION_UNAVAILABLE')
    if compute_checksum(config.payload) != config.checksum:
        raise FileRejected('PURPOSE_CHECKSUM_MISMATCH')
    plan = processing_plan(config)
    original = FileObject.objects.get(id=plan.original_file_id, patient_id=plan.patient_id)
    actor, patient, gate, group = lock_file_access(original, actor, auth_epoch,
        purpose=plan.purpose, service_action='manage_data_use')
    original = lock_original(original.id)
    if (plan.environment != gate.environment or plan.original_checksum != original.checksum
            or plan.original_version != original.object_version):
        raise FileRejected('PURPOSE_PLAN_MISMATCH')
    return plan, original, gate, group


def checked_bytes(file):
    from retention.content_fences import assert_content_available
    assert_content_available('files.fileobject', file.id)
    if file.status != FileObject.Status.AVAILABLE:
        raise FileRejected('FILE_NOT_AVAILABLE')
    data = object_store.get(file.object_key)
    if digest_bytes(data) != file.checksum or len(data) != file.size_bytes:
        raise FileRejected('PURPOSE_CHECKSUM_MISMATCH')
    return data


def lock_prepared_image(plan, original):
    if plan.prepared_file_id is None:
        return None
    prepared = FileObject.objects.select_for_update().get(id=plan.prepared_file_id, patient_id=original.patient_id)
    if (prepared.purpose != 'deidentified' or prepared.derived_from_id != original.id
            or prepared.file_group_id != original.file_group_id or prepared.checksum != plan.prepared_checksum
            or pending_disposition(prepared.id)):
        raise FileRejected('PURPOSE_PREPARED_FILE_MISMATCH')
    checked_bytes(prepared)
    return prepared


def lock_copy_context(copy_id, actor, auth_epoch, *, service=True, allow_unavailable=False, service_action='manage_data_use'):
    if service and service_action not in ('manage_data_use', 'read_assessment', 'execute_assessment'):
        raise FileRejected('PURPOSE_SERVICE_ACTION_INVALID')
    copy = PurposeCopy.objects.get(id=copy_id)
    original = FileObject.objects.get(id=copy.original_file_id, patient_id=copy.patient_id)
    actor, patient, gate, group = lock_file_access(original, actor, auth_epoch,
        purpose=copy.purpose if service else 'family_observation', service_action=service_action if service else None)
    copy = PurposeCopy.objects.select_for_update().get(id=copy.id, patient_id=patient.id)
    original = FileObject.objects.select_for_update().get(id=original.id, patient=patient)
    derived = FileObject.objects.select_for_update().get(id=copy.derived_file_id, patient=patient)
    if allow_unavailable:
        return copy, original, derived, actor, patient, gate, group
    lock_original(original.id)
    if pending_disposition(derived.id, copy.id):
        raise FileRejected('PURPOSE_DISPOSITION_PENDING')
    if derived.status != 'available' or derived.derived_from_id != original.id or derived.purpose != 'deidentified':
        raise FileRejected('PURPOSE_DERIVED_UNAVAILABLE')
    evidence = copy.processing_evidence
    if (copy.environment != gate.environment or evidence['original_version'] != original.object_version
            or evidence['original_checksum'] != original.checksum or evidence['original_group_version'] != str(group.version)
            or evidence['derived_checksum'] != derived.checksum):
        raise FileRejected('PURPOSE_VERSION_UNAVAILABLE')
    lock_use_authorization(copy.use_authorization_id, original, copy.purpose, gate.environment)
    config, plan, approvals, validations = lock_processing_config(evidence['config_id'], original,
        copy.purpose, evidence['target'], gate.environment)
    if (copy.processing_version != plan_processing_version(plan)
            or derived.processing_version != plan_processing_version(plan)
            or evidence['processing_version'] != plan_processing_version(plan)):
        raise FileRejected('PURPOSE_VERSION_UNAVAILABLE')
    if evidence['config_checksum'] != config.checksum or evidence['config_version'] != str(config.version):
        raise FileRejected('PURPOSE_CONFIGURATION_CHANGED')
    if copy.date_strategy != plan.date_strategy or evidence['required_scopes'] != plan.required_scopes:
        raise FileRejected('PURPOSE_PLAN_MISMATCH')
    page, number = processing_page(plan, original)
    if (evidence.get('file_page_id') != (str(plan.file_page_id) if plan.file_page_id is not None else None)
            or evidence.get('source_page_number', 1) != number):
        raise FileRejected('PURPOSE_PAGE_SOURCE_MISMATCH')
    lock_prepared_image(plan, original)
    if not current_copy_dependencies(copy):
        raise FileRejected('PURPOSE_DEPENDENCY_UNAVAILABLE')
    return copy, original, derived, actor, patient, gate, group


def current_review(copy, original, derived):
    if copy.status != 'approved' or copy.clinical_integrity_status != 'verified':
        return None
    review = PurposeCopyReview.objects.select_for_update().filter(copy_id=copy.id, patient_id=copy.patient_id,
        copy_version=copy.version, identity_result='passed', clinical_result='passed', revoked_at__isnull=True,
        original_checksum=original.checksum, derived_checksum=derived.checksum).order_by('-reviewed_at', '-id').first()
    if review is None or review.reviewer_id == copy.created_by or not review.evidence_ref.strip() or review.reviewed_at > timezone.now():
        return None
    if not current_copy_dependencies(copy, review):
        return None
    reviewer = Principal.objects.select_for_update().get(id=review.reviewer_id)
    authority = copy.processing_evidence.get('review_authority')
    if (reviewer.account_status != 'active' or authority is None or authority['review_id'] != str(review.id)
            or authority['auth_epoch'] != str(reviewer.auth_epoch)):
        return None
    now = timezone.now()
    for action in ('manage_data_use', 'verify_field'):
        grants = ServiceRoleGrant.objects.select_for_update().filter(principal_id=reviewer.id,
            environment=copy.environment, action=action).order_by('-valid_from', '-id')
        grant = grants.first()
        if (grant is None or str(grant.id) != authority['service_grants'][action] or grant.revoked_at is not None
                or grant.valid_from > review.reviewed_at or not grant.evidence_ref.strip()
                or (grant.valid_until is not None and grant.valid_until <= now)):
            return None
    original_scope = [grant for grant in current_file_grants(original, reviewer, copy.purpose, now)
        if str(grant.id) in authority['case_grants'] and grant.valid_from <= review.reviewed_at]
    if not original_scope:
        return None
    list(AccessGrant.objects.select_for_update().filter(id__in=[grant.id for grant in original_scope]))
    list(Relation.objects.select_for_update().filter(id__in=[grant.relation_id for grant in original_scope if grant.relation_id is not None]))
    if not any(str(grant.id) in authority['case_grants'] and grant.valid_from <= review.reviewed_at
            for grant in current_file_grants(original, reviewer, copy.purpose, timezone.now())):
        return None
    results = {item['scope']: item['result'] for item in review.checked_scopes}
    required = copy.processing_evidence['required_scopes']
    if set(results) != {'image_text', 'confirmed_text', 'model_input', 'log', 'presentation'}:
        return None
    if any(results[scope] != 'passed' for scope in required) or 'failed' in results.values():
        return None
    return review


def copy_restrictions(copy, original, derived):
    reasons = []
    now = timezone.now()
    evidence = copy.processing_evidence
    if not current_copy_dependencies(copy):
        reasons.append('PURPOSE_DEPENDENCY_UNAVAILABLE')
    group = FileGroup.objects.select_for_update().get(id=original.file_group_id, patient_id=copy.patient_id)
    policy = read_retention_policy_shared(original.retention_policy_id)
    if (original.status != 'available' or derived.status != 'available' or group.status != 'available'
            or evidence['original_version'] != original.object_version or evidence['original_checksum'] != original.checksum
            or evidence['derived_checksum'] != derived.checksum or evidence['original_group_version'] != str(group.version)
            or derived.derived_from_id != original.id or derived.purpose != 'deidentified'):
        reasons.append('PURPOSE_VERSION_UNAVAILABLE')
    if policy.status != 'active' or policy.approved_by_id is None or policy.approved_at is None or not policy.evidence_ref:
        reasons.append('PURPOSE_RETENTION_UNAVAILABLE')
    if pending_disposition(original.id) or pending_disposition(derived.id, copy.id):
        reasons.append('PURPOSE_DISPOSITION_PENDING')
    auth = DataUseAuthorization.objects.select_for_update().get(id=copy.use_authorization_id, patient_id=copy.patient_id)
    revision = SourceRevision.objects.select_for_update().get(id=auth.source_revision_id)
    source = SourceRegistry.objects.select_for_update().get(id=revision.source_id)
    approver = Principal.objects.select_for_update().get(id=auth.approved_by_id)
    if (auth.purpose != copy.purpose or auth.environment != copy.environment or copy.environment != runtime_environment()
            or not auth.evidence_ref.strip() or auth.revoked_at is not None or auth.approved_at > now
            or (auth.valid_until is not None and auth.valid_until <= now) or approver.account_status != 'active'
            or source.status != 'approved' or revision.status != 'approved' or not revision.rights_evidence_ref
            or group.source_revision_id != revision.id or not isinstance(revision.allowed_use, list)
            or copy.purpose not in revision.allowed_use):
        reasons.append('PURPOSE_AUTHORIZATION_UNAVAILABLE')
    config = ConfigVersion.objects.select_for_update().get(id=evidence['config_id'])
    if (config.status != 'enabled' or config.schema_version not in ('purpose-processing-2', 'purpose-processing-text-1')
            or config.kind != 'provider'
            or config.checksum != compute_checksum(config.payload) or evidence['config_checksum'] != config.checksum
            or evidence['config_version'] != str(config.version)):
        reasons.append('PURPOSE_CONFIGURATION_UNAVAILABLE')
    else:
        plan = processing_plan(config)
        if (copy.processing_version != plan_processing_version(plan)
                or derived.processing_version != plan_processing_version(plan)
                or evidence['processing_version'] != plan_processing_version(plan)):
            reasons.append('PURPOSE_VERSION_UNAVAILABLE')
        if (plan.original_file_id != original.id or plan.patient_id != copy.patient_id
                or plan.original_checksum != original.checksum or plan.original_version != original.object_version
                or plan.environment != copy.environment or plan.purpose != copy.purpose or plan.target != evidence['target']
                or plan.required_scopes != evidence['required_scopes'] or plan.date_strategy != copy.date_strategy):
            reasons.append('PURPOSE_PLAN_MISMATCH')
        if plan.prepared_file_id is not None:
            prepared = FileObject.objects.select_for_update().get(id=plan.prepared_file_id, patient_id=copy.patient_id)
            if (prepared.status != 'available' or prepared.purpose != 'deidentified' or prepared.derived_from_id != original.id
                    or prepared.file_group_id != original.file_group_id or prepared.checksum != plan.prepared_checksum
                    or pending_disposition(prepared.id)):
                reasons.append('PURPOSE_PREPARED_FILE_MISMATCH')
        scope = {'patient_id': str(copy.patient_id), 'original_file_id': str(original.id), 'purpose': copy.purpose,
            'target': evidence['target'], 'environment': copy.environment, 'config_checksum': config.checksum}
        approvals = list(ConfigApproval.objects.select_for_update().filter(config=config, approval_role='data_processing',
            approved_at__lte=now, revoked_at__isnull=True).exclude(valid_until__lte=now))
        qualified_approvals = [item for item in approvals if item.effective_scope == scope and item.evidence_ref.strip()
            and Principal.objects.select_for_update().get(id=item.approved_by_id).account_status == 'active']
        validations = list(ConfigValidation.objects.select_for_update().filter(config=config, config_checksum=config.checksum,
            environment=copy.environment, dataset_revision_id=revision.id, executed_at__lte=now,
            outcome='pass', revoked_at__isnull=True))
        artifact = plan_artifact_checksum(plan)
        qualified_validations = [item for item in validations if item.evidence_ref.strip() and item.artifact_sha256 == artifact
            and Principal.objects.select_for_update().get(id=item.executed_by_id).account_status == 'active']
        if not qualified_approvals or not qualified_validations:
            reasons.append('PURPOSE_PLAN_PROOF_REQUIRED')
    return reasons


def audit_copy(copy, original, actor, action, request_id=None):
    return write_audit(actor=actor, patient=original.patient, action=action, object_type='purpose_copy',
        object_id=copy.id, object_version=str(copy.version), request_id=request_id, outcome='allowed',
        retention_policy=original.retention_policy)


@transaction.atomic
def review_copy(*, copy_id, actor, auth_epoch, payload, request_id):
    copy, original, derived, actor, patient, gate, group = lock_copy_context(copy_id, actor, auth_epoch)
    if copy.created_by == actor.id:
        raise FileRejected('PURPOSE_INDEPENDENT_REVIEW_REQUIRED')
    decision = evaluate(principal=actor, patient=patient, action='verify_field', purpose=copy.purpose,
        environment=gate.environment, session_auth_epoch=auth_epoch)
    verification_grant = ServiceRoleGrant.objects.select_for_update().get(id=decision.service_grant.id)
    evaluate(principal=actor, patient=patient, action='verify_field', purpose=copy.purpose,
        environment=gate.environment, session_auth_epoch=auth_epoch)
    management = evaluate(principal=actor, patient=patient, action='manage_data_use', purpose=copy.purpose,
        environment=gate.environment, session_auth_epoch=auth_epoch)
    management_grant = ServiceRoleGrant.objects.select_for_update().get(id=management.service_grant.id)
    if not verification_grant.evidence_ref.strip() or not management_grant.evidence_ref.strip():
        raise FileRejected('PURPOSE_REVIEW_AUTHORITY_REQUIRED')
    case_grants = list(current_file_grants(original, actor, copy.purpose, timezone.now()))
    list(AccessGrant.objects.select_for_update().filter(id__in=[grant.id for grant in case_grants]))
    case_grants = list(current_file_grants(original, actor, copy.purpose, timezone.now()))
    if not case_grants:
        raise FileRejected('PURPOSE_FILE_SCOPE_DENIED')
    if copy.status != 'pending_review' or str(copy.version) != payload.expected_version:
        raise FileRejected('VERSION_CONFLICT')
    if payload.checksum != derived.checksum:
        raise FileRejected('PURPOSE_CHECKSUM_MISMATCH')
    checked_bytes(original)
    checked_bytes(derived)
    results = {item.scope: item.result for item in payload.scope_results}
    if set(results) != {'image_text', 'confirmed_text', 'model_input', 'log', 'presentation'}:
        raise FileRejected('PURPOSE_REVIEW_SCOPE_INCOMPLETE')
    required = copy.processing_evidence['required_scopes']
    identity_passed = all(results[scope] == 'passed' for scope in required) and 'failed' not in results.values()
    date_passed = (payload.date_shift_consistency == 'passed' if copy.date_strategy == 'consistent_shift'
        else payload.date_shift_consistency in ('passed', 'not_applicable'))
    clinical_passed = payload.clinical_integrity == 'passed' and date_passed
    clinical_failed = payload.clinical_integrity == 'failed' or payload.date_shift_consistency == 'failed'
    clinical_result = 'passed' if clinical_passed else 'failed' if clinical_failed else 'unknown'
    review = PurposeCopyReview.objects.create(id=uuid.uuid4(), patient_id=patient.id, copy_id=copy.id,
        copy_version=copy.version, reviewer_id=actor.id, reviewed_at=timezone.now(), evidence_ref=payload.evidence_ref,
        checked_scopes=[item.model_dump() for item in payload.scope_results],
        identity_result='passed' if identity_passed else 'failed', clinical_result=clinical_result,
        original_checksum=original.checksum, derived_checksum=derived.checksum)
    copy.clinical_integrity_status = 'verified' if clinical_passed else 'failed' if clinical_failed else 'pending'
    copy.status = ('approved' if identity_passed and clinical_passed else
        'rejected' if not identity_passed or clinical_failed else 'pending_review')
    copy.processing_evidence = {**copy.processing_evidence, 'date_review': payload.date_shift_consistency,
        'latest_review_id': str(review.id), 'reviewed_at': review.reviewed_at.isoformat(),
        'review_authority': {'review_id': str(review.id), 'auth_epoch': str(actor.auth_epoch),
            'service_grants': {'manage_data_use': str(management_grant.id), 'verify_field': str(verification_grant.id)},
            'case_grants': [str(grant.id) for grant in case_grants]}}
    copy.save(update_fields=['clinical_integrity_status', 'status', 'processing_evidence'])
    register_review(copy, review, [management_grant, verification_grant], case_grants)
    audit_copy(copy, original, actor, 'purpose_copy_review', request_id)
    return copy


@transaction.atomic
def revoke_copy(*, copy_id, actor, auth_epoch, payload, request_id):
    copy, original, derived, actor, patient, gate, group = lock_copy_context(copy_id, actor, auth_epoch, allow_unavailable=True)
    if str(copy.version) != payload.expected_version or copy.status == 'disposed':
        raise FileRejected('VERSION_CONFLICT')
    PurposeCopyReview.objects.filter(copy_id=copy.id, patient_id=patient.id, revoked_at__isnull=True).update(revoked_at=timezone.now())
    copy.status = 'revoked'
    copy.processing_evidence = {**copy.processing_evidence, 'revocation_reason': payload.reason,
        'revoked_at': timezone.now().isoformat(), 'revoked_by': str(actor.id)}
    copy.save(update_fields=['status', 'processing_evidence'])
    invalidated = invalidate_resource('purpose_copy', copy.id, str(copy.version))
    copy.processing_evidence = {**copy.processing_evidence, 'invalidated_node_ids': invalidated}
    copy.save(update_fields=['processing_evidence'])
    audit_copy(copy, original, actor, 'purpose_copy_revoke', request_id)
    write_audit(actor=actor, patient=patient, action='purpose_dependency_revoke', object_type='purpose_copy',
        object_id=copy.id, object_version=str(copy.version), request_id=request_id, outcome='allowed',
        retention_policy=original.retention_policy, reason_code='PURPOSE_REVOKED')
    return copy


def copy_view(copy, original, derived, review, *, restrictions, actor):
    actions = ['revoke'] if copy.status != 'disposed' else []
    if not restrictions:
        if review is not None:
            actions.insert(0, 'content')
        elif copy.status == 'pending_review' and copy.created_by != actor.id:
            now = timezone.now()
            grant = ServiceRoleGrant.objects.select_for_update().filter(principal=actor, action='verify_field',
                environment=copy.environment).order_by('-valid_from', '-id').first()
            if (grant is not None and grant.revoked_at is None and grant.valid_from <= now
                    and (grant.valid_until is None or grant.valid_until > now) and grant.evidence_ref.strip()):
                actions = ['review_content', 'review', *actions]
    return {'copy_id': str(copy.id), 'patient_id': str(copy.patient_id), 'original_file_id': str(original.id),
        'original_version': copy.processing_evidence['original_version'], 'version': str(copy.version),
        'copy_file_id': str(derived.id), 'checksum': derived.checksum, 'purpose': copy.purpose,
        'target': copy.processing_evidence['target'], 'status': copy.status,
        'review_id': str(review.id) if review is not None else None,
        'allowed_actions': actions}


@transaction.atomic
def copy_content(*, copy_id, actor, auth_epoch, purpose, target, service=True, review_only=False):
    copy, original, derived, actor, patient, gate, group = lock_copy_context(copy_id, actor, auth_epoch, service=service)
    if copy.purpose != purpose or copy.processing_evidence['target'] != target:
        raise FileRejected('PURPOSE_TARGET_MISMATCH')
    if not review_only and current_review(copy, original, derived) is None:
        raise FileRejected('PURPOSE_REVIEW_REQUIRED')
    if review_only and (not service or copy.status not in ('pending_review', 'approved', 'rejected')):
        raise FileRejected('PURPOSE_REVIEW_CONTENT_UNAVAILABLE')
    if review_only:
        if copy.created_by == actor.id:
            raise FileRejected('PURPOSE_INDEPENDENT_REVIEW_REQUIRED')
        evaluate(principal=actor, patient=patient, action='verify_field', purpose=copy.purpose,
            environment=gate.environment, session_auth_epoch=auth_epoch)
    data = checked_bytes(derived)
    checked_bytes(original)
    audit_copy(copy, original, actor, 'purpose_copy_review_content' if review_only else 'purpose_copy_content')
    return data, derived


def purpose_result(task):
    copy = PurposeCopy.objects.filter(patient_id=task.patient_id, processing_evidence__task_id=str(task.id)).first()
    attempt = TaskAttempt.objects.filter(task=task).order_by('-attempt_number').first()
    return {'result_type': 'purpose_copy' if copy is not None else None,
        'result_ref': str(copy.id) if copy is not None else None,
        'error_code': attempt.error_code if attempt is not None else None}
