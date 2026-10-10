import argparse
from datetime import datetime, timezone
import hashlib
import json
from collections import Counter
from decimal import Decimal
import math
from pathlib import Path
import statistics


ROOT = Path(__file__).resolve().parents[2]
REQUIRED_FIELDS = ("name", "comparator", "value", "unit", "negation", "medical_time", "context", "source")
UNCOVERED = object()
MISSING = object()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def blank_counts():
    return {field: {"correct": 0, "incorrect": 0, "uncovered": 0} for field in REQUIRED_FIELDS}


def record_comparison(counts, field, expected, actual):
    if expected is UNCOVERED or actual is MISSING:
        counts[field]["uncovered"] += 1
    elif actual == expected:
        counts[field]["correct"] += 1
    else:
        counts[field]["incorrect"] += 1


def ocr_gold_rows(expected_rows):
    return [
        {"name": row[0], "value": row[1], "unit": row[2], "comparator": row[3]}
        for row in expected_rows
    ]


def evaluate_ocr(path):
    evidence = read_json(path)
    input_root = ROOT / "docs/testing/evidence/QA03/ocr-inputs"
    counts = blank_counts()
    checked_sources = []
    rows = []
    latency_total = 0.0
    usage_total = Counter()
    request_count = 0

    for result in evidence["results"]:
        image_path = input_root / f"{result['id']}.png"
        image_hash = sha256(image_path)
        source_matches = image_hash == result["input_sha256"]
        checked_sources.append({"case_id": result["id"], "image_sha256": image_hash, "matches_call_evidence": source_matches})
        if not source_matches:
            raise ValueError(f"模拟原件摘要不匹配：{result['id']}")

        gold_rows = ocr_gold_rows(result["expected"])
        candidate_rows = result["output"]["candidates"]["items"]
        for index, gold in enumerate(gold_rows):
            candidate = candidate_rows[index] if index < len(candidate_rows) else None
            for field in ("name", "comparator", "value", "unit"):
                record_comparison(counts, field, gold[field], candidate.get(field, MISSING) if candidate is not None else MISSING)
            for field in ("negation", "medical_time", "context"):
                record_comparison(counts, field, UNCOVERED, candidate.get(field, MISSING) if candidate is not None else MISSING)
            record_comparison(counts, "source", UNCOVERED, candidate.get("source_text", MISSING) if candidate is not None else MISSING)
            rows.append({
                "case_id": result["id"],
                "candidate_index": index,
                "gold": gold,
                "candidate": {field: candidate.get(field) for field in ("name", "comparator", "value", "unit", "source_text")} if candidate is not None else None,
                "provider_request_id": result["output"].get("provider_request_id"),
                "input_sha256": result["input_sha256"],
            })

        if len(candidate_rows) > len(gold_rows):
            for candidate in candidate_rows[len(gold_rows):]:
                for field in ("name", "comparator", "value", "unit"):
                    record_comparison(counts, field, "__no_gold_item__", candidate.get(field, MISSING))

        latency_total += result.get("latency_seconds", 0)
        usage = result["output"].get("usage", {})
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if isinstance(usage.get(key), int):
                usage_total[key] += usage[key]
        request_count += bool(result["output"].get("provider_request_id"))

    return {
        "status": "SIMULATION_CANDIDATE_ONLY",
        "purpose": evidence.get("scope"),
        "source_evidence": {
            "provider": evidence.get("provider"),
            "models": sorted({result["output"].get("model") for result in evidence["results"] if result.get("output", {}).get("model")}),
            "data_mode": evidence.get("input_source"),
            "prompt_sha256": None,
            "evidence_file_sha256": sha256(path),
            "verified_source_count": len(checked_sources),
            "sources": checked_sources,
            "provider_request_count": request_count,
        },
        "candidate": {
            "field_counts": counts,
            "compared_candidate_fields": len(rows),
            "source_binding": {
                "correct": sum(item["matches_call_evidence"] for item in checked_sources),
                "incorrect": sum(not item["matches_call_evidence"] for item in checked_sources),
                "uncovered": 0,
                "denominator": len(checked_sources),
            },
            "rows": rows,
            "elapsed_seconds_sum": round(latency_total, 3),
            "usage_sum": dict(usage_total),
            "fee": None,
            "fee_basis": "NOT_MEASURED：调用证据没有保存该模型在调用时生效的价格表版本及逐请求账单。",
        },
        "adopted": {"status": "NOT_RUN", "field_counts": None, "reason": "现有本人采用记录来自其他数据库和不同调用；没有与本组候选及当前 sjia_simulation 保存记录逐项绑定。"},
        "saved_reread": {"status": "NOT_RUN", "field_counts": None, "reason": "当前 sjia_simulation 数据库与 API 不可连接；不能核验本组候选的实际保存和重新读取。"},
        "independent_answer": {
            "status": "SIMULATION_EXPECTED_VALUES",
            "origin": "QA03 逐案例 expected 数组；独立于 Provider 输出保存，未由同一提取器产生。",
            "limitations": ["没有独立临床人员复核。", "预期值仅覆盖 name、comparator、value、unit；否定、医学时间和语境未登记。", "source 正确数表示原始图片摘要与调用输入摘要一致；候选逐字段 source_text 未提供。"],
        },
        "runtime_versions": {
            "recorded_models": sorted({result["output"].get("model") for result in evidence["results"] if result.get("output", {}).get("model")}),
            "recorded_provider": evidence.get("provider"),
            "current_schema_match": "NOT_VERIFIED",
            "current_database": "sjia_simulation",
            "evidence_database": "sjia_qa03 for prior write/read records; QA03 model-response JSON does not bind its own database",
            "current_schema_match_reason": "旧调用证据未绑定当前识别 Schema 摘要；候选结果只用于注明 SIMULATION 的历史模型质量检查。",
        },
    }


def evaluate_voice(path):
    evidence = read_json(path)
    counts = blank_counts()
    rows = []
    latency_total = 0.0
    for result in evidence["results"]:
        if "output" not in result:
            for field in REQUIRED_FIELDS:
                record_comparison(counts, field, UNCOVERED, None)
            rows.append({"case_id": result["id"], "status": result.get("reason_code", "NO_CANDIDATE"), "candidate": None})
            continue
        candidate = result["output"]
        record_comparison(counts, "value", result.get("expected_temperature", MISSING), candidate.get("temperature", MISSING))
        record_comparison(counts, "unit", result.get("expected_unit", MISSING), candidate.get("temperature_unit", MISSING))
        for field in ("name", "comparator", "negation", "medical_time", "context"):
            record_comparison(counts, field, UNCOVERED, candidate.get(field, MISSING))
        record_comparison(counts, "source", UNCOVERED, candidate.get("source_text", MISSING))
        latency_total += result.get("latency_seconds", 0)
        rows.append({
            "case_id": result["id"],
            "input_sha256": hashlib.sha256(result["input"].encode("utf-8")).hexdigest(),
            "gold": {"value": result.get("expected_temperature"), "unit": result.get("expected_unit")},
            "candidate": {"value": candidate.get("temperature"), "unit": candidate.get("temperature_unit"), "observed_time": candidate.get("observed_time"), "symptoms": candidate.get("symptoms")},
        })
    return {
        "status": "SIMULATION_CANDIDATE_ONLY",
        "source_evidence": {
            "provider": evidence.get("provider"),
            "model": evidence.get("model"),
            "data_mode": evidence.get("data_mode"),
            "prompt_sha256": evidence.get("prompt_sha256"),
            "evidence_file_sha256": sha256(path),
            "recorded_calls": len(evidence["results"]),
            "candidate_outputs": sum("output" in result for result in evidence["results"]),
            "provider_request_ids": "NOT_RECORDED_PER_CALL",
        },
        "candidate": {
            "field_counts": counts,
            "source_binding": {
                "correct": sum(isinstance(result.get("input"), str) and "output" in result for result in evidence["results"]),
                "incorrect": 0,
                "uncovered": sum(not isinstance(result.get("input"), str) or "output" not in result for result in evidence["results"]),
                "denominator": len(evidence["results"]),
            },
            "compared_candidates": len(rows),
            "rows": rows,
            "elapsed_seconds_sum": round(latency_total, 3),
            "usage_sum": None,
            "fee": None,
            "fee_basis": "NOT_MEASURED：本组逐请求记录没有保存令牌计量和调用时价格表。",
        },
        "independent_answer": {
            "status": "SIMULATION_EXPECTED_VALUES",
            "origin": "QA03 每条输入对应的 expected_temperature 与 expected_unit；由测试用例独立维护，未由提取器生成。",
            "limitations": ["未记录逐例临床人员复核。", "否定、医学时间和症状语境没有独立结构化答案。", "source 正确数表示输入原文与对应候选保存在同一调用记录；来源文字本身没有成为模型字段。"],
        },
    }


