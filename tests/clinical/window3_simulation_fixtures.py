import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / ".planning-work/t48-simulation.json"
OUTPUT = ROOT / ".planning-work/parallel/window3"
PATIENT_ID = "e88faef5-8bf6-4256-b5a5-865c699103d6"


def write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def create_t59_fixture(clinical):
    dataset = clinical["dataset"]
    labels = {item["observation_period_id"]: item for item in dataset["labels"]}
    positive = next(item for item in dataset["labels"] if item["label_state"] == "positive")
    negative = next(item for item in dataset["labels"] if item["label_state"] == "negative")
    unknown = next(item for item in dataset["labels"] if item["label_state"] == "unknown")
    periods = []
    fact_cutoff = "2026-10-05T21:00:00+00:00"
    later_time = "2026-10-06T00:00:00+00:00"
    subgroup_values = {
        "treatment_status": "SIMULATION_fixture",
        "age_group": "SIMULATION_fixture",
        "infection_context": "SIMULATION_fixture",
        "data_completeness": "SIMULATION_fixture",
    }
    for label in (positive, negative, unknown):
        period_id = label["observation_period_id"]
        periods.append({
            "period_id": period_id,
            "patient_id": PATIENT_ID,
            "label_state": label["label_state"],
            "observation_hours": "2",
            "subgroups": subgroup_values,
            "facts": [
                {"fact_ref": f"SIMULATION_fixture:{period_id}:known", "source_ref": "SIMULATION_FIXTURE_ONLY", "used": True,
                    "available_at": "2026-10-05T20:00:00+00:00", "known_at": "2026-10-05T20:30:00+00:00"},
                {"fact_ref": f"SIMULATION_fixture:{period_id}:future", "source_ref": "SIMULATION_FIXTURE_ONLY", "used": False,
                    "available_at": later_time, "known_at": later_time},
            ],
            "static": {"rule_version": "SIMULATION_ONLY_STATIC_FIXTURE_V1", "program_version": "window3-algorithm-fixture-1",
                "input_sha256": "a" * 64, "prompts": []},
            "dynamic": {"rule_version": "SIMULATION_ONLY_DYNAMIC_FIXTURE_V1", "program_version": "window3-algorithm-fixture-1",
                "input_sha256": "a" * 64, "prompts": []},
        })
    by_period = {item["period_id"]: item for item in periods}
    positive_period = by_period[positive["observation_period_id"]]
    negative_period = by_period[negative["observation_period_id"]]
    unknown_period = by_period[unknown["observation_period_id"]]
    event = {
        "event_id": "SIMULATION_fixture_positive_event",
        "patient_id": PATIENT_ID,
        "period_id": positive["observation_period_id"],
        "label_id": positive["label_id"],
        "label_state": "positive",
        "reference_answer_ref": positive["reference_answer_ref"],
        "reference_at": "2026-10-05T16:00:00+00:00",
        "valid_alert_start": "2026-10-05T15:00:00+00:00",
        "valid_alert_end": "2026-10-05T17:00:00+00:00",
    }
    unknown_event = {
        "event_id": "SIMULATION_fixture_unknown_event",
        "patient_id": PATIENT_ID,
        "period_id": unknown["observation_period_id"],
        "label_id": unknown["label_id"],
        "label_state": "unknown",
        "reference_answer_ref": None,
    }
    positive_period["static"]["prompts"] = [{"prompt_id": "SIMULATION_fixture_static_positive", "at": "2026-10-05T16:10:00+00:00",
        "matched_event_id": event["event_id"], "outcome": "positive", "outcome_ref": "SIMULATION_FIXTURE_ONLY"}]
    positive_period["dynamic"]["prompts"] = [{"prompt_id": "SIMULATION_fixture_dynamic_positive", "at": "2026-10-05T15:50:00+00:00",
        "matched_event_id": event["event_id"], "outcome": "positive", "outcome_ref": "SIMULATION_FIXTURE_ONLY"}]
    negative_period["static"]["prompts"] = [{"prompt_id": "SIMULATION_fixture_static_negative", "at": "2026-10-05T18:30:00+00:00",
        "matched_event_id": None, "outcome": "negative", "outcome_ref": "SIMULATION_FIXTURE_ONLY"}]
    unknown_period["static"]["prompts"] = [{"prompt_id": "SIMULATION_fixture_static_unknown", "at": "2026-10-05T20:30:00+00:00",
        "matched_event_id": unknown_event["event_id"], "outcome": "unknown", "outcome_ref": "SIMULATION_FIXTURE_ONLY"}]
    unknown_period["dynamic"]["prompts"] = [{"prompt_id": "SIMULATION_fixture_dynamic_unknown", "at": "2026-10-05T20:35:00+00:00",
        "matched_event_id": unknown_event["event_id"], "outcome": "unknown", "outcome_ref": "SIMULATION_FIXTURE_ONLY"}]
    return {
        "schema_version": "window3-clinical-predictions-1",
        "data_mode": "SIMULATION",
        "evidence_scope": "SIMULATION_FIXTURE_ONLY",
        "fixture_note": "Authored algorithm scenario; it does not represent T49/T50 execution or clinical outcomes.",
        "freeze": {"dataset_id": dataset["dataset_id"], "version": dataset["version"], "checksum": dataset["checksum"]},
        "protocol": {
            "mode": "replay",
            "data_mode": "SIMULATION",
            "evidence_scope": "SIMULATION_FIXTURE_ONLY",
            "run_status": "completed",
            "known_at": fact_cutoff,
            "static_rule_version": "SIMULATION_ONLY_STATIC_FIXTURE_V1",
            "dynamic_rule_version": "SIMULATION_ONLY_DYNAMIC_FIXTURE_V1",
            "static_program_version": "window3-algorithm-fixture-1",
            "dynamic_program_version": "window3-algorithm-fixture-1",
            "static_execution_ref": "SIMULATION_FIXTURE_ONLY:static",
            "dynamic_execution_ref": "SIMULATION_FIXTURE_ONLY:dynamic",
            "matching_policy_ref": "SIMULATION_FIXTURE_ONLY:prompt-event-pairing",
            "subgroup_dimensions": ["treatment_status", "age_group", "infection_context", "data_completeness"],
            "side_effect_counts": {"published": 0, "notified": 0, "current_cycle_mutations": 0},
        },
        "patients": [{"patient_id": PATIENT_ID, "partition": "development"}],
        "periods": periods,
        "events": [event, unknown_event],
    }


