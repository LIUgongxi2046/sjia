"""Read/provider lifetime fence without extending business transactions.

Normal API/worker entrypoints hold a shared session advisory lock. Disposition
uses the matching exclusive transaction lock, so a pre-erasure response/provider
call finishes before closure is approved/applied; post-erasure consumers can
only observe filtered/tombstoned rows. Direct stale ORM writes are also guarded
by the database trigger, including nested snapshots and prior-version chains.
"""
from contextlib import contextmanager
from functools import wraps
from django.db import connection
from retention.erasure_models import ContentDisposed


@contextmanager
def content_reader():
    from retention.content_recovery import assert_configured_restore_admission
    assert_configured_restore_admission(verify_content=False)
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_lock_shared(1397377345,26)')
    try:
        yield
    finally:
        if connection.connection is not None:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock_shared(1397377345,26)')


def guarded_consumer(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with content_reader():
            return function(*args, **kwargs)
    return wrapped


def assert_content_available(adapter, identity):
    from retention.models import ContentAccessRevocation, ContentErasure
    if ContentErasure.objects.filter(adapter=adapter, object_id=identity).exists():
        raise ContentDisposed('CONTENT_DISPOSED')
    if ContentAccessRevocation.objects.filter(adapter=adapter, object_id=identity).exists():
        from retention.disposition import DispositionRejected
        raise DispositionRejected('CONTENT_ACCESS_REVOKED')
