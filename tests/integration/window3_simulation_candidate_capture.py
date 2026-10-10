import json
import os
import urllib.request
import http.cookiejar
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.simulation")
django.setup()

from django.db import connection, transaction
from configurations.models import ConfigVersion
from files.models import FileObject
from identity.models import Patient, PrincipalCredential
from recognition.models import CandidateField, CandidateSet, SourceAnchor
from tasks.models import Task, TaskAttempt


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / ".planning-work/parallel/window3/T55-current-candidate-evidence.json"
CANDIDATE_ID = "394219ed-209d-4879-abab-3a2436226bbe"
PATIENT_ID = "e88faef5-8bf6-4256-b5a5-865c699103d6"
API_ROOT = "http://127.0.0.1:8001"


def api_session():
    password = os.environ["SJIA_SIMULATION_PASSWORD"]
    cookies = http.cookiejar.CookieJar()
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    with client.open(f"{API_ROOT}/api/auth/bootstrap") as response:
        bootstrap = json.load(response)
    login = urllib.request.Request(
        f"{API_ROOT}/api/auth/login",
        data=json.dumps({"username": "simulation-family", "password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-CSRFToken": bootstrap["csrf_token"]},
    )
    with client.open(login) as response:
        if response.status != 200:
            raise RuntimeError("模拟家庭账号登录失败")
    with client.open(f"{API_ROOT}/api/session") as response:
        session = json.load(response)
    if session.get("audience") != "family":
        raise RuntimeError("API 会话没有 family 权限")
    return client, session["principal_id"]


def read_api(client, path):
    with client.open(f"{API_ROOT}/api/v1{path}") as response:
        return response.status, json.load(response)


def main():
    if connection.settings_dict["NAME"] != "sjia_simulation":
        raise RuntimeError("当前数据库必须为 sjia_simulation")
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT current_database()")
            database_name = cursor.fetchone()[0]
        candidate = CandidateSet.objects.get(id=CANDIDATE_ID, patient_id=PATIENT_ID)
        task = Task.objects.get(id=candidate.task_id, patient_id=PATIENT_ID)
        family_principal_id = PrincipalCredential.objects.get(username="simulation-family").principal_id
        if task.actor_id != family_principal_id:
            raise RuntimeError("模型任务创建账号与 simulation-family 不一致")
        attempt = TaskAttempt.objects.get(task_id=task.id, attempt_number=1)
        source_file_id = task.input_refs["file_id"]
        source_file = FileObject.objects.get(id=source_file_id, patient_id=PATIENT_ID, purpose="original")
        fields = []
        for item in CandidateField.objects.filter(candidate_set_id=candidate.id).order_by("field_path"):
            anchor = SourceAnchor.objects.get(id=item.anchor_id, patient_id=PATIENT_ID)
            fields.append({
                "candidate_field_id": str(item.id),
                "field_path": item.field_path,
                "raw_text": item.raw_text,
                "proposed_field": item.proposed_field,
                "source_anchor_id": str(anchor.id),
                "source_anchor_text": anchor.original_text,
                "source_page_id": str(anchor.page_id),
            })
        provider = ConfigVersion.objects.get(id=task.input_refs["provider_config_id"])
        if str(provider.id) != attempt.usage_evidence["provider_config_id"]:
            raise RuntimeError("任务与计价记录引用不同 Provider 配置")
        price_ref = task.input_refs["scope"]["configuration"]["evidence"]["pricing_evidence_ref"]
        pricing = {
            "provider_config_id": str(provider.id),
            "provider_checksum": provider.checksum,
            "input_per_million": provider.payload["input_per_million"],
            "output_per_million": provider.payload["output_per_million"],
            "currency": provider.payload["currency"],
            "evidence_ref": f"urn:sjia:source-revision:{price_ref['source_revision_id']}",
            "evidence_checksum": price_ref["checksum"],
            "evidence_matches_provider_payload": f"urn:sjia:source-revision:{price_ref['source_revision_id']}" == provider.payload["pricing_evidence_ref"],
        }
        candidate_payload = {
            "candidate_set_id": str(candidate.id),
            "patient_id": str(candidate.patient_id),
            "file_group_id": str(candidate.file_group_id),
            "file_group_version": str(candidate.file_group_version),
            "task_id": str(candidate.task_id),
            "provider": candidate.provider,
            "model_version": candidate.model_version,
            "provider_request_id": candidate.provider_request_id,
            "schema_version": candidate.schema_version,
            "status": candidate.status,
            "source_file_id": str(source_file.id),
            "source_file_sha256": source_file.checksum,
            "fields": fields,
        }
        task_payload = {"task_id": str(task.id), "actor_id": str(task.actor_id), "family_principal_id": str(family_principal_id), "status": task.status}
        attempt_payload = {
            "attempt_id": str(attempt.id),
            "provider_request_id": attempt.provider_request_id,
            "started_at": attempt.started_at.isoformat(),
            "finished_at": attempt.finished_at.isoformat(),
            "status": attempt.status,
            "usage": attempt.usage_evidence["usage"],
        }
    client, api_principal_id = api_session()
    if api_principal_id != str(family_principal_id):
        raise RuntimeError("API 登录账号与模拟模型任务创建账号不一致")
    candidate_status, candidate_view = read_api(client, f"/candidate-sets/{CANDIDATE_ID}")
    config_status, field_config = read_api(client, f"/patients/{PATIENT_ID}/field-config?kind=report")
    if candidate_status != 200 or candidate_view["status"] != candidate_payload["status"]:
        raise RuntimeError("API 候选读取与数据库记录不一致")
    api_evidence = {
        "candidate_read_status": candidate_status,
        "candidate_read_view": candidate_view,
        "field_config_http_status": config_status,
        "field_config_state": field_config["state"],
        "field_config_reason": field_config["reason_code"],
        "field_config_version": field_config["config_version"],
        "family_principal_id": api_principal_id,
    }
    independent_answers = {
        "/fields/1": {
            "name": "Ferritin", "comparator": "eq", "value": "900.00", "unit": "ng/mL",
            "negation": False, "medical_time": "unknown", "context": "SIMULATION laboratory report",
            "source": "Ferritin: 900.00 ng/mL",
        },
        "/fields/2": {
            "name": "CRP", "comparator": "eq", "value": "15.0", "unit": "mg/L",
            "negation": False, "medical_time": "unknown", "context": "SIMULATION laboratory report",
            "source": "CRP: 15.0 mg/L",
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "data_mode": "SIMULATION",
        "capture_scope": "candidate_only; read-only business database capture",
        "database_name": database_name,
        "database_host": connection.settings_dict["HOST"],
        "database_port": str(connection.settings_dict["PORT"]),
        "api": api_evidence,
        "candidate": candidate_payload,
        "task": task_payload,
        "attempt": attempt_payload,
        "pricing": pricing,
        "independent_answer_origin": "seed_simulation_batches.py index=0 generated source text, separately maintained in this capture script",
        "independent_simulation_answers": independent_answers,
        "adoption_status": "NOT_RUN",
        "saved_reread_status": "NOT_RUN",
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "SIMULATION_CANDIDATE_CAPTURED",
        "candidate_set_id": candidate_payload["candidate_set_id"],
        "provider_request_id": candidate_payload["provider_request_id"],
        "api_candidate_read_status": candidate_status,
        "field_config_state": api_evidence["field_config_state"],
        "field_config_reason": api_evidence["field_config_reason"],
        "output": str(OUTPUT),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
