from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Event

from django.db import close_old_connections, connections
from django.utils import timezone

from tasks.service import TaskRejected, heartbeat


def maintain_lease(task_id, generation, stop, policy, execution_until):
    close_old_connections()
    try:
        while not stop.wait(policy['heartbeat_seconds']):
            now = timezone.now()
            if execution_until <= now:
                raise TaskRejected('TASK_EXECUTION_DEADLINE_EXCEEDED')
            until = min(execution_until, now + timedelta(seconds=policy['lease_seconds']))
            if heartbeat(task_id, generation, until) != 1:
                raise TaskRejected('LEASE_GENERATION_MISMATCH')
    finally:
        connections.close_all()


@contextmanager
def lease_heartbeat(task_id, generation, policy, execution_until):
    stop = Event()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='assessment-lease') as executor:
        pending = executor.submit(maintain_lease, task_id, generation, stop, policy, execution_until)
        def stop_heartbeat():
            stop.set()
            pending.result()
        try:
            yield stop_heartbeat
        finally:
            stop_heartbeat()
