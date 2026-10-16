"""Runtime contract regressions. No database, Docker daemon or external calls."""
import json
import glob
import re
import shlex
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
import yaml

from observability import health, metrics
from observability.middleware import RuntimeObservationMiddleware, SafeRuntimeFormatter
from observability.views import internal_capabilities, internal_liveness, internal_metrics, internal_readiness

ROOT = Path(__file__).resolve().parents[2]


class DeploymentContractTests(SimpleTestCase):
    def setUp(self):
        self.compose = yaml.safe_load((ROOT / 'deploy/compose.yaml').read_text())
        self.services = self.compose['services']

    def test_frontend_image_copies_actual_build_and_design_dependencies(self):
        source = (ROOT / self.services['nginx']['build']['dockerfile']).read_text()
        for text in ('npm ci', 'npm run build', 'frontend-design', 'backend-design',
                'ui-delivery', '/app/guardian-h5/dist /usr/share/nginx/html'):
            self.assertIn(text, source)
        self.assertNotIn('localhost', source)

    def test_frontend_image_covers_every_external_static_import(self):
        # Check actual Docker COPY declarations, not only a hand-maintained filename list.
        copied_files, copied_directories = set(), set()
        for line in (ROOT / 'deploy/Dockerfile.frontend').read_text().splitlines():
            if not line.startswith('COPY ') or '--from=' in line:
                continue
            parts = shlex.split(line)[1:]
            for pattern in parts[:-1]:
                matches = glob.glob(str(ROOT / pattern))
                self.assertTrue(matches, pattern)
                for name in matches:
                    path = Path(name).resolve()
                    (copied_directories if path.is_dir() else copied_files).add(path)
        for path in (ROOT / 'guardian-h5/src').rglob('*'):
            if path.suffix not in ('.ts', '.tsx'):
                continue
            for name in re.findall(r"(?:from\s+|import\s*)['\"]([^'\"]+)['\"]", path.read_text()):
                if not name.startswith('.'):
                    continue
                target = (path.parent / name.split('?')[0]).resolve()
                if target.is_relative_to(ROOT / 'guardian-h5'):
                    continue
                # TypeScript imports often omit the extension.
                choices = [target, Path(str(target) + '.ts'), Path(str(target) + '.tsx')]
                target = next((value for value in choices if value.is_file()), target)
                self.assertTrue(target in copied_files or any(target.is_relative_to(directory)
                    for directory in copied_directories), f'Frontend image misses {target.relative_to(ROOT)}')

    def test_tls_files_require_explicit_operator_inputs_and_readonly_mounts(self):
        mounts = self.services['nginx']['volumes']
        for filename in ('server.crt', 'server.key'):
            mount = next(value for value in mounts if value['target'].endswith(filename))
            self.assertIn(':?', mount['source'])
            self.assertTrue(mount['read_only'])
            self.assertFalse(mount['bind']['create_host_path'])
        self.assertNotIn('openssl', (ROOT / 'deploy/Dockerfile.frontend').read_text())

    def test_runtime_processes_wait_for_completed_migrations(self):
        self.assertEqual(self.services['migrate']['command'], ['python', 'manage.py', 'migrate', '--noinput'])
        self.assertNotIn('migrate', self.services['migrate']['depends_on'])
        for name in ('api', 'worker', 'worker-purpose', 'worker-assessment', 'worker-notification', 'beat'):
            self.assertEqual(self.services[name]['depends_on']['migrate']['condition'], 'service_completed_successfully')

    def test_one_beat_and_every_dispatcher_queue_has_distinct_worker(self):
        commands = [service.get('command', []) for service in self.services.values()]
        self.assertEqual(sum('beat' in command for command in commands), 1)
        queues = []
        for name in ('worker', 'worker-purpose', 'worker-assessment', 'worker-notification'):
            command = self.services[name]['command']
            queue = next(value.split('=', 1)[1] for value in command if value.startswith('--queues='))
            queues.append(queue)
            self.assertTrue(any(value.startswith('--concurrency=') for value in command))
            self.assertIn('mem_limit', self.services[name])
            self.assertIn('fileobjects:/var/lib/sjia/objects', self.services[name]['volumes'])
        self.assertCountEqual(queues, ['default', 'purpose', 'assessment', 'notification'])

    def test_proxy_hides_internal_endpoints_and_never_logs_raw_urls(self):
        source = (ROOT / 'deploy/nginx.conf').read_text()
        self.assertIn('location /internal/ { return 404; }', source)
        self.assertIn('access_log off;', source)
        self.assertIn('Cache-Control "no-store"', source)

    def test_unused_object_store_no_longer_demands_unused_credentials(self):
        self.assertNotIn('object-store', self.services)
        self.assertEqual(self.services['api']['environment']['SJIA_FILE_OBJECT_ROOT'], '/var/lib/sjia/objects')
        self.assertIn('deploy/probe_runtime.py', (ROOT / 'deploy/Dockerfile').read_text())


