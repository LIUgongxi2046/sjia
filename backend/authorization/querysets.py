"""获准 QuerySet：列表、搜索与总数均执行同一范围规则。"""
from datetime import datetime, timezone

from authorization.models import AccessGrant


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def active_grants(principal_id, action, now=None):
    now = now or _utcnow()
    return (
        AccessGrant.objects.filter(
            principal_id=principal_id,
            action=action,
            valid_from__lte=now,
            revoked_at__isnull=True,
        )
        .exclude(valid_until__lte=now)
    )


def accessible_patient_ids(principal_id, action, now=None) -> set:
    return set(active_grants(principal_id, action, now).values_list("patient_id", flat=True))


def scope_permits(grant, record_id=None, author_id=None) -> bool:
    if grant.record_scope == "all":
        return True
    if grant.record_scope == "own":
        return author_id is not None and author_id == grant.principal_id
    if grant.record_scope == "assigned":
        return record_id is not None and grant.assigned_record_id == record_id
    return False
