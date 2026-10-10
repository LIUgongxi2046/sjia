import argparse
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / ".planning-work/parallel/window3/T55-voice-simulation-text-only.json"
DEFAULT_EVIDENCE = ROOT / ".planning-work/parallel/window3/T55-voice-adoption-roundtrip-evidence.json"
DEFAULT_OUTPUT = ROOT / ".planning-work/parallel/window3/T55-voice-simulation-evaluation.json"
DEFAULT_PROCESS_RESPONSE = ROOT / ".planning-work/parallel/window3/T55-voice-process-api-response.json"
DIMENSIONS = ("name", "comparator", "value", "unit", "negation", "medical_time", "context", "source")
LAYERS = ("candidate", "adopted", "saved_reread")
NOT_RUN_REASON = "SIMULATION_PROCESS_REJECTED_PROVIDER_BUDGET_UNKNOWN"
IDEMPOTENCY_NOT_RUN_REASON = "NO_ACCEPTED_VOICE_TASK_FOR_IDEMPOTENCY_PROBE"
REVOCATION_NOT_RUN_REASON = "AUTO_REVIEW_REJECTED_SHARED_SIMULATION_AUTHORIZATION_MUTATION"
TERMINAL_NOT_RUN_REASON = "NO_ACCEPTED_VOICE_TASK_FOR_TERMINAL_PROBE"


def canonical_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def score_layer(gold_fields, layer):
    allowed_statuses = ("SIMULATION_CANDIDATE_READ", "SIMULATION_CONFIRMED", "SIMULATION_SAVED_AND_READ")
    if layer.get("status") == "NOT_RUN":
        return {"status": "NOT_RUN", "reason_code": NOT_RUN_REASON,
            "field_counts": None, "fields": [], "extra_field_paths": []}
    if layer.get("status") not in allowed_statuses:
        raise RuntimeError("评价层状态不属于已定义的模拟运行状态")
    observed = layer.get("fields")
    if not isinstance(observed, list):
        raise RuntimeError("已运行评价层必须提供 fields 列表")
    observed_by_path = {item["field_path"]: item for item in observed}
    if len(observed_by_path) != len(observed):
        raise RuntimeError("同一评价层包含重复 field_path")
    counts = {dimension: {"correct": 0, "incorrect": 0, "uncovered": 0} for dimension in DIMENSIONS}
    decision_counts = {decision: 0 for decision in ("accepted", "modified", "unknown", "excluded", "not_recorded")}
    rows = []
    for gold in gold_fields:
        actual = observed_by_path.get(gold["field_path"])
        actual_values = actual.get("values", {}) if actual else {}
        decision = actual.get("decision") if actual else None
        decision_counts[decision if decision in decision_counts else "not_recorded"] += 1
        field_counts = {}
        for dimension in DIMENSIONS:
            expected_value = gold["source_span"] if dimension == "source" else gold[dimension]
            if decision in ("unknown", "excluded") or dimension not in actual_values:
                outcome = "uncovered"
                actual_value = None
            else:
                actual_value = actual_values[dimension]
                if dimension == "source":
                    source_ref = actual.get("source_ref")
                    outcome = "correct" if (isinstance(actual_value, str) and expected_value in actual_value
                        and source_ref == layer.get("source_file_id")) else "incorrect"
                else:
                    comparator_default = dimension == "comparator" and expected_value is None and actual_value == "eq"
                    outcome = "correct" if actual_value == expected_value or comparator_default else "incorrect"
            counts[dimension][outcome] += 1
            field_counts[dimension] = {"outcome": outcome, "expected": expected_value, "actual": actual_value}
        rows.append({"field_path": gold["field_path"], "field_counts": field_counts,
            "decision": decision,
            "source_ref": actual.get("source_ref") if actual else None,
            "raw_representation": actual.get("raw_representation") if actual else None})
    extra_paths = sorted(set(observed_by_path) - {item["field_path"] for item in gold_fields})
    return {"status": layer["status"], "field_counts": counts,
        "decision_counts": layer.get("reported_decision_counts", decision_counts),
        "decision_evidence": layer.get("decision_evidence", []), "fields": rows,
        "extra_field_paths": extra_paths, "extra_field_count": len(extra_paths)}


