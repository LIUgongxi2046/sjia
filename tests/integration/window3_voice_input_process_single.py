import argparse
import hashlib
import json
import os
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.simulation")
django.setup()

from django.db import connection
from files.models import FileObject
from files.purpose import checked_bytes
from recognition.models import VoiceInput
from sources.models import DataUseAuthorization
from tasks.models import OutboxEvent, Task

from window3_voice_input_adoption_roundtrip import api_session, get_json, request_json


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / ".simulation-work/t55-voice-fixture.json"
OUTPUT_PATH = ROOT / ".planning-work/parallel/window3/T55-voice-created-task.json"
PROCESS_RESPONSE_PATH = ROOT / ".planning-work/parallel/window3/T55-voice-process-api-response.json"
API_BASE = "http://127.0.0.1:8001"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice-input-id", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--task-output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--process-response-output", type=Path, default=PROCESS_RESPONSE_PATH)
    args = parser.parse_args()
    if args.task_output.exists():
        prior = json.loads(args.task_output.read_text(encoding="utf-8"))
        if (prior.get("voice_input_id") != args.voice_input_id
                or prior.get("request_id") != args.request_id or prior.get("task_id")):
            raise RuntimeError("模拟 VoiceInput 有不匹配或已完成的 Task 运行报告，拒绝重新提交")
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if fixture.get("mode") != "SIMULATION_TEXT_ONLY" or fixture.get("purpose_copy_status") != "approved":
        raise RuntimeError("模拟文字或获批用途副本状态不准确")
    transcript = fixture["confirmed_text"]
    transcript_sha256 = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
    if transcript_sha256 != fixture["transcript_sha256"]:
        raise RuntimeError("模拟确认文字摘要不匹配")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_schema(), inet_server_port()")
        database_name, schema_name, port = cursor.fetchone()
    if (database_name, schema_name, port) != ("sjia_simulation", "sjia", 5433):
        raise RuntimeError("模拟数据库身份与授权目标不一致")

    patient_id = fixture["patient_id"]
    original = FileObject.objects.get(id=fixture["original_file_id"], patient_id=patient_id)
    copy = FileObject.objects.get(id=fixture["derived_file_id"], patient_id=patient_id)
    authorization = DataUseAuthorization.objects.get(id=fixture["authorization_id"], patient_id=patient_id)
    if (original.media_type != "text/plain" or checked_bytes(original).decode("utf-8") != transcript
            or original.checksum != transcript_sha256 or authorization.purpose != "external_recognition"
            or authorization.revoked_at is not None or copy.status != "available"):
        raise RuntimeError("模拟原件、用途授权或派生副本未通过数据库复核")

    client, csrf, session = api_session(API_BASE, fixture["family_username"], "SJIA_SIMULATION_PASSWORD")
    if session.get("audience") != "family" or session.get("principal_id") != fixture["owner_id"]:
        raise RuntimeError("API 会话没有绑定准确的模拟家庭主体")
    options_status, options = get_json(client, API_BASE,
        f"/api/v2/patients/{patient_id}/voice-input-options")
    if options_status != 200 or options.get("patient_id") != patient_id:
        raise RuntimeError(f"VoiceInput options API 无法读取准确模拟患儿：HTTP {options_status}；reason_code={options.get('reason_code')}")
    original_options = [item for item in options["original_options"]
        if item.get("original_file_id") == fixture["original_file_id"]]
    if (len(original_options) != 1 or original_options[0].get("state") != "available"
            or original_options[0].get("purpose_copy_id") != fixture["purpose_copy_id"]
            or original_options[0].get("confirmed_text") != transcript
            or options.get("provider_config_id") != fixture["provider_config_id"]
            or options.get("field_config_id") != fixture["field_config_id"]):
        raise RuntimeError("VoiceInput options 没有返回批准的准确模拟原件和配置")
    source_options = [item for item in options["source_options"]
        if item.get("source_revision_id") == fixture["source_revision_id"]
        and item.get("external_authorization_id") == fixture["authorization_id"]
        and item.get("family_authorization_id") == fixture["family_authorization_id"]]
    if len(source_options) != 1:
        raise RuntimeError("VoiceInput options 未返回批准的双用途来源授权")

    request_id = args.request_id
    voice_id = args.voice_input_id
    database_voice = VoiceInput.objects.get(id=voice_id, patient_id=patient_id, actor_id=fixture["owner_id"],
        request_id=request_id, original_file_id=fixture["original_file_id"], transcript_sha256=transcript_sha256,
        input_mode="SIMULATION_TEXT_ONLY")
    if database_voice.status not in ("ready", "running"):
        raise RuntimeError(f"准确模拟 VoiceInput 当前状态不可处理：{database_voice.status}")
    read_status, voice_before = get_json(client, API_BASE,
        f"/api/v2/patients/{patient_id}/voice-inputs/{voice_id}")
    if (read_status != 200 or voice_before.get("id") != voice_id
            or voice_before.get("patient_id") != patient_id or voice_before.get("request_id") != request_id
            or voice_before.get("status") not in ("ready", "running") or voice_before.get("transcript") != transcript
            or voice_before.get("transcript_sha256") != transcript_sha256
            or voice_before.get("original_file_id") != fixture["original_file_id"]
            or voice_before.get("input_mode") != "SIMULATION_TEXT_ONLY"):
        raise RuntimeError("VoiceInput API 创建后重新读取内容不一致")

    preflight = {"data_mode": "SIMULATION", "evidence_scope": "SIMULATION_TEXT_ONLY",
        "status": "VOICE_INPUT_CREATED_AND_READ_PROCESS_NOT_SUBMITTED", "patient_id": patient_id,
        "principal_id": session["principal_id"], "original_file_id": fixture["original_file_id"],
        "purpose_copy_id": fixture["purpose_copy_id"], "authorization_id": fixture["authorization_id"],
        "request_id": request_id, "voice_input_id": voice_id, "database_status": database_voice.status,
        "api_read_status": read_status, "transcript_sha256": transcript_sha256,
        "task_id": None, "event_id": None}
    args.task_output.parent.mkdir(parents=True, exist_ok=True)
    args.task_output.write_text(json.dumps(preflight, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if json.loads(args.task_output.read_text(encoding="utf-8")) != preflight:
        raise RuntimeError("VoiceInput 与请求标识预处理记录保存重读不一致")

    if voice_before["status"] == "ready":
        process_status, process_view = request_json(client, API_BASE, "POST",
            f"/api/v2/patients/{patient_id}/voice-inputs/{voice_id}/process",
            {"expected_status_version": voice_before["status_version"],
                "purpose_copy_id": fixture["purpose_copy_id"],
                "provider_config_id": fixture["provider_config_id"]}, csrf)
        process_evidence = {"data_mode": "SIMULATION", "evidence_scope": "SIMULATION_TEXT_ONLY",
            "voice_input_id": voice_id, "request_id": request_id,
            "http_status": process_status, "response_body": process_view}
        args.process_response_output.parent.mkdir(parents=True, exist_ok=True)
        args.process_response_output.write_text(json.dumps(process_evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        if json.loads(args.process_response_output.read_text(encoding="utf-8")) != process_evidence:
            raise RuntimeError("VoiceInput process API 响应证据保存重读不一致")
        if process_status != 202 or process_view.get("id") != voice_id:
            raise RuntimeError(f"VoiceInput process API 没有接受准确对象：HTTP {process_status}；"
                f"code={process_view.get('code')}；message={process_view.get('message')}；"
                f"retryable={process_view.get('retryable')}")
    else:
        process_status = "NOT_RESUBMITTED_ALREADY_RUNNING"
        process_view = voice_before
    task = Task.objects.get(id=process_view.get("task_id"), actor_id=fixture["owner_id"], patient_id=patient_id)
    if (task.schema_version != "voice-input-1" or task.status not in ("accepted", "queued", "pending", "running")
            or task.input_refs.get("voice_input_id") != voice_id
            or task.input_refs.get("transcript_sha256") != transcript_sha256
            or task.input_refs.get("original_file_id") != fixture["original_file_id"]
            or task.input_refs.get("authorization_id") != fixture["authorization_id"]):
        raise RuntimeError("VoiceInput process 没有创建绑定准确原件和授权的单项 Task")
    events = list(OutboxEvent.objects.filter(task=task, event_type="voice_input.requested",
        schema_version="voice-input-1").order_by("occurred_at", "id"))
    if len(events) != 1 or str(events[0].aggregate_id) != voice_id:
        raise RuntimeError("VoiceInput process 没有创建唯一对应的 Outbox Event")
    api_read_status, voice_after_process = get_json(client, API_BASE,
        f"/api/v2/patients/{patient_id}/voice-inputs/{voice_id}")
    if (api_read_status != 200 or voice_after_process.get("task_id") != str(task.id)
            or voice_after_process.get("task_status") != task.status):
        raise RuntimeError("process 后 VoiceInput API 与已保存 Task 状态不一致")

    report = {"data_mode": "SIMULATION", "evidence_scope": "SIMULATION_TEXT_ONLY",
        "status": "VOICE_TASK_AND_EVENT_CREATED_READ_AND_VERIFIED", "api_base": API_BASE,
        "database": {"name": database_name, "schema": schema_name, "port": port},
        "patient_id": patient_id, "principal_id": session["principal_id"],
        "original_file_id": fixture["original_file_id"], "purpose_copy_id": fixture["purpose_copy_id"],
        "authorization_id": fixture["authorization_id"], "request_id": request_id,
        "voice_input_id": voice_id, "task_id": str(task.id), "task_type": task.task_type,
        "task_status": task.status, "event_id": str(events[0].id), "event_type": events[0].event_type,
        "input_sha256": task.input_sha256, "transcript_sha256": transcript_sha256,
        "api_create_status": 201, "api_read_after_create_status": read_status,
        "api_process_status": process_status, "api_read_after_process_status": api_read_status,
        "queue_consumption": "NOT_RUN_YET_EXACT_EVENT_ONLY"}
    args.task_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if json.loads(args.task_output.read_text(encoding="utf-8")) != report:
        raise RuntimeError("单项模拟 VoiceInput 创建记录保存重读不一致")
    print(json.dumps({key: report[key] for key in ("status", "voice_input_id", "task_id", "event_id", "input_sha256")},
        ensure_ascii=False))


if __name__ == "__main__":
    main()
