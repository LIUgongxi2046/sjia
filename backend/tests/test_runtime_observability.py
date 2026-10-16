"""No DB/network: SDK spans, real isolated process metrics, operator-only config."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from observability import tracing
from observability.middleware import RuntimeObservationMiddleware
from tasks.models import OutboxEvent
from tasks.dispatcher import publish_task

ROOT = Path(__file__).resolve().parents[2]


class TraceTests(SimpleTestCase):
    def setUp(self):
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider(sampler=ALWAYS_ON, resource=Resource({'service.name': 'synthetic'}))
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer = patch.object(tracing, 'get_tracer', return_value=self.provider.get_tracer('test'))
        self.tracer.start()
        self.addCleanup(self.tracer.stop)
        self.addCleanup(self.provider.shutdown)

    def test_api_outbox_dispatch_worker_parentage_and_no_sensitive_content(self):
        event = OutboxEvent(id=uuid.uuid4(), event_type='summary.requested', task_id=uuid.uuid4(), schema_version='test')
        task = SimpleNamespace(name='tasks.worker.consume', request=SimpleNamespace(headers={}))

        def handle(request):
            tracing.capture_outbox_trace(OutboxEvent, event)
            with patch('tasks.dispatcher.app.send_task', return_value=SimpleNamespace(id='broker-id')) as send:
                publish_task(event)
            task.request.headers = send.call_args.kwargs['headers']
            return HttpResponse(status=201)

        request = RequestFactory().post('/api/private-patient?secret=value', data='PRIVATE_CLINICAL',
            content_type='text/plain', HTTP_TRACEPARENT='00-' + '1' * 32 + '-' + '2' * 16 + '-01',
            HTTP_BAGGAGE='credential=PRIVATE_SECRET', HTTP_COOKIE='PRIVATE_COOKIE')
        request.resolver_match = SimpleNamespace(url_name='summary_create')
        response = RuntimeObservationMiddleware(handle)(request)
        tracing.begin_worker_span(task=task)
        tracing.end_worker_span(task=task, state='SUCCESS')
        spans = {span.name: span for span in self.exporter.get_finished_spans()}
        api, producer, worker = [spans[name] for name in ('api.request', 'outbox.publish', 'worker.execute')]
        self.assertIsNone(api.parent)
        self.assertEqual(producer.parent.span_id, api.context.span_id)
        self.assertEqual(worker.parent.span_id, producer.context.span_id)
        self.assertEqual(api.context.trace_id, worker.context.trace_id)
        self.assertEqual(response['X-Trace-ID'], f'{worker.context.trace_id:032x}')
        self.assertEqual(set(task.request.headers), {'traceparent'})
        output = '\n'.join(span.to_json() for span in spans.values())
        for secret in ('PRIVATE', 'private-patient', str(event.id), str(event.task_id), 'baggage'):
            self.assertNotIn(secret, output)

    def test_exception_recording_is_status_only_and_context_is_reset(self):
        with self.assertRaises(RuntimeError):
            with tracing.safe_span('api.request'):
                raise RuntimeError('PRIVATE_PATIENT_AND_CREDENTIAL')
        span = self.exporter.get_finished_spans()[0]
        self.assertEqual(span.status.status_code.name, 'ERROR')
        self.assertEqual(span.events, ())
        self.assertNotIn('PRIVATE', span.to_json())
        self.assertEqual(tracing.trace_headers(), {})

    def test_new_outbox_only_no_reparent_on_update(self):
        event = OutboxEvent(traceparent='old')
        event._state.adding = False
        with tracing.safe_span('api.request'):
            tracing.capture_outbox_trace(OutboxEvent, event)
        self.assertEqual(event.traceparent, 'old')

    def test_invalid_and_zero_traceparents_rejected(self):
        for value in (None, {}, 'secret', '00-' + '0' * 32 + '-' + '2' * 16 + '-01',
                '00-' + '1' * 32 + '-' + '0' * 16 + '-01', 'ff-' + '1' * 32 + '-' + '2' * 16 + '-01'):
            self.assertEqual(tracing.valid_traceparent(value), '')

    def test_exporter_is_never_constructed_by_default_even_with_standard_otel_environment(self):
        with patch('opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter') as exporter:
            provider = tracing.build_provider({'OTEL_EXPORTER_OTLP_ENDPOINT': 'https://unapproved.invalid'})
            exporter.assert_not_called()
            provider.shutdown()

    def test_export_admission_and_sampling_validation(self):
        for config in ({'SJIA_OTEL_EXPORT_ENABLED': 'true'}, {'SJIA_OTEL_SAMPLE_RATIO': 'nan'},
                {'SJIA_OTEL_SAMPLE_RATIO': '1.5'}, {'SJIA_OTEL_EXPORT_ENABLED': '1'},
                {'SJIA_OTEL_EXPORT_ENABLED': '1', 'SJIA_OTEL_TRACES_ENDPOINT': 'https://secret@collector/v1/traces'},
                {'SJIA_OTEL_EXPORT_ENABLED': '1', 'SJIA_OTEL_TRACES_ENDPOINT': 'https://collector/v1/traces',
                 'OTEL_EXPORTER_OTLP_HEADERS': 'Authorization=SECRET'},
                {'SJIA_OTEL_EXPORT_ENABLED': '1', 'SJIA_OTEL_TRACES_ENDPOINT': 'https://collector/v1/traces?token=secret'}):
            with self.assertRaises(ValueError):
                tracing.configuration(config)

    def test_enabled_exporter_constructs_with_real_sdk_without_requests_name_collision_or_send(self):
        with patch('opentelemetry.exporter.http.transport._urllib3.Urllib3HTTPTransport.request') as send:
            provider = tracing.build_provider({'SJIA_OTEL_EXPORT_ENABLED': '1',
                'SJIA_OTEL_TRACES_ENDPOINT': 'https://synthetic-collector.invalid/v1/traces'})
            provider.shutdown()
            send.assert_not_called()

    def test_worker_failure_has_bounded_state_no_exception_or_arguments(self):
        task = SimpleNamespace(name='tasks.worker.consume', request=SimpleNamespace(headers={'baggage': 'SECRET'}))
        tracing.begin_worker_span(task=task, args=('SECRET',), kwargs={'payload': 'SECRET'})
        tracing.end_worker_span(task=task, state='FAILURE', retval=RuntimeError('SECRET'))
        span = self.exporter.get_finished_spans()[0]
        self.assertEqual(dict(span.attributes), {'worker.outcome': 'FAILURE'})
        self.assertEqual(tracing.trace_headers(), {})


class MultiprocessMetricsTests(SimpleTestCase):
    def test_counters_and_histograms_aggregate_across_real_exited_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, PROMETHEUS_MULTIPROC_DIR=directory, PYTHONPATH=str(ROOT / 'backend'))
            for count in (3, 5):
                subprocess.run([sys.executable, '-c', 'from observability.metrics import record_request; '
                    f'[record_request("multiprocess_probe", 200, .25) for _ in range({count})]'],
                    env=env, check=True, capture_output=True, timeout=30)
            result = subprocess.run([sys.executable, '-c', 'from observability import metrics; '
                'metrics.operational_snapshot=lambda: ([], 0.); print(metrics.render_metrics()[0])'],
                env=env, check=True, capture_output=True, text=True, timeout=30).stdout
        self.assertIn('api_requests_total{operation="multiprocess_probe",status="200"} 8.0', result)
        self.assertIn('api_request_duration_seconds_count{operation="multiprocess_probe",status="200"} 8.0', result)
        self.assertIn('api_request_duration_seconds_sum{operation="multiprocess_probe",status="200"} 2.0', result)
        self.assertIn('sjia_metrics_multiprocess 1', result)


class OperatorAlertConfigTests(SimpleTestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('sjia_alert_config', ROOT / 'deploy/observability/configure.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_missing_operator_thresholds_fail_closed(self):
        example = json.loads((ROOT / 'deploy/observability/admission.example.json').read_text())
        with self.assertRaises(ValueError):
            self.module.render(example)

    def test_bounded_rules_and_receiver_file_render_without_contact(self):
        result = self.module.render({'operator_approved': True, 'approval_reference': 'synthetic-only',
            'alert_for_seconds': 60, 'outbox_age_seconds': 60, 'http_5xx_ratio': .02,
            'http_p95_seconds': 2, 'failed_or_uncertain_tasks': 1,
            'scrape_target': '127.0.0.1:8000', 'receiver_url_file': '/run/secrets/approved_alert_webhook'})
        self.assertEqual(len(result['sjia.rules.yaml']['groups'][0]['rules']), 7)
        receiver = result['alertmanager.yaml']['receivers'][0]['webhook_configs'][0]
        self.assertEqual(receiver['url_file'], '/run/secrets/approved_alert_webhook')
        self.assertNotIn('url', receiver)
