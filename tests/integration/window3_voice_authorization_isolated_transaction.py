import hashlib
import json
import os
import platform
from pathlib import Path
from unittest import TestCase
from uuid import uuid4

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.simulation")
django.setup()

from django.conf import settings
from django.db import connection, transaction
from django.test import Client
from django.utils import timezone
from django import get_version as django_version

from configurations.models import ConfigVersion
from files.models import FileGroup, FileObject
from files.purpose import checked_bytes
from files.service import FileRejected
from identity.models import Patient, Principal
from recognition.models import VoiceInput
from recognition.voice_input_service import create_voice_input, prepare_voice_processing
from sources.models import DataUseAuthorization, SourceRevision
from sources.service import create_use_authorization
from tasks.models import OutboxEvent, Task, TaskAttempt


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / ".simulation-work/t55-voice-fixture.json"
OUTPUT_PATH = ROOT / ".planning-work/parallel/window3/T55-authorization-revocation-service-roundtrip.json"


def database_identity():
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_schema(), inet_server_port()")
        database_name, schema_name, port = cursor.fetchone()
    return {"database": database_name, "schema": schema_name, "port": port}


def row_counts():
    return {
        "data_use_authorizations": DataUseAuthorization.objects.count(),
        "voice_inputs": VoiceInput.objects.count(),
        "tasks": Task.objects.count(),
        "outbox_events": OutboxEvent.objects.count(),
        "task_attempts": TaskAttempt.objects.count(),
    }


def authenticated_client(principal, audience):
    client = Client()
    session = client.session
    session["_sjia_principal_id"] = str(principal.id)
    session["_sjia_auth_epoch"] = principal.auth_epoch
    session["_sjia_audience"] = audience
    session.save()
    return client, {"principal_id": str(principal.id), "audience": audience,
        "session_key": session.session_key}


