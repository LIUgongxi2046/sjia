"""Qualified deterministic delta budgets; synthetic software data, no providers.

The lead runs the database classes on an immutable PostgreSQL test snapshot.
Policy-selection tests use SimpleTestCase and do not need a database.
"""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from api.base import ApiError
from archives.models import DeltaSummary, DeltaSummaryItem
from configurations.service import compute_checksum
from dependencies.models import DependencyNode
from requests.models import WriteIntent
from summaries import delta
from tasks.models import OutboxEvent, Task, TaskAttempt
from tests import test_archive_regrouping as archive_fixtures
from tests.synthetic_fixtures import synthetic_publication


class DeltaTaskPolicyTests(SimpleTestCase):
    def test_policy_must_exist_exactly_once_without_implicit_default(self):
        row = {'task_type': 'summary', 'execution_seconds': 17, 'lease_seconds': 11,
            'max_attempts': 1, 'max_concurrency': 2, 'retry_delays_seconds': []}
        for entries in ([], [{'task_type': 'ocr'}], [row, deepcopy(row)]):
            runtime = SimpleNamespace(payload={'tasks': entries})
            with self.subTest(entries=entries):
                with self.assertRaisesRegex(ApiError, 'ARCHIVE_DELTA_TASK_POLICY_UNAVAILABLE'):
                    delta.delta_task_policy(runtime)
                self.assertIsNone(delta.delta_task_policy(runtime, required=False))

    def test_exact_policy_is_detached_from_mutable_config_payload(self):
        row = {'task_type': 'summary', 'execution_seconds': 17, 'lease_seconds': 11,
            'max_attempts': 2, 'max_concurrency': 3, 'retry_delays_seconds': [7]}
        runtime = SimpleNamespace(payload={'tasks': [{'task_type': 'ocr'}, row]})
        result = delta.delta_task_policy(runtime)
        self.assertEqual(result, row)
        row['retry_delays_seconds'].append(99)
        row['execution_seconds'] = 300
        self.assertEqual(result['execution_seconds'], 17)
        self.assertEqual(result['retry_delays_seconds'], [7])


