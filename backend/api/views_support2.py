"""受控支持端 API：评估、病例、专业记录、任务与配置停用。"""
import uuid
from datetime import datetime, timezone

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, authorize_case, authorize_service, current_principal, get_patient, handle
from alerting.models import AlertEvaluation
from case_details.case_import import import_case
from case_details.models import (
    ClinicalEpisode,
    ClinicalStatement,
    DataQualityIssue,
    ExternalScoreObservation,
    FollowupEncounter,
    ImagingStudy,
    MedicationEvent,
    PatientProfileVersion,
    ProcedureEvent,
    RecordFieldGroup,
)
from configurations.models import ConfigVersion
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from identity.service import disable_account
from notifications.availability import receive_provider_callback
from records.correction import correct_record
from records.models import Record, RecordVersion
from records.service import create_record
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, SourceRevision
from tasks.models import Task


@api_view(["POST"])
@handle
def assessment_create(request, patient_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "execute_assessment")
    raise ApiError('ASSESSMENT_API_VERSION_REQUIRED', 422, '请使用支持评估的 API v2 接口提交准确资料引用。')


@api_view(["GET"])
@handle
def assessment_read(request, assessment_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "read_assessment")
    raise ApiError('ASSESSMENT_API_VERSION_REQUIRED', 422, '请使用支持评估的 API v2 接口读取保存结果。')


@api_view(["POST"])
@handle
def case_import(request, patient_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "import_case")
    patient = get_patient(patient_id)
    retention = RetentionPolicy.objects.filter(status="active").first()
    use_auth = DataUseAuthorization.objects.filter(patient=patient).first()
    from sources.models import SourceRevision

    revision = SourceRevision.objects.first()
    result = import_case(patient=patient, operator=principal, retention_policy=retention, use_authorization_id=use_auth.id, source_revision=revision)
    return Response(result, status=201)


@api_view(["POST"])
@handle
def professional_record_create(request, patient_id):
    from records.professional import submit_professional_record

    principal, epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError('AUTH_REQUIRED', 401)
    result = submit_professional_record(actor=principal, auth_epoch=epoch, audience=audience,
        operation='professional_record_create', target=request.path, payload=request.data, headers=request.headers)
    return Response(result, status=201)


@api_view(["POST"])
@handle
def principal_disable(request, principal_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "manage_grant")
    target = Principal.objects.filter(id=principal_id).first()
    if target is None:
        raise ApiError("PRINCIPAL_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    disable_account(target)
    return Response({"principal_id": str(target.id), "account_status": target.account_status})


@api_view(["POST"])
@handle
def task_retry(request, task_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    task = Task.objects.filter(id=task_id, actor=principal).first()
    if task is None:
        raise ApiError("TASK_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    if task.task_type != Task.TaskType.OCR:
        raise ApiError('TASK_NOT_RETRYABLE', status.HTTP_409_CONFLICT)
    authorize_case(request, principal, task.patient, 'write_record')
    authorize_case(request, principal, task.patient, 'read_file')
    from recognition.jobs import retry_recognition

    task = retry_recognition(task, principal)
    return Response({"task_id": str(task.id), "status": task.status})


@api_view(["POST"])
@handle
def task_reconcile(request, task_id):
    from tasks.reconciliation import submit_reconcile
    principal, epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", 401)
    return Response(submit_reconcile(actor=principal, auth_epoch=epoch, audience=audience,
        target=request.path, data=request.data, headers={
            'intent_id': request.headers.get('X-Write-Intent'),
            'request_id': request.headers.get('Idempotency-Key'),
            'generation': request.headers.get('X-Intent-Generation')}), status=202)


@api_view(["GET"])
@handle
def task_reconcile_read(request, task_id):
    from tasks.reconciliation import read_reconcile
    principal, epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", 401)
    return Response(read_reconcile(actor=principal, auth_epoch=epoch, audience=audience, task_id=task_id))


@api_view(["POST"])
@handle
def correction_request_resolve(request, correction_request_id):
    principal, epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience != 'support':
        raise ApiError('SUPPORT_AUDIENCE_REQUIRED', 403)
    from django.conf import settings
    from verification.correction_requests import submit_correction

    result = submit_correction(operation='correction_request_resolve', target=request.path, payload=request.data,
        actor=principal, epoch=epoch, audience=audience, environment=settings.SJIA_ENVIRONMENT,
        headers={'intent_id': request.headers.get('X-Write-Intent'), 'request_id': request.headers.get('Idempotency-Key'),
            'generation': request.headers.get('X-Intent-Generation')})
    return Response(result)


@api_view(["POST"])
@handle
def case_search(request, patient_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    patient = get_patient(patient_id)
    authorize_case(request, principal, patient, "read_case")
    profile = PatientProfileVersion.objects.filter(patient_id=patient.id).first()
    return Response({
        "patient_id": str(patient.id),
        "clinical_rule_eligible": False,
        "profile": {"sex": profile.sex, "age_raw_text": profile.age_raw_text, "chief_complaint": profile.chief_complaint} if profile else None,
        "counts": {
            "episodes": ClinicalEpisode.objects.filter(patient_id=patient.id).count(),
            "statements": ClinicalStatement.objects.filter(patient_id=patient.id).count(),
            "groups": RecordFieldGroup.objects.filter(patient_id=patient.id).count(),
            "imaging_studies": ImagingStudy.objects.filter(patient_id=patient.id).count(),
            "medication_events": MedicationEvent.objects.filter(patient_id=patient.id).count(),
            "procedures": ProcedureEvent.objects.filter(patient_id=patient.id).count(),
            "scores": ExternalScoreObservation.objects.filter(patient_id=patient.id).count(),
            "followups": FollowupEncounter.objects.filter(patient_id=patient.id).count(),
            "quality_issues": DataQualityIssue.objects.filter(patient_id=patient.id).count(),
        },
    })


@api_view(["POST"])
@handle
def professional_record_correct(request, record_id):
    from records.professional import submit_professional_record

    principal, epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError('AUTH_REQUIRED', 401)
    result = submit_professional_record(actor=principal, auth_epoch=epoch, audience=audience,
        operation='professional_record_correct', target=request.path, payload=request.data, headers=request.headers)
    return Response(result, status=201)


@api_view(["POST"])
@handle
def alert_evaluation_create(request, patient_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    raise ApiError('API_V2_REQUIRED', status.HTTP_409_CONFLICT)


@api_view(["GET"])
@handle
def alert_evaluation_read(request, evaluation_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    raise ApiError('API_V2_REQUIRED', status.HTTP_409_CONFLICT)


@api_view(["POST"])
@handle
def provider_callback(request, provider_id):
    receive_provider_callback(provider_id=provider_id, request=request)