def evaluate_runtime_candidate(path):
    evidence = read_json(path)
    if evidence["data_mode"] != "SIMULATION" or evidence["database_name"] != "sjia_simulation":
        raise ValueError("T55 当前候选必须来自明确的共享模拟数据库")
    candidate = evidence["candidate"]
    task = evidence["task"]
    attempt = evidence["attempt"]
    if candidate["status"] != "pending_confirmation" or candidate["schema_version"] != "report-recognition-2":
        raise ValueError("T55 当前候选状态或识别 Schema 版本不符合预期")
    if candidate["provider_request_id"] != attempt["provider_request_id"]:
        raise ValueError("T55 当前候选没有绑定准确模型请求")
    if task["status"] != "awaiting_confirmation" or task["task_id"] != candidate["task_id"]:
        raise ValueError("T55 当前候选任务身份或状态不匹配")
    if task.get("actor_id") != task.get("family_principal_id"):
        raise ValueError("T55 模拟家庭账号与模型任务创建人身份不一致")
    if evidence["api"].get("family_principal_id", task["actor_id"]) != task["actor_id"]:
        raise ValueError("T55 模拟家庭 API 会话与模型任务创建人身份不一致")
    if evidence["api"]["candidate_read_status"] != 200 or evidence["api"]["field_config_http_status"] != 200 or evidence["api"]["field_config_state"] != "available":
        raise ValueError("T55 当前候选读取或字段配置状态不允许模拟确认")
    if evidence["api"]["field_config_reason"] is not None:
        raise ValueError("T55 当前字段配置带有拒绝原因")
    if attempt["status"] != "completed" or not attempt["started_at"] or not attempt["finished_at"]:
        raise ValueError("T55 当前模型调用缺少完成状态或计时")
    if evidence["pricing"]["currency"] != "CNY" or not evidence["pricing"]["evidence_ref"]:
        raise ValueError("T55 当前模拟计价依据不完整")
    usage = attempt["usage"]
    input_tokens = usage["prompt_tokens"]
    output_tokens = usage["completion_tokens"]
    input_rate = Decimal(evidence["pricing"]["input_per_million"])
    output_rate = Decimal(evidence["pricing"]["output_per_million"])
    fee = (input_tokens * input_rate + output_tokens * output_rate) / Decimal(1_000_000)
    fields = {item["field_path"]: item for item in candidate["fields"]}
    expected = evidence["independent_simulation_answers"]
    if set(fields) != set(expected):
        raise ValueError("T55 当前模型候选与独立模拟答案字段集合不一致")
    candidate_values = {}
    for path_key, row in fields.items():
        proposed = row["proposed_field"]
        candidate_values[path_key] = {
            "name": proposed.get("original_name"),
            "comparator": proposed.get("qualifier"),
            "value": proposed.get("raw_numeric"),
            "unit": proposed.get("raw_unit"),
            "negation": MISSING,
            "medical_time": "unknown" if proposed.get("sample_time_origin") == "unknown" else MISSING,
            "context": MISSING,
            "source": row.get("source_anchor_text"),
        }
    layers = {"candidate": candidate_values}
    if evidence.get("adopted", {}).get("status") == "SIMULATION_CONFIRMED":
        if evidence["adopted"]["candidate_set_id"] != candidate["candidate_set_id"]:
            raise ValueError("T55 本人决定没有绑定同一模型候选")
        if evidence["api"].get("candidate_after_status") != 200 or evidence.get("candidate_after", {}).get("status") != "confirmed":
            raise ValueError("T55 本人决定没有从 API 重新读取确认状态")
        if any(item.get("decision") != "accepted" or item.get("confirmed_by") != evidence["adopted"]["actor_id"] for item in evidence["adopted"]["fields"]):
            raise ValueError("T55 本人决定记录缺少准确决定人或逐字段决定")
        layers["adopted"] = {item["field_path"]: item["values"] for item in evidence["adopted"]["fields"]}
    if evidence.get("saved_reread", {}).get("status") == "SIMULATION_SAVED_AND_READ":
        saved = evidence["saved_reread"]
        if saved["candidate_set_id"] != candidate["candidate_set_id"] or not saved["record_id"] or not saved["record_version_id"]:
            raise ValueError("T55 保存重读没有绑定同一模型候选或实际业务版本")
        if evidence["api"].get("record_save_status") != 201 or evidence["api"].get("record_read_status") != 200:
            raise ValueError("T55 记录写入与重读接口没有成功")
        if saved.get("api_view", {}).get("record_id") != saved["record_id"] or saved["api_view"].get("record_version_id") != saved["record_version_id"]:
            raise ValueError("T55 保存对象与 API 重读版本不一致")
        if not any(item.get("file_id") == candidate["source_file_id"] for item in saved["api_view"].get("attachments", [])):
            raise ValueError("T55 重读记录没有绑定模型候选原件")
        layers["saved_reread"] = {item["field_path"]: item["values"] for item in saved["fields"]}
    layer_counts = {}
    for layer_name, values_by_path in layers.items():
        layer_counts[layer_name] = {name: {"correct": 0, "incorrect": 0, "uncovered": 0} for name in REQUIRED_FIELDS}
        if set(values_by_path) != set(expected):
            raise ValueError(f"T55 {layer_name} 没有覆盖同一模拟 Gold 字段")
        for path_key, gold in expected.items():
            for field in REQUIRED_FIELDS:
                actual = values_by_path[path_key].get(field, MISSING)
                answer = gold.get(field, MISSING)
                if actual is MISSING or answer is MISSING:
                    layer_counts[layer_name][field]["uncovered"] += 1
                elif actual == answer:
                    layer_counts[layer_name][field]["correct"] += 1
                else:
                    layer_counts[layer_name][field]["incorrect"] += 1
    paired_layers = {}
    layer_names = list(layers)
    for left_index, left_name in enumerate(layer_names):
        for right_name in layer_names[left_index + 1:]:
            counts = {field: {"same": 0, "different": 0, "uncovered": 0} for field in REQUIRED_FIELDS}
            for path_key in expected:
                for field in REQUIRED_FIELDS:
                    left = layers[left_name][path_key].get(field, MISSING)
                    right = layers[right_name][path_key].get(field, MISSING)
                    if left is MISSING or right is MISSING:
                        counts[field]["uncovered"] += 1
                    elif left == right:
                        counts[field]["same"] += 1
                    else:
                        counts[field]["different"] += 1
            paired_layers[f"{left_name}_to_{right_name}"] = counts
    duration = (parse_time(attempt["finished_at"]) - parse_time(attempt["started_at"])).total_seconds()
    saved_status = evidence.get("saved_reread", {}).get("status", "NOT_RUN")
    adopted_status = evidence.get("adopted", {}).get("status", "NOT_RUN")
    workflow_status = saved_status if saved_status == "SIMULATION_SAVED_AND_READ" else adopted_status if adopted_status == "SIMULATION_CONFIRMED" else "SIMULATION_CANDIDATE_ONLY"
    return {
        "status": workflow_status,
        "candidate_set_id": candidate["candidate_set_id"],
        "patient_id": candidate["patient_id"],
        "file_group_id": candidate["file_group_id"],
        "file_group_version": candidate["file_group_version"],
        "source_file_id": candidate["source_file_id"],
        "source_file_sha256": candidate["source_file_sha256"],
        "provider_request_id": candidate["provider_request_id"],
        "model_version": candidate["model_version"],
        "schema_version": candidate["schema_version"],
        "independent_answer_origin": "seed_simulation_batches.py SIMULATION image source text; maintained separately from recognition response",
        "field_counts_by_layer": layer_counts,
        "paired_field_counts": paired_layers,
        "provider_elapsed_seconds": duration,
        "usage": usage,
        "fee": {
            "status": "SIMULATION_CONFIG_CALCULATED",
            "currency": evidence["pricing"]["currency"],
            "amount": str(fee),
            "basis": "Recorded prompt/completion tokens multiplied by the exact provider configuration rates; not an invoice.",
            "pricing_evidence_ref": evidence["pricing"]["evidence_ref"],
            "provider_config_id": evidence["pricing"]["provider_config_id"],
            "input_per_million": str(input_rate),
            "output_per_million": str(output_rate),
        },
        "adopted": {"status": evidence.get("adopted", {}).get("status", "NOT_RUN"), "actor_id": evidence.get("adopted", {}).get("actor_id")},
        "saved_reread": {"status": evidence.get("saved_reread", {}).get("status", "NOT_RUN"), "record_id": evidence.get("saved_reread", {}).get("record_id")},
        "evidence_sha256": sha256(path),
    }


