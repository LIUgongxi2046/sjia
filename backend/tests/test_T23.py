"""T23 通知许可、真实渠道与回执逻辑验证。

真实渠道与获准接收人由 T29/T30 依据 D05/D06/D09 验证；本文件验证许可与回执状态流转。
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

    def _authorize(self):
        return authorize_attempt(
            patient_id=self.recipient.patient_id, recipient_id=self.recipient.id, provider_idempotency_key="key-1",
            permit_expires_at=_now() + timedelta(minutes=5),
        )

    def test_authorize_creates_attempt(self):
        attempt = self._authorize()
        self.assertEqual(attempt.status, "authorized")
        self.assertEqual(attempt.attempt_number, 1)

    def test_start_attempt(self):
        attempt = self._authorize()
        start_attempt(attempt=attempt, provider_request_id="req-1")
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "started")
        self.assertEqual(attempt.provider_request_id, "req-1")

    def test_accepted_receipt(self):
        attempt = self._authorize()
        start_attempt(attempt=attempt, provider_request_id="req-1")
        record_receipt(attempt=attempt, provider="sms", provider_event_id="e1", status="accepted", evidence_sha256="a" * 64)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "accepted")
        self.assertEqual(NotificationReceipt.objects.count(), 1)

    def test_delivered_not_accepted_as_delivered(self):
        attempt = self._authorize()
        start_attempt(attempt=attempt, provider_request_id="req-1")
        record_receipt(attempt=attempt, provider="sms", provider_event_id="e1", status="accepted", evidence_sha256="a" * 64)
        record_receipt(attempt=attempt, provider="sms", provider_event_id="e2", status="delivered", evidence_sha256="b" * 64)
        attempt.refresh_from_db()
        # attempt 状态仍为 accepted，不记为 delivered；送达只存在于回执。
        self.assertEqual(attempt.status, "accepted")
        self.assertEqual(NotificationReceipt.objects.filter(status="delivered").count(), 1)

    def test_failed_receipt(self):
        attempt = self._authorize()
        start_attempt(attempt=attempt, provider_request_id="req-1")
        record_receipt(attempt=attempt, provider="sms", provider_event_id="e1", status="failed", evidence_sha256="a" * 64)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "failed")

    def test_mark_uncertain(self):
        attempt = self._authorize()
        start_attempt(attempt=attempt, provider_request_id="req-1")
        mark_uncertain(attempt=attempt)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "uncertain")

    def test_cancel_prevents_send(self):
        attempt = self._authorize()
        cancel_attempt(attempt=attempt)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "cancelled")
        with self.assertRaises(NotificationRejected):
            start_attempt(attempt=attempt, provider_request_id="req-1")

    def test_receipt_dedup_by_provider_event(self):
        attempt = self._authorize()
        start_attempt(attempt=attempt, provider_request_id="req-1")
        record_receipt(attempt=attempt, provider="sms", provider_event_id="e1", status="accepted", evidence_sha256="a" * 64)
        from django.db import IntegrityError

        with self.assertRaises(IntegrityError):
            record_receipt(attempt=attempt, provider="sms", provider_event_id="e1", status="accepted", evidence_sha256="a" * 64)
