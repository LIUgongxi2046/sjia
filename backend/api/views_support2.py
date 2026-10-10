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
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "manage_config")
    patient = get_patient(patient_id)
    retention = RetentionPolicy.objects.filter(status="active").first()
    use_auth = DataUseAuthorization.objects.filter(patient=patient).first()
    data = request.data or {}
    result = create_record(patient=patient, actor=principal, kind=data.get("kind", "professional"), capture_method="authorized_import", origin_type="authorized_clinical", retention_policy=retention, use_authorization_id=use_auth.id, fields=data.get("fields", []))
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
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "manage_config")
    task = Task.objects.filter(id=task_id).first()
    if task is None:
        raise ApiError("TASK_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    return Response({"task_id": str(task.id), "status": task.status})


@api_view(["POST"])
@handle
def correction_request_resolve(request, correction_request_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "manage_config")
    from verification.models import CorrectionRequest

    correction = CorrectionRequest.objects.filter(id=correction_request_id).first()
    if correction is None:
        raise ApiError("CORRECTION_REQUEST_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    correction.status = "resolved"
    correction.resolved_by = principal
    correction.resolved_at = datetime.now(timezone.utc)
    correction.save(update_fields=["status", "resolved_by", "resolved_at"])
    return Response({"correction_request_id": str(correction.id), "status": correction.status})


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
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "manage_config")
    record = Record.objects.filter(id=record_id).first()
    if record is None:
        raise ApiError("RECORD_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    retention = RetentionPolicy.objects.filter(status="active").first()
    use_auth = DataUseAuthorization.objects.filter(patient=record.patient).first()
    if retention is None or use_auth is None:
        raise ApiError("CONFIGURATION_PENDING", status.HTTP_503_SERVICE_UNAVAILABLE)
    data = request.data or {}
    result = correct_record(
        record=record, expected_version=int(data.get("expected_version", record.current_version)),
        actor=principal, reason=data.get("reason", ""), fields=data.get("fields", []),
        use_authorization_id=use_auth.id, retention_policy=retention,
        capture_method="authorized_import", source_revision=SourceRevision.objects.first(),
        origin_type=RecordVersion.OriginType.AUTHORIZED_CLINICAL,
    )
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
