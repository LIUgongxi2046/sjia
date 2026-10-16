"""家庭端 API：草稿、摘要、意图/请求、历史与历次值。"""
import uuid

from django.db import connection, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, authorize_case, current_principal, get_patient, handle
from drafts.models import Draft
from drafts.service import abandon_draft, create_draft, list_restorable_drafts, read_draft, update_draft
from records.models import Record
from records.queries import analyte_search, history_search
from django.conf import settings
from verification.correction_requests import (CORRECTION_OPERATIONS, prepare_correction_intent,
    correction_request_view, retry_correction_intent)
from requests.models import WriteIntent
from records.professional import PROFESSIONAL_OPERATIONS, prepare_professional_intent, professional_request_view, retry_professional_intent
from requests.record_intents import RECORD_OPERATIONS, prepare_record_intent, record_request_view, retry_record_intent
from requests.service import prepare_intent, query_intent, retry_intent
from requests.controlled import CONTROLLED_OPERATIONS, prepare_controlled_intent, controlled_request_view, retry_controlled_intent, recent_controlled_requests
from retention.models import RetentionPolicy
from summaries.models import Summary
from summaries.service import read_summary
from relations.application_requests import APPLICATION_OPERATIONS, prepare_application_intent, intent_view, application_request_view, retry_application_intent
from relations.inputs import ApplicationRetryInput
from summaries.inputs import SummaryRetryInput
from summaries.service import list_summaries
from summaries.submission import prepare_summary_intent, submit_summary, summary_request_view, summary_permission, retry_summary_intent


def _draft_view(draft: Draft) -> dict:
    return {
        "draft_id": str(draft.id), "form_type": draft.form_type, "version": str(draft.version),
        "saved_at": draft.saved_at.isoformat(), "status": draft.status, "payload": draft.payload,
        "patient_id": str(draft.patient_id),
    }


