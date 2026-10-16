"""Offline regression of design schema graph resolution and its repository boundary."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from referencing.exceptions import Unresolvable

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('backend_design_registry', ROOT / 'backend-design/check_v2.py')
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)
DRAFT = 'https://json-schema.org/draft/2020-12/schema'


class LocalDesignSchemaRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'repo'
        self.root.mkdir()

    def schema(self, name, **body):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'$schema': DRAFT, **body}))
        return path

    def load(self, *paths):
        with patch('socket.socket', side_effect=AssertionError('Network forbidden')):
            return checker.local_schema_registry(paths, root=self.root)

    def test_relative_transitive_graph_and_cycle_are_registered(self):
        first = self.schema('api/first.schema.json', **{'$ref': '../data/second.schema.json#/$defs/value'})
        second = self.schema('data/second.schema.json', **{'$defs': {'value': {'$ref': 'third.schema.json'}}})
        third = self.schema('data/third.schema.json', **{'type': 'string', 'not': {'$ref': '../api/first.schema.json'}})
        _, schemas, count = self.load(first)
        self.assertEqual(set(schemas), {first, second, third})
        self.assertEqual(count, 3)

    def test_nested_local_id_has_its_own_resolution_base(self):
        first = self.schema('first.schema.json', **{'$defs': {'nested': {'$id': 'nested/alias.schema.json', '$ref': 'target.schema.json'}}})
        target = self.schema('nested/target.schema.json', type='string')
        _, schemas, count = self.load(first)
        self.assertEqual(set(schemas), {first, target})
        self.assertEqual(count, 1)

    def test_known_local_urn_identity_resolves_without_network(self):
        first = self.schema('first.schema.json', **{'$ref': 'urn:synthetic:local#/$defs/value'})
        second = self.schema('second.schema.json', **{'$id': 'urn:synthetic:local', '$defs': {'value': {'type': 'string'}}})
        _, schemas, count = self.load(first, second)
        self.assertEqual(len(schemas), 2)
        self.assertEqual(count, 1)

    def test_unknown_external_reference_is_not_fetched_or_skipped(self):
        for reference in ('https://example.invalid/missing.schema.json', 'urn:unknown:synthetic'):
            first = self.schema('first.schema.json', **{'$ref': reference})
            with self.subTest(reference=reference), self.assertRaises(Unresolvable):
                self.load(first)

    def test_missing_local_file_and_pointer_fail(self):
        first = self.schema('first.schema.json', **{'$ref': 'missing.schema.json'})
        with self.assertRaises(FileNotFoundError):
            self.load(first)
        self.schema('missing.schema.json', type='string')
        first = self.schema('first.schema.json', **{'$ref': 'missing.schema.json#/$defs/absent'})
        with self.assertRaises(Unresolvable):
            self.load(first)

    def test_plain_encoded_absolute_and_symlink_escapes_fail_closed(self):
        outside = self.root.parent / 'outside.schema.json'
        outside.write_text(json.dumps({'$schema': DRAFT, 'type': 'string'}))
        link = self.root / 'link.schema.json'
        link.symlink_to(outside)
        for reference in ('../outside.schema.json', '%2e%2e/outside.schema.json', outside.as_uri(),
                          'link.schema.json', 'file://example.invalid/outside.schema.json'):
            first = self.schema('first.schema.json', **{'$ref': reference})
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                self.load(first)

    def test_query_and_non_schema_targets_are_rejected(self):
        for reference in ('other.schema.json?mode=read', 'other.txt'):
            first = self.schema('first.schema.json', **{'$ref': reference})
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                self.load(first)

    def test_duplicate_declared_ids_are_not_silently_overwritten(self):
        first = self.schema('first.schema.json', **{'$id': 'urn:synthetic:duplicate'})
        second = self.schema('second.schema.json', **{'$id': 'urn:synthetic:duplicate'})
        with self.assertRaisesRegex(ValueError, '多个本地来源'):
            self.load(first, second)

    def test_actual_v2_graph_includes_previously_missing_transitive_schemas(self):
        with patch('socket.socket', side_effect=AssertionError('Network forbidden')):
            _, schemas, count = checker.local_schema_registry([ROOT / 'backend-design/v2-protocol.schema.json'])
        self.assertIn(ROOT / 'alert-design/v2-recognition.schema.json', schemas)
        self.assertIn(ROOT / 'backend-design/archive-delta.schema.json', schemas)
        self.assertIn(ROOT / 'backend-design/observation-period.schema.json', schemas)
        self.assertGreater(count, 500)

    def test_original_source_uses_exact_registered_record_schema_alias(self):
        original = ROOT / 'backend/files/original-read.schema.json'
        with patch('socket.socket', side_effect=AssertionError('Network forbidden')):
            registry, schemas, _ = checker.local_schema_registry([original])
        target = ROOT / 'backend/records/search.schema.json'
        self.assertIn(target, schemas)
        resolved = registry.resolver(original.as_uri()).lookup('record-search.schema.json#/$defs/MedicalTimeView')
        self.assertEqual(resolved.contents, schemas[target]['$defs']['MedicalTimeView'])

    def test_catalog_action_list_requires_every_exact_registered_token(self):
        known = {'verify_field', 'execute_assessment'}
        self.assertEqual(checker.catalog_actions('verify_field;execute_assessment', known), known)
        for value in ('verify_field;unknown', ';verify_field', 'verify_field;', 'verify_field;verify_field'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                checker.catalog_actions(value, known)

    def test_inherited_referenced_and_nested_properties_are_checked_exactly(self):
        first = self.schema('first.schema.json', **{'$defs': {
            'Base': {'type': 'object', 'properties': {'value': {'type': 'string'}}},
            'View': {'allOf': [{'$ref': '#/$defs/Base'}], 'properties': {
                'nested': {'oneOf': [{'type': 'null'}, {'$ref': '#/$defs/Base'}]}}}}})
        registry, schemas, _ = self.load(first)
        view = schemas[first]['$defs']['View']
        resolver = registry.resolver(first.as_uri())
        self.assertTrue(checker.schema_has_field(view, ['value'], resolver))
        self.assertTrue(checker.schema_has_field(view, ['nested', 'value'], resolver))
        self.assertFalse(checker.schema_has_field(view, ['missing'], resolver))
        self.assertFalse(checker.schema_has_field(view, ['nested', 'missing'], resolver))

    def test_missing_enum_only_and_reference_cycle_do_not_pass_field_check(self):
        first = self.schema('first.schema.json', **{'$defs': {
            'Cycle': {'$ref': '#/$defs/Cycle'}, 'Enum': {'enum': ['target_id']}}})
        registry, schemas, _ = self.load(first)
        for name in ('Cycle', 'Enum'):
            self.assertFalse(checker.schema_has_field(schemas[first]['$defs'][name], ['target_id'], registry.resolver(first.as_uri())))