@override_settings(SJIA_ENVIRONMENT='synthetic', INTERNAL_PROBE_NETWORKS=['127.0.0.0/8', '::1/128'])
class RuntimeProbeTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_liveness_has_no_dependency_access(self):
        with patch('observability.views.readiness', side_effect=AssertionError('not a liveness dependency')):
            response = internal_liveness(self.factory.get('/internal/liveness'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['status'], 'alive')
        self.assertIn('no-store', response['Cache-Control'])

    def test_internal_probe_rejects_external_peer_even_with_forged_forwarding_header(self):
        response = internal_liveness(self.factory.get('/internal/liveness', REMOTE_ADDR='203.0.113.9',
            HTTP_X_FORWARDED_FOR='127.0.0.1'))
        self.assertEqual(response.status_code, 404)

    def test_readiness_uses_configured_environment_not_user_query(self):
        with patch('observability.views.readiness', return_value={'status': 'not_ready'}) as probe:
            response = internal_readiness(self.factory.get('/internal/readiness?environment=another'))
        probe.assert_called_once_with('synthetic')
        self.assertEqual(response.status_code, 503)

    def test_readiness_contains_dependency_errors_without_secret_exception_text(self):
        with patch.object(health, 'check_database', side_effect=RuntimeError('SECRET database password')), \
                patch.object(health, 'check_schema') as schema, \
                patch.object(health, 'check_broker', side_effect=OSError('SECRET redis URL')):
            result = health.readiness('synthetic')
        schema.assert_not_called()
        self.assertEqual(result['status'], 'not_ready')
        self.assertEqual(result['dependencies'], {'database': False, 'schema': False, 'broker': False, 'configuration': False})
        self.assertNotIn('SECRET', json.dumps(result))

    def test_pending_migration_stays_not_ready(self):
        with patch.object(health, 'check_database', return_value=True), \
                patch.object(health, 'check_schema', return_value=False), \
                patch.object(health, 'check_broker', return_value=True):
            self.assertEqual(health.readiness()['status'], 'not_ready')

    def test_readiness_requires_admitted_runtime_but_never_claims_clinical_approval(self):
        admitted = {'state':'admitted_current','reason_code':None,
            'loaded_config_version':{'identity':{'runtime_config_id':'synthetic'}},
            'desired_config_version':{'runtime_config_id':'synthetic'},
            'capabilities':{'ocr':{'state':'disabled','reason_code':'CAPABILITY_NOT_ENABLED'}}}
        with patch.object(health, 'check_database', return_value=True), \
                patch.object(health, 'check_schema', return_value=True), \
                patch.object(health, 'check_broker', return_value=True), \
                patch('config.runtime_admission.admission_status',return_value=admitted):
            result = health.readiness()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['clinical_qualification'], 'NOT_VERIFIED_BY_HEALTH_PROBE')
        self.assertEqual(result['loaded_config_version'],admitted['loaded_config_version'])

    def test_broker_timeouts_are_bounded_and_connection_is_closed(self):
        with patch.object(health.redis.Redis, 'from_url') as connect:
            connect.return_value.ping.return_value = True
            self.assertTrue(health.check_broker())
            self.assertEqual(connect.call_args.kwargs['socket_connect_timeout'], 2)
            self.assertEqual(connect.call_args.kwargs['socket_timeout'], 2)
            connect.return_value.close.assert_called_once()

    def test_metrics_unavailability_is_explicit_not_zero_success(self):
        with patch.object(metrics, 'operational_snapshot', side_effect=RuntimeError('PRIVATE SOURCE')):
            response = internal_metrics(self.factory.get('/internal/metrics'))
        self.assertEqual(response.status_code, 503)
        self.assertIn(b'sjia_operational_metrics_available 0', response.content)
        self.assertNotIn(b'PRIVATE SOURCE', response.content)
        self.assertNotIn(b'outbox_oldest_unpublished_seconds 0', response.content)

    def test_capabilities_do_not_invent_model_or_clinical_readiness(self):
        states = {'ocr':{'state':'disabled','reason_code':'CAPABILITY_NOT_ENABLED'},
            'summary':{'state':'configured','requires_request_authorization':True}}
        with patch('observability.views.readiness', return_value={'status':'ready','capabilities':states}):
            response = internal_capabilities(self.factory.get('/internal/capabilities'))
        value = json.loads(response.content)
        self.assertEqual(value['capabilities'],states)
        self.assertEqual(value['clinical_qualification'],'NOT_VERIFIED_BY_HEALTH_PROBE')


