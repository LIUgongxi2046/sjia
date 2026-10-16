"""T28 运行隔离、观测与健康检查逻辑验证。

真实备份恢复、告警外发与 RTO/RPO 由 T33 依据 D01/D09 验证；本文件验证健康检查。
运行：backend/.venv/bin/python backend/manage.py test tests.test_T28 --keepdb
"""
from datetime import datetime, timezone
from unittest.mock import patch

from django.test import TestCase

from family_content.models import EnvironmentGate
from observability.health import check_broker, check_database, readiness


def _now():
    return datetime.now(timezone.utc)


class HealthCheckTest(TestCase):
    def setUp(self):
        state = patch('config.runtime_admission._ADMITTED', None)
        state.start(); self.addCleanup(state.stop)
        EnvironmentGate.objects.create(environment="test", version=1, enabled=True, updated_at=_now())

    def test_database_reachable(self):
        self.assertTrue(check_database())

    def test_broker_reachable(self):
        self.assertTrue(check_broker())

    def test_readiness_requires_runtime_configuration(self):
        result = readiness("test")
        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(result["errors"]["configuration"], "RUNTIME_CONFIGURATION_REQUIRED")
        self.assertTrue(result["dependencies"]["database"])
        self.assertTrue(result["dependencies"]["broker"])
        # A database gate row is desired state, not evidence of process adoption.
        self.assertIsNone(result["loaded_config_version"])
        self.assertEqual(result["runtime_admission"], "not_ready")