def evaluate_field_layer(path, expected_phase):
    layer = read_json(path)
    if layer["schema_version"] != "window3-t55-field-layer-1" or layer["phase"] != expected_phase:
        raise ValueError("T55 字段层级输入版本或阶段不匹配")
    if layer["database"] != "sjia_simulation" or layer["data_mode"] != "SIMULATION":
        raise ValueError("T55 字段层级必须来自 sjia_simulation 模拟业务")
    base = read_json(ROOT / "docs/testing/evidence/QA03/ocr-evals.json")
    expected_rows = {}
    for result in base["results"]:
        for index, gold in enumerate(ocr_gold_rows(result["expected"])):
            key = (result["id"], index)
            expected_rows[key] = {
                "gold": gold,
                "provider_request_id": result["output"].get("provider_request_id"),
                "input_sha256": result["input_sha256"],
            }
    observed_rows = {}
    for row in layer["rows"]:
        key = (row["case_id"], row["candidate_index"])
        if key in observed_rows or key not in expected_rows:
            raise ValueError("T55 字段层级出现重复或未知候选")
        reference = expected_rows[key]
        if row["provider_request_id"] != reference["provider_request_id"] or row["input_sha256"] != reference["input_sha256"]:
            raise ValueError("T55 字段层级没有绑定准确模型调用和原件")
        if expected_phase == "adopted" and (not row.get("adopted_by") or not row.get("decision")):
            raise ValueError("T55 本人采用记录缺少确认身份或决定")
        if expected_phase == "saved_reread" and (not row.get("record_field_id") or not row.get("record_version_id") or not row.get("record_id") or not row.get("saved_at") or not row.get("read_at")):
            raise ValueError("T55 保存重读缺少实际字段版本或读取时点")
        observed_rows[key] = row
    if set(observed_rows) != set(expected_rows):
        raise ValueError("T55 字段层级没有覆盖全部独立 Gold 候选")

    counts = blank_counts()
    report_rows = []
    for key, reference in expected_rows.items():
        row = observed_rows[key]
        values = row["values"]
        for field in REQUIRED_FIELDS:
            if field in reference["gold"]:
                record_comparison(counts, field, reference["gold"][field], values.get(field, MISSING))
            elif field == "source":
                record_comparison(counts, field, reference["input_sha256"], row.get("source_sha256", MISSING))
            else:
                record_comparison(counts, field, UNCOVERED, values.get(field, MISSING))
        report_rows.append({"case_id": key[0], "candidate_index": key[1], "provider_request_id": reference["provider_request_id"], "input_sha256": reference["input_sha256"], "record_id": row.get("record_id"), "record_version_id": row.get("record_version_id"), "record_field_id": row.get("record_field_id"), "adopted_by": row.get("adopted_by"), "decision": row.get("decision"), "saved_at": row.get("saved_at"), "read_at": row.get("read_at"), "gold": reference["gold"], "values": values, "source_sha256": row.get("source_sha256")})
    return {
        "status": "SIMULATION_LAYER_COMPARED",
        "phase": expected_phase,
        "database": layer["database"],
        "field_counts": counts,
        "rows": report_rows,
        "evidence_file_sha256": sha256(path),
    }


def compare_field_layers(left, right, left_name, right_name):
    left_rows = {(row["case_id"], row["candidate_index"]): row for row in left["rows"]}
    right_rows = {(row["case_id"], row["candidate_index"]): row for row in right["rows"]}
    if set(left_rows) != set(right_rows):
        raise ValueError(f"T55 {left_name} 与 {right_name} 没有覆盖相同模型候选")
    counts = {field: {"same": 0, "different": 0, "uncovered": 0} for field in REQUIRED_FIELDS}
    for key, left_row in left_rows.items():
        right_row = right_rows[key]
        if left_row["provider_request_id"] != right_row["provider_request_id"] or left_row["input_sha256"] != right_row["input_sha256"]:
            raise ValueError(f"T55 {left_name} 与 {right_name} 没有绑定同一模型调用和原件")
        for field in REQUIRED_FIELDS:
            left_value = left_row["values"].get(field, MISSING)
            right_value = right_row["values"].get(field, MISSING)
            if left_value is MISSING or right_value is MISSING:
                counts[field]["uncovered"] += 1
            elif left_value == right_value:
                counts[field]["same"] += 1
            else:
                counts[field]["different"] += 1
    return {
        "status": "SIMULATION_PAIRED_FIELDS_COMPARED",
        "from": left_name,
        "to": right_name,
        "field_counts": counts,
        "paired_candidate_count": len(left_rows),
    }


def evaluate_clinical(path):
    evidence = read_json(path)
    clinical = evidence["inputs"]["clinical"]
    dataset = clinical["dataset"]
    run = clinical["run"]
    manifest = clinical["manifest"]
    if dataset["status"] != "frozen" or dataset["version"] != "6":
        raise ValueError("T48 临床集合不是本记录指定的冻结版本")
    if dataset["counts"]["observation_person_hours"] != "6":
        raise ValueError("冻结集合人时与来源记录不一致")

    labels = dataset["labels"]
    states = Counter(item["label_state"] for item in labels)
    if run["dataset_id"] != dataset["dataset_id"] or run["dataset_version"] != dataset["version"]:
        raise ValueError("T48 运行未绑定当前冻结集合版本")
    if dataset["counts"]["confirmed_labels"] != states["positive"] + states["negative"] or dataset["counts"]["unknown_labels"] != states["unknown"]:
        raise ValueError("冻结集合标签分母与标签明细不一致")
    partitions_by_patient = {}
    for item in dataset["partitions"]:
        partitions_by_patient.setdefault(item["patient_id"], set()).add(item["partition"])
    cross_partition_patients = sorted(patient for patient, partitions in partitions_by_patient.items() if len(partitions) > 1)
    if cross_partition_patients:
        raise ValueError("同一患儿跨开发与验证集合")
    unknown_count = states["unknown"]
    label_count = len(labels)
    no_metric_result = run["status"] == "insufficient"
    partition_for_patient = {
        item["patient_id"]: item["partition"]
        for item in dataset["partitions"]
    }
    partition_label_counts = {
        partition: {
            "patients": len({item["patient_id"] for item in dataset["partitions"] if item["partition"] == partition}),
            "positive": sum(item["label_state"] == "positive" and partition_for_patient[item["patient_id"]] == partition for item in labels),
            "negative": sum(item["label_state"] == "negative" and partition_for_patient[item["patient_id"]] == partition for item in labels),
            "unknown_excluded_from_negative": sum(item["label_state"] == "unknown" and partition_for_patient[item["patient_id"]] == partition for item in labels),
        }
        for partition in ("development", "validation")
    }

    return {
        "status": "SIMULATION_DENOMINATORS_ONLY",
        "dataset": {
            "dataset_id": dataset["dataset_id"],
            "version_id": dataset["version_id"],
            "version": dataset["version"],
            "checksum": dataset["checksum"],
            "frozen_at": dataset["frozen_at"],
            "purpose": "clinical_evaluation",
            "clinical_qualification": dataset["coverage"].get("clinical_qualification"),
            "evidence_file_sha256": sha256(path),
        },
        "denominators": {
            "patients": dataset["counts"]["patients"],
            "development_patients": len({item["patient_id"] for item in dataset["partitions"] if item["partition"] == "development"}),
            "validation_patients": len({item["patient_id"] for item in dataset["partitions"] if item["partition"] == "validation"}),
            "patients_crossing_partitions": len(cross_partition_patients),
            "cross_partition_patient_ids": cross_partition_patients,
            "observation_periods": dataset["counts"]["observation_periods"],
            "observation_person_hours": dataset["counts"]["observation_person_hours"],
            "confirmed_positive_labels": states["positive"],
            "confirmed_negative_labels": states["negative"],
            "unknown_labels_excluded_from_negative_denominator": unknown_count,
            "unknown_label_share_simulation": unknown_count / label_count if label_count else None,
            "labels_by_partition": partition_label_counts,
            "mas_event_units": dataset["counts"]["mas_events"],
            "sampling_event_units": dataset["counts"]["sampling_events"],
        },
        "replay": {
            "mode": run["mode"],
            "known_at": None,
            "task_status": run["task"]["status"],
            "run_status": run["status"],
            "future_information_exclusion": "NOT_VERIFIED",
            "reason": "保存的结果为 insufficient，未提供逐事实输入引用及每项 known_at 可得时间，无法验证历史输入排除情况。",
        },
        "metrics": {
            "sensitivity": None,
            "missed_events": None,
            "ppv": None,
            "alerts_per_observation_person_hour": None,
            "first_alert_time_difference": None,
            "event_unassessable_proportion": None,
            "dataset_unknown_label_share_simulation": unknown_count / label_count if label_count else None,
            "static_dynamic_comparison": None,
            "subgroup_results": None,
            "uncertainty_ranges": None,
            "status": "NOT_MEASURED",
            "reason": "T49/T50 临床正向计算结果为空；T48 冻结集合没有 MAS 事件单位，单人单集合不能形成开发与验证对照。运行状态为 insufficient，无结果不解释为零次提示或正确输出。",
            "uncertainty_reason": "当前没有可评价的事件与提示结果，也没有批准的患者相关性区间计算方案。",
        },
        "grouping": {
            "patient_group_count": len(partitions_by_patient),
            "cross_partition_patients": cross_partition_patients,
            "validation_group_available": any(item["partition"] == "validation" for item in dataset["partitions"]),
            "patient_partitions": {patient: next(iter(partitions)) for patient, partitions in partitions_by_patient.items()},
            "labeled_observation_periods": {
                item["observation_period_id"]: {
                    "patient_id": item["patient_id"],
                    "label_state": item["label_state"],
                    "label_id": item["label_id"],
                    "reference_answer_ref": item["reference_answer_ref"],
                }
                for item in labels
            },
            "partition_label_counts": partition_label_counts,
            "independent_raw_answer_content": "NOT_VERIFIED_FROM_T48_CAPTURE",
        },
        "runtime": {
            "freeze_id": dataset["dataset_id"],
            "run_id": run["run_id"],
            "database_at_capture": "sjia_simulation",
            "current_database_recheck": "NOT_RUN",
            "insufficient_result_observed": no_metric_result,
        },
    }