def create_t32_fixtures(clinical):
    labels = clinical["dataset"]["labels"]
    answers = []
    for label in labels:
        if label["label_state"] not in {"positive", "negative"}:
            continue
        raw_answer = f"SIMULATION scenario answer: frozen label state is {label['label_state']}. This is not original source text."
        answers.append({
            "label_id": label["label_id"],
            "reference_answer_ref": label["reference_answer_ref"],
            "label_state": label["label_state"],
            "answer_origin": "simulation_manual_independent",
            "model_provider_request_id": None,
            "raw_answer": raw_answer,
            "raw_answer_sha256": hashlib.sha256(raw_answer.encode("utf-8")).hexdigest(),
            "author_id": "SIMULATION_WINDOW3_ANSWER_MAINTAINER",
            "source_scope": "synthetic scenario fixture; not original source text",
        })
    gold = {
        "schema_version": "window3-independent-gold-1",
        "data_mode": "SIMULATION",
        "evidence_scope": "SIMULATION_FIXTURE_ONLY",
        "fixture_note": "Scenario strings check independent-answer validation only; they are not recovered Gold source text.",
        "answers": answers,
    }
    family = {
        "schema_version": "window3-family-observations-1",
        "data_mode": "SIMULATION",
        "evidence_scope": "SIMULATION_FIXTURE_ONLY",
        "fixture_note": "Synthetic task rows check aggregation only; no family participant or device was observed.",
        "observations": [
            {"observation_id": "SIMULATION_fixture_family_001", "participant_ref": "SIMULATION_fixture_participant_001",
                "task_ref": "SIMULATION_fixture_source_task", "observer_ref": "SIMULATION_window3_evaluator",
                "authorized": True, "authorization_ref": "SIMULATION_FIXTURE_ONLY", "started_at": "2026-10-09T01:00:00+00:00",
                "finished_at": "2026-10-09T01:02:00+00:00", "completed": True, "interrupted": False,
                "assistance_count": 0, "transcription_error_count": 0, "text_misunderstanding_count": 0,
                "device_profile": "SIMULATION_fixture_390px", "comprehension": {"source": True, "insufficient_data": True, "next_step": True, "contact_method": True}},
            {"observation_id": "SIMULATION_fixture_family_002", "participant_ref": "SIMULATION_fixture_participant_002",
                "task_ref": "SIMULATION_fixture_unknown_task", "observer_ref": "SIMULATION_window3_evaluator",
                "authorized": True, "authorization_ref": "SIMULATION_FIXTURE_ONLY", "started_at": "2026-10-09T02:00:00+00:00",
                "finished_at": "2026-10-09T02:04:00+00:00", "completed": False, "interrupted": True,
                "assistance_count": 2, "transcription_error_count": 1, "text_misunderstanding_count": 1,
                "device_profile": "SIMULATION_fixture_390px", "comprehension": {"source": True, "insufficient_data": False, "next_step": None, "contact_method": False}},
        ],
    }
    return gold, family


