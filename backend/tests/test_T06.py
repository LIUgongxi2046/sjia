"""T06 任务、Outbox 与租约逻辑验证。

运行：backend/.venv/bin/python backend/manage.py test tests.test_T06 --keepdb
"""
import uuid
from datetime import timedelta

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from identity.models import Patient, Principal
from tasks.models import OutboxEvent, Task
from tasks.outbox import claim_events, emit_event, mark_published, record_receipt
from tasks.service import TaskRejected, acquire_lease, cancel_task, complete_task, enqueue, fail_task, register_task


class TaskServiceTest(TestCase):
    def setUp(self):
        now = timezone.now()
        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-T06", status="active", created_at=now)

    def _task(self, task_type="ocr"):
        return register_task(actor=self.principal, task_type=task_type, schema_version="task-1", input_refs={"file_group_id": str(uuid.uuid4())}, patient=self.patient)

    def test_register_task_accepted(self):
        task = self._task()
        self.assertEqual(task.status, "accepted")
        self.assertEqual(task.status_version, 1)

    def test_file_validation_requires_frozen_qualified_policy(self):
        with self.assertRaisesRegex(TaskRejected, 'FILE_VALIDATION_POLICY_REQUIRED'):
            self._task(task_type="file_validation")

    def test_enqueue_only_affects_accepted(self):
        task = self._task()
        lease_until = timezone.now() + timedelta(seconds=45)
        self.assertEqual(acquire_lease(task.id, "w1", lease_until), 1)
        # 已被消费（running），enqueue 不得覆盖
        self.assertEqual(enqueue(task.id), 0)
        task.refresh_from_db()
        self.assertEqual(task.status, "running")

    def test_acquire_lease_increments_generation(self):
        task = self._task()
        lease_until = timezone.now() + timedelta(seconds=45)
        self.assertEqual(acquire_lease(task.id, "w1", lease_until), 1)
        task.refresh_from_db()
        self.assertEqual(task.status, "running")
        self.assertEqual(task.lease_generation, 1)
        self.assertEqual(task.lease_owner, "w1")

    def test_duplicate_acquire_rejected_before_lease_expiry(self):
        task = self._task()
        lease_until = timezone.now() + timedelta(seconds=45)
        acquire_lease(task.id, "w1", lease_until)
        self.assertEqual(acquire_lease(task.id, "w2", lease_until), 0)

    def test_old_lease_completion_rejected(self):
        task = self._task()
        lease_until = timezone.now() + timedelta(seconds=45)
        acquire_lease(task.id, "w1", lease_until)
        # 旧代次提交影响行数必须为零
        self.assertEqual(complete_task(task.id, lease_generation=0, result_type="assessment", result_id=uuid.uuid4()), 0)

    def test_complete_with_current_generation(self):
        task = self._task()
        lease_until = timezone.now() + timedelta(seconds=45)
        acquire_lease(task.id, "w1", lease_until)
        self.assertEqual(complete_task(task.id, lease_generation=1, result_type="assessment", result_id=uuid.uuid4()), 1)
        task.refresh_from_db()
        self.assertEqual(task.status, "completed")

    def test_fail_task(self):
        task = self._task()
        lease_until = timezone.now() + timedelta(seconds=45)
        acquire_lease(task.id, "w1", lease_until)
        self.assertEqual(fail_task(task.id, lease_generation=1, error_code="PROVIDER_ERROR"), 1)
        task.refresh_from_db()
        self.assertEqual(task.status, "failed")

    def test_cancel_task(self):
        task = self._task()
        self.assertEqual(cancel_task(task.id), 1)
        task.refresh_from_db()
        self.assertEqual(task.status, "cancelled")


class OutboxTest(TestCase):
    def test_claim_and_publish(self):
        event = emit_event(event_type="task_queued", schema_version="task-1", aggregate_id=uuid.uuid4(), aggregate_version=1, correlation_id=uuid.uuid4(), resource_refs={})
        claimed = claim_events(limit=10, owner="dispatcher", lease_duration_seconds=30)
        self.assertEqual([e.id for e in claimed], [event.id])
        event.refresh_from_db()
        self.assertEqual(event.lease_owner, "dispatcher")
        self.assertEqual(event.lease_generation, 1)
        self.assertEqual(mark_published(event.id, lease_generation=1), 1)
        event.refresh_from_db()
        self.assertIsNotNone(event.published_at)

    def test_claim_skips_published(self):
        event = emit_event(event_type="task_queued", schema_version="task-1", aggregate_id=uuid.uuid4(), aggregate_version=1, correlation_id=uuid.uuid4(), resource_refs={})
        mark_published(event.id, lease_generation=0)
        self.assertEqual(claim_events(limit=10, owner="dispatcher", lease_duration_seconds=30), [])

    def test_consumer_receipt_dedup(self):
        event = emit_event(event_type="task_queued", schema_version="task-1", aggregate_id=uuid.uuid4(), aggregate_version=1, correlation_id=uuid.uuid4(), resource_refs={})
        record_receipt("ocr_worker", event, uuid.uuid4())
        with self.assertRaises(IntegrityError):
            record_receipt("ocr_worker", event, uuid.uuid4())
