import hashlib
import http.cookiejar
import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import django


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.simulation")
django.setup()

from django.db import connection, transaction
from configurations.models import ConfigVersion
from files.models import FileObject
from files.purpose import checked_bytes
from records.models import RecordVersion
from sources.models import DataUseAuthorization, SourceRevision
from tasks.models import Task, TaskAttempt


ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = ROOT / ".planning-work/parallel/window3/T55-voice-adoption-plan.json"
SOURCE_PATH = ROOT / ".planning-work/parallel/window3/T55-voice-simulation-text-only.json"
OUTPUT = ROOT / ".planning-work/parallel/window3/T55-voice-adoption-roundtrip-evidence.json"
API_RESPONSE_OUTPUT = ROOT / ".planning-work/parallel/window3/T55-voice-adoption-api-response.json"


def required(value, name):
    if value is None or value == "" or value == [] or value == {}:
        raise RuntimeError(f"联调计划缺少准确参数：{name}")
    return value


def select(value, path):
    current = value
    for part in path.split("."):
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise RuntimeError(f"API 响应缺少准确字段：{path}")
    return current


def select_if_present(value, path):
    current = value
    for part in path.split("."):
        if isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


def format_values(value, variables):
    if isinstance(value, str):
        return value.format_map(variables)
    if isinstance(value, list):
        return [format_values(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: format_values(item, variables) for key, item in value.items()}
    return value


def get_json(client, api_base, path):
    with client.open(f"{api_base}{path}") as response:
        return response.status, json.load(response)


def post_json(client, api_base, path, payload, csrf, headers=None):
    return request_json(client, api_base, "POST", path, payload, csrf, headers)


def request_json(client, api_base, method, path, payload, csrf, headers=None):
    request_headers = {"Content-Type": "application/json", "X-CSRFToken": csrf}
    request_headers.update(headers or {})
    request = urllib.request.Request(f"{api_base}{path}", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=request_headers, method=method)
    with client.open(request) as response:
        return response.status, json.load(response)


def api_session(api_base, username, password_env):
    password = os.environ[password_env]
    cookies = http.cookiejar.CookieJar()
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies), JsonHTTPStatusProcessor())
    with client.open(f"{api_base}/api/auth/bootstrap") as response:
        bootstrap = json.load(response)
    login = urllib.request.Request(
        f"{api_base}/api/auth/login",
        data=json.dumps({"username": username, "password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-CSRFToken": bootstrap["csrf_token"]},
    )
    with client.open(login) as response:
        if response.status != 200:
            raise RuntimeError("模拟家庭账号登录失败")
    with client.open(f"{api_base}/api/session") as response:
        session = json.load(response)
    if session.get("audience") != "family":
        raise RuntimeError("API 会话没有 family 权限")
    return client, bootstrap["csrf_token"], session


class JsonHTTPStatusProcessor(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        return response

    https_response = http_response


def compare_selected_values(left, right, selectors, name):
    for result_name, selector in required(selectors, name).items():
        if select(left, selector) != select(right, selector):
            raise RuntimeError(f"相同请求重试返回的已保存结果不一致：{result_name}")


def verify_selected_expected(response, selectors, expected_values, name):
    selectors = required(selectors, f"{name}.selectors")
    expected_values = required(expected_values, f"{name}.expected")
    if set(selectors) != set(expected_values):
        raise RuntimeError(f"{name} 选择器与预期字段集合不一致")
    for result_name, selector in selectors.items():
        if select(response, selector) != expected_values[result_name]:
            raise RuntimeError(f"{name} 的 {result_name} 与预期不一致")


def task_attempt_snapshot(task_ids):
    return list(TaskAttempt.objects.filter(task_id__in=task_ids).order_by("task_id", "attempt_number").values(
        "id", "task_id", "attempt_number", "provider_request_id", "started_at", "finished_at", "status", "usage_evidence"))


def verify_terminal_probe(client, api_base, probe_name, plan, base_variables, patient_id, principal_id):
    if not plan or not plan.get("task_id"):
        return {"status": "NOT_RUN"}
    task_id = str(plan["task_id"])
    voice_input_id = str(required(plan.get("voice_input_id"), "terminal_probe.voice_input_id"))
    if task_id == base_variables["task_id"] or voice_input_id == base_variables["voice_input_id"]:
        raise RuntimeError("取消或模型结果未明检查必须引用独立的 VoiceInput/Task")
    task = Task.objects.filter(id=task_id, patient_id=patient_id, actor_id=principal_id).first()
    if task is None:
        raise RuntimeError("终态检查 Task 没有绑定同一模拟患儿和家庭主体")
    variables = {**base_variables, "task_id": task_id, "voice_input_id": voice_input_id}
    task_read_path = format_values(required(plan.get("task_read_path"), "terminal_probe.task_read_path"), variables)
    before_task_status, before_task_response = get_json(client, api_base, task_read_path)
    if before_task_status != 200 or select(before_task_response, required(plan.get("task_status_selector"), "terminal_probe.task_status_selector")) != plan.get("expected_task_before_status"):
        raise RuntimeError("终态检查 Task 初始状态与计划不符")
    if probe_name == "cancel" and not (plan.get("cancel_action") and plan["cancel_action"].get("path")):
        raise RuntimeError("取消场景缺少真实取消提交路径")
    if probe_name == "model_result_unknown" and plan.get("cancel_action") and plan["cancel_action"].get("path"):
        raise RuntimeError("模型结果未明场景只允许读取，不得提交取消或重新处理")
    if plan.get("cancel_action") and plan["cancel_action"].get("path"):
        action = plan["cancel_action"]
        action_path = format_values(required(action.get("path"), "terminal_probe.cancel_action.path"), variables)
        action_payload = format_values(required(action.get("payload"), "terminal_probe.cancel_action.payload"), variables)
        action_status, action_result = request_json(client, api_base, required(action.get("method"), "terminal_probe.cancel_action.method"), action_path, action_payload,
            variables["csrf_token"], {})
        if action_status != action.get("success_status"):
            raise RuntimeError("取消请求没有返回计划状态")
    else:
        action_status, action_result = None, None
    voice_read_path = format_values(required(plan.get("voice_input_read_path"), "terminal_probe.voice_input_read_path"), variables)
    voice_status, voice_response = get_json(client, api_base, voice_read_path)
    if voice_status != 200:
        raise RuntimeError("终态检查 VoiceInput API 读取失败")
    if str(select(voice_response, required(plan.get("voice_task_id_selector"), "terminal_probe.voice_task_id_selector"))) != task_id:
        raise RuntimeError("终态检查 VoiceInput 没有绑定准确 Task")
    if str(select(voice_response, required(plan.get("voice_patient_id_selector"), "terminal_probe.voice_patient_id_selector"))) != str(patient_id):
        raise RuntimeError("终态检查 VoiceInput 患儿身份不一致")
    if (voice_response.get("input_mode") != "SIMULATION_TEXT_ONLY"
            or voice_response.get("transcription_evidence", {}).get("mode") != "SIMULATION_TEXT_ONLY"):
        raise RuntimeError("终态检查 VoiceInput 缺少准确的模拟纯文字来源模式")
    task_status, task_response = get_json(client, api_base, task_read_path)
    if task_status != 200 or select(task_response, required(plan.get("task_status_selector"), "terminal_probe.task_status_selector")) != plan.get("expected_task_status"):
        raise RuntimeError("终态 Task API 状态与计划不符")
    attempt_statuses = list(TaskAttempt.objects.filter(task_id=task_id).order_by("attempt_number").values_list("status", flat=True))
    if attempt_statuses != required(plan.get("attempt_statuses"), "terminal_probe.attempt_statuses"):
        raise RuntimeError("终态 TaskAttempt 状态与计划不符")
    candidate_path = format_values(required(plan.get("candidate_read_path"), "terminal_probe.candidate_read_path"), variables)
    candidate_http_status, candidate = get_json(client, api_base, candidate_path)
    if candidate_http_status != plan.get("candidate_http_status", 200):
        raise RuntimeError("终态候选 API 状态与计划不符")
    if select(candidate, required(plan.get("candidate_status_selector"), "terminal_probe.candidate_status_selector")) != plan.get("candidate_expected_status"):
        raise RuntimeError("取消或结果未明 Task 不应产生可采用的候选")
    record_path = format_values(required(plan.get("record_absence_path"), "terminal_probe.record_absence_path"), variables)
    record_http_status, records = get_json(client, api_base, record_path)
    if record_http_status != plan.get("record_absence_status"):
        raise RuntimeError("终态 Task 的记录查询状态与计划不符")
    items_path = required(plan.get("record_items_path"), "terminal_probe.record_items_path")
    if select(records, items_path) != []:
        raise RuntimeError("取消或结果未明 Task 已产生正式记录")
    return {"status": "SIMULATION_TERMINAL_STATE_READ_AND_VERIFIED", "task_id": task_id,
        "voice_input_id": voice_input_id, "task_status": plan["expected_task_status"],
        "attempt_statuses": attempt_statuses, "cancel_action_status": action_status,
        "cancel_action_response": action_result, "candidate_http_status": candidate_http_status,
        "record_absence_http_status": record_http_status}


def normalize_fields(response, projection):
    rows = select(response, required(projection.get("items_path"), "projection.items_path"))
    if not isinstance(rows, list):
        raise RuntimeError("字段读取路径必须返回列表")
    output = []
    seen = set()
    for row in rows:
        field_path = select(row, required(projection.get("field_path"), "projection.field_path"))
        if field_path in seen:
            raise RuntimeError(f"响应中出现重复字段路径：{field_path}")
        seen.add(field_path)
        values = {}
        field_values = projection.get("values_by_field", {}).get(field_path, projection.get("values"))
        for dimension, selector in required(field_values, f"projection.{field_path}.values").items():
            found, value = select_if_present(row, selector)
            if found:
                values[dimension] = value
        item = {"field_path": field_path, "values": values}
        decision_selector = projection.get("decision")
        if decision_selector:
            found, decision = select_if_present(row, decision_selector)
            if found:
                item["decision"] = decision
        source_selector = projection.get("source_ref")
        if source_selector:
            found, source_ref = select_if_present(row, source_selector)
            if found:
                item["source_ref"] = source_ref
        output.append(item)
    return output


def normalize_voice_candidates(response, projection):
    candidates_path = required(projection.get("candidates_path"), "projection.candidates_path")
    candidates = select(response, candidates_path)
    if not isinstance(candidates, dict):
        raise RuntimeError("VoiceInput candidates 必须是已读取的对象")
    rows = []
    seen = set()
    for item in required(projection.get("field_mappings"), "projection.field_mappings"):
        field_path = required(item.get("field_path"), "projection.field_mappings.field_path")
        if field_path in seen:
            raise RuntimeError("候选投影包含重复 field_path")
        seen.add(field_path)
        values = {}
        for dimension, selector in required(item.get("values"), f"projection.{field_path}.values").items():
            found, value = select_if_present(candidates, selector)
            if found:
                values[dimension] = value
        rows.append({"field_path": field_path, "values": values, "source_ref": None})
    return rows


def main():
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    if plan.get("data_mode") != "SIMULATION" or plan.get("evidence_scope") != "SIMULATION_TEXT_ONLY":
        raise RuntimeError("只允许执行明确标记的模拟纯文字 VoiceInput 流程")
    if plan.get("plan_status") != "READY":
        raise RuntimeError("主窗口尚未提供准确的 VoiceInput 与 Task 接口参数")
    if source.get("data_mode") != "SIMULATION" or source.get("evidence_scope") != "SIMULATION_TEXT_ONLY":
        raise RuntimeError("转写来源没有明确模拟纯文字标记")
    independent = source["independent_answer"]
    if independent.get("answer_origin") != "simulation_manual_independent" or independent.get("derived_from_model_output") is not False:
        raise RuntimeError("独立答案必须单独维护且不得由模型输出生成")
    transcript_sha256 = hashlib.sha256(source["source"]["transcript_text"].encode("utf-8")).hexdigest()
    if transcript_sha256 != source["source"]["transcript_sha256"] or transcript_sha256 != plan["source_sha256"]:
        raise RuntimeError("转写文字摘要与联调计划不一致")
    if OUTPUT.exists():
        raise RuntimeError("本次 VoiceInput 联调已有结果文件，拒绝重复提交")

    database_expected = plan["database_identity"]
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), inet_server_port(), current_user")
        database_name, database_port, database_user = cursor.fetchone()
    database_actual = {"name": database_name, "port": database_port, "user": database_user}
    if database_actual != database_expected:
        raise RuntimeError("连接数据库与计划中的独立模拟数据库身份不一致")

    api_base = required(plan.get("api_base"), "api_base").rstrip("/")
    account = plan["account"]
    client, csrf, session = api_session(api_base, account["username"], account["password_env"])
    voice = plan["voice_input"]
    task_plan = plan["task"]
    record = plan["record"]
    voice_input_id = str(required(voice.get("voice_input_id"), "voice_input.voice_input_id"))
    task_id = str(required(task_plan.get("task_id"), "task.task_id"))
    patient_id = str(required(voice.get("patient_id"), "voice_input.patient_id"))
    task = Task.objects.filter(id=task_id, patient_id=patient_id, actor_id=session["principal_id"]).first()
    if task is None:
        raise RuntimeError("同一患儿、模拟家庭主体与 Task 未形成数据库绑定")
    if task.task_type != required(task_plan.get("expected_task_type"), "task.expected_task_type"):
        raise RuntimeError("Task 类型与主窗口确认的语音任务类型不一致")
    attempts = list(TaskAttempt.objects.filter(task_id=task_id).order_by("attempt_number").values(
        "id", "attempt_number", "provider_request_id", "started_at", "finished_at", "status", "usage_evidence"))
    successful = [item for item in attempts if item["status"] == "completed" and item["provider_request_id"]
        and item["started_at"] and item["finished_at"] and item["usage_evidence"] is not None]
    provider_attempts = [item for item in attempts if item["provider_request_id"]]
    if len(successful) != 1 or len(provider_attempts) != 1:
        raise RuntimeError("专用语音 Task 必须只有一条已完成的 provider Attempt")
    if any(item["started_at"] is None or item["finished_at"] is None for item in provider_attempts):
        raise RuntimeError("Task 中存在缺少实际起止时间的 provider 请求，无法验证授权覆盖范围")

    variables = {"voice_input_id": voice_input_id, "task_id": task_id, "patient_id": patient_id,
        "csrf_token": csrf}
    task_path = format_values(required(task_plan.get("read_path"), "task.read_path"), variables)
    voice_path = format_values(required(voice.get("read_path"), "voice_input.read_path"), variables)
    task_status, task_before = get_json(client, api_base, task_path)
    voice_status, voice_before = get_json(client, api_base, voice_path)
    if task_status != 200 or voice_status != 200:
        raise RuntimeError("Task 或 VoiceInput API 读取失败")
    already_saved = select(voice_before, voice["selectors"]["status"]) == "saved"
    if already_saved:
        first_save = json.loads((ROOT / ".planning-work/parallel/window3/T55-voice-adoption-api-response-first-save.json").read_text(encoding="utf-8"))
        if (first_save.get("data_mode") != "SIMULATION" or first_save.get("evidence_scope") != "SIMULATION_TEXT_ONLY"
                or first_save.get("voice_input_id") != voice_input_id or first_save.get("http_status") != plan["adoption"].get("success_status")
                or select(first_save.get("response_body", {}), "voice_input.saved_record_version_id")
                != select(voice_before, voice["selectors"]["saved_record_version_id"])):
            raise RuntimeError("已保存 VoiceInput 缺少匹配的首次采用响应证据")
    else:
        for selector_name, expected in required(task_plan.get("expected_before"), "task.expected_before").items():
            if select(task_before, selector_name) != expected:
                raise RuntimeError(f"Task 初始值不符合计划：{selector_name}")
        for selector_name, expected in required(voice.get("expected_before"), "voice_input.expected_before").items():
            if select(voice_before, selector_name) != expected:
                raise RuntimeError(f"VoiceInput 初始值不符合计划：{selector_name}")
    if select(voice_before, voice["selectors"]["patient_id"]) != patient_id:
        raise RuntimeError("VoiceInput API 患儿与联调计划不一致")
    if select(voice_before, voice["selectors"]["task_id"]) != task_id:
        raise RuntimeError("VoiceInput API 没有关联同一 Task")
    variables["request_id"] = str(select(voice_before, required(voice["selectors"].get("request_id"), "voice_input.selectors.request_id")))
    transcript = select(voice_before, voice["selectors"]["transcript"])
    transcript_sha256 = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
    if transcript != source["source"]["transcript_text"] or transcript_sha256 != source["source"]["transcript_sha256"]:
        raise RuntimeError("持久化转写文字与模拟输入原文不一致")
    if voice_before["input_mode"] != "SIMULATION_TEXT_ONLY":
        raise RuntimeError("当前联调仅接受明确的 SIMULATION_TEXT_ONLY 输入")
    transcription_evidence = voice_before["transcription_evidence"]
    if (transcription_evidence.get("mode") != "SIMULATION_TEXT_ONLY"
            or transcription_evidence.get("transcript_sha256") != transcript_sha256
            or str(transcription_evidence.get("original_file_id")) != str(voice_before["original_file_id"])):
        raise RuntimeError("模拟转写证明没有绑定准确原件和确认文字摘要")
    original_file = FileObject.objects.get(id=voice_before["original_file_id"], patient_id=patient_id)
    original_bytes = checked_bytes(original_file)
    if (original_file.media_type != "text/plain"
            or original_file.checksum != transcription_evidence.get("original_checksum")
            or hashlib.sha256(original_bytes).hexdigest() != original_file.checksum
            or original_bytes.decode("utf-8", errors="strict") != transcript):
        raise RuntimeError("VoiceInput 原文原件摘要、媒体类型或正文不一致")
    if (str(task.input_refs.get("voice_input_id")) != voice_input_id
            or task.input_refs.get("transcript_sha256") != transcript_sha256
            or str(task.input_refs.get("original_file_id")) != str(original_file.id)):
        raise RuntimeError("Task input_refs 没有绑定准确语音输入、原文摘要与原件")
    authorization = voice["authorization"]
    usage_authorization_id = str(task.input_refs["authorization_id"])
    variables["usage_authorization_id"] = usage_authorization_id
    auth_db = DataUseAuthorization.objects.filter(id=usage_authorization_id, patient_id=patient_id).first()
    if auth_db is None:
        raise RuntimeError("Task 引用的模拟用途授权没有读取到")
    for name in ("purpose", "source_revision_id", "environment", "approved_by_id"):
        expected = required(authorization["expected"].get(name), f"voice_input.authorization.expected.{name}")
        actual = getattr(auth_db, name)
        if name.endswith("_id") and actual is not None:
            actual = str(actual)
        if actual != expected:
            raise RuntimeError(f"模拟数据库用途授权字段与计划不一致：{name}")
    authorized_at = auth_db.approved_at
    api_expires_at = auth_db.valid_until
    api_revoked_at = auth_db.revoked_at
    model_start = min(item["started_at"] for item in provider_attempts)
    model_finish = max(item["finished_at"] for item in provider_attempts)
    if authorized_at.tzinfo is None or authorized_at > model_start:
        raise RuntimeError("语音处理授权时间晚于模型调用开始时间")
    if api_revoked_at is not None:
        if api_revoked_at.tzinfo is None or api_revoked_at <= model_finish:
            raise RuntimeError("模型调用时语音处理授权已撤销或撤销时间无时区")
    if api_expires_at is not None:
        if api_expires_at.tzinfo is None or api_expires_at < model_finish:
            raise RuntimeError("模型调用结束时语音处理授权已经过期")
    if auth_db is None or str(auth_db.approved_by_id) != str(required(authorization["expected"].get("approved_by_id"), "voice_input.authorization.expected.approved_by_id")):
        raise RuntimeError("模拟数据库没有读取到计划中的用途授权批准主体")
    if auth_db.environment != required(authorization["expected"].get("environment"), "voice_input.authorization.expected.environment"):
        raise RuntimeError("数据库中的用途授权环境与模拟环境不一致")
    if auth_db.purpose != authorization["expected"]["purpose"] or str(auth_db.source_revision_id) != str(authorization["expected"]["source_revision_id"]):
        raise RuntimeError("数据库中的用途授权 purpose 或 source revision 与计划不一致")
    if auth_db.approved_at != authorized_at or auth_db.revoked_at is not None:
        raise RuntimeError("数据库中的用途授权批准时间或撤销状态不符合调用时状态")
    if auth_db.valid_until is not None and (auth_db.valid_until.tzinfo is None or auth_db.valid_until < model_finish):
        raise RuntimeError("数据库中的用途授权在模型调用完成前到期")
    source_revision = SourceRevision.objects.filter(id=auth_db.source_revision_id).first()
    if source_revision is None or source_revision.status != "approved":
        raise RuntimeError("用途授权引用的 SourceRevision 在模型调用时未批准")
    allowed_use_selector = authorization.get("source_revision_allowed_use_selector")
    allowed_use = source_revision.allowed_use if allowed_use_selector is None else select(source_revision.allowed_use, allowed_use_selector)
    if allowed_use != required(authorization["expected"].get("source_revision_allowed_use"), "voice_input.authorization.expected.source_revision_allowed_use"):
        raise RuntimeError("SourceRevision allowed_use 没有包含计划中的语音处理用途")
    for name, selector in required(task_plan.get("input_ref_selectors"), "task.input_ref_selectors").items():
        expected_ref = voice_input_id if name == "voice_input_id" else usage_authorization_id
        if str(select(task.input_refs, selector)) != expected_ref:
            raise RuntimeError(f"Task input_refs 没有绑定准确的 {name}")
    candidate_attempt = successful[0]
    model_attempt_id = str(candidate_attempt["id"])
    model_request_id = candidate_attempt["provider_request_id"]
    usage_evidence = candidate_attempt["usage_evidence"]
    model_provider = str(usage_evidence["provider_config_id"])
    model_name = usage_evidence["model"]
    provider_config = ConfigVersion.objects.get(id=model_provider, kind="provider", schema_version="provider-1")
    provider_rows = [item for item in required(voice_before.get("provider_evidence"), "voice_input.provider_evidence")
        if item.get("provider_request_id") == model_request_id]
    if len(provider_rows) != 1 or provider_rows[0] != usage_evidence:
        raise RuntimeError("VoiceInput 读取的调用证据与同次 TaskAttempt 不一致")
    if model_name != provider_config.payload["model_version"]:
        raise RuntimeError("TaskAttempt 模型标识与准确 Provider 配置版本不一致")
    if model_provider != required(plan["model_call"].get("provider"), "model_call.provider") or model_name != required(plan["model_call"].get("model"), "model_call.model"):
        raise RuntimeError("VoiceInput 保存的 provider/model 与计划中的准确版本不一致")
    variables["task_attempt_id"] = model_attempt_id
    variables["provider_request_id"] = model_request_id
    candidate_fields = normalize_voice_candidates(voice_before, plan["projections"]["candidate"])

    adoption_plan = plan["adoption"]
    adoption_path = f"/api/v2/patients/{patient_id}/voice-inputs/{voice_input_id}/adopt"
    adoption_payload = format_values(required(adoption_plan.get("payload"), "adoption.payload"), variables)
    if "request_id" in adoption_payload:
        raise RuntimeError("VoiceInputAdoption 不接受独立 request_id；服务使用 VoiceInput.request_id")
    adoption_payload["voice_input_id"] = voice_input_id
    adoption_payload["expected_status_version"] = str(required(adoption_plan.get("request_expected_status_version"),
        "adoption.request_expected_status_version")) if already_saved else str(select(voice_before, voice["selectors"]["status_version"]))
    adoption_headers = {}
    attempt_ids_before_adoption = task_attempt_snapshot([task_id])
    if already_saved:
        first_save = json.loads((ROOT / ".planning-work/parallel/window3/T55-voice-adoption-api-response-first-save.json").read_text(encoding="utf-8"))
        replay = json.loads((ROOT / ".planning-work/parallel/window3/T55-voice-adoption-api-response-idempotent.json").read_text(encoding="utf-8"))
        adoption_status, adoption_response = first_save["http_status"], first_save["response_body"]
        duplicate_adoption_status, duplicate_adoption_response = replay["http_status"], replay["response_body"]
        if (replay.get("data_mode") != "SIMULATION" or replay.get("evidence_scope") != "SIMULATION_TEXT_ONLY"
                or replay.get("voice_input_id") != voice_input_id or duplicate_adoption_status != adoption_status
                or select(duplicate_adoption_response, "idempotent_replay") is not True):
            raise RuntimeError("已有同一请求幂等重放响应缺少准确模拟证据")
    else:
        adoption_method = required(adoption_plan.get("method"), "adoption.method")
        adoption_status, adoption_response = request_json(client, api_base, adoption_method, adoption_path, adoption_payload, csrf, adoption_headers)
        api_response_evidence = {"data_mode": "SIMULATION", "evidence_scope": "SIMULATION_TEXT_ONLY",
            "voice_input_id": voice_input_id, "request_id": variables["request_id"],
            "http_status": adoption_status, "response_body": adoption_response}
        API_RESPONSE_OUTPUT.write_text(json.dumps(api_response_evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if json.loads(API_RESPONSE_OUTPUT.read_text(encoding="utf-8")) != api_response_evidence:
            raise RuntimeError("采用 API 状态及响应保存后重新读取不一致")
        if adoption_status != adoption_plan["success_status"]:
            raise RuntimeError(f"语音候选确认没有返回预期状态：HTTP {adoption_status}；reason_code={adoption_response.get('code')}")
        duplicate_adoption_status, duplicate_adoption_response = request_json(client, api_base, adoption_method, adoption_path,
            adoption_payload, csrf, adoption_headers)
        if duplicate_adoption_status != adoption_status:
            raise RuntimeError("同一 VoiceInput.request_id 的采用重放返回了不同 HTTP 状态")
        compare_selected_values(adoption_response, duplicate_adoption_response,
            {"record_version_id": "record.record_version_id",
                "saved_record_version_id": "voice_input.saved_record_version_id"},
            "adoption.same-VoiceInput-request-replay")
    attempt_ids_after_adoption = task_attempt_snapshot([task_id])
    if attempt_ids_after_adoption != attempt_ids_before_adoption:
        raise RuntimeError("重复本人确认请求改变了 TaskAttempt 或触发了模型重试")
    adopted_path = format_values(required(adoption_plan.get("read_path_after"), "adoption.read_path_after"), variables)
    adopted_status, adopted_response = get_json(client, api_base, adopted_path)
    if adopted_status != 200:
        raise RuntimeError("VoiceInput 确认状态重读失败")
    adopted_fields = normalize_fields(adopted_response, plan["projections"]["adopted"])
    expected_decisions = required(adoption_plan.get("expected_decisions"), "adoption.expected_decisions")
    adopted_by_path = {field["field_path"]: field for field in adopted_fields}
    candidate_paths = {field["field_path"] for field in candidate_fields}
    if set(adopted_by_path) != candidate_paths or set(expected_decisions) != candidate_paths:
        raise RuntimeError("每个实际候选字段必须有且仅有一项本人决定，包括未选择字段")
    if any(adopted_by_path[field_path].get("decision") != decision for field_path, decision in expected_decisions.items()):
        raise RuntimeError("本人采用、修改、未知或排除决定与计划不一致")
    valid_decisions = {"accepted", "modified", "unknown", "excluded"}
    if any(decision not in valid_decisions for decision in expected_decisions.values()):
        raise RuntimeError("字段决定只允许 accepted、modified、unknown 或 excluded")

    receipt = select(adoption_response, "record")
    if not select(adoption_response, "voice_input.saved_record_version_id"):
        raise RuntimeError("VoiceInput 采用接口没有返回已保存的 RecordVersion 标识")
    if select(receipt, record["record_id_selector"]) is None or select(receipt, record["record_version_id_selector"]) is None:
        raise RuntimeError("VoiceInput 采用接口没有返回真实记录标识")
    variables["record_id"] = select(receipt, record["record_id_selector"])
    variables["record_version_id"] = select(receipt, record["record_version_id_selector"])
    reread_path = format_values(required(record.get("read_path"), "record.read_path"), variables)
    read_at = datetime.now(timezone.utc).isoformat()
    reread_status, reread = get_json(client, api_base, reread_path)
    if reread_status != 200:
        raise RuntimeError("保存后 RecordVersion API 重读失败")
    record_id = select(reread, record["record_id_selector"])
    record_version_id = select(reread, record["record_version_id_selector"])
    if record_id != select(receipt, record["record_id_selector"]) or record_version_id != select(receipt, record["record_version_id_selector"]):
        raise RuntimeError("保存回执与 RecordVersion 重读身份不一致")
    if select(reread, record["patient_id_selector"]) != patient_id:
        raise RuntimeError("保存后记录重读的患儿身份不一致")
    saved_fields = normalize_fields(reread, plan["projections"]["saved_reread"])
    saved_by_path = {field["field_path"]: field for field in saved_fields}
    excluded_paths = {path for path, decision in expected_decisions.items() if decision == "excluded"}
    if excluded_paths & set(saved_by_path):
        raise RuntimeError("标记 excluded 的字段被写入正式记录")
    expected_saved_values = required(adoption_plan.get("expected_saved_values"), "adoption.expected_saved_values")
    if set(saved_by_path) != set(expected_saved_values):
        raise RuntimeError("保存字段集合与语音决定合并后的准确字段集合不一致")
    for field_path, expected_values in expected_saved_values.items():
        actual_values = saved_by_path[field_path]["values"]
        if actual_values != expected_values:
            raise RuntimeError(f"正式记录字段与独立保存预期不一致：{field_path}")

    voice_after_status, voice_after = get_json(client, api_base, voice_path)
    task_after_status, task_after = get_json(client, api_base, task_path)
    if voice_after_status != 200 or task_after_status != 200:
        raise RuntimeError("VoiceInput 或 Task 终态 API 重读失败")
    if str(select(voice_after, voice["selectors"]["saved_record_version_id"])) != str(record_version_id):
        raise RuntimeError("VoiceInput 重读缺少准确的已保存 RecordVersion")
    for selector_name, expected in required(voice.get("expected_after"), "voice_input.expected_after").items():
        if select(voice_after, selector_name) != expected:
            raise RuntimeError(f"VoiceInput 终态与计划不一致：{selector_name}")
    for selector_name, expected in required(task_plan.get("expected_after"), "task.expected_after").items():
        if select(task_after, selector_name) != expected:
            raise RuntimeError(f"Task 终态与计划不一致：{selector_name}")
    expected_layers = {"candidate": candidate_fields, "adopted": adopted_fields}
    for layer_name, layer_fields in expected_layers.items():
        expected_paths = required(plan["expected_field_paths"].get(layer_name), f"expected_field_paths.{layer_name}")
        if {field["field_path"] for field in layer_fields} != set(expected_paths):
            raise RuntimeError(f"{layer_name} 字段集合与计划或独立答案不一致")
    adopted_by_path = {field["field_path"]: field["values"] for field in adopted_fields}
    saved_values_by_path = {field["field_path"]: field["values"] for field in saved_fields}
    expected_decision_saved_mapping = required(adoption_plan.get("decision_saved_mapping"), "adoption.decision_saved_mapping")
    if set(expected_decision_saved_mapping) != set(expected_decisions):
        raise RuntimeError("本人决定到正式记录字段的映射必须覆盖全部五项决定")
    for decision_path, saved_field_path in expected_decision_saved_mapping.items():
        decision = expected_decisions[decision_path]
        if decision in ("accepted", "modified"):
            decision_value = adopted_by_path[decision_path].get("value")
            saved_value = saved_values_by_path[saved_field_path]
            if decision_path == "temperature" and decision_value != saved_value.get("value"):
                raise RuntimeError("本人确认的体温与保存后的 temp 数值不一致")
            if decision_path == "temperature_unit" and decision_value != saved_value.get("unit"):
                raise RuntimeError("本人确认的体温单位与保存后的 temp 单位不一致")
            if decision_path == "symptoms":
                saved_text = saved_value.get("value")
                if not isinstance(saved_text, str):
                    raise RuntimeError("本人确认的症状未以文本保存在 observation_note")
                saved_selections = json.loads(saved_text)
                if saved_selections.get("symptoms") != decision_value:
                    raise RuntimeError("本人确认的症状与保存后的 observation_note 不一致")

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        saved_version = RecordVersion.objects.filter(id=record_version_id, record_id=record_id,
            patient_id=patient_id).values("id", "version", "author_id", "record_id").first()
        task_after_db = Task.objects.filter(id=task_id, patient_id=patient_id).values("status", "actor_id").first()
    if saved_version is None or str(saved_version["id"]) != record_version_id:
        raise RuntimeError("只读数据库没有读取到准确的 RecordVersion")
    if str(saved_version["author_id"]) != session["principal_id"]:
        raise RuntimeError("RecordVersion 作者与确认家庭主体不一致")
    if task_after_db is None or str(task_after_db["actor_id"]) != session["principal_id"]:
        raise RuntimeError("只读数据库中的 Task 主体与当前家庭账号不一致")

    terminal_results = {
        name: verify_terminal_probe(client, api_base, name, probe, variables, patient_id, session["principal_id"])
        for name, probe in required(plan.get("terminal_probes"), "terminal_probes").items()
    }
    revocation_plan = plan.get("authorization_revocation_probe")
    revocation_result = {"status": "NOT_RUN"}
    if revocation_plan and revocation_plan.get("revoke", {}).get("path"):
        task_rows_before = list(Task.objects.filter(patient_id=patient_id, actor_id=session["principal_id"]).order_by("id").values(
            "id", "status", "status_version", "input_sha256"))
        task_ids_before = [item["id"] for item in task_rows_before]
        attempt_rows_before = task_attempt_snapshot(task_ids_before)
        revoke_path = format_values(required(revocation_plan["revoke"].get("path"), "authorization_revocation_probe.revoke.path"), variables)
        revoke_payload = format_values(required(revocation_plan["revoke"].get("payload"), "authorization_revocation_probe.revoke.payload"), variables)
        revoke_header = required(revocation_plan["revoke"].get("idempotency_header"), "authorization_revocation_probe.revoke.idempotency_header")
        revoke_headers = {revoke_header: str(required(revocation_plan["revoke"].get("request_id"), "authorization_revocation_probe.revoke.request_id"))}
        revoke_status, revoke_response = request_json(client, api_base,
            required(revocation_plan["revoke"].get("method"), "authorization_revocation_probe.revoke.method"),
            revoke_path, revoke_payload, csrf, revoke_headers)
        if revoke_status != revocation_plan["revoke"].get("success_status"):
            raise RuntimeError("用途授权撤销没有返回计划状态")
        verify_selected_expected(revoke_response, revocation_plan["revoke"].get("response_selectors"),
            revocation_plan["revoke"].get("expected_response"), "authorization_revocation_probe.revoke")
        revoked_auth_db = DataUseAuthorization.objects.filter(id=usage_authorization_id, patient_id=patient_id).first()
        if revoked_auth_db is None or revoked_auth_db.revoked_at is None:
            raise RuntimeError("授权撤销响应后数据库仍未保存 revoked_at")
        rejected = revocation_plan["rejected_processing"]
        rejected_path = format_values(required(rejected.get("path"), "authorization_revocation_probe.rejected_processing.path"), variables)
        rejected_payload = format_values(required(rejected.get("payload"), "authorization_revocation_probe.rejected_processing.payload"), variables)
        rejected_status, rejected_response = request_json(client, api_base,
            required(rejected.get("method"), "authorization_revocation_probe.rejected_processing.method"),
            rejected_path, rejected_payload, csrf, {})
        if rejected_status != rejected.get("status"):
            raise RuntimeError("撤销授权后发起处理没有被准确拒绝")
        verify_selected_expected(rejected_response, rejected.get("error_selectors"), rejected.get("expected_errors"),
            "authorization_revocation_probe.rejected_processing")
        task_rows_after = list(Task.objects.filter(patient_id=patient_id, actor_id=session["principal_id"]).order_by("id").values(
            "id", "status", "status_version", "input_sha256"))
        attempt_rows_after = task_attempt_snapshot([item["id"] for item in task_rows_after])
        if task_rows_after != task_rows_before or attempt_rows_after != attempt_rows_before:
            raise RuntimeError("撤销授权后的拒绝请求仍创建了 Task 或 TaskAttempt")
        final_voice_status, final_voice = get_json(client, api_base,
            format_values(required(revocation_plan["read_paths"].get("voice_input"), "authorization_revocation_probe.read_paths.voice_input"), variables))
        final_task_status, final_task = get_json(client, api_base,
            format_values(required(revocation_plan["read_paths"].get("task"), "authorization_revocation_probe.read_paths.task"), variables))
        final_record_status, final_record = get_json(client, api_base,
            format_values(required(revocation_plan["read_paths"].get("record"), "authorization_revocation_probe.read_paths.record"), variables))
        if final_voice_status != 200 or final_task_status != 200 or final_record_status != 200:
            raise RuntimeError("撤銷拒绝后无法重读原 VoiceInput、Task 或 RecordVersion")
        read_selectors = required(revocation_plan.get("read_selectors"), "authorization_revocation_probe.read_selectors")
        if select(final_voice, required(read_selectors.get("voice_status"), "authorization_revocation_probe.read_selectors.voice_status")) != select(voice_after, voice["selectors"]["status"]):
            raise RuntimeError("撤销拒绝后 VoiceInput 状态发生变化")
        if select(final_task, required(read_selectors.get("task_status"), "authorization_revocation_probe.read_selectors.task_status")) != select(task_after, task_plan["selectors"]["status"]):
            raise RuntimeError("撤销拒绝后 Task 状态发生变化")
        if select(final_record, required(read_selectors.get("record_id"), "authorization_revocation_probe.read_selectors.record_id")) != record_id:
            raise RuntimeError("撤销拒绝后记录 ID 发生变化")
        if select(final_record, required(read_selectors.get("record_version_id"), "authorization_revocation_probe.read_selectors.record_version_id")) != record_version_id:
            raise RuntimeError("撤销拒绝后 RecordVersion 发生变化")
        revocation_result = {"status": "SIMULATION_REVOKED_USE_REJECTED", "revoke_http_status": revoke_status,
            "database_revoked_at": revoked_auth_db.revoked_at.isoformat(),
            "rejected_processing_http_status": rejected_status, "task_ids_unchanged": True,
            "task_attempt_ids_unchanged": True, "saved_voice_input_task_record_reread": True,
            "final_read_http_statuses": {"voice_input": final_voice_status, "task": final_task_status,
                "record": final_record_status}}

    successful_attempt = candidate_attempt
    elapsed_seconds = (successful_attempt["finished_at"] - successful_attempt["started_at"]).total_seconds()
    usage_evidence = successful_attempt["usage_evidence"]
    raw_usage = usage_evidence.get("usage")
    usage = None if raw_usage is None else {dimension: select(raw_usage, selector) for dimension, selector in
        required(plan["model_call"].get("usage_selectors"), "model_call.usage_selectors").items()}
    if usage_evidence.get("cost_status") == "measured":
        if usage is None or usage_evidence.get("currency") != "CNY":
            raise RuntimeError("已计价语音请求缺少令牌用量或 CNY 计价单位")
        pricing = {"status": "SIMULATION_CONFIG_CALCULATED", "source_ref": usage_evidence["pricing_evidence_ref"],
            "input_cny_per_million": usage_evidence["input_per_million"],
            "output_cny_per_million": usage_evidence["output_per_million"],
            "cost_cny": usage_evidence["actual_cost"]}
    else:
        if usage_evidence.get("actual_cost") is not None:
            raise RuntimeError("未测量费用状态不能包含实际费用数值")
        pricing = {"status": "NOT_MEASURED", "cost_cny": None,
            "source_ref": usage_evidence.get("pricing_evidence_ref"),
            "currency": usage_evidence.get("currency")}
    call_evidence = {
        "provider_request_id": successful_attempt["provider_request_id"],
        "provider": model_provider,
        "model": model_name,
        "started_at": successful_attempt["started_at"].isoformat(),
        "finished_at": successful_attempt["finished_at"].isoformat(),
        "elapsed_seconds": elapsed_seconds,
        "provider_duration_ms": usage_evidence.get("provider_duration_ms"),
        "usage": usage,
        "pricing": pricing,
        "task_attempt_history": [{"id": str(item["id"]), "attempt_number": item["attempt_number"],
            "provider_request_id": item["provider_request_id"], "started_at": item["started_at"].isoformat() if item["started_at"] else None,
            "finished_at": item["finished_at"].isoformat() if item["finished_at"] else None,
            "status": item["status"], "usage_evidence": item["usage_evidence"]} for item in attempts],
    }
    evidence = {
        "schema_version": "window3-t55-voice-roundtrip-2",
        "data_mode": "SIMULATION",
        "evidence_scope": "SIMULATION_TEXT_ONLY",
        "source_transcript_sha256": transcript_sha256,
        "independent_answer_id": independent["answer_id"],
        "task_id": task_id,
        "voice_input_id": voice_input_id,
        "patient_id": patient_id,
        "principal_id": session["principal_id"],
        "database_identity": database_actual,
        "authorization_verification": {"id": str(auth_db.id), "patient_id": str(auth_db.patient_id),
            "source_revision_id": str(auth_db.source_revision_id), "purpose": auth_db.purpose,
            "environment": auth_db.environment, "approved_by_id": str(auth_db.approved_by_id),
            "approved_at": auth_db.approved_at.isoformat(),
            "source_revision_allowed_use": allowed_use,
            "valid_until": auth_db.valid_until.isoformat() if auth_db.valid_until else None,
            "revoked_at_before_model_call": auth_db.revoked_at.isoformat() if auth_db.revoked_at else None},
        "candidate_status_before": select(voice_before, voice["selectors"]["status"]),
        "voice_input_status_after": select(voice_after, voice["selectors"]["status"]),
        "task_status_before": select(task_before, task_plan["selectors"]["status"]),
        "task_status_after": select(task_after, task_plan["selectors"]["status"]),
        "model_call": call_evidence,
        "layers": {
            "candidate": {"status": "SIMULATION_CANDIDATE_READ", "fields": candidate_fields},
            "adopted": {"status": "SIMULATION_CONFIRMED", "fields": adopted_fields,
                "confirmation_response": adoption_response},
            "saved_reread": {"status": "SIMULATION_SAVED_AND_READ", "fields": saved_fields,
                "record_id": record_id, "record_version_id": record_version_id, "version": str(saved_version["version"]),
                "record_view": reread, "read_at": read_at},
        },
        "idempotency": {"status": "SIMULATION_SAME_REQUEST_REPLAYED", "first_status": adoption_status,
            "replay_status": duplicate_adoption_status,
            "task_attempt_ids_unchanged": True,
            "voice_request_id": variables["request_id"],
            "selected_response_values": {"record_version_id": select(duplicate_adoption_response, "record.record_version_id"),
                "saved_record_version_id": select(duplicate_adoption_response, "voice_input.saved_record_version_id")}},
        "authorization_revocation_probe": revocation_result,
        "terminal_probes": terminal_results,
        "http_statuses": {"task_before": task_status, "voice_input_before": voice_status,
            "adoption": adoption_status, "adoption_idempotency_replay": duplicate_adoption_status,
            "adoption_read": adopted_status,
            "adoption_replay": duplicate_adoption_status, "record_save": adoption_status, "record_read": reread_status,
            "voice_input_after": voice_after_status, "task_after": task_after_status},
        "database_record_version": {"id": str(saved_version["id"]), "version": str(saved_version["version"]),
            "author_id": str(saved_version["author_id"])},
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"status": "SIMULATION_VOICE_INPUT_CONFIRMED_SAVED_REREAD",
        "task_id": task_id, "voice_input_id": voice_input_id, "record_version_id": record_version_id,
        "field_count": len(saved_fields), "evidence": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
