import argparse
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
from psycopg import sql
from jsonschema import Draft202012Validator, FormatChecker
from markdown_it import MarkdownIt


ROOT = Path(__file__).resolve().parents[1]
DELIVERY = ROOT / "data-design"
WORK = ROOT / ".data-work"
DOCUMENT = ROOT / "sJIA合并MAS预警平台数据LLD.md"
PARSER = MarkdownIt("commonmark").enable("table")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, log):
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    log.append({"command": command, "stdout": result.stdout, "stderr": result.stderr, "exit_code": result.returncode})
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
    result.check_returncode()
    return result.stdout


def check_documents():
    text = DOCUMENT.read_text(encoding="utf-8")
    tokens = PARSER.parse(text)
    headings = [tokens[index + 1].content for index, token in enumerate(tokens) if token.type == "heading_open" and token.tag == "h2"]
    require(len(headings) == 7, "数据设计必须包含七个主要章节")
    for number, heading in enumerate(headings, 1):
        require(heading.startswith(f"{number}. "), "主要章节编号不连续")
    links = []
    for token in tokens:
        for child in token.children or []:
            if child.type != "link_open":
                continue
            parsed = urlparse(child.attrGet("href"))
            if parsed.scheme:
                continue
            target = (ROOT / unquote(parsed.path)).resolve()
            require(target.is_relative_to(ROOT), "本地引用超出项目目录")
            if target != DELIVERY / "validation-report.json":
                require(target.is_file(), f"本地引用不存在：{target.name}")
            links.append(str(target.relative_to(ROOT)))
    coverage = {}
    for prefix, total in [("FD-", 57), ("FR-", 12), ("AC-", 12), ("QA", 12), ("DQ", 10)]:
        actual = set(re.findall(rf"(?<![A-Z-]){re.escape(prefix)}\d{{2}}(?!\d)", text))
        expected = {f"{prefix}{number:02d}" for number in range(1, total + 1)}
        require(expected <= actual, f"映射缺少：{sorted(expected - actual)}")
        coverage[prefix] = len(expected)
    return {"status": "PASS", "headings": headings, "tables": sum(t.type == "table_open" for t in tokens), "mermaid_blocks": sum(t.type == "fence" and t.info == "mermaid" for t in tokens), "coverage": coverage, "local_links": sorted(set(links))}


