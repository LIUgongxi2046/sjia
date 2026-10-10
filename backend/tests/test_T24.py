"""T24 更正、撤销与依赖失效逻辑验证。

运行：backend/.venv/bin/python backend/manage.py test tests.test_T24 --keepdb
"""
from datetime import datetime, timezone

from django.test import TestCase

from identity.models import Patient, Principal
from records.correction import CorrectionRejected, correct_record
from records.models import Record, RecordField
from records.service import create_record
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from sources.service import register_revision, register_source
from verification.service import is_field_verified


def _now():
    return datetime.now(timezone.utc)


class CorrectionServiceTest(TestCase):
    def setUp(self):
        now = _now()
        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-T24", status="active", created_at=now)
        self.retention = RetentionPolicy.objects.create(policy_key="obs", version=1, data_class="observation", rules={}, schema_version="r-1", status="active")
        source = register_source(source_key="cor-src", owner="o", location="l", purpose="p", sensitivity="s", update_frequency="d")
        revision = register_revision(source=source, version="v1", checksum="c" * 64, license="l", allowed_use=["family_observation"])
        self.use_auth = DataUseAuthorization.objects.create(patient=self.patient, source_revision=revision, purpose="family_observation", environment="test", evidence_ref="e", approved_by=self.principal, approved_at=now)
        self.record_result = create_record(
            patient=self.patient, actor=self.principal, kind="observation", capture_method="manual", origin_type="self_report",
            retention_policy=self.retention, use_authorization_id=self.use_auth.id,
            fields=[{"field_code": "temp", "value_type": "decimal", "value_state": "known", "raw_numeric": "38.0", "raw_unit": "C", "sample_time_origin": "observation"}],
        )
        self.record = Record.objects.get(id=self.record_result["record_id"])

    def _correct(self, expected_version=1, reason="输入更正", fields=None):
        return correct_record(
            record=self.record, expected_version=expected_version, actor=self.principal, reason=reason,
            fields=fields or [{"field_code": "temp", "value_type": "decimal", "value_state": "known", "raw_numeric": "38.5", "raw_unit": "C", "sample_time_origin": "observation"}],
            use_authorization_id=self.use_auth.id, retention_policy=self.retention,
        )

    def test_correct_creates_new_version(self):
        result = self._correct()
        self.assertEqual(result["version"], 2)
        self.record.refresh_from_db()
        self.assertEqual(self.record.current_version, 2)

    def test_version_conflict(self):
        self._correct()
        with self.assertRaises(CorrectionRejected):
            self._correct(expected_version=1)

    def test_reason_required(self):
        with self.assertRaises(CorrectionRejected):
            self._correct(reason="")

    def test_new_fields_unverified(self):
        result = self._correct()
        new_field = RecordField.objects.get(record_version_id=result["record_version_id"], field_code="temp")
        self.assertFalse(is_field_verified(field_id=new_field.id, record_version_id=result["record_version_id"], patient=self.patient, purpose="assessment"))

    def test_data_epoch_incremented(self):
        self.patient.refresh_from_db()
        before = self.patient.data_epoch
        self._correct()
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.data_epoch, before + 1)
