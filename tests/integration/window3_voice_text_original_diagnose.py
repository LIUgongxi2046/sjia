import hashlib
import json
import os
import urllib.request
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.simulation")
django.setup()

from archives.models import PurposeCopy
from files.models import FileObject
from files.purpose import checked_bytes

from window3_voice_input_adoption_roundtrip import api_session, get_json


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / ".simulation-work/t55-voice-fixture.json"
OUTPUT_PATH = ROOT / ".planning-work/parallel/window3/T55-new-text-original-diagnosis.json"
API_BASE = "http://127.0.0.1:8001"


def main():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    text = fixture["confirmed_text"] + "\nSIMULATION 新增确认文字：2026年10月9日08时00分，补充记录仍需本人核对。"
    expected = text.encode("utf-8")
    checksum = hashlib.sha256(expected).hexdigest()
    original = FileObject.objects.get(patient_id=fixture["patient_id"], checksum=checksum,
        purpose="original", media_type="text/plain")
    client, csrf, session = api_session(API_BASE, fixture["family_username"], "SJIA_SIMULATION_PASSWORD")
    group_status, group = get_json(client, API_BASE, f"/api/uploads/{original.file_group_id}")
    content_request = urllib.request.Request(f"{API_BASE}/api/files/{original.id}/content",
        headers={"Accept": "text/plain,application/json"}, method="GET")
    with client.open(content_request) as response:
        content_status = response.status
        content_type = response.headers.get("Content-Type")
        api_bytes = response.read()
    voice_options_status, voice_options = get_json(client, API_BASE,
        f"/api/v2/patients/{fixture['patient_id']}/voice-input-options")
    option_rows = [item for item in voice_options.get("original_options", [])
        if item.get("original_file_id") == str(original.id)]
    approved_copies = list(PurposeCopy.objects.filter(original_file_id=original.id, patient_id=original.patient_id,
        purpose="external_recognition", status="approved").order_by("-created_at", "-id"))
    option = option_rows[0] if len(option_rows) == 1 else {}
    approved_copy = approved_copies[0] if len(approved_copies) == 1 else None
    group = FileGroup.objects.get(id=original.file_group_id, patient_id=original.patient_id)
    stored_bytes = checked_bytes(original)
    report = {"data_mode": "SIMULATION", "evidence_scope": "SIMULATION_NEW_TEXT_ORIGINAL",
        "status": "SIMULATION_ORIGINAL_OPTIONS_REREAD", "patient_id": fixture["patient_id"],
        "principal_id": session["principal_id"], "file_id": str(original.id),
        "group_id": str(original.file_group_id), "file_checksum": original.checksum,
        "expected_sha256": checksum, "expected_size": len(expected),
        "database_object_sha256": hashlib.sha256(stored_bytes).hexdigest(),
        "database_object_size": len(stored_bytes), "api_http_status": content_status,
        "api_content_type": content_type, "api_sha256": hashlib.sha256(api_bytes).hexdigest(),
        "api_size": len(api_bytes), "api_matches_expected": api_bytes == expected,
        "api_matches_database_object": api_bytes == stored_bytes,
        "file_group_source_revision_id": str(group.source_revision_id),
        "upload_group_http_status": group_status, "upload_group_status": group.get("status"),
        "upload_group_file_count": len(group.get("files", [])),
        "purpose_copy_count_for_original": PurposeCopy.objects.filter(original_file_id=original.id).count(),
        "approved_purpose_copy_count": len(approved_copies),
        "approved_purpose_copy_id": str(approved_copy.id) if approved_copy else None,
        "voice_options_http_status": voice_options_status,
        "voice_options_patient_matches": voice_options.get("patient_id") == fixture["patient_id"],
        "voice_options_source_revision_ids": [item.get("source_revision_id")
            for item in voice_options.get("source_options", [])],
        "voice_options_original_option_ids": [item.get("original_file_id")
            for item in voice_options.get("original_options", [])],
        "voice_options_target_original_count": len(option_rows),
        "voice_options_original_state": option.get("state"),
        "voice_options_purpose_copy_id": option.get("purpose_copy_id"),
        "voice_options_purpose_request_id": option.get("purpose_request_id"),
        "voice_options_purpose_task_id": option.get("purpose_task_id"),
        "voice_options_purpose_state": option.get("purpose_state"),
        "voice_options_purpose_reason_code": option.get("purpose_reason_code"),
        "voice_options_checksum_matches": option.get("checksum") == checksum,
        "voice_options_text_matches": option.get("confirmed_text") == text}
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if json.loads(OUTPUT_PATH.read_text(encoding="utf-8")) != report:
        raise RuntimeError("新文字原件诊断报告保存重读不一致")
    if voice_options_status != 200 or not report["voice_options_patient_matches"] or len(option_rows) != 1:
        raise RuntimeError(f"新文字原件用途状态 API 重读失败：HTTP {voice_options_status}；候选数 {len(option_rows)}")
    required_status_fields = {"purpose_copy_id", "purpose_request_id", "purpose_task_id",
        "purpose_state", "purpose_reason_code", "state"}
    if not required_status_fields <= option.keys():
        raise RuntimeError("语音选项没有返回完整用途处理与审核状态字段")
    if option.get("checksum") != checksum or option.get("confirmed_text") != text:
        raise RuntimeError("新文字原件用途状态与原件正文或摘要不匹配")
    if approved_copy is None:
        raise RuntimeError("独立文字原件没有唯一 approved PurposeCopy，拒绝模糊比对")
    if (option.get("purpose_copy_id") != str(approved_copy.id) or option.get("purpose_state") != "approved"
            or option.get("state") != "available" or option.get("purpose_reason_code") is not None):
        raise RuntimeError("家庭语音选项没有准确反映数据库中的 approved PurposeCopy 状态")
    for field in ("purpose_request_id", "purpose_task_id"):
        value = option.get(field)
        if value is not None and not isinstance(value, str):
            raise RuntimeError(f"用途状态字段类型不正确：{field}")
    report["status"] = "SIMULATION_ORIGINAL_SAVED_AND_REREAD"
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if json.loads(OUTPUT_PATH.read_text(encoding="utf-8")) != report:
        raise RuntimeError("新文字原件诊断报告保存重读不一致")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