def check_dataset():
    schema = json.loads((DELIVERY / "dataset.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    dataset = json.loads((DELIVERY / "sample_dataset.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(dataset)
    require(dataset["dataset_kind"] == "project_documentation", "项目文档样本类型不一致")
    ids = set()
    references = []
    for record in dataset["records"]:
        require(record["chunk_id"] not in ids, "样本引用标识重复")
        ids.add(record["chunk_id"])
        source = (ROOT / record["source_ref"]).resolve()
        require(source.is_relative_to(ROOT), "样本来源超出项目范围")
        content = source.read_text(encoding="utf-8")
        require(record["version"] in content, "来源版本与样本不符")
        parsed = PARSER.parse(content)
        section = next((i for i, token in enumerate(parsed) if token.type == "heading_open" and parsed[i + 1].content == record["section_title"]), None)
        require(section is not None, "样本章节不存在")
        start_line = parsed[section].map[0]
        end_line = len(content.splitlines())
        for token in parsed[section + 3:]:
            if token.type == "heading_open":
                end_line = token.map[0]
                break
        section_text = "\n".join(content.splitlines()[start_line:end_line])
        require(record["markdown_text"] in section_text, "样本原文不在指定章节")
        references.append({"chunk_id": record["chunk_id"], "source_ref": record["source_ref"], "source_sha256": digest(source), "text_sha256": hashlib.sha256(record["markdown_text"].encode()).hexdigest(), "section_start_line": start_line + 1, "source_match": "PASS"})
    status = json.loads((DELIVERY / "clinical-dataset-status.json").read_text(encoding="utf-8"))
    require(status["status"] == "LOCAL_DEMO_AVAILABLE" and status["available_records"] == 1, "病例 demo 状态与现有资料不符")
    require(status["import_status"] == "NOT_RUN" and status["clinically_verified_records"] == 0, "正式导入和临床核对尚未执行")
    subprocess.run([sys.executable, str(ROOT / "demo-data/validate.py")], cwd=ROOT, check=True)
    demo = json.loads((ROOT / "demo-data/validation-report.json").read_text(encoding="utf-8"))
    require(demo["status"] == "PASS", "病例 demo 检查未通过")
    subprocess.run([sys.executable, str(DELIVERY / "project-case.py")], cwd=ROOT, check=True)
    projection = json.loads((DELIVERY / "case-design-projection.json").read_text(encoding="utf-8"))
    return {"json_schema": "PASS", "actual_document_records": len(ids), "source_checks": references, "local_demo": demo["counts"],
            "clinical_import": "NOT_RUN", "clinical_schema_instance_validation": "NOT_RUN", "demo_schema_validation": "PASS",
            "case_extension_schema_validation": "PASS", "projected_entities": {key: len(value) for key, value in projection["case_details"].items()},
            "projection_sha256": digest(DELIVERY / "case-design-projection.json")}


def check_case_times(connection):
    projection = json.loads((DELIVERY / "case-design-projection.json").read_text(encoding="utf-8"))
    patient = json.loads((ROOT / "demo-data/current-patient/case.json").read_text(encoding="utf-8"))["patient"]
    with connection.transaction(force_rollback=True):
        connection.execute("INSERT INTO sjia.patient(id, display_id, status, created_at) VALUES (%s,%s,'restricted',CURRENT_TIMESTAMP)", (patient["id"], patient["display_id"]))
        for medical_time in projection["medical_times"]:
            columns = list(medical_time)
            connection.execute(sql.SQL("INSERT INTO sjia.medical_time ({}) VALUES ({})").format(
                sql.SQL(",").join(map(sql.Identifier, columns)), sql.SQL(",").join(sql.Placeholder() for _ in columns)), list(medical_time.values()))
            row = connection.execute(sql.SQL("SELECT {} FROM sjia.medical_time WHERE id = %s").format(sql.SQL(",").join(map(sql.Identifier, columns))), (medical_time["id"],)).fetchone()
            normalized = {key: (str(value) if key in ("id", "patient_id", "local_date") and value is not None else value) for key, value in zip(columns, row)}
            require(normalized == medical_time, "医学时间读回与实际病例不一致")
    require(connection.execute("SELECT count(*) FROM sjia.patient").fetchone()[0] == 0, "验证病例数据未清理")
    return {"status": "PASS", "actual_case_medical_times": len(projection["medical_times"]), "transaction": "rolled_back", "production_import": "NOT_RUN"}


def check_case_columns(connection):
    schema = json.loads((DELIVERY / "case-extension.schema.json").read_text(encoding="utf-8"))
    contexts = set(schema["$defs"]["context"]["properties"])
    mappings = {
        "profile": ("patient_profile_version", set()), "episode": ("clinical_episode", set()),
        "group": ("record_field_group", {"members"}), "statement": ("clinical_statement", {"evidence"}),
        "imaging": ("imaging_study", {"sites"}), "imageLink": ("imaging_asset_link", set()),
        "medication": ("medication_event", {"items"}), "procedure": ("procedure_event", set()),
        "score": ("external_score_observation", {"components"}), "followup": ("followup_encounter", {"references"}),
        "issue": ("data_quality_issue", {"references"})
    }
    checked = []
    for name, (table, nested) in mappings.items():
        expected = contexts | (set(schema["$defs"][name]["properties"]) - nested)
        actual = {row[0] for row in connection.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'sjia' AND table_name = %s", (table,))}
        require(actual == expected, f"病例 SQL 与交换字段不一致：{table}")
        checked.append(table)
    child_mappings = {
        "record_field_group_member": (schema["$defs"]["group"]["properties"]["members"]["items"], {"group_id", "patient_id", "record_version_id"}),
        "statement_evidence": (schema["$defs"]["statement"]["properties"]["evidence"]["items"], {"statement_id", "patient_id"}),
        "imaging_study_site": (schema["$defs"]["imaging"]["properties"]["sites"]["items"], {"study_id", "patient_id"}),
        "medication_event_item": (schema["$defs"]["medicationItem"], {"event_id", "patient_id", "record_version_id"}),
        "external_score_component": (schema["$defs"]["score"]["properties"]["components"]["items"], {"score_id", "patient_id", "record_version_id"}),
        "followup_reference": (schema["$defs"]["followupReference"], {"encounter_id", "patient_id"}),
        "data_quality_issue_reference": (schema["$defs"]["issueReference"], {"issue_id", "patient_id"})
    }
    for table, (definition, inherited) in child_mappings.items():
        expected = set(definition["properties"]) | inherited
        actual = {row[0] for row in connection.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'sjia' AND table_name = %s", (table,))}
        require(actual == expected, f"病例子项 SQL 与交换字段不一致：{table}")
        checked.append(table)
    return {"status": "PASS", "checked_entity_tables": checked}


def check_database(port):
    initdb = shutil.which("initdb")
    pg_ctl = shutil.which("pg_ctl")
    require(initdb is not None and pg_ctl is not None, "缺少实际 PostgreSQL 工具")
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S-%f")
    directory = WORK / f"postgres-{stamp}"
    directory.mkdir(parents=True)
    cluster = directory / "cluster"
    commands = []
    run([initdb, "-D", str(cluster), "--encoding=UTF8", "--locale=C", "--auth=trust", "--username=data_design_validator"], commands)
    run([pg_ctl, "-D", str(cluster), "-l", str(directory / "server.log"), "-o", f"-h 127.0.0.1 -p {port} -k ''", "-w", "start"], commands)
    try:
        with psycopg.connect(host="127.0.0.1", port=port, dbname="postgres", user="data_design_validator", autocommit=True) as connection:
            version = connection.execute("SHOW server_version").fetchone()[0]
            connection.execute((DELIVERY / "schema.sql").read_text(encoding="utf-8"))
            connection.execute((DELIVERY / "case-schema.sql").read_text(encoding="utf-8"))
            connection.execute((DELIVERY / "queries.sql").read_text(encoding="utf-8"))
            connection.execute((DELIVERY / "case-queries.sql").read_text(encoding="utf-8"))
            case_times = check_case_times(connection)
            case_columns = check_case_columns(connection)
            tables = [row[0] for row in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'sjia' ORDER BY tablename")]
            require(len(tables) == 82, "数据表数量与当前设计不一致")
            expected_case_tables = {"patient_profile_version", "clinical_episode", "record_field_group", "record_field_group_member", "clinical_statement", "statement_evidence", "imaging_study", "imaging_study_site", "imaging_asset_link", "medication_event", "medication_event_item", "procedure_event", "external_score_observation", "external_score_component", "followup_encounter", "followup_reference", "data_quality_issue", "data_quality_issue_reference"}
            require(expected_case_tables <= set(tables), "病例结构表缺少")
            row_counts = {}
            for table in tables:
                row_counts[table] = connection.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier("sjia"), sql.Identifier(table))).fetchone()[0]
            require(all(count == 0 for count in row_counts.values()), "结构验证数据库包含未授权样本")
            constraints = connection.execute("SELECT contype, count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE n.nspname = 'sjia' GROUP BY contype ORDER BY contype").fetchall()
            required_constraints = {"record_current_version_fk", "candidate_task_fk", "assessment_task_fk", "summary_task_fk", "draft_correction_fk", "grant_record_fk", "patient_current_profile_fk", "medical_time_partial_shape"}
            actual_constraints = {row[0] for row in connection.execute("SELECT conname FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE n.nspname = 'sjia'")}
            require(required_constraints <= actual_constraints, "关键复合引用约束缺少")
            deferred = connection.execute("SELECT condeferrable, condeferred FROM pg_constraint WHERE conname = 'record_current_version_fk'").fetchone()
            require(deferred == (True, True), "当前版本循环引用必须延迟至提交检查")
            unsafe_fks = connection.execute("SELECT conname FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE n.nspname = 'sjia' AND contype = 'f' AND confdeltype = 'c'").fetchall()
            require(not unsafe_fks, "结构含有未经批准的级联删除")
            result = {"status": "PASS", "engine_version": version, "target_engine": "PostgreSQL 17", "target_version_execution": "NOT_RUN" if not version.startswith("17.") else "PASS", "table_count": len(tables), "tables": tables, "constraints_by_type": dict(constraints), "rows_inserted": 0, "prepared_query_validation": "PASS", "clinical_data_readback": "BLOCKED", "permission_and_concurrency_validation": "NOT_RUN"}
            result["case_time_readback"] = case_times
            result["case_exchange_column_check"] = case_columns
            result["rows_retained"] = 0
            result["rows_inserted"] = case_times["actual_case_medical_times"] + 1
    finally:
        run([pg_ctl, "-D", str(cluster), "-m", "fast", "-w", "stop"], commands)
        (directory / "commands.json").write_text(json.dumps(commands, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["command_log"] = str((directory / "commands.json").relative_to(ROOT))
    result["server_log"] = str((directory / "server.log").relative_to(ROOT))
    result["server_stopped"] = True
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=55439)
    arguments = parser.parse_args()
    WORK.mkdir(exist_ok=True)
    started = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    inputs = [DOCUMENT, ROOT / ".gitignore", ROOT / "sJIA合并MAS预警平台PRD.md", ROOT / "sJIA合并MAS预警平台HLD.md", ROOT / "sJIA合并MAS预警平台需求输入.md", ROOT / "sJIA合并MAS预警平台产品目标.md", ROOT / "guardian-h5/src/features/registration/ReportsPage.tsx", ROOT / "guardian-h5/src/components/SaveActions.tsx", ROOT / "guardian-h5/src/components/DictionaryField.tsx"]
    inputs.extend(path for path in DELIVERY.iterdir() if path.is_file() and path.name not in ("validation-report.json", "case-design-projection.json"))
    inputs.extend(path for path in (ROOT / "demo-data").rglob("*") if path.is_file() and not path.name.endswith("validation-report.json"))
    before = {str(path.relative_to(ROOT)): digest(path) for path in sorted(set(inputs))}
    document_result = check_documents()
    dataset_result = check_dataset()
    database_result = check_database(arguments.port)
    after = {name: digest(ROOT / name) for name in before}
    require(before == after, "验证期间输入发生改变")
    report = {
        "capability": "DESIGN", "status": "PASS", "started_at": started,
        "finished_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "command": "PYTHONPATH=.data-work/deps python3 data-design/validate.py --port " + str(arguments.port),
        "python_version": sys.version,
        "dependencies": {name: importlib.metadata.version(name) for name in ("jsonschema", "markdown-it-py", "psycopg")},
        "document": document_result, "dataset": dataset_result, "database": database_result,
        "inputs_sha256": before,
        "mermaid_syntax_execution": "NOT_RUN", "visual_validation": "NOT_RUN",
        "business_validation": "BLOCKED", "clinical_validation": "BLOCKED",
        "limitation": "结果证明设计文档、用户病例、领域转换、交换字段、SQL 结构及实际医学时间读回检查通过；病例尚未导入生产数据库，没有业务接口、临床核对或目标 PostgreSQL 17 执行证据"
    }
    (DELIVERY / "validation-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"design_checks": report["status"], "tables": database_result["table_count"], "document_samples": dataset_result["actual_document_records"], "engine_version": database_result["engine_version"], "business_validation": report["business_validation"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