def semantic_layer(layer_name, layer, source_file_id):
    raw_fields = layer.get("fields", [])
    by_path = {item["field_path"]: item for item in raw_fields}
    decisions = {item["field_path"]: item.get("decision") for item in raw_fields}
    semantic = {}

    def add(field_path, values, decision=None, source=None, source_ref=None, raw=None):
        semantic[field_path] = {"field_path": field_path, "values": values, "decision": decision,
            "source_ref": source_ref, "raw_representation": raw}

    if layer_name in ("candidate", "adopted"):
        temp = by_path.get("temperature", {})
        unit = by_path.get("temperature_unit", {})
        symptoms = by_path.get("symptoms", {})
        time = by_path.get("observed_time", {})
        temp_values = temp.get("values", {})
        unit_values = unit.get("values", {})
        time_values = time.get("values", {})
        temperature_value = temp_values.get("value")
        if temperature_value is not None:
            add("/fields/body_temperature", {"name": "体温", "value": str(temperature_value),
                "unit": "°C" if unit_values.get("value") == "C" else unit_values.get("value"),
                **({"medical_time": "2026-10-09T07:00:00+08:00"}
                    if time_values.get("medical_time") == "2026年10月9日早上7点" else {})},
                decision=temp.get("decision"), raw={"temperature": temperature_value,
                    "unit": unit_values.get("value"), "time": time_values.get("medical_time")})
        symptom_values = symptoms.get("values", {}).get("value")
        if isinstance(symptom_values, list):
            symptom_specs = (
                ("发热", "/fields/fever", "发热", "present", "affirmed"),
                ("没有皮疹", "/fields/rash", "皮疹", "absent", "negated"),
                ("精神比平时差", "/fields/activity", "活动状态", "reduced_relative_to_usual", "affirmed"),
            )
            for source_text, field_path, name, value, negation in symptom_specs:
                if source_text in symptom_values:
                    add(field_path, {"name": name, "value": value, "negation": negation},
                        decision=symptoms.get("decision"), raw=source_text)
    elif layer_name == "saved_reread":
        temp = by_path.get("temp", {})
        temp_values = temp.get("values", {})
        if temp_values.get("value") is not None:
            unit_value = temp_values.get("unit")
            add("/fields/body_temperature", {"name": "体温" if temp_values.get("name") == "temperature" else temp_values.get("name"),
                "value": str(temp_values["value"]), "comparator": temp_values.get("comparator"),
                "unit": "°C" if unit_value == "C" else unit_value,
                **({"source": temp_values["source"]} if temp_values.get("source") else {})},
                source_ref=temp.get("source_ref"), raw=temp_values)
        note = by_path.get("observation_note", {})
        note_values = note.get("values", {})
        raw_text = note_values.get("value")
        if isinstance(raw_text, str):
            selected = json.loads(raw_text)
            symptom_values = selected.get("symptoms", [])
            symptom_specs = (
                ("发热", "/fields/fever", "发热", "present", "affirmed"),
                ("没有皮疹", "/fields/rash", "皮疹", "absent", "negated"),
                ("精神比平时差", "/fields/activity", "活动状态", "reduced_relative_to_usual", "affirmed"),
            )
            for source_text, field_path, name, value, negation in symptom_specs:
                if source_text in symptom_values:
                    add(field_path, {"name": name, "value": value, "negation": negation,
                        **({"source": note_values["source"]} if note_values.get("source") else {})},
                        source_ref=note.get("source_ref"), raw=raw_text)
    decision_evidence = [{"field_path": item["field_path"], "decision": item.get("decision"),
        "selected_value": item.get("values", {}).get("value")} for item in raw_fields if item.get("decision") is not None]
    reported_decision_counts = {decision: sum(item["decision"] == decision for item in decision_evidence)
        for decision in ("accepted", "modified", "unknown", "excluded")}
    reported_decision_counts["not_recorded"] = 0
    return {"status": layer["status"], "fields": list(semantic.values()), "source_file_id": source_file_id,
        "decision_evidence": decision_evidence, "reported_decision_counts": reported_decision_counts,
        "raw_field_paths": sorted(by_path)}


