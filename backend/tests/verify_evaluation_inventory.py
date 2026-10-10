import hashlib
import io
import json
import os
import unittest
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

django.setup()

from django.core.management import call_command
from case_details.evaluation_inventory import SourceManifest, inspect_evaluation_sources
from case_details.evaluation_bindings import required_file_bindings
from case_details.evaluation_evidence import dataset_input, require_raw_manifest_reference
from case_details.evaluation_datasets import operation_input
from case_details.evaluation_protocol import EvaluationLabels, EvaluationProtocol
from case_details.evaluation_units import EvaluationUnitManifest, EvaluationUnitsRejected, unit_manifest_view


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / 'demo-data/current-patient/source-manifest.json'
UNITS = ROOT / '.planning-work/evaluation-units-unrecorded.json'


class EvaluationInventoryTest(unittest.TestCase):
    def test_actual_source_files_match_manifest(self):
        manifest = json.loads(MANIFEST.read_text())
        result = inspect_evaluation_sources(MANIFEST)
        self.assertEqual(result['file_integrity'], 'verified')
        self.assertEqual(result['counts']['files'], 5)
        self.assertEqual(result['total_bytes'], sum(item['size_bytes'] for item in manifest['sources']))
        for source in manifest['sources']:
            content = (MANIFEST.parent / source['path']).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), source['sha256'])
        self.assertEqual(result['source_manifest_sha256'], hashlib.sha256(MANIFEST.read_bytes()).hexdigest())

    def test_missing_statistical_units_remain_unknown(self):
        result = inspect_evaluation_sources(MANIFEST)
        self.assertEqual(result['purpose_qualification'], 'not_evaluated')
        expected = {'pages', 'documents', 'patients', 'encounters', 'sampling_events', 'mas_events',
            'observation_periods', 'observation_person_hours', 'confirmed_labels', 'unknown_labels'}
        self.assertEqual(set(result['unrecorded_units']), expected)
        for unit in expected:
            self.assertIsNone(result['counts'][unit])
        self.assertEqual(result['identical_content_groups'], [])

    def test_absent_unit_mapping_is_explicitly_unqualified(self):
        result = inspect_evaluation_sources(MANIFEST)
        self.assertEqual(result['unit_mapping']['state'], 'unrecorded')
        self.assertEqual(result['unit_mapping']['qualification'], 'not_evaluated')
        self.assertIsNone(result['unit_mapping']['manifest_sha256'])
        self.assertEqual(result['unit_mapping']['declared_counts'], result['counts'])

    def test_source_inventory_cannot_supply_independent_unit_mapping(self):
        with self.assertRaises(ValidationError):
            inspect_evaluation_sources(MANIFEST, unit_manifest_path=MANIFEST)

    def test_command_requires_the_unit_mapping_format(self):
        with self.assertRaises(ValidationError):
            call_command('inspect_evaluation_sources', str(MANIFEST),
                unit_manifest=str(MANIFEST), stdout=io.StringIO())

    def test_actual_unrecorded_units_do_not_become_empty_denominators(self):
        original = inspect_evaluation_sources(MANIFEST)
        mapped = inspect_evaluation_sources(MANIFEST, unit_manifest_path=UNITS)
        self.assertEqual(mapped['unit_mapping']['state'], 'provided')
        self.assertEqual(mapped['unit_mapping']['qualification'], 'not_evaluated')
        self.assertEqual(mapped['unit_mapping']['declared_counts'], original['counts'])
        self.assertEqual(mapped['counts'], original['counts'])
        self.assertTrue(all(value is None for value in mapped['unit_mapping']['coverage'].values()))
        self.assertEqual(mapped['unit_mapping']['manifest_sha256'], hashlib.sha256(UNITS.read_bytes()).hexdigest())

    def test_absent_registered_file_bindings_remain_unknown(self):
        result = inspect_evaluation_sources(MANIFEST, unit_manifest_path=UNITS)
        self.assertIn('files', result['unit_mapping']['coverage'])
        self.assertIsNone(result['unit_mapping']['coverage']['files'])

    def test_unrecorded_file_bindings_cannot_supply_business_sources(self):
        inventory = inspect_evaluation_sources(MANIFEST)
        manifest = EvaluationUnitManifest.model_validate_json(UNITS.read_bytes())
        with self.assertRaisesRegex(EvaluationUnitsRejected, '^UNIT_FILE_BINDINGS_REQUIRED$'):
            required_file_bindings(inventory, manifest)

    def test_unregistered_raw_manifest_cannot_supply_approved_evidence(self):
        manifest = EvaluationUnitManifest.model_validate_json(UNITS.read_bytes())
        with self.assertRaisesRegex(EvaluationUnitsRejected, '^UNIT_RAW_MANIFEST_REFERENCE_REQUIRED$'):
            require_raw_manifest_reference(manifest)

    def test_local_source_inventory_cannot_supply_dataset_creation(self):
        with self.assertRaisesRegex(EvaluationUnitsRejected, '^DATASET_INPUT_INVALID$'):
            dataset_input(json.loads(MANIFEST.read_text()))

    def test_unrecorded_unit_mapping_cannot_supply_dataset_creation(self):
        with self.assertRaisesRegex(EvaluationUnitsRejected, '^DATASET_INPUT_INVALID$'):
            dataset_input(json.loads(UNITS.read_text()))

    def test_source_manifest_cannot_supply_fixed_protocol(self):
        with self.assertRaises(ValidationError):
            EvaluationProtocol.model_validate_json(MANIFEST.read_bytes())

    def test_unrecorded_mapping_cannot_supply_independent_labels(self):
        with self.assertRaises(ValidationError):
            EvaluationLabels.model_validate_json(UNITS.read_bytes())

    def test_local_source_inventory_cannot_supply_dataset_mutations(self):
        payload = json.loads(MANIFEST.read_text())
        for operation in ('dataset_create', 'dataset_partition', 'dataset_label', 'dataset_freeze'):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(EvaluationUnitsRejected, '^DATASET_INPUT_INVALID$'):
                    operation_input(operation, payload)

    def test_actual_mapping_command_preserves_unknown_counts(self):
        output = io.StringIO()
        call_command('inspect_evaluation_sources', str(MANIFEST), unit_manifest=str(UNITS), stdout=output)
        self.assertEqual(json.loads(output.getvalue()), inspect_evaluation_sources(MANIFEST, unit_manifest_path=UNITS))

    def test_source_file_checksum_cannot_replace_manifest_checksum(self):
        inventory = inspect_evaluation_sources(MANIFEST)
        manifest = EvaluationUnitManifest.model_validate_json(UNITS.read_bytes())
        wrong_binding = manifest.model_copy(update={'source_manifest_sha256': inventory['files'][0]['sha256']})
        with self.assertRaisesRegex(EvaluationUnitsRejected, '^UNIT_SOURCE_VERSION_MISMATCH$'):
            unit_manifest_view(inventory, wrong_binding, hashlib.sha256(UNITS.read_bytes()).hexdigest())

    def test_management_command_reports_actual_inventory(self):
        output = io.StringIO()
        call_command('inspect_evaluation_sources', str(MANIFEST), stdout=output)
        self.assertEqual(json.loads(output.getvalue()), inspect_evaluation_sources(MANIFEST))

    def test_actual_inventory_counts_match_dataset_protocol(self):
        protocol = json.loads((ROOT / 'backend-design/v2-protocol.schema.json').read_text())
        validator = Draft202012Validator({**protocol, '$ref': '#/$defs/DatasetCounts'},
            format_checker=FormatChecker())
        validator.validate(inspect_evaluation_sources(MANIFEST)['counts'])

    def test_project_documents_cannot_supply_source_inventory(self):
        documentation = ROOT / 'data-design/sample_dataset.json'
        with self.assertRaises(ValidationError):
            SourceManifest.model_validate_json(documentation.read_bytes())


if __name__ == '__main__':
    unittest.main(verbosity=2)
