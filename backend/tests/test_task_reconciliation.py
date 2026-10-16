"""Synthetic, local-only T06 reconciliation regression tests."""
from datetime import timedelta
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from authorization.models import AccessGrant, ServiceRoleGrant
from authorization.policy import Denied
from configurations.service import compute_checksum
from tasks.models import ConsumerReceipt, OutboxEvent, Task, TaskAttempt
from tasks.reconciliation import execute_reconcile
from tasks.service import TaskRejected, acquire_lease, create_attempt
from tests import test_summary_async_closeout as summary_tests


class TaskReconciliationTests(TestCase):
    def setUp(self):
        summary_tests.AsyncSummaryTests.setUp(self)
        gate = self.synthetic.gate
        self.target_task, self.summary_event = summary_tests.AsyncSummaryTests.submit(self)
        self.service_grant = ServiceRoleGrant.objects.create(principal=self.principal, environment=gate,
            action='reconcile_task', granted_by=self.principal, evidence_ref='synthetic-reconcile-only',
            valid_from=timezone.now())
        self.case_grant = AccessGrant.objects.create(principal=self.principal, patient=self.patient,
            action='read_case', field_codes=[], record_scope='all', purpose='clinical',
            granted_by=self.principal, evidence_ref='synthetic-case-only', valid_from=timezone.now())
        session = self.client.session
        session['_sjia_audience'] = 'support'
        session.save()

    def reconcile(self, target=None):
        target = target or self.target_task
        target.refresh_from_db()
        path = f'/api/support/tasks/{target.id}/reconcile'
        payload = {'expected_status_version': str(target.status_version),
            'expected_lease_generation': str(target.lease_generation)}
        request_id = str(uuid4())
        prepared = self.client.post('/api/write-intents', {'operation_id': 'task_reconcile',
            'target_key': path, 'request_id': request_id, 'schema_version': '1.0.0', 'payload': payload},
            content_type='application/json')
        self.assertEqual(prepared.status_code, 201, prepared.content)
        intent = prepared.json()
        headers = {'HTTP_X_WRITE_INTENT': intent['intent_id'], 'HTTP_X_INTENT_GENERATION': intent['generation'],
            'HTTP_IDEMPOTENCY_KEY': request_id}
        response = self.client.post(path, payload, content_type='application/json', **headers)
        self.assertEqual(response.status_code, 202, response.content)
        repeated = self.client.post(path, payload, content_type='application/json', **headers)
        self.assertEqual(repeated.status_code, 202, repeated.content)
        self.assertEqual(repeated.json()['task_id'], response.json()['task_id'])
        task = Task.objects.get(id=response.json()['task_id'])
        return task, OutboxEvent.objects.get(task=task)

    def expire_summary_lease(self):
        now = timezone.now()
        self.assertEqual(acquire_lease(self.target_task.id, 'interrupted-local-worker', now + timedelta(seconds=20)), 1)
        self.target_task.refresh_from_db()
        create_attempt(self.target_task, self.target_task.lease_generation)
        Task.objects.filter(id=self.target_task.id).update(lease_until=now - timedelta(seconds=1))
        OutboxEvent.objects.filter(id=self.summary_event.id).update(published_at=now)
        self.target_task.refresh_from_db()

    def test_202_report_and_duplicate_delivery_without_external_effect(self):
        task, event = self.reconcile()
        self.assertEqual(task.status, 'accepted')
        execute_reconcile(event, 'local-metadata-worker')
        execute_reconcile(event, 'duplicate')
        task.refresh_from_db()
        self.target_task.refresh_from_db()
        self.assertEqual(task.status, 'completed')
        self.assertEqual(self.target_task.status, 'accepted')
        self.assertEqual(TaskAttempt.objects.filter(task=task).count(), 1)
        self.assertEqual(ConsumerReceipt.objects.filter(event=event).count(), 1)
        response = self.client.get(f'/api/support/reconciliation-tasks/{task.id}')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['report']['outcome'], 'domain_review_required')
        self.assertFalse(response.json()['report']['changed'])

    def test_expired_uncommitted_local_summary_is_fenced_failed_without_republish(self):
        self.expire_summary_lease()
        before = self.target_task.input_refs
        task, event = self.reconcile()
        execute_reconcile(event, 'local-metadata-worker')
        self.target_task.refresh_from_db()
        self.summary_event.refresh_from_db()
        self.assertEqual(self.target_task.status, 'failed')
        self.assertEqual(self.target_task.input_refs, before)
        self.assertIsNotNone(self.summary_event.published_at)
        self.assertEqual(OutboxEvent.objects.filter(task=self.target_task).count(), 1)
        self.assertEqual(TaskAttempt.objects.get(task=self.target_task).status, 'failed')
        report = TaskAttempt.objects.get(task=task).usage_evidence['reconciliation_report']
        self.assertEqual(report['outcome'], 'local_summary_lease_expired')
        from summaries.jobs import execute_summary
        from summaries.models import Summary
        execute_summary(self.summary_event, 'resumed-local-worker')
        self.target_task.refresh_from_db()
        self.assertEqual(self.target_task.status, 'failed')
        self.assertEqual(Summary.objects.filter(task_id=self.target_task.id).count(), 0)

    def test_unknown_provider_outcome_is_never_replayed(self):
        self.expire_summary_lease()
        TaskAttempt.objects.filter(task=self.target_task).update(provider_request_id='synthetic-unknown-outcome')
        task, event = self.reconcile()
        execute_reconcile(event, 'metadata-worker')
        self.target_task.refresh_from_db()
        self.summary_event.refresh_from_db()
        self.assertEqual(self.target_task.status, 'running')
        self.assertIsNotNone(self.summary_event.published_at)
        report = TaskAttempt.objects.get(task=task).usage_evidence['reconciliation_report']
        self.assertEqual(report['outcome'], 'domain_review_required')
        self.assertFalse(report['changed'])

    def test_existing_consumer_receipt_prevents_requeue(self):
        self.expire_summary_lease()
        ConsumerReceipt.objects.create(consumer='summary', event=self.summary_event,
            effect_resource_id=self.target_task.id, processed_at=timezone.now())
        task, event = self.reconcile()
        execute_reconcile(event, 'metadata-worker')
        self.target_task.refresh_from_db()
        self.assertEqual(self.target_task.status, 'running')
        self.assertFalse(TaskAttempt.objects.get(task=task).usage_evidence['reconciliation_report']['changed'])

    def test_current_service_revocation_fails_without_target_changes(self):
        task, event = self.reconcile()
        self.service_grant.revoked_at = timezone.now()
        self.service_grant.save(update_fields=['revoked_at'])
        with self.assertRaises(Denied):
            execute_reconcile(event, 'revoked-worker')
        task.refresh_from_db()
        self.target_task.refresh_from_db()
        self.assertEqual(task.status, 'failed')
        self.assertEqual(self.target_task.status, 'accepted')
        self.assertEqual(TaskAttempt.objects.get(task=task).error_code, 'SERVICE_ACTION_DENIED')

    def test_event_tampering_is_rejected_without_failure_receipt(self):
        task, event = self.reconcile()
        event.correlation_id = uuid4()
        with self.assertRaises(TaskRejected):
            execute_reconcile(event, 'tampered-event')
        task.refresh_from_db()
        self.assertEqual(task.status, 'accepted')
        self.assertFalse(ConsumerReceipt.objects.filter(event=event).exists())

    def test_changed_target_is_reported_and_cancelled_reconcile_has_no_effect(self):
        self.expire_summary_lease()
        task, event = self.reconcile()
        Task.objects.filter(id=self.target_task.id).update(status='cancelled', status_version=99)
        execute_reconcile(event, 'metadata-worker')
        report = TaskAttempt.objects.get(task=task).usage_evidence['reconciliation_report']
        self.assertEqual(report['reason'], 'RECONCILE_TARGET_CHANGED')
        second, second_event = self.reconcile()
        from tasks.service import cancel_task
        cancel_task(second.id)
        execute_reconcile(second_event, 'cancelled-worker')
        second.refresh_from_db()
        self.assertEqual(second.status, 'cancelled')
        self.assertFalse(TaskAttempt.objects.filter(task=second).exists())

    def test_recursive_target_is_rejected_and_case_scope_required(self):
        task, _ = self.reconcile()
        response = self.client.post('/api/write-intents', {'operation_id': 'task_reconcile',
            'target_key': f'/api/support/tasks/{task.id}/reconcile', 'request_id': str(uuid4()),
            'schema_version': '1.0.0', 'payload': {'expected_status_version': '1', 'expected_lease_generation': '0'}},
            content_type='application/json')
        self.assertNotEqual(response.status_code, 201)
        self.case_grant.revoked_at = timezone.now()
        self.case_grant.save(update_fields=['revoked_at'])
        response = self.client.get(f'/api/support/reconciliation-tasks/{task.id}')
        self.assertNotEqual(response.status_code, 200)

    def test_immutable_runtime_and_input_tampering_fail_closed(self):
        task, event = self.reconcile()
        task.input_refs['context']['runtime_checksum'] = '0' * 64
        task.input_sha256 = compute_checksum(task.input_refs)
        task.save(update_fields=['input_refs', 'input_sha256'])
        with self.assertRaises(TaskRejected):
            execute_reconcile(event, 'changed-runtime')
        task.refresh_from_db()
        self.assertEqual(task.status, 'failed')
        self.target_task.refresh_from_db()
        self.assertEqual(self.target_task.status, 'accepted')

    def test_request_recovery_uses_support_authority_and_exact_request(self):
        task, event = self.reconcile()
        from requests.models import WriteIntent
        intent = WriteIntent.objects.get(write_request=task.request_row)
        response = self.client.get(f'/api/requests/{intent.id}')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['task']['task_id'], str(task.id))
        response = self.client.get(f'/api/requests/{intent.request_id}', {'lookup': 'request_id',
            'operation_id': 'task_reconcile', 'target_key': intent.target_key})
        self.assertEqual(response.status_code, 200, response.content)
        self.service_grant.revoked_at = timezone.now()
        self.service_grant.save(update_fields=['revoked_at'])
        response = self.client.get(f'/api/requests/{intent.id}')
        self.assertNotEqual(response.status_code, 200)

    def test_expired_reconcile_budget_has_no_target_effect(self):
        self.expire_summary_lease()
        task, event = self.reconcile()
        Task.objects.filter(id=task.id).update(deadline_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(TaskRejected):
            execute_reconcile(event, 'expired-budget-worker')
        task.refresh_from_db()
        self.target_task.refresh_from_db()
        self.assertEqual(task.status, 'failed')
        self.assertEqual(self.target_task.status, 'running')
        self.assertEqual(TaskAttempt.objects.get(task=task).error_code, 'RECONCILE_DEADLINE_EXCEEDED')

    def test_expired_owned_reconcile_lease_is_closed_safely(self):
        from tasks.reconciliation import record_reconcile_failure
        task, event = self.reconcile()
        self.assertEqual(acquire_lease(task.id, 'expired-owner', timezone.now() + timedelta(seconds=20)), 1)
        task.refresh_from_db()
        create_attempt(task, task.lease_generation)
        Task.objects.filter(id=task.id).update(lease_until=timezone.now() - timedelta(seconds=1))
        record_reconcile_failure(event, 'wrong-owner', 'RECONCILE_DEADLINE_EXCEEDED')
        task.refresh_from_db()
        self.assertEqual(task.status, 'running')
        record_reconcile_failure(event, 'expired-owner', 'RECONCILE_DEADLINE_EXCEEDED',
            expected_generation=task.lease_generation)
        task.refresh_from_db()
        self.assertEqual(task.status, 'failed')
        self.assertEqual(TaskAttempt.objects.get(task=task).status, 'failed')
        self.assertEqual(ConsumerReceipt.objects.filter(event=event).count(), 1)

    def test_session_epoch_change_revokes_worker(self):
        task, event = self.reconcile()
        self.principal.auth_epoch += 1
        self.principal.save(update_fields=['auth_epoch'])
        with self.assertRaises(Denied):
            execute_reconcile(event, 'stale-session-worker')
        self.target_task.refresh_from_db()
        self.assertEqual(self.target_task.status, 'accepted')

    def test_case_grant_field_scope_is_not_whole_case_permission(self):
        self.case_grant.field_codes = ['temp']
        self.case_grant.save(update_fields=['field_codes'])
        target = self.target_task
        response = self.client.post('/api/write-intents', {'operation_id': 'task_reconcile',
            'target_key': f'/api/support/tasks/{target.id}/reconcile', 'request_id': str(uuid4()),
            'schema_version': '1.0.0', 'payload': {'expected_status_version': str(target.status_version),
                'expected_lease_generation': str(target.lease_generation)}}, content_type='application/json')
        self.assertNotEqual(response.status_code, 201)
        self.assertFalse(Task.objects.filter(task_type='reconcile').exists())

    def test_stale_or_generic_failure_callback_cannot_close_newer_generation(self):
        from tasks.reconciliation import record_reconcile_failure
        task, event = self.reconcile()
        self.assertEqual(acquire_lease(task.id, 'reused-owner', timezone.now() + timedelta(seconds=20)), 1)
        task.refresh_from_db()
        create_attempt(task, task.lease_generation)
        Task.objects.filter(id=task.id).update(lease_until=timezone.now() - timedelta(seconds=1))
        record_reconcile_failure(event, 'reused-owner', 'STALE_FAILURE', expected_generation=task.lease_generation - 1)
        record_reconcile_failure(event, 'reused-owner', 'GENERIC_FAILURE')
        task.refresh_from_db()
        self.assertEqual(task.status, 'running')
        self.assertEqual(TaskAttempt.objects.get(task=task).status, 'running')
        self.assertFalse(ConsumerReceipt.objects.filter(event=event).exists())
