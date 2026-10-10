import csv
import hashlib
import importlib.metadata
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker
from markdown_it import MarkdownIt
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1]
DELIVERY = ROOT / "alert-design"
REPORT = DELIVERY / "validation-report.json"
PARSER = MarkdownIt("commonmark").enable("table")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def check_schema(path):
    schema = read_json(path)
    Draft202012Validator.check_schema(schema)
    resource = Resource.from_contents(schema)
    registry = Registry().with_resource(schema["$id"], resource)
    resolver = registry.resolver(schema["$id"])
    references = [obj["$ref"] for obj in walk(schema) if "$ref" in obj]
    for reference in references:
        resolver.lookup(reference)
    require(not any("default" in obj for obj in walk(schema)), "预警 Schema 不应带临床默认参数")
    objects = [obj for obj in walk(schema) if obj.get("type") == "object"]
    require(all(obj.get("additionalProperties") is False for obj in objects), "预警对象必须拒绝额外字段")
    return {"status": "PASS", "definitions": len(schema["$defs"]), "resolved_references": len(references), "strict_objects": len(objects)}


def check_documents(document_paths):
    links = set()
    counts = {}
    for path in document_paths:
        tokens = PARSER.parse(path.read_text(encoding="utf-8"))
        headings = [tokens[index + 1].content for index, token in enumerate(tokens) if token.type == "heading_open" and token.tag == "h2"]
        if path == document_paths[0]:
            require(len(headings) == 8, "预警设计需要八个主要章节")
            require(all(title.startswith(f"{number}. ") for number, title in enumerate(headings, 1)), "章节编号不连续")
        for token in tokens:
            for child in token.children or []:
                if child.type != "link_open":
                    continue
                target = urlparse(child.attrGet("href"))
                if target.scheme:
                    require(target.scheme == "https", "外部证据需要 HTTPS")
                    continue
                linked = (path.parent / unquote(target.path)).resolve()
                require(linked.is_relative_to(ROOT), "本地引用必须位于项目范围")
                require(linked.is_file() or linked == REPORT, f"本地引用不存在：{linked.name}")
                links.add(str(linked.relative_to(ROOT)))
        counts[str(path.relative_to(ROOT))] = {"major_sections": len(headings), "tables": sum(t.type == "table_open" for t in tokens), "mermaid_blocks": sum(t.type == "fence" and t.info == "mermaid" for t in tokens)}
    return {"status": "PASS", "documents": counts, "local_references": sorted(links), "mermaid_rendering": "NOT_RUN"}


def check_existing_interfaces(manifest):
    with (ROOT / "backend-design/api-catalog.csv").open(encoding="utf-8", newline="") as stream:
        catalog = list(csv.DictReader(stream))
    operations = {row["operation_id"]: row for row in catalog}
    for operation in manifest["existing_operations"]:
        require(operation in operations, f"现有接口不存在：{operation}")
    protocol = read_json(ROOT / "backend-design/protocol.schema.json")
    kinds = protocol["$defs"]["AssessmentInput"]["properties"]["kind"]["enum"]
    require(kinds == ["classification", "ms"], "既有 assessment 类型已改变，需要重新核查设计")
    task_type = manifest["registered_task_type"]
    require(task_type != manifest["existing_task_type"], "评估任务与专业量表任务分别登记")
    require(task_type in protocol["$defs"]["TaskView"]["properties"]["task_type"]["enum"], "评估任务未登记")
    return {"status": "PASS", "existing_operations": manifest["existing_operations"], "existing_assessment_kinds": kinds, "registered_task_type": task_type, "task_runtime": "NOT_RUN", "api_runtime": "NOT_RUN"}


