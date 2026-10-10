import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.simulation")
django.setup()

from django.db import connection, transaction
from alerting.models import AlertDecision, AlertEvaluation, AlertEvaluationAssessment, AlertEvaluationInput, AlertEvent, AlertSignalResult
from archives.models import ArchiveFactVersion, EvaluationDatasetVersion, EvaluationLabel, EvaluationPatientPartition
from assessment.models import Assessment, AssessmentInput, AssessmentOutput
from configurations.models import ConfigApproval, ConfigValidation, ConfigVersion
from medical_time.models import MedicalTime
from records.models import RecordField, RecordVersion
from verification.models import Verification, VerificationField
from tasks.models import Task, TaskAttempt


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / ".planning-work/parallel/window3/T59-saved-rule-result-capture.json"


def iso(value):
    return value.isoformat() if value is not None else None


def config_evidence(config):
    if config is None:
        return None
    approvals = ConfigApproval.objects.filter(config_id=config.id).order_by("approved_at", "id")
    validations = ConfigValidation.objects.filter(config_id=config.id).order_by("executed_at", "id")
    return {
        "config_id": str(config.id),
        "config_key": config.config_key,
        "kind": config.kind,
        "version": config.version,
        "schema_version": config.schema_version,
        "checksum": config.checksum,
        "status": config.status,
        "source_revision_id": str(config.source_revision_id) if config.source_revision_id else None,
        "created_at": iso(config.created_at),
        "approvals": [
            {"approval_id": str(item.id), "approved_by": str(item.approved_by_id), "role": item.approval_role,
             "approved_at": iso(item.approved_at), "evidence_ref": item.evidence_ref,
             "effective_scope": item.effective_scope, "valid_until": iso(item.valid_until), "revoked_at": iso(item.revoked_at)}
            for item in approvals
        ],
        "validations": [
            {"validation_id": str(item.id), "config_checksum": item.config_checksum,
             "environment": item.environment_id, "artifact_sha256": item.artifact_sha256,
             "dataset_revision_id": str(item.dataset_revision_id), "evidence_ref": item.evidence_ref,
             "evidence_sha256": item.evidence_sha256, "outcome": item.outcome,
             "executed_by": str(item.executed_by_id), "executed_at": iso(item.executed_at),
             "revoked_at": iso(item.revoked_at)}
            for item in validations
        ],
    }


def task_evidence(task_id, expected_type, expected_schema):
    task = Task.objects.get(id=task_id)
    if task.task_type != expected_type or task.schema_version != expected_schema:
        raise ValueError(f"Task {task_id} 类型或 Schema 版本不符合捕获目标")
    attempts = TaskAttempt.objects.filter(task_id=task.id).order_by("attempt_number")
    return task, {
        "task_id": str(task.id), "task_type": task.task_type, "schema_version": task.schema_version,
        "patient_id": str(task.patient_id) if task.patient_id else None, "actor_id": str(task.actor_id),
        "input_refs": task.input_refs, "input_sha256": task.input_sha256, "status": task.status,
        "status_version": task.status_version, "created_at": iso(task.created_at),
        "deadline_at": iso(task.deadline_at),
        "attempts": [
            {"attempt_id": str(item.id), "attempt_number": item.attempt_number,
             "provider_request_id": item.provider_request_id, "started_at": iso(item.started_at),
             "finished_at": iso(item.finished_at), "status": item.status,
             "usage_evidence": item.usage_evidence}
            for item in attempts
        ],
    }


