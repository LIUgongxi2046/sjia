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
        from tests.synthetic_fixtures import qualify_demo
        qualify_demo(self, self.principal, self.patient)
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

    def test_summary_submit_http_and_service_projection(self):
        activate_summary_configs(self)
        created = post_record(self.client, self.patient, {'kind': 'observation', 'capture_method': 'manual', 'fields': [{'field_code': 'temp', 'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '38.0', 'raw_unit': 'C', 'medical_time': {'precision': 'date', 'local_date': '2026-09-27', 'resolution_status': 'local_only'}, 'sample_time_origin': 'observation'}]})
        self.assertEqual(created.status_code, 201)
        from tests.integration.helpers import post_intended
        from summaries.service import list_summaries, read_summary
        response = post_intended(self.client, f'/api/patients/{self.patient.id}/summaries', 'summary_create',
            {'from_date': '2026-09-01', 'to_date': '2026-09-30', 'scope_timezone': 'Asia/Shanghai',
                'include_unknown_time': False, 'record_kinds': ['observation'], 'include_source_status': True})
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(response.json()['status'], 'accepted')
        from tasks.models import Task, OutboxEvent
        from summaries.jobs import execute_summary
        from summaries.models import Summary
        task = Task.objects.get(id=response.json()['task_id'])
        execute_summary(OutboxEvent.objects.get(task=task, event_type='summary.requested'), 'synthetic-summary-worker')
        task.refresh_from_db()
        self.assertEqual(task.status, 'completed')
        summary_id = Summary.objects.get(task_id=task.id).id
        self.patient.refresh_from_db()
        # Snapshot HTTP reads are covered in TransactionTestCase; this class
        # keeps submission+content checks without mocking transaction SQL.
        self.assertEqual(len(list_summaries(self.patient)['summaries']), 1)
        content = read_summary(summary_id, self.patient)
        self.assertFalse(content['needs_regeneration'])
        self.assertEqual(content['items'][0]['snapshot']['field']['raw_numeric'], '38.0')

    def test_intent_flow(self):
        from tests.synthetic_fixtures import current_record_payload
        from uuid import uuid4
        target = f'/api/patients/{self.patient.id}/records'
        request_id = str(uuid4())
        payload = current_record_payload(self.patient, {'kind': 'observation', 'capture_method': 'manual',
            'fields': [{'field_code': 'temp', 'value_type': 'decimal', 'value_state': 'known',
                'raw_numeric': '38.0', 'raw_unit': 'C', 'sample_time_origin': 'observation'}]})
        response = self.client.post('/api/write-intents', {'operation_id': 'record_create', 'target_key': target,
            'request_id': request_id, 'payload': payload, 'schema_version': 'back-json-1'}, content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        intent_id = response.json()['intent_id']
        query_response = self.client.get(f'/api/requests/{intent_id}')
        self.assertEqual(query_response.status_code, 200, query_response.content)
        saved = query_response.json()
        self.assertEqual(saved['status'], 'prepared')
        self.assertEqual(saved['patient_id'], str(self.patient.id))
        self.assertEqual(saved['target_key'], target)
        self.assertEqual(saved['request_id'], request_id)
        self.assertEqual(saved['record_input'], payload)
        self.assertIsNone(saved['receipt'])

    def test_config_create_and_approve(self):
        from tests.integration.helpers import create_and_approve_synthetic_config
        create_and_approve_synthetic_config(self, 'synthetic-flow2')


    def test_history_and_analyte(self):
        from unittest.mock import patch
        # Permission/wire check only; genuine isolation tested separately.
        with patch('records.history.begin_history_snapshot'):
            history_response = self.client.post(f"/api/patients/{self.patient.id}/history/search", {}, content_type="application/json")
        self.assertEqual(history_response.status_code, 200)
        with patch('records.history.begin_history_snapshot'):
            analyte_response = self.client.post(f"/api/patients/{self.patient.id}/analytes/temp/search", {}, content_type="application/json")
        self.assertEqual(analyte_response.status_code, 200)
