"""An allowlisted log record only; request/response bodies and URL values never enter it."""
import json
import logging
import re
from time import monotonic
from opentelemetry.trace import SpanKind, Status, StatusCode

from observability.metrics import record_request
from observability.tracing import safe_span

LOGGER = logging.getLogger('sjia.runtime')
SAFE_OPERATION = re.compile(r'^[A-Za-z_][A-Za-z0-9_.-]{0,119}$')


class SafeRuntimeFormatter(logging.Formatter):
    def format(self, record):
        event = getattr(record, 'runtime_event', {})
        # Ignore message text, exception payloads and arbitrary extra log fields.
        return json.dumps({name: event[name] for name in ('trace_id', 'operation', 'status', 'duration_seconds')
            if name in event}, separators=(',', ':'))


class RuntimeObservationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Public trace headers are ignored. Trace IDs are correlation, never identity.
        with safe_span('api.request', root=True, kind=SpanKind.SERVER) as span:
            request.trace_id = f'{span.get_span_context().trace_id:032x}'
            return self.observe(request, span)

    def observe(self, request, span):
        started = monotonic()
        status = 500
        try:
            response = self.get_response(request)
            status = response.status_code
            response['X-Trace-ID'] = request.trace_id
            return response
        finally:
            match = getattr(request, 'resolver_match', None)
            operation = (getattr(match, 'url_name', None) or getattr(getattr(match, 'func', None), '__name__', None)
                or 'unmatched')
            if not SAFE_OPERATION.fullmatch(operation):
                operation = 'unmatched'
            elapsed = max(0., monotonic() - started)
            span.set_attribute('operation', operation)
            span.set_attribute('http.response.status_code', status)
            if status >= 500:
                span.set_status(Status(StatusCode.ERROR))
            record_request(operation, status, elapsed)
            LOGGER.info('runtime_request', extra={'runtime_event': {'trace_id': request.trace_id,
                'operation': operation, 'status': status, 'duration_seconds': round(elapsed, 6)}})
