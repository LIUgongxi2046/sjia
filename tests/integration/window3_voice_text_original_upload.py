import hashlib
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import uuid4

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.simulation")
django.setup()

from django.db import connection
from files.models import FileObject
from files.purpose import checked_bytes

from window3_voice_input_adoption_roundtrip import api_session, get_json, request_json


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / ".simulation-work/t55-voice-fixture.json"
OUTPUT_PATH = ROOT / ".planning-work/parallel/window3/T55-new-text-original-upload.json"
API_BASE = "http://127.0.0.1:8001"


def main():
    if OUTPUT_PATH.exists():
        raise RuntimeError("新文字原件已有本次运行证据，拒绝重复上传")
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if fixture.get("mode") != "SIMULATION_TEXT_ONLY":
        raise RuntimeError("上传输入没有明确的模拟纯文字标识")
    patient_id = fixture["patient_id"]
    text = fixture["confirmed_text"] + "\nSIMULATION 新增确认文字：2026年10月9日08时00分，补充记录仍需本人核对。"
    data = text.encode("utf-8")
    checksum = hashlib.sha256(data).hexdigest()
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_schema(), inet_server_port()")
        database_name, schema_name, port = cursor.fetchone()
    if (database_name, schema_name, port) != ("sjia_simulation", "sjia", 5433):
        raise RuntimeError("模拟数据库身份与授权目标不一致")
    client, csrf, session = api_session(API_BASE, fixture["family_username"], "SJIA_SIMULATION_PASSWORD")
    if session.get("audience") != "family" or session.get("principal_id") != fixture["owner_id"]:
        raise RuntimeError("API 会话没有绑定准确模拟家庭主体")
    options_status, options = get_json(client, API_BASE,
        f"/api/v2/patients/{patient_id}/archive-upload-options")
    if options_status != 200 or options.get("patient_id") != patient_id:
        raise RuntimeError(f"原件上传 options API 失败：HTTP {options_status}")
    if "text/plain" not in options.get("media_types", []) or len(data) > options["max_upload_bytes"]:
        raise RuntimeError("当前批准上传配置不接受该 text/plain 原件")
    authorizations = [item for item in options["authorizations"]
        if item.get("authorization_id") == fixture["family_authorization_id"]
        and item.get("source_revision_id") == fixture["source_revision_id"]]
    if len(authorizations) != 1:
        raise RuntimeError("新文字原件没有准确匹配的来源授权")

    group_id, upload_id = str(uuid4()), str(uuid4())
    create_status, group = request_json(client, API_BASE, "POST", f"/api/patients/{patient_id}/uploads",
        {"authorization_id": fixture["family_authorization_id"], "client_group_id": group_id}, csrf)
    if create_status != 201 or group.get("group_id") != group_id:
        raise RuntimeError("原件上传组创建回执不匹配")
    query = urllib.parse.urlencode({"checksum": checksum, "size": len(data), "media_type": "text/plain", "ordinal": 1})
    put = urllib.request.Request(f"{API_BASE}/api/uploads/{group_id}/files/{upload_id}?{query}",
        data=data, headers={"Content-Type": "application/octet-stream", "X-CSRFToken": csrf}, method="PUT")
    with client.open(put) as response:
        upload_status = response.status
        upload_receipt = json.load(response)
    if upload_status != 201 or upload_receipt.get("checksum") != checksum or upload_receipt.get("size_bytes") != len(data):
        raise RuntimeError("text/plain 原件上传回执摘要不匹配")
    complete_status, completed = request_json(client, API_BASE, "POST",
        f"/api/uploads/{group_id}/complete", {}, csrf)
    if complete_status != 200 or completed.get("status") != "available":
        raise RuntimeError("原件上传组没有完成并进入 available 状态")
    read_status, read_group = get_json(client, API_BASE, f"/api/uploads/{group_id}")
    if (read_status != 200 or read_group.get("group_id") != group_id
            or read_group.get("patient_id") != patient_id or read_group.get("status") != "available"
            or read_group.get("authorization_id") != fixture["family_authorization_id"]
            or read_group.get("source_revision_id") != fixture["source_revision_id"]
            or len(read_group.get("files", [])) != 1):
        raise RuntimeError("原件上传组保存后重新读取不匹配")
    file_row = read_group["files"][0]
    if (file_row.get("file_id") != upload_receipt.get("file_id") or file_row.get("media_type") != "text/plain"
            or file_row.get("checksum") != checksum or file_row.get("size_bytes") != len(data)
            or file_row.get("purpose") != "original" or file_row.get("page_count", 0) < 1):
        raise RuntimeError("原件目录重读字段与上传回执不一致")
    content_request = urllib.request.Request(f"{API_BASE}/api/files/{file_row['file_id']}/content",
        headers={"Accept": "text/plain,application/json"}, method="GET")
    with client.open(content_request) as response:
        content_status = response.status
        read_bytes = response.read()
    if (content_status != 200 or read_bytes != data or hashlib.sha256(read_bytes).hexdigest() != checksum):
        raise RuntimeError("新文字原件内容重新读取不一致")

    original = FileObject.objects.get(id=file_row["file_id"], patient_id=patient_id)
    if (str(original.file_group_id) != group_id or original.media_type != "text/plain"
            or original.checksum != checksum or checked_bytes(original) != data):
        raise RuntimeError("新文字原件数据库保存值与 API 重读不一致")
    report = {"data_mode": "SIMULATION", "evidence_scope": "SIMULATION_NEW_TEXT_ORIGINAL",
        "status": "SIMULATION_ORIGINAL_SAVED_AND_REREAD", "api_base": API_BASE,
        "database": {"name": database_name, "schema": schema_name, "port": port},
        "patient_id": patient_id, "principal_id": session["principal_id"],
        "source_revision_id": fixture["source_revision_id"],
        "authorization_id": fixture["family_authorization_id"], "upload_group_id": group_id,
        "upload_id": upload_id, "file_id": str(original.id), "checksum": checksum,
        "size_bytes": len(data), "text_differs_from_precreated_original": checksum != fixture["transcript_sha256"],
        "api_statuses": {"options": options_status, "create_group": create_status,
            "upload_file": upload_status, "complete_group": complete_status,
            "read_group": read_status, "read_original_content": content_status},
        "page_count": file_row["page_count"], "purpose_copy_status": "AWAITING_REVIEW",
        "voice_input_processing": "NOT_RUN"}
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if json.loads(OUTPUT_PATH.read_text(encoding="utf-8")) != report:
        raise RuntimeError("新文字原件模拟报告保存重读不一致")
    print(json.dumps({key: report[key] for key in ("status", "upload_group_id", "file_id", "checksum",
        "text_differs_from_precreated_original", "api_statuses")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
