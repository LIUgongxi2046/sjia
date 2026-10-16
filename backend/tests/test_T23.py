"""T23 retired API refusal and historical storage/state checks, synthetic only.

Native signed permit, acceptance and delivered-receipt positives are NOT_RUN here.
Protocol injection isolates refusal branches only; no clinical or provider approval is implied.
运行：backend/.venv/bin/python backend/manage.py test tests.test_T23 --keepdb
"""
import uuid
from datetime import datetime, timedelta, timezone

from django.test import TestCase

from notifications.models import NotificationAttempt, NotificationReceipt
from notifications.service import (
    NotificationRejected,
    authorize_attempt,
    cancel_attempt,
    mark_uncertain,
    record_receipt,
    start_attempt,
)


def _now():
    return datetime.now(timezone.utc)


class NotificationServiceTest(TestCase):
    def setUp(self):
        now = _now()
        from identity.models import Patient, Principal
        from relations.models import Relation, RelationApplication
        from alerting.models import AlertEvent, AlertEvaluation, AlertRecipient
        from family_content.models import ReleaseBundle
        from configurations.models import ConfigVersion
        from contacts.models import ContactVersion
        from sources.models import DataUseAuthorization
        from sources.service import register_revision, register_source
        from tasks.models import Task

        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-T23", status="active", created_at=now)
        source = register_source(source_key="n-src", owner="o", location="l", purpose="p", sensitivity="s", update_frequency="d")
        revision = register_revision(source=source, version="v1", checksum="c" * 64, license="l", allowed_use=["family_observation"])
        use_auth = DataUseAuthorization.objects.create(patient=self.patient, source_revision=revision, purpose="family_observation", environment="test", evidence_ref="e", approved_by=self.principal, approved_at=now)
        policy_config = ConfigVersion.objects.create(config_key="tp", kind="rule", version=1, schema_version="rule-1", payload={}, checksum="a" * 64, status="enabled", created_by=self.principal, created_at=now)
        contact = ContactVersion.objects.create(contact_id=uuid.uuid4(), version=1, contact_name="科室", purpose="alert", channel="phone", channel_value="010-1", owner=self.principal, source_revision=revision, approval_config=policy_config, verified_at=now, updated_at=now, valid_until=now + timedelta(days=1), status="active")
        release = ReleaseBundle.objects.create(environment="test", version=1, publication_epoch=0, status="enabled", contact_version=contact, validation_config=policy_config)
        task = Task.objects.create(actor=self.principal, patient=self.patient, task_type="alert_evaluation", schema_version="task-1", input_refs={}, input_sha256="a" * 64, status="completed", status_version=1, created_at=now, deadline_at=now)
        evaluation = AlertEvaluation.objects.create(patient=self.patient, task_id=task.id, environment="test", assessment_at=now, known_at=now, context_sequence=1, mode="current", status="completed", decision="met", data_epoch=0, verification_epoch=0, publication_epoch=0, authorization_version=1, lease_generation=1, use_authorization_id=use_auth.id, input_digest="a" * 64, missing_items=[], computed_at=now, validity="valid")
        event = AlertEvent.objects.create(patient=self.patient, evaluation_id=evaluation.id, release=release, trigger_key="t", trigger_policy=policy_config, input_snapshot={}, validity="valid", created_at=now)
        application = RelationApplication.objects.create(version=1, applicant=self.principal, patient=self.patient, evidence_ref="proof", status="approved", checked_by=self.principal, checked_at=now, created_at=now)
        relation = Relation.objects.create(patient=self.patient, guardian=self.principal, application=application, status="active", version=1, checked_by=self.principal, checked_at=now, valid_from=now)
        self.recipient = AlertRecipient.objects.create(patient_id=self.patient.id, alert_id=event.id, relation_id=relation.id, channel="phone", delivery_status="not_sent")

    def _historical_attempt(self, status='authorized'):
        # Deliberately a historical, non-native row. No send permit is issued.
        return NotificationAttempt.objects.create(patient_id=self.patient.id, recipient_id=self.recipient.id,
            attempt_number=1, send_permit_id=uuid.uuid4(), provider_idempotency_key='synthetic-historical-key',
            authorized_at=_now(), permit_expires_at=_now() + timedelta(minutes=5), status=status)

    def test_unconfigured_authorization_stays_fail_closed(self):
        with self.assertRaisesMessage(NotificationRejected, 'NOTIFICATION_PROVIDER_UNAVAILABLE'):
            authorize_attempt(patient_id=self.patient.id, recipient_id=self.recipient.id,
                provider_idempotency_key='synthetic-test', permit_expires_at=_now() + timedelta(minutes=5))
        self.assertFalse(NotificationAttempt.objects.exists())

    def test_retired_authorize_requires_native_permit(self):
        from unittest.mock import patch
        # UNIT: isolate the retired API refusal after protocol admission only.
        with patch('notifications.service.require_notification_protocol'), self.assertRaisesMessage(
                NotificationRejected, 'NOTIFICATION_NATIVE_PERMIT_REQUIRED'):
            authorize_attempt(patient_id=self.patient.id, recipient_id=self.recipient.id,
                provider_idempotency_key='synthetic-test', permit_expires_at=_now() + timedelta(minutes=5))
        self.assertFalse(NotificationAttempt.objects.exists())

    def test_start_rejects_mismatched_request_identity(self):
        from unittest.mock import patch
        attempt = self._historical_attempt()
        with patch('notifications.service.require_notification_protocol'), patch(
                'notifications.native.submit_native') as submit, self.assertRaisesMessage(
                NotificationRejected, 'NOTIFICATION_PROVIDER_REQUEST_MISMATCH'):
            start_attempt(attempt=attempt, provider_request_id='different-request')
        submit.assert_not_called()
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'authorized')

    def _unsigned_receipt_rejected(self, status):
        from unittest.mock import patch
        attempt = self._historical_attempt('started')
        with patch('notifications.service.require_notification_protocol'), self.assertRaisesMessage(
                NotificationRejected, 'NOTIFICATION_SIGNED_RECEIPT_REQUIRED'):
            record_receipt(attempt=attempt, provider='synthetic', provider_event_id='synthetic-event',
                status=status, evidence_sha256='a' * 64)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'started')
        self.assertFalse(NotificationReceipt.objects.exists())

    def test_unsigned_accepted_receipt_rejected(self):
        self._unsigned_receipt_rejected('accepted')

    def test_unsigned_delivered_receipt_rejected(self):
        self._unsigned_receipt_rejected('delivered')

    def test_unsigned_failed_receipt_rejected(self):
        self._unsigned_receipt_rejected('failed')

    def test_historical_mark_uncertain_is_idempotent(self):
        attempt = self._historical_attempt('started')
        mark_uncertain(attempt=attempt)
        mark_uncertain(attempt=attempt)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'uncertain')

    def test_historical_cancel_is_terminal_for_cancel_operation(self):
        attempt = self._historical_attempt()
        cancel_attempt(attempt=attempt)
        attempt.refresh_from_db()
        finished = attempt.finished_at
        cancel_attempt(attempt=attempt)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'cancelled')
        self.assertEqual(attempt.finished_at, finished)
        self.assertIsNotNone(finished)
        with self.assertRaises(NotificationRejected):
            start_attempt(attempt=attempt, provider_request_id='synthetic-historical-key')
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'cancelled')

    def test_historical_receipt_provider_event_is_unique_in_storage(self):
        from django.db import IntegrityError, transaction
        attempt = self._historical_attempt('accepted')
        values = {'attempt': attempt, 'provider': 'synthetic', 'provider_event_id': 'synthetic-event',
            'status': 'accepted', 'occurred_at': _now(), 'received_at': _now(), 'evidence_sha256': 'a' * 64}
        NotificationReceipt.objects.create(**values)
        with self.assertRaises(IntegrityError), transaction.atomic():
            NotificationReceipt.objects.create(**values)
        self.assertEqual(NotificationReceipt.objects.count(), 1)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'accepted')