class PrivacyObservationTests(SimpleTestCase):
    def test_middleware_log_and_metrics_never_receive_case_path_body_or_headers(self):
        request = RequestFactory().post('/api/patients/private-id?name=SECRET',
            data='SECRET CLINICAL BODY', content_type='text/plain', HTTP_COOKIE='SECRET_COOKIE',
            HTTP_X_REQUEST_ID='untrusted-secret')
        request.resolver_match = SimpleNamespace(url_name='record_create', func=None)
        with patch('observability.middleware.LOGGER') as logger, \
                patch('observability.middleware.record_request') as record:
            response = RuntimeObservationMiddleware(lambda request: HttpResponse(status=201))(request)
        event = logger.info.call_args.kwargs['extra']['runtime_event']
        self.assertEqual(set(event), {'trace_id', 'operation', 'status', 'duration_seconds'})
        self.assertNotIn('SECRET', json.dumps(event))
        self.assertNotIn('private-id', json.dumps(event))
        self.assertRegex(response['X-Trace-ID'], '^[a-f0-9]{32}$')
        self.assertEqual(record.call_args.args[:2], ('record_create', 201))

    def test_formatter_ignores_message_exception_and_arbitrary_extra(self):
        record = logging.LogRecord('sjia.runtime', logging.ERROR, '', 0, 'SECRET', (), None)
        record.runtime_event = {'trace_id': 'abc', 'operation': 'read', 'status': 500,
            'duration_seconds': .1, 'body': 'SECRET'}
        record.secret = 'SECRET'
        self.assertNotIn('SECRET', SafeRuntimeFormatter().format(record))

    def test_prometheus_histogram_uses_only_fixed_operation_and_status_labels(self):
        metrics.record_request('synthetic_probe', 200, .025)
        with patch.object(metrics, 'operational_snapshot', return_value=([], 0.)):
            text, available = metrics.render_metrics()
        self.assertTrue(available)
        self.assertIn('api_requests_total{operation="synthetic_probe",status="200"}', text)
        self.assertIn('le="+Inf"', text)
        self.assertNotIn('patient_id', text)


class ProxyTLSOptInTests(SimpleTestCase):
    def test_default_does_not_trust_forwarded_proto(self):
        from config.settings.proxy import trusted_proxy_header
        from django.middleware.csrf import CsrfViewMiddleware
        request = RequestFactory().post('/synthetic', HTTP_HOST='testserver',
            HTTP_X_FORWARDED_PROTO='https', HTTP_ORIGIN='https://testserver')
        with override_settings(SECURE_PROXY_SSL_HEADER=trusted_proxy_header(), CSRF_TRUSTED_ORIGINS=[]):
            self.assertFalse(request.is_secure())
            self.assertFalse(CsrfViewMiddleware(lambda request: None)._origin_verified(request))

    def test_explicit_proxy_opt_in_accepts_same_origin_https_only(self):
        from config.settings.proxy import trusted_proxy_header
        from django.middleware.csrf import CsrfViewMiddleware
        request = RequestFactory().post('/synthetic', HTTP_HOST='testserver',
            HTTP_X_FORWARDED_PROTO='https', HTTP_ORIGIN='https://testserver')
        with override_settings(SECURE_PROXY_SSL_HEADER=trusted_proxy_header('1'), CSRF_TRUSTED_ORIGINS=[]):
            self.assertTrue(request.is_secure())
            self.assertTrue(CsrfViewMiddleware(lambda request: None)._origin_verified(request))
            request.META['HTTP_ORIGIN'] = 'https://untrusted.example'
            self.assertFalse(CsrfViewMiddleware(lambda request: None)._origin_verified(request))

    def test_invalid_opt_in_is_rejected_and_compose_never_silently_enables_it(self):
        from config.settings.proxy import trusted_proxy_header
        for value in ('true', '', 'yes', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                trusted_proxy_header(value)
        compose = yaml.safe_load((ROOT / 'deploy/compose.yaml').read_text())
        self.assertIn(':?', compose['services']['api']['environment']['SJIA_TRUST_PROXY_TLS'])
        self.assertNotIn('ports', compose['services']['api'])
        self.assertIn('proxy_set_header X-Forwarded-Proto https;', (ROOT / 'deploy/nginx.conf').read_text())
