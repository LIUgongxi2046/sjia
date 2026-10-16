"""Two real PostgreSQL connections contend for the same approved selection CAS."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

from django.db import connections, transaction
from django.test import Client, TransactionTestCase
from django.utils import timezone

from alerting.models import AlertPresetChoice
from api.base import ApiError
from tests.isolated_rows import IsolatedSchemaRows
from tests import test_patient_alert_presets as fixtures


class PresetSelectionConcurrencyTest(TransactionTestCase):
    reset_sequences = False
    replace_trigger = fixtures.PatientAlertPresetTest.replace_trigger
    schedule_payload = fixtures.PatientAlertPresetTest.schedule_payload
    assignment_payload = fixtures.PatientAlertPresetTest.assignment_payload
    payload = fixtures.PatientAlertPresetTest.payload
    prepare = fixtures.PatientAlertPresetTest.prepare
    submit = fixtures.PatientAlertPresetTest.submit
    event = fixtures.PatientAlertPresetTest.event
    published_for_separate_selector = fixtures.PatientAlertPresetTest.published_for_separate_selector

    def setUp(self):
        rows = IsolatedSchemaRows(require_empty=True)
        self.addCleanup(rows.cleanup)
        fixtures.PatientAlertPresetTest.setUp(self)

    def test_two_connections_cannot_commit_two_choices_from_same_version(self):
        intents = [self.prepare(), self.prepare()]
        start = Barrier(2, timeout=20)
        def contender(intent):
            try:
                start.wait()
                try:
                    return ('committed', self.submit(intent)['version'])
                except ApiError as error:
                    return ('rejected', error.code)
            finally:
                connections['default'].close()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(contender, intents))
        self.assertCountEqual(results, [('committed', '1'), ('rejected', 'PRESET_VERSION_CONFLICT')])
        self.assertEqual(AlertPresetChoice.objects.filter(patient=self.patient).count(), 1)

    def test_two_shared_family_readers_never_upgrade_locks_or_mutate_tasks(self):
        from alerting.models import AlertEvent
        from family_content.alert_detail import alert_content
        from family_content.gate_locks import read_environment_gate_shared
        from tasks.models import Task
        _, _, _, event, _, _ = self.published_for_separate_selector()
        before = list(Task.objects.order_by('id').values('id', 'status', 'status_version', 'lease_generation'))
        start = Barrier(2, timeout=20)

        def reader(_):
            sql = []
            def observe(execute, statement, params, many, context):
                sql.append(statement.upper())
                return execute(statement, params, many, context)
            try:
                with transaction.atomic():
                    with connections['default'].cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '5s'")
                    read_environment_gate_shared(self.fx.gate.environment)
                    start.wait()
                    with connections['default'].execute_wrapper(observe):
                        result = alert_content(AlertEvent.objects.get(id=event.id), ['synthetic_test'])
                self.assertFalse(any('FOR UPDATE' in item or 'FOR NO KEY UPDATE' in item for item in sql), sql)
                self.assertFalse(any(item.lstrip().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for item in sql), sql)
                return result['content_state']
            finally:
                connections['default'].close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reader, range(2)))
        self.assertEqual(results, ['available', 'available'])
        self.assertEqual(list(Task.objects.order_by('id').values('id', 'status', 'status_version', 'lease_generation')), before)

    def test_relation_revocation_waits_for_shared_read_then_next_read_is_closed(self):
        from authorization.models import ServiceRoleGrant
        from family_content.alert_detail import alert_content
        from family_content.gate_locks import read_environment_gate_shared
        from tests.synthetic_fixtures import ensure_synthetic_audit_policy
        _, relation, _, event, _, _ = self.published_for_separate_selector()
        ensure_synthetic_audit_policy(self.actor, case=self)
        ServiceRoleGrant.objects.create(principal=self.actor, environment=self.fx.gate, action='decide_relation',
            granted_by=self.actor, evidence_ref='SYNTHETIC CONCURRENCY TEST ONLY', valid_from=timezone.now())
        client = Client()
        session = client.session
        session['_sjia_principal_id'], session['_sjia_auth_epoch'] = str(self.actor.id), self.actor.auth_epoch
        session['_sjia_audience'] = 'support'
        session.save()
        ready, release, attempted = Event(), Event(), Event()

        def reader():
            try:
                with transaction.atomic():
                    read_environment_gate_shared(self.fx.gate.environment)
                    state = alert_content(event, ['synthetic_test'])['content_state']
                    ready.set()
                    if not release.wait(20):
                        raise AssertionError('Timed out waiting to release the synthetic family read')
                    return state
            finally:
                connections['default'].close()

        def writer():
            try:
                attempted.set()
                response = client.post(f'/api/support/relations/{relation.id}/revoke',
                    {'reason': 'SYNTHETIC CONCURRENCY TEST ONLY'}, content_type='application/json')
                return response.status_code, response.json()
            finally:
                connections['default'].close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            reading = executor.submit(reader)
            try:
                self.assertTrue(ready.wait(20))
                writing = executor.submit(writer)
                self.assertTrue(attempted.wait(20))
                self.assertFalse(writing.done())
            finally:
                release.set()
            self.assertEqual(reading.result(timeout=20), 'available')
            status, body = writing.result(timeout=20)
        self.assertEqual(status, 200, body)
        self.assertEqual(body['status'], 'revoked')
        self.assertEqual(alert_content(event, ['synthetic_test'])['content_state'], 'unavailable')
