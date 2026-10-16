"""Pure validation of the documented one-or-more task ownership format."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('frontend_v2_checks', Path(__file__).with_name('check_v2.py'))
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class TaskReferenceTests(unittest.TestCase):
    def test_single_and_existing_multiple_owners(self):
        ids = {'T49', 'T44'}
        self.assertEqual(checks.validate_task_references('T49', ids), ['T49'])
        self.assertEqual(checks.validate_task_references('T49;T44', ids), ['T49', 'T44'])

    def test_every_owner_must_be_registered(self):
        for value in ('T49;UNKNOWN', 'UNKNOWN;T44', 'T49;', ';T44', 'T49;;T44', '', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                checks.validate_task_references(value, {'T49', 'T44'})

    def test_duplicate_owner_is_not_a_second_responsibility(self):
        with self.assertRaises(ValueError):
            checks.validate_task_references('T49;T49', {'T49'})


class LocalSchemaRegistryTests(unittest.TestCase):
    def write(self, root, name, **schema):
        path = root / name
        path.write_text(json.dumps({'$schema': 'https://json-schema.org/draft/2020-12/schema', **schema}))
        return path

    def test_controlled_local_transitive_reference_resolves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first = self.write(root, 'first.schema.json', **{'$ref': 'second.schema.json#/$defs/Value'})
            second = self.write(root, 'second.schema.json', **{'$defs': {'Value': {'type': 'string'}}})
            registry, schemas, _ = checks.local_schema_registry([first], root=root)
            self.assertEqual(set(schemas), {first, second})
            self.assertEqual(registry.resolver(first.as_uri()).lookup('second.schema.json#/$defs/Value').contents,
                {'type': 'string'})

    def test_missing_or_external_reference_fails_without_retrieval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for reference in ('missing.schema.json', 'https://example.invalid/private.schema.json'):
                first = self.write(root, 'first.schema.json', **{'$ref': reference})
                with self.subTest(reference=reference), self.assertRaises(Exception):
                    checks.local_schema_registry([first], root=root)

    def test_parent_directory_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first = self.write(root, 'first.schema.json', **{'$ref': '../outside.schema.json'})
            with self.assertRaises(ValueError):
                checks.local_schema_registry([first], root=root)

    def test_invalid_refs_inside_conditional_and_union_branches_are_not_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for reference in ('missing.schema.json', 'https://example.invalid/private.schema.json'):
                for branch in ({'then': {'$ref': reference}}, {'anyOf': [{}, {'$ref': reference}]}):
                    first = self.write(root, 'first.schema.json', type='object', **branch)
                    with self.subTest(reference=reference, branch=branch), self.assertRaises(Exception):
                        checks.local_schema_registry([first], root=root)


class SharedDescriptionTests(unittest.TestCase):
    def test_full_v2_uses_the_same_anyof_and_unknown_semantics(self):
        registry, schemas, _ = checks.local_schema_registry([checks.ROOT / 'backend-design/v2-protocol.schema.json'])
        resolver = registry.resolver((checks.ROOT / 'backend-design/v2-protocol.schema.json').as_uri())
        definitions = schemas[checks.ROOT / 'backend-design/v2-protocol.schema.json']['$defs']
        properties, _ = checks.object_fields(definitions['EvaluationResultsView'], resolver)
        self.assertEqual(properties['clinical_report'], {'kind': 'types', 'values': ['null', 'object']})
        properties, _ = checks.object_fields(definitions['ClinicalIntervalSection'], resolver)
        self.assertEqual(properties['protocol'], {'kind': 'unknown'})
        properties, _ = checks.object_fields(definitions['AssessmentView'], resolver)
        self.assertIn('clinical_selections', properties)

    def test_required_only_allof_is_not_dropped(self):
        properties, required = checks.object_fields({'allOf': [
            {'properties': {'id': {'type': 'string'}}}, {'required': ['id']},
        ]}, None)
        self.assertEqual(required, {'id'})
        with self.assertRaises(ValueError):
            checks.object_fields({'properties': {'id': {'type': 'string'}}, 'required': ['missing']}, None)

    def test_sibling_object_unions_cannot_overwrite_each_other(self):
        from referencing import Registry, Resource
        schema = {'$schema': 'https://json-schema.org/draft/2020-12/schema', '$defs': {
            name: {'type': 'object', 'properties': {'kind': {'const': name}}, 'required': ['kind']}
            for name in ('A', 'B', 'C')}}
        resolver = Registry().with_resource('file:///synthetic.schema.json', Resource.from_contents(schema)).resolver('file:///synthetic.schema.json')
        branches = lambda *names: [{'$ref': '#/$defs/' + name} for name in names]
        self.assertEqual(checks.object_union_description({'oneOf': branches('A', 'B')}, resolver, 'Choice'),
            {'union_refs': ['A', 'B'], 'keyword': 'oneOf'})
        with self.assertRaisesRegex(ValueError, '同级对象联合组合'):
            checks.object_union_description({'oneOf': branches('A', 'B'), 'anyOf': branches('A', 'C')}, resolver, 'Choice')
        with self.assertRaisesRegex(ValueError, '同级对象联合组合'):
            checks.object_union_description({'oneOf': branches('A', 'B'), 'anyOf': [{'type': 'null'}]}, resolver, 'Choice')


if __name__ == '__main__':
    unittest.main()
