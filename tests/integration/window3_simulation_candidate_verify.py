import json
import os
import urllib.request
import http.cookiejar
from datetime import datetime, timezone
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.simulation")
django.setup()

from django.db import connection, transaction
from recognition.models import CandidateConfirmation, CandidateField
from requests.models import WriteIntent, WriteRequest
from records.models import RecordVersion


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / ".planning-work/parallel/window3/T55-current-candidate-evidence.json"
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
    return client, session


def get_json(client, path):
    with client.open(f"{API_ROOT}{path}") as response:
        return response.status, json.load(response)


def main():
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    if evidence["data_mode"] != "SIMULATION" or evidence["database_name"] != "sjia_simulation":
        raise RuntimeError("只允许复核明确的共享模拟候选")
    if evidence.get("adopted", {}).get("status") != "SIMULATION_CONFIRMED":
        raise RuntimeError("没有已完成的同候选模拟家庭决定")
    if evidence.get("saved_reread", {}).get("status") != "SIMULATION_SAVED_AND_READ":
        raise RuntimeError("没有已完成的同候选记录保存与重读")
    client, session = api_session()
    candidate_id = evidence["candidate"]["candidate_set_id"]
    patient_id = evidence["candidate"]["patient_id"]
    record_id = evidence["saved_reread"]["record_id"]
    expected_version_id = evidence["saved_reread"]["record_version_id"]
    candidate_status, candidate = get_json(client, f"/api/candidate-sets/{candidate_id}")
    record_status, record = get_json(client, f"/api/records/{record_id}")
    if candidate_status != 200 or candidate["status"] != "confirmed":
        raise RuntimeError("当前 API 没有读取到已确认候选")
    if record_status != 200 or record["record_id"] != record_id or record["record_version_id"] != expected_version_id:
        raise RuntimeError("当前 API 重读的记录或版本与保存回执不一致")
    if record["patient_id"] != patient_id or len(record["fields"]) != len(candidate["candidates"]):
        raise RuntimeError("当前 API 重读记录与候选患儿或字段分母不一致")
    if not any(item.get("file_id") == evidence["candidate"]["source_file_id"] for item in record.get("attachments", [])):
        raise RuntimeError("当前 API 重读记录没有绑定同一原件")
    field_by_id = {str(item.id): item for item in CandidateField.objects.filter(candidate_set_id=candidate_id)}
    confirmation_ids = [item["confirmation_id"] for item in record["fields"]]
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        confirmations = list(CandidateConfirmation.objects.filter(id__in=confirmation_ids).values(
            "id", "candidate_field_id", "confirmed_by_id", "decision", "confirmed_at"))
        intents = list(WriteIntent.objects.filter(write_request__resource_id=record_id, actor_id=session["principal_id"],
            operation_id="record_create").values("id", "target_key", "status", "generation", "write_request_id", "request_id"))
        writes = list(WriteRequest.objects.filter(resource_id=record_id, actor_id=session["principal_id"],
            action="record_create").values("id", "target_key", "status", "resource_id", "request_id"))
        versions = list(RecordVersion.objects.filter(id=expected_version_id, record_id=record_id,
            patient_id=patient_id).values("id", "version", "author_id", "record_id"))
    confirmation_by_id = {str(item["id"]): item for item in confirmations}
    saved_field_by_confirmation = {item["confirmation_id"]: item for item in evidence["saved_reread"]["fields"]}
    if len(confirmations) != len(confirmation_ids) or len(intents) != 1 or len(writes) != 1 or len(versions) != 1:
        raise RuntimeError("只读数据库复核没有得到唯一确认、写入意图、写入请求及版本")
    if intents[0]["status"] != "committed" or writes[0]["status"] != "committed" or intents[0]["write_request_id"] != writes[0]["id"]:
        raise RuntimeError("数据库写入意图与写入请求没有提交或互相绑定")
    if intents[0]["target_key"] != f"/api/patients/{patient_id}/records" or writes[0]["target_key"] != intents[0]["target_key"]:
        raise RuntimeError("数据库写入目标路径不匹配")
    if str(versions[0]["author_id"]) != session["principal_id"] or versions[0]["version"] != 1:
        raise RuntimeError("数据库记录版本的模拟主体或版本序号不匹配")
    for field in record["fields"]:
        confirmation = confirmation_by_id[field["confirmation_id"]]
        candidate_field = field_by_id[str(confirmation["candidate_field_id"])]
        saved_field = saved_field_by_confirmation[field["confirmation_id"]]
        reread_values = {"name": field["original_name"], "comparator": field["qualifier"],
            "value": field["raw_numeric"], "unit": field["raw_unit"],
            "medical_time": "unknown" if field["sample_time_origin"] == "unknown" else None,
            "source": field["source_anchor"]["original_text"]}
        if (str(confirmation["confirmed_by_id"]) != session["principal_id"] or confirmation["decision"] != "accepted"
                or candidate_field.field_path != saved_field["field_path"] or reread_values != saved_field["values"]):
            raise RuntimeError("数据库确认主体、决定或候选字段路径与 API 记录不匹配")
        if field["source_anchor"]["file_id"] != evidence["candidate"]["source_file_id"]:
            raise RuntimeError("保存重读字段出处没有绑定同一原件")
    observed_at = datetime.now(timezone.utc).isoformat()
    evidence["api"].update({"verification_candidate_read_status": candidate_status,
        "verification_record_read_status": record_status, "candidate_after_status": candidate_status,
        "record_read_status": record_status})
    evidence["adoption_status"] = "SIMULATION_CONFIRMED"
    evidence["saved_reread_status"] = "SIMULATION_SAVED_AND_READ"
    evidence["candidate_after"] = candidate
    evidence["saved_reread"]["api_view"] = record
    evidence["saved_reread"]["read_at"] = observed_at
    evidence["verification"] = {"status": "SIMULATION_READ_ONLY_REVERIFIED", "observed_at": observed_at,
        "candidate_set_id": candidate_id, "record_id": record_id, "record_version_id": expected_version_id,
        "write_intent_id": str(intents[0]["id"]), "write_request_id": str(writes[0]["id"]),
        "record_version": str(versions[0]["version"]), "confirmation_ids": confirmation_ids,
        "candidate_api_read_status": candidate_status, "record_api_read_status": record_status}
    EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"status": "SIMULATION_READ_ONLY_REVERIFIED", "candidate_set_id": candidate_id,
        "record_id": record_id, "record_version_id": expected_version_id, "write_intent_id": str(intents[0]["id"]),
        "write_request_id": str(writes[0]["id"]), "field_count": len(record["fields"]), "evidence": str(EVIDENCE)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
