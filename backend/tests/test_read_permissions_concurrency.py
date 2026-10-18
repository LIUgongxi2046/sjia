from concurrent.futures import ThreadPoolExecutor
from threading import Event

from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError, connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from authorization.models import AccessGrant
from authorization.policy import Denied, evaluate
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal, PrincipalIdentity
from records.snapshot import begin_record_snapshot
from tests.isolated_rows import IsolatedSchemaRows


class ReadPermissionsConcurrencyTests(TransactionTestCase):
    def setUp(self):
        rows = IsolatedSchemaRows(require_empty=True)
        self.addCleanup(rows.cleanup)
        now = timezone.now()
        self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id='SYNTHETIC READ CONCURRENCY',
            status='active', created_at=now)
        self.gate = EnvironmentGate.objects.create(environment='test', enabled=True, version=1, updated_at=now)
        AccessGrant.objects.create(principal=self.actor, patient=self.patient, action='read_raw',
            field_codes=['temp'], record_scope='all', purpose='family_observation',
            granted_by=self.actor, evidence_ref='SYNTHETIC SOFTWARE TEST ONLY', valid_from=now)
        AccessGrant.objects.create(principal=self.actor, patient=self.patient, action='write_record',
            field_codes=['temp'], record_scope='all', purpose='family_observation',
            granted_by=self.actor, evidence_ref='SYNTHETIC SOFTWARE TEST ONLY', valid_from=now)

    def read(self):
        return evaluate(principal=self.actor, patient=self.patient, action='read_raw',
            purpose='family_observation', environment=self.gate.environment,
            session_auth_epoch=self.actor.auth_epoch)

    def during_read(self, operation):
        ready, release = Event(), Event()

        def reader():
            try:
                with transaction.atomic():
                    self.assertTrue(self.read().allowed)
                    ready.set()
                    self.assertTrue(release.wait(20), '并行检查未及时释放读取事务')
            finally:
                connections['default'].close()

        with ThreadPoolExecutor(max_workers=1) as executor:
            reading = executor.submit(reader)
            try:
                self.assertTrue(ready.wait(20), '读取事务未及时完成授权检查')
                with transaction.atomic():
                    with connections['default'].cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '500ms'")
                    operation()
            finally:
                release.set()
                reading.result(timeout=20)

    def test_same_account_readers_run_concurrently(self):
        self.during_read(lambda: self.assertTrue(self.read().allowed))

    def test_foreign_key_insert_commits_during_permission_read(self):
        self.during_read(lambda: PrincipalIdentity.objects.create(principal=self.actor,
            provider='synthetic-read-test', provider_subject='software-test', verified_at=timezone.now()))
        self.assertTrue(PrincipalIdentity.objects.filter(principal=self.actor).exists())

    def test_reading_write_options_uses_shared_authorization(self):
        self.during_read(lambda: self.assertTrue(evaluate(principal=self.actor,
            patient=self.patient, action='write_record', purpose='family_observation',
            environment=self.gate.environment, session_auth_epoch=self.actor.auth_epoch,
            read_only=True).allowed))

    def test_account_changes_wait_for_reader_and_next_read_checks_current_status(self):
        with self.assertRaises(OperationalError):
            self.during_read(lambda: Principal.objects.filter(id=self.actor.id).update(account_status='disabled'))
        Principal.objects.filter(id=self.actor.id).update(account_status='disabled')
        with self.assertRaises(Denied) as rejected:
            self.read()
        self.assertEqual(rejected.exception.reason_code, 'ACCOUNT_NOT_ACTIVE')

    def test_next_read_checks_current_auth_epoch(self):
        Principal.objects.filter(id=self.actor.id).update(auth_epoch=self.actor.auth_epoch + 1)
        with self.assertRaises(Denied) as rejected:
            self.read()
        self.assertEqual(rejected.exception.reason_code, 'AUTH_EPOCH_MISMATCH')

    def test_unlocked_authorization_requires_read_only_transaction(self):
        with self.assertRaises(ImproperlyConfigured):
            evaluate(principal=self.actor, patient=self.patient, action='read_raw',
                purpose='family_observation', environment=self.gate.environment,
                session_auth_epoch=self.actor.auth_epoch, lock=False)

    def test_summary_worker_scope_allows_audit_foreign_keys_and_read_snapshot(self):
        from summaries.jobs import lock_summary_scope
        ready, release = Event(), Event()

        def worker():
            try:
                with transaction.atomic():
                    lock_summary_scope(self.actor.id, self.patient.id, self.gate.environment)
                    ready.set()
                    self.assertTrue(release.wait(20))
            finally:
                connections['default'].close()

        with ThreadPoolExecutor(max_workers=1) as executor:
            working = executor.submit(worker)
            try:
                self.assertTrue(ready.wait(20))
                with transaction.atomic():
                    with connections['default'].cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '500ms'")
                    AccessGrant.objects.create(principal=self.actor, patient=self.patient,
                        action='read_history', field_codes=['temp'], record_scope='all',
                        purpose='family_observation', granted_by=self.actor,
                        evidence_ref='SYNTHETIC SOFTWARE TEST ONLY', valid_from=timezone.now())
                with transaction.atomic():
                    begin_record_snapshot()
                    self.assertTrue(evaluate(principal=self.actor, patient=self.patient,
                        action='read_raw', purpose='family_observation',
                        environment=self.gate.environment, session_auth_epoch=self.actor.auth_epoch,
                        lock=False).allowed)
            finally:
                release.set()
                working.result(timeout=20)

    def test_read_only_snapshot_reads_committed_account_during_account_update(self):
        ready, release = Event(), Event()

        def writer():
            try:
                with transaction.atomic():
                    Principal.objects.filter(id=self.actor.id).update(account_status='disabled')
                    ready.set()
                    self.assertTrue(release.wait(20))
            finally:
                connections['default'].close()

        with ThreadPoolExecutor(max_workers=1) as executor:
            writing = executor.submit(writer)
            try:
                self.assertTrue(ready.wait(20))
                with transaction.atomic():
                    begin_record_snapshot()
                    self.assertTrue(evaluate(principal=self.actor, patient=self.patient,
                        action='read_raw', purpose='family_observation',
                        environment=self.gate.environment, session_auth_epoch=self.actor.auth_epoch,
                        lock=False).allowed)
            finally:
                release.set()
                writing.result(timeout=20)
        with self.assertRaises(Denied) as rejected:
            self.read()
        self.assertEqual(rejected.exception.reason_code, 'ACCOUNT_NOT_ACTIVE')
