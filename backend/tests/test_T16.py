"""T16 时间轴、历次值、历史时间目录与来源逻辑验证。

运行：backend/.venv/bin/python backend/manage.py test tests.test_T16 --keepdb
"""
from datetime import datetime, timedelta, timezone

from django.test import TestCase

from identity.models import Patient, Principal
from records.models import RecordField
from records.queries import analyte_search, history_search, record_search, record_version_read, source_resolve
from records.service import create_record
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from sources.service import register_revision, register_source


def _now():
    return datetime.now(timezone.utc)


class RecordQueryTest(TestCase):
    def setUp(self):
        now = _now()
        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-T16", status="active", created_at=now)
        self.retention = RetentionPolicy.objects.create(policy_key="obs", version=1, data_class="observation", rules={}, schema_version="r-1", status="active")
        source = register_source(source_key="q-src", owner="o", location="l", purpose="p", sensitivity="s", update_frequency="d")
        revision = register_revision(source=source, version="v1", checksum="c" * 64, license="l", allowed_use=["family_observation"])
        self.use_auth = DataUseAuthorization.objects.create(patient=self.patient, source_revision=revision, purpose="family_observation", environment="test", evidence_ref="e", approved_by=self.principal, approved_at=now)

    def _temp_record(self, value, unit="C", method="oral", medical_time=None, concept_code="temp"):
        return create_record(
            patient=self.patient, actor=self.principal, kind="observation", capture_method="manual", origin_type="self_report",
            retention_policy=self.retention, use_authorization_id=self.use_auth.id,
            fields=[{
                "field_code": "temp", "concept_code": concept_code, "value_type": "decimal", "value_state": "known",
                "raw_numeric": value, "raw_unit": unit, "method": method, "qualifier": "eq",
                "medical_time": medical_time, "sample_time_origin": "observation",
            }],
        )

    def test_source_resolve(self):
        result = self._temp_record("37.0")
        field = RecordField.objects.get(record_version_id=result["record_version_id"])
        resolved = source_resolve(patient=self.patient, record_version_id=result["record_version_id"], field_id=field.id)
        self.assertEqual(resolved["field_code"], "temp")
        self.assertEqual(resolved["record_version_id"], result["record_version_id"])

    def test_record_version_read(self):
        result = self._temp_record("37.0")
        read = record_version_read(patient=self.patient, record_version_id=result["record_version_id"])
        self.assertEqual(read["version"], 1)
        self.assertEqual(read["fields"][0]["raw_numeric"], "37.0")

    def test_record_search_and_cursor(self):
        for value in ["37.0", "38.0", "39.0"]:
            self._temp_record(value)
        page1 = record_search(patient=self.patient, kind="observation", limit=2)
        self.assertEqual(len(page1["records"]), 2)
        self.assertIsNotNone(page1["next_cursor"])
        page2 = record_search(patient=self.patient, kind="observation", cursor=page1["next_cursor"], limit=2)
        self.assertEqual(len(page2["records"]), 1)
        self.assertIsNone(page2["next_cursor"])

    def test_analyte_direction(self):
        self._temp_record("37.0", medical_time={"precision": "date", "local_date": "2026-09-01", 'timezone_name': 'Asia/Shanghai', "resolution_status": "local_only"})
        self._temp_record("38.0", medical_time={"precision": "date", "local_date": "2026-09-02", 'timezone_name': 'Asia/Shanghai', "resolution_status": "local_only"})
        self._temp_record("38.5", method=None, medical_time={"precision": "date", "local_date": "2026-09-03", 'timezone_name': 'Asia/Shanghai', "resolution_status": "local_only"})
        result = analyte_search(patient=self.patient, concept_code="temp")
        directions = [entry["direction"] for entry in result["trend"]]
        self.assertEqual(directions[0], None)
        self.assertEqual(directions[1], "up")
        self.assertIsNone(directions[2])
        self.assertEqual(result["trend"][2]["not_comparable_reason"], "unit_or_method_or_precision_mismatch")

    def test_history_search(self):
        self._temp_record("37.0", medical_time={"precision": "date", "local_date": "2026-09-01", "resolution_status": "local_only"})
        entries = history_search(patient=self.patient)
        self.assertEqual(len(entries["entries"]), 1)
        self.assertEqual(entries["entries"][0]["precision"], "date")
        self.assertEqual(entries["entries"][0]["field_count"], 1)

    def test_duplicate_time_blocks_comparison_on_both_sides(self):
        for date, value in [('2026-09-01', '37'), ('2026-09-02', '38'), ('2026-09-02', '39'), ('2026-09-03', '40')]:
            self._temp_record(value, medical_time={'precision': 'date', 'local_date': date,
                'timezone_name': 'Asia/Shanghai', 'resolution_status': 'local_only'})
        result = analyte_search(patient=self.patient, concept_code='temp')['trend']
        self.assertEqual([item['direction'] for item in result], [None, None, None, None])
        for item in result[1:]:
            self.assertIn('same_time', item['comparison_reasons'])