def parse_time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("评价时点缺少 UTC offset")
    return parsed


def calculate_mode_metrics(periods, events, mode):
    prompts = [(period, prompt) for period in periods for prompt in period[mode]["prompts"]]
    confirmed_events = [event for event in events if event["label_state"] == "positive"]
    unknown_events = [event for event in events if event["label_state"] == "unknown"]
    detected = {}
    first_differences = []
    first_by_event = {}
    for event in confirmed_events:
        matching = [
            prompt for _, prompt in prompts
            if prompt.get("matched_event_id") == event["event_id"]
            and parse_time(event["valid_alert_start"]) <= parse_time(prompt["at"]) <= parse_time(event["valid_alert_end"])
        ]
        if matching:
            detected[event["event_id"]] = matching
            first_prompt = min(matching, key=lambda item: parse_time(item["at"]))
            delta = (parse_time(first_prompt["at"]) - parse_time(event["reference_at"])).total_seconds()
            first_differences.append(delta)
            first_by_event[event["event_id"]] = delta

    known_prompts = [prompt for _, prompt in prompts if prompt["outcome"] in {"positive", "negative"}]
    positive_prompts = sum(prompt["outcome"] == "positive" for prompt in known_prompts)
    unknown_prompts = sum(prompt["outcome"] == "unknown" for _, prompt in prompts)
    negative_period_hours = sum(float(period["observation_hours"]) for period in periods if period["label_state"] == "negative")
    total_observation_hours = sum(float(period["observation_hours"]) for period in periods)
    negative_period_prompts = sum(
        len(period[mode]["prompts"])
        for period in periods
        if period["label_state"] == "negative"
    )
    event_total = len(confirmed_events) + len(unknown_events)
    prompt_total = len(prompts)
    return {
        "sensitivity": len(detected) / len(confirmed_events) if confirmed_events else None,
        "sensitivity_counts": {"detected_events": len(detected), "positive_events": len(confirmed_events), "missed_events": len(confirmed_events) - len(detected)},
        "missed_event_ids": [event["event_id"] for event in confirmed_events if event["event_id"] not in detected],
        "ppv": positive_prompts / len(known_prompts) if known_prompts else None,
        "ppv_counts": {"positive_prompts": positive_prompts, "known_prompts": len(known_prompts), "unknown_prompts": unknown_prompts},
        "alerts_per_observation_person_hour": prompt_total / total_observation_hours if total_observation_hours else None,
        "alerts_per_negative_observation_person_hour": negative_period_prompts / negative_period_hours if negative_period_hours else None,
        "total_prompt_count": prompt_total,
        "total_observation_person_hours": total_observation_hours,
        "negative_period_prompt_count": negative_period_prompts,
        "negative_period_observation_hours": negative_period_hours,
        "first_alert_time_difference_seconds": first_differences,
        "first_alert_time_difference_summary_seconds": {
            "count": len(first_differences),
            "mean": statistics.fmean(first_differences) if first_differences else None,
            "median": statistics.median(first_differences) if first_differences else None,
        },
        "first_alert_time_difference_by_event_seconds": first_by_event,
        "event_unassessable_proportion": len(unknown_events) / event_total if event_total else None,
        "event_unknown_counts": {"unknown_events": len(unknown_events), "all_positive_or_unknown_events": event_total},
        "uncertainty_ranges": {
            "sensitivity_identification": {
                "lower": len(detected) / event_total if event_total else None,
                "upper": (len(detected) + len(unknown_events)) / event_total if event_total else None,
                "basis": "SIMULATION upper/lower identification limits treat unknown event labels as all missed or all detected.",
            },
            "ppv_identification": {
                "lower": positive_prompts / prompt_total if prompt_total else None,
                "upper": (positive_prompts + unknown_prompts) / prompt_total if prompt_total else None,
                "basis": "SIMULATION upper/lower identification limits treat unknown prompt outcomes as all negative or all positive.",
            },
            "confidence_interval": None,
            "confidence_interval_status": "NOT_MEASURED: no approved patient-cluster interval method is present in the input protocol.",
        },
    }


