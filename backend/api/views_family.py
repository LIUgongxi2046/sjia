"""家庭端 API 端点：会话、关联、记录、草稿、首页、提示、摘要与任务。"""
import uuid

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from alerting.models import AlertEvent, AlertRecipient
from django.db import connection, transaction
from requests.models import WriteIntent
from requests.service import compute_digest, commit_intent
from api.base import ApiError, authorize_case, current_principal, get_patient, handle
from api.patient_selection import current_selected_patient
from configurations.field_config import read_field_config
from configurations.schemas import validate_operation_input
from authorization.policy import matching_case_grants
from drafts.models import Draft
from drafts.service import abandon_draft, create_draft, update_draft
from identity.service import IdentityRejected, authenticate_account, register_account
from identity.session import bootstrap_csrf, clear_session, establish_session, session_read_metadata
from identity.support_session import support_capabilities
from records.correction import correct_record
from records.models import Record, RecordField, RecordVersion
from records.queries import record_version_read, record_history_read, source_resolve
from records.service import create_record, read_record
from records.snapshot import authorize_record_snapshot, record_read_snapshot
from relations.models import Relation, RelationApplication
from relations.application_requests import submit_application
from relations.queries import relation_list_read, relation_view, application_list_read, application_view


@api_view(["GET"])
@handle
def auth_bootstrap(request):
    token = bootstrap_csrf(request)
    return Response({"csrf_token": token, "return_paths": ["/home", "/records", "/account", "/associations"], "schema_version": "1.0.0"})


@api_view(["POST"])
@handle
def auth_start(request):
    raise ApiError('AUTH_PROVIDER_UNAVAILABLE', status.HTTP_503_SERVICE_UNAVAILABLE, '外部身份提供方尚未配置，请使用账号密码登录。')


@api_view(["POST"])
@handle
def auth_complete(request):
    raise ApiError('AUTH_PROVIDER_UNAVAILABLE', status.HTTP_503_SERVICE_UNAVAILABLE, '外部身份提供方尚未配置，请使用账号密码登录。')


def _session_payload(request, principal, audience='family'):
    return {
        "principal_id": str(principal.id), "account_status": principal.account_status,
        **session_read_metadata(request, principal_id=principal.id, auth_epoch=principal.auth_epoch, audience=audience),
        "audience": audience,
        "capabilities": support_capabilities(principal) if audience == 'support' else [], "csrf_token": bootstrap_csrf(request),
        "selected_patient_id": current_selected_patient(request, principal) if audience == 'family' else None,
    }


@api_view(["POST"])
@handle
def auth_register(request):
    data = request.data or {}
    try:
        principal = register_account(username=data.get("username", ""), password=data.get("password", ""))
    except IdentityRejected as exc:
        raise ApiError(exc.reason_code, status.HTTP_400_BAD_REQUEST)
    establish_session(request, principal_id=principal.id, auth_epoch=principal.auth_epoch, audience="family")
    return Response(_session_payload(request, principal), status=201)


@api_view(["POST"])
@handle
def auth_login(request):
    data = request.data or {}
    try:
        principal = authenticate_account(username=data.get("username", ""), password=data.get("password", ""))
    except IdentityRejected as exc:
        raise ApiError(exc.reason_code, status.HTTP_401_UNAUTHORIZED)
    establish_session(request, principal_id=principal.id, auth_epoch=principal.auth_epoch, audience="family")
    return Response(_session_payload(request, principal))


@api_view(["GET"])
@handle
def capabilities_read(request):
    principal, _, audience = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    if audience == 'support':
        return Response({'audience': audience, 'capabilities': support_capabilities(principal)})
    capabilities = ["record:write", "record:read", "draft:manage", "alert:receive", "summary:create"]
    return Response({"audience": audience or "family", "capabilities": capabilities})


@api_view(["GET"])
@handle
def session_read(request):
    principal, auth_epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    return Response(_session_payload(request, principal, audience))


@api_view(["POST"])
@handle
def session_logout(request):
    clear_session(request)
    return Response({})


@api_view(["GET"])
@handle
def relation_list(request):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    return Response(relation_list_read(principal))