def selected_fact_evidence(task, patient_id):
    known_at = datetime.fromisoformat(task.input_refs["payload"]["known_at"].replace("Z", "+00:00"))
    selected_refs = task.input_refs["snapshot"]["selected_refs"]
    facts = []
    for variable_code, reference in sorted(selected_refs.items()):
        if reference is None:
            facts.append({"variable_code": variable_code, "selected": False})
            continue
        fact = ArchiveFactVersion.objects.get(id=reference["fact_version_id"], patient_id=patient_id)
        field = RecordField.objects.get(id=reference["field_id"], record_version_id=reference["record_version_id"], patient_id=patient_id)
        version = RecordVersion.objects.get(id=reference["record_version_id"], patient_id=patient_id)
        verification = Verification.objects.get(id=reference["verification_id"], patient_id=patient_id) if reference["verification_id"] else None
        verification_field = VerificationField.objects.filter(
            verification_id=reference["verification_id"], record_version_id=reference["record_version_id"],
            patient_id=patient_id, field_id=reference["field_id"],
        ).first() if verification is not None else None
        medical_time = MedicalTime.objects.get(id=reference["medical_time_id"], patient_id=patient_id) if reference["medical_time_id"] else None
        available_at = fact.available_at
        verified_at = verification.verified_at if verification is not None else None
        facts.append({
            "variable_code": variable_code, "selected": True,
            "fact_version_id": str(fact.id), "fact_version": fact.version,
            "record_version_id": str(version.id), "record_version": version.version,
            "record_quality_status": version.quality_status, "recorded_at": iso(version.recorded_at),
            "record_source_revision_id": str(version.source_revision_id) if version.source_revision_id else None,
            "field_id": str(field.id), "field_code": field.field_code, "concept_code": field.concept_code,
            "original_name": field.original_name, "value_type": field.value_type, "value_state": field.value_state,
            "raw_text": field.raw_text, "raw_numeric": str(field.raw_numeric) if field.raw_numeric is not None else None,
            "categorical_value": field.categorical_value, "qualifier": field.qualifier, "raw_unit": field.raw_unit,
            "method": field.method, "medical_time_id": str(fact.medical_time_id) if fact.medical_time_id else None,
            "medical_time": None if medical_time is None else {
                "precision": medical_time.precision, "raw_text": medical_time.raw_text,
                "instant": iso(medical_time.instant), "local_date": iso(medical_time.local_date),
                "local_datetime": iso(medical_time.local_datetime), "timezone_name": medical_time.timezone_name,
                "utc_offset_minutes": medical_time.utc_offset_minutes, "resolution_status": medical_time.resolution_status,
            },
            "available_at": iso(available_at), "availability_status": fact.availability_status,
            "verification_id": str(verification.id) if verification is not None else None,
            "verified_at": iso(verified_at), "verification_revoked_at": iso(verification.revoked_at) if verification else None,
            "verification_evidence_ref": verification.evidence_ref if verification else None,
            "verification_field_result": verification_field.result if verification_field else None,
            "verification_status": reference.get("verification_status"),
            "available_by_known_at": fact.availability_status == "known" and available_at is not None and available_at <= known_at,
            "verified_by_known_at": verification is not None and verified_at is not None
                and verified_at <= known_at and verification_field is not None
                and verification_field.result == "consistent"
                and (verification.revoked_at is None or verification.revoked_at > known_at),
            "source_anchor_id": str(fact.source_anchor_id) if fact.source_anchor_id else None,
            "source_revision_id": str(version.source_revision_id) if version.source_revision_id else None,
        })
    return facts


def source_link_check(input_rows, selected_facts):
    captured_refs = {
        (item["record_version_id"], item["field_id"], item["verification_id"])
        for item in selected_facts if item["selected"]
    }
    result_refs = {
        (str(item["record_version_id"]), str(item["field_id"]), str(item["verification_id"]))
        for item in input_rows
    }
    unmatched = sorted(result_refs - captured_refs)
    return {
        "result_input_count": len(input_rows),
        "unique_result_input_reference_count": len(result_refs),
        "captured_selected_fact_count": len(captured_refs),
        "unmatched_result_input_references": [
            {"record_version_id": ref[0], "field_id": ref[1], "verification_id": ref[2]}
            for ref in unmatched
        ],
        "all_result_inputs_match_task_selected_facts": not unmatched,
    }


