"""Evidence-backed due review. Expiry dates are floors, never delete permission.

This workflow retains anti-resurrection identities and retry receipts until a
separate supported backup/retry-window proof authorizes their disposal.
"""
from django.db import transaction
from django.utils import timezone
from retention.content_registry import digest
from retention.content_schemas import ContentPolicyRules, ContentReviewAttestation, ContentLifecycle
from retention.disposition import DispositionRejected
from retention.models import ContentDispositionReview


def review_evidence_valid(plan,review):
    from configurations.evidence import inspect_approved_evidence
    from authorization.policy import check_service_grant,matching_case_grants,Denied
    from django.conf import settings
    from pydantic import ValidationError
    try:
        body=ContentReviewAttestation.model_validate(review.attestation)
    except ValidationError:
        return False
    if (body.plan_id!=plan.id or body.manifest_sha256!=plan.manifest_sha256
            or body.policy_sha256!=plan.manifest['policy_sha256'] or body.review_version!=review.version
            or body.reviewed_by!=review.reviewed_by_id or body.reviewed_at!=review.reviewed_at
            or body.next_recheck_at!=review.next_recheck_at):
        return False
    now=timezone.now()
    if review.reviewed_by.account_status!='active':
        return False
    try:
        check_service_grant(review.reviewed_by,'manage_disposition',settings.SJIA_ENVIRONMENT,now)
    except Denied:
        return False
    if not any(grant.purpose==plan.policy.rules['access_purpose'] and grant.record_scope=='all'
            and not grant.field_codes and grant.evidence_ref.strip() for grant in matching_case_grants(
                review.reviewed_by,plan.patient,'read_case',now,whole_resource=True)):
        return False
    _,_,reason=inspect_approved_evidence(f'urn:sjia:source-revision:{review.source_revision_id}',
        purpose='configuration_approval',expected_checksum=digest(review.attestation))
    return reason is None


@transaction.atomic
def lifecycle(plan, *, now=None):
    now=now or timezone.now()
    rules=ContentPolicyRules.model_validate(plan.policy.rules)
    review=ContentDispositionReview.objects.filter(plan=plan).order_by('-version').first()
    valid=review_evidence_valid(plan,review) if review else None
    due=review.next_recheck_at if valid else rules.recheck_at
    return ContentLifecycle.model_validate({'schema_version':'content-disposition-lifecycle-1','plan_id':str(plan.id),
        'retention_basis_source_revision_id':str(plan.policy.source_revision_id),
        'next_recheck_at':due.isoformat(),'review_due':due<=now or valid is False,
        'review_evidence_status':'valid' if valid else 'invalid_or_revoked' if review else 'not_reviewed',
        'review_version':review.version if review else 0,
        'receipt_keep_until':rules.receipt_keep_until.isoformat(),
        'metadata_keep_until':rules.metadata_keep_until.isoformat(),
        'receipt_expiry_review_due':rules.receipt_keep_until<=now,
        'metadata_expiry_review_due':rules.metadata_keep_until<=now,
        'expiry_disposal_status':'blocked_pending_backup_and_retry_window_proof',
        'unresolved_copy_obligations':[{'kind':item,'reason_code':'NO_VERIFIED_COPY_ERASURE_PROTOCOL',
            'review_due_at':due.isoformat(),'all_copies_erased':False} for item in plan.manifest['obligations']],
        'last_review':review.attestation if valid else None}).model_dump(mode='json')


@transaction.atomic
def record_review(*,plan_id,actor,auth_epoch,data,source_revision_id):
    from retention.content_engine import read_plan,write_barrier
    from configurations.evidence import inspect_approved_evidence
    write_barrier()
    plan=read_plan(plan_id=plan_id,actor=actor,auth_epoch=auth_epoch)
    body=ContentReviewAttestation.model_validate(data)
    latest=ContentDispositionReview.objects.filter(plan=plan).order_by('-version').first()
    duplicate=latest and latest.attestation==body.model_dump(mode='json') and latest.source_revision_id==source_revision_id
    now=timezone.now()
    if (body.plan_id!=plan.id or body.manifest_sha256!=plan.manifest_sha256
            or body.policy_sha256!=plan.manifest['policy_sha256'] or body.reviewed_by!=actor.id
            or (not duplicate and body.review_version!=(latest.version+1 if latest else 1))
            or body.reviewed_at>now or (not duplicate and body.next_recheck_at<=now) or body.next_recheck_at<=body.reviewed_at):
        raise DispositionRejected('CONTENT_REVIEW_SCOPE_CHANGED')
    attestation=body.model_dump(mode='json')
    _,content,reason=inspect_approved_evidence(f'urn:sjia:source-revision:{source_revision_id}',
        purpose='configuration_approval',expected_checksum=digest(attestation),lock=True)
    if reason or ContentReviewAttestation.model_validate_json(content).model_dump(mode='json')!=attestation:
        raise DispositionRejected('CONTENT_REVIEW_EVIDENCE_UNAVAILABLE')
    if duplicate:
        if not review_evidence_valid(plan,latest):
            raise DispositionRejected('CONTENT_REVIEW_EVIDENCE_UNAVAILABLE')
        return latest
    return ContentDispositionReview.objects.create(plan=plan,version=body.review_version,
        reviewed_by=actor,reviewed_at=body.reviewed_at,next_recheck_at=body.next_recheck_at,
        source_revision_id=source_revision_id,attestation=attestation)
