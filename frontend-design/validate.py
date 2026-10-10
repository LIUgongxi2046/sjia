import csv
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator
from markdown_it import MarkdownIt
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1]
DELIVERY = ROOT / "frontend-design"
WORK = ROOT / ".front-work"
DOCUMENT = ROOT / "sJIA合并MAS预警平台前端LLD.md"
PARSER = MarkdownIt("commonmark").enable("table")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def check_document():
    content = DOCUMENT.read_text(encoding="utf-8")
    tokens = PARSER.parse(content)
    headings = [tokens[index + 1].content for index, token in enumerate(tokens) if token.type == "heading_open" and token.tag == "h2"]
    require(len(headings) == 7, "前端文档需要七个主要章节")
    require(all(title.startswith(f"{index}. ") for index, title in enumerate(headings, 1)), "章节编号不连续")
    links = set()
    for token in tokens:
        for child in token.children or []:
            if child.type != "link_open":
                continue
            url = urlparse(child.attrGet("href"))
            require(not url.scheme, "设计引用应使用本地项目资料")
            target = (ROOT / unquote(url.path)).resolve()
            require(target.is_relative_to(ROOT), "引用超出项目范围")
            if target != DELIVERY / "validation-report.json":
                require(target.is_file(), f"引用缺少：{target.name}")
            links.add(str(target.relative_to(ROOT)))
    coverage = {}
    for prefix, count in [("FD-", 57), ("FR-", 12), ("AC-", 12), ("AC-H5-", 6), ("NFR-", 13), ("FRONT-Q", 20)]:
        actual = set(re.findall(rf"(?<![A-Z-]){re.escape(prefix)}\d{{2}}(?!\d)", content))
        expected = {f"{prefix}{index:02d}" for index in range(1, count + 1)}
        require(expected <= actual, f"需求编号缺少：{sorted(expected - actual)}")
        coverage[prefix] = len(expected)
    forbidden = ["\u6808", "\u843d", "\u6b7b", "\u62c6", "\u504f", "\u5951\u7ea6", "\u4e0d\u662f", "\u800c\u662f"]
    for term in forbidden:
        require(term not in content, f"文档含有禁止用词：U+{ord(term[0]):04X}")
    return {"status": "PASS", "headings": headings, "coverage": coverage, "local_links": sorted(links), "tables": sum(token.type == "table_open" for token in tokens), "mermaid_blocks": sum(token.type == "fence" and token.info == "mermaid" for token in tokens), "mermaid_rendering": "NOT_RUN"}


def check_schema_and_bindings():
    files = [ROOT / "backend-design/protocol.schema.json", ROOT / "data-design/dataset.schema.json", ROOT / "data-design/case-extension.schema.json"]
    schemas = {path: read_json(path) for path in files}
    registry = Registry().with_resources((path.as_uri(), Resource.from_contents(schema)) for path, schema in schemas.items())
    for path, schema in schemas.items():
        Draft202012Validator.check_schema(schema)
        registry.resolver(path.as_uri()).lookup("#/$defs")
    catalog = read_csv(ROOT / "backend-design/api-catalog.csv")
    operations = {row["operation_id"]: row for row in catalog}
    bindings = read_csv(DELIVERY / "api-bindings.csv")
    binding_ids = {row["contract_id"] for row in bindings}
    require(len(binding_ids) == len(bindings), "接口绑定重复")
    family_ids = {row["operation_id"] for row in catalog if row["caller"] in {"family", "family_support"}}
    require(binding_ids == family_ids, f"家庭接口绑定不完整：{sorted(family_ids - binding_ids)} / {sorted(binding_ids - family_ids)}")
    definitions = schemas[files[0]]["$defs"]
    for row in bindings:
        require(all(row.values()), f"接口绑定存在空值：{row['contract_id']}")
        source = operations[row["contract_id"]]
        require(row["request_mapping"] == source["input_schema"], "输入 Schema 不一致")
        require(row["response_mapping"] == source["output_schema"], "输出 Schema 不一致")
        require(source["input_schema"] in definitions and source["output_schema"] in definitions, "Schema 定义缺少")
        require("/support/" not in source["path"], "家庭绑定不能访问支持接口")
        require(row["status"] == "PENDING", "未实施接口不能标记成功")
    return {"status": "PASS", "bindings": len(bindings), "family_operations": len(family_ids)}, operations


def check_routes(operations):
    rows = read_csv(DELIVERY / "routes.csv")
    require(len(rows) == 24, "需要23个业务及状态入口和独立评审入口")
    require(len({row["path"] for row in rows}) == len(rows), "路由路径重复")
    require(len({row["route_id"] for row in rows}) == len(rows), "路由标识重复")
    design = read_csv(ROOT / "ui-delivery/route-design-map.csv")
    expected = {row["source_id"]: row["screen_id"] for row in design if row["source_id"].startswith("/") or row["source_id"] == "*"}
    require({row["path"]: row["screen_id"] for row in rows} == expected, "路由及页面与UI维护表不一致")
    for row in rows:
        require(all(row.values()), f"路由定义空值：{row['route_id']}")
        for operation in row["api_refs"].split(";"):
            if operation == "none":
                continue
            require(operation in operations, f"路由使用未知接口：{operation}")
            require(operations[operation]["caller"] in {"family", "family_support"}, "路由调用范围不符合家庭入口")
    primary = {row["path"] for row in rows if row["layout"] == "primary"}
    require(primary == {"/home", "/records", "/account"}, "主导航页面不一致")
    return {"status": "PASS", "route_entries": len(rows), "business_and_status": 23, "primary_paths": sorted(primary)}


