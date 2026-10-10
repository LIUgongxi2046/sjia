import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase

from identity.models import Patient, Principal
from requests.models import WriteIntent, WriteRequest
from requests.notifications import request_value
from requests.service import NORMALIZATION_VERSION, prepare_intent
from retention.models import RetentionPolicy
from relations.models import Relation, RelationApplication
from relations.service import lock_relation_for_access, revoke_relation


class NotificationRevocationCompetitionTest(TransactionTestCase):
    databases = {"default"}

    def setUp(self):
        now = datetime.now(timezone.utc)
        guardian = Principal.objects.create(
            account_status=Principal.AccountStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        reviewer = Principal.objects.create(
            account_status=Principal.AccountStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        patient = Patient.objects.create(
            display_id=f"SIM-T23-{uuid.uuid4()}",
            status=Patient.Status.ACTIVE,
            created_at=now,
        )
        application = RelationApplication.objects.create(
            version=1,
            applicant=guardian,
            patient=patient,
            evidence_ref="simulation://t23/relation",
            status=RelationApplication.Status.APPROVED,
            checked_by=reviewer,
            checked_at=now,
            created_at=now,
        )
        self.relation = Relation.objects.create(
            patient=patient,
            guardian=guardian,
            application=application,
            status=Relation.Status.ACTIVE,
            version=1,
            auth_epoch=0,
            checked_by=reviewer,
            checked_at=now,
            valid_from=now,
        )
        self.guardian = guardian
        self.patient = patient

    def test_unknown_request_result_cannot_be_retried(self):
        now = datetime.now(timezone.utc)
        retention = RetentionPolicy.objects.create(
            policy_key=f"sim-t23-notification-{uuid.uuid4()}",
            version=1,
            data_class="simulation-notification",
            rules={},
            schema_version="retention-1",
            status=RetentionPolicy.Status.ACTIVE,
        )
        recipient_id = uuid.uuid4()
        target_key = f"/support/notification-records/{recipient_id}/send"
        request_id = uuid.uuid4()
        payload = {"expected_snapshot": "a" * 64}
        intent = prepare_intent(
            actor=self.guardian,
            patient=self.patient,
            operation_id="notification_v2_send",
            target_key=target_key,
            request_id=request_id,
            payload=payload,
            recovery_payload=payload,
            schema_version="2.0.0",
            retention_policy=retention,
            ttl_seconds=300,
            authorization_context={"mode": "SIMULATION"},
        )
        WriteRequest.objects.create(
            actor=self.guardian,
            patient=self.patient,
            action=intent.operation_id,
            target_key=target_key,
            request_id=request_id,
            payload_sha256=intent.payload_sha256,
            normalization_version=NORMALIZATION_VERSION,
            expected_version=None,
            status=WriteRequest.Status.PROCESSING,
            resource_type=None,
            resource_id=None,
            error_code=None,
            created_at=now,
            completed_at=None,
            expires_at=None,
        )

        result = request_value(intent)
        saved_intent = WriteIntent.objects.get(id=intent.id)
        saved_request = WriteRequest.objects.get(
            actor=self.guardian,
            action=intent.operation_id,
            target_key=target_key,
            request_id=request_id,
        )
        self.assertEqual(result["state"], "unknown")
        self.assertFalse(result["may_retry"])
        self.assertIsNone(result["receipt"])
        self.assertEqual(saved_intent.status, WriteIntent.Status.PREPARED)
        self.assertIsNone(saved_intent.write_request_id)
        self.assertEqual(saved_request.status, WriteRequest.Status.PROCESSING)

    def test_notification_scope_lock_serializes_with_revocation(self):
        scope_locked = threading.Event()
        revoke_waiting_for_row = threading.Event()
        allow_scope_transaction_to_finish = threading.Event()
        stale_relation = Relation.objects.get(id=self.relation.id)

        def hold_notification_scope_lock():
            close_old_connections()
            try:
                with transaction.atomic():
                    relation = lock_relation_for_access(self.relation.id, patient_id=self.relation.patient_id)
                    if relation.status != Relation.Status.ACTIVE:
                        raise AssertionError("notification scope did not observe the active relation")
                    scope_locked.set()
                    if not allow_scope_transaction_to_finish.wait(timeout=10):
                        raise AssertionError("notification scope transaction release timed out")
            finally:
                close_old_connections()

        def revoke_after_scope_lock():
            close_old_connections()
            try:
                if not scope_locked.wait(timeout=10):
                    raise AssertionError("notification scope lock was not acquired")

                def observe_row_lock_attempt(execute, sql, params, many, context):
                    if "FOR UPDATE" in sql.upper():
                        revoke_waiting_for_row.set()
                    return execute(sql, params, many, context)

                with connection.execute_wrapper(observe_row_lock_attempt):
                    return revoke_relation(
                        relation=stale_relation,
                        reason="simulation competition check",
                        revoked_by=stale_relation.guardian,
                    )
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            scope_future = executor.submit(hold_notification_scope_lock)
            revoke_future = executor.submit(revoke_after_scope_lock)
            if not revoke_waiting_for_row.wait(timeout=10):
                allow_scope_transaction_to_finish.set()
                raise AssertionError("revocation did not reach its row lock")
            allow_scope_transaction_to_finish.set()
            scope_future.result(timeout=10)
            revoked = revoke_future.result(timeout=10)

        current = Relation.objects.get(id=self.relation.id)
        self.assertEqual(revoked.status, Relation.Status.REVOKED)
        self.assertEqual(current.status, Relation.Status.REVOKED)
        self.assertEqual(current.auth_epoch, 1)
