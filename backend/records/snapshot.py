"""One coherent, read-only permission/selection/serialization snapshot for records."""
from functools import wraps

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from authorization.policy import (Denied, check_case_grant, check_principal_session,
    environment_enabled)
from identity.session import read_session_principal


def begin_record_snapshot():
    # Must be the first statement inside the HTTP operation's transaction.
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')


def record_read_snapshot(view):
    @wraps(view)
    @transaction.atomic
    def snapshot(*args, **kwargs):
        begin_record_snapshot()
        return view(*args, **kwargs)
    return snapshot


def authorize_record_snapshot(request, principal, patient, action, *, record_id=None,
                              author_id=None, field_codes=None):
    """Read authorization only; mutation callers must retain their locked gate.

    A revocation committed after this transaction's first read applies to the next
    query. Never mix new permission rows with old clinical rows inside one result.
    """
    if action not in {'read_raw', 'read_history'}:
        raise Denied('READ_SNAPSHOT_ACTION_INVALID')
    _, auth_epoch, _ = read_session_principal(request)
    try:
        environment_enabled(settings.SJIA_ENVIRONMENT)
        check_principal_session(principal, auth_epoch)
        # Family content reads cannot bypass the non-active patient boundary
        # enforced by the family relation scope and summary readers. Controlled
        # professional audit/disposition routes use their own authorization.
        if patient.status != 'active':
            raise Denied('PATIENT_UNAVAILABLE')
        return check_case_grant(principal, patient, action, timezone.now(),
            record_id=record_id, author_id=author_id, field_codes=field_codes,
            purpose='family_observation')
    except Denied as exc:
        from api.base import ApiError
        raise ApiError(exc.reason_code, 403) from None