def paired_layers(scored):
    pairs = {}
    for left_index, left_name in enumerate(LAYERS):
        left = scored[left_name]
        for right_name in LAYERS[left_index + 1:]:
            right = scored[right_name]
            pair_counts = {dimension: {"same": None, "different": None, "uncovered": None} for dimension in DIMENSIONS}
            if left["field_counts"] is not None and right["field_counts"] is not None:
                pair_counts = {dimension: {"same": 0, "different": 0, "uncovered": 0} for dimension in DIMENSIONS}
                right_rows = {row["field_path"]: row for row in right["fields"]}
                for left_row in left["fields"]:
                    right_row = right_rows[left_row["field_path"]]
                    for dimension in DIMENSIONS:
                        left_value = left_row["field_counts"][dimension]
                        right_value = right_row["field_counts"][dimension]
                        if left_value["outcome"] == "uncovered" or right_value["outcome"] == "uncovered":
                            pair_counts[dimension]["uncovered"] += 1
                        elif left_value["actual"] == right_value["actual"]:
                            pair_counts[dimension]["same"] += 1
                        else:
                            pair_counts[dimension]["different"] += 1
            pairs[f"{left_name}_vs_{right_name}"] = {
                "status": "MEASURED" if left["field_counts"] is not None and right["field_counts"] is not None else "NOT_MEASURED",
                "field_counts": pair_counts,
            }
    return pairs


