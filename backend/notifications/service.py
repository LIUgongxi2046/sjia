from datetime import datetime, timezone

from django.db import transaction

from notifications.models import NotificationAttempt
from notifications.availability import require_notification_protocol
from notifications.errors import NotificationRejected


def _now():
    return datetime.now(timezone.utc)


@transaction.atomic
def authorize_attempt(*, patient_id, recipient_id, provider_idempotency_key, permit_expires_at, principal_auth_epoch=0, relation_auth_epoch=0, publication_epoch=0):
    require_notification_protocol()
    raise NotificationRejected('NOTIFICATION_NATIVE_PERMIT_REQUIRED')


@transaction.atomic
def start_attempt(*, attempt, provider_request_id):
    require_notification_protocol()
    from notifications.native import submit_native

    attempt = _locked_attempt(attempt)
    if provider_request_id != attempt.provider_idempotency_key:
        raise NotificationRejected('NOTIFICATION_PROVIDER_REQUEST_MISMATCH')
    return submit_native(attempt.id)


@transaction.atomic
def record_receipt(*, attempt, provider, provider_event_id, status, evidence_sha256, occurred_at=None):
    require_notification_protocol()
    raise NotificationRejected('NOTIFICATION_SIGNED_RECEIPT_REQUIRED')


@transaction.atomic
def mark_uncertain(*, attempt) -> NotificationAttempt:
    attempt = _locked_attempt(attempt)
    if attempt.provider_idempotency_key.startswith('sjia-in-app-2:'):
        raise NotificationRejected('NOTIFICATION_NATIVE_QUERY_REQUIRED')
    if attempt.status == NotificationAttempt.Status.UNCERTAIN:
        return attempt
    if attempt.status != NotificationAttempt.Status.STARTED:
        raise NotificationRejected("ATTEMPT_NOT_IN_FLIGHT")
    attempt.status = NotificationAttempt.Status.UNCERTAIN
    attempt.save(update_fields=["status"])
    return attempt


@transaction.atomic
def cancel_attempt(*, attempt) -> NotificationAttempt:
    attempt = _locked_attempt(attempt)
    if attempt.status == NotificationAttempt.Status.CANCELLED:
        return attempt
    if attempt.status != NotificationAttempt.Status.AUTHORIZED:
        raise NotificationRejected("ATTEMPT_NOT_CANCELLABLE")
    if attempt.provider_idempotency_key.startswith('sjia-in-app-2:'):
        from notifications.native import checked_permit
        from tasks.models import Task

        task = checked_permit(attempt)
        if task.status not in ('accepted', 'cancelled'):
            raise NotificationRejected('ATTEMPT_NOT_CANCELLABLE')
        task.status, task.status_version = Task.Status.CANCELLED, task.status_version + 1
        task.save(update_fields=['status', 'status_version'])
    attempt.status = NotificationAttempt.Status.CANCELLED
    attempt.finished_at = _now()
    attempt.save(update_fields=["status", "finished_at"])
    return attempt


def _locked_attempt(attempt):
    current = NotificationAttempt.objects.select_for_update().filter(id=attempt.id).first()
    if current is None:
        raise NotificationRejected('NOTIFICATION_ATTEMPT_NOT_FOUND')
    return current
