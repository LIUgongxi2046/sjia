"""本地端到端流程集成测试三：文件、评估、字典、更正、处置与病例（虚拟数据）。"""
import hashlib
import io
import uuid
from datetime import datetime, timezone

from django.core.management import call_command
from django.test import TestCase
from PIL import Image

from identity.models import Patient, Principal
from tests.integration.helpers import post_record


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (10, 20), "white").save(buf, format="PNG")
    return buf.getvalue()


class FamilyFlow3IntegrationTest(TestCase):
    def setUp(self):
        call_command("seed_demo", verbosity=0)
        self.principal = Principal.objects.filter(account_status="active").first()
        self.patient = Patient.objects.filter(display_id="demo-child").first()
        session = self.client.session
        session["_sjia_principal_id"] = str(self.principal.id)
        session["_sjia_auth_epoch"] = self.principal.auth_epoch
        session["_sjia_audience"] = "family"
        session.save()

    def test_file_upload_and_content(self):
        data = _png()
        checksum = hashlib.sha256(data).hexdigest()
        upload_id = str(uuid.uuid4())
        group_response = self.client.post(f"/api/patients/{self.patient.id}/uploads", {}, content_type="application/json")
        self.assertEqual(group_response.status_code, 201)
        group_id = group_response.json()["group_id"]
        upload_response = self.client.put(f"/api/uploads/{group_id}/files/{upload_id}?checksum={checksum}&size={len(data)}&media_type=image/png", data, content_type="application/octet-stream")
        self.assertEqual(upload_response.status_code, 201)
        file_id = upload_response.json()["file_id"]
        complete_response = self.client.post(f"/api/uploads/{group_id}/complete", {}, content_type="application/json")
        self.assertEqual(complete_response.status_code, 200)
        content_response = self.client.get(f"/api/files/{file_id}/content")
        self.assertEqual(content_response.status_code, 200)
        self.assertEqual(content_response.content, data)
        range_response = self.client.get(f"/api/files/{file_id}/content", HTTP_RANGE="bytes=0-4")
        self.assertEqual(range_response.status_code, 206)
        self.assertEqual(len(range_response.content), 5)

    def test_assessment_classification(self):
        response = self.client.post(f"/api/support/patients/{self.patient.id}/assessments", {"kind": "classification", "premise_met": True, "ferritin": "700", "platelet": "100", "ast": "50", "triglycerides": "200", "fibrinogen": "300"}, content_type="application/json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["result"]["classification_result"], "meets")

    def test_dictionary_search(self):
        response = self.client.post("/api/dictionaries/search", {"query": "体温"}, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["matches"]), 1)
        self.assertEqual(response.json()["matches"][0]["concept_code"], "temp")

    def test_correction_request_and_disposition(self):
        record_response = post_record(self.client, self.patient, {"kind": "observation", "capture_method": "manual", "fields": [{"field_code": "temp", "value_type": "decimal", "value_state": "known", "raw_numeric": "38.0", "raw_unit": "C", "sample_time_origin": "observation"}]})
        self.assertEqual(record_response.status_code, 201)
        record_id = record_response.json()["record_id"]
        correction_response = self.client.post(f"/api/records/{record_id}/correction-requests", {"field_ids": [], "reason": "更正"}, content_type="application/json")
        self.assertEqual(correction_response.status_code, 201)
        disposition_response = self.client.post(f"/api/patients/{self.patient.id}/dispositions", {"scope": {"targets": []}}, content_type="application/json")
        self.assertEqual(disposition_response.status_code, 201)

    def test_case_import(self):
        response = self.client.post(f"/api/support/patients/{self.patient.id}/case/import", {}, content_type="application/json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "IMPORTED")
        self.assertFalse(response.json()["clinical_rule_eligible"])