def evaluate_prediction_input(path, frozen_dataset, frozen_grouping):
    data = read_json(path)
    if data["schema_version"] != "window3-clinical-predictions-1":
        raise ValueError("临床运行输入 Schema 版本不匹配")
    freeze = data["freeze"]
    if (freeze["dataset_id"], freeze["version"], freeze["checksum"]) != (frozen_dataset["dataset_id"], frozen_dataset["version"], frozen_dataset["checksum"]):
        raise ValueError("临床运行未绑定准确冻结集合版本")
    protocol = data["protocol"]
    if protocol["mode"] != "replay":
        raise ValueError("T59 历史评价输入必须固定为 replay")
    if protocol["data_mode"] != "SIMULATION":
        raise ValueError("本窗口 T59 只接收明确标识的模拟输入")
    if protocol.get("evidence_scope") != "SIMULATION_FIXTURE_ONLY":
        raise ValueError("模拟规则输出必须声明只用于评价程序场景检查")
    if protocol["run_status"] != "completed":
        raise ValueError("T59 仅接受已完成的静态和动态 replay")
    if not all(protocol[key] for key in ("static_rule_version", "dynamic_rule_version", "static_program_version", "dynamic_program_version", "static_execution_ref", "dynamic_execution_ref")):
        raise ValueError("T59 静态或动态计算缺少固定规则、程序或执行引用")
    if protocol["side_effect_counts"] != {"published": 0, "notified": 0, "current_cycle_mutations": 0}:
        raise ValueError("T59 replay 记录到发布、通知或当前周期修改")
    if len(protocol["subgroup_dimensions"]) != len(set(protocol["subgroup_dimensions"])):
        raise ValueError("T59 分组维度重复")
    required_subgroups = {"treatment_status", "age_group", "infection_context", "data_completeness"}
    if not required_subgroups.issubset(set(protocol["subgroup_dimensions"])):
        raise ValueError("T59 固定方案缺少治疗、年龄、感染或资料完整度分组")
    known_at = parse_time(protocol["known_at"])
    if not protocol.get("matching_policy_ref"):
        raise ValueError("缺少预先登记的提示配对方案")

    partitions = {}
    for patient in data["patients"]:
        if patient["patient_id"] in partitions:
            raise ValueError("固定集合患儿分组出现重复记录")
        if patient["partition"] not in {"development", "validation"}:
            raise ValueError("患儿分组必须为 development 或 validation")
        partitions.setdefault(patient["patient_id"], set()).add(patient["partition"])
    if any(len(values) != 1 for values in partitions.values()):
        raise ValueError("同一患儿跨开发与验证集合")
    normalized_partitions = {patient: next(iter(values)) for patient, values in partitions.items()}
    if normalized_partitions != frozen_grouping["patient_partitions"]:
        raise ValueError("临床运行患儿与 T48 冻结集合分组不完全一致")
    if any(patient not in partitions for patient in {period["patient_id"] for period in data["periods"]}):
        raise ValueError("观察时段患儿缺少冻结分组")
    event_ids = {event["event_id"] for event in data["events"]}
    if len(event_ids) != len(data["events"]):
        raise ValueError("固定集合内事件标识重复")
    period_by_id = {period["period_id"]: period for period in data["periods"]}
    if len(period_by_id) != len(data["periods"]):
        raise ValueError("固定集合内观察时段标识重复")
    if set(period_by_id) != set(frozen_grouping["labeled_observation_periods"]):
        raise ValueError("临床运行观察时段与 T48 冻结标签分母不完全一致")
    if any(event["patient_id"] not in partitions for event in data["events"]):
        raise ValueError("独立事件患儿缺少冻结分组")
    for event in data["events"]:
        if event["label_state"] not in {"positive", "unknown"}:
            raise ValueError("独立事件状态必须为 positive 或 unknown")
        if event["period_id"] not in period_by_id or period_by_id[event["period_id"]]["patient_id"] != event["patient_id"]:
            raise ValueError("独立事件没有绑定准确患儿观察时段")
        if event["label_state"] == "positive":
            if parse_time(event["valid_alert_start"]) > parse_time(event["valid_alert_end"]):
                raise ValueError("事件提示有效时间窗倒置")
            parse_time(event["reference_at"])
        frozen_period = frozen_grouping["labeled_observation_periods"][event["period_id"]]
        if event["label_id"] != frozen_period["label_id"] or event["label_state"] != frozen_period["label_state"]:
            raise ValueError("事件没有绑定冻结标签答案引用")
        if event["label_state"] == "positive" and event["reference_answer_ref"] != frozen_period["reference_answer_ref"]:
            raise ValueError("阳性事件没有绑定冻结 Gold 答案来源")

    future_facts_excluded = 0
    for period in data["periods"]:
        if period["label_state"] not in {"positive", "negative", "unknown"}:
            raise ValueError("观察时段缺少有效独立标签状态")
        frozen_period = frozen_grouping["labeled_observation_periods"][period["period_id"]]
        if period["patient_id"] != frozen_period["patient_id"] or period["label_state"] != frozen_period["label_state"]:
            raise ValueError("观察时段与 T48 冻结患儿或标签不一致")
        static = period["static"]
        dynamic = period["dynamic"]
        if not protocol["static_rule_version"] or not protocol["dynamic_rule_version"]:
            raise ValueError("静态或动态规则版本缺失")
        if static["rule_version"] != protocol["static_rule_version"] or dynamic["rule_version"] != protocol["dynamic_rule_version"]:
            raise ValueError("静态或动态规则版本与固定方案不符")
        if static["program_version"] != protocol["static_program_version"] or dynamic["program_version"] != protocol["dynamic_program_version"]:
            raise ValueError("静态或动态程序版本与固定方案不符")
        if static["input_sha256"] != dynamic["input_sha256"]:
            raise ValueError("静态与动态运行没有使用相同输入摘要")
        if not isinstance(static["input_sha256"], str) or len(static["input_sha256"]) != 64 or any(character not in "0123456789abcdef" for character in static["input_sha256"].lower()):
            raise ValueError("静态与动态输入摘要格式无效")
        observation_hours = float(period["observation_hours"])
        if not math.isfinite(observation_hours) or observation_hours <= 0:
            raise ValueError("观察时段人时必须大于零")
        dimensions = set(period["subgroups"])
        if dimensions != set(protocol["subgroup_dimensions"]):
            raise ValueError("观察时段没有完整提供预先登记的分组字段")
        if any(value is not None and not isinstance(value, str) for value in period["subgroups"].values()):
            raise ValueError("分组值必须是已核对文字或 unknown")
        period_fact_references = set()
        for fact in period["facts"]:
            fact_ref = fact["fact_ref"]
            if not fact_ref or fact_ref in period_fact_references:
                raise ValueError("回放事实引用缺失或重复")
            period_fact_references.add(fact_ref)
            source_ref = fact["source_ref"]
            if not source_ref:
                raise ValueError("回放事实缺少原始来源引用")
            if not isinstance(fact["used"], bool):
                raise ValueError("回放事实使用状态必须为布尔值")
            available_at = parse_time(fact["available_at"])
            fact_known_at = parse_time(fact["known_at"])
            if available_at > known_at or fact_known_at > known_at:
                if fact["used"]:
                    raise ValueError("known_at 之后可得的事实进入 replay")
                future_facts_excluded += 1
            if fact["used"] and available_at > known_at:
                raise ValueError("known_at 之后可得的事实进入 replay")
            if fact["used"] and fact_known_at > known_at:
                raise ValueError("known_at 之后确认的事实进入 replay")
        for mode in (static, dynamic):
            for prompt in mode["prompts"]:
                if prompt["outcome"] not in {"positive", "negative", "unknown"}:
                    raise ValueError("提示结局缺少独立标注")
                if not prompt.get("outcome_ref"):
                    raise ValueError("提示结局缺少独立答案来源引用")
                parse_time(prompt["at"])
                if prompt.get("matched_event_id") and prompt["matched_event_id"] not in event_ids:
                    raise ValueError("提示引用了当前固定集合之外的事件")

    static_metrics = calculate_mode_metrics(data["periods"], data["events"], "static")
    dynamic_metrics = calculate_mode_metrics(data["periods"], data["events"], "dynamic")
    partition_metrics = {}
    for partition in ("development", "validation"):
        patient_ids = {patient for patient, value in normalized_partitions.items() if value == partition}
        selected_periods = [period for period in data["periods"] if period["patient_id"] in patient_ids]
        selected_period_ids = {period["period_id"] for period in selected_periods}
        selected_events = [event for event in data["events"] if event["period_id"] in selected_period_ids]
        partition_metrics[partition] = {
            "patients": len(patient_ids),
            "observation_periods": len(selected_periods),
            "static": calculate_mode_metrics(selected_periods, selected_events, "static"),
            "dynamic": calculate_mode_metrics(selected_periods, selected_events, "dynamic"),
            "clinical_interpretation": "NOT_MEASURED: simulation fixture only" if protocol["evidence_scope"] == "SIMULATION_FIXTURE_ONLY" else "NOT_MEASURED",
        }
    shared_events = sorted(set(static_metrics["first_alert_time_difference_by_event_seconds"]) & set(dynamic_metrics["first_alert_time_difference_by_event_seconds"]))
    subgroup_results = {}
    for dimension in protocol["subgroup_dimensions"]:
        values = sorted({period["subgroups"][dimension] if period["subgroups"][dimension] is not None else "unknown" for period in data["periods"]})
        subgroup_results[dimension] = {}
        for value in values:
            selected_periods = [period for period in data["periods"] if (period["subgroups"][dimension] if period["subgroups"][dimension] is not None else "unknown") == value]
            selected_period_ids = {period["period_id"] for period in selected_periods}
            selected_events = [event for event in data["events"] if event["period_id"] in selected_period_ids]
            subgroup_results[dimension][value] = {
                "periods": len(selected_periods),
                "patients": len({period["patient_id"] for period in selected_periods}),
                "static": calculate_mode_metrics(selected_periods, selected_events, "static"),
                "dynamic": calculate_mode_metrics(selected_periods, selected_events, "dynamic"),
            }
    return {
        "status": "SIMULATION_METRICS_FROM_FIXED_INPUT",
        "evidence_scope": protocol["evidence_scope"],
        "frozen_dataset": freeze,
        "protocol": protocol,
        "known_at": protocol["known_at"],
        "future_facts_used": 0,
        "future_facts_excluded": future_facts_excluded,
        "denominators": {
            "patients": len(partitions),
            "validation_patients": sum(value == "validation" for value in normalized_partitions.values()),
            "development_patients": sum(value == "development" for value in normalized_partitions.values()),
            "observation_periods": len(data["periods"]),
            "positive_periods": sum(period["label_state"] == "positive" for period in data["periods"]),
            "negative_periods": sum(period["label_state"] == "negative" for period in data["periods"]),
            "unknown_periods_excluded_from_negative_denominator": sum(period["label_state"] == "unknown" for period in data["periods"]),
            "observation_person_hours": sum(float(period["observation_hours"]) for period in data["periods"]),
            "positive_events": sum(event["label_state"] == "positive" for event in data["events"]),
            "unknown_events": sum(event["label_state"] == "unknown" for event in data["events"]),
        },
        "patient_partitions": normalized_partitions,
        "static": static_metrics,
        "dynamic": dynamic_metrics,
        "metrics_by_patient_partition": partition_metrics,
        "validation_clinical_status": "NOT_MEASURED" if partition_metrics["validation"]["patients"] == 0 or protocol["evidence_scope"] == "SIMULATION_FIXTURE_ONLY" else "REQUIRES_INDEPENDENT_VALIDATION_REVIEW",
        "subgroups": subgroup_results,
        "static_dynamic_comparison": {
            "fixed_rule_versions": {"static": protocol["static_rule_version"], "dynamic": protocol["dynamic_rule_version"]},
            "same_period_count": len(data["periods"]),
            "same_input_verified": True,
            "event_sensitivity_difference": dynamic_metrics["sensitivity"] - static_metrics["sensitivity"] if dynamic_metrics["sensitivity"] is not None and static_metrics["sensitivity"] is not None else None,
            "total_observation_alert_burden_difference_per_person_hour": dynamic_metrics["alerts_per_observation_person_hour"] - static_metrics["alerts_per_observation_person_hour"] if dynamic_metrics["alerts_per_observation_person_hour"] is not None and static_metrics["alerts_per_observation_person_hour"] is not None else None,
            "negative_period_alert_burden_difference_per_person_hour": dynamic_metrics["alerts_per_negative_observation_person_hour"] - static_metrics["alerts_per_negative_observation_person_hour"] if dynamic_metrics["alerts_per_negative_observation_person_hour"] is not None and static_metrics["alerts_per_negative_observation_person_hour"] is not None else None,
            "first_alert_time_difference_difference_by_event_seconds": {event_id: dynamic_metrics["first_alert_time_difference_by_event_seconds"][event_id] - static_metrics["first_alert_time_difference_by_event_seconds"][event_id] for event_id in shared_events},
            "events_with_both_modes_alert": shared_events,
            "static_first_alert_time_difference_by_event_seconds": static_metrics["first_alert_time_difference_by_event_seconds"],
            "dynamic_first_alert_time_difference_by_event_seconds": dynamic_metrics["first_alert_time_difference_by_event_seconds"],
        },
        "input_file_sha256": sha256(path),
    }


