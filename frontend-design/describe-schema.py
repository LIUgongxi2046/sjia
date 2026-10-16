import importlib.util
import json
import re
import sys

from referencing import Resource
from referencing.jsonschema import DRAFT202012
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "backend-design/protocol.schema.json", ROOT / "data-design/dataset.schema.json", ROOT / "data-design/case-extension.schema.json"]
_SPEC = importlib.util.spec_from_file_location("frontend_strict_schema_registry", ROOT / "backend-design/check_v2.py")
_REGISTRY_HELPER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_REGISTRY_HELPER)
REGISTRY, SCHEMAS, _ = _REGISTRY_HELPER.local_schema_registry(FILES, root=ROOT)


def shape_key(shape):
    # JSON keeps true distinct from 1, while enum/type ordering is immaterial.
    return shape["kind"], tuple(sorted(json.dumps(item, sort_keys=True) for item in shape.get("values", [])))


def intersect_shapes(left, right):
    """Intersect structural domains; never turn an allOf into a union."""
    if left["kind"] == "unknown":
        return right
    if right["kind"] == "unknown":
        return left
    if left["kind"] == right["kind"] == "types":
        a, b = set(left["values"]), set(right["values"])
        values = a & b
        if "number" in a and "integer" in b or "integer" in a and "number" in b:
            values.add("integer")
        if not values:
            raise ValueError("交集类型结构不兼容")
        return {"kind": "types", "values": sorted(values)}
    if left["kind"] == right["kind"] == "enum":
        allowed = {json.dumps(value, sort_keys=True) for value in right["values"]}
        values = [value for value in left["values"] if json.dumps(value, sort_keys=True) in allowed]
    else:
        enum, types = (left, right) if left["kind"] == "enum" else (right, left)
        def allowed(value):
            if value is None:
                kind = "null"
            elif isinstance(value, bool):
                kind = "boolean"
            elif isinstance(value, int):
                return bool({"number", "integer"} & set(types["values"]))
            elif isinstance(value, float):
                return "number" in types["values"] or value.is_integer() and "integer" in types["values"]
            elif isinstance(value, str):
                kind = "string"
            elif isinstance(value, list):
                kind = "array"
            elif isinstance(value, dict):
                kind = "object"
            else:
                raise ValueError("枚举不是 JSON 值")
            return kind in types["values"]
        values = [value for value in enum["values"] if allowed(value)]
    if not values:
        raise ValueError("交集类型结构不兼容")
    return {"kind": "enum", "values": values}


def describe(value, resolver, *, context=None, fallback=None, _enter=True):
    # An explicitly unconstrained JSON Schema is represented as TS unknown, not
    # object or any. Annotation-only / unsupported constraints still fail closed.
    if value is True or value == {}:
        return context if context is not None else {"kind": "unknown"}
    if not isinstance(value, dict):
        raise ValueError("Schema 属性没有可表示的类型")
    if _enter and resolver is not None:
        resolver = resolver.in_subresource(Resource.from_contents(value, default_specification=DRAFT202012))
    shapes = []
    if "$ref" in value:
        resolved = resolver.lookup(value["$ref"])
        shapes.append(describe(resolved.contents, resolved.resolver,
            fallback=context if context is not None else fallback, _enter=False))
    if "enum" in value:
        shapes.append({"kind": "enum", "values": value["enum"]})
    if "const" in value:
        shapes.append({"kind": "enum", "values": [value["const"]]})
    if "type" in value:
        types = value["type"] if isinstance(value["type"], list) else [value["type"]]
        shapes.append({"kind": "types", "values": sorted(set(types))})
    base = context
    for shape in shapes:
        base = shape if base is None else intersect_shapes(base, shape)
    for child in value.get("allOf", []):
        if isinstance(child, dict) and any(key in child for key in
                ("$ref", "type", "enum", "const", "allOf", "oneOf", "anyOf")):
            shape = describe(child, resolver, fallback=base if base is not None else fallback)
            base = shape if base is None else intersect_shapes(base, shape)
        elif child is False:
            raise ValueError("交集含有不可满足的 Schema")
    for keyword in ("oneOf", "anyOf"):
        if keyword not in value:
            continue
        # A typed branch keeps its own domain until the complete union is built.
        # Untyped refinements still inherit an ancestor's domain as a fallback,
        # including through nested unions; fallback is not an intersection.
        children = [describe(child, resolver, fallback=base if base is not None else fallback)
            for child in value[keyword]]
        if not children:
            raise ValueError("联合缺少类型来源")
        if any(shape["kind"] == "unknown" for shape in children):
            if keyword == "oneOf" and len(children) != 1:
                raise ValueError("oneOf 含有不受限分支，不能展平为 unknown")
            union = {"kind": "unknown"}
        else:
            if any(shape["kind"] != "types" for shape in children):
                raise ValueError("联合类型需要准确的类别检查")
            union = {"kind": "types", "values": sorted({item for shape in children for item in shape.get("values", [])})}
        base = union if base is None else intersect_shapes(base, union)
    if base is None:
        base = fallback
    if base is None:
        raise ValueError("Schema 属性缺少可检查类型")
    return base


def object_definition(schema, resolver, *, allow_empty=False, allow_partial=False):
    """Project unconditional structure; conditional obligations remain runtime rules.

    Traverse every allOf branch (including required-only branches) and $ref
    siblings. Conditional then/else requirements must not become unconditional.
    """
    required, properties = set(), {}

    def collect(value, current_resolver, *, enter=True):
        if not isinstance(value, dict):
            if value is False:
                raise ValueError("Schema 对象不可满足")
            return
        if enter and current_resolver is not None:
            current_resolver = current_resolver.in_subresource(Resource.from_contents(value, default_specification=DRAFT202012))
        required.update(value.get("required", []))
        if "$ref" in value:
            resolved = current_resolver.lookup(value["$ref"])
            collect(resolved.contents, resolved.resolver, enter=False)
        for branch in value.get("allOf", []):
            collect(branch, current_resolver)
        definitions = list(value.get("properties", {}).items())
        for pattern, definition in value.get("patternProperties", {}).items():
            match = re.fullmatch(r"\^([A-Za-z_][A-Za-z_0-9]*)\$", pattern)
            if not match:
                raise ValueError("Schema 模式属性需要显式有限绑定：" + pattern)
            definitions.append((match.group(1), definition))
        for key, definition in definitions:
            try:
                shape = describe(definition, current_resolver, context=properties.get(key))
            except ValueError as error:
                raise ValueError("交集属性结构或类型无法描述：" + key) from error
            properties[key] = shape

    collect(schema, resolver)
    if not properties and not allow_empty:
        raise ValueError("Schema 对象缺少可检查的属性定义")
    if not allow_partial and not required <= set(properties):
        raise ValueError("Schema 必填属性没有定义")
    return {"required": sorted(required), "properties": properties}


def main():
    types = {}
    for name, definition, filename in json.load(sys.stdin):
        path = Path(filename).resolve()
        if path not in FILES:
            raise ValueError("Schema 文件超出当前登记范围")
        schema = SCHEMAS[path]["$defs"][definition]
        types[name] = object_definition(schema, REGISTRY.resolver(path.as_uri()))
    print(json.dumps({"types": types, "task_status": SCHEMAS[FILES[0]]["$defs"]["TaskStatus"]["enum"]}))


if __name__ == "__main__":
    main()
