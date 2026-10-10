"""审计服务：受保护动作的允许、拒绝与提交记录。

写入成功审计与业务同事务；读取与拒绝审计在独立短事务确认后才返回响应。
审计无法保存时调用方应拒绝受保护操作并告警。
"""
from datetime import datetime, timezone

from audit.models import AuditEvent


def write_audit(
    *,
    actor=None,
    patient=None,
    action,
    object_type,
    object_id=None,
    object_version=None,
    request_id=None,
    outcome,
    reason_code=None,
    retention_policy,
    occurred_at=None,
) -> AuditEvent:
    return AuditEvent.objects.create(
        actor=actor,
        patient=patient,
        action=action,
        object_type=object_type,
        object_id=object_id,
        object_version=object_version,
        request_id=request_id,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        outcome=outcome,
        reason_code=reason_code,
        retention_policy=retention_policy,
    )
