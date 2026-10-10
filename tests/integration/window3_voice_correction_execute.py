import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.simulation")
django.setup()

from django.db import connection, transaction
from django.forms.models import model_to_dict
from django.utils import timezone
from archives.models import PurposeCopy
from files.models import FileObject
from files.purpose import checked_bytes
from recognition.models import VoiceInput
from recognition.voice_input_jobs import execute_voice_input, record_voice_failure
from records.models import RecordVersion
from sources.models import DataUseAuthorization
from tasks.models import ConsumerReceipt, OutboxEvent, Task, TaskAttempt

from window3_voice_input_adoption_roundtrip import api_session, get_json, post_json


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / ".simulation-work/t55-voice-fixture.json"
OUTPUT_PATH = ROOT / ".planning-work/parallel/window3/T55-correction-voice-execution.json"
API_BASE = "http://127.0.0.1:8001"
CORRECTION_RECORD_ID = "7a673e4a-7707-4474-a749-4880e32ebb89"


def save_evidence(evidence):
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8")
    reread = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    if reread != json.loads(json.dumps(evidence, ensure_ascii=False, default=str)):
        raise RuntimeError("单 Event 运行证据保存后重新读取不一致")


def main():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if fixture.get("mode") != "SIMULATION_TEXT_ONLY" or fixture.get("purpose_copy_status") != "approved":
        raise RuntimeError("语音更正执行只接受明确标记的模拟文字原件和已批准用途副本")
    if OUTPUT_PATH.exists():
        raise RuntimeError("语音更正单 Event 运行证据已存在，拒绝重复创建或执行")
    transcript = fixture["confirmed_text"]
    transcript_sha256 = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
    if transcript_sha256 != fixture["transcript_sha256"]:
        raise RuntimeError("模拟原文摘要与 fixture 不一致")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_schema(), inet_server_port(), current_user")
        db_name, db_schema, db_port, db_user = cursor.fetchone()
    database = {"name": db_name, "schema": db_schema, "port": db_port, "user": db_user}
    if database != {"name": "sjia_simulation", "schema": "sjia", "port": 5433, "user": "sjia"}:
        raise RuntimeError("语音更正执行连接的数据库不是 sjia_simulation")

    client, csrf, session = api_session(API_BASE, fixture["family_username"], "SJIA_SIMULATION_PASSWORD")
    if session.get("audience") != "family" or session.get("principal_id") != fixture["owner_id"]:
        raise RuntimeError("API 会话没有绑定指定模拟家庭主体")
    original = FileObject.objects.get(id=fixture["original_file_id"], patient_id=fixture["patient_id"])
    derived = FileObject.objects.get(id=fixture["derived_file_id"], patient_id=fixture["patient_id"])
    purpose_copy = PurposeCopy.objects.get(id=fixture["purpose_copy_id"], patient_id=fixture["patient_id"])
    authorization = DataUseAuthorization.objects.get(id=fixture["authorization_id"], patient_id=fixture["patient_id"])
    if (original.media_type != "text/plain" or checked_bytes(original).decode("utf-8") != transcript
            or original.checksum != transcript_sha256 or derived.status != "available"
            or purpose_copy.original_file_id != original.id or purpose_copy.derived_file_id != derived.id
            or purpose_copy.status != "approved" or str(purpose_copy.version) != fixture["purpose_copy_version"]
            or authorization.revoked_at is not None):
        raise RuntimeError("模拟原件、用途副本或授权与当前 fixture 不一致")
    options_status, options = get_json(client, API_BASE,
        f"/api/v2/patients/{fixture['patient_id']}/voice-input-options")
    originals = [item for item in options.get("original_options", [])
        if item.get("original_file_id") == fixture["original_file_id"]]
    sources = [item for item in options.get("source_options", [])
        if item.get("source_revision_id") == fixture["source_revision_id"]
        and item.get("external_authorization_id") == fixture["authorization_id"]
        and item.get("family_authorization_id") == fixture["family_authorization_id"]]
    if (options_status != 200 or options.get("patient_id") != fixture["patient_id"]
            or len(originals) != 1 or originals[0].get("purpose_state") != "approved"
            or originals[0].get("purpose_copy_id") != fixture["purpose_copy_id"] or len(sources) != 1
            or options.get("field_config_id") != fixture["field_config_id"]
            or options.get("provider_config_id") != fixture["provider_config_id"]):
        raise RuntimeError("VoiceInput options 未返回当前批准的原件、授权或模拟配置")

    before_record_status, before_record = get_json(client, API_BASE,
        f"/api/records/{CORRECTION_RECORD_ID}")
    if (before_record_status != 200 or before_record.get("patient_id") != fixture["patient_id"]
            or before_record.get("kind") != "observation" or before_record.get("version") != 1):
        raise RuntimeError("模拟更正目标 Record 不符合单次版本回归的前置条件")
    before_record_count = RecordVersion.objects.filter(patient_id=fixture["patient_id"],
        capture_method="voice_confirmed").count()

    request_id = str(uuid.uuid4())
    create_payload = {"request_id": request_id, "authorization_id": fixture["authorization_id"],
        "field_config_id": fixture["field_config_id"], "confirmed_text": transcript,
        "transcript_confirmed": True, "input_mode": "SIMULATION_TEXT_ONLY",
        "transcription_evidence_id": None, "original_file_id": fixture["original_file_id"]}
    create_status, created = post_json(client, API_BASE,
        f"/api/v2/patients/{fixture['patient_id']}/voice-inputs", create_payload, csrf)
    if create_status != 201 or created.get("status") != "ready" or created.get("task_id") is not None:
        raise RuntimeError(f"模拟 VoiceInput 创建失败：HTTP {create_status}；{created.get('code')}")
    voice_id = str(created["id"])
    voice_status, voice_before = get_json(client, API_BASE,
        f"/api/v2/patients/{fixture['patient_id']}/voice-inputs/{voice_id}")
    if (voice_status != 200 or voice_before.get("id") != voice_id
            or voice_before.get("request_id") != request_id or voice_before.get("transcript") != transcript
            or voice_before.get("transcript_sha256") != transcript_sha256
            or voice_before.get("input_mode") != "SIMULATION_TEXT_ONLY"):
        raise RuntimeError("模拟 VoiceInput 创建后 API 重读不一致")
    created_evidence = {"data_mode": "SIMULATION", "evidence_scope": "SIMULATION_TEXT_ONLY",
        "database": database, "patient_id": fixture["patient_id"], "principal_id": session["principal_id"],
        "record_id": CORRECTION_RECORD_ID, "record_version_before": before_record["version"],
        "record_version_id_before": before_record["record_version_id"],
        "voice_input_id": voice_id, "request_id": request_id,
        "original_file_id": fixture["original_file_id"], "original_sha256": transcript_sha256,
        "purpose_copy_id": fixture["purpose_copy_id"], "field_config_id": fixture["field_config_id"],
        "provider_config_id": fixture["provider_config_id"], "api_create_status": create_status,
        "api_read_after_create_status": voice_status, "status": "SIMULATION_VOICE_INPUT_CREATED_AND_READ",
        "model_execution": "NOT_RUN", "provider_call_count": 0}
    save_evidence(created_evidence)

    process_status, process_view = post_json(client, API_BASE,
        f"/api/v2/patients/{fixture['patient_id']}/voice-inputs/{voice_id}/process",
        {"expected_status_version": voice_before["status_version"],
            "purpose_copy_id": fixture["purpose_copy_id"],
            "provider_config_id": fixture["provider_config_id"]}, csrf)
    if process_status != 202 or process_view.get("id") != voice_id or not process_view.get("task_id"):
        raise RuntimeError(f"模拟 VoiceInput process API 失败：HTTP {process_status}；{process_view.get('code')}")
    task = Task.objects.get(id=process_view["task_id"], patient_id=fixture["patient_id"],
        actor_id=session["principal_id"], schema_version="voice-input-1")
    if task.input_refs.get("voice_input_id") != voice_id or task.input_sha256 is None:
        raise RuntimeError("process API 创建的 Task 未绑定准确 VoiceInput")
    events = list(OutboxEvent.objects.filter(task=task, event_type="voice_input.requested",
        schema_version="voice-input-1").order_by("occurred_at", "id"))
    if len(events) != 1 or str(events[0].aggregate_id) != voice_id:
        raise RuntimeError("process API 没有创建唯一准确的语音 Event")
    event = events[0]
    task_submitted_evidence = {**created_evidence, "status": "SIMULATION_TASK_EVENT_CREATED_AND_READ",
        "api_process_status": process_status, "task_id": str(task.id), "task_type": task.task_type,
        "task_status_before_execution": task.status, "event_id": str(event.id),
        "event_type": event.event_type, "input_sha256": task.input_sha256,
        "queue_consumption": "EXACT_SINGLE_EVENT_DIRECT_SERVICE_CALL"}
    save_evidence(task_submitted_evidence)

    owner = f"window3-correction-{uuid.uuid4()}"
    try:
        execute_voice_input(event, owner)
    finally:
        failure = sys.exception()
        if failure is not None:
            record_voice_failure(event, owner, getattr(failure, "reason_code", str(failure)))

    task.refresh_from_db()
    event.refresh_from_db()
    voice_row = VoiceInput.objects.get(id=voice_id, patient_id=fixture["patient_id"], actor_id=session["principal_id"])
    receipts = list(ConsumerReceipt.objects.filter(consumer="voice_input", event=event))
    attempts = list(TaskAttempt.objects.filter(task=task).order_by("attempt_number"))
    if len(receipts) != 1:
        failed_evidence = {**task_submitted_evidence, "status": "SIMULATION_EVENT_NOT_CONFIRMED",
            "task_status_after_execution": task.status, "voice_input_status_after_execution": voice_row.status,
            "consumer_receipt_count": len(receipts), "attempts": [model_to_dict(item) for item in attempts],
            "provider_call_count": sum(1 for item in attempts if item.provider_request_id)}
        save_evidence(failed_evidence)
        raise RuntimeError("准确单 Event 执行后没有唯一 ConsumerReceipt；此脚本不会重试")
    if (len(attempts) != 1 or not attempts[0].provider_request_id or attempts[0].status != "completed"
            or voice_row.status != "awaiting_confirmation" or voice_row.candidates is None
            or voice_row.raw_response_file_id is None):
        raise RuntimeError("模拟语音 Event 没有保存完整的实际 Provider 候选结果")
    api_read_status, voice_after = get_json(client, API_BASE,
        f"/api/v2/patients/{fixture['patient_id']}/voice-inputs/{voice_id}")
    if (api_read_status != 200 or voice_after.get("status") != "awaiting_confirmation"
            or voice_after.get("id") != voice_id or voice_after.get("task_id") != str(task.id)
            or voice_after.get("candidates") != voice_row.candidates):
        raise RuntimeError("实际候选保存后 VoiceInput API 重新读取不一致")
    attempt = attempts[0]
    usage_evidence = attempt.usage_evidence or {}
    if usage_evidence.get("phase") != "provider_finished" or usage_evidence.get("provider_called") is not True:
        raise RuntimeError("TaskAttempt 没有实际 Provider 调用和 usage 证据")
    after_record_count = RecordVersion.objects.filter(patient_id=fixture["patient_id"],
        capture_method="voice_confirmed").count()
    if after_record_count != before_record_count:
        raise RuntimeError("VoiceInput 候选执行阶段改变了 RecordVersion 数量")
    report = {**task_submitted_evidence, "status": "SIMULATION_ACTUAL_PROVIDER_RESULT_SAVED_AND_READ",
        "model_execution": "EXECUTED_AND_SAVED",
        "task_status_after_execution": task.status, "voice_input_status_after_execution": voice_row.status,
        "voice_input_status_version": voice_row.status_version, "voice_input_candidates": voice_row.candidates,
        "raw_response_file_id": str(voice_row.raw_response_file_id), "provider_request_id": attempt.provider_request_id,
        "provider_model": usage_evidence.get("model"), "usage": usage_evidence.get("usage"),
        "provider_duration_ms": usage_evidence.get("provider_duration_ms"),
        "attempt_started_at": attempt.started_at.isoformat() if attempt.started_at else None,
        "attempt_finished_at": attempt.finished_at.isoformat() if attempt.finished_at else None,
        "attempt_elapsed_seconds": (attempt.finished_at - attempt.started_at).total_seconds()
            if attempt.started_at and attempt.finished_at else None,
        "cost_cny": usage_evidence.get("actual_cost"), "cost_status": usage_evidence.get("cost_status"),
        "pricing_evidence_ref": usage_evidence.get("pricing_evidence_ref"),
        "consumer_receipt": model_to_dict(receipts[0]), "attempt": model_to_dict(attempt),
        "api_read_after_execution_status": api_read_status, "api_read_after_execution": voice_after,
        "record_version_count_before": before_record_count, "record_version_count_after_model": after_record_count,
        "provider_call_count": 1, "queue_consumption": "EXACT_SINGLE_EVENT_DIRECT_SERVICE_CALL",
        "audio_transcription": "NOT_RUN", "clinical_review": "NOT_RUN", "actual_invoice": "NOT_RUN"}
    save_evidence(report)
    print(json.dumps({"status": report["status"], "voice_input_id": voice_id, "task_id": str(task.id),
        "event_id": str(event.id), "provider_request_id": attempt.provider_request_id,
        "provider_call_count": report["provider_call_count"], "cost_cny": report["cost_cny"],
        "evidence": str(OUTPUT_PATH)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
