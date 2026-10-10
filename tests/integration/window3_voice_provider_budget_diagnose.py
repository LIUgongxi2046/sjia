import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.simulation")
django.setup()

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from archives.models import ArchivePageAttempt
from configurations.models import ConfigVersion
from family_content.models import EnvironmentGate
from recognition.models import VoiceInput
from tasks.models import Task, TaskAttempt


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = ROOT / ".planning-work/parallel/window3/T55-provider-budget-diagnosis.json"
VOICE_INPUT_ID = "6a85134c-3bdd-4d87-b161-5455d038bc0a"


def main():
    database = connection.settings_dict
    if (database["NAME"], database["PORT"]) != ("sjia_simulation", "5433"):
        raise RuntimeError("预算诊断只允许读取 sjia_simulation:5433")
    now = timezone.now()
    budget_timezone = ZoneInfo(settings.TIME_ZONE)
    budget_date = now.astimezone(budget_timezone).date()
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SHOW search_path")
            search_path = cursor.fetchone()[0]
        if "sjia" not in search_path.split(","):
            raise RuntimeError("预算诊断没有连接 sjia schema")
        voice_input = VoiceInput.objects.select_related("patient", "actor").get(id=VOICE_INPUT_ID)
        gate = EnvironmentGate.objects.get(environment=settings.SJIA_ENVIRONMENT)
        runtime = ConfigVersion.objects.get(id=gate.runtime_config_id, kind="provider",
            schema_version="backend-runtime-1")
        voice_entries = [item for item in runtime.payload["providers"]
            if item.get("kind") == "voice" and item.get("enabled")]
        if len(voice_entries) != 1:
            raise RuntimeError("当前 runtime 没有唯一启用的语音 Provider")
        provider_entry = voice_entries[0]
        billing_entries = [item for item in runtime.payload["providers"] if item.get("enabled")
            and item.get("billing_scope_key") == provider_entry.get("billing_scope_key")]
        config_ids = [item["config_id"] for item in billing_entries]
        billing_providers = list(ConfigVersion.objects.filter(id__in=config_ids, kind="provider").order_by("id"))
        if len(billing_providers) != len(set(config_ids)):
            raise RuntimeError("同一 billing_scope 存在缺失的 Provider 配置")
        provider = ConfigVersion.objects.get(id=provider_entry["config_id"], kind="provider",
            schema_version="provider-1")
        config_keys = sorted({item.config_key for item in billing_providers})
        historical_ids = [str(item) for item in ConfigVersion.objects.filter(
            config_key__in=config_keys, kind="provider").values_list("id", flat=True)]
        tasks = list(Task.objects.filter(input_refs__provider_config_id__in=historical_ids,
            input_refs__environment=settings.SJIA_ENVIRONMENT).order_by("created_at", "id"))
        task_rows = []
        for task in tasks:
            task_attempts = list(TaskAttempt.objects.filter(task_id=task.id).order_by("attempt_number"))
            archive_attempts = list(ArchivePageAttempt.objects.filter(task_id=task.id).order_by("attempt_number"))
            included = task.created_at.astimezone(budget_timezone).date() == budget_date or task.status in (
                "accepted", "queued", "running", "cancel_requested")
            task_rows.append({
                "task_id": str(task.id),
                "task_type": task.task_type,
                "schema_version": task.schema_version,
                "status": task.status,
                "created_at": task.created_at.isoformat(),
                "budget_date_included": included,
                "provider_config_id": task.input_refs.get("provider_config_id"),
                "environment": task.input_refs.get("environment"),
                "input_sha256": task.input_sha256,
                "reserved_cost": task.input_refs.get("reserved_cost"),
                "raw_response_file_id_in_input_refs": task.input_refs.get("raw_response_file_id"),
                "attempts": [{
                    "attempt_id": str(attempt.id),
                    "attempt_number": attempt.attempt_number,
                    "status": attempt.status,
                    "started_at": attempt.started_at.isoformat(),
                    "finished_at": attempt.finished_at.isoformat() if attempt.finished_at else None,
                    "provider_request_id": attempt.provider_request_id,
                    "error_code": attempt.error_code,
                    "usage_evidence": attempt.usage_evidence,
                } for attempt in task_attempts],
                "archive_page_attempts": [{
                    "archive_page_attempt_id": str(attempt.id),
                    "page_id": str(attempt.page_id),
                    "task_attempt_id": str(attempt.task_attempt_id),
                    "attempt_number": attempt.attempt_number,
                    "status": attempt.status,
                    "started_at": attempt.started_at.isoformat(),
                    "finished_at": attempt.finished_at.isoformat() if attempt.finished_at else None,
                    "provider_request_id": attempt.provider_request_id,
                    "cost": str(attempt.cost) if attempt.cost is not None else None,
                    "currency": attempt.currency,
                    "error_code": attempt.error_code,
                    "usage_evidence": attempt.usage_evidence,
                } for attempt in archive_attempts],
            })
        report = {
            "data_mode": "SIMULATION",
            "evidence_scope": "SIMULATION_PROVIDER_BUDGET_DIAGNOSIS",
            "database": {"name": database["NAME"], "schema": "sjia", "port": database["PORT"]},
            "captured_at": now.isoformat(),
            "budget_date": budget_date.isoformat(),
            "budget_timezone": settings.TIME_ZONE,
            "environment_gate": {"environment": gate.environment, "runtime_config_id": str(runtime.id)},
            "runtime_config": {"id": str(runtime.id), "version": str(runtime.version),
                "checksum": runtime.checksum, "voice_billing_scope_entry": provider_entry,
                "billing_scope_entries": billing_entries},
            "provider_config": {"id": str(provider.id), "config_key": provider.config_key,
                "version": str(provider.version), "checksum": provider.checksum, "payload": provider.payload},
            "billing_scope_provider_configs": [{"id": str(item.id), "config_key": item.config_key,
                "version": str(item.version), "checksum": item.checksum, "payload": item.payload}
                for item in billing_providers],
            "billing_scope_config_keys": config_keys,
            "provider_config_ids_in_billing_scope": historical_ids,
            "voice_input": {"id": str(voice_input.id), "patient_id": str(voice_input.patient_id),
                "actor_id": str(voice_input.actor_id), "status": voice_input.status,
                "status_version": voice_input.status_version, "task_id": str(voice_input.task_id) if voice_input.task_id else None,
                "raw_response_file_id": str(voice_input.raw_response_file_id) if voice_input.raw_response_file_id else None,
                "purpose_copy_id": str(voice_input.purpose_copy_id) if voice_input.purpose_copy_id else None},
            "tasks": task_rows,
            "task_count": len(task_rows),
            "task_attempt_count": sum(len(item["attempts"]) for item in task_rows),
            "archive_page_attempt_count": sum(len(item["archive_page_attempts"]) for item in task_rows),
            "unknown_cost_candidates": [
                {"task_id": row["task_id"], "attempt_id": attempt["attempt_id"],
                 "provider_request_id": attempt["provider_request_id"], "status": attempt["status"]}
                for row in task_rows if row["budget_date_included"]
                for attempt in row["attempts"] if attempt["provider_request_id"] is not None
                and (attempt["usage_evidence"] or {}).get("actual_cost") is None],
            "unknown_archive_cost_candidates": [
                {"task_id": row["task_id"], "archive_page_attempt_id": attempt["archive_page_attempt_id"],
                 "task_attempt_id": attempt["task_attempt_id"], "provider_request_id": attempt["provider_request_id"],
                 "status": attempt["status"]}
                for row in task_rows if row["budget_date_included"]
                for attempt in row["archive_page_attempts"] if attempt["provider_request_id"] is not None
                and attempt["cost"] is None],
        }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reread = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    if reread != report:
        raise RuntimeError("预算诊断报告保存重读不一致")
    print(json.dumps({"status": "SIMULATION_PROVIDER_BUDGET_DIAGNOSED", "output": str(OUTPUT_PATH),
        "task_count": report["task_count"], "task_attempt_count": report["task_attempt_count"],
        "archive_page_attempt_count": report["archive_page_attempt_count"],
        "unknown_cost_candidate_count": len(report["unknown_cost_candidates"]),
        "unknown_archive_cost_candidate_count": len(report["unknown_archive_cost_candidates"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
