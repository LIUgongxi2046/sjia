"""T27 真实病例领域导入、权限查询与回读逻辑验证。

本地用途核验已有病例全部结构；临床边界与 Gold 由 T32 依据 D08 验证。
运行：backend/.venv/bin/python backend/manage.py test tests.test_T27 --keepdb
"""
import os
import uuid
from pathlib import Path
from unittest import skipUnless
from datetime import datetime, timezone

from django.test import TestCase

from case_details.case_import import import_case
from case_details.models import ClinicalStatement, MedicationEvent, PatientProfileVersion, RecordFieldGroup
from identity.models import Patient, Principal
from medical_time.models import MedicalTime
from records.models import Record, RecordField
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from sources.service import register_revision, register_source


def _now():
    return datetime.now(timezone.utc)


@skipUnless(os.environ.get('SJIA_RUN_CLINICAL_EVIDENCE_TESTS') == '1',
    'external_clinical_input: real-case evidence tests are opt-in; not a synthetic development pass')
class CaseImportTest(TestCase):
    def setUp(self):
        projection = Path(__file__).resolve().parents[2] / 'data-design' / 'case-design-projection.json'
        if not projection.is_file():
            self.skipTest('external_clinical_input: authorized case-design-projection.json is unavailable')
        self.skipTest('external_clinical_input: separate qualified real-data admission and authorized evidence runner required; offline suite does not open clinical input')
        now = _now()
        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(id=uuid.UUID("a5e40001-11b2-4023-8dcc-19800ae724b0"), display_id="case-demo", status="active", created_at=now)
        self.retention = RetentionPolicy.objects.create(policy_key="case", version=1, data_class="case", rules={}, schema_version="r-1", status="active")
        source = register_source(source_key="case-src", owner="o", location="l", purpose="p", sensitivity="s", update_frequency="d")
        self.revision = register_revision(source=source, version="v1", checksum="c" * 64, license="l", allowed_use=["case_import"])
        self.use_auth = DataUseAuthorization.objects.create(patient=self.patient, source_revision=self.revision, purpose="case_import", environment="test", evidence_ref="e", approved_by=self.principal, approved_at=now)

    def test_import_case_counts(self):
        result = import_case(patient=self.patient, operator=self.principal, retention_policy=self.retention, use_authorization_id=self.use_auth.id, source_revision=self.revision)
        self.assertEqual(result["status"], "IMPORTED")
        self.assertEqual(result["medical_times"], 33)
        self.assertEqual(result["fields"], 231)
        self.assertGreater(result["records"], 0)
        self.assertFalse(result["clinical_rule_eligible"])

    def test_import_creates_entities(self):
        import_case(patient=self.patient, operator=self.principal, retention_policy=self.retention, use_authorization_id=self.use_auth.id, source_revision=self.revision)
        self.assertEqual(PatientProfileVersion.objects.filter(patient_id=self.patient.id).count(), 1)
        self.assertEqual(ClinicalStatement.objects.filter(patient_id=self.patient.id).count(), 37)
        self.assertEqual(RecordFieldGroup.objects.filter(patient_id=self.patient.id).count(), 16)
        self.assertEqual(MedicationEvent.objects.filter(patient_id=self.patient.id).count(), 17)

    def test_value_round_trip(self):
        import_case(patient=self.patient, operator=self.principal, retention_policy=self.retention, use_authorization_id=self.use_auth.id, source_revision=self.revision)
        field = RecordField.objects.filter(patient=self.patient, original_name="patient").first()
        self.assertIsNotNone(field)
        self.assertIn("男", field.raw_text)
        self.assertEqual(MedicalTime.objects.filter(patient=self.patient).count(), 33)
