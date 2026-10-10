import hashlib
import json
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid5

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
DELIVERY = ROOT / "data-design"
DATA = json.loads((ROOT / "demo-data/current-patient/case.json").read_text(encoding="utf-8"))
PATIENT = DATA["patient"]["id"]
NAMESPACE = UUID(PATIENT)
FIELDS = {}
TIMES = {}
LINKS = {}
ENTITIES = {key: [] for key in ("profiles", "episodes", "groups", "statements", "imaging_studies", "image_links", "medication_events", "procedures", "scores", "followups", "quality_issues")}
THERAPY_MONTHS = {
    "therapy-steroid-stop-2024": "2024-11", "therapy-prednisone-restart": "2025-02",
    "therapy-naproxen": "2025-03", "therapy-mtx-oral": "2025-03", "therapy-prednisone-taper": "2025-03",
    "therapy-cyclosporine-taper": "2025-03", "therapy-supportive": "2025-03",
    "therapy-mtx-route-change": "2025-05", "therapy-naproxen-stop": "2026-02", "therapy-tocilizumab-interval": "2026-08"
}
THERAPY_RANGES = {"therapy-2024-critical": ("2024-01", "2024-03"), "therapy-2024-stop": ("2024-04", "2024-08")}


def identity(key):
    return str(uuid5(NAMESPACE, key))


def field(key, version, source, name, raw, value=None, qualifier=None, unit=None, flag=None, reference=None):
    identifier = identity("field/" + key)
    if identifier in FIELDS:
        raise ValueError("字段标识重复：" + key)
    FIELDS[identifier] = {"id": identifier, "patient_id": PATIENT, "record_version_id": version, "source_id": source,
                          "original_name": name, "raw_text": raw, "value_type": "decimal" if value is not None else "text",
                          "raw_numeric": value, "qualifier": qualifier, "raw_unit": unit, "report_flag": flag, "reference_interval": reference}
    LINKS[key] = identifier
    return identifier


def context(key, source, raw):
    version = identity("record-version/" + key)
    original = field(key, version, source, key, raw)
    return {"id": identity("entity/" + key), "patient_id": PATIENT, "record_version_id": version, "source_field_id": original}


def time_id(key):
    return None if key is None else identity("time/" + key)


def add_time(key, raw, precision, local_date=None, month=None, start_month=None, end_month=None):
    entry = {"id": time_id(key), "patient_id": PATIENT, "raw_text": raw, "precision": precision,
             "local_date": local_date, "local_datetime": None, "timezone_name": None, "utc_offset_minutes": None, "instant": None,
             "resolution_status": "local_only" if precision in ("date", "month", "month_range") else "unknown",
             "local_year": None, "local_month": None, "end_year": None, "end_month": None,
             "relative_text": raw if precision == "relative" else None}
    if month or start_month:
        value = datetime.strptime(month or start_month, "%Y-%m")
        entry.update(local_year=value.year, local_month=value.month)
    if end_month:
        value = datetime.strptime(end_month, "%Y-%m")
        entry.update(end_year=value.year, end_month=value.month)
    TIMES[entry["id"]] = entry
    return entry["id"]


def statement(key, source, raw, label, category, status, status_text, time_ref=None, subject="patient", statement_type="history"):
    item = {**context(key, source, raw), "statement_type": statement_type, "subject": subject, "category": category,
            "concept_code": None, "label": label, "assertion_status": status, "status_raw_text": status_text,
            "medical_time_id": time_id(time_ref), "body_site": None, "laterality": None, "evidence": []}
    ENTITIES["statements"].append(item)
    return item["id"]


