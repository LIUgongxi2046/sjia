"""Synthetic PostgreSQL races for batch/page lock order and stale workers."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from archives.models import Archive, ArchiveBatch, ArchivePage, ArchivePageAttempt
from archives.page_execution import lock_receipt_attempts, record_page_failure
from configurations.models import ConfigVersion
from family_content.models import EnvironmentGate
from files.models import FileGroup, FileObject, FilePage
from identity.models import Patient, Principal
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from sources.service import register_source, register_revision
from tasks.models import ConsumerReceipt, Task, TaskAttempt
from tasks.outbox import emit_event


class ArchiveFailureLockRegressionTest(TransactionTestCase):
    def setUp(self):
        now = timezone.now()
        self.extra_cleanup = []
        key = str(uuid4())
        self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id=f'SIM-REPAIR-LOCK-{key}', status='active', created_at=now)
        retention = RetentionPolicy.objects.create(policy_key=f'sim-lock-{key}', version=1, data_class='report',
            rules={}, schema_version='retention-1', status='active')
        source = register_source(source_key=f'sim-lock-{key}', owner='simulation', location='local',
            purpose='test', sensitivity='synthetic', update_frequency='test')
        revision = register_revision(source=source, version='1', checksum='c' * 64,
            license='synthetic-test-only', allowed_use=['family_observation'])
        authorization = DataUseAuthorization.objects.create(patient=self.patient, source_revision=revision,
            purpose='family_observation', environment='test', evidence_ref='synthetic-test-fixture',
            approved_by=self.actor, approved_at=now)
        config = ConfigVersion.objects.create(config_key=f'sim-lock-{key}', kind='content', version=1,
            schema_version='test-1', payload={}, checksum='a' * 64, status='draft', created_by=self.actor, created_at=now)
        group = FileGroup.objects.create(patient=self.patient, owner=self.actor, version=1, status='available',
            source_revision=revision, use_authorization_id=authorization.id, created_at=now)
        original = FileObject.objects.create(patient=self.patient, file_group_id=group.id,
            object_key=f'synthetic-no-real-file-{key}', object_version='1', checksum='b' * 64, size_bytes=1,
            media_type='image/png', purpose='original', status='available', retention_policy=retention, created_at=now)
        original_page = FilePage.objects.create(patient=self.patient, file_group_id=group.id, file_id=original.id,
            ordinal=1, width=1, height=1, rotation=0)
        archive = Archive.objects.create(patient_id=self.patient.id, created_at=now)
        self.batch = ArchiveBatch.objects.create(patient_id=self.patient.id, archive_id=archive.id,
            submitted_by=self.actor.id, logical_request_id=uuid4(), version=1, received_at=now,
            quota_policy_id=config.id, budget_status='reserved', status='processing')
        self.task = Task.objects.create(actor=self.actor, patient=self.patient, task_type='ocr',
            schema_version='archive-page-1', input_refs={}, input_sha256='d' * 64, status='running',
            status_version=1, lease_generation=1, lease_owner='repair-worker',
            lease_until=now + timedelta(minutes=2), created_at=now, deadline_at=now + timedelta(minutes=5))
        self.page = ArchivePage.objects.create(patient_id=self.patient.id, batch_id=self.batch.id,
            file_page_id=original_page.id, file_id=original.id, file_object_version='1', checksum='b' * 64,
            version=1, status='processing', status_version=1, current_task_id=self.task.id,
            available_at=now, ordinal=1)
        self.task.input_refs = {'archive_page_id': str(self.page.id), 'batch_id': str(self.batch.id)}
        self.task.save(update_fields=['input_refs'])
        self.attempt = TaskAttempt.objects.create(task=self.task, attempt_number=1, lease_generation=1,
            started_at=now, status='running', usage_evidence={'phase': 'pre_provider'})
        self.page_attempt = ArchivePageAttempt.objects.create(patient_id=self.patient.id, page_id=self.page.id,
            page_version=1, task_id=self.task.id, task_attempt_id=self.attempt.id, attempt_number=1,
            input_sha256='d' * 64, model_version='synthetic-model', started_at=now, status='running')
        self.event = emit_event(event_type='archive_page.requested', schema_version='archive-page-1',
            aggregate_id=self.page.id, aggregate_version=1, correlation_id=self.task.id,
            resource_refs={'simulation': True}, task=self.task)
        self.original = original
        self.cleanup_rows = [self.event, self.page_attempt, self.attempt, self.page, self.task,
            self.batch, archive, original_page, original, group, authorization, self.patient,
            config, revision, source, retention, self.actor]

    def tearDown(self):
        # Django flush does not enumerate this project's schema-qualified tables.
        # Delete only this synthetic fixture, in FK order; never truncate shared data.
        ConsumerReceipt.objects.filter(event_id=self.event.id).delete()
        FileObject.objects.filter(patient_id=self.patient.id, purpose='ocr_response').delete()
        for row in self.cleanup_rows:
            type(row).objects.filter(pk=row.pk).delete()
        for row in self.extra_cleanup:
            type(row).objects.filter(pk=row.pk).delete()
        super().tearDown()

    def test_cancel_and_failure_serialize_batch_then_page_without_deadlock(self):
        self.assertEqual(connection.vendor, 'postgresql')
        worker_ready = threading.Event()
        worker_pid = []

        def fail_in_second_connection():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    worker_pid.append(cursor.fetchone()[0])
                worker_ready.set()
                record_page_failure(self.event, 'repair-worker', 'SIMULATED_TIMEOUT')
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                ArchiveBatch.objects.select_for_update().get(id=self.batch.id)
                future = pool.submit(fail_in_second_connection)
                self.assertTrue(worker_ready.wait(5))
                # Wait until PostgreSQL confirms the failure worker is blocked by
                # this batch lock. Old page->batch code held the page at this point.
                deadline, blocked = time.monotonic() + 5, False
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT cardinality(pg_blocking_pids(%s))', [worker_pid[0]])
                        blocked = cursor.fetchone()[0] > 0
                    if blocked:
                        break
                    time.sleep(0.01)
                self.assertTrue(blocked, 'Failure worker did not contend on the batch lock')
                page = ArchivePage.objects.select_for_update().get(id=self.page.id)
                task = Task.objects.select_for_update().get(id=self.task.id)
                page.status, task.status = 'cancel_requested', 'cancel_requested'
                page.save(update_fields=['status'])
                task.save(update_fields=['status'])
            future.result(timeout=10)
        self.page.refresh_from_db()
        self.task.refresh_from_db()
        self.attempt.refresh_from_db()
        self.assertEqual((self.page.status, self.task.status, self.attempt.status), ('cancelled',) * 3)
        self.assertFalse(self.page_attempt.candidate_set_id)

    def test_late_failure_does_not_overwrite_terminal_cancellation(self):
        Task.objects.filter(id=self.task.id).update(status='cancelled')
        ArchivePage.objects.filter(id=self.page.id).update(status='cancelled')
        record_page_failure(self.event, 'repair-worker', 'LATE_FAILURE')
        self.page.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual((self.page.status, self.task.status), ('cancelled', 'cancelled'))

    def test_stale_task_does_not_overwrite_newer_page_retry(self):
        ArchivePage.objects.filter(id=self.page.id).update(current_task_id=None, status='uploaded')
        record_page_failure(self.event, 'repair-worker', 'STALE_FAILURE')
        self.page.refresh_from_db()
        self.assertEqual(self.page.status, 'uploaded')
        self.assertIsNone(self.page.current_task_id)

    def test_cancel_with_unknown_provider_outcome_retains_uncertainty(self):
        Task.objects.filter(id=self.task.id).update(status='cancel_requested')
        ArchivePage.objects.filter(id=self.page.id).update(status='cancel_requested')
        TaskAttempt.objects.filter(id=self.attempt.id).update(usage_evidence={'phase': 'provider_started'})
        record_page_failure(self.event, 'repair-worker', 'SIMULATED_TIMEOUT')
        self.page.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual((self.page.status, self.task.status), ('uncertain', 'uncertain'))

    def test_cancel_and_late_receipt_with_patient_foreign_key_do_not_deadlock(self):
        self.assertEqual(connection.vendor, 'postgresql')
        ready = threading.Event()
        worker_pid = []
        gate, created = EnvironmentGate.objects.get_or_create(environment='test',
            defaults={'version': 1, 'enabled': False, 'updated_at': timezone.now()})
        if created:
            self.extra_cleanup.append(gate)

        def save_receipt_in_second_connection():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    worker_pid.append(cursor.fetchone()[0])
                ready.set()
                with transaction.atomic():
                    attempt, page_attempt = lock_receipt_attempts(self.task.id, self.attempt.id, self.page_attempt.id)
                    response = FileObject.objects.create(patient_id=self.patient.id,
                        file_group_id=self.original.file_group_id, object_key=f'synthetic-receipt-{uuid4()}',
                        object_version='1', checksum='e' * 64, size_bytes=2, media_type='application/json',
                        purpose='ocr_response', status='available',
                        retention_policy_id=self.original.retention_policy_id, created_at=timezone.now())
                    attempt.usage_evidence = {'phase': 'provider_finished',
                        'provider_request_id': 'synthetic-receipt', 'raw_response_file_id': str(response.id)}
                    attempt.save(update_fields=['usage_evidence'])
                    return response.id
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                EnvironmentGate.objects.select_for_update().get(pk=gate.pk)
                Patient.objects.select_for_update().get(id=self.patient.id)
                ArchiveBatch.objects.select_for_update().get(id=self.batch.id)
                ArchivePage.objects.select_for_update().get(id=self.page.id)
                future = pool.submit(save_receipt_in_second_connection)
                self.assertTrue(ready.wait(5))
                deadline, blocked = time.monotonic() + 5, False
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT cardinality(pg_blocking_pids(%s))', [worker_pid[0]])
                        blocked = cursor.fetchone()[0] > 0
                    if blocked:
                        break
                    time.sleep(0.01)
                self.assertTrue(blocked, 'Receipt must contend with cancellation scope locks')
                task = Task.objects.select_for_update().get(id=self.task.id)
                task.status = 'cancelled'
                task.save(update_fields=['status'])
                ArchivePage.objects.filter(id=self.page.id).update(status='cancelled')
            response_id = future.result(timeout=10)
        self.task.refresh_from_db()
        self.attempt.refresh_from_db()
        self.page_attempt.refresh_from_db()
        self.assertEqual(self.task.status, 'cancelled')
        self.assertEqual(self.attempt.usage_evidence['raw_response_file_id'], str(response_id))
        self.assertTrue(FileObject.objects.filter(id=response_id).exists())
        self.assertIsNone(self.page_attempt.candidate_set_id)
