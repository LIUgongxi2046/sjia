"""首页与提示投影：assessment_state 判定与真实阅读记录。

只有 assessment_state=current 且无有效事件时可显示 clear；
GET、预加载和历史查看不创建临床事件或阅读记录；首次真实打开才写入 read_at。
"""
from datetime import datetime, timezone

from alerting.models import AlertRecipient


def determine_assessment_state(*, config_available: bool, evaluation=None) -> str:
    """返回 not_evaluated/configuration_pending/insufficient/current/stale。"""
    if not config_available:
        return "configuration_pending"
    if evaluation is None:
        return "not_evaluated"
    status = getattr(evaluation, "status", None)
    decision = getattr(evaluation, "decision", None)
    validity = getattr(evaluation, "validity", None)
    if status == "configuration_pending":
        return "configuration_pending"
    if status == "insufficient" or (status == "completed" and decision == "unknown"):
        return "insufficient"
    if status == "completed" and validity == "valid":
        return "current"
    return "stale"


def mark_read(recipient: AlertRecipient, read_at=None) -> AlertRecipient:
    """首次真实打开写入 read_at，重复不改变时间。"""
    AlertRecipient.objects.filter(id=recipient.id, read_at__isnull=True).update(read_at=read_at or datetime.now(timezone.utc))
    recipient.refresh_from_db(fields=['read_at'])
    return recipient


def clear_condition(*, assessment_state: str, has_current_alert: bool) -> bool:
    return assessment_state == "current" and not has_current_alert
