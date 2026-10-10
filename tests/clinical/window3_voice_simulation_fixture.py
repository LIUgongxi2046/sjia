import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / ".planning-work/parallel/window3"
TRANSCRIPT = "模拟转写记录：2026年10月9日早上7点测体温38.5摄氏度。孩子今天早上发热，没有皮疹，精神比平时差。"


def write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    transcript_sha256 = hashlib.sha256(TRANSCRIPT.encode("utf-8")).hexdigest()
    transcript = {
        "schema_version": "window3-simulation-voice-transcript-1",
        "data_mode": "SIMULATION",
        "evidence_scope": "SIMULATION_TEXT_ONLY",
        "source": {
            "source_id": "SIMULATION_WINDOW3_VOICE_TEXT_001",
            "source_kind": "synthetic_transcript_text",
            "audio_source_available": False,
            "transcript_text": TRANSCRIPT,
            "transcript_sha256": transcript_sha256,
        },
        "model_candidate": {
            "status": "NOT_RUN",
            "task_id": None,
            "voice_input_id": None,
            "provider_request_id": None,
            "fields": [],
        },
        "independent_answer": {
            "answer_id": "SIMULATION_WINDOW3_VOICE_GOLD_001",
            "answer_origin": "simulation_manual_independent",
            "author_ref": "SIMULATION_WINDOW3_ANSWER_MAINTAINER",
            "derived_from_model_output": False,
            "clinical_review": "NOT_RUN",
            "source_transcript_sha256": transcript_sha256,
            "fields": [
                {
                    "field_path": "/fields/body_temperature",
                    "name": "体温",
                    "comparator": None,
                    "value": "38.5",
                    "unit": "°C",
                    "negation": "not_applicable",
                    "medical_time": "2026-10-09T07:00:00+08:00",
                    "context": "家庭晨间测量",
                    "source_span": "早上7点测体温38.5摄氏度",
                },
                {
                    "field_path": "/fields/fever",
                    "name": "发热",
                    "comparator": None,
                    "value": "present",
                    "unit": None,
                    "negation": "affirmed",
                    "medical_time": "2026-10-09 morning Asia/Shanghai",
                    "context": "家庭症状观察",
                    "source_span": "孩子今天早上发热",
                },
                {
                    "field_path": "/fields/rash",
                    "name": "皮疹",
                    "comparator": None,
                    "value": "absent",
                    "unit": None,
                    "negation": "negated",
                    "medical_time": "2026-10-09 morning Asia/Shanghai",
                    "context": "家庭症状观察",
                    "source_span": "没有皮疹",
                },
                {
                    "field_path": "/fields/activity",
                    "name": "活动状态",
                    "comparator": None,
                    "value": "reduced_relative_to_usual",
                    "unit": None,
                    "negation": "affirmed",
                    "medical_time": "2026-10-09 morning Asia/Shanghai",
                    "context": "家庭症状观察",
                    "source_span": "精神比平时差",
                },
            ],
        },
        "adoption_roundtrip": {"status": "NOT_RUN"},
    }
    plan = {
        "schema_version": "window3-simulation-voice-input-roundtrip-plan-2",
        "data_mode": "SIMULATION",
        "evidence_scope": "SIMULATION_TEXT_ONLY",
        "plan_status": "WAITING_FOR_MAIN_WINDOW_VOICE_INTERFACE",
        "source_fixture": "T55-voice-simulation-text-only.json",
        "source_sha256": transcript_sha256,
        "api_base": "http://127.0.0.1:8001",
        "database_identity": {"name": None, "port": None, "user": None},
        "account": {"username": "simulation-family", "password_env": "SJIA_SIMULATION_PASSWORD"},
        "task": {
            "task_id": None,
            "expected_task_type": None,
            "read_path": None,
            "selectors": {"status": None, "input_sha256": None},
            "input_ref_selectors": {"voice_input_id": None, "usage_authorization_id": None},
            "expected_before": None,
            "expected_after": None,
        },
        "voice_input": {
            "voice_input_id": None,
            "patient_id": None,
            "read_path": None,
            "selectors": {"status": None, "transcript": None, "audio_reference": None,
                "patient_id": None, "task_id": None, "usage_authorization": None,
                "usage_authorization_id": None, "task_input_sha256": None},
            "model_call_selectors": {"task_attempt_id": None, "provider_request_id": None, "provider": None, "model": None},
            "expected_before": None,
            "expected_after": None,
            "authorization": {
                "source_revision_allowed_use_selector": None,
                "selectors": {"status": None, "purpose": None, "authorized_at": None,
                    "expires_at": None, "revoked_at": None, "source_revision_id": None},
                "expected": {"status": None, "purpose": None, "source_revision_id": None,
                    "environment": None, "approved_by_id": None, "source_revision_allowed_use": None},
            },
        },
        "projections": {
            "candidate": {"items_path": None, "field_path": None, "values": None, "source_ref": None},
            "adopted": {"items_path": None, "field_path": None, "values": None, "decision": None, "source_ref": None},
            "saved_reread": {"items_path": None, "field_path": None, "values": None, "decision": None, "source_ref": None},
        },
        "expected_field_paths": {"candidate": None, "adopted": None, "saved_reread": None},
        "adoption": {
            "method": None,
            "path": None,
            "payload": None,
            "success_status": None,
            "read_path_after": None,
            "expected_decisions": None,
            "expected_saved_decisions": None,
            "idempotency": {"header": None, "comparison_selectors": None},
        },
        "record": {
            "field_config_path": None,
            "field_config_version_selector": None,
            "expected_field_config_version": None,
            "field_config_kind_selector": None,
            "field_config_capture_method_selector": None,
            "target_path": None,
            "read_path": None,
            "write_intent_path": "/api/write-intents",
            "operation_id": "record_create",
            "request_schema_version": "back-json-1",
            "kind": None,
            "capture_method": None,
            "payload": None,
            "save_success_status": None,
            "record_id_selector": None,
            "record_version_id_selector": None,
            "patient_id_selector": None,
        },
        "model_call": {"provider": None, "model": None, "usage_selectors": {"prompt_tokens": None, "completion_tokens": None},
            "pricing": {"status": "NOT_MEASURED", "cost_cny": None}},
        "authorization_revocation_probe": {
            "revoke": {"method": None, "path": None, "payload": None, "request_id": None, "success_status": None,
                "idempotency_header": None,
                "response_selectors": None, "expected_response": None},
            "rejected_processing": {"method": None, "path": None, "payload": None, "request_id": None, "status": None,
                "idempotency_header": None, "error_selectors": None, "expected_errors": None},
            "read_paths": {"voice_input": None, "task": None, "record": None},
            "read_selectors": {"voice_status": None, "task_status": None,
                "record_id": None, "record_version_id": None},
        },
        "terminal_probes": {
            "cancel": {"task_id": None, "voice_input_id": None, "task_read_path": None,
                "task_status_selector": None, "expected_task_before_status": None,
                "voice_input_read_path": None, "voice_task_id_selector": None,
                "voice_patient_id_selector": None, "voice_audio_reference_selector": None,
                "cancel_action": {"method": None, "path": None, "payload": None, "success_status": None,
                    "request_id": None, "idempotency_header": None},
                "expected_task_status": None, "attempt_statuses": None,
                "candidate_read_path": None, "candidate_status_selector": None, "candidate_expected_status": None,
                "record_absence_path": None, "record_absence_status": None,
                "record_items_path": None},
            "model_result_unknown": {"task_id": None, "voice_input_id": None, "task_read_path": None,
                "task_status_selector": None, "expected_task_before_status": None,
                "voice_input_read_path": None, "voice_task_id_selector": None,
                "voice_patient_id_selector": None, "voice_audio_reference_selector": None,
                "expected_task_status": None, "attempt_statuses": None,
                "candidate_read_path": None, "candidate_status_selector": None, "candidate_expected_status": None,
                "record_absence_path": None, "record_absence_status": None,
                "record_items_path": None},
        },
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    transcript_path = OUTPUT / "T55-voice-simulation-text-only.json"
    plan_path = OUTPUT / "T55-voice-adoption-plan.json"
    write(transcript_path, transcript)
    write(plan_path, plan)
    print(json.dumps({
        "status": "SIMULATION_TEXT_ONLY_FIXTURE_CREATED",
        "transcript_sha256": transcript_sha256,
        "paths": [str(transcript_path), str(plan_path)],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