def evaluate_saved_rule_capture(path, frozen_dataset, frozen_grouping):
    capture = read_json(path)
    if capture["schema_version"] != "window3-saved-t49-t50-capture-2":
        raise ValueError("T49/T50 保存结果捕获 Schema 版本不匹配")
    if capture["data_mode"] != "SIMULATION" or capture["database_name"] != "sjia_simulation" or capture["database_port"] != 5433:
        raise ValueError("T49/T50 保存结果必须来自共享 sjia_simulation:5433 且标记 SIMULATION")
    if capture["evidence_scope"] != "ACTUAL_SJIA_SIMULATION_TASK_RESULT" or capture["read_only"] is not True:
        raise ValueError("T49/T50 捕获必须标明只读实际模拟 Task 结果")
    classification = capture["classification"]
    ms = capture["ms"]
    dynamic = capture["dynamic"]
    classification_task = classification["task"]
    ms_task = ms["task"]
    dynamic_task = dynamic["task"]
    if (classification_task["task_type"] != "assessment" or ms_task["task_type"] != "assessment"
            or dynamic_task["task_type"] != "alert_evaluation"):
        raise ValueError("保存结果 Task 类型与 T49/T50 角色不一致")
    if any(task["status"] != "completed" for task in (classification_task, ms_task, dynamic_task)):
        raise ValueError("T49/T50 Task 未全部完成")
    if any(len(result["results"]) != 1 for result in (classification, ms, dynamic)):
        raise ValueError("T49/T50 捕获缺少已保存计算结果")
    source_dataset = capture["frozen_dataset"]
    if (source_dataset["dataset_id"], source_dataset["dataset_version_id"], str(source_dataset["version"]), source_dataset["checksum"]) != (
        frozen_dataset["dataset_id"], frozen_dataset["version_id"], str(frozen_dataset["version"]), frozen_dataset["checksum"]
    ):
        raise ValueError("Task 捕获绑定的冻结集合与当前 T48 版本不一致")
    patients = {
        item["patient_id"]
        for result in (classification, ms, dynamic)
        for item in result["results"]
    }
    if len(patients) != 1 or capture["cohort_binding"]["patient_id"] not in patients:
        raise ValueError("classification、MS、dynamic 结果的患儿身份不一致")
    patient_id = next(iter(patients))
    configs = {
        "classification_rule": classification["results"][0]["rule"],
        "ms_rule": ms["results"][0]["rule"],
        "dynamic_policy": dynamic["results"][0]["policy"],
        "dynamic_window_policy": dynamic["results"][0]["window_policy"],
    }
    config_versions = {
        name: None if config is None else {
            "config_id": config["config_id"], "config_key": config["config_key"],
            "version": config["version"], "schema_version": config["schema_version"],
            "checksum": config["checksum"], "status": config["status"],
            "approval_count": len(config["approvals"]), "validation_count": len(config["validations"]),
        }
        for name, config in configs.items()
    }
    known_at_values = capture["cohort_binding"]["known_at_values"]
    assessment_at_values = capture["cohort_binding"]["assessment_at_values"]
    modes = capture["cohort_binding"]["modes"]
    is_frozen_member = patient_id in frozen_grouping["patient_partitions"]
    same_known_at = len(set(known_at_values)) == 1
    same_assessment_at = len(set(assessment_at_values)) == 1
    all_replay = set(modes) == {"replay"}
    capture_conditions = {
        "patient_is_frozen_member": is_frozen_member,
        "same_known_at": same_known_at,
        "same_assessment_at": same_assessment_at,
        "task_result_time_matches": all(capture["cohort_binding"]["task_result_time_matches"]),
        "all_result_inputs_match_task_selected_facts": capture["cohort_binding"]["all_result_inputs_match_task_selected_facts"],
        "no_fact_after_known_at": capture["cohort_binding"]["facts_unavailable_or_unverified_by_known_at"] == 0,
        "all_modes_replay": all_replay,
        "independent_event_labels_present": False,
        "prompt_outcomes_and_matching_policy_present": False,
    }
    capture_ready_for_metrics = all(capture_conditions.values())
    return {
        "status": "SIMULATION_SAVED_RULE_RESULTS_CAPTURED",
        "evidence_scope": capture["evidence_scope"],
        "database_name": capture["database_name"],
        "database_port": capture["database_port"],
        "capture_sha256": sha256(path),
        "captured_at": capture["captured_at"],
        "frozen_dataset": frozen_dataset,
        "task_provenance": {
            "classification_task_id": classification_task["task_id"],
            "classification_task_status": classification_task["status"],
            "classification_result_count": len(classification["results"]),
            "ms_task_id": ms_task["task_id"],
            "ms_task_status": ms_task["status"],
            "ms_result_count": len(ms["results"]),
            "dynamic_task_id": dynamic_task["task_id"],
            "dynamic_task_status": dynamic_task["status"],
            "dynamic_result_count": len(dynamic["results"]),
            "patient_id": patient_id,
            "patient_partition": frozen_grouping["patient_partitions"].get(patient_id),
            "member_of_frozen_set": is_frozen_member,
            "input_sha256": {"classification": classification_task["input_sha256"],
                             "ms": ms_task["input_sha256"], "dynamic": dynamic_task["input_sha256"]},
            "known_at_values": known_at_values,
            "assessment_at_values": assessment_at_values,
            "task_result_time_matches": capture["cohort_binding"]["task_result_time_matches"],
            "source_link_checks": capture["cohort_binding"]["source_link_checks"],
            "all_result_inputs_match_task_selected_facts": capture["cohort_binding"]["all_result_inputs_match_task_selected_facts"],
            "selected_fact_count": capture["cohort_binding"]["selected_fact_count"],
            "facts_unavailable_or_unverified_by_known_at": capture["cohort_binding"]["facts_unavailable_or_unverified_by_known_at"],
            "modes": modes,
            "capture_conditions": capture_conditions,
        },
        "fixed_versions": {"configs": config_versions, "task_type_model_versions": capture["task_type_model_versions"]},
        "clinical_metric_status": "NOT_RUN" if not capture_ready_for_metrics else "NOT_RUN_NEEDS_PERIOD_AND_OUTCOME_BINDING",
        "clinical_metric_reason": "三个 Task 的计算输出不构成独立答案。需冻结观察时段、原始独立 Gold、逐事实 available_at/known_at、提示结局、提示事件时间与预先固定配对方案，才能运行临床指标。",
        "source_traceability": {
            "classification_input_count": len(classification["results"][0]["inputs"]),
            "ms_input_count": len(ms["results"][0]["inputs"]),
            "dynamic_input_count": sum(len(item["inputs"]) for item in dynamic["results"]),
            "dynamic_signal_count": sum(len(item["signals"]) for item in dynamic["results"]),
            "dynamic_decision_count": sum(len(item["decisions"]) for item in dynamic["results"]),
            "dynamic_event_count": sum(len(item["events"]) for item in dynamic["results"]),
            "selected_fact_count": capture["cohort_binding"]["selected_fact_count"],
            "facts_unavailable_or_unverified_by_known_at": capture["cohort_binding"]["facts_unavailable_or_unverified_by_known_at"],
            "all_result_inputs_match_task_selected_facts": capture["cohort_binding"]["all_result_inputs_match_task_selected_facts"],
            "independent_answer_evidence": "NOT_READ_FROM_T49_T50_RESULT",
            "future_fact_exclusion": "NOT_VERIFIED_WITHOUT_FACT_AVAILABLE_AT_AND_INDEPENDENT_LABEL_TIMELINE",
        },
        "input_file_sha256": sha256(path),
    }


