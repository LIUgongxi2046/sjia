"""服务端草稿：创建、串行更新、放弃与提交。

草稿独立于正式记录；submitted/abandoned/expired 不恢复；
版本冲突返回 VERSION_CONFLICT，旧响应不覆盖新输入由客户端 DraftCoordinator 保证。
"""
from datetime import datetime, timezone

from django.db import transaction
from django.db.models import Q

from drafts.models import Draft


class DraftRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


@transaction.atomic
def create_draft(
    *,
    patient,
    owner,
    form_type,
    schema_version,
    payload,
    retention_policy,
    correction_record_id=None,
    correction_version=None,
    expires_at=None,
) -> Draft:
    return Draft.objects.create(
        patient=patient,
        owner=owner,
        form_type=form_type,
        version=1,
        schema_version=schema_version,
        payload=payload,
        correction_record_id=correction_record_id,
        correction_version=correction_version,
        status=Draft.Status.EDITING,
        saved_at=datetime.now(timezone.utc),
        expires_at=expires_at,
        retention_policy=retention_policy,
    )


@transaction.atomic
def update_draft(*, draft, expected_version, payload) -> Draft:
    draft = Draft.objects.select_for_update().get(id=draft.id, owner=draft.owner, patient=draft.patient)
    if draft.expires_at and draft.expires_at <= datetime.now(timezone.utc):
        raise DraftRejected('DRAFT_EXPIRED')
    if draft.status != Draft.Status.EDITING:
        raise DraftRejected("DRAFT_NOT_EDITING")
    if draft.version != expected_version:
        raise DraftRejected("VERSION_CONFLICT")
    draft.payload = payload
    draft.version = draft.version + 1
    draft.saved_at = datetime.now(timezone.utc)
    draft.save(update_fields=["payload", "version", "saved_at"])
    return draft


@transaction.atomic
def abandon_draft(*, draft, expected_version) -> Draft:
    draft = Draft.objects.select_for_update().get(id=draft.id, owner=draft.owner, patient=draft.patient)
    if draft.status != Draft.Status.EDITING:
        raise DraftRejected("DRAFT_NOT_EDITING")
    if draft.version != expected_version:
        raise DraftRejected("VERSION_CONFLICT")
    draft.status = Draft.Status.ABANDONED
    draft.save(update_fields=["status"])
    return draft


@transaction.atomic
def submit_draft(*, draft, expected_version, submitted_request) -> Draft:
    draft = Draft.objects.select_for_update().get(id=draft.id, owner=draft.owner, patient=draft.patient)
    if draft.expires_at and draft.expires_at <= datetime.now(timezone.utc):
        raise DraftRejected('DRAFT_EXPIRED')
    if draft.status != Draft.Status.EDITING:
        raise DraftRejected("DRAFT_NOT_EDITING")
    if draft.version != expected_version:
        raise DraftRejected("VERSION_CONFLICT")
    draft.status = Draft.Status.SUBMITTED
    draft.submitted_request = submitted_request
    draft.save(update_fields=["status", "submitted_request"])
    return draft


def read_draft(draft_id, *, owner, patient) -> Draft | None:
    return Draft.objects.filter(id=draft_id, owner=owner, patient=patient).first()


def list_restorable_drafts(*, owner, patient, form_type=None):
    query = Draft.objects.filter(owner=owner, patient=patient, status=Draft.Status.EDITING).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=datetime.now(timezone.utc)))
    if form_type is not None:
        query = query.filter(form_type=form_type)
    return query.order_by("-saved_at")
