import json
import os
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.simulation")
django.setup()

from django.db import connection
from recognition.models import VoiceInput
from tasks.models import OutboxEvent, Task, TaskAttempt

from window3_voice_input_adoption_roundtrip import api_session, get_json


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / ".simulation-work/t55-voice-fixture.json"
OUTPUT_PATH = ROOT / ".planning-work/parallel/window3/T55-voice-process-diagnosis.json"
API_BASE = "http://127.0.0.1:8001"
VOICE_INPUT_ID = "6a85134c-3bdd-4d87-b161-5455d038bc0a"
REQUEST_ID = "de1136ed-4eab-45d7-98fe-d836f9be78bc"


def main():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_schema(), inet_server_port()")
        database_name, schema_name, port = cursor.fetchone()
    if (database_name, schema_name, port) != ("sjia_simulation", "sjia", 5433):
        raise RuntimeError("模拟数据库身份与授权目标不一致")
    row = VoiceInput.objects.get(id=VOICE_INPUT_ID, request_id=REQUEST_ID,
        patient_id=fixture["patient_id"], actor_id=fixture["owner_id"])
    task = Task.objects.filter(id=row.task_id, patient_id=fixture["patient_id"], actor_id=fixture["owner_id"]).first()
    events = list(OutboxEvent.objects.filter(task=task, event_type="voice_input.requested",
        schema_version="voice-input-1").values("id", "event_type", "aggregate_id", "status", "created_at")) if task else []
    attempts = list(TaskAttempt.objects.filter(task=task).order_by("attempt_number").values(
        "id", "attempt_number", "status", "provider_request_id", "started_at", "finished_at")) if task else []
    client, _, session = api_session(API_BASE, fixture["family_username"], "SJIA_SIMULATION_PASSWORD")
    api_status, api = get_json(client, API_BASE,
        f"/api/v2/patients/{fixture['patient_id']}/voice-inputs/{VOICE_INPUT_ID}")
    report = {"data_mode": "SIMULATION", "evidence_scope": "SIMULATION_TEXT_ONLY",
        "status": "VOICE_PROCESS_API_AND_DATABASE_DIAGNOSED",
        "database": {"name": database_name, "schema": schema_name, "port": port},
        "principal_id": session["principal_id"], "patient_id": fixture["patient_id"],
        "voice_input_id": str(row.id), "request_id": str(row.request_id),
        "voice_status": row.status, "voice_status_version": row.status_version,
        "voice_task_id": str(row.task_id) if row.task_id else None,
        "task": None if task is None else {"id": str(task.id), "type": task.task_type,
            "schema_version": task.schema_version, "status": task.status,
            "input_sha256": task.input_sha256, "input_ref_keys": sorted(task.input_refs.keys()),
            "voice_input_ref": task.input_refs.get("voice_input_id"),
            "authorization_id_ref": task.input_refs.get("authorization_id"),
            "purpose_copy_id_ref": task.input_refs.get("purpose_copy", {}).get("copy_id")},
        "outbox_events": [{**item, "id": str(item["id"]), "aggregate_id": str(item["aggregate_id"]),
            "created_at": item["created_at"].isoformat()} for item in events],
        "task_attempts": [{**item, "id": str(item["id"]),
            "started_at": item["started_at"].isoformat() if item["started_at"] else None,
            "finished_at": item["finished_at"].isoformat() if item["finished_at"] else None} for item in attempts],
        "api_read_status": api_status, "api_status": api.get("status"),
        "api_task_id": api.get("task_id"), "api_task_status": api.get("task_status"),
        "api_response_keys": sorted(api.keys()) if isinstance(api, dict) else None}
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    if json.loads(OUTPUT_PATH.read_text(encoding="utf-8")) != json.loads(
            json.dumps(report, ensure_ascii=False, default=str)):
        raise RuntimeError("语音处理诊断报告保存重读不一致")
    print(json.dumps(report, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
