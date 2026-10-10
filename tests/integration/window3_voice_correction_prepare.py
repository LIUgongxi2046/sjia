import hashlib
import json
import os
import uuid
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.simulation")
django.setup()

from django.db import connection, transaction
from archives.models import PurposeCopy
from files.models import FileObject
from files.purpose import checked_bytes
from recognition.models import VoiceInput

from window3_voice_input_adoption_roundtrip import api_session, get_json, post_json


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / ".simulation-work/t55-voice-fixture.json"
OUTPUT_PATH = ROOT / ".planning-work/parallel/window3/T55-correction-voice-prepared.json"
API_BASE = "http://127.0.0.1:8001"


def save_evidence(evidence):
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if json.loads(OUTPUT_PATH.read_text(encoding="utf-8")) != evidence:
        raise RuntimeError("语音更正模拟准备证据保存后重新读取不一致")


def main():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if fixture.get("mode") != "SIMULATION_TEXT_ONLY" or fixture.get("purpose_copy_status") != "approved":
        raise RuntimeError("语音更正准备只接受明确标记的模拟文字原件和已批准用途副本")
    if OUTPUT_PATH.exists():
        raise RuntimeError("语音更正准备已有记录，拒绝重复创建 VoiceInput")
    transcript = fixture["confirmed_text"]
    digest = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
    if digest != fixture["transcript_sha256"]:
        raise RuntimeError("模拟原文摘要与 fixture 不一致")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_schema(), inet_server_port(), current_user")
        db_name, db_schema, db_port, db_user = cursor.fetchone()
    database = {"name": db_name, "schema": db_schema, "port": db_port, "user": db_user}
    if database != {"name": "sjia_simulation", "schema": "sjia", "port": 5433, "user": "sjia"}:
        raise RuntimeError("语音更正准备连接的数据库不是 sjia_simulation")

    client, csrf, session = api_session(API_BASE, fixture["family_username"], "SJIA_SIMULATION_PASSWORD")
    if session.get("audience") != "family" or session.get("principal_id") != fixture["owner_id"]:
        raise RuntimeError("API 会话没有绑定指定模拟家庭主体")
    original = FileObject.objects.get(id=fixture["original_file_id"], patient_id=fixture["patient_id"])
    copy = FileObject.objects.get(id=fixture["derived_file_id"], patient_id=fixture["patient_id"])
    purpose_copy = PurposeCopy.objects.get(id=fixture["purpose_copy_id"], patient_id=fixture["patient_id"])
    if (original.media_type != "text/plain" or checked_bytes(original).decode("utf-8") != transcript
            or original.checksum != digest or copy.status != "available"
            or purpose_copy.derived_file_id != copy.id or purpose_copy.original_file_id != original.id
            or purpose_copy.status != "approved" or str(purpose_copy.version) != fixture["purpose_copy_version"]):
        raise RuntimeError("原件或已审核用途副本与模拟更正 fixture 不一致")
    options_status, options = get_json(client, API_BASE,
        f"/api/v2/patients/{fixture['patient_id']}/voice-input-options")
    originals = [item for item in options.get("original_options", [])
        if item.get("original_file_id") == fixture["original_file_id"]]
    if (options_status != 200 or options.get("patient_id") != fixture["patient_id"]
            or len(originals) != 1 or originals[0].get("state") != "available"
            or originals[0].get("purpose_state") != "approved"
            or originals[0].get("purpose_copy_id") != fixture["purpose_copy_id"]
            or options.get("field_config_id") != fixture["field_config_id"]
            or options.get("provider_config_id") != fixture["provider_config_id"]):
        raise RuntimeError("VoiceInput options API 没有返回准确获批的原件与模拟配置")

    request_id = str(uuid.uuid4())
    payload = {"request_id": request_id, "authorization_id": fixture["authorization_id"],
        "field_config_id": fixture["field_config_id"], "confirmed_text": transcript,
        "transcript_confirmed": True, "input_mode": "SIMULATION_TEXT_ONLY",
        "transcription_evidence_id": None, "original_file_id": fixture["original_file_id"]}
    create_status, created = post_json(client, API_BASE,
        f"/api/v2/patients/{fixture['patient_id']}/voice-inputs", payload, csrf)
    evidence = {"data_mode": "SIMULATION", "evidence_scope": "SIMULATION_TEXT_ONLY",
        "status": "VOICE_INPUT_CREATE_PENDING_READBACK", "database": database,
        "patient_id": fixture["patient_id"], "principal_id": session["principal_id"],
        "original_file_id": fixture["original_file_id"], "original_sha256": digest,
        "purpose_copy_id": fixture["purpose_copy_id"], "field_config_id": fixture["field_config_id"],
        "provider_config_id": fixture["provider_config_id"], "request_id": request_id,
        "create_http_status": create_status, "create_response": created}
    save_evidence(evidence)
    if create_status != 201 or created.get("status") != "ready" or created.get("task_id") is not None:
        raise RuntimeError(f"模拟更正 VoiceInput 创建结果异常：HTTP {create_status}；{created.get('code')}")

    voice_id = created["id"]
    read_status, voice_read = get_json(client, API_BASE,
        f"/api/v2/patients/{fixture['patient_id']}/voice-inputs/{voice_id}")
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        row = VoiceInput.objects.get(id=voice_id, patient_id=fixture["patient_id"],
            actor_id=session["principal_id"], request_id=request_id)
    if (read_status != 200 or voice_read.get("id") != str(voice_id)
            or voice_read.get("request_id") != request_id or voice_read.get("status") != "ready"
            or voice_read.get("transcript") != transcript or voice_read.get("transcript_sha256") != digest
            or voice_read.get("original_file_id") != fixture["original_file_id"]
            or voice_read.get("input_mode") != "SIMULATION_TEXT_ONLY"
            or row.status != "ready" or row.task_id is not None):
        raise RuntimeError("VoiceInput 创建后 API 与数据库重读不一致")
    evidence.update({"status": "SIMULATION_VOICE_INPUT_CREATED_AND_READ", "voice_input_id": str(voice_id),
        "read_http_status": read_status, "read_response": voice_read,
        "database_status": row.status, "database_task_id": None,
        "model_task_submission": "NOT_RUN", "queue_consumption": "NOT_RUN"})
    save_evidence(evidence)
    print(json.dumps({"status": evidence["status"], "voice_input_id": str(voice_id),
        "request_id": request_id, "evidence": str(OUTPUT_PATH)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
