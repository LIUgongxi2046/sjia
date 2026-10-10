import json
import sys
from pathlib import Path

from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "backend-design/protocol.schema.json", ROOT / "data-design/dataset.schema.json", ROOT / "data-design/case-extension.schema.json"]
SCHEMAS = {path: json.loads(path.read_text(encoding="utf-8")) for path in FILES}
REGISTRY = Registry().with_resources((path.as_uri(), Resource.from_contents(schema)) for path, schema in SCHEMAS.items())


def describe(value, resolver):
    if "$ref" in value:
        resolved = resolver.lookup(value["$ref"])
        return describe(resolved.contents, resolved.resolver)
    if "enum" in value:
        return {"kind": "enum", "values": value["enum"]}
    if "const" in value:
        return {"kind": "enum", "values": [value["const"]]}
    if "type" in value:
        types = value["type"] if isinstance(value["type"], list) else [value["type"]]
        return {"kind": "types", "values": types}
    if "oneOf" in value:
        types = []
        for child in value["oneOf"]:
            child_shape = describe(child, resolver)
            if child_shape["kind"] != "types":
                raise ValueError("联合类型需要准确的类别检查")
            types.extend(child_shape["values"])
        return {"kind": "types", "values": sorted(set(types))}
    if "allOf" in value:
        types = []
        for child in value["allOf"]:
            if "$ref" in child or "type" in child:
                shape = describe(child, resolver)
                if shape["kind"] != "types":
                    raise ValueError("交集类型需要准确的类别检查")
                types.extend(shape["values"])
        if not types:
            raise ValueError("交集缺少类型来源")
        return {"kind": "types", "values": sorted(set(types))}
    raise ValueError("Schema 属性缺少可检查类型")


types = {}
for name, definition, filename in json.load(sys.stdin):
    path = Path(filename).resolve()
    if path not in SCHEMAS:
        raise ValueError("Schema 文件超出当前登记范围")
    schema = SCHEMAS[path]["$defs"][definition]
    resolver = REGISTRY.resolver(path.as_uri())
    types[name] = {
        "required": schema.get("required", []),
        "properties": {key: describe(value, resolver) for key, value in schema["properties"].items()},
    }
print(json.dumps({"types": types, "task_status": SCHEMAS[FILES[0]]["$defs"]["TaskStatus"]["enum"]}))
