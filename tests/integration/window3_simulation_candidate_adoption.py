import json
import os
import urllib.request
import http.cookiejar
import uuid
from datetime import datetime, timezone
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.simulation")
django.setup()

from django.db import connection, transaction
from recognition.models import CandidateConfirmation, CandidateField


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / ".planning-work/parallel/window3/T55-current-candidate-evidence.json"
OUTPUT = ROOT / ".planning-work/parallel/window3/T55-current-candidate-evidence.json"
API_ROOT = "http://127.0.0.1:8001"


def api_session():
    password = os.environ["SJIA_SIMULATION_PASSWORD"]
    cookies = http.cookiejar.CookieJar()
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    with client.open(f"{API_ROOT}/api/auth/bootstrap") as response:
        bootstrap = json.load(response)
    login = urllib.request.Request(
        f"{API_ROOT}/api/auth/login",
        data=json.dumps({"username": "simulation-family", "password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-CSRFToken": bootstrap["csrf_token"]},
    )
    with client.open(login) as response:
        if response.status != 200:
            raise RuntimeError("模拟家庭账号登录失败")
    with client.open(f"{API_ROOT}/api/session") as response:
        session = json.load(response)
    if session.get("audience") != "family":
        raise RuntimeError("API 会话没有 family 权限")
    return client, bootstrap["csrf_token"], session


def get_json(client, path):
    with client.open(f"{API_ROOT}{path}") as response:
        return response.status, json.load(response)


def post_json(client, path, payload, csrf, extra_headers=None):
    headers = {"Content-Type": "application/json", "X-CSRFToken": csrf}
    headers.update(extra_headers or {})
    request = urllib.request.Request(f"{API_ROOT}{path}", data=json.dumps(payload).encode("utf-8"), headers=headers)
    with client.open(request) as response:
        return response.status, json.load(response)


def normalized_values(field, source_text):
    return {
        "name": field.get("original_name"),
        "comparator": field.get("qualifier"),
        "value": field.get("raw_numeric"),
        "unit": field.get("raw_unit"),
        "medical_time": "unknown" if field.get("sample_time_origin") == "unknown" else None,
        "source": source_text,
    }


def main():
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    candidate_id = evidence["candidate"]["candidate_set_id"]
    patient_id = evidence["candidate"]["patient_id"]
    if evidence["data_mode"] != "SIMULATION" or evidence["database_name"] != "sjia_simulation":
        raise RuntimeError("只允许处理明确的共享模拟候选")
    if evidence.get("adopted") or evidence.get("saved_reread"):
        raise RuntimeError("当前候选已有采用或保存证据，停止重复提交")
    client, csrf, session = api_session()
    if evidence["task"].get("actor_id") != session["principal_id"] or evidence["task"].get("family_principal_id") != evidence["task"].get("actor_id"):
        raise RuntimeError("simulation-family 与原模型任务创建账号身份不符")
    candidate_status, candidate_view = get_json(client, f"/api/candidate-sets/{candidate_id}")
    config_status, field_config = get_json(client, f"/api/patients/{patient_id}/field-config?kind=report")
    if candidate_status != 200 or candidate_view["status"] != "pending_confirmation":
        raise RuntimeError("当前候选不再等待本人确认")
    if config_status != 200 or field_config["state"] != "available" or not field_config["config_version"]:
        raise RuntimeError("报告字段配置当前不可写")
    if field_config["config_version"] != evidence["api"]["field_config_version"]:
        raise RuntimeError("报告字段配置版本与候选采集时不同")
    if len(candidate_view["candidates"]) != 2:
        raise RuntimeError("当前模拟候选字段数量与独立答案资料不同")
    target = f"/api/patients/{patient_id}/records"
    request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"window3-t55-{candidate_id}"))
    fields = [{**item["proposed_field"], "candidate_field_id": item["candidate_field_id"]}
        for item in candidate_view["candidates"]]
    decisions = [{"candidate_field_id": item["candidate_field_id"], "decision": "accepted"}
        for item in candidate_view["candidates"]]
    payload = {
        "kind": "report",
        "capture_method": "ocr_confirmed",
        "field_config_version": field_config["config_version"],
        "candidate_set_id": candidate_id,
        "decisions": decisions,
        "fields": fields,
        "file_ids": [evidence["candidate"]["source_file_id"]],
        "source_organization": "SIMULATION laboratory",
        "report": {"report_status": "unknown"},
    }
    intent_status, intent = post_json(client, "/api/write-intents", {
        "operation_id": "record_create",
        "target_key": target,
        "request_id": request_id,
        "schema_version": "back-json-1",
        "payload": payload,
    }, csrf)
    if intent_status != 201 or not intent.get("intent_id") or not intent.get("generation"):
        raise RuntimeError("记录写入意图没有实际准备完成")
    save_status, receipt = post_json(client, target, payload, csrf, {
        "X-Write-Intent": intent["intent_id"],
        "X-Intent-Generation": intent["generation"],
        "Idempotency-Key": request_id,
    })
    if save_status != 201 or not receipt.get("record_id") or not receipt.get("record_version_id"):
        raise RuntimeError("模拟报告记录没有实际保存")
    saved_at = datetime.now(timezone.utc).isoformat()
    read_status, reread = get_json(client, f"/api/records/{receipt['record_id']}")
    read_at = datetime.now(timezone.utc).isoformat()
    if read_status != 200 or reread["record_version_id"] != receipt["record_version_id"]:
        raise RuntimeError("模拟报告记录没有按准确保存版本重新读取")
    candidate_after_status, candidate_after = get_json(client, f"/api/candidate-sets/{candidate_id}")
    if candidate_after_status != 200 or candidate_after["status"] != "confirmed":
        raise RuntimeError("模拟候选本人决定没有保存")
    candidate_fields = list(CandidateField.objects.filter(candidate_set_id=candidate_id).order_by("field_path"))
    candidate_path_by_id = {str(item.id): item.field_path for item in candidate_fields}
    confirmation_ids = [item["confirmation_id"] for item in reread["fields"]]
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        confirmations = list(CandidateConfirmation.objects.filter(id__in=confirmation_ids).values(
            "id", "candidate_field_id", "confirmed_by_id", "confirmed_text", "decision", "confirmed_at"))
    confirmation_by_id = {str(item["id"]): item for item in confirmations}
    adopted_fields = []
    saved_fields = []
    for field in reread["fields"]:
        confirmation = confirmation_by_id[field["confirmation_id"]]
        if str(confirmation["confirmed_by_id"]) != session["principal_id"] or confirmation["decision"] != "accepted":
            raise RuntimeError("本人确认主体或决定与实际家庭账号不符")
        confirmed = json.loads(confirmation["confirmed_text"])
        path = candidate_path_by_id[str(confirmation["candidate_field_id"])]
        anchor_text = field["source_anchor"]["original_text"]
        adopted_fields.append({"field_path": path, "values": normalized_values(confirmed, anchor_text),
            "decision": confirmation["decision"], "confirmed_by": str(confirmation["confirmed_by_id"]),
            "confirmed_at": confirmation["confirmed_at"].isoformat()})
        saved_values = {
            "name": field["original_name"],
            "comparator": field["qualifier"],
            "value": field["raw_numeric"],
            "unit": field["raw_unit"],
            "medical_time": "unknown" if field["sample_time_origin"] == "unknown" else None,
            "source": anchor_text,
        }
        saved_fields.append({"field_path": path, "field_id": field["field_id"], "values": saved_values,
            "confirmation_id": field["confirmation_id"], "source_anchor": field["source_anchor"]})
    if len(adopted_fields) != len(candidate_path_by_id) or len(saved_fields) != len(candidate_path_by_id):
        raise RuntimeError("本人确认及保存重读字段没有完整覆盖同一候选")
    evidence["api"].update({
        "field_config_http_status": config_status,
        "field_config_state": field_config["state"],
        "field_config_reason": field_config["reason_code"],
        "field_config_version": field_config["config_version"],
        "write_intent_status": intent_status,
        "record_save_status": save_status,
        "record_read_status": read_status,
        "candidate_after_status": candidate_after_status,
    })
    evidence["candidate_after"] = candidate_after
    evidence["adopted"] = {
        "status": "SIMULATION_CONFIRMED",
        "candidate_set_id": candidate_id,
        "actor_id": session["principal_id"],
        "fields": adopted_fields,
    }
    evidence["saved_reread"] = {
        "status": "SIMULATION_SAVED_AND_READ",
        "candidate_set_id": candidate_id,
        "record_id": receipt["record_id"],
        "record_version_id": receipt["record_version_id"],
        "version": str(receipt["version"]),
        "saved_at": reread["recorded_at"],
        "save_response_observed_at": saved_at,
        "read_at": read_at,
        "fields": saved_fields,
        "api_view": reread,
    }
    OUTPUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "SIMULATION_CONFIRMED_SAVED_REREAD",
        "candidate_set_id": candidate_id,
        "record_id": receipt["record_id"],
        "record_version_id": receipt["record_version_id"],
        "field_count": len(saved_fields),
        "evidence": str(OUTPUT),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
