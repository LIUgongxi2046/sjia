"""Offline schema/type self-check regressions; no Django, database or network."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

from referencing import Registry, Resource
from referencing.exceptions import Unresolvable


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("describe_schema", HERE / "describe-schema.py")
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)
BASE = "file:///synthetic/root.schema.json"
OTHER = "file:///synthetic/other.schema.json"
DIALECT = "https://json-schema.org/draft/2020-12/schema"


class DescribeSchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = {"$schema": DIALECT, "$defs": {
            "Text": {"type": "string"},
            "Base": {"type": "object", "required": ["id"], "properties": {
                "id": {"$ref": "#/$defs/Text"}, "note": {"type": ["string", "null"]}}},
            "Alias": {"$ref": "#/$defs/Base"},
            "Array": {"type": "array", "items": {"type": "string"}},
        }}
        # The relative reference resolves in OTHER, not the calling root schema.
        other = {"$schema": DIALECT, "$defs": {
            "Text": {"type": "integer"},
            "Base": {"type": "object", "required": ["count"], "properties": {
                "count": {"$ref": "#/$defs/Text"}}},
        }}
        self.registry = Registry().with_resources([
            (BASE, Resource.from_contents(self.schema)),
            (OTHER, Resource.from_contents(other)),
        ])
        self.resolver = self.registry.resolver(BASE)

    def test_top_level_ref_follows_aliases_and_preserves_optionality(self):
        self.assertEqual(helper.object_definition({"$ref": "#/$defs/Alias"}, self.resolver), {
            "required": ["id"], "properties": {
                "id": {"kind": "types", "values": ["string"]},
                "note": {"kind": "types", "values": ["null", "string"]}}})

    def test_external_registered_ref_uses_its_own_resolver(self):
        self.assertEqual(helper.object_definition({"$ref": "other.schema.json#/$defs/Base"}, self.resolver), {
            "required": ["count"], "properties": {"count": {"kind": "types", "values": ["integer"]}}})

    def test_allof_merges_nested_refs_local_fields_and_required_only_branches(self):
        schema = {"allOf": [
            {"allOf": [{"$ref": "#/$defs/Alias"}]},
            {"required": ["confirmed"]},
            {"properties": {"confirmed": {"const": True}}},
        ], "required": ["label"], "properties": {"label": {"type": "string"}}}
        result = helper.object_definition(schema, self.resolver)
        self.assertEqual(result["required"], ["confirmed", "id", "label"])
        self.assertEqual(set(result["properties"]), {"id", "note", "confirmed", "label"})
        self.assertEqual(result["properties"]["confirmed"], {"kind": "enum", "values": [True]})

    def test_conditional_requirements_are_not_promoted_to_unconditional_fields(self):
        schema = {"allOf": [{"$ref": "#/$defs/Base"}, {
            "if": {"required": ["id"]},
            "then": {"required": ["note"], "properties": {"note": {"type": "string"}}},
            "else": {"properties": {"id": {"type": "null"}}},
        }]}
        result = helper.object_definition(schema, self.resolver)
        self.assertEqual(result["required"], ["id"])
        self.assertEqual(result["properties"]["note"]["values"], ["null", "string"])

    def test_anyof_and_oneof_preserve_nullable_ref_types(self):
        for keyword in ("anyOf", "oneOf"):
            with self.subTest(keyword=keyword):
                self.assertEqual(helper.describe({keyword: [
                    {"$ref": "#/$defs/Alias"}, {"type": "null"},
                ]}, self.resolver), {"kind": "types", "values": ["null", "object"]})

    def test_nested_allof_resolves_type_without_widening_it(self):
        self.assertEqual(helper.describe({"allOf": [
            {"allOf": [{"$ref": "#/$defs/Array"}]}, {"minItems": 1},
        ]}, self.resolver), {"kind": "types", "values": ["array"]})

    def test_unknown_object_and_property_refs_fail_closed(self):
        for ref in ("#/$defs/Missing", "unregistered.schema.json#/$defs/Base"):
            with self.subTest(ref=ref):
                with self.assertRaises(Unresolvable):
                    helper.object_definition({"$ref": ref}, self.resolver)
                with self.assertRaises(Unresolvable):
                    helper.object_definition({"properties": {"value": {"$ref": ref}}}, self.resolver)

    def test_conflicting_properties_fail_closed_in_allof_and_ref_siblings(self):
        for schema in (
            {"allOf": [{"$ref": "#/$defs/Base"}, {"properties": {"id": {"type": "integer"}}}]},
            {"$ref": "#/$defs/Base", "properties": {"id": {"type": "integer"}}},
        ):
            with self.subTest(schema=schema), self.assertRaisesRegex(ValueError, "交集属性结构.*id"):
                helper.object_definition(schema, self.resolver)

    def test_boolean_and_numeric_literals_are_not_mistaken_for_equal(self):
        with self.assertRaisesRegex(ValueError, "交集属性结构.*confirmed"):
            helper.object_definition({"allOf": [
                {"properties": {"confirmed": {"const": True}}},
                {"properties": {"confirmed": {"const": 1}}},
            ]}, self.resolver)

    def test_identical_reordered_types_and_enums_are_compatible(self):
        result = helper.object_definition({"allOf": [
            {"properties": {"kind": {"enum": ["a", "b"]}, "note": {"type": ["string", "null"]}}},
            {"properties": {"kind": {"enum": ["b", "a"]}, "note": {"type": ["null", "string"]}}},
        ]}, self.resolver)
        self.assertEqual(set(result["properties"]), {"kind", "note"})
        self.assertEqual(set(result["properties"]["kind"]["values"]), {"a", "b"})
        self.assertEqual(result["properties"]["note"], {"kind": "types", "values": ["null", "string"]})

    def test_conflicting_scalar_intersections_are_not_relabelled_as_unions(self):
        for schema in (
            {"allOf": [{"type": "string"}, {"type": "integer"}]},
            {"$ref": "#/$defs/Text", "type": "integer"},
        ):
            with self.subTest(schema=schema), self.assertRaisesRegex(ValueError, "交集类型结构"):
                helper.describe(schema, self.resolver)

    def test_unsupported_enum_unions_and_empty_unions_fail_closed(self):
        for keyword in ("anyOf", "oneOf"):
            for branches in ([], [{"enum": ["unverified"]}, {"type": "null"}]):
                with self.subTest(keyword=keyword, branches=branches), self.assertRaises(ValueError):
                    helper.describe({keyword: branches}, self.resolver)

    def test_missing_property_types_or_required_definitions_fail_closed(self):
        for schema in (
            {"properties": {"value": {"description": "No type"}}},
            {"properties": {"value": {"type": "string"}}, "required": ["missing"]},
            {"type": "object"},
        ):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                helper.object_definition(schema, self.resolver)

    def test_cli_resolves_real_inherited_fields_and_declaration_anyof(self):
        root = HERE.parent
        mappings = [
            ["ClinicalField", "clinicalField", str(root / "data-design/dataset.schema.json")],
            ["RecordInput", "RecordInput", str(root / "backend-design/protocol.schema.json")],
            ["ApplicationView", "ApplicationView", str(root / "backend-design/protocol.schema.json")],
        ]
        result = subprocess.run([sys.executable, str(HERE / "describe-schema.py")],
            input=json.dumps(mappings), text=True, capture_output=True, check=True)
        output = json.loads(result.stdout)
        clinical = output["types"]["ClinicalField"]
        self.assertIn("field_id", clinical["required"])
        self.assertIn("quality_reasons", clinical["properties"])
        record = output["types"]["RecordInput"]
        self.assertEqual(record["properties"]["family_confirmation"], {"kind": "enum", "values": [True]})
        self.assertNotIn("family_confirmation", record["required"])
        self.assertEqual(output["types"]["ApplicationView"]["properties"]["applicant_declaration"],
            {"kind": "types", "values": ["null", "object"]})
        self.assertEqual(output["task_status"], helper.SCHEMAS[helper.FILES[0]]["$defs"]["TaskStatus"]["enum"])

    def test_cli_refuses_schema_files_outside_registered_sources(self):
        result = subprocess.run([sys.executable, str(HERE / "describe-schema.py")],
            input=json.dumps([["Unregistered", "Unknown", str(HERE / "unregistered.schema.json")]]),
            text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("Schema 文件超出当前登记范围", result.stderr)


class ComposedSchemaRegressions(unittest.TestCase):
    def setUp(self):
        self.registry = Registry().with_resource(BASE, Resource.from_contents({
            "$schema": DIALECT, "$defs": {
                "Nullable": {"type": ["string", "null"]},
                "Enum": {"enum": ["a", "b"]},
            }}))
        self.resolver = self.registry.resolver(BASE)

    def test_explicit_empty_schema_means_unknown_not_object_or_any(self):
        for schema in ({}, True):
            self.assertEqual(helper.describe(schema, self.resolver), {"kind": "unknown"})
        with self.assertRaises(ValueError):
            helper.describe(False, self.resolver)

    def test_every_anyof_branch_is_checked_including_after_unknown(self):
        for branches in ([{}, {"$ref": "#/$defs/Missing"}], [{"type": "null"}, {"description": "untyped"}]):
            with self.subTest(branches=branches), self.assertRaises(Exception):
                helper.describe({"anyOf": branches}, self.resolver)
        self.assertEqual(helper.describe({"anyOf": [{}, {"type": "null"}]}, self.resolver), {"kind": "unknown"})
        with self.assertRaises(ValueError):
            helper.describe({"oneOf": [{}, {"type": "null"}]}, self.resolver)

    def test_ref_siblings_and_intersections_narrow_instead_of_widen(self):
        self.assertEqual(helper.describe({"$ref": "#/$defs/Nullable", "type": "null"}, self.resolver),
            {"kind": "types", "values": ["null"]})
        self.assertEqual(helper.describe({"$ref": "#/$defs/Enum", "const": "a"}, self.resolver),
            {"kind": "enum", "values": ["a"]})
        self.assertEqual(helper.describe({"allOf": [{"type": "number"}, {"type": "integer"}]}, self.resolver),
            {"kind": "types", "values": ["integer"]})
        with self.assertRaises(ValueError):
            helper.describe({"type": "string", "const": 1}, self.resolver)

    def test_parent_domain_is_intersected_after_the_complete_union(self):
        for keyword in ('anyOf', 'oneOf'):
            self.assertEqual(helper.describe({'type': 'string', keyword: [
                {'type': 'string'}, {'type': 'null'},
            ]}, self.resolver), {'kind': 'types', 'values': ['string']})
            with self.assertRaises(ValueError):
                helper.describe({'type': 'boolean', keyword: [
                    {'type': 'string'}, {'type': 'null'},
                ]}, self.resolver)
            with self.assertRaises(Unresolvable):
                helper.describe({'type': 'string', keyword: [
                    {'type': 'string'}, {'type': 'null'}, {'$ref': '#/$defs/Missing'},
                ]}, self.resolver)

    def test_nested_untyped_union_keeps_ancestor_domain_without_intersecting_typed_branches(self):
        for keyword in ('anyOf', 'oneOf'):
            self.assertEqual(helper.describe({'type': 'object', 'anyOf': [{keyword: [
                {'required': ['a']}, {'type': 'object'},
            ]}]}, self.resolver), {'kind': 'types', 'values': ['object']})
        self.assertEqual(helper.describe({'type': 'object', 'anyOf': [{'anyOf': [
            {'required': ['a']}, {'type': 'null'},
        ]}]}, self.resolver), {'kind': 'types', 'values': ['object']})

    def test_parent_object_domain_is_preserved_for_conditional_union_constraints(self):
        self.assertEqual(helper.describe({"type": "object", "oneOf": [
            {"properties": {"mode": {"const": "a"}}},
            {"properties": {"mode": {"const": "b"}}},
        ]}, self.resolver), {"kind": "types", "values": ["object"]})

    def test_required_only_allof_and_ref_siblings_are_not_dropped(self):
        result = helper.object_definition({"allOf": [
            {"properties": {"id": {"type": "string"}}}, {"required": ["id"]},
            {"properties": {"id": {}}},
        ]}, self.resolver)
        self.assertEqual(result, {"required": ["id"], "properties": {"id": {"kind": "types", "values": ["string"]}}})

    def test_exact_pattern_property_is_finite_and_general_patterns_fail_closed(self):
        result = helper.object_definition({"type": "object", "required": ["id"],
            "properties": {"id": {"type": "string"}},
            "patternProperties": {"^extra$": {"type": "array"}}}, self.resolver)
        self.assertIn("extra", result["properties"])
        self.assertNotIn("extra", result["required"])
        with self.assertRaises(ValueError):
            helper.object_definition({"patternProperties": {"^extra": {"type": "array"}}}, self.resolver)

    def test_nested_resource_id_keeps_ref_resolution_scope(self):
        from referencing.jsonschema import DRAFT202012
        nested = {"$id": "nested/typed.schema.json", "$defs": {"Text": {"type": "string"}},
            "type": "object", "properties": {"id": {"$ref": "#/$defs/Text"}}, "required": ["id"]}
        root = {"$schema": DIALECT, "$defs": {"Nested": nested}}
        registry = Registry().with_resource(BASE, Resource.from_contents(root, default_specification=DRAFT202012)).crawl()
        self.assertEqual(helper.object_definition(nested, registry.resolver(BASE)), {
            "required": ["id"], "properties": {"id": {"kind": "types", "values": ["string"]}}})

    def test_conditional_obligations_remain_enforced_by_unchanged_schema(self):
        from copy import deepcopy
        from jsonschema import Draft202012Validator
        schema = {"type": "object", "additionalProperties": False, "required": ["state"],
            "properties": {"state": {"enum": ["ready", "draft"]}, "payload": {"type": "string"}},
            "allOf": [{"if": {"properties": {"state": {"const": "ready"}}, "required": ["state"]},
                "then": {"required": ["payload"]}, "else": {"not": {"required": ["payload"]}}}]}
        before = deepcopy(schema)
        projection = helper.object_definition(schema, self.resolver)
        self.assertEqual(projection["required"], ["state"])
        self.assertEqual(schema, before)
        validator = Draft202012Validator(schema)
        for good in ({"state": "ready", "payload": "kept"}, {"state": "draft"}):
            self.assertTrue(validator.is_valid(good), good)
        for bad in ({"state": "ready"}, {"state": "draft", "payload": "forbidden"}, {"state": "unknown"}):
            self.assertFalse(validator.is_valid(bad), bad)


if __name__ == "__main__":
    unittest.main()
