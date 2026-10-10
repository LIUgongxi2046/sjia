import csv
import hashlib
import importlib.metadata
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

import psycopg
from jsonschema import Draft202012Validator, FormatChecker
from markdown_it import MarkdownIt
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1]
DELIVERY = ROOT / "backend-design"
WORK = ROOT / ".backend-work"
DOCUMENT = ROOT / "sJIA合并MAS预警平台后端LLD.md"
PARSER = MarkdownIt("commonmark").enable("table")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def check_document():
    content = DOCUMENT.read_text(encoding="utf-8")
    tokens = PARSER.parse(content)
    headings = [tokens[i + 1].content for i, token in enumerate(tokens) if token.type == "heading_open" and token.tag == "h2"]
    require(len(headings) == 8, "后端设计需要八个主要章节")
    require(all(title.startswith(f"{i}. ") for i, title in enumerate(headings, 1)), "章节编号不连续")
    local_links = set()
    for token in tokens:
        for child in token.children or []:
            if child.type != "link_open":
                continue
            target = urlparse(child.attrGet("href"))
            if target.scheme:
                continue
            path = (ROOT / unquote(target.path)).resolve()
            require(path.is_relative_to(ROOT), "本地引用超出项目目录")
            if path != DELIVERY / "validation-report.json":
                require(path.is_file(), f"引用文件不存在：{path.name}")
            local_links.add(str(path.relative_to(ROOT)))
    coverage = {}
    for prefix, count in [("FD-", 57), ("FR-", 12), ("AC-", 12), ("AC-H5-", 6), ("NFR-", 13), ("BQA", 18)]:
        found = set(re.findall(rf"(?<![A-Z-]){re.escape(prefix)}\d{{2}}(?!\d)", content))
        expected = {f"{prefix}{number:02d}" for number in range(1, count + 1)}
        require(expected <= found, f"缺少追溯标识：{sorted(expected - found)}")
        coverage[prefix] = count
    return {"status": "PASS", "headings": headings, "tables": sum(t.type == "table_open" for t in tokens), "mermaid_blocks": sum(t.type == "fence" and t.info == "mermaid" for t in tokens), "local_links": sorted(local_links), "coverage": coverage, "mermaid_rendering": "NOT_RUN"}