@api_view(["GET", "POST"])
@handle
def application_list(request):
    principal, auth_epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    if request.method == "POST":
        receipt = submit_application(actor=principal, auth_epoch=auth_epoch, audience=audience,
            operation='application_create', target=request.path, data=request.data,
            headers={'intent_id': request.headers.get('X-Write-Intent'), 'request_id': request.headers.get('Idempotency-Key'),
                'generation': request.headers.get('X-Intent-Generation')})
        return Response(receipt, status=201)
    return Response(application_list_read(principal))


@api_view(["GET"])
@handle
def relation_read(request, relation_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    relation = Relation.objects.filter(id=relation_id, guardian=principal).first()
    if relation is None:
        raise ApiError("RELATION_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    return Response(relation_view(relation, principal, detail=True))


@api_view(["GET"])
@handle
def application_read(request, application_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    application = RelationApplication.objects.filter(id=application_id, applicant=principal).first()
    if application is None:
        raise ApiError("APPLICATION_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    return Response(application_view(application, principal, detail=True))


@api_view(["POST"])
@handle
def application_withdraw(request, application_id):
    principal, auth_epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    receipt = submit_application(actor=principal, auth_epoch=auth_epoch, audience=audience,
        operation='application_withdraw', target=request.path, data=request.data, application_id=application_id,
        headers={'intent_id': request.headers.get('X-Write-Intent'), 'request_id': request.headers.get('Idempotency-Key'),
            'generation': request.headers.get('X-Intent-Generation')})
    return Response(receipt)


@api_view(["POST"])
@handle
@transaction.atomic
def record_create(request, patient_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    patient = get_patient(patient_id)
    from records.input_validation import validate_family_record_input

    data = validate_family_record_input(request.data)
    fields = data.get('fields', [])
    if not isinstance(fields, list) or not fields or any(not isinstance(field, dict) or not field.get('field_code') for field in fields):
        raise ApiError('RECORD_FIELDS_REQUIRED', status.HTTP_422_UNPROCESSABLE_ENTITY)
    authorize_case(request, principal, patient, "write_record", author_id=principal.id, field_codes=[field['field_code'] for field in fields])
    from identity.models import Patient
    patient = Patient.objects.select_for_update().get(id=patient.id)
    if patient.status != 'active':
        raise ApiError('PATIENT_UNAVAILABLE', 409)
    if data.get('kind') not in ('observation', 'report', 'therapy') or data.get('capture_method') not in ('manual', 'ocr_confirmed', 'voice_confirmed'):
        raise ApiError('RECORD_TYPE_INVALID', status.HTTP_422_UNPROCESSABLE_ENTITY)
    if data['kind'] == 'therapy':
        from api.therapy_view import require_therapy_confirmation

        require_therapy_confirmation(data)
    origin_type = 'report_transcription' if data['kind'] == 'report' else data.get('origin_type', 'self_report')
    if origin_type not in ('self_report', 'report_transcription'):
        raise ApiError('RECORD_SOURCE_INVALID', status.HTTP_422_UNPROCESSABLE_ENTITY)
    from retention.models import RetentionPolicy
    from sources.models import DataUseAuthorization

    from django.conf import settings
    from django.db.models import Q

    from records.admission import family_record_admission

    retention, use_auth = family_record_admission(patient, data['kind'])
    intent_id = request.headers.get('X-Write-Intent')
    if not intent_id:
        raise ApiError('WRITE_INTENT_REQUIRED', status.HTTP_422_UNPROCESSABLE_ENTITY)
    intent = WriteIntent.objects.select_for_update().filter(id=intent_id, actor=principal, operation_id='record_create', target_key=request.path).first()
    if intent is None or intent.payload_sha256 != compute_digest('record_create', request.path, 'back-json-1', data):
        raise ApiError('WRITE_INTENT_MISMATCH', status.HTTP_409_CONFLICT)
    if request.headers.get('X-Intent-Generation') != str(intent.generation) or request.headers.get('Idempotency-Key') != str(intent.request_id):
        raise ApiError('WRITE_INTENT_MISMATCH', status.HTTP_409_CONFLICT)
    if intent.status == 'committed':
        from requests.record_intents import _receipt

        if intent.patient_id != patient.id:
            raise ApiError('WRITE_INTENT_PATIENT_MISMATCH', 409)
        return Response(_receipt(intent))
    if intent.status != 'prepared' or intent.expires_at <= timezone.now():
        raise ApiError('WRITE_INTENT_EXPIRED', status.HTTP_409_CONFLICT)
    from configurations.field_config import validate_record_configuration

    if not data.get('field_config_version'):
        raise ApiError('FIELD_CONFIG_VERSION_REQUIRED', 422)
    validate_record_configuration(data['field_config_version'], data['kind'], fields)
    intent.patient = patient
    intent.save(update_fields=['patient'])
    confirmation = None
    if data.get('candidate_set_id'):
        from recognition.confirmation import prepare_confirmed_fields
        from files.models import FileObject

        if data['kind'] != 'report' or data['capture_method'] != 'ocr_confirmed':
            raise ApiError('RECORD_TYPE_INVALID', status.HTTP_422_UNPROCESSABLE_ENTITY)
        authorize_case(request, principal, patient, 'read_file')
        fields, candidate_set, task, group = prepare_confirmed_fields(candidate_set_id=data['candidate_set_id'],
            patient=patient, actor=principal, fields=fields, decisions=data.get('decisions'))
        authorize_case(request, principal, patient, 'write_record', author_id=principal.id, field_codes=[field['field_code'] for field in fields])
        validate_record_configuration(data['field_config_version'], data['kind'], fields)
        expected_files = set(str(value) for value in FileObject.objects.filter(file_group_id=group.id, patient=patient, purpose='original').values_list('id', flat=True))
        if set(data.get('file_ids', [])) != expected_files:
            raise ApiError('CANDIDATE_FILES_MISMATCH', status.HTTP_422_UNPROCESSABLE_ENTITY)
        confirmation = (candidate_set, task)
    result = create_record(
        patient=patient, actor=principal, kind=data.get("kind", "observation"),
        capture_method=data.get("capture_method", "manual"), origin_type=origin_type,
        retention_policy=retention, use_authorization_id=use_auth.id, fields=fields,
        file_ids=data.get('file_ids', []), source_organization=data.get('source_organization'), report_metadata=data.get('report'),
        source_revision=use_auth.source_revision,
    )
    if confirmation:
        from recognition.confirmation import complete_confirmation

        complete_confirmation(*confirmation)
    receipt = commit_intent(intent=intent, action='record_create', resource_type='record', resource_id=result['record_id'])
    if data['kind'] == 'therapy':
        from api.therapy_view import save_therapy_confirmation

        save_therapy_confirmation(result=result, actor=principal, patient=patient, intent=intent, retention=retention)
    if data.get('draft_id'):
        from drafts.service import submit_draft
        draft = Draft.objects.filter(id=data['draft_id'], owner=principal, patient=patient).first()
        if draft is None or draft.form_type != data['kind'] or 'draft_version' not in data:
            raise ApiError('DRAFT_NOT_RESTORABLE', status.HTTP_409_CONFLICT)
        submit_draft(draft=draft, expected_version=int(data['draft_version']), submitted_request=receipt)
    return Response(result, status=201)


def _readable_record_history_versions(principal, patient, *, record_id, author_id):
    from records.access import permitted_field_codes

    codes = permitted_field_codes(principal, patient, 'read_history', record_id=record_id, author_id=author_id)
    versions = RecordVersion.objects.filter(record_id=record_id, patient=patient)
    return {str(version_id) for version_id in RecordField.objects.filter(
        patient=patient, record_version_id__in=versions.values('id'), field_code__in=codes
    ).values_list('record_version_id', flat=True).distinct()}


@api_view(["GET"])
@handle
@record_read_snapshot
def record_read(request, record_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    record = Record.objects.filter(id=record_id).first()
    if record is None:
        raise ApiError("RECORD_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    author = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=record.id, patient=record.patient, version=1).author_id
    decision = authorize_record_snapshot(request, principal, record.patient, "read_raw", record_id=record.id, author_id=author)
    result = read_record(record.id, record.patient)
    from authorization.policy import matching_case_grants

    requested_fields = [field['field_code'] for field in result['fields']]
    result['allowed_actions'] = [action for action in ('correct_record', 'request_correction', 'read_history') if any(matching_case_grants(
        principal, record.patient, action, timezone.now(), record_id=record.id, author_id=author,
        field_codes=requested_fields if action == 'correct_record' else None,
    )) and (action != 'correct_record' or record.kind != 'professional')]
    from records.access import permitted_field_codes

    visible_codes = permitted_field_codes(principal, record.patient, 'read_raw', record_id=record.id, author_id=author)
    result['fields'] = [field for field in result['fields'] if field['field_code'] in visible_codes]
    if not result['fields']:
        raise ApiError('CASE_ACTION_DENIED', status.HTTP_403_FORBIDDEN)
    visible = {field['field_id'] for field in result['fields']}
    result['fact_refs'] = [reference for reference in result['fact_refs'] if reference['field_id'] in visible]
    if 'read_history' in result['allowed_actions'] and not _readable_record_history_versions(
            principal, record.patient, record_id=record.id, author_id=author):
        result['allowed_actions'].remove('read_history')
    if result['kind'] == 'therapy':
        from api.therapy_view import therapy_events_view, therapy_confirmation_view

        result['therapy_events'] = therapy_events_view(result, include_history='read_history' in result['allowed_actions'])
        result['family_confirmation'] = therapy_confirmation_view(result, principal)
    return Response(result)


@api_view(["GET"])
@handle
@record_read_snapshot
def record_history(request, record_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    record = Record.objects.filter(id=record_id).first()
    if record is None:
        raise ApiError("RECORD_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    author = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=record.id, patient=record.patient, version=1).author_id
    authorize_record_snapshot(request, principal, record.patient, "read_history", record_id=record.id, author_id=author)
    visible_versions = _readable_record_history_versions(
        principal, record.patient, record_id=record.id, author_id=author)
    if not visible_versions:
        raise ApiError('CASE_ACTION_DENIED', status.HTTP_403_FORBIDDEN)
    result = record_history_read(patient=record.patient, record_id=record.id)
    result['versions'] = [version for version in result['versions']
        if version['record_version_id'] in visible_versions]
    # Keep the real current number as authorized record-header metadata. An
    # older visible version must not be mislabeled current, and hidden version
    # entries/UUIDs do not become readable through this directory.
    return Response(result)


@api_view(["POST"])
@handle
@transaction.atomic
def record_correct(request, record_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    record = Record.objects.filter(id=record_id).first()
    if record is None:
        raise ApiError("RECORD_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    data = request.data or {}
    if record.kind == 'therapy':
        from api.therapy_view import require_therapy_confirmation

        require_therapy_confirmation(data)
    from records.correction import validate_correction_request

    validate_correction_request(data)
    first_author = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=record.id, patient=record.patient, version=1).author_id
    fields = data.get('fields', [])
    if not fields or any(not isinstance(field, dict) or not field.get('field_code') for field in fields):
        raise ApiError('RECORD_FIELDS_REQUIRED', status.HTTP_422_UNPROCESSABLE_ENTITY)
    authorize_case(request, principal, record.patient, "correct_record", field_codes=[field['field_code'] for field in fields], record_id=record.id, author_id=first_author)
    from identity.models import Patient
    patient = Patient.objects.select_for_update().get(id=record.patient_id)
    if patient.status != 'active':
        raise ApiError('PATIENT_UNAVAILABLE', 409)
    record = Record.objects.select_for_update().get(id=record.id, patient=patient)
    if record.status != 'active':
        raise ApiError('RECORD_UNAVAILABLE', 409)
    from retention.models import RetentionPolicy
    from sources.models import DataUseAuthorization, SourceRevision

    from django.conf import settings
    from django.db.models import Q

    previous_version = RecordVersion.objects.get(record_id=record.id, patient=record.patient, version=record.current_version)
    from records.admission import family_record_admission

    retention, use_auth = family_record_admission(record.patient, record.kind,
        source_revision_id=previous_version.source_revision_id)
    intent_id = request.headers.get('X-Write-Intent')
    if not intent_id:
        raise ApiError('WRITE_INTENT_REQUIRED', 422)
    intent = WriteIntent.objects.select_for_update().filter(id=intent_id, actor=principal,
        operation_id='record_correct', target_key=request.path).first()
    if (intent is None or intent.payload_sha256 != compute_digest('record_correct', request.path, 'back-json-1', data)
            or request.headers.get('X-Intent-Generation') != str(intent.generation)
            or request.headers.get('Idempotency-Key') != str(intent.request_id)):
        raise ApiError('WRITE_INTENT_MISMATCH', 409)
    if intent.patient_id != record.patient_id:
        raise ApiError('WRITE_INTENT_PATIENT_MISMATCH', 409)
    if intent.status == 'committed':
        from requests.record_intents import _receipt
        return Response(_receipt(intent))
    if intent.status != 'prepared' or intent.expires_at <= timezone.now():
        raise ApiError('WRITE_INTENT_EXPIRED', 409)
    if record.kind == 'therapy':
        config = read_field_config(record.patient, 'therapy')
        if config['state'] != 'available':
            raise ApiError('FIELD_CONFIGURATION_PENDING', 503)
        from configurations.field_config import validate_record_configuration

        validate_record_configuration(config['config_version'], 'therapy', fields)
        intent.patient = record.patient
        intent.save(update_fields=['patient'])
    source_revision = previous_version.source_revision
    if 'expected_version' not in data:
        raise ApiError('EXPECTED_VERSION_REQUIRED', status.HTTP_400_BAD_REQUEST)
    result = correct_record(
        record=record, expected_version=int(data.get("expected_version", record.current_version)),
        actor=principal, reason=data.get("reason", ""), fields=data.get("fields", []),
        use_authorization_id=use_auth.id, retention_policy=retention, source_revision=source_revision,
        origin_type=previous_version.origin_type,
    )
    submitted_request = commit_intent(intent=intent, action='record_correct', resource_type='record_version',
        resource_id=result['record_version_id'], expected_version=str(data['expected_version']))
    if record.kind == 'therapy':
        from api.therapy_view import save_therapy_confirmation
        save_therapy_confirmation(result=result, actor=principal, patient=record.patient, intent=intent, retention=retention)
    if data.get('draft_id'):
        from drafts.service import submit_draft
        draft = Draft.objects.select_for_update().filter(id=data['draft_id'], owner=principal, patient=record.patient, form_type='correction').first()
        if draft is None or draft.correction_record_id != record.id or draft.correction_version != int(data['expected_version']) or 'draft_version' not in data:
            raise ApiError('DRAFT_NOT_RESTORABLE', status.HTTP_409_CONFLICT)
        submit_draft(draft=draft, expected_version=int(data['draft_version']), submitted_request=submitted_request)
    return Response(result, status=201)


@api_view(["POST"])
@handle
@record_read_snapshot
def record_search(request, patient_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    patient = get_patient(patient_id)
    from records.queries import record_search as search
    from records.search_input import RecordSearchInput
    from records.access import query_permissions, permitted_field_codes
    from authorization.policy import check_principal_session, environment_enabled
    from django.conf import settings

    environment_enabled(settings.SJIA_ENVIRONMENT)
    check_principal_session(principal, current_principal(request)[1])
    scopes = query_permissions(principal, patient)
    if not scopes:
        raise ApiError('CASE_ACTION_DENIED', 403)
    data = RecordSearchInput.model_validate(request.data).model_dump()
    result = search(patient=patient, principal=principal, permission_scopes=scopes, **data)
    for record in result['records']:
        author = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=record['record_id'], patient=patient, version=1).author_id
        codes = permitted_field_codes(principal, patient, 'read_raw', record_id=record['record_id'], author_id=author)
        record['detail']['fields'] = [field for field in record['detail']['fields'] if field['field_code'] in codes]
        visible = {field['field_id'] for field in record['detail']['fields']}
        record['detail']['fact_refs'] = [reference for reference in record['detail']['fact_refs'] if reference['field_id'] in visible]
    result['records'] = [record for record in result['records'] if record['detail']['fields']]
    return Response(result)


@api_view(["GET"])
@handle
@record_read_snapshot
def record_version_read_view(request, record_version_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    from records.models import RecordVersion

    version = RecordVersion.all_objects.filter(id=record_version_id).first()
    if version is None:
        raise ApiError("VERSION_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    author = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=version.record_id, patient=version.patient, version=1).author_id
    authorize_record_snapshot(request, principal, version.patient, "read_history", record_id=version.record_id, author_id=author)
    result = record_version_read(patient=version.patient, record_version_id=record_version_id)
    from records.access import permitted_field_codes

    codes = permitted_field_codes(principal, version.patient, 'read_history', record_id=version.record_id, author_id=author)
    result['fields'] = [field for field in result['fields'] if field['field_code'] in codes]
    if not result['fields']:
        raise ApiError('CASE_ACTION_DENIED', status.HTTP_403_FORBIDDEN)
    visible = {field['field_id'] for field in result['fields']}
    result['fact_refs'] = [reference for reference in result['fact_refs'] if reference['field_id'] in visible]
    if result['kind'] == 'therapy':
        from api.therapy_view import therapy_events_view, therapy_confirmation_view

        result['therapy_events'] = therapy_events_view(result, include_history=True)
        result['family_confirmation'] = therapy_confirmation_view(result, principal)
    return Response(result)


@api_view(["GET"])
@handle
@record_read_snapshot
def source_resolve_view(request, record_version_id, field_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    from records.models import RecordVersion

    version = RecordVersion.all_objects.filter(id=record_version_id).first()
    if version is None:
        raise ApiError("VERSION_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    field = RecordField.all_objects.filter(id=field_id, record_version_id=version.id, patient=version.patient).first()
    if field is None:
        raise ApiError("FIELD_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    first_author = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=version.record_id, patient=version.patient, version=1).author_id
    authorize_record_snapshot(request, principal, version.patient, "read_raw", record_id=version.record_id,
                   author_id=first_author, field_codes=[field.field_code] if field.content_erased_at is None else None)
    from retention.content_fences import assert_content_available
    assert_content_available('records.recordversion', version.id)
    assert_content_available('records.recordfield', field.id)
    return Response(source_resolve(patient=version.patient, record_version_id=record_version_id, field_id=field_id))


@api_view(["GET"])
@handle
@transaction.atomic
def field_config_read_view(request, patient_id):
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
    principal, _, audience = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    if audience != 'family':
        raise ApiError('FAMILY_AUDIENCE_REQUIRED', 403)
    patient = get_patient(patient_id)
    authorize_case(request, principal, patient, 'read_raw', author_id=principal.id, require_relation=True)
    if any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise ApiError('CONFIG_QUERY_INVALID', 422)
    data = validate_operation_input('FieldConfigQuery', request.query_params.dict())
    now = timezone.now()
    readable = {code for grant in matching_case_grants(principal, patient, 'read_raw', now,
        author_id=principal.id, require_relation=True, purpose='family_observation') for code in grant.field_codes}
    writable = {code for grant in matching_case_grants(principal, patient, 'write_record', now,
        author_id=principal.id, require_relation=True, purpose='family_observation') for code in grant.field_codes}
    return Response(read_field_config(patient, data['kind'], version=data.get('version'),
        readable_field_codes=readable, writable_field_codes=readable & writable))


@api_view(["GET"])
@handle
@transaction.atomic
def home_read(request, patient_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    patient = get_patient(patient_id)
    authorize_case(request, principal, patient, "read_raw", require_relation=True)
    from django.conf import settings
    from api.family_home_details import build_home

    return Response(build_home(principal, patient, settings.SJIA_ENVIRONMENT))


@api_view(["GET"])
@handle
def alert_list(request, patient_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    patient = get_patient(patient_id)
    authorize_case(request, principal, patient, "receive_alert")
    relation = Relation.objects.filter(patient=patient, guardian=principal, status="active").first()
    recipients = {}
    if relation is not None:
        recipients = {r.alert_id: r for r in AlertRecipient.objects.filter(patient_id=patient.id, relation_id=relation.id)}
    events = AlertEvent.objects.filter(patient=patient).order_by("-created_at")
    from family_content.alert_detail import alert_content

    return Response({"alerts": [{
        "alert_id": str(e.id), "created_at": e.created_at.isoformat(), "validity": alert_content(e, [])['validity'],
        "channel": recipients[e.id].channel if e.id in recipients else "in_app",
        "read_at": recipients[e.id].read_at.isoformat() if e.id in recipients and recipients[e.id].read_at else None,
    } for e in events]})


@api_view(["GET"])
@handle
def alert_stats(request, patient_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    patient = get_patient(patient_id)
    authorize_case(request, principal, patient, "receive_alert")
    relation = Relation.objects.filter(patient=patient, guardian=principal, status="active").first()
    if relation is None:
        return Response({"total": 0, "unread": 0})
    recipients = AlertRecipient.objects.filter(patient_id=patient.id, relation_id=relation.id)
    total = recipients.count()
    unread = recipients.filter(read_at__isnull=True).count()
    return Response({"total": total, "unread": unread})


@api_view(["POST"])
@handle
@transaction.atomic
def alert_mark_read(request, alert_id):
    from api.views_notifications import notification_identity, notification_scope
    from family_content.alert_detail import alert_content

    principal, _, _ = notification_identity(request, False)
    if request.query_params or request.data != {}:
        raise ApiError('ALERT_READ_INPUT_INVALID', 422)
    event = AlertEvent.objects.select_for_update().filter(id=alert_id).first()
    if event is None:
        raise ApiError("ALERT_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    _, _, rows = notification_scope(request, event.patient_id, False, principal, None)
    content = alert_content(event, [])
    if content['validity'] != 'valid' or content['content_state'] != 'available':
        raise ApiError('ALERT_CONTENT_INVALIDATED', 409)
    recipient = rows.select_for_update().filter(alert_id=event.id, channel='in_app').first()
    if recipient is not None:
        from alerting.home import mark_read

        mark_read(recipient)
    return Response({"alert_id": str(event.id), "read_at": recipient.read_at.isoformat() if recipient and recipient.read_at else None})


@api_view(["GET"])
@handle
@transaction.atomic
def task_read(request, task_id):
    from tasks.models import Task

    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    task = Task.objects.filter(id=task_id, actor=principal).first()
    if task is None:
        raise ApiError("TASK_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    if task.schema_version == 'voice-input-1':
        from api.views_voice_inputs import locked_voice, voice_task_view
        from recognition.models import VoiceInput

        row = VoiceInput.objects.get(task=task, actor=principal)
        row, *_ = locked_voice(request, task.patient_id, row.id)
        return Response(voice_task_view(row))
    if task.task_type == Task.TaskType.FILE_VALIDATION:
        from files.validation import read_validation_task
        _, epoch, audience = current_principal(request)
        return Response(read_validation_task(task=task, actor=principal, auth_epoch=epoch, audience=audience))
    if task.task_type == Task.TaskType.SUMMARY:
        from summaries.submission import summary_permission, summary_task_view

        _, auth_epoch, audience = current_principal(request)
        summary_permission(principal, task.patient, auth_epoch, audience, 'read_summary')
        return Response(summary_task_view(task))
    result = {'result_type': None, 'result_ref': None, 'error_code': None}
    if task.task_type == Task.TaskType.OCR:
        from recognition.jobs import recognition_result

        authorize_case(request, principal, task.patient, 'read_file')
        result = recognition_result(task)
    return Response({'task_id': str(task.id), 'status': task.status, 'status_version': str(task.status_version), **result})


@api_view(["POST"])
@handle
@transaction.atomic
def task_cancel(request, task_id):
    from tasks.models import Task
    from tasks.service import cancel_task

    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    seed = Task.objects.filter(id=task_id, actor=principal).first()
    if seed is not None and seed.task_type == Task.TaskType.FILE_VALIDATION:
        from files.validation import cancel_validation
        _, epoch, audience = current_principal(request)
        cancel_validation(task=seed, actor=principal, auth_epoch=epoch, audience=audience)
        return Response({})
    if seed is not None and seed.task_type == Task.TaskType.SUMMARY:
        from summaries.jobs import cancel_summary

        _, epoch, audience = current_principal(request)
        cancel_summary(task_id=seed.id, actor=principal, auth_epoch=epoch, audience=audience)
        return Response({})
    if seed is not None and seed.schema_version == 'voice-input-1':
        from recognition.models import VoiceInput
        from recognition.voice_input_service import cancel_voice_input

        _, epoch, _ = current_principal(request)
        row = VoiceInput.objects.get(task=seed, actor=principal)
        cancel_voice_input(voice_input_id=row.id, actor=principal, auth_epoch=epoch,
            expected_status_version=str(row.status_version))
        return Response({})
    task = Task.objects.select_for_update().filter(id=task_id, actor=principal).first()
    if task is None:
        raise ApiError("TASK_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    if task.task_type == Task.TaskType.SUMMARY:
        from summaries.submission import summary_permission

        _, auth_epoch, audience = current_principal(request)
        summary_permission(principal, task.patient, auth_epoch, audience)
    if cancel_task(task.id):
        from recognition.models import CandidateSet

        CandidateSet.objects.filter(task_id=task.id, status=CandidateSet.Status.PENDING_CONFIRMATION).update(status=CandidateSet.Status.CANCELLED)
    return Response({})