class DeltaBudgetFixture:
    _grant = archive_fixtures.ArchiveRegroupingTests._grant

    def setUp(self):
        archive_fixtures.ArchiveRegroupingTests.setUp(self)
        _, _, configs = synthetic_publication(self.fixture, field_codes=['synthetic_report'],
            purpose='family_observation')
        self.visibility = configs['visibility']
        self.policy = self.set_policy(execution_seconds=17, lease_seconds=11,
            heartbeat_seconds=3, visibility_timeout_seconds=40, max_concurrency=2)
        self.input = {'baseline_batch_id': None, 'baseline_document_version_ids': []}
        self.target = f'/archive-batches/{self.batch.id}/delta-summaries'

    def set_policy(self, **changes):
        self.runtime.payload = deepcopy(self.runtime.payload)
        row = next(row for row in self.runtime.payload['tasks'] if row['task_type'] == 'summary')
        row.update(changes)
        self.runtime.save(update_fields=['payload'])
        self.fixture.qualify(self.runtime)
        return deepcopy(row)

    def scope(self):
        patient, _, context, materials, facts, relations = delta.material_scope(
            self.request, self.actor, self.actor.auth_epoch, self.batch.id, self.input, generating=True)
        return patient, context, materials, facts, relations

    def generate(self, scope=None):
        return delta.generate_delta(self.actor, self.actor.auth_epoch, *(scope or self.scope()))

    def prepare(self):
        identity = str(uuid4())
        response = self.client.post('/api/v2/write-intents', {'operation_id': 'archive_delta_create',
            'target_key': self.target, 'request_id': identity, 'schema_version': '2.0.0',
            'payload': self.input}, content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        value = response.json()
        return value, {'HTTP_X_WRITE_INTENT': value['intent_id'],
            'HTTP_X_INTENT_GENERATION': value['generation'], 'HTTP_IDEMPOTENCY_KEY': identity}

    def running_task(self, schema='summary-task-2', runtime_id=None, **changes):
        now = timezone.now()
        identity = str(runtime_id or self.runtime.id)
        refs = {'context': {'runtime_id': identity}} if schema == 'summary-task-2' else {'runtime': {'id': identity}}
        values = dict(actor=self.actor, patient=self.patient, task_type='summary', schema_version=schema,
            input_refs=refs, input_sha256=compute_checksum(refs), status='running', status_version=2,
            lease_generation=1, lease_owner='synthetic-other-owner', lease_until=now + timedelta(minutes=1),
            created_at=now, deadline_at=now + timedelta(minutes=2))
        values.update(changes)
        return Task.objects.create(**values)

    def assert_no_delta_output(self):
        self.assertFalse(DeltaSummary.objects.filter(patient_id=self.patient.id).exists())
        self.assertFalse(DeltaSummaryItem.objects.filter(patient_id=self.patient.id).exists())
        self.assertFalse(DependencyNode.objects.filter(patient_id=self.patient.id, resource_type='archive_delta').exists())


@override_settings(SJIA_ENVIRONMENT='test')
class ArchiveDeltaBudgetTests(DeltaBudgetFixture, TestCase):
    def test_explicit_execution_and_lease_policy_are_used_and_captured(self):
        captured = {}
        original = delta.build_items
        def observe(*args):
            task = Task.objects.get(schema_version=delta.DELTA_SCHEMA)
            captured.update(created_at=task.created_at, deadline_at=task.deadline_at,
                lease_until=task.lease_until, policy=task.input_refs['task_policy'])
            return original(*args)
        with patch('summaries.delta.build_items', side_effect=observe):
            result, task = self.generate()
        task.refresh_from_db()
        self.assertEqual(task.status, 'completed')
        self.assertEqual(captured['deadline_at'] - captured['created_at'], timedelta(seconds=17))
        self.assertGreaterEqual(captured['lease_until'], captured['created_at'] + timedelta(seconds=11))
        self.assertLess(captured['lease_until'], captured['created_at'] + timedelta(seconds=12))
        self.assertEqual(captured['policy'], self.policy)
        self.assertEqual(task.input_refs['runtime'], delta.entity_reference(self.runtime))
        self.assertEqual(task.input_sha256, compute_checksum(task.input_refs))
        attempt = TaskAttempt.objects.get(task=task)
        self.assertEqual((attempt.attempt_number, attempt.status, attempt.provider_request_id), (1, 'completed', None))
        self.assertEqual(attempt.usage_evidence['item_count'], DeltaSummaryItem.objects.filter(summary_id=result.id).count())
        self.assertFalse(OutboxEvent.objects.filter(task=task).exists())

    def test_missing_summary_policy_rejects_real_qualification_admission(self):
        self.runtime.payload['tasks'] = [row for row in self.runtime.payload['tasks'] if row['task_type'] != 'summary']
        self.runtime.save(update_fields=['payload'])
        self.fixture.qualify(self.runtime)
        with self.assertRaisesRegex(ApiError, 'ARCHIVE_DELTA_TASK_POLICY_UNAVAILABLE'):
            self.scope()
        self.assertFalse(Task.objects.filter(schema_version=delta.DELTA_SCHEMA).exists())
        self.assert_no_delta_output()

    def test_duplicate_policy_is_rejected_by_real_runtime_qualification(self):
        self.runtime.payload['tasks'].append(deepcopy(self.policy))
        self.runtime.checksum = compute_checksum(self.runtime.payload)
        self.runtime.save(update_fields=['payload', 'checksum'])
        with self.assertRaisesRegex(ApiError, 'RUNTIME_CONFIGURATION_PENDING'):
            self.scope()
        self.assert_no_delta_output()

    def test_policy_change_after_scope_requires_fresh_admission(self):
        scope = self.scope()
        self.set_policy(execution_seconds=19)
        with self.assertRaisesRegex(ApiError, 'ARCHIVE_DELTA_PREPARATION_CHANGED'):
            self.generate(scope)
        self.assertFalse(Task.objects.filter(schema_version=delta.DELTA_SCHEMA).exists())
        self.assert_no_delta_output()

    def assert_expiration_rolls_back(self, *, lease, during_insert=False):
        if not lease:
            self.policy = self.set_policy(lease_seconds=29)
        scope = self.scope()
        now = [timezone.now()]
        delay = self.policy['lease_seconds' if lease else 'execution_seconds']
        target = 'summaries.delta.DeltaSummaryItem.objects.create' if during_insert else 'summaries.delta.build_items'
        original = DeltaSummaryItem.objects.create if during_insert else delta.build_items
        def expire(*args, **kwargs):
            value = original(*args, **kwargs)
            now[0] += timedelta(seconds=delay)
            return value
        with patch('summaries.delta.timezone.now', side_effect=lambda: now[0]), patch(target, side_effect=expire):
            with self.assertRaisesRegex(ApiError, 'ARCHIVE_DELTA_TASK_DEADLINE_EXCEEDED'):
                self.generate(scope)
        self.assert_no_delta_output()
        self.assertFalse(Task.objects.filter(schema_version=delta.DELTA_SCHEMA).exists())
        self.assertFalse(TaskAttempt.objects.filter(task__schema_version=delta.DELTA_SCHEMA).exists())

    def test_execution_budget_expiration_before_output_rolls_back(self):
        self.assert_expiration_rolls_back(lease=False)

    def test_lease_budget_expiration_before_output_rolls_back(self):
        self.assert_expiration_rolls_back(lease=True)

    def test_execution_expiration_after_partial_insert_rolls_back(self):
        self.assert_expiration_rolls_back(lease=False, during_insert=True)

    def test_lease_expiration_after_partial_insert_rolls_back(self):
        self.assert_expiration_rolls_back(lease=True, during_insert=True)

    def test_stale_generation_cannot_finish_or_publish_output(self):
        original = delta.build_items
        def change_generation(*args):
            values = original(*args)
            Task.objects.filter(schema_version=delta.DELTA_SCHEMA).update(lease_generation=2)
            return values
        with patch('summaries.delta.build_items', side_effect=change_generation):
            with self.assertRaisesRegex(ApiError, 'ARCHIVE_DELTA_TASK_RESULT_REJECTED'):
                self.generate()
        self.assert_no_delta_output()
        self.assertFalse(Task.objects.filter(schema_version=delta.DELTA_SCHEMA).exists())

    def test_exhausted_attempt_budget_cannot_take_over_or_build(self):
        # Fault injection returns an already-attempted local task from registration.
        # Real lease/attempt services establish the exhausted state; no authority or
        # qualification evaluator is patched.
        from tasks.service import acquire_lease, create_attempt, finish_attempt
        original = delta.register_task
        def exhausted_registration(**kwargs):
            task = original(**kwargs)
            acquire_lease(task.id, 'synthetic-previous-owner', timezone.now() + timedelta(seconds=5))
            task.refresh_from_db()
            attempt = create_attempt(task, task.lease_generation)
            finish_attempt(attempt, 'failed', error_code='SYNTHETIC_LOCAL_FAILURE')
            Task.objects.filter(id=task.id).update(lease_until=timezone.now())
            return task
        with patch('summaries.delta.register_task', side_effect=exhausted_registration), \
                patch('summaries.delta.acquire_lease') as acquire, patch('summaries.delta.build_items') as build:
            with self.assertRaisesRegex(ApiError, 'ARCHIVE_DELTA_TASK_ATTEMPT_LIMIT_EXCEEDED'):
                self.generate()
        acquire.assert_not_called()
        build.assert_not_called()
        self.assert_no_delta_output()
        self.assertFalse(Task.objects.filter(schema_version=delta.DELTA_SCHEMA).exists())

    def test_both_current_runtime_summary_families_consume_capacity_without_mutation(self):
        first = self.running_task()
        second = self.running_task(delta.DELTA_SCHEMA)
        before = list(Task.objects.filter(id__in=[first.id, second.id]).order_by('id').values())
        with patch('summaries.delta.build_items') as build:
            with self.assertRaisesRegex(ApiError, 'ARCHIVE_DELTA_TASK_CONCURRENCY_EXCEEDED'):
                self.generate()
        build.assert_not_called()
        self.assertEqual(before, list(Task.objects.filter(id__in=[first.id, second.id]).order_by('id').values()))
        self.assertEqual(Task.objects.count(), 2)
        self.assert_no_delta_output()

    def test_queued_expired_terminal_and_other_runtime_tasks_do_not_consume_capacity(self):
        self.policy = self.set_policy(max_concurrency=1)
        now = timezone.now()
        self.running_task(status='queued')
        self.running_task(status='completed')
        self.running_task(lease_until=now)
        self.running_task(deadline_at=now)
        self.running_task(runtime_id=uuid4())
        before = list(Task.objects.order_by('id').values())
        _, task = self.generate()
        self.assertEqual(before, list(Task.objects.exclude(id=task.id).order_by('id').values()))
        task.refresh_from_db()
        self.assertEqual(task.status, 'completed')

    def test_http_create_read_task_receipt_and_idempotent_replay_stay_native(self):
        intent, headers = self.prepare()
        response = self.client.post('/api/v2' + self.target, self.input, content_type='application/json', **headers)
        self.assertEqual(response.status_code, 202, response.content)
        receipt = response.json()
        self.assertEqual(receipt['resource_type'], 'archive_delta')
        for target in (f"/api/v2/archive-delta-summaries/{receipt['resource_id']}",
                f"/api/v2/archive-delta-tasks/{receipt['task_id']}"):
            read = self.client.get(target)
            self.assertEqual(read.status_code, 200, read.content)
            self.assertEqual(read.json()['status'], 'completed')
        replay = self.client.post('/api/v2' + self.target, self.input, content_type='application/json', **headers)
        self.assertEqual(replay.status_code, 202, replay.content)
        self.assertEqual(replay.json(), receipt)
        self.assertEqual(TaskAttempt.objects.filter(task_id=receipt['task_id']).count(), 1)
        self.assertEqual(WriteIntent.objects.get(id=intent['intent_id']).status, 'committed')
        self.assertEqual(DeltaSummary.objects.filter(patient_id=self.patient.id).count(), 1)

    def test_old_snapshot_without_policy_is_invalidated_without_fabricated_budget(self):
        result, task = self.generate()
        task.input_refs.pop('task_policy')
        task.input_sha256 = compute_checksum(task.input_refs)
        task.save(update_fields=['input_refs', 'input_sha256'])
        view = delta.read_delta(self.request, self.actor, self.actor.auth_epoch, result)
        self.assertEqual((view['status'], view['validity'], view['items']), ('superseded', 'invalid', []))
        task.refresh_from_db()
        self.assertNotIn('task_policy', task.input_refs)
        self.assertEqual(task.status, 'superseded')

    def test_stale_prefetched_validity_cannot_reveal_invalidated_items(self):
        result, _ = self.generate()
        DeltaSummary.objects.filter(id=result.id).update(validity='invalid')
        self.assertEqual(result.validity, 'valid')
        view = delta.read_delta(self.request, self.actor, self.actor.auth_epoch, result)
        self.assertEqual((view['validity'], view['items']), ('invalid', []))

    def test_stale_prefetched_owner_cannot_read_reassigned_result(self):
        result, _ = self.generate()
        DeltaSummary.objects.filter(id=result.id).update(owner_id=self.other.id)
        self.assertEqual(result.owner_id, self.actor.id)
        with self.assertRaisesRegex(ApiError, 'ARCHIVE_DELTA_SCOPE_DENIED'):
            delta.read_delta(self.request, self.actor, self.actor.auth_epoch, result)

    def test_prefetched_patient_task_and_owner_are_not_authoritative(self):
        result, task = self.generate()
        result.patient_id = uuid4()
        result.owner_id = self.other.id
        result.task_id = uuid4()
        view = delta.read_delta(self.request, self.actor, self.actor.auth_epoch, result)
        self.assertEqual((view['patient_id'], view['task_id']), (str(self.patient.id), str(task.id)))
        self.assertEqual((view['status'], view['validity']), ('completed', 'valid'))

    def test_current_policy_change_invalidates_completed_snapshot(self):
        result, _ = self.generate()
        self.set_policy(max_concurrency=3)
        view = delta.read_delta(self.request, self.actor, self.actor.auth_epoch, result)
        self.assertEqual((view['validity'], view['items']), ('invalid', []))


@override_settings(SJIA_ENVIRONMENT='test')
class ArchiveDeltaBudgetConcurrencyTests(DeltaBudgetFixture, TransactionTestCase):
    """The real gate lock serializes delta admission with an async summary claim."""
    def setUp(self):
        from tests.isolated_rows import IsolatedSchemaRows
        rows = IsolatedSchemaRows(require_empty=True)
        self.addCleanup(rows.cleanup)
        # Reused archive fixtures create the record and its current version as a
        # deferred-FK pair. Commit that pair together before opening competitors.
        with transaction.atomic():
            super().setUp()

    def test_other_connection_committed_claim_is_counted_after_gate_wait(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from time import monotonic, sleep
        from django.db import connection, connections
        from summaries.jobs import lock_summary_scope

        self.assertEqual(connection.vendor, 'postgresql')
        self.policy = self.set_policy(max_concurrency=1)
        with transaction.atomic():
            scope = self.scope()
        started = Event()
        worker_pid = []
        def generate_waiting_delta():
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    worker_pid.append(cursor.fetchone()[0])
                started.set()
                try:
                    self.generate(scope)
                    return 'unexpected_success'
                except ApiError as error:
                    return error.code
            finally:
                connections['default'].close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                lock_summary_scope(self.actor.id, self.patient.id, 'test')
                future = pool.submit(generate_waiting_delta)
                self.assertTrue(started.wait(10))
                limit, blocked = monotonic() + 10, False
                while monotonic() < limit:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT cardinality(pg_blocking_pids(%s))', [worker_pid[0]])
                        blocked = cursor.fetchone()[0] > 0
                    if blocked:
                        break
                    sleep(0.01)
                self.assertTrue(blocked, 'Delta did not wait on the shared summary gate lock')
                claimed = self.running_task()
            self.assertEqual(future.result(timeout=15), 'ARCHIVE_DELTA_TASK_CONCURRENCY_EXCEEDED')
        self.assert_no_delta_output()
        claimed.refresh_from_db()
        self.assertEqual((claimed.status, claimed.lease_generation, claimed.lease_owner),
            ('running', 1, 'synthetic-other-owner'))
        self.assertFalse(Task.objects.filter(schema_version=delta.DELTA_SCHEMA).exists())

    def test_http_read_waits_on_gate_before_result_lock_and_observes_invalidation(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from time import monotonic, sleep
        from django.db import connection, connections
        from summaries.jobs import lock_summary_scope

        with transaction.atomic():
            result, _ = self.generate()
        started = Event()
        worker_pid = []
        def read_waiting_delta():
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    worker_pid.append(cursor.fetchone()[0])
                started.set()
                response = self.client.get(f'/api/v2/archive-delta-summaries/{result.id}')
                return response.status_code, response.json()
            finally:
                connections['default'].close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                lock_summary_scope(self.actor.id, self.patient.id, 'test')
                future = pool.submit(read_waiting_delta)
                self.assertTrue(started.wait(10))
                limit, blocked = monotonic() + 10, False
                while monotonic() < limit:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT cardinality(pg_blocking_pids(%s))', [worker_pid[0]])
                        blocked = cursor.fetchone()[0] > 0
                    if blocked:
                        break
                    sleep(0.01)
                self.assertTrue(blocked, 'Read did not wait for the shared summary gate lock')
                # The old HTTP route held this row while waiting on the gate.
                current = DeltaSummary.objects.select_for_update(nowait=True).get(id=result.id)
                current.validity = 'invalid'
                current.save(update_fields=['validity'])
            status, body = future.result(timeout=15)
        self.assertEqual(status, 200, body)
        self.assertEqual((body['status'], body['validity'], body['items']), ('superseded', 'invalid', []))