def main():
    for item in DATA["time_contexts"]:
        add_time(item["id"], item["raw_text"], item["precision"], item["date"], item["month"], item["start_month"], item["end_month"])
    patient = DATA["patient"]
    ENTITIES["profiles"].append({**context("patient", patient["source_id"], patient["reported_age"]["raw_text"] + "；" + patient["chief_complaint"]),
        "sex": patient["sex"], "reported_age_years": patient["reported_age"]["years"], "reported_age_months": patient["reported_age"]["months"],
        "age_raw_text": patient["reported_age"]["raw_text"], "age_reference_time_id": None, "birth_date": patient["birth_date"],
        "patient_name": patient["name"], "chief_complaint": patient["chief_complaint"]})
    for item in DATA["history"] + DATA["qualitative_findings"]:
        kind = "physical_exam" if item["category"] == "physical_exam" else "history"
        statement(item["id"], item["source_id"], item["source_quote"], item["text"], item["category"], "unknown", item["text"], item["time_ref"], "family" if item["category"] == "family_history" else "patient", kind)
        if item["category"] == "care_episode":
            ENTITIES["episodes"].append({**context("episode/" + item["id"], item["source_id"], item["source_quote"]),
                "episode_type": "hospitalization", "label": item["text"], "start_time_id": time_id(item["time_ref"]),
                "end_time_id": None, "organization_text": None, "department_text": None})
    for panel in DATA["measurement_panels"]:
        group = {**context(panel["id"], panel["source_id"], panel.get("source_quote", panel["label"])),
            "group_type": "vitals" if panel["id"] == "admission-vitals" else "laboratory", "label": panel["label"],
            "specimen_text": panel["specimen"], "method_text": None, "medical_time_id": time_id(panel["time_ref"]),
            "source_quote": panel.get("source_quote"), "members": []}
        for ordinal, item in enumerate(panel["items"], 1):
            identifier = field(panel["id"] + "/" + item["id"], group["record_version_id"], panel["source_id"], item["name"], item["raw_text"], item["value"], item["qualifier"], item["unit"], item["flag"], item["reference_interval"])
            group["members"].append({"field_id": identifier, "ordinal": ordinal, "role": "result"})
        ENTITIES["groups"].append(group)
    for panel in DATA["microbiology"]:
        group = {**context(panel["id"], panel["source_id"], panel["method"]), "group_type": "microbiology", "label": panel["method"],
            "specimen_text": panel["specimen"], "method_text": panel["method"], "medical_time_id": time_id(panel["time_ref"]), "source_quote": None, "members": []}
        for ordinal, item in enumerate(panel["results"], 1):
            identifier = field(panel["id"] + "/" + str(ordinal), group["record_version_id"], panel["source_id"], item["organism"], item["raw_text"], item["value"], "eq" if item["value"] else None, item["unit"], item["interpretation"])
            group["members"].append({"field_id": identifier, "ordinal": ordinal, "role": "result"})
        ENTITIES["groups"].append(group)
    for item in DATA["diagnoses"]:
        statement(item["id"], item["source_id"], item["basis"], item["name"], None, "source_diagnosis", item["status"], item["time_ref"], statement_type="diagnosis")
    for ordinal, item in enumerate(DATA["differential_diagnoses"], 1):
        statement("differential/" + str(ordinal), item["source_id"], item["source_quote"], item["name"], None, "unknown", item["source_status"], statement_type="differential")
    for item in DATA["imaging_studies"]:
        ENTITIES["imaging_studies"].append({**context(item["id"], item["source_id"], item["report_text"]),
            "report_id": identity("report/" + item["id"]), "modality": item["modality"], "contrast": item["contrast"],
            "study_time_id": time_id(item["time_ref"]), "external_study_ref": None,
            "sites": [{"ordinal": 1, "body_site": item["body_site"], "laterality": item["laterality"]}]})
    for item in DATA["image_assets"]:
        ENTITIES["image_links"].append({**context("asset/" + item["source_id"], item["source_id"], item["note"]),
            "file_id": identity("file/" + item["source_id"]), "study_id": None, "link_status": "requires_confirmation",
            "evidence_field_id": None, "evidence_record_version_id": None, "confirmed_by": None, "confirmed_at": None})
    for item in DATA["therapy_events"]:
        key = "therapy-time/" + item["id"]
        if item["precision"] == "month":
            event_time = add_time(key, item["time_text"], "month", month=THERAPY_MONTHS[item["id"]])
        elif item["precision"] == "month_range":
            start, end = THERAPY_RANGES[item["id"]]
            event_time = add_time(key, item["time_text"], "month_range", start_month=start, end_month=end)
        else:
            event_time = add_time(key, item["time_text"], item["precision"], item["date"])
        event = {**context(item["id"], item["source_id"], item["source_quote"]), "action": item["action"],
            "medical_time_id": event_time, "previous_event_id": None, "reason_statement_id": None, "note": item["note"], "items": []}
        for ordinal, name in enumerate(item["medications"], 1):
            event["items"].append({"id": identity("medication/" + item["id"] + "/" + str(ordinal)), "source_field_id": event["source_field_id"],
                "ordinal": ordinal, "medication_name": name, "medication_code": None, "dose": item["dose"], "dose_unit": item["dose_unit"],
                "concentration_text": None, "route_text": item["route"], "frequency_text": item["frequency"], "interval_value": None, "interval_unit": None})
        ENTITIES["medication_events"].append(event)
        for ordinal, name in enumerate(item["procedures"], 1):
            ENTITIES["procedures"].append({**context("procedure/" + item["id"] + "/" + str(ordinal), item["source_id"], item["source_quote"]),
                "procedure_name": name, "medical_time_id": event_time, "related_medication_event_id": event["id"], "body_site": None})
    for row in DATA["disease_activity"]["rows"]:
        key = "score/" + row["date"]
        score = {**context(key, "score-image", "SJADAS " + row["sjadas"]), "instrument": "SJADAS", "instrument_version": None,
            "medical_time_id": add_time(key, row["date"], "date", row["date"]), "context_text": row["context"], "note": row["note"], "components": []}
        score["total_field_id"] = field(key + "/total", score["record_version_id"], "score-image", "SJADAS", row["sjadas"], row["sjadas"], "eq")
        for ordinal, code in enumerate(("sms", "ajc", "physician_global", "parent_patient_global", "esr"), 1):
            identifier = field(key + "/" + code, score["record_version_id"], "score-image", code, row[code], row[code], "eq")
            score["components"].append({"field_id": identifier, "component_code": code, "ordinal": ordinal})
        ENTITIES["scores"].append(score)
    for row in DATA["followup_records"]:
        followup = {**context(row["id"], row["source_id"], row["label"]), "kind": row["kind"], "label": row["label"],
            "medical_time_id": time_id("score/" + row["score_row_date"]) if row["score_row_date"] else time_id(row["time_ref"]),
            "channel_text": "线上门诊" if row["kind"] == "latest_followup" else None, "references": []}
        def link(kind, target_key, target_id):
            reference = {"id": identity("followup-link/" + row["id"] + "/" + target_id), "relation_kind": kind,
                         "statement_id": None, "group_id": None, "score_id": None, "medication_event_id": None}
            reference[target_key] = target_id
            followup["references"].append(reference)
        if row["score_row_date"]:
            link("score", "score_id", identity("entity/score/" + row["score_row_date"]))
        for ref in row["laboratory_refs"]:
            link("laboratory", "group_id", identity("entity/" + ref))
        for ordinal, outcome in enumerate(row["outcomes"], 1):
            target = statement("outcome/" + row["id"] + "/" + str(ordinal), row["source_id"], outcome["source_quote"], outcome["name"], None,
                "unknown", outcome["status"], row["time_ref"], statement_type="followup_mode" if outcome["name"] == "随访方式" else "symptom")
            link("outcome", "statement_id", target)
        if row["kind"] == "latest_followup":
            current = {**context("current-treatment", "case-text", DATA["followup"]["source_quote"]), "action": "ongoing",
                "medical_time_id": time_id("latest-followup"), "previous_event_id": None, "reason_statement_id": None, "note": None, "items": []}
            for ordinal, drug in enumerate(DATA["followup"]["current_medication_assertions"], 1):
                current["items"].append({"id": identity("current-medication/" + str(ordinal)), "source_field_id": current["source_field_id"], "ordinal": ordinal,
                    "medication_name": drug["name"], "medication_code": None, "dose": drug["dose"], "dose_unit": drug["unit"], "concentration_text": None,
                    "route_text": drug["route_text"], "frequency_text": drug["frequency"], "interval_value": None, "interval_unit": None})
            ENTITIES["medication_events"].append(current)
            link("reported_current_treatment", "medication_event_id", current["id"])
        ENTITIES["followups"].append(followup)
    for issue in DATA["data_issues"]:
        item = {**context(issue["id"], "case-text", issue["description"]), "category": issue["category"], "description": issue["description"],
            "handling": issue["handling"], "status": "open", "resolution_text": None, "resolution_field_id": None, "resolution_record_version_id": None,
            "resolved_by": None, "resolved_at": None, "references": []}
        for ordinal, ref in enumerate(issue["refs"], 1):
            if ref == "disease_activity":
                for score in ENTITIES["scores"]:
                    item["references"].append({"id": identity("issue-reference/" + issue["id"] + "/" + score["id"]),
                        "field_id": score["source_field_id"], "record_version_id": score["record_version_id"], "file_id": None, "role": "affected"})
                continue
            reference = {"id": identity("issue-reference/" + issue["id"] + "/" + str(ordinal)), "field_id": None, "record_version_id": None, "file_id": None, "role": "affected"}
            if ref in {s["id"] for s in DATA["sources"]}:
                reference["file_id"] = identity("file/" + ref)
            else:
                reference["field_id"] = LINKS[ref]
                reference["record_version_id"] = FIELDS[LINKS[ref]]["record_version_id"]
            item["references"].append(reference)
        ENTITIES["quality_issues"].append(item)
    schema = json.loads((DELIVERY / "case-extension.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(ENTITIES)
    time_schema = json.loads((DELIVERY / "dataset.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(time_schema)
    validator = Draft202012Validator({"$ref": "#/$defs/medicalTime", "$defs": time_schema["$defs"]}, format_checker=FormatChecker())
    for item in TIMES.values():
        validator.validate(item)
    for collection in ENTITIES.values():
        for item in collection:
            source = FIELDS[item["source_field_id"]]
            if source["record_version_id"] != item["record_version_id"]:
                raise ValueError("来源字段与实体版本不一致")
    entity_ids = {item["id"] for collection in ENTITIES.values() for item in collection}
    for encounter in ENTITIES["followups"]:
        for reference in encounter["references"]:
            targets = [reference[key] for key in ("statement_id", "group_id", "score_id", "medication_event_id") if reference[key] is not None]
            if len(targets) != 1 or targets[0] not in entity_ids:
                raise ValueError("随访关联缺少实体")
    for group in ENTITIES["groups"]:
        for member in group["members"]:
            if FIELDS[member["field_id"]]["record_version_id"] != group["record_version_id"]:
                raise ValueError("分组成员与来源版本不一致")
    result = {"dataset_version": DATA["dataset_version"], "status": "DESIGN_PROJECTION_VALIDATED", "patient_id": PATIENT,
        "source_sha256": hashlib.sha256((ROOT / "demo-data/current-patient/case.json").read_bytes()).hexdigest(),
        "scope": "从用户病例生成确定性技术标识及领域结构，未创建生产账户、用途批准或临床核对；正式记录、文件和报告需在授权导入事务中建立。",
        "medical_times": list(TIMES.values()), "fields": list(FIELDS.values()), "case_details": ENTITIES}
    (DELIVERY / "case-design-projection.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "medical_times": len(TIMES), "fields": len(FIELDS), "entities": {key: len(value) for key, value in ENTITIES.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
