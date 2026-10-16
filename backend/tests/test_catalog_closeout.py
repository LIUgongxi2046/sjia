"""Current catalog routes resolve without invoking APIs or touching patient data."""
import csv
import importlib.util
import json
import re
from pathlib import Path

from django.test import SimpleTestCase
from django.urls import Resolver404, resolve
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
UUID = '11111111-1111-4111-8111-111111111111'


def rows(name):
    with (ROOT / name).open(newline='') as stream:
        return list(csv.DictReader(stream))


class CatalogCloseoutTests(SimpleTestCase):
    def test_every_catalog_path_and_method_resolves(self):
        for name, prefix in [('backend-design/api-catalog.csv', '/api'),
                             ('backend-design/v2-api-catalog.csv', '/api/v2')]:
            catalog = rows(name)
            self.assertEqual(len(catalog), len({row['operation_id'] for row in catalog}))
            for row in catalog:
                with self.subTest(catalog=name, operation=row['operation_id']):
                    path = prefix + re.sub(r'\{[^}]+\}', UUID, row['path'])
                    match = resolve(path)
                    self.assertIn(row['method'].lower(), match.func.cls.http_method_names)

    def test_changed_routes_use_actual_handlers_and_status_contracts(self):
        catalog = {row['operation_id']: row for row in rows('backend-design/v2-api-catalog.csv')}
        expected = {
            'archive_relation_search': ('archive_relation_search', 'GET', '200'),
            'archive_relation_update': ('archive_relation_update', 'POST', '200'),
            'notification_scopes': ('notification_case_directory', 'GET', '200'),
            'notification_support_history': ('support_notification_history', 'GET', '200'),
            'notification_support_record': ('support_notification_record', 'GET', '200'),
            'notification_v2_send': ('notification_send', 'POST', '201'),
        }
        for operation, (handler, method, success) in expected.items():
            row = catalog[operation]
            match = resolve('/api/v2' + re.sub(r'\{[^}]+\}', UUID, row['path']))
            self.assertEqual(match.func.cls.__name__, handler)
            self.assertEqual((row['method'], row['success']), (method, success))

    def test_notification_replacement_does_not_alias_resolve_to_send(self):
        catalog = {row['operation_id']: row for row in rows('backend-design/v2-api-catalog.csv')}
        self.assertTrue({'notification_search', 'notification_read', 'notification_resolve'}.isdisjoint(catalog))
        for path in ['/support/deliveries/search', f'/support/deliveries/{UUID}',
                     f'/support/deliveries/{UUID}/resolve']:
            with self.assertRaises(Resolver404):
                resolve('/api/v2' + path)
        row = next(row for row in rows('frontend-design/v2-routes.csv') if row['screen_id'] == 'UI2-39')
        self.assertEqual(row['container'], 'NotificationPage')
        operations = {r['operation_id'] for r in rows('backend-design/api-catalog.csv')} | catalog.keys()
        for field in ('read_operations', 'write_operations'):
            self.assertLessEqual(set(row[field].split(';')), operations)
        self.assertNotIn('notification_resolve', row['write_operations'])

    def test_v2_catalog_schema_refs_point_to_real_definitions(self):
        specification = importlib.util.spec_from_file_location('catalog_checker', ROOT / 'backend-design/check_v2.py')
        checker = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(checker)
        definitions = json.loads((ROOT / 'backend-design/v2-protocol.schema.json').read_text())['$defs']
        for row in rows('backend-design/v2-api-catalog.csv'):
            for key in ('input_schema', 'output_schema'):
                value = row[key]
                with self.subTest(operation=row['operation_id'], key=key):
                    path, schema = checker.catalog_schema(value, definitions)
                    self.assertTrue(path.is_relative_to(ROOT))
                    self.assertIsInstance(schema, (dict, bool))

    def test_notification_query_does_not_advertise_unsupported_filters(self):
        from notifications.reading import DeliveryQuery

        schema = json.loads((ROOT / 'backend-design/notification.schema.json').read_text())['$defs']['Query']
        validator = Draft202012Validator(schema)
        for payload in ({}, {'cursor': None}, {'cursor': 'signed-cursor'}):
            validator.validate(payload)
            DeliveryQuery.model_validate(payload)
        for payload in ({'limit': 20}, {'patient_id': UUID}, {'status': 'delivered'}, {'cursor': ''}):
            self.assertFalse(validator.is_valid(payload))
            with self.assertRaises(ValueError):
                DeliveryQuery.model_validate(payload)

    def test_catalog_schema_resolution_cannot_escape_repo(self):
        specification = importlib.util.spec_from_file_location('catalog_checker', ROOT / 'backend-design/check_v2.py')
        checker = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(checker)
        for reference in ('../../outside.schema.json', '/tmp/outside.schema.json',
                          'https://example.invalid/schema.schema.json'):
            with self.assertRaises((ValueError, FileNotFoundError)):
                checker.catalog_schema(reference, {})

    def test_reconciliation_catalog_uses_its_actual_custom_response(self):
        catalog = {row['operation_id']: row for row in rows('backend-design/api-catalog.csv')}
        write, read = catalog['task_reconcile'], catalog['task_reconcile_read']
        self.assertEqual((write['success'], read['success']), ('202', '200'))
        self.assertEqual(write['auth'], 'reconcile_task')
        self.assertEqual(read['auth'], 'reconcile_task')
        expected = 'backend/tasks/reconciliation.schema.json#/$defs/ReconciliationTaskView'
        self.assertEqual(write['output_schema'], expected)
        self.assertEqual(read['output_schema'], expected)
        schema = json.loads((ROOT / 'backend/tasks/reconciliation.schema.json').read_text())
        Draft202012Validator.check_schema(schema)
        for operation in ('task_reconcile', 'task_reconcile_read'):
            route = resolve('/api' + catalog[operation]['path'].replace('{task_id}', UUID))
            self.assertEqual(route.url_name, operation)

    def test_reconciliation_input_schema_matches_admission(self):
        from tasks.reconciliation import ReconcileInput

        schema = json.loads((ROOT / 'backend/tasks/reconciliation.schema.json').read_text())['$defs']['ReconcileInput']
        validator = Draft202012Validator(schema)
        valid = {'expected_status_version': '2', 'expected_lease_generation': '0'}
        validator.validate(valid)
        self.assertEqual(ReconcileInput.model_validate(valid).model_dump(), valid)
        for invalid in ({}, {**valid, 'expected_status_version': 2},
                        {**valid, 'expected_lease_generation': '-1'},
                        {**valid, 'retry_provider': True}):
            self.assertFalse(validator.is_valid(invalid))
            with self.assertRaises(ValueError):
                ReconcileInput.model_validate(invalid)
