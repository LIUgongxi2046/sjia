"""Manual, allowlisted OpenTelemetry. No auto-instrumentation or clinical payloads.

Only traceparent crosses the API/outbox/broker boundary. Baggage, tracestate,
request headers, URLs, SQL, arguments, IDs and exception text are never recorded.
Export is disabled unless an operator explicitly admits a configured endpoint.
"""
from contextlib import contextmanager
import os
import re
from threading import Lock
from urllib.parse import urlsplit

from opentelemetry import trace
from opentelemetry.context import Context, attach, detach
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_PROVIDER = None
_PID = None
_LOCK = Lock()
_PROPAGATOR = TraceContextTextMapPropagator()
_TRACEPARENT = re.compile(r'^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$')
_SPAN_NAMES = frozenset(('api.request', 'outbox.publish', 'worker.execute'))


def valid_traceparent(value):
    if not isinstance(value, str) or not _TRACEPARENT.fullmatch(value):
        return ''
    return value if int(value[3:35], 16) and int(value[36:52], 16) else ''


def configuration(environ=None):
    environ = os.environ if environ is None else environ
    enabled = environ.get('SJIA_OTEL_EXPORT_ENABLED', '0')
    if enabled not in ('0', '1'):
        raise ValueError('OTEL_EXPORT_OPT_IN_INVALID')
    try:
        ratio = float(environ.get('SJIA_OTEL_SAMPLE_RATIO', '0.1'))
    except ValueError:
        raise ValueError('OTEL_SAMPLE_RATIO_INVALID') from None
    if not 0 <= ratio <= 1:
        raise ValueError('OTEL_SAMPLE_RATIO_INVALID')
    endpoint = environ.get('SJIA_OTEL_TRACES_ENDPOINT', '')
    if enabled == '1':
        # The official exporter otherwise merges ambient OTEL headers/certificates.
        # Do not accidentally transmit credentials belonging to a different collector.
        if any(value and key.startswith(('OTEL_EXPORTER_OTLP', 'OTEL_PYTHON_EXPORTER_',
                'OTEL_PYTHON_SDK_INTERNAL_METRICS')) for key, value in environ.items()):
            raise ValueError('OTEL_AMBIENT_EXPORT_CONFIGURATION_FORBIDDEN')
        parsed = urlsplit(endpoint)
        if (parsed.scheme not in ('https', 'http') or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment or
                parsed.path != '/v1/traces'):
            raise ValueError('OTEL_APPROVED_ENDPOINT_REQUIRED')
    return enabled == '1', ratio, endpoint


def build_provider(environ=None):
    enabled, ratio, endpoint = configuration(environ)
    # Resource() deliberately avoids SDK environment/host/process detectors.
    provider = TracerProvider(resource=Resource({'service.name': 'sjia-runtime'}),
        sampler=ParentBased(TraceIdRatioBased(ratio)), shutdown_on_exit=True)
    if enabled:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.http.transport._urllib3 import Urllib3HTTPTransport
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        # This project has a Django app named requests. Use the pinned SDK's
        # official urllib3 transport to avoid shadowing Python requests.Session.
        # PoolManager has no environment-proxy/netrc credential discovery.
        exporter = OTLPSpanExporter(endpoint=endpoint, headers={}, timeout=3,
            _transport=Urllib3HTTPTransport(verify=True))
        provider.add_span_processor(BatchSpanProcessor(exporter, max_queue_size=512,
            max_export_batch_size=64, schedule_delay_millis=5000, export_timeout_millis=3000))
    return provider


def get_tracer():
    global _PROVIDER, _PID
    pid = os.getpid()
    if _PROVIDER is None or _PID != pid:
        with _LOCK:
            if _PROVIDER is None or _PID != pid:
                _PROVIDER, _PID = build_provider(), pid
    return _PROVIDER.get_tracer('sjia.safe-runtime', '1')


def trace_headers():
    carrier = {}
    _PROPAGATOR.inject(carrier)
    value = valid_traceparent(carrier.get('traceparent'))
    return {'traceparent': value} if value else {}


def parent_context(value):
    value = valid_traceparent(value)
    return _PROPAGATOR.extract({'traceparent': value}, context=Context()) if value else Context()


@contextmanager
def safe_span(name, *, parent=None, root=False, kind=SpanKind.INTERNAL):
    if name not in _SPAN_NAMES:
        raise ValueError('TRACE_OPERATION_UNREGISTERED')
    context = parent_context(parent) if parent is not None else (Context() if root else None)
    with get_tracer().start_as_current_span(name, context=context, kind=kind,
            record_exception=False, set_status_on_exception=False) as span:
        try:
            yield span
        except BaseException:
            # Never record exception str/repr/stacktrace, which may contain patient data.
            span.set_status(Status(StatusCode.ERROR))
            raise


def outbox_span(event, operation='outbox.publish'):
    return safe_span(operation, parent=getattr(event, 'traceparent', ''), kind=SpanKind.PRODUCER)


def capture_outbox_trace(sender, instance, raw=False, **kwargs):
    if raw:
        return
    if instance._state.adding:
        instance.traceparent = trace_headers().get('traceparent', '')


def begin_worker_span(sender=None, task=None, **kwargs):
    task = task or sender
    if task is None or not task.name.startswith(('tasks.', 'retention.', 'alerting.', 'notifications.')):
        return
    request = task.request
    headers = getattr(request, 'headers', None) or {}
    span = get_tracer().start_span('worker.execute',
        context=parent_context(headers.get('traceparent')), kind=SpanKind.CONSUMER)
    token = attach(trace.set_span_in_context(span))
    request._sjia_trace = (span, token)


def end_worker_span(sender=None, task=None, state=None, **kwargs):
    task = task or sender
    request = getattr(task, 'request', None)
    current = getattr(request, '_sjia_trace', None)
    if current is None:
        return
    span, token = current
    try:
        outcome = state if state in ('SUCCESS', 'FAILURE', 'RETRY', 'REVOKED') else 'UNKNOWN'
        span.set_attribute('worker.outcome', outcome)
        if outcome == 'FAILURE':
            span.set_status(Status(StatusCode.ERROR))
    finally:
        detach(token)
        span.end()
        del request._sjia_trace


def register_signals():
    from celery.signals import task_postrun, task_prerun
    from django.db.models.signals import pre_save
    from tasks.models import OutboxEvent

    configuration()  # Reject malformed admission at startup; create no exporter yet.
    pre_save.connect(capture_outbox_trace, sender=OutboxEvent,
        dispatch_uid='sjia.outbox.traceparent', weak=False)
    task_prerun.connect(begin_worker_span, dispatch_uid='sjia.worker.trace.begin', weak=False)
    task_postrun.connect(end_worker_span, dispatch_uid='sjia.worker.trace.end', weak=False)