def static_result(task_id, expected_kind):
    task, task_data = task_evidence(task_id, Task.TaskType.ASSESSMENT, "static-assessment-task-2")
    task_data["payload"] = task.input_refs["payload"]
    rows = Assessment.objects.filter(task_id=task.id).order_by("id")
    assessments = []
    for row in rows:
        if row.kind != expected_kind:
            raise ValueError(f"Task {task_id} 的 Assessment kind 与 {expected_kind} 不符")
        output = AssessmentOutput.objects.filter(assessment_id=row.id).first()
        inputs = AssessmentInput.objects.filter(assessment_id=row.id).order_by("variable_code")
        input_rows = [
            {"input_id": str(item.id), "variable_code": item.variable_code,
             "record_version_id": str(item.record_version_id), "field_id": str(item.field_id),
             "verification_id": str(item.verification_id),
             "normalization_id": str(item.normalization_id) if item.normalization_id else None,
             "input_snapshot": item.input_snapshot}
            for item in inputs
        ]
        selected_facts = selected_fact_evidence(task, row.patient_id)
        assessments.append({
            "assessment_id": str(row.id), "patient_id": str(row.patient_id), "kind": row.kind,
            "assessment_at": iso(row.assessment_at), "known_at": task.input_refs["payload"]["known_at"],
            "mode": task.input_refs["payload"]["mode"], "rule": config_evidence(row.rule),
            "window_policy": config_evidence(row.window_policy), "data_epoch": row.data_epoch,
            "verification_epoch": row.verification_epoch, "computation_status": row.computation_status,
            "validity": row.validity, "missing_items": row.missing_items, "computed_at": iso(row.computed_at),
            "output": None if output is None else {
                "schema_version": output.schema_version, "classification_conditions": output.classification_conditions,
                "classification_result": output.classification_result, "ms_contributions": output.ms_contributions,
                "ms_total": str(output.ms_total) if output.ms_total is not None else None,
                "ms_threshold_result": output.ms_threshold_result,
            },
            "inputs": input_rows,
            "selected_facts": selected_facts,
            "source_link_check": source_link_check(input_rows, selected_facts),
        })
    return {"task": task_data, "results": assessments}


def dynamic_result(task_id):
    task, task_data = task_evidence(task_id, Task.TaskType.ALERT_EVALUATION, "dynamic-evaluation-task-2")
    task_data["payload"] = task.input_refs["payload"]
    rows = AlertEvaluation.objects.filter(task_id=task.id).order_by("id")
    evaluations = []
    for row in rows:
        decisions = AlertDecision.objects.filter(evaluation_id=row.id).order_by("rule_key")
        input_rows = [
            {"input_id": str(item.id), "signal_id": item.signal_id, "position": item.position,
             "variable_code": item.variable_code, "input_role": item.input_role,
             "record_version_id": str(item.record_version_id), "field_id": str(item.field_id),
             "verification_id": str(item.verification_id),
             "normalization_id": str(item.normalization_id) if item.normalization_id else None,
             "medical_time_id": str(item.medical_time_id) if item.medical_time_id else None,
             "source_revision_id": str(item.source_revision_id)}
            for item in AlertEvaluationInput.objects.filter(evaluation_id=row.id).order_by("signal_id", "position")
        ]
        selected_facts = selected_fact_evidence(task, row.patient_id)
        evaluations.append({
            "evaluation_id": str(row.id), "patient_id": str(row.patient_id), "environment": row.environment,
            "policy": config_evidence(row.policy), "window_policy": config_evidence(row.window_policy),
            "assessment_at": iso(row.assessment_at), "known_at": iso(row.known_at),
            "context_sequence": row.context_sequence, "mode": row.mode, "status": row.status,
            "decision": row.decision, "data_epoch": row.data_epoch,
            "verification_epoch": row.verification_epoch, "publication_epoch": row.publication_epoch,
            "authorization_version": row.authorization_version, "use_authorization_id": str(row.use_authorization_id),
            "input_digest": row.input_digest, "missing_items": row.missing_items,
            "computed_at": iso(row.computed_at), "validity": row.validity,
            "inputs": input_rows,
            "assessments": [
                {"assessment_id": str(item.assessment_id), "role": item.role}
                for item in AlertEvaluationAssessment.objects.filter(evaluation_id=row.id).order_by("role", "assessment_id")
            ],
            "signals": [
                {"signal_id": item.signal_id, "schema_version": item.schema_version,
                 "state": item.state, "algorithm_version": item.algorithm_version,
                 "input_field_ids": [str(value) for value in item.input_field_ids],
                 "reason_codes": item.reason_codes, "pair_results": item.pair_results}
                for item in AlertSignalResult.objects.filter(evaluation_id=row.id).order_by("signal_id")
            ],
            "decisions": [
                {"rule_key": item.rule_key, "state": item.state, "action": item.action,
                 "reason_codes": item.reason_codes, "episode_id": str(item.episode_id) if item.episode_id else None,
                 "sequence": item.sequence}
                for item in decisions
            ],
            "events": [
                {"event_id": str(item.id), "trigger_key": item.trigger_key,
                 "trigger_policy_id": str(item.trigger_policy_id), "validity": item.validity,
                 "created_at": iso(item.created_at)}
                for item in AlertEvent.objects.filter(evaluation_id=row.id).order_by("created_at", "id")
            ],
            "selected_facts": selected_facts,
            "source_link_check": source_link_check(input_rows, selected_facts),
        })
    return {"task": task_data, "results": evaluations}


