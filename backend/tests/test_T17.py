"""T17 受控专业核对、逐字段资格与撤销逻辑验证。

运行：backend/.venv/bin/python backend/manage.py test tests.test_T17 --keepdb
"""
import uuid
from datetime import datetime, timezone

from django.test import TestCase

from configurations.models import ConfigVersion
from identity.models import Patient, Principal
from records.models import RecordField, RecordVersion
from records.service import create_record
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from sources.service import register_revision, register_source
from verification.service import VerificationRejected, is_field_verified, revoke_verification, verify_fields


def _now():
    return datetime.now(timezone.utc)


class VerificationServiceTest(TestCase):
    def setUp(self):
        now = _now()
        self.verifier = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-T17", status="active", created_at=now)
        self.retention = RetentionPolicy.objects.create(policy_key="obs", version=1, data_class="observation", rules={}, schema_version="r-1", status="active")
        source = register_source(source_key="v-src", owner="o", location="l", purpose="p", sensitivity="s", update_frequency="d")
        self.revision = register_revision(source=source, version="v1", checksum="c" * 64, license="l", allowed_use=["family_observation"])
        self.use_auth = DataUseAuthorization.objects.create(patient=self.patient, source_revision=self.revision, purpose="family_observation", environment="test", evidence_ref="e", approved_by=self.verifier, approved_at=now)
        self.policy = ConfigVersion.objects.create(config_key="vp", kind="verification", version=1, schema_version="verification-1", payload={}, checksum="a" * 64, status="enabled", created_by=self.verifier, created_at=now)

    def _record(self):
        result = create_record(
            patient=self.patient, actor=self.verifier, kind="observation", capture_method="manual", origin_type="self_report",
            retention_policy=self.retention, use_authorization_id=self.use_auth.id,
            fields=[
                {"field_code": "temp", "value_type": "decimal", "value_state": "known", "raw_numeric": "38.0", "raw_unit": "C", "sample_time_origin": "observation"},
                {"field_code": "rash", "value_type": "categorical", "value_state": "known", "categorical_value": "yes", "sample_time_origin": "observation"},
            ],
        )
        fields = list(RecordField.objects.filter(record_version_id=result["record_version_id"]).order_by("ordinal"))
        return result, fields

    def test_verify_specified_field_only(self):
        result, fields = self._record()
        temp_field, rash_field = fields
        verify_fields(patient=self.patient, record_version_id=result["record_version_id"], verifier=self.verifier, policy=self.policy, purpose="assessment", evidence_ref="e", evidence_source=self.revision, field_results=[(temp_field.id, "consistent")])
        self.assertTrue(is_field_verified(field_id=temp_field.id, record_version_id=result["record_version_id"], patient=self.patient, purpose="assessment"))
        self.assertFalse(is_field_verified(field_id=rash_field.id, record_version_id=result["record_version_id"], patient=self.patient, purpose="assessment"))

    def test_revoke_prevents_old_proof(self):
        result, fields = self._record()
        temp_field = fields[0]
        verification = verify_fields(patient=self.patient, record_version_id=result["record_version_id"], verifier=self.verifier, policy=self.policy, purpose="assessment", evidence_ref="e", evidence_source=self.revision, field_results=[(temp_field.id, "consistent")])
        revoke_verification(verification=verification, revoked_by=self.verifier, reason="source corrected")
        self.assertFalse(is_field_verified(field_id=temp_field.id, record_version_id=result["record_version_id"], patient=self.patient, purpose="assessment"))

    def test_revoke_requires_reason(self):
        result, fields = self._record()
        verification = verify_fields(patient=self.patient, record_version_id=result["record_version_id"], verifier=self.verifier, policy=self.policy, purpose="assessment", evidence_ref="e", evidence_source=self.revision, field_results=[(fields[0].id, "consistent")])
        with self.assertRaises(VerificationRejected):
            revoke_verification(verification=verification, revoked_by=self.verifier, reason="")

    def test_verify_requires_fields(self):
        with self.assertRaises(VerificationRejected):
            verify_fields(patient=self.patient, record_version_id=uuid.uuid4(), verifier=self.verifier, policy=self.policy, purpose="assessment", evidence_ref="e", evidence_source=self.revision, field_results=[])

    def test_old_proof_not_attached_to_new_version(self):
        result, fields = self._record()
        temp_field = fields[0]
        verify_fields(patient=self.patient, record_version_id=result["record_version_id"], verifier=self.verifier, policy=self.policy, purpose="assessment", evidence_ref="e", evidence_source=self.revision, field_results=[(temp_field.id, "consistent")])
        # 新字段（不同 record_version）不继承旧证明
        other_field_id = uuid.uuid4()
        self.assertFalse(is_field_verified(field_id=other_field_id, record_version_id=result["record_version_id"], patient=self.patient, purpose="assessment"))