def create_partition_fixture():
    patients = [
        {"patient_id": "SIMULATION_PATIENT_DEVELOPMENT", "partition": "development", "source_ref": "SIMULATION_FIXTURE_ONLY:development-source"},
        {"patient_id": "SIMULATION_PATIENT_VALIDATION", "partition": "validation", "source_ref": "SIMULATION_FIXTURE_ONLY:validation-source"},
    ]
    label_specs = [
        ("SIMULATION_DEV_POSITIVE", patients[0]["patient_id"], "development", "positive", "SIMULATION_DEV_PERIOD_POSITIVE"),
        ("SIMULATION_DEV_UNKNOWN", patients[0]["patient_id"], "development", "unknown", "SIMULATION_DEV_PERIOD_UNKNOWN"),
        ("SIMULATION_VAL_POSITIVE", patients[1]["patient_id"], "validation", "positive", "SIMULATION_VAL_PERIOD_POSITIVE"),
        ("SIMULATION_VAL_NEGATIVE", patients[1]["patient_id"], "validation", "negative", "SIMULATION_VAL_PERIOD_NEGATIVE"),
        ("SIMULATION_VAL_UNKNOWN", patients[1]["patient_id"], "validation", "unknown", "SIMULATION_VAL_PERIOD_UNKNOWN"),
    ]
    labels = []
    for label_id, patient_id, partition, label_state, period_id in label_specs:
        answer = None if label_state == "unknown" else f"SIMULATION independent fixture answer: {label_state}; not a clinical source."
        labels.append({
            "label_id": label_id,
            "patient_id": patient_id,
            "partition": partition,
            "observation_period_id": period_id,
            "label_state": label_state,
            "reference_answer_ref": None if answer is None else f"SIMULATION_FIXTURE_ONLY:{label_id}:answer",
            "raw_answer": answer,
            "raw_answer_sha256": None if answer is None else hashlib.sha256(answer.encode("utf-8")).hexdigest(),
            "answer_origin": "simulation_manual_independent" if answer is not None else None,
            "model_provider_request_id": None,
            "source_ref": f"SIMULATION_FIXTURE_ONLY:{label_id}:source",
            "limitations": ["SIMULATION unknown label; no negative conclusion"] if label_state == "unknown" else [],
        })
    return {
        "schema_version": "window3-partition-evaluation-fixture-1",
        "data_mode": "SIMULATION",
        "evidence_scope": "SIMULATION_FIXTURE_ONLY",
        "fixture_note": "Two independent synthetic patient identities check partition and label denominator handling. This is not T48 or clinical validation evidence.",
        "dataset": {"dataset_id": "SIMULATION_WINDOW3_PARTITION_FIXTURE", "version": "1", "checksum": "SIMULATION_FIXTURE_ONLY"},
        "patients": patients,
        "observation_periods": [
            {"observation_period_id": label["observation_period_id"], "patient_id": label["patient_id"], "observation_hours": "2"}
            for label in labels
        ],
        "labels": labels,
    }


def main():
    captured = json.loads(SOURCE.read_text(encoding="utf-8"))
    clinical = captured["inputs"]["clinical"]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    t59 = create_t59_fixture(clinical)
    gold, family = create_t32_fixtures(clinical)
    partitions = create_partition_fixture()
    write(OUTPUT / "T59-algorithm-fixture.json", t59)
    write(OUTPUT / "T59-partition-fixture.json", partitions)
    write(OUTPUT / "T32-independent-answer-fixture.json", gold)
    write(OUTPUT / "T32-family-observation-fixture.json", family)
    print(json.dumps({
        "status": "SIMULATION_FIXTURES_CREATED",
        "source_dataset_id": clinical["dataset"]["dataset_id"],
        "source_version": clinical["dataset"]["version"],
        "paths": [str(OUTPUT / "T59-algorithm-fixture.json"), str(OUTPUT / "T59-partition-fixture.json"), str(OUTPUT / "T32-independent-answer-fixture.json"), str(OUTPUT / "T32-family-observation-fixture.json")],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
