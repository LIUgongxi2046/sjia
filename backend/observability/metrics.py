"""Bounded Prometheus labels and Gunicorn multiprocess aggregates.

PROMETHEUS_MULTIPROC_DIR must be set before Python starts. Only the API instance's
Gunicorn children share that directory; never share it with another API/container.
"""
import math
import os
import re

from django.db.models import Count, Min
from django.utils import timezone
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest, multiprocess

_REGISTRY = CollectorRegistry()
_COUNTER = Counter('api_requests_total', 'Completed requests across this API instance.',
    ('operation', 'status'), registry=_REGISTRY)
_DURATION = Histogram('api_request_duration_seconds', 'Request duration across this API instance.',
    ('operation', 'status'), buckets=(.01, .05, .1, .5, 1., 5., 30.), registry=_REGISTRY)
_SAFE_OPERATION = re.compile(r'^[A-Za-z_][A-Za-z0-9_.-]{0,119}$')


def record_request(operation, status, seconds):
    operation = operation if isinstance(operation, str) and _SAFE_OPERATION.fullmatch(operation) else 'unmatched'
    status = str(status) if isinstance(status, int) and 100 <= status <= 599 else '500'
    seconds = max(0., seconds) if isinstance(seconds, (float, int)) and math.isfinite(seconds) else 0.
    _COUNTER.labels(operation, status).inc()
    _DURATION.labels(operation, status).observe(seconds)


def operational_snapshot():
    from tasks.models import OutboxEvent, Task

    tasks = list(Task.objects.filter(status__in=Task.Status.values, task_type__in=Task.TaskType.values)
        .values('task_type', 'status').annotate(count=Count('id')))
    oldest = OutboxEvent.objects.filter(published_at__isnull=True).aggregate(oldest=Min('occurred_at'))['oldest']
    return tasks, max(0., (timezone.now() - oldest).total_seconds()) if oldest else 0.


def render_metrics():
    registry = _REGISTRY
    if os.environ.get('PROMETHEUS_MULTIPROC_DIR'):
        # A fresh registry avoids counting locally registered metrics twice.
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    lines = [generate_latest(registry).decode('utf-8').rstrip(),
        '# TYPE sjia_metrics_multiprocess gauge',
        f'sjia_metrics_multiprocess {int(bool(os.environ.get("PROMETHEUS_MULTIPROC_DIR")))}']
    try:
        tasks, oldest = operational_snapshot()
        lines.extend(['# TYPE sjia_tasks gauge', '# TYPE outbox_oldest_unpublished_seconds gauge',
            f'outbox_oldest_unpublished_seconds {oldest:.6f}'])
        for task in tasks:
            lines.append(f'sjia_tasks{{task_type="{task["task_type"]}",status="{task["status"]}"}} {task["count"]}')
        available = True
    except Exception:
        # Missing data is never rendered as a reassuring zero backlog.
        available = False
    lines.extend(['# TYPE sjia_operational_metrics_available gauge', f'sjia_operational_metrics_available {int(available)}'])
    return '\n'.join(lines) + '\n', available
