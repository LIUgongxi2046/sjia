import csv
import hashlib
import json
import os
import runpy
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".review-work"
OUTPUT = ROOT / "review-design/handshake-check.json"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(arguments):
    result = subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        result.check_returncode()
    return {"command": arguments, "exit_code": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def main():
    WORK.mkdir(exist_ok=True)
    os.environ["TMPDIR"] = str(WORK)
    inputs = sorted({
        *ROOT.glob("sJIA*.md"),
        *ROOT.glob("frontend-design/*"),
        *ROOT.glob("backend-design/*"),
        *ROOT.glob("alert-design/*"),
        *ROOT.glob("data-design/*.sql"),
        *ROOT.glob("data-design/*.schema.json"),
        ROOT / "data-design/case-design-projection.json",
        ROOT / "data-design/clinical-dataset-status.json",
        ROOT / "demo-data/current-patient/case.json",
        ROOT / "ui-delivery/tokens.json",
        ROOT / "ui-delivery/route-design-map.csv",
        ROOT / "guardian-h5/package-lock.json",
        ROOT / "guardian-h5/README.md",
        *ROOT.glob("guardian-h5/src/**/*.tsx"),
        ROOT / ".gitignore", Path(__file__).resolve(),
    })
    inputs = [path for path in inputs if path.is_file() and path.name != "sJIA合并MAS预警平台握手审查报告.md"]
    before = {str(path.relative_to(ROOT)): digest(path) for path in inputs}
    front = runpy.run_path(str(ROOT / "frontend-design/validate.py"))
    back = runpy.run_path(str(ROOT / "backend-design/validate.py"))
    alert = runpy.run_path(str(ROOT / "alert-design/validate.py"))
    data = runpy.run_path(str(ROOT / "data-design/validate.py"))
    document_checks = {
        "front": front["check_document"](),
        "back": back["check_document"](),
        "alert": alert["check_documents"]([
            ROOT / "sJIA合并MAS预警平台预警编排LLD.md",
            ROOT / "alert-design/clinical-evidence.md",
        ]),
        "data": data["check_documents"](),
    }
    protocol_check, registry = back["check_protocol"]()
    binding_check, operations = front["check_schema_and_bindings"]()
    route_check = front["check_routes"](operations)
    token_check = front["check_tokens"]()
    acceptance_check = front["check_acceptance"]()
    commands = [
        command(["node", "guardian-h5/node_modules/typescript/bin/tsc",
                 "-p", "frontend-design/tsconfig.json"]),
        command(["node", "frontend-design/check-types.mjs"]),
        command(["node", "guardian-h5/node_modules/typescript/bin/tsc",
                 "-p", "guardian-h5/tsconfig.json", "--noEmit"]),
    ]
    types = json.loads(commands[1]["stdout"])
    declared_routes = {row["path"] for row in read_csv(ROOT / "frontend-design/routes.csv")} | {"/"}
    if set(types["current_source_routes"]) != declared_routes:
        raise ValueError("实际路由与设计目录不一致")
    manifest = read_json(ROOT / "alert-design/design-manifest.json")
    schema_checks = {
        name: alert["check_schema"](ROOT / "alert-design" / name)
        for name in ["policy.schema.json", "node.schema.json"]
    }
    existing_interfaces = alert["check_existing_interfaces"](manifest)
    case_checks = {
        "back": back["check_actual_case"](registry),
        "alert": alert["check_real_case"](manifest),
    }
    database = back["check_database"]()
    definitions = read_json(ROOT / "backend-design/protocol.schema.json")["$defs"]
    runtime = read_json(ROOT / "backend-design/runtime.schema.json")
    required_properties = {
        "HomeView": {"assessment_state", "assessed_at", "current_alert_id"},
        "CandidateField": {"proposed_field", "field_config_version"},
        "FamilyRecord": {"patient_id", "display_id", "correction_reason", "author", "original_files", "original_files_complete"},
        "AlertView": {"patient_id", "display_id"}, "SummaryView": {"patient_id", "display_id"},
        "ApplicationView": {"version"}, "ApplicationAction": {"expected_version"},
        "RelationDecision": {"expected_version"}, "SessionView": {"csrf_token"},
        "RequestView": {"operation_id", "target_key", "prepared_at"},
        "RecordInput": {"original_file_ids"},
        "FieldConfigView": {"config_version", "fields", "watch_field_codes"},
        "SourceResolution": {"patient_id", "record_id", "version", "record_version_id", "field_id"},
    }
    missing = {name: sorted(fields - set(definitions[name]["required"]))
               for name, fields in required_properties.items()}
    required_operations = {"field_config_read", "source_resolve", "history_search", "application_read", "request_recent_account", "auth_bootstrap"}
    task_type = manifest["registered_task_type"]
    observations = {
        "missing_entities": sorted(set(manifest["registered_entities"]) - set(database["tables"])),
        "missing_required_properties": {name: fields for name, fields in missing.items() if fields},
        "missing_operations": sorted(required_operations - set(operations)),
        "task_type_registered": task_type in definitions["TaskView"]["properties"]["task_type"]["enum"],
        "task_result_registered": task_type in definitions["TaskView"]["properties"]["result_type"]["enum"],
        "runtime_task_registered": task_type in runtime["properties"]["tasks"]["items"]["properties"]["task_type"]["enum"],
        "candidate_uses_field_input": definitions["CandidateField"]["properties"]["proposed_field"]["oneOf"][0] == {"$ref": "#/$defs/FieldInput"},
        "history_time_filter": "medical_time_id" in definitions["RecordSearch"]["properties"],
        "request_recovery_path": operations["request_recent_account"]["path"],
        "critical_constraints_checked": database["critical_constraints_checked"],
    }
    if observations["missing_entities"] or observations["missing_required_properties"] or observations["missing_operations"]:
        raise ValueError("跨层接收存在缺少定义")
    if not all(observations[name] for name in ["task_type_registered", "task_result_registered", "runtime_task_registered", "candidate_uses_field_input", "history_time_filter"]):
        raise ValueError("跨层类型或映射不一致")
    if observations["request_recovery_path"] != "/requests":
        raise ValueError("本人无患儿意图查询路径不一致")
    after = {name: digest(ROOT / name) for name in before}
    if before != after:
        raise ValueError("审查期间输入发生变化")
    report = {
        "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "mode": "DESIGN_REVIEW", "capability": "DESIGN",
        "design_decision": "CONDITIONALLY_APPROVED", "design_handshakes": "PASS",
        "execution_evidence": "INSUFFICIENT_EVIDENCE",
        "command": "PYTHONPATH=.data-work/deps python3 review-design/check-handshakes.py",
        "environment": {"python": sys.version, "node": command(["node", "--version"])["stdout"].strip(),
                        "temporary_directory": ".review-work", "git": "ABSENT"},
        "input_sha256": before, "documents": document_checks,
        "protocol": protocol_check, "bindings": binding_check, "routes": route_check,
        "tokens": token_check, "acceptance": acceptance_check,
        "types": types, "commands": commands, "alert_schemas": schema_checks,
        "existing_interfaces": existing_interfaces, "case_checks": case_checks,
        "database": database, "cross_layer_observations": observations,
        "external_dependencies": {"D01-D09": "PENDING", "identity_provider": "PENDING", "clinical_gold": "PENDING"},
        "business_integration": "NOT_RUN", "clinical_validation": "NOT_RUN",
        "visual_checks": "NOT_RUN", "provider_calls": "NOT_RUN",
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"design_decision": report["design_decision"],
                      "operations": protocol_check["operations"], "bindings": binding_check["bindings"],
                      "types": types["types"], "database_tables": database["table_count"],
                      "cross_layer_observations": observations,
                      "report": str(OUTPUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
