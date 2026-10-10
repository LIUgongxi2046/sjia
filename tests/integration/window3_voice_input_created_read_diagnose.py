import hashlib
import json
import os
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.simulation")
django.setup()

from recognition.models import VoiceInput
from window3_voice_input_adoption_roundtrip import api_session, get_json


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / ".simulation-work/t55-voice-fixture.json"
OUTPUT_PATH = ROOT / ".planning-work/parallel/window3/T55-voice-create-read-diagnosis.json"
API_BASE = "http://127.0.0.1:8001"


def main():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    expected = fixture["confirmed_text"]
    expected_hash = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    rows = list(VoiceInput.objects.filter(patient_id=fixture["patient_id"],
        original_file_id=fixture["original_file_id"], transcript_sha256=expected_hash,
        input_mode="SIMULATION_TEXT_ONLY").order_by("created_at", "id"))
    if not rows:
        raise RuntimeError("API create 后数据库没有对应模拟 VoiceInput")
    if len(rows) != 1:
        raise RuntimeError("准确模拟原件对应多个 VoiceInput，拒绝选择其一")
    row = rows[0]
    client, _, session = api_session(API_BASE, fixture["family_username"], "SJIA_SIMULATION_PASSWORD")
    read_status, response = get_json(client, API_BASE,
        f"/api/v2/patients/{fixture['patient_id']}/voice-inputs/{row.id}")
    transcript = response.get("transcript")
    report = {"data_mode": "SIMULATION", "evidence_scope": "SIMULATION_TEXT_ONLY",
        "status": "VOICE_CREATE_READ_DIAGNOSED", "patient_id": fixture["patient_id"],
        "principal_id": session["principal_id"], "voice_input_id": str(row.id),
        "request_id": str(row.request_id), "database_status": row.status,
        "database_original_file_id": str(row.original_file_id),
        "database_transcript_sha256": row.transcript_sha256,
        "api_http_status": read_status, "api_voice_input_id": response.get("id"),
        "api_patient_id": response.get("patient_id"), "api_request_id": response.get("request_id"),
        "api_status": response.get("status"), "api_status_version": response.get("status_version"),
        "api_transcript_sha256": response.get("transcript_sha256"),
        "api_transcript_size": len(transcript) if isinstance(transcript, str) else None,
        "api_transcript_hash_recomputed": hashlib.sha256(transcript.encode("utf-8")).hexdigest()
            if isinstance(transcript, str) else None,
        "api_original_file_id": response.get("original_file_id"),
        "api_input_mode": response.get("input_mode"),
        "assertion_results": {"http_200": read_status == 200,
            "id_matches_database": response.get("id") == str(row.id),
            "patient_matches": response.get("patient_id") == fixture["patient_id"],
            "request_id_matches": response.get("request_id") == str(row.request_id),
            "status_ready": response.get("status") == "ready",
            "transcript_matches": transcript == expected,
            "hash_matches": response.get("transcript_sha256") == expected_hash,
            "original_matches": response.get("original_file_id") == fixture["original_file_id"],
            "mode_matches": response.get("input_mode") == "SIMULATION_TEXT_ONLY"}}
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if json.loads(OUTPUT_PATH.read_text(encoding="utf-8")) != report:
        raise RuntimeError("VoiceInput 创建后读回诊断报告保存重读不一致")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
