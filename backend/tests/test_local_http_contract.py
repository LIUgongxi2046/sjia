"""Readiness truthfulness checks; these do not start servers or use a database."""
from copy import deepcopy
import importlib.util
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]


class LocalHttpHealthContractTests(SimpleTestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('local_http_smoke', ROOT / 'deploy/check_local_http.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.live = {'status': 'alive', 'scope': 'process_only'}
        self.health = {'status': 'not_ready',
            'dependencies': {'database': True, 'broker': True, 'schema': True, 'configuration': False},
            'errors': {'configuration': 'RUNTIME_CONFIGURATION_REQUIRED'},
            'clinical_qualification': 'NOT_VERIFIED_BY_HEALTH_PROBE'}

    def test_missing_runtime_config_is_recorded_as_503_without_approval(self):
        result = self.module.validate_runtime_health(200, self.live, 503, self.health)
        self.assertEqual(result['api_liveness_http'], 200)
        self.assertEqual(result['api_readiness_http'], 503)
        self.assertFalse(result['runtime_dependencies']['configuration'])

    def test_ready_requires_all_dependencies_and_no_errors(self):
        self.health['dependencies']['configuration'] = True
        self.health.update(status='ready', errors={})
        result = self.module.validate_runtime_health(200, self.live, 200, self.health)
        self.assertEqual(result['runtime_readiness'], 'ready')

    def test_wrong_schema_dependency_or_unexpected_readiness_failure_is_rejected(self):
        for name in ('database', 'broker', 'schema'):
            health = deepcopy(self.health)
            health['dependencies'][name] = False
            with self.assertRaises(AssertionError):
                self.module.validate_runtime_health(200, self.live, 503, health)
        self.health['errors']['configuration'] = 'CONFIGURATION_UNAVAILABLE'
        with self.assertRaises(AssertionError):
            self.module.validate_runtime_health(200, self.live, 503, self.health)

    def test_old_two_dependency_response_or_wrong_liveness_fails(self):
        with self.assertRaises(AssertionError):
            self.module.validate_runtime_health(503, self.live, 503, self.health)
        del self.health['dependencies']['schema']
        with self.assertRaises(AssertionError):
            self.module.validate_runtime_health(200, self.live, 503, self.health)
