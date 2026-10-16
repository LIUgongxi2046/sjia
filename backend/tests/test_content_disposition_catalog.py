"""Read-only T26 route/schema catalog parity; no database access."""
import csv
import json
import re
from pathlib import Path
from django.test import SimpleTestCase
from django.urls import resolve
from api.views_content_disposition import PlanRequest,ApprovalRequest,ExecuteRequest,ContentReviewRequest
from retention.content_schemas import ContentFamilyRequestInput,ContentFamilyRequestView,ContentFamilyOptions,ContentFamilyRequests
ROOT=Path(__file__).resolve().parents[2]
UUID='11111111-1111-4111-8111-111111111111'

class ContentDispositionCatalogTests(SimpleTestCase):
    def test_all_thirteen_routes_and_fifteen_methods_are_real_and_cataloged(self):
        for filename,prefix in [('api-catalog.csv','/api'),('v2-api-catalog.csv','/api/v2')]:
            with (ROOT/'backend-design'/filename).open() as f:
                rows=[row for row in csv.DictReader(f) if '/content-disposition/' in row['path']]
            self.assertEqual(len(rows),15)
            self.assertEqual(len({row['path'] for row in rows}),13)
            self.assertEqual(len({row['operation_id'] for row in rows}),15)
            for row in rows:
                route=resolve(prefix+re.sub(r'\{[^}]+\}',UUID,row['path']))
                self.assertIn(row['method'].lower(),route.func.cls.http_method_names)
                if row['caller']=='family':
                    self.assertNotIn('/approve',row['path']);self.assertNotIn('/execute',row['path'])
                    self.assertEqual(row['auth'],'request_disposition')
                else:self.assertEqual(row['auth'],'manage_disposition')
    def test_versioned_schemas_are_actual_runtime_models(self):
        for stem,model in [('preview-input',PlanRequest),('approval-input',ApprovalRequest),('execute-input',ExecuteRequest),
                ('review-input',ContentReviewRequest),('family-request',ContentFamilyRequestInput),
                ('family-view',ContentFamilyRequestView),('family-options',ContentFamilyOptions),('family-requests',ContentFamilyRequests)]:
            saved=json.loads((ROOT/f'backend-design/content-disposition-{stem}.schema.json').read_text())
            self.assertEqual(saved,model.model_json_schema(),stem)
    def test_execute_catalog_matches_durable_sync_service_instead_of_old_task_intent(self):
        with (ROOT/'backend-design/v2-api-catalog.csv').open() as f:rows={row['operation_id']:row for row in csv.DictReader(f)}
        execute=rows['content_disposition_execute']
        self.assertEqual(execute['success'],'200');self.assertEqual(execute['task_type'],'none')
        self.assertEqual(execute['idempotency'],'plan_journal');self.assertEqual(execute['read_operation'],'content_disposition_read')
        self.assertEqual(rows['family_content_disposition_requests_create']['idempotency'],'actor_client_key')