def value_at(source, dotted):
    value = source
    for part in dotted.split("."):
        require(part in value, f"Token路径不存在：{dotted}")
        value = value[part]
    return value


def check_tokens():
    mapping = read_json(DELIVERY / "token-map.json")
    source = read_json(ROOT / mapping["source"])
    require(mapping["sourceVersion"] == source["version"], "Token版本不一致")
    require(len({row["css"] for row in mapping["tokens"]}) == len(mapping["tokens"]), "CSS变量重复")
    for row in mapping["tokens"]:
        value_at(source, row["light"])
        value_at(source, row["dark"])
        require(row["components"], "Token缺少使用组件")
    for key in mapping["alerts"].values():
        require(key in source["alertDesign"], "预警颜色来源缺少")
    require(source["color"]["clinicalSeverity"] is None and mapping["clinicalSeverityRequiresApproval"], "临床等级尚需批准")
    for relative in mapping["atlasPaths"]:
        require((ROOT / relative).is_file(), f"图标资产不存在：{relative}")
    return {"status": "PASS", "mapped_tokens": len(mapping["tokens"]), "alert_states": len(mapping["alerts"]), "atlas_paths": mapping["atlasPaths"], "visual": "NOT_RUN"}


def check_acceptance():
    rows = read_csv(DELIVERY / "acceptance.csv")
    expected = {f"FRONT-Q{index:02d}" for index in range(1, 21)}
    require({row["check_id"] for row in rows} == expected and len(rows) == 20, "验收编号不完整")
    for row in rows:
        require(all(row.values()), f"验收缺少字段：{row['check_id']}")
        require(row["status"] in {"BLOCKED", "NOT_RUN"}, "业务用例未执行不能标记通过")
    return {"status": "PASS", "cases": len(rows), "business_execution": "NOT_RUN", "blocked": sum(row["status"] == "BLOCKED" for row in rows)}


def run_command(command, environment):
    result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True)
    if result.returncode:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise RuntimeError(f"检查未通过：{command}")
    return {"command": command, "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def main():
    WORK.mkdir(exist_ok=True)
    environment = dict(os.environ, TMPDIR=str(WORK))
    document = check_document()
    bindings, operations = check_schema_and_bindings()
    routes = check_routes(operations)
    tokens = check_tokens()
    acceptance = check_acceptance()
    typecheck = run_command(["node", "guardian-h5/node_modules/typescript/bin/tsc", "-p", "frontend-design/tsconfig.json"], environment)
    type_mapping = run_command(["node", "frontend-design/check-types.mjs"], environment)
    type_result = json.loads(type_mapping["stdout"])
    source_paths = type_result["current_source_routes"]
    declared_paths = [row["path"] for row in read_csv(DELIVERY / "routes.csv")] + ["/"]
    require(len(source_paths) == len(set(source_paths)) and set(source_paths) == set(declared_paths), "路由目录与当前 App 源码不一致")
    routes["current_source_paths"] = source_paths
    routes["root_redirect"] = type_result["root_redirect"]
    inputs = [DOCUMENT, *sorted(DELIVERY.glob("*.csv")), DELIVERY / "types.ts", DELIVERY / "tsconfig.json", DELIVERY / "token-map.json", DELIVERY / "check-types.mjs", DELIVERY / "describe-schema.py", DELIVERY / "validate.py", ROOT / "backend-design/api-catalog.csv", ROOT / "backend-design/protocol.schema.json", ROOT / "data-design/dataset.schema.json", ROOT / "data-design/case-extension.schema.json", ROOT / "ui-delivery/tokens.json", ROOT / "ui-delivery/route-design-map.csv", ROOT / "guardian-h5/src/app/App.tsx", ROOT / "guardian-h5/package-lock.json"]
    report = {
        "task_id": "FRONT-DESIGN-01", "capability": "DESIGN", "status": "PASS",
        "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "command": "PYTHONPATH=.data-work/deps python3 frontend-design/validate.py",
        "environment": {"python": sys.version.split()[0], "node": run_command(["node", "--version"], environment)["stdout"].strip(), "jsonschema": importlib.metadata.version("jsonschema"), "markdown-it-py": importlib.metadata.version("markdown-it-py"), "temporary_directory": ".front-work"},
        "checks": {"document": document, "bindings": bindings, "routes": routes, "tokens": tokens, "acceptance": acceptance, "types": type_result},
        "commands": [typecheck, type_mapping],
        "input_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs},
        "business_acceptance": "NOT_RUN", "visual_acceptance": "NOT_RUN", "mobile_acceptance": "NOT_RUN", "mermaid_rendering": "NOT_RUN",
        "protocol_dependencies": [f"FRONT-D{index:02d}" for index in range(1, 14)],
    }
    (DELIVERY / "validation-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "routes": routes["route_entries"], "bindings": bindings["bindings"], "types": report["checks"]["types"]["types"], "acceptance_cases": acceptance["cases"], "report": "frontend-design/validation-report.json"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
