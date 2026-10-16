"""Async summaries use synthetic data and real admission/worker/database paths."""
from datetime import timedelta
from uuid import uuid4

from django.core.management import call_command
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from authorization.models import AccessGrant
from authorization.policy import Denied
from identity.models import Patient, Principal
from summaries.jobs import execute_summary
from summaries.models import Summary
from summaries.service import SummaryRejected, read_summary, list_summaries
from tasks.models import ConsumerReceipt, OutboxEvent, Task, TaskAttempt
from tests.integration.helpers import activate_summary_configs, post_record
from tests.synthetic_fixtures import qualify_demo


class AsyncSummaryTests(TestCase):
    def setUp(self):
        call_command('seed_demo', verbosity=0)
        self.principal = Principal.objects.filter(account_status='active').first()
        self.patient = Patient.objects.get(display_id='demo-child')
        qualify_demo(self, self.principal, self.patient)
        activate_summary_configs(self)
        session = self.client.session
        session['_sjia_principal_id'] = str(self.principal.id)
        session['_sjia_auth_epoch'] = self.principal.auth_epoch
        session['_sjia_audience'] = 'family'
        session.save()
        result = post_record(self.client, self.patient, {'kind': 'observation', 'capture_method': 'manual',
            'fields': [{'field_code': 'temp', 'value_type': 'decimal', 'value_state': 'known',
                'raw_numeric': '38.0', 'raw_unit': 'C', 'sample_time_origin': 'observation',
                'medical_time': {'precision': 'date', 'local_date': '2026-09-27', 'resolution_status': 'local_only'}}]})
        self.assertEqual(result.status_code, 201, result.content)
        self.patient.refresh_from_db()
        self.payload = {'from_date': '2026-09-01', 'to_date': '2026-09-30', 'scope_timezone': 'Asia/Shanghai',
            'include_unknown_time': False, 'record_kinds': ['observation'], 'include_source_status': True}
        self.target = f'/api/patients/{self.patient.id}/summaries'
        request_id = str(uuid4())
        prepared = self.client.post('/api/write-intents', {'operation_id': 'summary_create',
            'target_key': self.target, 'request_id': request_id, 'payload': self.payload,
            'schema_version': '1.0.0'}, content_type='application/json')
        self.assertEqual(prepared.status_code, 201, prepared.content)
        self.intent = prepared.json()
        self.headers = {'HTTP_X_WRITE_INTENT': self.intent['intent_id'],
            'HTTP_X_INTENT_GENERATION': self.intent['generation'], 'HTTP_IDEMPOTENCY_KEY': request_id}

    def submit(self):
        response = self.client.post(self.target, self.payload, content_type='application/json', **self.headers)
        self.assertEqual(response.status_code, 202, response.content)
        task = Task.objects.get(id=response.json()['task_id'])
        return task, OutboxEvent.objects.get(task=task, event_type='summary.requested')

    def test_admission_is_queued_and_idempotent_without_materialization(self):
        task, event = self.submit()
        self.assertEqual(task.status, 'accepted')
        self.assertFalse(Summary.objects.filter(task_id=task.id).exists())

        self.assertNotIn('fields', task.input_refs)
        repeated, _ = self.submit()
        self.assertEqual(repeated.id, task.id)
        self.assertEqual(OutboxEvent.objects.filter(task=task).count(), 1)
        response = self.client.get(f"/api/requests/{self.intent['intent_id']}")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['receipt']['resource_type'], 'task')
        self.assertEqual(response.json()['receipt']['resource_id'], str(task.id))
        self.assertEqual(response.json()['task']['status'], 'accepted')

    def test_worker_materializes_once_and_preserves_admission(self):
        task, event = self.submit()
        admission, digest = task.input_refs, task.input_sha256
        execute_summary(event, 'synthetic-worker')
        execute_summary(event, 'duplicate-worker')
        task.refresh_from_db()
        self.assertEqual(task.status, 'completed')
        self.assertEqual((task.input_refs, task.input_sha256), (admission, digest))
        summary = Summary.objects.get(task_id=task.id)
        self.assertIsNotNone(summary.execution_snapshot)
        result = read_summary(summary.id, self.patient)
        self.assertFalse(result['needs_regeneration'])
        self.assertEqual(result['items'][0]['snapshot']['field']['raw_numeric'], '38.0')
        self.assertFalse(list_summaries(self.patient)['summaries'][0]['needs_regeneration'])
        self.assertEqual(ConsumerReceipt.objects.filter(event=event, consumer='summary').count(), 1)
        self.assertEqual(TaskAttempt.objects.filter(task=task).count(), 1)
        repeated, _ = self.submit()
        self.assertEqual(repeated.id, task.id)

    def test_cancel_before_worker_prevents_output(self):
        task, event = self.submit()
        cancelled = self.client.post(f'/api/tasks/{task.id}/cancel', {}, content_type='application/json')
        self.assertEqual(cancelled.status_code, 200, cancelled.content)
        execute_summary(event, 'late-worker')
        task.refresh_from_db()
        self.assertEqual(task.status, 'cancelled')
        self.assertFalse(Summary.objects.filter(task_id=task.id).exists())
        self.assertEqual(ConsumerReceipt.objects.get(event=event, consumer='summary').effect_resource_id, task.id)

    def test_revoked_permission_fails_without_output(self):
        task, event = self.submit()
        AccessGrant.objects.filter(principal=self.principal, patient=self.patient,
            action='create_summary').update(revoked_at=timezone.now())
        with self.assertRaises(Denied):
            execute_summary(event, 'revoked-worker')
        task.refresh_from_db()
        self.assertEqual(task.status, 'failed')
        self.assertFalse(Summary.objects.filter(task_id=task.id).exists())

    def test_cancel_after_claim_before_render_preserves_terminal_receipt(self):
        from unittest.mock import patch
        from summaries.jobs import cancel_summary, current_task

        task, event = self.submit()
        calls = 0
        def interleaved(event):
            nonlocal calls
            calls += 1
            if calls == 2:
                cancel_summary(task_id=task.id, actor=self.principal,
                    auth_epoch=self.principal.auth_epoch, audience='family')
            return current_task(event)
        with patch('summaries.jobs.current_task', side_effect=interleaved):
            execute_summary(event, 'interleaved-worker')
        task.refresh_from_db()
        self.assertEqual(task.status, 'cancelled')
        self.assertFalse(Summary.objects.filter(task_id=task.id).exists())
        self.assertEqual(TaskAttempt.objects.get(task=task).status, 'cancelled')
        self.assertEqual(ConsumerReceipt.objects.get(event=event, consumer='summary').effect_resource_id, task.id)

    def test_changed_patient_epoch_requires_fresh_request(self):
        task, event = self.submit()
        Patient.objects.filter(id=self.patient.id).update(data_epoch=self.patient.data_epoch + 1)
        with self.assertRaisesMessage(SummaryRejected, 'SUMMARY_INPUT_CHANGED'):
            execute_summary(event, 'changed-worker')
        task.refresh_from_db()
        self.assertEqual(task.status, 'failed')
        self.assertFalse(Summary.objects.filter(task_id=task.id).exists())

    def test_restricted_patient_without_epoch_change_cannot_materialize(self):
        task, event = self.submit()
        Patient.objects.filter(id=self.patient.id).update(status='restricted')
        with self.assertRaisesMessage(SummaryRejected, 'SUMMARY_PATIENT_UNAVAILABLE'):
            execute_summary(event, 'restricted-worker')
        task.refresh_from_db()
        self.assertEqual(task.status, 'failed')
        self.assertFalse(Summary.objects.filter(task_id=task.id).exists())

    def test_restricted_patient_is_rejected_at_admission(self):
        Patient.objects.filter(id=self.patient.id).update(status='restricted')
        response = self.client.post(self.target, self.payload, content_type='application/json', **self.headers)
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(response.json()['code'], 'SUMMARY_PATIENT_UNAVAILABLE')
        self.assertFalse(Task.objects.filter(schema_version='summary-task-2').exists())

    def test_deadline_is_enforced_and_error_is_visible(self):
        task, event = self.submit()
        Task.objects.filter(id=task.id).update(deadline_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaisesMessage(SummaryRejected, 'SUMMARY_DEADLINE_EXCEEDED'):
            execute_summary(event, 'expired-worker')
        response = self.client.get(f'/api/tasks/{task.id}')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['error_code'], 'SUMMARY_DEADLINE_EXCEEDED')
        self.assertIsNone(response.json()['poll_after_ms'])

    def test_snapshot_tampering_is_rejected(self):
        task, event = self.submit()
        execute_summary(event, 'synthetic-worker')
        summary = Summary.objects.get(task_id=task.id)
        summary.execution_snapshot['fields'][0]['raw_numeric'] = '99'
        summary.save(update_fields=['execution_snapshot'])
        with self.assertRaisesMessage(SummaryRejected, 'SUMMARY_EXECUTION_SNAPSHOT_INVALID'):
            read_summary(summary.id, self.patient)

    def test_field_scope_change_does_not_expand_frozen_scope(self):
        task, event = self.submit()
        AccessGrant.objects.filter(principal=self.principal, patient=self.patient,
            action='create_summary').update(field_codes=['rash'])
        with self.assertRaisesMessage(SummaryRejected, 'SUMMARY_INPUT_CHANGED'):
            execute_summary(event, 'changed-field-worker')
        self.assertFalse(Summary.objects.filter(task_id=task.id).exists())

    def test_foreign_correlation_cannot_fail_or_execute_task(self):
        task, event = self.submit()
        event.correlation_id = uuid4()
        with self.assertRaisesMessage(SummaryRejected, 'SUMMARY_TASK_INPUT_INVALID'):
            execute_summary(event, 'invalid-event-worker')
        task.refresh_from_db()
        self.assertEqual(task.status, 'accepted')
        self.assertFalse(TaskAttempt.objects.filter(task=task).exists())

    def test_local_render_failure_does_not_leave_partial_summary(self):
        from unittest.mock import patch
        from summaries.service import add_summary_item

        task, event = self.submit()
        def fail_after_insert(**values):
            add_summary_item(**values)
            raise RuntimeError('synthetic failure after first item')
        with patch('summaries.service.add_summary_item', side_effect=fail_after_insert):
            with self.assertRaisesMessage(RuntimeError, 'synthetic failure'):
                execute_summary(event, 'failure-worker')
        task.refresh_from_db()
        self.assertEqual(task.status, 'failed')
        self.assertFalse(Summary.objects.filter(task_id=task.id).exists())

    def test_stale_failure_callback_cannot_fail_reused_owner_new_generation(self):
        from summaries.jobs import record_summary_failure
        from tasks.service import acquire_lease, create_attempt

        task, event = self.submit()
        self.assertEqual(acquire_lease(task.id, 'same-celery-id', timezone.now() + timedelta(seconds=20)), 1)
        task.refresh_from_db()
        old_generation = task.lease_generation
        create_attempt(task, old_generation)
        Task.objects.filter(id=task.id).update(lease_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(acquire_lease(task.id, 'same-celery-id', timezone.now() + timedelta(seconds=20)), 1)
        task.refresh_from_db()
        create_attempt(task, task.lease_generation)
        for generation in (old_generation, None):
            record_summary_failure(event, 'same-celery-id', 'STALE_FAILURE', lease_generation=generation)
            task.refresh_from_db()
            self.assertEqual(task.status, 'running')
            self.assertFalse(ConsumerReceipt.objects.filter(event=event).exists())
        record_summary_failure(event, 'same-celery-id', 'CURRENT_FAILURE', lease_generation=task.lease_generation)
        task.refresh_from_db()
        self.assertEqual(task.status, 'failed')
        self.assertEqual(TaskAttempt.objects.get(task=task, lease_generation=task.lease_generation).error_code, 'CURRENT_FAILURE')

    def test_rehashed_scope_cannot_escape_original_write_request(self):
        from configurations.service import compute_checksum

        task, event = self.submit()
        task.input_refs['scope']['include_unknown_time'] = not task.input_refs['scope']['include_unknown_time']
        task.input_sha256 = compute_checksum(task.input_refs)
        task.save(update_fields=['input_refs', 'input_sha256'])
        with self.assertRaisesMessage(SummaryRejected, 'SUMMARY_TASK_INPUT_INVALID'):
            execute_summary(event, 'changed-scope-worker')
        task.refresh_from_db()
        self.assertEqual(task.status, 'accepted')
        self.assertFalse(Summary.objects.filter(task_id=task.id).exists())
        self.assertFalse(ConsumerReceipt.objects.filter(event=event).exists())


class AsyncSummaryConcurrencyTests(TransactionTestCase):
    """Physical separate PG connections, rather than simulated lock claims."""
    submit = AsyncSummaryTests.submit

    def setUp(self):
        from tests.isolated_rows import IsolatedSchemaRows

        rows = IsolatedSchemaRows()
        self.addCleanup(rows.cleanup)
        AsyncSummaryTests.setUp(self)

    def run_interleaved(self, cancel):
        import threading
        from unittest.mock import patch
        from django.db import connection, connections
        from summaries.jobs import cancel_summary, current_task

        self.assertEqual(connection.vendor, 'postgresql')
        task, event = self.submit()
        claimed, resume = threading.Event(), threading.Event()
        failures = []
        calls = 0
        def intercept(value):
            nonlocal calls
            if threading.current_thread().name == 'summary-owner':
                calls += 1
                if calls == 2:
                    claimed.set()
                    if not resume.wait(10):
                        raise RuntimeError('synthetic concurrency barrier timeout')
            return current_task(value)
        def work():
            try:
                execute_summary(OutboxEvent.objects.get(id=event.id), 'summary-owner')
            except Exception as error:
                failures.append(error)
            finally:
                connections['default'].close()
        with patch('summaries.jobs.current_task', side_effect=intercept):
            worker = threading.Thread(target=work, name='summary-owner')
            worker.start()
            try:
                self.assertTrue(claimed.wait(10), failures)
                if cancel:
                    self.assertEqual(cancel_summary(task_id=task.id, actor=self.principal,
                        auth_epoch=self.principal.auth_epoch, audience='family'), 1)
                else:
                    execute_summary(event, 'duplicate-owner')
            finally:
                resume.set()
                worker.join(15)
            self.assertFalse(worker.is_alive(), 'summary worker did not release its locks')
        self.assertEqual(failures, [])
        task.refresh_from_db()
        self.assertEqual(task.status, 'cancelled' if cancel else 'completed')
        self.assertEqual(Summary.objects.filter(task_id=task.id).count(), 0 if cancel else 1)
        self.assertEqual(TaskAttempt.objects.filter(task=task).count(), 1)
        self.assertEqual(ConsumerReceipt.objects.filter(event=event, consumer='summary').count(), 1)

    def test_cancel_between_claim_and_commit_on_another_connection(self):
        self.run_interleaved(cancel=True)

    def test_duplicate_consumer_cannot_take_live_lease(self):
        self.run_interleaved(cancel=False)
