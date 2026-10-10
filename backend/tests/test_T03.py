"""T03 授权、审计与家庭投影逻辑验证。

这些是 Policy Gate、QuerySet、审计与投影的单元逻辑检查，验证允许与拒绝路径。
真实账号、岗位与获准资料的业务验收由 T09/T29 依据 D02/D08 执行。
运行：backend/.venv/bin/python backend/manage.py test tests.test_T03 --keepdb
"""
import uuid
from datetime import datetime, timedelta, timezone

from django.test import TestCase
from rest_framework import serializers

from audit.models import AuditEvent
from audit.service import write_audit
from authorization.actions import CASE_ACTIONS, SERVICE_ACTIONS
from authorization.policy import Denied, evaluate
from authorization.querysets import accessible_patient_ids
from authorization.serializers import RejectUnknownFieldsMixin
from authorization.models import AccessGrant, ServiceRoleGrant
from configurations.models import ConfigVersion
from family_content.models import EnvironmentGate
from family_content.projection import project_fields
from identity.models import Patient, Principal
from relations.models import Relation, RelationApplication
from retention.models import RetentionPolicy


def _now():
    return datetime.now(timezone.utc)


class PolicyGateTest(TestCase):
    def setUp(self):
        now = _now()
        self.now = now
        self.principal = Principal.objects.create(account_status="active", auth_epoch=0, created_at=now, updated_at=now)
        self.disabled = Principal.objects.create(account_status="disabled", auth_epoch=0, created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-T03", status="active", created_at=now)
        self.other_patient = Patient.objects.create(display_id="P-OTHER", status="active", created_at=now)
        self.retention = RetentionPolicy.objects.create(policy_key="audit", version=1, data_class="audit", rules={}, schema_version="r-1", status="active")
        self.runtime_config = ConfigVersion.objects.create(
            config_key="runtime", kind="validation", version=1, schema_version="runtime-1",
            payload={}, checksum="a" * 64, status="enabled", created_by=self.principal, created_at=now,
        )
        EnvironmentGate.objects.create(environment="test", version=1, enabled=True, runtime_config=self.runtime_config, updated_at=now)

    def _grant(self, principal=None, patient=None, action="read_raw", scope="all", field_codes=None, relation=None, revoked=False, expired=False):
        now = self.now
        grant = AccessGrant.objects.create(
            patient=patient or self.patient,
            principal=principal or self.principal,
            relation_id=relation.id if relation else None,
            action=action,
            field_codes=field_codes or ["temp", "symptom"],
            record_scope=scope,
            purpose="family_observation",
            granted_by=self.principal,
            evidence_ref="test",
            valid_from=now - timedelta(days=1),
            valid_until=None if not expired else now - timedelta(hours=1),
            revoked_at=now - timedelta(hours=1) if revoked else None,
        )
        return grant

    def _relation(self, patient=None):
        application = RelationApplication.objects.create(
            version=1,
            applicant=self.principal,
            patient=patient or self.patient,
            evidence_ref="test",
            status="approved",
            checked_by=self.principal,
            checked_at=self.now,
            created_at=self.now,
        )
        return Relation.objects.create(
            patient=patient or self.patient,
            guardian=self.principal,
            application=application,
            status="active",
            version=1,
            checked_by=self.principal,
            checked_at=self.now,
            valid_from=self.now - timedelta(days=1),
        )

    def test_allow_with_active_grant(self):
        self._grant()
        decision = evaluate(principal=self.principal, patient=self.patient, action="read_raw", purpose="family_observation", environment="test", session_auth_epoch=0)
        self.assertTrue(decision.allowed)

    def test_deny_cross_patient(self):
        self._grant(patient=self.patient)
        with self.assertRaises(Denied):
            evaluate(principal=self.principal, patient=self.other_patient, action="read_raw", purpose="family_observation", environment="test", session_auth_epoch=0)

    def test_deny_scope_own_with_different_author(self):
        self._grant(scope="own")
        with self.assertRaises(Denied):
            evaluate(principal=self.principal, patient=self.patient, action="read_raw", purpose="family_observation", environment="test", session_auth_epoch=0, author_id=uuid.uuid4())

    def test_deny_field_not_granted(self):
        self._grant(field_codes=["temp"])
        with self.assertRaises(Denied):
            evaluate(principal=self.principal, patient=self.patient, action="read_raw", purpose="family_observation", environment="test", session_auth_epoch=0, field_codes=["symptom"])

    def test_deny_revoked_grant(self):
        self._grant(revoked=True)
        with self.assertRaises(Denied):
            evaluate(principal=self.principal, patient=self.patient, action="read_raw", purpose="family_observation", environment="test", session_auth_epoch=0)

    def test_deny_expired_grant(self):
        self._grant(expired=True)
        with self.assertRaises(Denied):
            evaluate(principal=self.principal, patient=self.patient, action="read_raw", purpose="family_observation", environment="test", session_auth_epoch=0)

    def test_deny_disabled_account(self):
        self._grant()
        with self.assertRaises(Denied):
            evaluate(principal=self.disabled, patient=self.patient, action="read_raw", purpose="family_observation", environment="test", session_auth_epoch=0)

    def test_deny_auth_epoch_mismatch(self):
        self._grant()
        with self.assertRaises(Denied):
            evaluate(principal=self.principal, patient=self.patient, action="read_raw", purpose="family_observation", environment="test", session_auth_epoch=1)

    def test_service_action_allowed(self):
        ServiceRoleGrant.objects.create(
            principal=self.principal, environment_id="test", action="verify_field",
            granted_by=self.principal, evidence_ref="test", valid_from=self.now - timedelta(days=1),
        )
        decision = evaluate(principal=self.principal, patient=self.patient, action="verify_field", purpose="clinical", environment="test", session_auth_epoch=0)
        self.assertTrue(decision.allowed)
        self.assertIsNotNone(decision.service_grant)

    def test_service_action_denied_without_grant(self):
        with self.assertRaises(Denied):
            evaluate(principal=self.principal, patient=self.patient, action="verify_field", purpose="clinical", environment="test", session_auth_epoch=0)

    def test_relation_required_when_grant_has_relation(self):
        relation = self._relation()
        self._grant(relation=relation)
        decision = evaluate(principal=self.principal, patient=self.patient, action="read_raw", purpose="family_observation", environment="test", session_auth_epoch=0, require_relation=True)
        self.assertTrue(decision.allowed)

    def test_queryset_accessible_patient_ids(self):
        self._grant(patient=self.patient)
        ids = accessible_patient_ids(self.principal.id, "read_raw", self.now)
        self.assertEqual(ids, {self.patient.id})


class AuditServiceTest(TestCase):
    def setUp(self):
        now = _now()
        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-AUDIT", status="active", created_at=now)
        self.retention = RetentionPolicy.objects.create(policy_key="audit", version=1, data_class="audit", rules={}, schema_version="r-1", status="active")

    def test_write_audit_persists(self):
        write_audit(actor=self.principal, patient=self.patient, action="read_raw", object_type="record", object_id=uuid.uuid4(), outcome="denied", reason_code="CASE_ACTION_DENIED", retention_policy=self.retention)
        self.assertEqual(AuditEvent.objects.filter(actor=self.principal, outcome="denied").count(), 1)


class FamilyProjectionTest(TestCase):
    def test_project_fields_filters_unapproved(self):
        fields = [
            {"field_code": "temp", "value": "38.0"},
            {"field_code": "crp", "value": "12"},
            {"field_code": "platelet", "value": "90"},
        ]
        result = project_fields(fields, ["temp", "platelet"])
        self.assertEqual([f["field_code"] for f in result], ["temp", "platelet"])


class ProtectedFieldRejectionTest(TestCase):
    def test_reject_protected_field(self):
        class SampleSerializer(RejectUnknownFieldsMixin, serializers.Serializer):
            temp = serializers.CharField()

        serializer = SampleSerializer(data={"temp": "38.0", "patient_id": "unexpected"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("patient_id", serializer.errors)
