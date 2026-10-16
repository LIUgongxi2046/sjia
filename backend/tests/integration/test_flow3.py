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
        from tests.synthetic_fixtures import qualify_demo
        qualify_demo(self, self.principal, self.patient)
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
        from tests.integration.helpers import complete_synthetic_upload
        complete_response = complete_synthetic_upload(self, group_id)
        self.assertEqual(complete_response.json()['status'], 'available')
        content_response = self.client.get(f"/api/files/{file_id}/content")
        self.assertEqual(content_response.status_code, 200)
        self.assertEqual(content_response.content, data)
        range_response = self.client.get(f"/api/files/{file_id}/content", HTTP_RANGE="bytes=0-4")
        self.assertEqual(range_response.status_code, 206)
        self.assertEqual(len(range_response.content), 5)

    def test_legacy_assessment_requires_v2(self):
        from tests.integration.helpers import support_session
        support_session(self.client)
        response = self.client.post(f'/api/support/patients/{self.patient.id}/assessments',
            {'kind': 'classification'}, content_type='application/json')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'ASSESSMENT_API_VERSION_REQUIRED')


    def test_dictionary_search(self):
        from uuid import uuid4
        self.synthetic.configuration('dictionary', {'schema_version': 'dictionary-1',
            'entries': [{'id': str(uuid4()), 'concept_code': 'temp', 'category': 'vital', 'label': '体温',
                'definition_ref': self.synthetic.reference, 'aliases': []}],
            'evidence_refs': [self.synthetic.reference]})
        response = self.client.post('/api/dictionaries/search', {'category': 'vital', 'query': '体温'}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['match_state'], 'unique')
        self.assertEqual(len(response.json()['options']), 1)
        self.assertEqual(response.json()['options'][0]['concept_code'], 'temp')


    def test_version_bound_correction_request(self):
        record_response = post_record(self.client, self.patient, {"kind": "observation", "capture_method": "manual", "fields": [{"field_code": "temp", "value_type": "decimal", "value_state": "known", "raw_numeric": "38.0", "raw_unit": "C", "sample_time_origin": "observation"}]})
        self.assertEqual(record_response.status_code, 201, record_response.content)
        from records.models import RecordField
        record_id = record_response.json()['record_id']
        version_id = record_response.json()['record_version_id']
        field = RecordField.objects.get(record_version_id=version_id)
        from tests.integration.helpers import post_intended
        response = post_intended(self.client, f'/api/records/{record_id}/correction-requests', 'correction_request_create',
            {'record_version_id': version_id, 'field_ids': [str(field.id)], 'reason': 'synthetic correction request',
                'proposed_text': None})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['resource_type'], 'correction_request')
        self.assertEqual(response.json()['version'], '1')
        read = self.client.get(f"/api/correction-requests/{response.json()['resource_id']}")
        self.assertEqual(read.status_code, 200, read.content)
        self.assertEqual(read.json()['status'], 'pending')
        self.assertEqual(read.json()['request']['field_ids'], [str(field.id)])
        self.assertEqual(read.json()['request']['record_version_id'], version_id)


    def test_family_cannot_import_external_case(self):
        response = self.client.post(f'/api/support/patients/{self.patient.id}/case/import', {}, content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'SUPPORT_AUDIENCE_REQUIRED')