def references(value):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def check_protocol():
    paths = [DELIVERY / "protocol.schema.json", DELIVERY / "runtime.schema.json", ROOT / "data-design/dataset.schema.json", ROOT / "data-design/case-extension.schema.json"]
    schemas = {path: load_json(path) for path in paths}
    registry = Registry().with_resources((path.as_uri(), Resource.from_contents(schema)) for path, schema in schemas.items())
    reference_count = 0
    for path, schema in schemas.items():
        Draft202012Validator.check_schema(schema)
        resolver = registry.resolver(path.as_uri())
        for reference in references(schema):
            resolver.lookup(reference)
            reference_count += 1
    definitions = schemas[DELIVERY / "protocol.schema.json"]["$defs"]
    with (DELIVERY / "api-catalog.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    require(len({row["operation_id"] for row in rows}) == len(rows), "操作标识重复")
    require(len({(row["method"], row["path"]) for row in rows}) == len(rows), "方法与路径重复")
    for row in rows:
        require(all(row.values()), f"操作目录含有空值：{row['operation_id']}")
        require(row["input_schema"] in definitions and row["output_schema"] in definitions, f"Schema 引用缺少：{row['operation_id']}")
        require(row["method"] in {"GET", "POST", "PUT"}, "接口方法未登记")
        require(row["timeout_class"] in {"read", "write", "upload", "provider"}, "接口超时类别未登记")
        if row["transaction"] not in {"read_snapshot", "file_snapshot", "intent_query"}:
            require(row["idempotency"] in {"intent", "natural", "flow_key"}, f"写操作缺少幂等规则：{row['operation_id']}")
        if row["path"].startswith("/support/"):
            require(row["caller"] == "support" and row["auth"] not in {"anonymous", "session"}, "受控操作缺少岗位或资源授权")
    require({f"FR-{i:02d}" for i in range(1, 13)} <= {row["requirement"] for row in rows}, "接口需求覆盖不完整")
    require(definitions["RecordInput"]["additionalProperties"] is False, "家庭输入不能接收额外字段")
    require(not ({"patient_id", "author_id", "verification_status", "approved_by"} & set(definitions["RecordInput"]["properties"])), "家庭输入含保护字段")
    require("ms_total" not in definitions["FamilyRecord"]["properties"], "原始记录包含内部评分")
    return {"status": "PASS", "operations": len(rows), "definitions": len(definitions), "resolved_schema_references": reference_count, "runtime_schema": "PASS", "business_validation": "NOT_RUN"}, registry


def check_actual_case(registry):
    path = ROOT / "data-design/case-design-projection.json"
    data = load_json(path)
    protocol_uri = (DELIVERY / "protocol.schema.json").as_uri()
    medical_validator = Draft202012Validator({"$ref": protocol_uri + "#/$defs/MedicalTime"}, registry=registry, format_checker=FormatChecker())
    for item in data["medical_times"]:
        medical_validator.validate(item)
    domain_validator = Draft202012Validator({"$ref": (ROOT / "data-design/case-extension.schema.json").as_uri()}, registry=registry, format_checker=FormatChecker())
    domain_validator.validate(data["case_details"])
    entity_validator = Draft202012Validator({"$ref": protocol_uri + "#/$defs/CaseEntity"}, registry=registry, format_checker=FormatChecker())
    for entities in data["case_details"].values():
        for item in entities:
            entity_validator.validate(item)
    return {"status": "PASS", "source": str(path.relative_to(ROOT)), "source_sha256": checksum(path), "medical_times": len(data["medical_times"]), "case_entities": {key: len(items) for key, items in data["case_details"].items()}, "clinical_import": "NOT_RUN", "api_readback": "NOT_RUN", "independent_clinical_answers": "BLOCKED"}


def run(command, commands):
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    commands.append({"command": command, "stdout": result.stdout, "stderr": result.stderr, "exit_code": result.returncode})
    if result.returncode:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
    result.check_returncode()
    return result.stdout


def check_database():
    initdb = shutil.which("initdb")
    pg_ctl = shutil.which("pg_ctl")
    require(initdb and pg_ctl, "缺少真实 PostgreSQL 工具")
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S-%f")
    directory = WORK / f"postgres-{stamp}"
    directory.mkdir(parents=True)
    cluster = directory / "cluster"
    commands = []
    run([initdb, "-D", str(cluster), "--encoding=UTF8", "--locale=C", "--auth=trust", "--username=backend_design_validator"], commands)
    run([pg_ctl, "-D", str(cluster), "-l", str(directory / "server.log"), "-o", "-h 127.0.0.1 -p 55441 -k ''", "-w", "start"], commands)
    try:
        with psycopg.connect(host="127.0.0.1", port=55441, dbname="postgres", user="backend_design_validator", autocommit=True) as connection:
            version = connection.execute("SHOW server_version").fetchone()[0]
            for path in [ROOT / "data-design/schema.sql", ROOT / "data-design/case-schema.sql", DELIVERY / "schema.sql", ROOT / "data-design/queries.sql", ROOT / "data-design/case-queries.sql"]:
                connection.execute(path.read_text(encoding="utf-8"))
            tables = [row[0] for row in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'sjia' ORDER BY tablename")]
            require(len(tables) == 88, "数据库表数量与后端设计不一致")
            require({"environment_gate", "service_role_grant", "write_intent", "upload_part", "config_validation", "correction_request"} <= set(tables), "运行结构缺少")
            task_constraint = connection.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'task_task_type_check'").fetchone()[0]
            require("file_validation" in task_constraint, "文件验证任务种类缺少")
            require("alert_evaluation" in task_constraint, "预警评估任务种类缺少")
            columns = {}
            for table in ["write_intent", "upload_part", "environment_gate", "config_validation", "correction_request", "alert_evaluation", "alert_evaluation_input", "alert_evaluation_assessment", "alert_signal_result", "alert_episode", "alert_decision", "alert_event", "record_attachment", "candidate_field", "relation_application"]:
                columns[table] = [dict(zip(["name", "type", "nullable"], row)) for row in connection.execute("SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_schema = 'sjia' AND table_name = %s ORDER BY ordinal_position", (table,))]
            constraints = connection.execute("""
                SELECT t.relname, c.contype,
                    ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(id, position)
                          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.id ORDER BY k.position),
                    r.relname,
                    ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY k(id, position)
                          JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.id ORDER BY k.position),
                    pg_get_constraintdef(c.oid)
                FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                LEFT JOIN pg_class r ON r.oid = c.confrelid
                WHERE n.nspname = 'sjia' AND t.relname = ANY(%s)
                ORDER BY t.relname, c.conname
            """, (list(columns),)).fetchall()
            required_constraints = [
                ("alert_evaluation", "u", ["task_id"], None, []),
                ("alert_evaluation", "f", ["task_id", "patient_id"], "task", ["id", "patient_id"]),
                ("alert_evaluation_input", "p", ["evaluation_id", "signal_id", "position"], None, []),
                ("alert_evaluation_input", "f", ["field_id", "record_version_id", "patient_id"], "record_field", ["id", "record_version_id", "patient_id"]),
                ("alert_evaluation_input", "f", ["verification_id", "field_id"], "verification_field", ["verification_id", "field_id"]),
                ("alert_evaluation_assessment", "f", ["assessment_id", "patient_id"], "assessment", ["id", "patient_id"]),
                ("alert_event", "f", ["evaluation_id", "patient_id"], "alert_evaluation", ["id", "patient_id"]),
                ("alert_decision", "u", ["evaluation_id", "rule_key"], None, []),
                ("alert_decision", "u", ["patient_id", "rule_key", "sequence"], None, []),
                ("alert_decision", "f", ["previous_decision_id", "patient_id", "rule_key"], "alert_decision", ["id", "patient_id", "rule_key"]),
                ("record_attachment", "f", ["record_version_id", "patient_id"], "record_version", ["id", "patient_id"]),
                ("record_attachment", "f", ["file_id", "patient_id"], "file_object", ["id", "patient_id"]),
            ]
            for expected in required_constraints:
                require(any(tuple(row[:5]) == expected for row in constraints), f"关键约束缺少：{expected[0:3]}")
            episode_index = connection.execute("""
                SELECT i.indisunique, pg_get_expr(i.indpred, i.indrelid)
                FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'sjia' AND c.relname = 'episode_one_open'
            """).fetchone()
            require(episode_index == (True, "(status = 'open'::text)"), "持续周期唯一约束不完整")
            require(any(c["name"] == "evaluation_id" and c["nullable"] == "NO" for c in columns["alert_event"]), "事件必须引用评估")
            row_count = connection.execute("SELECT count(*) FROM sjia.patient").fetchone()[0]
            require(row_count == 0, "结构检查不得生成临床病例")
            result = {"status": "PASS", "engine_version": version, "target_engine": "PostgreSQL 17", "target_version_execution": "PASS" if version.startswith("17.") else "NOT_RUN", "table_count": len(tables), "tables": tables, "runtime_columns": columns, "critical_constraints_checked": len(required_constraints), "constraints": constraints, "episode_unique_index": episode_index, "prepared_queries": "PASS", "rows_inserted": 0, "permission_and_concurrency": "NOT_RUN"}
    finally:
        run([pg_ctl, "-D", str(cluster), "-m", "fast", "-w", "stop"], commands)
        (directory / "commands.json").write_text(json.dumps(commands, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["command_log"] = str((directory / "commands.json").relative_to(ROOT))
    return result


def main():
    inputs = [DOCUMENT, ROOT / ".gitignore", *[path for path in DELIVERY.iterdir() if path.is_file() and path.name != "validation-report.json"], ROOT / "sJIA合并MAS预警平台PRD.md", ROOT / "sJIA合并MAS预警平台HLD.md", ROOT / "sJIA合并MAS预警平台数据LLD.md", ROOT / "sJIA合并MAS预警平台页面流程.md", ROOT / "guardian-h5/原型交互说明.md", ROOT / "guardian-h5/src/app/App.tsx", ROOT / "guardian-h5/src/components/SaveActions.tsx", ROOT / "guardian-h5/src/components/PhotoReview.tsx", ROOT / "guardian-h5/src/components/DictionaryField.tsx", ROOT / "data-design/schema.sql", ROOT / "data-design/case-schema.sql", ROOT / "data-design/queries.sql", ROOT / "data-design/case-queries.sql", ROOT / "data-design/dataset.schema.json", ROOT / "data-design/case-extension.schema.json", ROOT / "data-design/case-design-projection.json", ROOT / "data-design/clinical-dataset-status.json"]
    before = {str(path.relative_to(ROOT)): checksum(path) for path in inputs}
    document = check_document()
    protocol, registry = check_protocol()
    actual_case = check_actual_case(registry)
    database = check_database()
    require(before == {str(path.relative_to(ROOT)): checksum(path) for path in inputs}, "检查期间输入发生变化")
    report = {"status": "PASS", "capability": "DESIGN", "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "command": "PYTHONPATH=.data-work/deps python3 backend-design/validate.py", "environment": {"cwd": str(ROOT), "python": sys.version, "packages": {name: importlib.metadata.version(name) for name in ["jsonschema", "referencing", "markdown-it-py", "psycopg"]}}, "input_sha256": before, "document": document, "protocol": protocol, "actual_case": actual_case, "database": database, "business_acceptance": "BLOCKED", "provider_calls": "NOT_RUN", "visual_checks": "NOT_RUN"}
    (DELIVERY / "validation-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "operations": protocol["operations"], "definitions": protocol["definitions"], "database_tables": database["table_count"], "actual_case_medical_times": actual_case["medical_times"], "report": "backend-design/validation-report.json"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