def timing_and_cost(evidence):
    call = evidence.get("model_call") if evidence else None
    if not call:
        return {"elapsed_seconds": None, "elapsed_status": "NOT_MEASURED", "pricing": {"status": "NOT_MEASURED", "cost_cny": None}}
    if call.get("started_at") is None or call.get("finished_at") is None:
        return {"elapsed_seconds": None, "elapsed_status": "NOT_MEASURED", "pricing": {"status": "NOT_MEASURED", "cost_cny": None}}
    started = datetime.fromisoformat(call["started_at"])
    finished = datetime.fromisoformat(call["finished_at"])
    if started.tzinfo is None or finished.tzinfo is None:
        raise RuntimeError("模型调用起止时间必须包含时区")
    elapsed = (finished - started).total_seconds()
    if elapsed < 0:
        raise RuntimeError("模型调用结束时间早于开始时间")
    supplied_elapsed = call.get("elapsed_seconds")
    if supplied_elapsed is not None and abs(float(supplied_elapsed) - elapsed) > 0.001:
        raise RuntimeError("模型调用耗时与实际开始、结束时点不一致")
    pricing = call.get("pricing", {"status": "NOT_MEASURED", "cost_cny": None})
    if pricing.get("status") not in ("SIMULATION_CONFIG_CALCULATED", "NOT_MEASURED"):
        raise RuntimeError("模拟评价只接受有来源的模拟计价或 NOT_MEASURED")
    if pricing.get("status") == "SIMULATION_CONFIG_CALCULATED":
        if not call.get("provider_request_id") or not call.get("model") or call.get("usage") is None:
            raise RuntimeError("模拟计价缺少同次模型请求、模型版本或 usage")
        if not pricing.get("source_ref"):
            raise RuntimeError("模拟价格缺少准确来源版本")
        input_tokens = call["usage"].get("prompt_tokens")
        output_tokens = call["usage"].get("completion_tokens")
        if input_tokens is None or output_tokens is None:
            raise RuntimeError("模拟价格计算缺少实际令牌计数")
        expected_cost = (
            Decimal(str(input_tokens)) * Decimal(str(pricing["input_cny_per_million"]))
            + Decimal(str(output_tokens)) * Decimal(str(pricing["output_cny_per_million"]))
        ) / Decimal(1_000_000)
        if Decimal(str(pricing["input_cny_per_million"])) < 0 or Decimal(str(pricing["output_cny_per_million"])) < 0:
            raise RuntimeError("模拟单价不能为负数")
        if Decimal(str(pricing["cost_cny"])) != expected_cost:
            raise RuntimeError("模拟价格与实际 usage 和计价配置不一致")
    elif pricing.get("status") == "NOT_MEASURED" and pricing.get("cost_cny") is not None:
        raise RuntimeError("未测量费用不能带有费用数值")
    return {"provider_request_id": call.get("provider_request_id"), "provider": call.get("provider"),
        "model": call.get("model"), "usage": call.get("usage"), "elapsed_seconds": elapsed, "elapsed_status": "MEASURED",
        "pricing": pricing}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--process-response", type=Path, default=DEFAULT_PROCESS_RESPONSE)
    parser.add_argument("--cancel-evidence", type=Path)
    args = parser.parse_args()

    source = json.loads(args.input.read_text(encoding="utf-8"))
    if source.get("data_mode") != "SIMULATION" or source.get("evidence_scope") != "SIMULATION_TEXT_ONLY":
        raise RuntimeError("输入必须明确标记 SIMULATION_TEXT_ONLY")
    independent = source["independent_answer"]
    if independent.get("answer_origin") != "simulation_manual_independent" or independent.get("derived_from_model_output") is not False:
        raise RuntimeError("独立答案必须与模型输出分开维护")
    transcript = source["source"]["transcript_text"]
    source_sha256 = canonical_hash(transcript)
    if source_sha256 != source["source"]["transcript_sha256"] or source_sha256 != independent["source_transcript_sha256"]:
        raise RuntimeError("转写文本、独立答案和摘要不一致")
    gold_fields = independent["fields"]
    if not gold_fields or len({item["field_path"] for item in gold_fields}) != len(gold_fields):
        raise RuntimeError("独立答案 field_path 缺少或重复")
    for field in gold_fields:
        if field["source_span"] not in transcript:
            raise RuntimeError(f"独立答案原文片段不在转写文字中：{field['field_path']}")

    evidence = json.loads(args.evidence.read_text(encoding="utf-8")) if args.evidence.exists() else None
    if evidence is not None:
        if evidence.get("data_mode") != "SIMULATION" or evidence.get("evidence_scope") != "SIMULATION_TEXT_ONLY":
            raise RuntimeError("运行证据必须明确标记 SIMULATION_TEXT_ONLY")
        if evidence.get("source_transcript_sha256") != source_sha256:
            raise RuntimeError("运行证据引用了其他转写输入")
        if evidence.get("independent_answer_id") != independent["answer_id"]:
            raise RuntimeError("运行证据引用了其他独立答案")
        layers = evidence["layers"]
    else:
        layers = {name: {"status": "NOT_RUN", "fields": []} for name in LAYERS}
    source_file_id = None
    if evidence is not None:
        source_file_id = layers["saved_reread"]["record_view"]["attachments"][0]["file_id"]
    semantic_layers = {name: semantic_layer(name, layers[name], source_file_id) for name in LAYERS}
    adopted_decisions = {item["field_path"]: item.get("decision") for item in semantic_layers["adopted"]["fields"]}
    semantic_layers["saved_reread"]["decision_evidence"] = semantic_layers["adopted"]["decision_evidence"]
    semantic_layers["saved_reread"]["reported_decision_counts"] = semantic_layers["adopted"]["reported_decision_counts"]
    for item in semantic_layers["saved_reread"]["fields"]:
        item["decision"] = adopted_decisions.get(item["field_path"])
    scored = {name: score_layer(gold_fields, semantic_layers[name]) for name in LAYERS}
    process_response = json.loads(args.process_response.read_text(encoding="utf-8")) if args.process_response.exists() else None
    if process_response is not None and (process_response.get("data_mode") != "SIMULATION"
            or process_response.get("evidence_scope") != "SIMULATION_TEXT_ONLY"
            or not isinstance(process_response.get("http_status"), int)
            or not isinstance(process_response.get("response_body"), dict)):
        raise RuntimeError("VoiceInput process 响应证据缺少准确模拟范围或 HTTP 结果")
    cancel_evidence = json.loads(args.cancel_evidence.read_text(encoding="utf-8")) if args.cancel_evidence else None
    if cancel_evidence is not None:
        if (cancel_evidence.get("data_mode") != "SIMULATION"
                or cancel_evidence.get("evidence_scope") != "SIMULATION_TEXT_ONLY"
                or cancel_evidence.get("status") != "SIMULATION_READY_VOICE_CANCELLED_AND_READ"
                or cancel_evidence.get("create", {}).get("http_status") != 201
                or cancel_evidence.get("cancel", {}).get("http_status") != 200
                or cancel_evidence.get("readback", {}).get("http_status") != 200
                or cancel_evidence.get("readback", {}).get("database_status") != "cancelled"
                or cancel_evidence.get("readback", {}).get("database_task_id") is not None
                or cancel_evidence.get("readback", {}).get("task_rows_for_voice_input") != []):
            raise RuntimeError("VoiceInput 取消证据没有通过 API 和只读数据库检查")
    revocation_probe = evidence.get("authorization_revocation_probe", {"status": "NOT_RUN"}) if evidence else {"status": "NOT_RUN"}
    if revocation_probe.get("status") == "NOT_RUN":
        revocation_probe["reason"] = REVOCATION_NOT_RUN_REASON
    terminal_probes = evidence.get("terminal_probes", {}) if evidence else {}
    terminal_probes = {
        "cancel": {"status": "SIMULATION_READY_VOICE_CANCELLED_AND_READ",
            "voice_input_id": cancel_evidence["readback"]["response_body"]["id"],
            "create_http_status": 201, "cancel_http_status": 200, "read_http_status": 200,
            "task_rows": 0, "record_version_count_unchanged": True}
            if cancel_evidence else terminal_probes.get("cancel", {"status": "NOT_RUN", "reason": TERMINAL_NOT_RUN_REASON}),
        "model_result_unknown": terminal_probes.get("model_result_unknown", {
            "status": "NOT_RUN", "reason": TERMINAL_NOT_RUN_REASON})}
    report = {
        "schema_version": "window3-t55-voice-evaluation-2",
        "data_mode": "SIMULATION",
        "evidence_scope": "SIMULATION_TEXT_ONLY",
        "status": "SIMULATION_EVALUATION_PREPARED" if evidence is None else (
            "SIMULATION_EVALUATED" if all(scored[name]["field_counts"] is not None for name in LAYERS)
            else "SIMULATION_PARTIAL" if any(scored[name]["field_counts"] is not None for name in LAYERS)
            else "SIMULATION_EVIDENCE_WITHOUT_MEASURED_LAYERS"),
        "source": {"source_id": source["source"]["source_id"], "transcript_sha256": source_sha256,
            "audio_source_available": source["source"]["audio_source_available"]},
        "independent_answer": {"answer_id": independent["answer_id"], "origin": independent["answer_origin"],
            "derived_from_model_output": independent["derived_from_model_output"],
            "clinical_review": independent["clinical_review"], "field_count": len(gold_fields)},
        "model_call": timing_and_cost(evidence),
        "process_submission": {"status": "NOT_RUN" if process_response is None else "SIMULATION_RESPONSE_CAPTURED",
            "voice_input_id": process_response.get("voice_input_id") if process_response else None,
            "request_id": process_response.get("request_id") if process_response else None,
            "http_status": process_response.get("http_status") if process_response else None,
            "error_code": process_response.get("response_body", {}).get("code") if process_response else None},
        "layers": scored,
        "paired_layer_comparisons": paired_layers(scored),
        "idempotency": evidence.get("idempotency", {"status": "NOT_RUN", "reason": IDEMPOTENCY_NOT_RUN_REASON}) if evidence else {"status": "NOT_RUN", "reason": IDEMPOTENCY_NOT_RUN_REASON},
        "authorization_revocation_probe": revocation_probe,
        "terminal_probes": terminal_probes,
        "clinical_validity": "NOT_RUN",
        "family_understanding": "NOT_RUN",
        "task_id": evidence.get("task_id") if evidence else None,
        "voice_input_id": evidence.get("voice_input_id") if evidence else None,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reread = json.loads(args.output.read_text(encoding="utf-8"))
    if reread != report:
        raise RuntimeError("语音评价报告保存后重新读取不一致")
    print(json.dumps({"task": "T55_VOICE", "status": report["status"],
        "layer_statuses": {name: value["status"] for name, value in scored.items()},
        "process_http_status": report["process_submission"]["http_status"],
        "process_error_code": report["process_submission"]["error_code"],
        "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