def frozen_dataset_evidence(dataset_version_id):
    dataset = EvaluationDatasetVersion.objects.get(id=dataset_version_id)
    if dataset.status != "frozen" or dataset.frozen_at is None:
        raise ValueError("T48 评价集合版本尚未冻结")
    partitions = EvaluationPatientPartition.objects.filter(dataset_version_id=dataset.id).order_by("partition", "patient_id")
    labels = EvaluationLabel.objects.filter(partition_id__in=partitions.values("id")).order_by("patient_id", "observation_period_id")
    partition_rows = [
        {"partition_id": str(item.id), "patient_id": str(item.patient_id), "partition": item.partition,
         "use_authorization_id": str(item.use_authorization_id), "source_refs": item.source_refs}
        for item in partitions
    ]
    label_rows = [
        {"label_id": str(item.id), "patient_id": str(item.patient_id), "partition_id": str(item.partition_id),
         "observation_period_id": str(item.observation_period_id), "label_state": item.label_state,
         "reference_answer_ref": item.reference_answer_ref, "reference_recognition_at": iso(item.reference_recognition_at),
         "confirmed_by": str(item.confirmed_by) if item.confirmed_by else None,
         "confirmed_at": iso(item.confirmed_at), "limitations": item.limitations}
        for item in labels
    ]
    labels_by_partition = {
        name: {
            "patients": len({item["patient_id"] for item in partition_rows if item["partition"] == name}),
            "positive": sum(label.label_state == "positive" and str(label.patient_id) in {
                item["patient_id"] for item in partition_rows if item["partition"] == name
            } for label in labels),
            "negative": sum(label.label_state == "negative" and str(label.patient_id) in {
                item["patient_id"] for item in partition_rows if item["partition"] == name
            } for label in labels),
            "unknown_excluded_from_negative": sum(label.label_state == "unknown" and str(label.patient_id) in {
                item["patient_id"] for item in partition_rows if item["partition"] == name
            } for label in labels),
        }
        for name in ("development", "validation")
    }
    return {
        "dataset_id": str(dataset.dataset_id), "dataset_version_id": str(dataset.id),
        "version": dataset.version, "purpose": dataset.purpose, "environment": dataset.environment,
        "status": dataset.status, "checksum": dataset.checksum, "frozen_at": iso(dataset.frozen_at),
        "protocol_ref": dataset.protocol_ref, "coverage": dataset.coverage,
        "partitions": partition_rows, "labels": label_rows, "labels_by_partition": labels_by_partition,
        "patient_partition_count": len(partition_rows),
        "cross_partition_patient_ids": sorted({item["patient_id"] for item in partition_rows
            if sum(row["patient_id"] == item["patient_id"] for row in partition_rows) > 1}),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--classification-task-id", required=True)
    parser.add_argument("--ms-task-id", required=True)
    parser.add_argument("--dynamic-task-id", required=True)
    parser.add_argument("--dataset-version-id", required=True)
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()
    task_ids = (args.classification_task_id, args.ms_task_id, args.dynamic_task_id)
    if len(set(task_ids)) != 3:
        raise ValueError("classification、MS、dynamic 必须引用三个不同的真实 Task")
    if connection.settings_dict["NAME"] != "sjia_simulation":
        raise RuntimeError("只允许读取 sjia_simulation")
    if connection.settings_dict["HOST"] not in ("127.0.0.1", "localhost") or str(connection.settings_dict["PORT"]) != "5433":
        raise RuntimeError("只允许读取共享模拟 PostgreSQL 127.0.0.1:5433")
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT current_database(), inet_server_port(), current_setting('transaction_read_only')")
            database_name, database_port, transaction_read_only = cursor.fetchone()
        if database_name != "sjia_simulation":
            raise RuntimeError("数据库连接身份与 sjia_simulation 不一致")
        if database_port != 5433:
            raise RuntimeError("数据库服务端口与共享模拟 PostgreSQL 5433 不一致")
        if transaction_read_only != "on":
            raise RuntimeError("数据库事务没有进入只读模式")
        dataset = frozen_dataset_evidence(args.dataset_version_id)
        classification = static_result(args.classification_task_id, "classification")
        ms = static_result(args.ms_task_id, "ms")
        dynamic = dynamic_result(args.dynamic_task_id)
        if not classification["results"] or not ms["results"] or not dynamic["results"]:
            raise ValueError("指定 Task 缺少已保存的 Assessment 或 AlertEvaluation 结果")
        if any(len(result["results"]) != 1 for result in (classification, ms, dynamic)):
            raise ValueError("每个指定 Task 必须关联且只关联一条结果")
        if any(item["output"] is None for result in (classification, ms) for item in result["results"]):
            raise ValueError("静态 Assessment 缺少已保存的 AssessmentOutput")
        if any(result["task"]["status"] != "completed" for result in (classification, ms, dynamic)):
            raise ValueError("classification、MS、dynamic 三个 Task 均须完成后才能捕获")
        result_patients = {
            item["patient_id"]
            for result in (classification, ms, dynamic)
            for item in result["results"]
        }
        if len(result_patients) != 1:
            raise ValueError("三个 Task 的结果必须属于同一模拟患儿")
        patient_id = next(iter(result_patients))
        cohort_membership = [item for item in dataset["partitions"] if item["patient_id"] == patient_id]
        task_contexts = [result["task"]["payload"] for result in (classification, ms, dynamic)]
        known_at_values = [context["known_at"] for context in task_contexts]
        assessment_at_values = [context["assessment_at"] for context in task_contexts]
        mode_values = [context["mode"] for context in task_contexts]
        result_rows = [classification["results"][0], ms["results"][0], dynamic["results"][0]]
        task_result_time_matches = [
            result["assessment_at"] == context["assessment_at"]
            and result["mode"] == context["mode"]
            and ("known_at" not in result or result["known_at"] == context["known_at"])
            for result, context in zip(result_rows, task_contexts)
        ]
        source_link_checks = {
            "classification": classification["results"][0]["source_link_check"],
            "ms": ms["results"][0]["source_link_check"],
            "dynamic": dynamic["results"][0]["source_link_check"],
        }
        selected_facts = [fact for result in (classification, ms, dynamic)
                          for item in result["results"] for fact in item["selected_facts"] if fact["selected"]]
        future_fact_count = sum(not fact["available_by_known_at"] or not fact["verified_by_known_at"] for fact in selected_facts)
        payload = {
            "schema_version": "window3-saved-t49-t50-capture-2", "data_mode": "SIMULATION",
            "evidence_scope": "ACTUAL_SJIA_SIMULATION_TASK_RESULT", "read_only": True,
            "captured_at": datetime.now(timezone.utc).isoformat(), "database_name": database_name, "database_port": database_port,
            "frozen_dataset": dataset,
            "task_type_model_versions": {"classification": "deterministic_assessment", "ms": "deterministic_assessment",
                                         "dynamic": "deterministic_alert_evaluation", "provider": "NOT_APPLICABLE",
                                         "model": "NOT_APPLICABLE"},
            "classification": classification, "ms": ms, "dynamic": dynamic,
            "cohort_binding": {"patient_id": patient_id, "dataset_membership": cohort_membership,
                               "member_of_frozen_set": bool(cohort_membership),
                               "same_known_at": len(set(known_at_values)) == 1,
                               "same_assessment_at": len(set(assessment_at_values)) == 1,
                               "known_at_values": known_at_values,
                               "assessment_at_values": assessment_at_values,
                               "task_result_time_matches": task_result_time_matches,
                               "source_link_checks": source_link_checks,
                               "all_result_inputs_match_task_selected_facts": all(
                                   item["all_result_inputs_match_task_selected_facts"] for item in source_link_checks.values()),
                               "selected_fact_count": len(selected_facts),
                               "facts_unavailable_or_unverified_by_known_at": future_fact_count,
                               "modes": mode_values},
            "metric_evaluation_status": "NOT_RUN",
            "metric_evaluation_reason": "该捕获只读取三项已保存 ORM 结果；需冻结集合中的独立答案与事件、逐事实 available_at/known_at、同一回放时点、提示结局及固定配对方案。集合外患儿结果仅作 SIMULATION 执行链路核对。",
        }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "READ_ONLY_CAPTURED", "database": database_name, "database_port": database_port,
                      "classification_task_id": classification["task"]["task_id"], "classification_result_count": len(classification["results"]),
                      "ms_task_id": ms["task"]["task_id"], "ms_result_count": len(ms["results"]),
                      "dynamic_task_id": dynamic["task"]["task_id"], "dynamic_result_count": len(dynamic["results"]),
                      "member_of_frozen_set": payload["cohort_binding"]["member_of_frozen_set"],
                      "metric_evaluation_status": payload["metric_evaluation_status"], "output": str(target)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
