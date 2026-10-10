"""本地端到端流程集成测试二：草稿、摘要、意图与受控支持（虚拟数据）。"""
from django.core.management import call_command
from django.test import TestCase

from identity.models import Patient, Principal
from tests.integration.helpers import activate_summary_configs, post_record


class FamilyFlow2IntegrationTest(TestCase):
    def setUp(self):
        call_command("seed_demo", verbosity=0)
        self.principal = Principal.objects.filter(account_status="active").first()
        self.patient = Patient.objects.filter(display_id="demo-child").first()
        session = self.client.session
        session["_sjia_principal_id"] = str(self.principal.id)
        session["_sjia_auth_epoch"] = self.principal.auth_epoch
        session["_sjia_audience"] = "family"
        session.save()

    def test_draft_flow(self):
        response = self.client.post(f"/api/patients/{self.patient.id}/drafts", {"form_type": "observation", "payload": {"temp": "38.0"}}, content_type="application/json")
        self.assertEqual(response.status_code, 201)
        draft_id = response.json()["draft_id"]
        list_response = self.client.get(f"/api/patients/{self.patient.id}/drafts")
        self.assertEqual(len(list_response.json()["drafts"]), 1)
        update_response = self.client.put(f"/api/drafts/{draft_id}", {"expected_version": 1, "payload": {"temp": "38.5"}}, content_type="application/json")
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()["version"], "2")

    def test_summary_flow(self):
        activate_summary_configs(self.principal)
        created = post_record(self.client, self.patient, {'kind': 'observation', 'capture_method': 'manual', 'fields': [{'field_code': 'temp', 'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '38.0', 'raw_unit': 'C', 'medical_time': {'precision': 'date', 'local_date': '2026-09-27', 'resolution_status': 'local_only'}, 'sample_time_origin': 'observation'}]})
        self.assertEqual(created.status_code, 201)
        response = self.client.post(f"/api/patients/{self.patient.id}/summaries", {"from_date": "2026-09-01", "to_date": "2026-09-30", 'scope_timezone': 'Asia/Shanghai'}, content_type="application/json")
        self.assertEqual(response.status_code, 201)
        summary_id = response.json()["summary_id"]
        list_response = self.client.get(f"/api/patients/{self.patient.id}/summaries")
        self.assertEqual(len(list_response.json()["summaries"]), 1)
        read_response = self.client.get(f"/api/summaries/{summary_id}")
        self.assertEqual(read_response.status_code, 200)
        self.assertEqual(read_response.json()['items'][0]['snapshot']['raw_numeric'], '38.0')

    def test_intent_flow(self):
        response = self.client.post("/api/write-intents", {"operation_id": "record_create", "target_key": "/records", "request_id": "12345678-1234-5678-1234-567812345678", "payload": {"kind": "observation"}, "schema_version": "back-json-1"}, content_type="application/json")
        self.assertEqual(response.status_code, 201)
        intent_id = response.json()["intent_id"]
        query_response = self.client.get(f"/api/requests/{intent_id}")
        self.assertEqual(query_response.status_code, 200)
        self.assertEqual(query_response.json()["status"], "prepared")

    def test_config_create_and_approve(self):
        create_response = self.client.post("/api/support/configs", {"kind": "rule", "config_key": "r-demo", "schema_version": "rule-1", "payload": {"x": 1}}, content_type="application/json")
        self.assertEqual(create_response.status_code, 201)
        config_id = create_response.json()["config_id"]
        approve_response = self.client.post(f"/api/support/configs/{config_id}/approvals", {"approval_role": "clinical", "evidence_ref": "demo", "effective_scope": {}}, content_type="application/json")
        self.assertEqual(approve_response.status_code, 200)
        self.assertEqual(approve_response.json()["status"], "approved")

    def test_history_and_analyte(self):
        history_response = self.client.post(f"/api/patients/{self.patient.id}/history/search", {}, content_type="application/json")
        self.assertEqual(history_response.status_code, 200)
        analyte_response = self.client.post(f"/api/patients/{self.patient.id}/analytes/temp/search", {}, content_type="application/json")
        self.assertEqual(analyte_response.status_code, 200)
