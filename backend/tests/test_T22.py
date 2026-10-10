"""T22 首页、提示详情、来源与真实阅读逻辑验证。

运行：backend/.venv/bin/python backend/manage.py test tests.test_T22 --keepdb
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from django.test import TestCase

from alerting.home import clear_condition, determine_assessment_state, mark_read
from alerting.models import AlertRecipient


def _now():
    return datetime.now(timezone.utc)


class AssessmentStateTest(TestCase):
    def test_configuration_pending(self):
        self.assertEqual(determine_assessment_state(config_available=False, evaluation=None), "configuration_pending")

    def test_not_evaluated(self):
        self.assertEqual(determine_assessment_state(config_available=True, evaluation=None), "not_evaluated")

    def test_insufficient_status(self):
        ev = SimpleNamespace(status="insufficient", decision="unknown", validity="valid")
        self.assertEqual(determine_assessment_state(config_available=True, evaluation=ev), "insufficient")

    def test_insufficient_unknown_decision(self):
        ev = SimpleNamespace(status="completed", decision="unknown", validity="valid")
        self.assertEqual(determine_assessment_state(config_available=True, evaluation=ev), "insufficient")

    def test_current(self):
        ev = SimpleNamespace(status="completed", decision="met", validity="valid")
        self.assertEqual(determine_assessment_state(config_available=True, evaluation=ev), "current")

    def test_stale_invalid(self):
        ev = SimpleNamespace(status="completed", decision="met", validity="invalid")
        self.assertEqual(determine_assessment_state(config_available=True, evaluation=ev), "stale")

    def test_clear_only_current_without_alert(self):
        self.assertTrue(clear_condition(assessment_state="current", has_current_alert=False))
        self.assertFalse(clear_condition(assessment_state="current", has_current_alert=True))
        self.assertFalse(clear_condition(assessment_state="insufficient", has_current_alert=False))


class ReadReceiptTest(TestCase):
    def setUp(self):
        now = _now()
        from identity.models import Patient, Principal
        from relations.models import Relation, RelationApplication
        from alerting.models import AlertEvent, AlertEvaluation
        from family_content.models import ReleaseBundle
        from configurations.models import ConfigVersion
        from contacts.models import ContactVersion
        from sources.models import DataUseAuthorization
        from sources.service import register_revision, register_source

        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-T22", status="active", created_at=now)
        source = register_source(source_key="a-src", owner="o", location="l", purpose="p", sensitivity="s", update_frequency="d")
        revision = register_revision(source=source, version="v1", checksum="c" * 64, license="l", allowed_use=["family_observation"])
        use_auth = DataUseAuthorization.objects.create(patient=self.patient, source_revision=revision, purpose="family_observation", environment="test", evidence_ref="e", approved_by=self.principal, approved_at=now)
        policy_config = ConfigVersion.objects.create(config_key="tp", kind="rule", version=1, schema_version="rule-1", payload={}, checksum="a" * 64, status="enabled", created_by=self.principal, created_at=now)
        contact = ContactVersion.objects.create(contact_id=uuid.uuid4(), version=1, contact_name="科室", purpose="alert", channel="phone", channel_value="010-1", owner=self.principal, source_revision=revision, approval_config=policy_config, verified_at=now, updated_at=now, valid_until=now + timedelta(days=1), status="active")
        release = ReleaseBundle.objects.create(environment="test", version=1, publication_epoch=0, status="enabled", contact_version=contact, validation_config=policy_config)
        from tasks.models import Task

        task = Task.objects.create(actor=self.principal, patient=self.patient, task_type="alert_evaluation", schema_version="task-1", input_refs={}, input_sha256="a" * 64, status="completed", status_version=1, created_at=now, deadline_at=now)
        evaluation = AlertEvaluation.objects.create(patient=self.patient, task_id=task.id, environment="test", assessment_at=now, known_at=now, context_sequence=1, mode="current", status="completed", decision="met", data_epoch=0, verification_epoch=0, publication_epoch=0, authorization_version=1, lease_generation=1, use_authorization_id=use_auth.id, input_digest="a" * 64, missing_items=[], computed_at=now, validity="valid")
        event = AlertEvent.objects.create(patient=self.patient, evaluation_id=evaluation.id, release=release, trigger_key="t", trigger_policy=policy_config, input_snapshot={}, validity="valid", created_at=now)
        application = RelationApplication.objects.create(version=1, applicant=self.principal, patient=self.patient, evidence_ref="proof", status="approved", checked_by=self.principal, checked_at=now, created_at=now)
        relation = Relation.objects.create(patient=self.patient, guardian=self.principal, application=application, status="active", version=1, checked_by=self.principal, checked_at=now, valid_from=now)
        self.recipient = AlertRecipient.objects.create(patient_id=self.patient.id, alert_id=event.id, relation_id=relation.id, channel="phone", delivery_status="delivered")

    def test_first_read_sets_read_at(self):
        mark_read(self.recipient)
        self.recipient.refresh_from_db()
        self.assertIsNotNone(self.recipient.read_at)

    def test_repeat_read_does_not_change(self):
        mark_read(self.recipient)
        first = self.recipient.read_at
        later = first + timedelta(seconds=10)
        mark_read(self.recipient, read_at=later)
        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.read_at, first)
