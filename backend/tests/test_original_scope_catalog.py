"""Read-only catalog checks for the original-requirement additions; no patient DB."""
import csv
import json
from pathlib import Path

from django.test import SimpleTestCase
from django.urls import resolve
from jsonschema import Draft202012Validator

from alerting.preset_policies import PresetDirectoryView, PresetSelectionInput


ROOT = Path(__file__).resolve().parents[2]
UUID = '11111111-1111-4111-8111-111111111111'


def rows(path):
    with (ROOT / path).open(newline='') as stream:
        return list(csv.DictReader(stream))


class OriginalScopeCatalogTests(SimpleTestCase):
    def test_preset_catalog_resolves_actual_operation_and_method(self):
        catalog = {row['operation_id']: row for row in rows('backend-design/api-catalog.csv')}
        for operation, method in [('alert_preset_directory', 'GET'), ('alert_preset_select', 'POST')]:
            with self.subTest(operation=operation):
                row = catalog[operation]
                match = resolve('/api' + row['path'].replace('{patient_id}', UUID))
                self.assertEqual(match.url_name, operation)
                self.assertEqual(match.func.cls.__name__, operation)
                self.assertEqual(row['method'], method)
                self.assertIn(method.lower(), match.func.cls.http_method_names)
                self.assertEqual(row['success'], '200')
                self.assertEqual(row['auth'], 'select_alert_preset')

    def test_preset_schema_files_match_the_runtime_models(self):
        for filename, model in [('alert-preset-view.schema.json', PresetDirectoryView),
                                ('alert-preset-selection.schema.json', PresetSelectionInput)]:
            with self.subTest(filename=filename):
                schema = json.loads((ROOT / 'backend-design' / filename).read_text())
                Draft202012Validator.check_schema(schema)
                schema.pop('$schema', None)
                self.assertEqual(schema, model.model_json_schema())

    def test_selection_does_not_advertise_guardian_medical_edits(self):
        schema = json.loads((ROOT / 'backend-design/alert-preset-selection.schema.json').read_text())
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(set(schema['properties']), {
            'assignment_id', 'assignment_checksum', 'expected_version', 'schedule_config_id'})
        payload = {'assignment_id': UUID, 'assignment_checksum': 'a' * 64,
                   'expected_version': '0', 'schedule_config_id': UUID}
        validator = Draft202012Validator(schema)
        self.assertTrue(validator.is_valid(payload))
        for extra in ('threshold', 'weights', 'core_indicators', 'purpose', 'patient_id'):
            self.assertFalse(validator.is_valid({**payload, extra: 'unapproved'}))

    def test_supplemental_route_has_real_component_and_catalog_operations(self):
        supplements = rows('frontend-design/original-scope-routes.csv')
        self.assertEqual(len({row['screen_id'] for row in supplements}), len(supplements))
        self.assertEqual(len({row['path'] for row in supplements}), len(supplements))
        entry = next(row for row in supplements if row['path'] == '/alert-presets')
        self.assertEqual(entry['container'], 'AlertPresetsPage')
        self.assertIn('select_alert_preset', entry['guard'].split(';'))
        app = (ROOT / 'guardian-h5/src/app/App.tsx').read_text()
        self.assertIn('path="alert-presets"', app)
        self.assertTrue((ROOT / 'guardian-h5/src/features/account/AlertPresetsPage.tsx').is_file())
        known = {row['operation_id'] for row in rows('backend-design/api-catalog.csv')}
        for column in ('read_operations', 'write_operations'):
            self.assertLessEqual(set(entry[column].split(';')), known)
        historical = {row['screen_id'] for row in rows('frontend-design/v2-routes.csv')}
        self.assertNotIn(entry['screen_id'], historical)

    def test_hlh_supplement_documents_current_v2_not_disabled_v1(self):
        text = (ROOT / 'backend-design/original-scope-api.md').read_text()
        self.assertIn('API v2', text)
        self.assertIn('旧 v1 assessment handler 明确拒绝', text)
        for filename in ('hlh-input-policy.schema.json', 'hlh-2004-rule.schema.json',
                         'hlh-2004-inputs.schema.json', 'hlh-2004-result.schema.json'):
            self.assertIn(filename, text)
            Draft202012Validator.check_schema(json.loads((ROOT / 'backend-design' / filename).read_text()))
