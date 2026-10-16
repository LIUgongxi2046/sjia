"""受控支持端 API：配置、来源、保留、联系、发布、核对、处置与审计。"""
import uuid
from datetime import datetime, timedelta, timezone

from django.db import transaction

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, authorize_service, current_principal, get_patient, handle
from authorization.models import AccessGrant, ServiceRoleGrant
from authorization.policy import matching_case_grants
from configurations.models import ConfigVersion
from family_content.models import EnvironmentGate
from identity.models import Principal
from relations.models import Relation, RelationApplication
from relations.service import decide_application, revoke_relation
from retention.disposition import approve_disposition_request, mark_item_deleted
from retention.models import DispositionItem, DispositionRequest, RetentionPolicy
from sources.models import DataUseAuthorization, SourceRevision
from sources.service import create_use_authorization, register_revision, register_source
from verification.models import Verification, VerificationField, VerificationRequest
from verification.service import revoke_verification, verify_fields
from files.purpose import runtime_environment
from files.purpose_schemas import PurposeCaseGrantInput, PurposeDispositionApprovalInput, PurposeDispositionReceiptInput, PurposeServiceGrantInput, PurposeUseAuthorizationInput
from files.purpose_dependencies import invalidate_resource


@api_view(["GET"])
@handle
@transaction.atomic
def application_review(request, application_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "decide_relation")
    application = RelationApplication.objects.select_for_update().filter(id=application_id).first()
    if application is None:
        raise ApiError("APPLICATION_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    from relations.queries import application_declaration_view
    return Response({"application_id": str(application.id), "applicant_id": str(application.applicant_id),
        "patient_id": str(application.patient_id) if application.patient_id else None,
        "status": application.status, "version": str(application.version),
        "evidence_ref": application.evidence_ref, "note": application.note,
        "requested_access": application.requested_access, "created_at": application.created_at.isoformat(),
        "checked_at": application.checked_at.isoformat() if application.checked_at else None, "reason": application.reason,
        **application_declaration_view(application)})


@api_view(["POST"])
@handle
@transaction.atomic
def application_decide(request, application_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "decide_relation")
    application = RelationApplication.objects.filter(id=application_id).first()
    if application is None:
        raise ApiError("APPLICATION_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    data = request.data or {}
    from relations.inputs import ApplicationDecisionInput

    data = ApplicationDecisionInput.model_validate(data)
    relation = decide_application(application=application, decision=data.decision, checked_by=principal,
        expected_version=int(data.expected_version), action=data.action, field_codes=data.field_codes, purpose="family_observation",
        patient=get_patient(data.patient_id) if data.patient_id else None, reason=data.reason)
    application.refresh_from_db(fields=['status', 'version'])
    return Response({"relation_id": str(relation.id) if relation else None, "status": application.status, "version": str(application.version)})


@api_view(["POST"])
@handle
@transaction.atomic
def relation_revoke(request, relation_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "decide_relation")
    relation = Relation.objects.filter(id=relation_id).first()
    if relation is None:
        raise ApiError("RELATION_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    relation = revoke_relation(relation=relation, reason=(request.data or {}).get("reason", ""), revoked_by=principal)
    return Response({"relation_id": str(relation.id), "status": relation.status})


@api_view(["POST"])
@handle
def source_register(request):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "manage_config")
    data = request.data or {}
    source = register_source(source_key=data.get("source_key", ""), owner=data.get("owner", ""), location=data.get("location", ""), purpose=data.get("purpose", ""), sensitivity=data.get("sensitivity", ""), update_frequency=data.get("update_frequency", ""))
    return Response({"source_id": str(source.id), "source_key": source.source_key, "status": source.status}, status=201)


@api_view(["POST"])
@handle
def source_revision_register(request, source_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "manage_config")
    from sources.models import SourceRegistry

    source = SourceRegistry.objects.filter(id=source_id).first()
    if source is None:
        raise ApiError("SOURCE_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    data = request.data or {}
    revision = register_revision(source=source, version=data.get("version", "v1"), checksum=data.get("checksum", "c" * 64), license=data.get("license", ""), allowed_use=data.get("allowed_use", []))
    return Response({"source_revision_id": str(revision.id), "status": revision.status}, status=201)


@api_view(["POST"])
@handle
def retention_policy_create(request):
    # Legacy import compatibility uses the same intent/evidence checks as the
    # canonical views_sources route; registration must never imply activation.
    from api.views_sources import source_write

    with transaction.atomic():
        return source_write(request, 'retention_policy_create', {})


@api_view(["POST"])
@handle
@transaction.atomic
def use_authorization_create(request, patient_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "manage_data_use")
    patient = get_patient(patient_id)
    from sources.models import SourceRevision

    data = PurposeUseAuthorizationInput.model_validate(request.data)
    now = datetime.now(timezone.utc)
    EnvironmentGate.objects.select_for_update().get(environment=runtime_environment())
    principal = Principal.objects.select_for_update().get(id=principal.id)
    authorize_service(request, principal, 'manage_data_use')
    grants = [grant for grant in matching_case_grants(principal, patient, 'read_case', now)
        if grant.purpose == data.purpose and not grant.field_codes and grant.evidence_ref.strip()]
    list(AccessGrant.objects.select_for_update().filter(id__in=[grant.id for grant in grants]))
    current_ids = {grant.id for grant in matching_case_grants(principal, patient, 'read_case', datetime.now(timezone.utc))
        if grant.purpose == data.purpose and not grant.field_codes and grant.evidence_ref.strip()}
    if not current_ids.intersection(grant.id for grant in grants):
        raise ApiError('PURPOSE_CASE_SCOPE_DENIED', 403)
    revision = SourceRevision.objects.select_for_update().select_related('source').get(id=data.source_revision_id)
    if (data.environment != runtime_environment() or revision.status != 'approved' or revision.source.status != 'approved'
            or not revision.rights_evidence_ref or not isinstance(revision.allowed_use, list)
            or data.purpose not in revision.allowed_use or (data.valid_until is not None and data.valid_until <= now)):
        raise ApiError('PURPOSE_AUTHORIZATION_UNAVAILABLE', 409)
    auth = create_use_authorization(patient=patient, source_revision=revision, purpose=data.purpose,
        environment=data.environment, evidence_ref=data.evidence_ref, approved_by=principal,
        deidentification_ref=data.deidentification_ref, valid_until=data.valid_until)
    return Response({"authorization_id": str(auth.id)}, status=201)


@api_view(["POST"])
@handle
def verification_submit(request):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "verify_field")
    raise ApiError('VERIFICATION_API_VERSION_REQUIRED', 422, '请使用 API v2 提交准确核对请求与字段版本。')


@api_view(["POST"])
@handle
def verification_revoke(request, verification_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "revoke_verification")
    raise ApiError('VERIFICATION_API_VERSION_REQUIRED', 422, '请使用 API v2 提交准确核对证明与撤销原因。')


@api_view(["POST"])
@handle
@transaction.atomic
def disposition_approve(request, disposition_id):
    principal, epoch, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "manage_disposition")
    disposition = DispositionRequest.objects.filter(id=disposition_id).first()
    if disposition is None:
        raise ApiError("DISPOSITION_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    payload = PurposeDispositionApprovalInput.model_validate(request.data)
    disposition = approve_disposition_request(request=disposition, approved_by=principal, auth_epoch=epoch, payload=payload)
    return Response({"disposition_id": str(disposition.id), "status": disposition.status})


@api_view(["POST"])
@handle
@transaction.atomic
def disposition_execute(request, disposition_id):
    principal, epoch, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "manage_disposition")
    data = PurposeDispositionReceiptInput.model_validate(request.data)
    item = DispositionItem.objects.filter(id=data.item_id, request_id=disposition_id).first()
    if item is None:
        raise ApiError("DISPOSITION_ITEM_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    item = mark_item_deleted(item=item, receipt_ref=data.receipt_ref, receipt_sha256=data.receipt_sha256, executed_by=principal, auth_epoch=epoch)
    return Response({"item_id": str(item.id), "status": item.status})


@api_view(["POST"])
@handle
def audit_search(request):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "read_assessment")
    from audit.models import AuditEvent

    events = AuditEvent.objects.all().order_by("-occurred_at")[:50]
    return Response({"audit": [{"action": e.action, "object_type": e.object_type, "outcome": e.outcome, "occurred_at": e.occurred_at.isoformat()} for e in events]})


@api_view(["POST"])
@handle
@transaction.atomic
def grant_create(request):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "manage_grant")
    data = PurposeCaseGrantInput.model_validate(request.data)
    patient = get_patient(data.patient_id)
    target = Principal.objects.filter(id=data.principal_id, account_status='active').first()
    if target is None:
        raise ApiError("PRINCIPAL_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    if data.relation_id is not None:
        Relation.objects.get(id=data.relation_id, patient=patient, guardian=target, status='active')
    if data.assigned_record_id is not None:
        from records.models import Record

        Record.objects.get(id=data.assigned_record_id, patient=patient)
    grant = AccessGrant.objects.create(patient=patient, principal=target, relation_id=data.relation_id,
        action=data.action, field_codes=data.field_codes, record_scope=data.record_scope,
        assigned_record_id=data.assigned_record_id, purpose=data.purpose, granted_by=principal,
        evidence_ref=data.evidence_ref, valid_from=data.valid_from, valid_until=data.valid_until)
    return Response({"grant_id": str(grant.id), "action": grant.action, "record_scope": grant.record_scope}, status=201)


@api_view(["POST"])
@handle
@transaction.atomic
def grant_revoke(request, grant_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "manage_grant")
    grant = AccessGrant.objects.filter(id=grant_id).first()
    if grant is None:
        raise ApiError("GRANT_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    if grant.revoked_at is None:
        grant.revoked_at = datetime.now(timezone.utc)
        grant.save(update_fields=["revoked_at"])
    invalidate_resource('access_grant', grant.id)
    return Response({"grant_id": str(grant.id), "revoked_at": grant.revoked_at.isoformat()})


@api_view(["POST"])
@handle
@transaction.atomic
def role_grant_create(request):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "manage_grant")
    data = PurposeServiceGrantInput.model_validate(request.data)
    target = Principal.objects.filter(id=data.principal_id, account_status='active').first()
    if target is None:
        raise ApiError("PRINCIPAL_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    if data.environment != runtime_environment():
        raise ApiError('PURPOSE_ENVIRONMENT_UNAVAILABLE', 409)
    gate = EnvironmentGate.objects.filter(environment=data.environment, enabled=True).first()
    if gate is None:
        raise ApiError("CONFIG_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    role_grant = ServiceRoleGrant.objects.create(
        principal=target, environment=gate, action=data.action,
        granted_by=principal, evidence_ref=data.evidence_ref,
        valid_from=data.valid_from, valid_until=data.valid_until,
    )
    return Response({"role_grant_id": str(role_grant.id), "action": role_grant.action}, status=201)


@api_view(["POST"])
@handle
@transaction.atomic
def role_grant_revoke(request, grant_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "manage_grant")
    role_grant = ServiceRoleGrant.objects.filter(id=grant_id).first()
    if role_grant is None:
        raise ApiError("GRANT_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    if role_grant.revoked_at is None:
        role_grant.revoked_at = datetime.now(timezone.utc)
        role_grant.revoked_by = principal
        role_grant.revocation_reason = (request.data or {}).get("reason", "")
        role_grant.save(update_fields=["revoked_at", "revoked_by", "revocation_reason"])
    invalidate_resource('service_role_grant', role_grant.id)
    return Response({"role_grant_id": str(role_grant.id), "revoked_at": role_grant.revoked_at.isoformat()})


@api_view(["POST"])
@handle
def verification_request_create(request):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "verify_field")
    raise ApiError('VERIFICATION_API_VERSION_REQUIRED', 422, '请使用 API v2 创建准确版本与字段范围的核对请求。')


@api_view(["GET"])
@handle
def verification_request_read(request, verification_request_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "verify_field")
    raise ApiError('VERIFICATION_API_VERSION_REQUIRED', 422, '请使用 API v2 读取准确核对请求。')


@api_view(["GET"])
@handle
def verification_read(request, verification_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "verify_field")
    raise ApiError('VERIFICATION_API_VERSION_REQUIRED', 422, '请使用 API v2 读取准确核对证明与当前状态。')


@api_view(["POST"])
@handle
@transaction.atomic
def runtime_activate(request, environment):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "manage_config")
    gate = EnvironmentGate.objects.select_for_update().filter(environment=environment).first()
    if gate is None:
        raise ApiError("CONFIG_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    data = request.data or {}
    runtime_config = ConfigVersion.objects.filter(id=data.get("runtime_config_id")).first()
    if runtime_config is None:
        raise ApiError("CONFIG_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    gate.runtime_config = runtime_config
    gate.version = gate.version + 1
    gate.publication_epoch = gate.publication_epoch + 1
    gate.updated_at = datetime.now(timezone.utc)
    gate.save(update_fields=["runtime_config", "version", "publication_epoch", "updated_at"])
    return Response({"environment": gate.environment, "gate_version": str(gate.version), "runtime_config_id": str(gate.runtime_config_id)})


@api_view(["POST"])
@handle
@transaction.atomic
def source_revision_revoke(request, source_revision_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "manage_config")
    revision = SourceRevision.objects.filter(id=source_revision_id).first()
    if revision is None:
        raise ApiError("SOURCE_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    revision.status = SourceRevision.Status.REVOKED
    revision.save(update_fields=["status"])
    invalidate_resource('source_revision', revision.id, revision.version)
    return Response({"source_revision_id": str(revision.id), "status": revision.status})


@api_view(["POST"])
@handle
@transaction.atomic
def use_authorization_revoke(request, authorization_id):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError("AUTH_REQUIRED", status.HTTP_401_UNAUTHORIZED)
    authorize_service(request, principal, "manage_grant")
    authorization = DataUseAuthorization.objects.filter(id=authorization_id).first()
    if authorization is None:
        raise ApiError("AUTHORIZATION_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    if authorization.revoked_at is None:
        authorization.revoked_at = datetime.now(timezone.utc)
        authorization.save(update_fields=["revoked_at"])
    invalidate_resource('data_use_authorization', authorization.id)
    return Response({"authorization_id": str(authorization.id), "revoked_at": authorization.revoked_at.isoformat()})


@api_view(["POST"])
@handle
def retention_policy_activate(request, policy_id):
    from api.views_sources import source_write

    with transaction.atomic():
        return source_write(request, 'retention_policy_activate', {'policy_id': policy_id})
