"""本地端到端流程集成测试（虚拟数据）。

运行：backend/.venv/bin/python backend/manage.py test tests.integration.test_flow --keepdb
虚拟数据仅用于本地联调，不登记为真实验收。
"""
from django.core.management import call_command
from django.test import TestCase

from identity.models import Patient, Principal
from tests.integration.helpers import post_record


class FamilyFlowIntegrationTest(TestCase):
    def setUp(self):
        call_command("seed_demo", verbosity=0)
        self.principal = Principal.objects.filter(account_status="active").first()
        self.patient = Patient.objects.filter(display_id="demo-child").first()
        session = self.client.session
        session["_sjia_principal_id"] = str(self.principal.id)
        session["_sjia_auth_epoch"] = self.principal.auth_epoch
        session["_sjia_audience"] = "family"
        session.save()

    def test_bootstrap_and_session(self):
        response = self.client.get("/api/auth/bootstrap")
        self.assertEqual(response.status_code, 200)
        response = self.client.get("/api/session")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["principal_id"], str(self.principal.id))

    def test_relation_list(self):
        response = self.client.get("/api/relations")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["relations"]), 1)
        self.assertEqual(response.json()["relations"][0]["patient_id"], str(self.patient.id))

    def test_field_config_read(self):
        response = self.client.get(f"/api/patients/{self.patient.id}/field-config?kind=observation")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["state"], "available")
        self.assertEqual([f["field_code"] for f in body["fields"]], ["temp", "rash"])

    def test_record_create_and_read(self):
        payload = {
            "kind": "observation", "capture_method": "manual",
            "fields": [
                {"field_code": "temp", "value_type": "decimal", "value_state": "known", "raw_numeric": "38.0", "raw_unit": "C", "sample_time_origin": "observation"},
            ],
        }
        response = post_record(self.client, self.patient, payload)
        self.assertEqual(response.status_code, 201)
        record_id = response.json()["record_id"]
        read_response = self.client.get(f"/api/records/{record_id}")
        self.assertEqual(read_response.status_code, 200)
        self.assertEqual(read_response.json()["fields"][0]["raw_numeric"], "38.0")

    def test_record_read_denied_without_session(self):
        from records.service import create_record
        from retention.models import RetentionPolicy
        from sources.models import DataUseAuthorization

        retention = RetentionPolicy.objects.filter(status="active").first()
        use_auth = DataUseAuthorization.objects.filter(patient=self.patient).first()
        result = create_record(patient=self.patient, actor=self.principal, kind="observation", capture_method="manual", origin_type="self_report", retention_policy=retention, use_authorization_id=use_auth.id, fields=[{"field_code": "temp", "value_type": "decimal", "value_state": "known", "raw_numeric": "38.0", "raw_unit": "C", "sample_time_origin": "observation"}])
        anonymous = self.client
        anonymous.session.flush()
        response = anonymous.get(f"/api/records/{result['record_id']}")
        self.assertEqual(response.status_code, 401)

    def test_home_read(self):
        response = self.client.get(f"/api/patients/{self.patient.id}/home")
        self.assertEqual(response.status_code, 200)
        self.assertIn("assessment_state", response.json())