def check_real_case(manifest):
    case_path = ROOT / manifest["case_path"]
    case = read_json(case_path)
    schema = read_json(ROOT / "demo-data/case.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(case)
    basis = case["use_basis"]
    require(basis["clinical_verification"] == "unverified", "病例核对状态已改变，需要重新核查准入依据")
    require(basis["clinical_rule_eligible"] is False, "病例计算资格已改变，需要重新核查批准")
    require(basis["risk_result"] is None and basis["ms_result"] is None, "当前病例不能携带本次未授权产生的结果")
    sources = {source["id"]: source for source in case["sources"]}
    times = {time["id"]: time for time in case["time_contexts"]}
    measurements = []
    for panel in case["measurement_panels"]:
        require(panel["source_id"] in sources, "报告组缺少来源")
        require(panel["time_ref"] in times, "报告组缺少时间引用")
        measurements.extend(panel["items"])
    source_files = []
    for source in sources.values():
        path = (case_path.parent / source["path"]).resolve()
        require(path.is_relative_to(ROOT) and path.is_file(), "病例来源文件不存在或超出范围")
        source_files.append({"path": str(path.relative_to(ROOT)), "sha256": digest(path)})
    return {
        "status": "PASS",
        "scope": "现有病例结构和准入依据核查",
        "dataset_version": case["dataset_version"],
        "source_count": len(sources),
        "image_source_count": sum(source["kind"] == "image" for source in sources.values()),
        "measurement_panel_count": len(case["measurement_panels"]),
        "measurement_count": len(measurements),
        "qualified_measurement_count": sum(item.get("qualifier") not in (None, "eq") for item in measurements),
        "unknown_numeric_measurement_count": sum(item.get("value") is None for item in measurements),
        "time_precision_counts": dict(sorted(Counter(item["precision"] for item in times.values()).items())),
        "clinical_rule_eligible": basis["clinical_rule_eligible"],
        "clinical_calculation": "NOT_RUN",
        "source_files": source_files
    }


def main():
    manifest = read_json(DELIVERY / "design-manifest.json")
    require(manifest["capability"] == "DESIGN" and manifest["production_agent"] is False, "当前设计能力声明不一致")
    require(manifest["clinical_configuration_status"] == "NOT_CONFIGURED", "当前没有获准临床配置实例")
    schemas = {name: check_schema(ROOT / name) for name in manifest["schemas"]}
    documents = [ROOT / manifest["document"], ROOT / manifest["evidence"]]
    document_result = check_documents(documents)
    content = documents[0].read_text(encoding="utf-8")
    for node in manifest["nodes"]:
        require(f"| {node} |" in content, f"缺少节点定义：{node}")
    for number in range(1, 13):
        require(f"| AQA{number:02d} |" in content, "缺少验收定义")
    inputs = list(dict.fromkeys(documents + [Path(__file__), DELIVERY / "design-manifest.json"] + [ROOT / name for name in manifest["schemas"]] + [ROOT / name for name in document_result["local_references"] if ROOT / name != REPORT] + [ROOT / "demo-data/case.schema.json", ROOT / "backend-design/protocol.schema.json", ROOT / "backend-design/api-catalog.csv", ROOT / manifest["case_path"], ROOT / ".gitignore"]))
    report = {
        "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "capability": "DESIGN",
        "status": "PASS",
        "check_id": "AQA01",
        "command": "PYTHONPATH=.data-work/deps python3 alert-design/validate.py",
        "python": sys.version,
        "dependencies": {name: importlib.metadata.version(name) for name in ["jsonschema", "referencing", "markdown-it-py"]},
        "documents": document_result,
        "schemas": schemas,
        "interfaces": check_existing_interfaces(manifest),
        "real_case_readiness": check_real_case(manifest),
        "clinical_approval": "BLOCKED",
        "clinical_performance": "NOT_RUN",
        "business_integration": "NOT_RUN",
        "visual_checks": "NOT_RUN",
        "inputs": [{"path": str(path.relative_to(ROOT)), "sha256": digest(path)} for path in inputs]
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"check_id": "AQA01", "status": "PASS", "report": str(REPORT), "clinical_calculation": "NOT_RUN"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
