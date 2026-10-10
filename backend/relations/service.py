"""关联申请、核验、关系与授权建立。

同事务建立 relation 与明确动作的 access_grant；撤销关系不删除资料。
"""
from datetime import datetime, timezone

from django.db import transaction

from authorization.models import AccessGrant
from relations.models import Relation, RelationApplication


class RelationRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


def lock_relation_for_access(relation_id, *, patient_id=None):
    relations = Relation.objects.select_for_update()
    if patient_id is not None:
        relations = relations.filter(patient_id=patient_id)
    return relations.get(id=relation_id)


@transaction.atomic
def create_application(*, applicant, evidence_ref, patient=None, note=None, requested_access=None) -> RelationApplication:
    if not isinstance(evidence_ref, str) or not evidence_ref.strip():
        raise RelationRejected("EVIDENCE_REQUIRED")
    if requested_access is not None and requested_access not in ('read', 'read_write', 'review_decides'):
        raise RelationRejected('APPLICATION_ACCESS_INVALID')
    return RelationApplication.objects.create(
        version=1,
        applicant=applicant,
        patient=patient,
        evidence_ref=evidence_ref,
        note=note,
        requested_access=requested_access,
        status=RelationApplication.Status.PENDING,
        created_at=datetime.now(timezone.utc),
    )


@transaction.atomic
def withdraw_application(*, application, expected_version) -> RelationApplication:
    application = RelationApplication.objects.select_for_update().get(id=application.id)
    if application.status != RelationApplication.Status.PENDING:
        raise RelationRejected("APPLICATION_NOT_PENDING")
    if application.version != expected_version:
        raise RelationRejected("VERSION_CONFLICT")
    application.status = RelationApplication.Status.WITHDRAWN
    application.version = application.version + 1
    if application.version > 9_223_372_036_854_775_807:
        raise RelationRejected('VERSION_RANGE_EXCEEDED')
    application.save(update_fields=["status", "version"])
    return application


@transaction.atomic
def decide_application(
    *,
    application,
    decision,
    checked_by,
    expected_version,
    action=None,
    field_codes=None,
    purpose=None,
) -> Relation:
    application = RelationApplication.objects.select_for_update().get(id=application.id)
    if decision not in (RelationApplication.Status.APPROVED, RelationApplication.Status.REJECTED):
        raise RelationRejected('APPLICATION_DECISION_INVALID')
    if application.status != RelationApplication.Status.PENDING:
        raise RelationRejected("APPLICATION_NOT_PENDING")
    if application.version != expected_version:
        raise RelationRejected("VERSION_CONFLICT")
    now = datetime.now(timezone.utc)
    application.status = decision
    application.checked_by = checked_by
    application.checked_at = now
    application.version = application.version + 1
    if application.version > 9_223_372_036_854_775_807:
        raise RelationRejected('VERSION_RANGE_EXCEEDED')
    application.save(update_fields=["status", "checked_by", "checked_at", "version"])
    if decision != RelationApplication.Status.APPROVED:
        return None
    relation = Relation.objects.create(
        patient=application.patient,
        guardian=application.applicant,
        application=application,
        status=Relation.Status.ACTIVE,
        version=1,
        checked_by=checked_by,
        checked_at=now,
        valid_from=now,
    )
    if action:
        AccessGrant.objects.create(
            patient=application.patient,
            principal=application.applicant,
            relation_id=relation.id,
            action=action,
            field_codes=field_codes or [],
            record_scope="all",
            purpose=purpose or "family_observation",
            granted_by=checked_by,
            evidence_ref=application.evidence_ref,
            valid_from=now,
        )
    return relation


@transaction.atomic
def revoke_relation(*, relation, reason, revoked_by) -> Relation:
    relation = lock_relation_for_access(relation.id)
    if relation.status != Relation.Status.ACTIVE:
        raise RelationRejected("RELATION_NOT_ACTIVE")
    if not reason:
        raise RelationRejected("REASON_REQUIRED")
    now = datetime.now(timezone.utc)
    relation.status = Relation.Status.REVOKED
    relation.revoked_at = now
    relation.reason = reason
    relation.auth_epoch = relation.auth_epoch + 1
    relation.save(update_fields=["status", "revoked_at", "reason", "auth_epoch"])
    return relation
