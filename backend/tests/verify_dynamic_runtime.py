import json
import os
import unittest
from hashlib import sha256

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django
import httpx

django.setup()

from django.db import connection, transaction
from django.conf import settings
from django.utils import timezone
from jsonschema import Draft202012Validator

from identity.models import Patient
from tasks.models import Task
from tasks.models import OutboxEvent
from configurations.models import ConfigVersion
from configurations.schemas import SCHEMAS
from alerting.triggers import SOURCE_TYPES, schedule_due
from tests.verify_v2_migration import ROOT, require, snapshot


class DynamicRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.atomic = transaction.atomic()
        cls.atomic.__enter__()
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cls.before = snapshot(connection.connection)
        cls.counts = {'anonymous_http_requests': 0}
        cls.sources = {path: sha256((ROOT / path).read_bytes()).hexdigest() for path in (
            'backend/api/urls_v2.py', 'backend/api/views_support2.py', 'backend/api/views_dynamic.py', 'backend/api/views_v2_requests.py',
            'backend/alerting/policies.py', 'backend/alerting/computation.py', 'backend/alerting/inputs.py', 'backend/alerting/lifecycle.py',
            'backend/alerting/service.py', 'backend/alerting/options.py', 'backend/alerting/triggers.py', 'backend/assessment/options.py', 'backend/assessment/qualified_facts.py',
            'backend/assessment/selection.py', 'backend/assessment/clinical_selection.py', 'backend/requests/dynamic.py',
            'backend/configurations/schemas.py', 'backend/configurations/service.py', 'backend/configurations/lifecycle.py',
            'backend/verification/workflow.py', 'backend/files/purpose_dependencies.py', 'backend/config/settings/base.py',
            'backend/tasks/worker.py', 'backend/tasks/dispatcher.py',
            'backend/archives/inputs.py', 'backend-design/v2-protocol.schema.json', 'alert-design/node.schema.json', 'backend/tests/verify_dynamic_runtime.py')}
        cls.passed = True

    def tearDown(self):
        if self._outcome.result.failures or self._outcome.result.errors:
            type(self).passed = False

    @classmethod
    def tearDownClass(cls):
        require(snapshot(connection.connection) == cls.before, '动态检查改变已保存业务资料。')
        require(all(sha256((ROOT / path).read_bytes()).hexdigest() == value for path, value in cls.sources.items()),
            '动态检查期间源码发生变化。')
        report = {'checked_at': timezone.now().isoformat(), 'status': 'PASS' if cls.passed else 'FAIL',
            'scope': 'ACTUAL_ANONYMOUS_DYNAMIC_HTTP', 'counts': cls.counts, 'preserved_tables': len(cls.before),
            'business_writes': 0, 'source_sha256': cls.sources, 'clinical_acceptance': 'NOT_RUN', 'visual': 'NOT_RUN'}
        (ROOT / '.planning-work/t50-runtime.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        cls.atomic.__exit__(None, None, None)
        print(json.dumps({key: value for key, value in report.items() if key != 'source_sha256'}, ensure_ascii=False))

    def test_dynamic_endpoints_reject_anonymous_identity_before_resource_lookup(self):
        patient = Patient.objects.order_by('id').first()
        task = Task.objects.order_by('id').first()
        self.assertIsNotNone(patient)
        self.assertIsNotNone(task)
        targets = [('POST', f'/api/v2/support/patients/{patient.id}/alert-evaluations'),
            ('GET', f'/api/v2/support/patients/{patient.id}/alert-evaluation-options?mode=current&known_at={timezone.now().isoformat()}'),
            ('GET', f'/api/v2/support/alert-evaluations/{task.id}'),
            ('GET', f'/api/v2/support/alert-evaluation-tasks/{task.id}'),
            ('GET', f'/api/v2/support/patients/{patient.id}/alert-evaluation-triggers')]
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10) as client:
            for method, path in targets:
                response = client.request(method, path)
                self.assertEqual(response.status_code, 401, path)
                self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
                self.assertIn('no-store', response.headers['cache-control'])
                self.counts['anonymous_http_requests'] += 1

    def test_trigger_registration_and_actual_schedule_directory(self):
        Draft202012Validator.check_schema(SCHEMAS[('provider', 'dynamic-schedule-2')])
        self.assertEqual(schedule_due.name, 'alerting.triggers.schedule_due')
        self.assertEqual(settings.CELERY_BEAT_SCHEDULE['schedule-dynamic-evaluations']['task'], schedule_due.name)
        schedules = ConfigVersion.objects.filter(schema_version='dynamic-schedule-2',
            payload__environment=settings.SJIA_ENVIRONMENT)
        ids = [str(value) for value in schedules.values_list('id', flat=True)]
        tasks = list(Task.objects.filter(schema_version='dynamic-trigger-task-2', input_refs__schedule__id__in=ids).values_list('id', flat=True))
        self.counts.update({'actual_schedule_versions': len(ids), 'actual_trigger_tasks': len(tasks),
            'actual_source_events': OutboxEvent.objects.filter(event_type__in=SOURCE_TYPES).count()})


if __name__ == '__main__':
    unittest.main(verbosity=2)
