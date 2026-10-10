import json
import os
import uuid
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.simulation")
django.setup()

from django.db import connection, transaction
from files.models import FileObject
from identity.models import PrincipalCredential
from recognition.models import VoiceInput
from records.models import RecordVersion
from tasks.models import Task

from window3_voice_input_adoption_roundtrip import api_session, get_json, post_json


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / ".simulation-work/t55-voice-fixture.json"
OUTPUT_PATH = ROOT / ".planning-work/parallel/window3/T55-voice-cancel-evidence.json"
API_BASE = "http://127.0.0.1:8001"


def save_evidence(evidence):
    OUTPUT_PATH.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reread = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    if reread != evidence:
        raise RuntimeError("取消探针保存后重新读取不一致")


def main():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if fixture.get("mode") != "SIMULATION_TEXT_ONLY" or fixture.get("purpose_copy_status") != "approved":
        raise RuntimeError("取消探针只能使用明确标记的模拟原件和已批准用途副本")
    if OUTPUT_PATH.exists():
        raise RuntimeError("本次取消探针已有结果，拒绝重复创建 VoiceInput")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_schema(), inet_server_port(), current_user")
        db_name, db_schema, db_port, db_user = cursor.fetchone()
    database = {"name": db_name, "schema": db_schema, "port": db_port, "user": db_user}
    if database != {"name": "sjia_simulation", "schema": "sjia", "port": 5433, "user": "sjia"}:
        raise RuntimeError("取消探针数据库身份不是 sjia_simulation")

    client, csrf, session = api_session(API_BASE, fixture["family_username"], "SJIA_SIMULATION_PASSWORD")
    if session.get("principal_id") != fixture["owner_id"] or session.get("audience") != "family":
        raise RuntimeError("取消探针没有绑定模拟家庭主体")
    original = FileObject.objects.get(id=fixture["original_file_id"], patient_id=fixture["patient_id"])
    if original.media_type != "text/plain" or original.checksum != fixture["transcript_sha256"]:
        raise RuntimeError("取消探针原件与本窗口模拟文字摘要不一致")
    prior_voice_confirmed_records = RecordVersion.objects.filter(patient_id=fixture["patient_id"],
        capture_method="voice_confirmed").count()
    request_id = str(uuid.uuid4())
    create_payload = {
        "request_id": request_id,
        "authorization_id": fixture["authorization_id"],
        "field_config_id": fixture["field_config_id"],
        "confirmed_text": fixture["confirmed_text"],
        "transcript_confirmed": True,
        "input_mode": "SIMULATION_TEXT_ONLY",
        "transcription_evidence_id": None,
        "original_file_id": fixture["original_file_id"],
    }
    create_path = f"/api/v2/patients/{fixture['patient_id']}/voice-inputs"
    create_status, created = post_json(client, API_BASE, create_path, create_payload, csrf)
    evidence = {"data_mode": "SIMULATION", "evidence_scope": "SIMULATION_TEXT_ONLY",
        "database": database, "patient_id": fixture["patient_id"], "principal_id": session["principal_id"],
        "external_authorization_id": fixture["authorization_id"], "request_id": request_id,
        "create": {"http_status": create_status, "response_body": created}}
    save_evidence(evidence)
    if create_status != 201:
        raise RuntimeError(f"取消探针 VoiceInput 创建失败：HTTP {create_status}；reason_code={created.get('code')}")
    voice_id = created["id"]
    if (created.get("status") != "ready" or created.get("task_id") is not None
            or created.get("input_mode") != "SIMULATION_TEXT_ONLY" or created.get("patient_id") != fixture["patient_id"]):
        raise RuntimeError("取消探针对象创建后状态或患儿范围不符合预期")
    cancel_path = f"/api/v2/patients/{fixture['patient_id']}/voice-inputs/{voice_id}/cancel"
    cancel_status, canceled = post_json(client, API_BASE, cancel_path,
        {"expected_status_version": created["status_version"]}, csrf)
    evidence["cancel"] = {"http_status": cancel_status, "response_body": canceled}
    save_evidence(evidence)
    if cancel_status != 200 or canceled.get("status") != "cancelled":
        raise RuntimeError(f"ready VoiceInput 取消没有完成：HTTP {cancel_status}；reason_code={canceled.get('code')}")
    reread_status, reread = get_json(client, API_BASE, f"/api/v2/patients/{fixture['patient_id']}/voice-inputs/{voice_id}")
    if reread_status != 200 or reread.get("status") != "cancelled" or reread.get("task_id") is not None:
        raise RuntimeError("取消后 VoiceInput API 重新读取不一致")
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        saved = VoiceInput.objects.get(id=voice_id, actor_id=session["principal_id"], patient_id=fixture["patient_id"])
        task_rows = list(Task.objects.filter(input_refs__voice_input_id=voice_id).values_list("id", flat=True))
        record_count = RecordVersion.objects.filter(patient_id=fixture["patient_id"],
            capture_method="voice_confirmed").count()
    if (saved.status != "cancelled" or saved.task_id is not None or task_rows
            or record_count != prior_voice_confirmed_records):
        raise RuntimeError("数据库取消后仍存在关联 Task 或非取消状态")
    evidence["readback"] = {"http_status": reread_status, "response_body": reread,
        "database_status": saved.status, "database_task_id": str(saved.task_id) if saved.task_id else None,
        "task_rows_for_voice_input": task_rows, "patient_voice_confirmed_record_count": record_count}
    evidence["status"] = "SIMULATION_READY_VOICE_CANCELLED_AND_READ"
    save_evidence(evidence)
    print(json.dumps({"status": evidence["status"], "voice_input_id": voice_id,
        "http_statuses": {"create": create_status, "cancel": cancel_status, "read": reread_status},
        "task_rows": len(task_rows), "evidence": str(OUTPUT_PATH)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