def evaluate_partition_fixture(path):
    data = read_json(path)
    if data["schema_version"] != "window3-partition-evaluation-fixture-1":
        raise ValueError("T59 分组场景 Schema 版本不匹配")
    if data["data_mode"] != "SIMULATION" or data["evidence_scope"] != "SIMULATION_FIXTURE_ONLY":
        raise ValueError("T59 分组场景必须标记为 SIMULATION_FIXTURE_ONLY")
    patients = {}
    for item in data["patients"]:
        patient_id = item["patient_id"]
        if patient_id in patients or item["partition"] not in {"development", "validation"}:
            raise ValueError("模拟患者身份重复或 partition 无效")
        if not item["source_ref"].startswith("SIMULATION_FIXTURE_ONLY:"):
            raise ValueError("模拟患者缺少明确场景来源引用")
        patients[patient_id] = item["partition"]
    if {"development", "validation"} - set(patients.values()):
        raise ValueError("分组场景必须分别包含 development 与 validation 患者")
    periods = {}
    for item in data["observation_periods"]:
        period_id = item["observation_period_id"]
        if period_id in periods or item["patient_id"] not in patients:
            raise ValueError("观察时段标识重复或患儿没有单一冻结分组")
        hours = float(item["observation_hours"])
        if not math.isfinite(hours) or hours <= 0:
            raise ValueError("观察时段长度必须是正数")
        periods[period_id] = item["patient_id"]
    if len(periods) != len(data["observation_periods"]):
        raise ValueError("观察时段标识重复")
    label_ids = set()
    period_ids_with_labels = set()
    partition_counts = {
        partition: {"patients": sum(value == partition for value in patients.values()),
                    "positive": 0, "negative": 0, "unknown_excluded_from_negative": 0,
                    "observation_periods": 0, "observation_person_hours": 0.0}
        for partition in ("development", "validation")
    }
    for label in data["labels"]:
        label_id = label["label_id"]
        period_id = label["observation_period_id"]
        if label_id in label_ids or period_id in period_ids_with_labels:
            raise ValueError("独立标签或观察时段重复")
        label_ids.add(label_id)
        period_ids_with_labels.add(period_id)
        patient_id = label["patient_id"]
        if patient_id not in patients or patients[patient_id] != label["partition"]:
            raise ValueError("标签患者或 partition 未绑定唯一分组")
        if period_id not in periods or periods[period_id] != patient_id:
            raise ValueError("标签没有绑定同一患儿观察时段")
        if label["label_state"] not in {"positive", "negative", "unknown"}:
            raise ValueError("标签状态必须为 positive、negative 或 unknown")
        if not label["source_ref"].startswith("SIMULATION_FIXTURE_ONLY:"):
            raise ValueError("标签来源必须明确标记为模拟场景")
        if label["label_state"] == "unknown":
            if label["reference_answer_ref"] is not None or label["raw_answer"] is not None:
                raise ValueError("unknown 标签不得带入阳性或阴性答案")
            partition_counts[label["partition"]]["unknown_excluded_from_negative"] += 1
        else:
            answer_digest = hashlib.sha256(label["raw_answer"].encode("utf-8")).hexdigest()
            if (label["answer_origin"] != "simulation_manual_independent"
                    or label["model_provider_request_id"] is not None
                    or answer_digest != label["raw_answer_sha256"]
                    or not label["reference_answer_ref"].startswith("SIMULATION_FIXTURE_ONLY:")):
                raise ValueError("模拟独立答案必须可校验、明确来源且没有模型请求标识")
            partition_counts[label["partition"]][label["label_state"]] += 1
        partition_counts[label["partition"]]["observation_periods"] += 1
        partition_counts[label["partition"]]["observation_person_hours"] += float(
            next(item["observation_hours"] for item in data["observation_periods"] if item["observation_period_id"] == period_id)
        )
    if period_ids_with_labels != set(periods):
        raise ValueError("每个模拟观察时段必须恰有一条独立标签")
    return {
        "status": "SIMULATION_PARTITION_FIXTURE_VERIFIED",
        "evidence_scope": data["evidence_scope"],
        "dataset": data["dataset"],
        "patient_partitions": patients,
        "patients_crossing_partitions": [],
        "denominators_by_partition": partition_counts,
        "clinical_metrics": "NOT_MEASURED",
        "clinical_interpretation": "SIMULATION_FIXTURE_ONLY; synthetic labels and denominators verify partition handling only.",
        "future_information_exclusion": "NOT_MEASURED",
        "input_file_sha256": sha256(path),
    }


def evaluate_family_observations(path):
    data = read_json(path)
    if data["schema_version"] != "window3-family-observations-1":
        raise ValueError("家庭任务观察 Schema 版本不匹配")
    if data["data_mode"] != "SIMULATION":
        raise ValueError("本窗口家庭观察程序只接收明确标识的模拟输入")
    if data.get("evidence_scope") != "SIMULATION_FIXTURE_ONLY":
        raise ValueError("模拟家庭观察必须声明只用于评价程序场景检查")
    rows = data["observations"]
    if not rows:
        raise ValueError("家庭理解评价没有任何实际任务观察行")
    required_understanding = ("source", "insufficient_data", "next_step", "contact_method")
    durations = []
    observation_ids = set()
    for row in rows:
        if not row.get("observation_id") or row["observation_id"] in observation_ids:
            raise ValueError("家庭任务观察引用缺失或重复")
        observation_ids.add(row["observation_id"])
        if not row.get("participant_ref") or not row.get("task_ref") or not row.get("observer_ref"):
            raise ValueError("家庭任务观察缺少模拟参与者、任务或观察者引用")
        if row["authorized"] is not True or not row.get("authorization_ref"):
            raise ValueError("家庭任务缺少参与授权依据")
        start = parse_time(row["started_at"])
        end = parse_time(row["finished_at"])
        if end < start:
            raise ValueError("家庭任务结束时间早于开始时间")
        if not isinstance(row["completed"], bool) or not isinstance(row["interrupted"], bool):
            raise ValueError("家庭任务完成与中断状态必须来自明确观察")
        if any(not isinstance(row[key], int) or row[key] < 0 for key in ("assistance_count", "transcription_error_count", "text_misunderstanding_count")):
            raise ValueError("家庭任务次数必须是非负整数")
        if not isinstance(row["device_profile"], str) or not row["device_profile"]:
            raise ValueError("缺少实际设备记录")
        durations.append((end - start).total_seconds())
        if set(row["comprehension"]) != set(required_understanding):
            raise ValueError("家庭理解观察项目不完整")
        if any(value is not None and not isinstance(value, bool) for value in row["comprehension"].values()):
            raise ValueError("家庭理解答案必须为明确判断或未回答")
    task_count = len(rows)
    completed = sum(row["completed"] is True for row in rows)
    understanding = {}
    for item in required_understanding:
        known = [row["comprehension"][item] for row in rows if row["comprehension"][item] is not None]
        understanding[item] = {
            "understood": sum(value is True for value in known),
            "misunderstood": sum(value is False for value in known),
            "unanswered": task_count - len(known),
            "known_denominator": len(known),
        }
    by_task = {}
    by_device = {}
    for row, duration in zip(rows, durations):
        for target, key in ((by_task, row["task_ref"]), (by_device, row["device_profile"])):
            bucket = target.setdefault(key, {"observations": 0, "completed": 0, "elapsed_seconds": 0.0, "assistance_count": 0, "transcription_error_count": 0, "text_misunderstanding_count": 0, "interruption_count": 0})
            bucket["observations"] += 1
            bucket["completed"] += row["completed"] is True
            bucket["elapsed_seconds"] += duration
            bucket["assistance_count"] += row["assistance_count"]
            bucket["transcription_error_count"] += row["transcription_error_count"]
            bucket["text_misunderstanding_count"] += row["text_misunderstanding_count"]
            bucket["interruption_count"] += row["interrupted"] is True
    return {
        "status": data["data_mode"],
        "evidence_scope": data["evidence_scope"],
        "observation_count": task_count,
        "task_completion": {"completed": completed, "incomplete_or_interrupted": task_count - completed, "denominator": task_count, "rate": completed / task_count if task_count else None},
        "elapsed_seconds": {"total": sum(durations), "mean": sum(durations) / len(durations) if durations else None, "observations": durations},
        "assistance_count": sum(row["assistance_count"] for row in rows) if task_count else None,
        "transcription_error_count": sum(row["transcription_error_count"] for row in rows) if task_count else None,
        "text_misunderstanding_count": sum(row["text_misunderstanding_count"] for row in rows) if task_count else None,
        "interruption_count": sum(row["interrupted"] is True for row in rows) if task_count else None,
        "comprehension": understanding if task_count else None,
        "device_profiles": sorted({row["device_profile"] for row in rows}),
        "task_groups": by_task,
        "device_groups": by_device,
        "observation_ids": sorted(observation_ids),
        "source_file_sha256": sha256(path),
    }


def evaluate_gold_evidence(path, labels):
    data = read_json(path)
    if data["schema_version"] != "window3-independent-gold-1" or data["data_mode"] != "SIMULATION":
        raise ValueError("T32 独立答案资料必须使用指定 Schema 并标记 SIMULATION")
    if data.get("evidence_scope") != "SIMULATION_FIXTURE_ONLY":
        raise ValueError("模拟答案必须声明只用于评价程序场景检查")
    expected = {item.get("id", item.get("label_id")): item for item in labels if item["label_state"] in {"positive", "negative"}}
    received = {}
    for answer in data["answers"]:
        label_id = answer["label_id"]
        if label_id in received or label_id not in expected:
            raise ValueError("T32 Gold 答案引用重复或不属于固定标签")
        label = expected[label_id]
        if answer["reference_answer_ref"] != label["reference_answer_ref"] or answer["label_state"] != label["label_state"]:
            raise ValueError("T32 Gold 答案未绑定冻结标签版本")
        if answer["answer_origin"] != "simulation_manual_independent" or answer.get("model_provider_request_id") is not None:
            raise ValueError("T32 独立答案不得来自模型提取器")
        digest = hashlib.sha256(answer["raw_answer"].encode("utf-8")).hexdigest()
        if digest != answer["raw_answer_sha256"]:
            raise ValueError("T32 Gold 原文摘要不匹配")
        if answer.get("source_scope") != "synthetic scenario fixture; not original source text":
            raise ValueError("T32 模拟答案必须说明它不属于原始资料文字")
        received[label_id] = {"reference_answer_ref": answer["reference_answer_ref"], "label_state": answer["label_state"], "raw_answer_sha256": digest, "author_id": answer["author_id"], "reviewer_id": answer.get("reviewer_id"), "source_scope": answer["source_scope"]}
    if set(received) != set(expected):
        raise ValueError("T32 独立答案没有覆盖全部已确认标签")
    return {"status": "SIMULATION_FIXTURE_ANSWER_VERIFIED", "evidence_scope": data["evidence_scope"], "answer_count": len(received), "answers": list(received.values()), "evidence_file_sha256": sha256(path)}