@api_view(["GET", "POST"])
@handle
def draft_collection(request, patient_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    patient = get_patient(patient_id)
    authorize_case(request, principal, patient, "manage_draft")
    if request.method == "POST":
        from drafts.policy import draft_retention
        retention, expires_at = draft_retention()
        data = request.data or {}
        form_type = data.get('form_type')
        payload = data.get('payload')
        if form_type not in Draft.FormType.values or not isinstance(payload, dict):
            raise ApiError('DRAFT_INPUT_INVALID', status.HTTP_422_UNPROCESSABLE_ENTITY)
        correction = None
        if form_type == 'correction':
            correction = Record.objects.filter(id=payload.get('recordId'), patient=patient).first()
            if correction is None or payload.get('version') != correction.current_version:
                raise ApiError('VERSION_CONFLICT', status.HTTP_409_CONFLICT)
            from records.models import RecordVersion
            author_id = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(patient=patient, record_id=correction.id, version=1).author_id
            authorize_case(request, principal, patient, 'correct_record', record_id=correction.id, author_id=author_id)
        draft = create_draft(patient=patient, owner=principal, form_type=form_type, schema_version="backend-draft-1", payload=payload, retention_policy=retention,
                             correction_record_id=correction.id if correction else None, correction_version=correction.current_version if correction else None,
                             expires_at=expires_at)
        return Response(_draft_view(draft), status=201)
    drafts = list_restorable_drafts(owner=principal, patient=patient)
    return Response({"drafts": [_draft_view(d) for d in drafts]})


@api_view(["GET", "PUT"])
@handle
def draft_detail(request, draft_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    draft = Draft.objects.filter(id=draft_id, owner=principal).first()
    if draft is None:
        raise ApiError("DRAFT_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    authorize_case(request, principal, draft.patient, 'manage_draft')
    if draft.status != 'editing' or (draft.expires_at and draft.expires_at <= timezone.now()):
        raise ApiError('DRAFT_NOT_RESTORABLE', status.HTTP_409_CONFLICT)
    if draft.form_type == 'correction' and not Record.objects.filter(id=draft.correction_record_id, patient=draft.patient, current_version=draft.correction_version).exists():
        raise ApiError('VERSION_CONFLICT', status.HTTP_409_CONFLICT)
    if request.method == "PUT":
        data = request.data or {}
        if 'expected_version' not in data or not isinstance(data.get('payload'), dict):
            raise ApiError('DRAFT_INPUT_INVALID', status.HTTP_422_UNPROCESSABLE_ENTITY)
        if draft.form_type == 'correction' and (data.get('payload', {}).get('recordId') != str(draft.correction_record_id) or data.get('payload', {}).get('version') != draft.correction_version):
            raise ApiError('DRAFT_TARGET_CHANGED', status.HTTP_409_CONFLICT)
        expected_version = int(data['expected_version'])
        draft = update_draft(draft=draft, expected_version=expected_version, payload=data['payload'])
    return Response(_draft_view(draft))


@api_view(["POST"])
@handle
def draft_abandon(request, draft_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    draft = Draft.objects.filter(id=draft_id, owner=principal).first()
    if draft is None:
        raise ApiError("DRAFT_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    authorize_case(request, principal, draft.patient, 'manage_draft')
    expected_version = int((request.data or {}).get("expected_version", draft.version))
    draft = abandon_draft(draft=draft, expected_version=expected_version)
    return Response(_draft_view(draft))


@transaction.atomic
def summary_listing(request, patient_id):
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
    principal, auth_epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    patient = get_patient(patient_id)
    decision = summary_permission(principal, patient, auth_epoch, audience, 'read_summary')
    return Response(list_summaries(patient, decision.grant.field_codes))


@api_view(["GET", "POST"])
@handle
def summary_collection(request, patient_id):
    if request.method == 'GET':
        return summary_listing(request, patient_id)
    principal, auth_epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError('AUTH_REQUIRED', status.HTTP_401_UNAUTHORIZED)
    patient = get_patient(patient_id)
    result = submit_summary(actor=principal, patient=patient, auth_epoch=auth_epoch, audience=audience,
        target=request.path, data=request.data, headers={'intent_id': request.headers.get('X-Write-Intent'),
            'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')})
    return Response(result, status=202)


@api_view(["GET"])
@handle
@transaction.atomic
def summary_read(request, summary_id):
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
    principal, auth_epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    summary = Summary.objects.filter(id=summary_id).first()
    if summary is None:
        raise ApiError("SUMMARY_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    decision = summary_permission(principal, summary.patient, auth_epoch, audience, 'read_summary')
    result = read_summary(summary_id, summary.patient)
    if any(item['snapshot']['field']['field_code'] not in decision.grant.field_codes for item in result['items']):
        result['needs_regeneration'] = True
    if result['needs_regeneration']:
        result['items'] = []
        result['trend_projection'] = None
    return Response(result)


@api_view(["POST"])
@handle
@transaction.atomic
def intent_prepare(request):
    principal, auth_epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    data = request.data or {}
    if data.get('operation_id') in CORRECTION_OPERATIONS:
        intent = prepare_correction_intent(data=data, actor=principal, epoch=auth_epoch, audience=audience,
            environment=settings.SJIA_ENVIRONMENT)
        return Response(intent_view(intent), status=201)
    if data.get('operation_id') in PROFESSIONAL_OPERATIONS:
        intent = prepare_professional_intent(actor=principal, auth_epoch=auth_epoch, audience=audience, data=data)
        return Response(intent_view(intent), status=201)
    from requests.policy import request_retention, require_request_policy_binding
    retention = request_retention(settings.SJIA_ENVIRONMENT)
    require_request_policy_binding(actor=principal, data=data, policy=retention)
    if data.get('operation_id') == 'alert_preset_select':
        from requests.alert_presets import prepare_preset_intent
        return Response(intent_view(prepare_preset_intent(actor=principal, auth_epoch=auth_epoch,
            audience=audience, data=data, retention_policy=retention)), status=201)
    if data.get('operation_id') == 'task_reconcile':
        from tasks.reconciliation import prepare_reconcile_intent
        return Response(intent_view(prepare_reconcile_intent(actor=principal, auth_epoch=auth_epoch,
            audience=audience, data=data, retention_policy=retention)), status=201)
    if data.get('operation_id') in CONTROLLED_OPERATIONS:
        intent = prepare_controlled_intent(request, data, retention)
        return Response(intent_view(intent), status=201)
    if data.get('operation_id') in APPLICATION_OPERATIONS:
        intent = prepare_application_intent(actor=principal, auth_epoch=auth_epoch, audience=audience,
            data=data, retention_policy=retention)
        return Response(intent_view(intent), status=201)
    if data.get('operation_id') == 'summary_create':
        intent = prepare_summary_intent(actor=principal, auth_epoch=auth_epoch, audience=audience,
            data=data, retention_policy=retention)
        return Response(intent_view(intent), status=201)
    if data.get('operation_id') == 'file_group_complete':
        from files.validation import prepare_validation_intent
        return Response(intent_view(prepare_validation_intent(actor=principal, auth_epoch=auth_epoch,
            audience=audience, data=data, retention_policy=retention)), status=201)
    if data.get('operation_id') in RECORD_OPERATIONS:
        intent = prepare_record_intent(actor=principal, auth_epoch=auth_epoch, audience=audience, data=data, retention_policy=retention)
        return Response(intent_view(intent), status=201)
    intent = prepare_intent(actor=principal, operation_id=data.get("operation_id", ""), target_key=data.get("target_key", ""), request_id=uuid.UUID(data.get("request_id", str(uuid.uuid4()))), payload=data.get("payload", {}), schema_version=data.get("schema_version", "back-json-1"), retention_policy=retention)
    return Response({"intent_id": str(intent.id), "generation": str(intent.generation), "status": intent.status, "expires_at": intent.expires_at.isoformat()}, status=201)


@api_view(["POST"])
@handle
def intent_retry(request, intent_id):
    principal, auth_epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    intent = WriteIntent.objects.filter(id=intent_id, actor=principal).first()
    if intent is None:
        raise ApiError("INTENT_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    if intent.operation_id in CORRECTION_OPERATIONS:
        intent = retry_correction_intent(intent, data=request.data, actor=principal, epoch=auth_epoch,
            audience=audience, environment=settings.SJIA_ENVIRONMENT)
        return Response(intent_view(intent))
    if intent.operation_id == 'alert_preset_select':
        from requests.alert_presets import retry_preset_intent
        data = ApplicationRetryInput.model_validate(request.data)
        return Response(intent_view(retry_preset_intent(actor=principal, auth_epoch=auth_epoch, audience=audience,
            intent=intent, expected_generation=data.expected_generation)))
    if intent.operation_id == 'task_reconcile':
        raise ApiError('RECONCILE_RETRY_NOT_AVAILABLE', 409)
    if intent.operation_id in CONTROLLED_OPERATIONS:
        return Response(intent_view(retry_controlled_intent(request, intent)))
    if intent.operation_id in APPLICATION_OPERATIONS:
        data = ApplicationRetryInput.model_validate(request.data)
        intent = retry_application_intent(actor=principal, auth_epoch=auth_epoch, audience=audience,
            intent_id=intent.id, expected_generation=data.expected_generation)
        return Response(intent_view(intent))
    if intent.operation_id == 'summary_create':
        data = SummaryRetryInput.model_validate(request.data)
        intent = retry_summary_intent(actor=principal, auth_epoch=auth_epoch, audience=audience,
            intent_id=intent.id, expected_generation=data.expected_generation)
        return Response(intent_view(intent))
    if intent.operation_id in PROFESSIONAL_OPERATIONS:
        data = ApplicationRetryInput.model_validate(request.data)
        intent = retry_professional_intent(actor=principal, auth_epoch=auth_epoch, audience=audience, intent=intent, expected_generation=data.expected_generation)
        return Response(intent_view(intent))
    if intent.operation_id == 'file_group_complete':
        from files.validation import retry_validation_intent
        data = ApplicationRetryInput.model_validate(request.data)
        return Response(intent_view(retry_validation_intent(actor=principal, auth_epoch=auth_epoch,
            audience=audience, intent=intent, expected_generation=data.expected_generation)))
    if intent.operation_id in RECORD_OPERATIONS:
        data = ApplicationRetryInput.model_validate(request.data)
        intent = retry_record_intent(actor=principal, auth_epoch=auth_epoch, audience=audience, intent_id=intent.id, expected_generation=data.expected_generation)
        return Response(intent_view(intent))
    if 'expected_generation' not in (request.data or {}):
        raise ApiError('EXPECTED_GENERATION_REQUIRED', 422)
    expected_generation = int(request.data["expected_generation"])
    intent = retry_intent(intent=intent, expected_generation=expected_generation)
    return Response({"intent_id": str(intent.id), "generation": str(intent.generation), "status": intent.status})


@api_view(['POST'])
@handle
def intent_cancel(request, intent_id):
    from requests.controlled import cancel_controlled_intent

    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError('AUTH_REQUIRED', 401)
    intent = WriteIntent.objects.get(id=intent_id, actor=principal, operation_id__in=CONTROLLED_OPERATIONS)
    cancel_controlled_intent(request, intent)
    return Response(controlled_request_view(request, intent))


@api_view(["GET"])
@handle
@transaction.atomic
def request_query(request, intent_id):
    principal, auth_epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    if request.query_params.get('lookup') == 'request_id':
        from requests.controlled import controlled_context, controlled_target

        if set(request.query_params) != {'lookup', 'operation_id', 'target_key'} or any(
                len(request.query_params.getlist(key)) != 1 for key in request.query_params):
            raise ApiError('CONTROLLED_QUERY_INVALID', 422)
        operation, target = request.query_params['operation_id'], request.query_params['target_key']
        if operation in CORRECTION_OPERATIONS:
            intent = WriteIntent.objects.get(request_id=intent_id, actor=principal, operation_id=operation, target_key=target)
            return Response(correction_request_view(intent, actor=principal, epoch=auth_epoch, audience=audience,
                environment=settings.SJIA_ENVIRONMENT))
        if operation == 'alert_preset_select':
            from requests.alert_presets import preset_request_view
            intent = WriteIntent.objects.get(request_id=intent_id, actor=principal, operation_id=operation, target_key=target)
            return Response(preset_request_view(actor=principal, auth_epoch=auth_epoch, audience=audience, intent=intent))
        if operation == 'task_reconcile':
            from tasks.reconciliation import reconciliation_request_view
            intent = WriteIntent.objects.get(request_id=intent_id, actor=principal, operation_id=operation, target_key=target)
            return Response(reconciliation_request_view(actor=principal, auth_epoch=auth_epoch,
                audience=audience, intent=intent))
        controlled_context(request, operation)
        controlled_target(operation, target)
        intent = WriteIntent.objects.filter(request_id=intent_id, actor=principal,
            operation_id=operation, target_key=target, patient__isnull=True).first()
        if intent is None:
            raise ApiError('INTENT_NOT_FOUND', status.HTTP_404_NOT_FOUND)
        return Response(controlled_request_view(request, intent))
    intent = WriteIntent.objects.filter(id=intent_id, actor=principal).first()
    if intent is None:
        raise ApiError("INTENT_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    if intent.operation_id in CORRECTION_OPERATIONS:
        if request.query_params:
            raise ApiError('CORRECTION_QUERY_INVALID', 422)
        return Response(correction_request_view(intent, actor=principal, epoch=auth_epoch, audience=audience,
            environment=settings.SJIA_ENVIRONMENT))
    if intent.operation_id == 'alert_preset_select':
        if request.query_params:
            raise ApiError('PRESET_QUERY_INVALID', 422)
        from requests.alert_presets import preset_request_view
        return Response(preset_request_view(actor=principal, auth_epoch=auth_epoch, audience=audience, intent=intent))
    if intent.operation_id == 'task_reconcile':
        if request.query_params:
            raise ApiError('RECONCILE_QUERY_INVALID', 422)
        from tasks.reconciliation import reconciliation_request_view
        return Response(reconciliation_request_view(actor=principal, auth_epoch=auth_epoch,
            audience=audience, intent=intent))
    if intent.operation_id in CONTROLLED_OPERATIONS:
        if request.query_params:
            raise ApiError('CONTROLLED_QUERY_INVALID', 422)
        return Response(controlled_request_view(request, intent))
    if intent.operation_id in APPLICATION_OPERATIONS:
        return Response(application_request_view(intent))
    if intent.operation_id == 'summary_create':
        summary_permission(principal, intent.patient, auth_epoch, audience)
        return Response(summary_request_view(intent))
    if intent.operation_id in PROFESSIONAL_OPERATIONS:
        return Response(professional_request_view(actor=principal, auth_epoch=auth_epoch, audience=audience, intent=intent))
    if intent.operation_id == 'file_group_complete':
        from files.validation import validation_request_view
        return Response(validation_request_view(actor=principal, auth_epoch=auth_epoch, audience=audience, intent=intent))
    if intent.operation_id in RECORD_OPERATIONS:
        return Response(record_request_view(actor=principal, auth_epoch=auth_epoch, audience=audience, intent=intent))
    if intent.patient_id is not None:
        authorize_case(request, principal, intent.patient, 'view_request')
    return Response({"operation_id": intent.operation_id, "target_key": intent.target_key, "status": intent.status, "prepared_at": intent.prepared_at.isoformat(), "resource_id": str(intent.write_request.resource_id) if intent.write_request and intent.write_request.resource_id else None})


@api_view(["GET"])
@handle
def request_recent_account(request):
    principal, _, audience = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    if audience == 'support':
        return Response(recent_controlled_requests(request))
    from requests.service import request_recent_account

    intents = request_recent_account(principal.id, ["application_create", "application_withdraw"])
    return Response({"requests": [application_request_view(intent) for intent in intents]})


@api_view(["GET"])
@handle
def request_recent(request, patient_id):
    principal, auth_epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    patient = get_patient(patient_id)
    if request.query_params.get('operation_id') == 'summary_create':
        authorize_case(request, principal, patient, "view_request")
        summary_permission(principal, patient, auth_epoch, audience)
        intents = WriteIntent.objects.filter(actor=principal, patient=patient, operation_id='summary_create').order_by('-prepared_at', '-id')
        return Response({'patient_id': str(patient.id), 'requests': [summary_request_view(intent) for intent in intents]})
    if request.query_params.get('operation_id') in RECORD_OPERATIONS:
        operation = request.query_params['operation_id']
        intents = WriteIntent.objects.filter(actor=principal, patient=patient, operation_id=operation, recovery_payload__isnull=False)
        if request.query_params.get('pending') == 'true':
            intents = intents.exclude(status='committed')
        record_id = request.query_params.get('record_id')
        if record_id:
            from uuid import UUID
            try:
                if str(UUID(record_id)) != record_id or operation != 'record_correct':
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise ApiError('RECORD_INTENT_INPUT_INVALID', 422) from None
            intents = intents.filter(target_key=f'/api/records/{record_id}/corrections')
        kind = request.query_params.get('kind')
        if kind:
            if kind not in ('observation', 'report', 'therapy'):
                raise ApiError('RECORD_INTENT_INPUT_INVALID', 422)
            intents = intents.filter(recovery_payload__kind=kind)
        rows = list(intents.order_by('-prepared_at', '-id')[:51])
        if len(rows) > 50:
            raise ApiError('RECORD_RECOVERY_LIMIT_EXCEEDED', 409)
        views = [record_request_view(actor=principal, auth_epoch=auth_epoch, audience=audience, intent=intent) for intent in rows]
        # Field-write admission takes the exclusive gate prefix before the
        # request-read check; never upgrade a held shared gate behind an actor lock.
        authorize_case(request, principal, patient, 'view_request')
        return Response({'patient_id': str(patient.id), 'requests': views})
    authorize_case(request, principal, patient, "view_request")
    intents = WriteIntent.objects.filter(actor=principal, patient=patient).order_by("-prepared_at")[:50]
    return Response({"requests": [{"intent_id": str(i.id), "operation_id": i.operation_id, "status": i.status} for i in intents]})


@api_view(["POST"])
@handle
@transaction.atomic
def history_search_view(request, patient_id):
    from records.history import begin_history_snapshot

    begin_history_snapshot()
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    patient = get_patient(patient_id)
    from authorization.policy import Denied, check_principal_session, environment_enabled
    from django.conf import settings
    from records.access import query_permissions

    try:
        environment_enabled(settings.SJIA_ENVIRONMENT)
        check_principal_session(principal, current_principal(request)[1])
    except Denied as exc:
        raise ApiError(exc.reason_code, status.HTTP_403_FORBIDDEN) from exc
    scopes = query_permissions(principal, patient)
    if not scopes:
        raise ApiError('CASE_ACTION_DENIED', 403)
    from records.history import checked_query

    query = checked_query(request.data)
    return Response(history_search(patient=patient, principal=principal, permission_scopes=scopes, **query))


@api_view(["POST"])
@handle
@transaction.atomic
def analyte_search_view(request, patient_id, concept_code):
    from records.history import begin_history_snapshot

    begin_history_snapshot()
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    patient = get_patient(patient_id)
    from authorization.policy import Denied, check_principal_session, environment_enabled
    from django.conf import settings
    from records.access import query_permissions

    try:
        environment_enabled(settings.SJIA_ENVIRONMENT)
        check_principal_session(principal, current_principal(request)[1])
    except Denied as exc:
        raise ApiError(exc.reason_code, 403) from exc
    scopes = query_permissions(principal, patient)
    if not scopes:
        raise ApiError('CASE_ACTION_DENIED', 403)
    if request.data != {}:
        raise ApiError('ANALYTE_QUERY_INVALID')
    return Response(analyte_search(patient=patient, concept_code=concept_code,
        principal=principal, permission_scopes=scopes))