def main():
    if OUTPUT_PATH.exists():
        raise RuntimeError("撤销事务验证证据已存在，拒绝覆盖")
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if fixture.get("mode") != "SIMULATION_TEXT_ONLY":
        raise RuntimeError("模拟语音 fixture 未标记 SIMULATION_TEXT_ONLY")
    db_identity = database_identity()
    if db_identity != {"database": "sjia_simulation", "schema": "sjia", "port": 5433}:
        raise RuntimeError(f"模拟数据库身份不符：{db_identity}")

    patient = Patient.objects.get(id=fixture["patient_id"])
    family = Principal.objects.get(id=fixture["owner_id"], account_status="active")
    reviewer = Principal.objects.get(id=fixture["reviewer_id"], account_status="active")
    original = FileObject.objects.get(id=fixture["original_file_id"], patient=patient)
    original_group = FileGroup.objects.get(id=original.file_group_id, patient=patient)
    source_revision = SourceRevision.objects.select_related("source").get(id=fixture["source_revision_id"])
    field_config = ConfigVersion.objects.get(id=fixture["field_config_id"], kind="content",
        schema_version="field-config-1", status="enabled", payload__kind="observation")
    transcript = fixture["confirmed_text"]
    transcript_sha256 = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
    if (original.media_type != "text/plain" or checked_bytes(original).decode("utf-8") != transcript
            or original.checksum != transcript_sha256 or source_revision.status != "approved"
            or source_revision.source.status != "approved" or "external_recognition" not in source_revision.allowed_use
            or original_group.source_revision_id != source_revision.id):
        raise RuntimeError("只读模拟原件或来源版本核验失败")

    before = row_counts()
    simulation_auth_id = None
    simulation_voice_id = None
    revoke_result = None
    service_result = None
    process_result = None
    inside_counts = None
    request_id = str(uuid4())

    with transaction.atomic():
        family_client, family_session = authenticated_client(family, "family")
        support_principal = Principal.objects.get(id="225d95ea-9d19-4642-a4df-b569acdea79f",
            account_status="active")
        support_client, support_session = authenticated_client(support_principal, "support")
        if family_session.get("principal_id") != str(family.id):
            raise RuntimeError("家庭模拟会话主体与 fixture 不一致")
        if support_session.get("principal_id") != "225d95ea-9d19-4642-a4df-b569acdea79f":
            raise RuntimeError("支持模拟会话主体与既有模拟账号不一致")
        authorization = create_use_authorization(patient=patient, source_revision=source_revision,
            purpose="external_recognition", environment=settings.SJIA_ENVIRONMENT,
            evidence_ref="SIMULATION_TEST_ONLY:window3_voice_authorization_isolated_transaction",
            approved_by=reviewer)
        voice_input = create_voice_input(patient=patient, actor=family, auth_epoch=family.auth_epoch,
            original_file_id=original.id, request={
                "request_id": request_id,
                "authorization_id": str(authorization.id),
                "field_config_id": str(field_config.id),
                "confirmed_text": transcript,
                "transcript_confirmed": True,
                "input_mode": "SIMULATION_TEXT_ONLY",
                "transcription_evidence_id": None,
            })
        if voice_input.status != "ready" or voice_input.use_authorization_id != authorization.id:
            raise RuntimeError("新建 VoiceInput 未绑定独立授权")
        simulation_auth_id = str(authorization.id)
        simulation_voice_id = str(voice_input.id)

        revoke_response = support_client.post(
            f"/api/support/use-authorizations/{authorization.id}/revoke", {}, format="json")
        revoke_body = revoke_response.json()
        authorization.refresh_from_db()
        if (revoke_response.status_code != 200 or revoke_body.get("authorization_id") != str(authorization.id)
                or authorization.revoked_at is None):
            raise RuntimeError(f"真实模拟授权撤销 API 未成功：HTTP {revoke_response.status_code}，{revoke_body}")
        revoke_result = {"http_status": revoke_response.status_code,
            "authorization_id": str(authorization.id), "revoked_at": authorization.revoked_at.isoformat(),
            "response": revoke_body}

        service_request = {
            "expected_status_version": str(voice_input.status_version),
            "purpose_copy_id": str(fixture["purpose_copy_id"]),
            "provider_config_id": str(fixture["provider_config_id"]),
        }
        with TestCase().assertRaises(FileRejected) as rejection:
            prepare_voice_processing(voice_input_id=voice_input.id, actor=family,
                auth_epoch=family.auth_epoch, request=service_request)
        if rejection.exception.reason_code != "PURPOSE_AUTHORIZATION_UNAVAILABLE":
            raise RuntimeError(f"撤销后的实际服务返回非预期原因：{rejection.exception.reason_code}")
        service_result = {"rejected": True, "reason_code": rejection.exception.reason_code,
            "voice_input_id": str(voice_input.id)}

        process_response = family_client.post(
            f"/api/v2/patients/{patient.id}/voice-inputs/{voice_input.id}/process", {
                **service_request,
            }, content_type="application/json")
        process_body = process_response.json()
        if process_response.status_code != 403 or process_body.get("code") != "PURPOSE_AUTHORIZATION_UNAVAILABLE":
            raise RuntimeError(f"撤销后的语音处理未按预期拒绝：HTTP {process_response.status_code}，{process_body}")
        voice_input.refresh_from_db()
        inside_counts = row_counts()
        if (voice_input.status != "ready" or voice_input.task_id is not None
                or inside_counts["tasks"] != before["tasks"]
                or inside_counts["outbox_events"] != before["outbox_events"]
                or inside_counts["task_attempts"] != before["task_attempts"]):
            raise RuntimeError("撤销拒绝后语音输入状态或任务事件数量发生非预期变化")
        process_result = {"http_status": process_response.status_code, "response": process_body,
            "voice_status_after_rejection": voice_input.status, "task_id": None,
            "new_task_count": inside_counts["tasks"] - before["tasks"],
            "new_event_count": inside_counts["outbox_events"] - before["outbox_events"],
            "new_attempt_count": inside_counts["task_attempts"] - before["task_attempts"]}
        transaction.set_rollback(True)

    after = row_counts()
    if after != before:
        raise RuntimeError(f"数据库事务回滚后行数不一致：before={before}, after={after}")
    if (DataUseAuthorization.objects.filter(id=simulation_auth_id).exists()
            or VoiceInput.objects.filter(id=simulation_voice_id).exists()):
        raise RuntimeError("事务回滚后新建的模拟授权或 VoiceInput 仍存在")
    retained_authorization = DataUseAuthorization.objects.get(id=fixture["authorization_id"])
    if retained_authorization.revoked_at is not None:
        raise RuntimeError("共享原授权状态意外发生变化")

    report = {
        "data_mode": "SIMULATION",
        "evidence_scope": "SIMULATION_TEXT_ONLY",
        "status": "SIMULATION_REVOCATION_REJECTED_AND_TRANSACTION_ROLLED_BACK",
        "created_at": timezone.now().isoformat(),
        "database": db_identity,
        "runtime": {"python": platform.python_version(), "django": django_version()},
        "fixture": {"patient_id": str(patient.id), "family_principal_id": str(family.id),
            "source_revision_id": str(source_revision.id), "original_file_id": str(original.id),
            "original_checksum": original.checksum, "field_config_id": str(field_config.id)},
        "dedicated_rows": {"data_use_authorization_id": simulation_auth_id,
            "voice_input_id": simulation_voice_id,
            "request_id": request_id,
            "evidence_ref": "SIMULATION_TEST_ONLY:window3_voice_authorization_isolated_transaction"},
        "support_session": {"principal_id": support_session["principal_id"],
            "audience": support_session["audience"]},
        "family_session": {"principal_id": family_session["principal_id"],
            "audience": family_session["audience"]},
        "before_counts": before,
        "inside_transaction_counts": inside_counts,
        "authorization_revoke_api": revoke_result,
        "voice_processing_service": service_result,
        "voice_process_api": process_result,
        "after_rollback_counts": after,
        "new_rows_absent_after_rollback": True,
        "shared_authorization_unchanged": True,
        "transaction_rollback_verified": True,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reread = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    if reread != report:
        raise RuntimeError("模拟授权撤销事务证据保存后重读不一致")
    print(json.dumps({"status": report["status"], "evidence": str(OUTPUT_PATH.relative_to(ROOT)),
        "sha256": hashlib.sha256(OUTPUT_PATH.read_bytes()).hexdigest(),
        "revoke_http": revoke_result["http_status"], "process_http": process_result["http_status"],
        "process_code": process_result["response"].get("code"),
        "rollback_verified": report["transaction_rollback_verified"],
        "saved_and_reread": reread == report}, ensure_ascii=False))


main()
