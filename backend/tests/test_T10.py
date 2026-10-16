"""T10 体温、症状与基础记录保存逻辑验证。

运行：backend/.venv/bin/python backend/manage.py test tests.test_T10 --keepdb
"""
from datetime import datetime, timezone

from django.test import TestCase

from identity.models import Patient, Principal
from records.models import RecordField, RecordVersion
from records.service import RecordRejected, create_record, read_record
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from sources.service import register_revision, register_source


def _now():
    return datetime.now(timezone.utc)


class RecordServiceTest(TestCase):
    def setUp(self):
        now = _now()
        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-T10", status="active", created_at=now)
        self.retention = RetentionPolicy.objects.create(policy_key="obs", version=1, data_class="observation", rules={}, schema_version="r-1", status="active")
        source = register_source(source_key="obs-src", owner="o", location="l", purpose="p", sensitivity="s", update_frequency="d")
        revision = register_revision(source=source, version="v1", checksum="c" * 64, license="l", allowed_use=["family_observation"])
        self.use_auth = DataUseAuthorization.objects.create(
            patient=self.patient, source_revision=revision, purpose="family_observation", environment="test",
            evidence_ref="e", approved_by=self.principal, approved_at=now,
        )

    def _fields(self):
        return [
            {
                "field_code": "temp", "value_type": "decimal", "value_state": "known",
                "raw_numeric": "38.0", "raw_unit": "C", "qualifier": "eq",
                "medical_time": {"precision": "minute", "local_datetime": "2026-09-27T08:30:00", "timezone_name": "Asia/Shanghai", "resolution_status": "local_only"},
                "sample_time_origin": "observation",
            },
            {
                "field_code": "rash", "value_type": "categorical", "value_state": "known",
                "categorical_value": "yes", "sample_time_origin": "observation",
            },
        ]

    def _create(self):
        return create_record(
            patient=self.patient, actor=self.principal, kind="observation", capture_method="manual",
            origin_type="self_report", retention_policy=self.retention, use_authorization_id=self.use_auth.id,
            fields=self._fields(),
        )

    def test_create_and_read_preserves_values(self):
        result = self._create()
        self.assertEqual(result["patient_id"], str(self.patient.id))
        read = read_record(result["record_id"], self.patient)
        self.assertEqual(read["kind"], "observation")
        self.assertEqual(read["version"], 1)
        self.assertEqual(len(read["fields"]), 2)
        temp = next(f for f in read["fields"] if f["field_code"] == "temp")
        self.assertEqual(temp["raw_numeric"], "38.0")
        self.assertEqual(temp["raw_unit"], "C")
        self.assertEqual(temp["qualifier"], "eq")
        self.assertIsNotNone(temp["medical_time_id"])

    def test_zero_value_preserved(self):
        fields = [{"field_code": "temp", "value_type": "decimal", "value_state": "known", "raw_numeric": "0", "raw_unit": "C", "sample_time_origin": "observation"}]
        result = create_record(patient=self.patient, actor=self.principal, kind="observation", capture_method="manual", origin_type="self_report", retention_policy=self.retention, use_authorization_id=self.use_auth.id, fields=fields)
        field = RecordField.objects.get(record_version_id=result["record_version_id"], field_code="temp")
        self.assertEqual(str(field.raw_numeric), "0")

    def test_unknown_value_state_preserved(self):
        fields = [{"field_code": "temp", "value_type": "decimal", "value_state": "unknown", "raw_numeric": None, "sample_time_origin": "observation"}]
        result = create_record(patient=self.patient, actor=self.principal, kind="observation", capture_method="manual", origin_type="self_report", retention_policy=self.retention, use_authorization_id=self.use_auth.id, fields=fields)
        field = RecordField.objects.get(record_version_id=result["record_version_id"], field_code="temp")
        self.assertEqual(field.value_state, "unknown")
        self.assertIsNone(field.raw_numeric)

    def test_protected_field_rejected(self):
        fields = [{"field_code": "temp", "value_type": "decimal", "value_state": "known", "raw_numeric": "38", "patient_id": "x"}]
        with self.assertRaises(RecordRejected):
            create_record(patient=self.patient, actor=self.principal, kind="observation", capture_method="manual", origin_type="self_report", retention_policy=self.retention, use_authorization_id=self.use_auth.id, fields=fields)

    def test_decimal_known_requires_numeric(self):
        fields = [{"field_code": "temp", "value_type": "decimal", "value_state": "known", "raw_numeric": None}]
        with self.assertRaises(RecordRejected):
            create_record(patient=self.patient, actor=self.principal, kind="observation", capture_method="manual", origin_type="self_report", retention_policy=self.retention, use_authorization_id=self.use_auth.id, fields=fields)

    def test_field_without_medical_time_stays_null(self):
        fields = [{"field_code": "note", "value_type": "text", "value_state": "known", "raw_text": "口述说明", "sample_time_origin": "observation"}]
        result = create_record(patient=self.patient, actor=self.principal, kind="observation", capture_method="manual", origin_type="self_report", retention_policy=self.retention, use_authorization_id=self.use_auth.id, fields=fields)
        field = RecordField.objects.get(record_version_id=result["record_version_id"], field_code="note")
        self.assertIsNone(field.medical_time_id)

    def test_read_missing_record_raises(self):
        with self.assertRaises(RecordRejected):
            read_record("00000000-0000-0000-0000-000000000000", self.patient)