def evaluate_t32(t48_path, family_path=None, gold_path=None):
    t59 = evaluate_clinical(t48_path)
    dataset = t59["dataset"]
    labels = read_json(t48_path)["inputs"]["clinical"]["dataset"]["labels"]
    answer_refs = [label["reference_answer_ref"] for label in labels if label.get("reference_answer_ref")]
    gold = evaluate_gold_evidence(gold_path, labels) if gold_path else None
    family = evaluate_family_observations(family_path) if family_path else {
        "status": "NOT_RUN",
        "observation_count": None,
        "task_completion": None,
        "elapsed_seconds": None,
        "assistance_count": None,
        "transcription_error_count": None,
        "text_misunderstanding_count": None,
        "interruption_count": None,
        "comprehension": None,
        "reason": "没有获准参与者的家庭任务原始观察记录。",
    }
    return {
        "status": "SIMULATION_PREPARATION",
        "dataset": dataset,
        "independent_answers": {
            "confirmed_answer_reference_count": len(answer_refs),
            "answer_references": answer_refs,
            "raw_answer_content_status": gold["status"] if gold else "NOT_READABLE_FROM_CAPTURED_EVIDENCE",
            "raw_answer_evidence": gold,
            "clinical_reviewer_status": "NOT_RUN",
            "model_output_used_to_generate_independent_answer": False if gold else "NOT_VERIFIED_FROM_T48_CAPTURE",
        },
        "historical_replay": t59["replay"],
        "patient_grouping": t59["grouping"],
        "treatment_age_infection_completeness_groups": {
            "status": "NOT_MEASURED",
            "required_dimensions": ["treatment_status", "age_group", "infection_context", "data_completeness"],
            "reason": "T48 冻结清单没有这些患者分层结果；不能从一名模拟患儿推断分组差异。",
        },
        "family_understanding": family,
        "real_clinical_validity": "NOT_RUN",
        "real_family_understanding": "NOT_RUN",
    }


def write_report(path, payload):
    payload["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    payload["runner_sha256"] = sha256(__file__)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", choices=("T55", "T59", "T32", "all"), default="all")
    parser.add_argument("--ocr-evidence", default=str(ROOT / "docs/testing/evidence/QA03/ocr-evals.json"))
    parser.add_argument("--t48-evidence", default=str(ROOT / ".planning-work/t48-simulation.json"))
    parser.add_argument("--predictions", default=None)
    parser.add_argument("--saved-rule-capture", default=None)
    parser.add_argument("--partition-fixture", default=None)
    parser.add_argument("--family-evidence", default=None)
    parser.add_argument("--gold-evidence", default=None)
    parser.add_argument("--adopted-evidence", default=None)
    parser.add_argument("--saved-reread-evidence", default=None)
    parser.add_argument("--runtime-candidate-evidence", default=None)
    parser.add_argument("--output-dir", default=str(ROOT / ".planning-work/parallel/window3"))
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    if args.task in ("T55", "all"):
        ocr_report = evaluate_ocr(args.ocr_evidence)
        voice_report = evaluate_voice(ROOT / "docs/testing/evidence/QA03/voice-evals.json")
        adopted = evaluate_field_layer(args.adopted_evidence, "adopted") if args.adopted_evidence else {"status": "NOT_RUN", "field_counts": None, "rows": None, "reason": "没有与本组模型调用身份对应的 sjia_simulation 本人逐字段决定。"}
        saved_reread = evaluate_field_layer(args.saved_reread_evidence, "saved_reread") if args.saved_reread_evidence else {"status": "NOT_RUN", "field_counts": None, "rows": None, "reason": "没有同一候选业务字段的数据库保存和重新读取证据。"}
        candidate_layer = {"rows": [
            {
                "case_id": row["case_id"],
                "candidate_index": row["candidate_index"],
                "provider_request_id": row["provider_request_id"],
                "input_sha256": row["input_sha256"],
                "values": {key: row["candidate"].get(key, MISSING) for key in REQUIRED_FIELDS if key != "source"},
            }
            for row in ocr_report["candidate"]["rows"]
        ]}
        paired_layers = {}
        if adopted["rows"] is not None:
            paired_layers["candidate_to_adopted"] = compare_field_layers(candidate_layer, adopted, "candidate", "adopted")
        if adopted["rows"] is not None and saved_reread["rows"] is not None:
            paired_layers["adopted_to_saved_reread"] = compare_field_layers(adopted, saved_reread, "adopted", "saved_reread")
        if saved_reread["rows"] is not None:
            paired_layers["candidate_to_saved_reread"] = compare_field_layers(candidate_layer, saved_reread, "candidate", "saved_reread")
        runtime_candidate = evaluate_runtime_candidate(args.runtime_candidate_evidence) if args.runtime_candidate_evidence else None
        if runtime_candidate is not None:
            if args.adopted_evidence is None and runtime_candidate["adopted"]["status"] == "SIMULATION_CONFIRMED":
                adopted = {"status": runtime_candidate["adopted"]["status"], "field_counts": runtime_candidate["field_counts_by_layer"]["adopted"], "rows": None, "candidate_set_id": runtime_candidate["candidate_set_id"]}
            if args.saved_reread_evidence is None and runtime_candidate["saved_reread"]["status"] == "SIMULATION_SAVED_AND_READ":
                saved_reread = {"status": runtime_candidate["saved_reread"]["status"], "field_counts": runtime_candidate["field_counts_by_layer"]["saved_reread"], "rows": None, "candidate_set_id": runtime_candidate["candidate_set_id"], "record_id": runtime_candidate["saved_reread"]["record_id"]}
            paired_layers.update(runtime_candidate["paired_field_counts"])
        report = {
            "status": "SIMULATION",
            "report_ocr": ocr_report,
            "voice": voice_report,
            "adopted": adopted,
            "saved_reread": saved_reread,
            "paired_layers": paired_layers,
            "same_candidate_status": runtime_candidate["status"] if runtime_candidate else "NOT_RUN",
            "current_database": "sjia_simulation",
            "evidence_reuse": "QA03 原件摘要、模型版本、Prompt 摘要、请求计量与用途已经核对；其 OCR Schema 摘要未绑定当前版本，因此只用于历史 SIMULATION 字段检查。",
        }
        if runtime_candidate is not None:
            report["runtime_candidate"] = runtime_candidate
        write_report(output_dir / "T55-simulation-evaluation.json", report)
        print(json.dumps({"task": "T55", "status": report["status"], "same_candidate_status": report["same_candidate_status"], "ocr_requests": report["report_ocr"]["source_evidence"]["provider_request_count"], "voice_calls": report["voice"]["source_evidence"]["recorded_calls"], "adopted": report["adopted"]["status"], "saved_reread": report["saved_reread"]["status"], "output": str(output_dir / "T55-simulation-evaluation.json")}, ensure_ascii=False))
    if args.task in ("T59", "all"):
        report = evaluate_clinical(args.t48_evidence)
        if args.predictions:
            report["simulation_algorithm_check"] = evaluate_prediction_input(args.predictions, report["dataset"], report["grouping"])
        if args.saved_rule_capture:
            report["saved_t49_t50_results"] = evaluate_saved_rule_capture(args.saved_rule_capture, report["dataset"], report["grouping"])
        if args.partition_fixture:
            report["simulation_partition_fixture"] = evaluate_partition_fixture(args.partition_fixture)
        write_report(output_dir / "T59-simulation-evaluation.json", report)
        print(json.dumps({"task": "T59", "status": report["status"], "denominators": report["denominators"], "metrics": report["metrics"]["status"], "output": str(output_dir / "T59-simulation-evaluation.json")}, ensure_ascii=False))
    if args.task in ("T32", "all"):
        report = evaluate_t32(args.t48_evidence, args.family_evidence, args.gold_evidence)
        write_report(output_dir / "T32-simulation-evaluation.json", report)
        print(json.dumps({"task": "T32", "status": report["status"], "independent_answer_refs": report["independent_answers"]["confirmed_answer_reference_count"], "family_understanding": report["family_understanding"]["status"], "output": str(output_dir / "T32-simulation-evaluation.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
