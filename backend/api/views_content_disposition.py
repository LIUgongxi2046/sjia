"""Versioned generic disposition endpoints; old file-only routes stay intact."""
from uuid import UUID
from django.conf import settings
from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response
from api.base import ApiError, handle
from api.views_purpose import support_principal
from retention.content_engine import (approve_plan, execute_plan, preview_plan,
    _current_plan, _identity, write_barrier, read_plan)
from retention.content_registry import REGISTRY, REGISTRY_SHA256, PRESERVATION_SHA256
from retention.content_schemas import Contract, ContentPlanInput, ContentPlanView, Digest
from pydantic import Field
from assessment.qualified_facts import QualificationRejected


class PlanRequest(Contract):
    family_request_id: UUID | None = None
    policy_id: UUID
    request: ContentPlanInput


class ApprovalRequest(Contract):
    expected_manifest_sha256: Digest
    source_revision_id: UUID


class ExecuteRequest(Contract):
    expected_manifest_sha256: Digest


def view(plan):
    return ContentPlanView.model_validate({'schema_version':'content-disposition-plan-view-1','plan_id':str(plan.id),
        'patient_id':str(plan.patient_id),'status':plan.status,'manifest':plan.manifest,
        'manifest_sha256':plan.manifest_sha256,'approved_at':plan.approved_at.isoformat() if plan.approved_at else None,
        'completed_at':plan.completed_at.isoformat() if plan.completed_at else None,
        'receipt':plan.receipt,'runtime_execution_enabled':getattr(settings,'SJIA_CONTENT_DISPOSITION_ENABLED',False)}).model_dump(mode='json')


@api_view(['GET'])
@handle
@transaction.atomic
def content_disposition_catalog(request):
    from authorization.policy import check_service_grant
    from django.utils import timezone
    actor,epoch=support_principal(request)
    check_service_grant(actor,'manage_disposition',settings.SJIA_ENVIRONMENT,timezone.now())
    return Response({'schema_version':'content-disposition-catalog-1','registry_sha256':REGISTRY_SHA256,
        'preservation_sha256':PRESERVATION_SHA256,'profile':'relationship-skeleton-1',
        'runtime_execution_enabled':getattr(settings,'SJIA_CONTENT_DISPOSITION_ENABLED',False),
        'default_policy':None,'adapters':[{'adapter':label,'payload_fields':spec['payload_fields'],
            'preserved_fields':spec['preserved_fields']} for label,spec in sorted(REGISTRY.items())],
        'unsupported':['account_identity_and_sessions','shared_evaluation_output_partition_erasure',
            'ark_chat_completion_provider_copy_erasure','physical_database_pages_and_wal_erasure'],
        'proof_scope':'logical_payload_and_controlled_local_path_absence'})


@api_view(['POST'])
@handle
def content_disposition_preview(request,patient_id):
    actor,epoch=support_principal(request)
    payload=PlanRequest.model_validate(request.data)
    plan=preview_plan(actor=actor,auth_epoch=epoch,patient_id=patient_id,policy_id=payload.policy_id,
        data=payload.request.model_dump(mode='json'),family_request_id=payload.family_request_id)
    return Response(view(plan),status=201)


@api_view(['GET'])
@handle
@transaction.atomic
def content_disposition_read(request,plan_id):
    actor,epoch=support_principal(request)
    write_barrier()
    plan = read_plan(plan_id=plan_id,actor=actor,auth_epoch=epoch)
    return Response(view(plan))


@api_view(['POST'])
@handle
def content_disposition_approve(request,plan_id):
    actor,epoch=support_principal(request)
    payload=ApprovalRequest.model_validate(request.data)
    plan=approve_plan(plan_id=plan_id,actor=actor,auth_epoch=epoch,**payload.model_dump())
    return Response(view(plan))


@api_view(['POST'])
@handle
def content_disposition_execute(request,plan_id):
    actor,epoch=support_principal(request)
    payload=ExecuteRequest.model_validate(request.data)
    plan=execute_plan(plan_id=plan_id,actor=actor,auth_epoch=epoch,**payload.model_dump())
    return Response(view(plan))


class ContentReviewRequest(Contract):
    source_revision_id: UUID
    attestation: 'ContentReviewAttestation'


from retention.content_schemas import ContentReviewAttestation
ContentReviewRequest.model_rebuild()


def _permitted_patient(actor,epoch,patient):
    from authorization.models import AccessGrant
    from authorization.policy import Denied
    purposes=AccessGrant.objects.filter(principal=actor,patient=patient,action='read_case',
        record_scope='all',field_codes=[]).values_list('purpose',flat=True).distinct()
    for purpose in purposes:
        try:
            _identity(actor,epoch,patient,purpose)
            return
        except (Denied,QualificationRejected) as exc:
            if isinstance(exc,QualificationRejected) and exc.reason_code!='ASSESSMENT_FIELD_SCOPE_DENIED':
                raise
            continue
    raise ApiError('CONTENT_WHOLE_RESOURCE_AUTHORIZATION_REQUIRED',403)


@api_view(['GET'])
@handle
@transaction.atomic
def content_disposition_patients(request):
    from authorization.models import AccessGrant
    from authorization.policy import check_service_grant
    from identity.models import Patient
    from django.utils import timezone
    actor,epoch=support_principal(request)
    check_service_grant(actor,'manage_disposition',settings.SJIA_ENVIRONMENT,timezone.now())
    rows=Patient.objects.filter(id__in=AccessGrant.objects.filter(principal=actor,action='read_case',
        record_scope='all',field_codes=[]).values('patient_id')).order_by('display_id','id')
    if rows.count()>500:
        raise ApiError('CONTENT_INVENTORY_LIMIT_EXCEEDED',409)
    result=[]
    for patient in rows:
        try:
            _permitted_patient(actor,epoch,patient)
        except ApiError:
            continue
        result.append({'patient_id':str(patient.id),'display_id':patient.display_id})
    return Response({'schema_version':'content-disposition-patients-1','patients':result})


@api_view(['GET'])
@handle
@transaction.atomic
def content_disposition_options(request,patient_id):
    from identity.models import Patient
    from retention.models import ContentDispositionPolicy,ContentDispositionHold
    from retention.content_engine import _policy,_hold
    from retention.content_registry import patient_rows,row_version
    from retention.disposition import DispositionRejected
    from authorization.policy import Denied
    from files.models import FileObject
    actor,epoch=support_principal(request)
    patient=Patient.objects.select_for_update().get(id=patient_id)
    _permitted_patient(actor,epoch,patient)
    policies=[]
    for candidate in ContentDispositionPolicy.objects.filter(environment=settings.SJIA_ENVIRONMENT).order_by('created_at','id'):
        try:
            policy,rules=_policy(candidate.id)
            _identity(actor,epoch,patient,rules.access_purpose)
        except (DispositionRejected,Denied,QualificationRejected) as exc:
            if isinstance(exc,QualificationRejected) and exc.reason_code!='ASSESSMENT_FIELD_SCOPE_DENIED':
                raise
            continue
        policies.append({'policy_id':str(policy.id),'version':policy.version,'mode':rules.mode,
            'execution_enabled':policy.enabled,'retention_seconds':rules.retention_seconds,'clock':rules.clock,
            'access_purpose':rules.access_purpose,'recheck_at':rules.recheck_at.isoformat()})
    hold=ContentDispositionHold.objects.filter(patient=patient).order_by('-version','id').first()
    hold_value=None
    if hold:
        reason=None
        try:
            _hold(patient.id,hold.id,hold.version)
        except (DispositionRejected,Denied) as exc:
            reason=exc.reason_code
        hold_value={'hold_id':str(hold.id),'version':hold.version,'state':hold.state,
            'valid_until':hold.valid_until.isoformat(),'eligible':reason is None,'reason_code':reason}
    targets=[]
    for row in patient_rows(patient.id):
        if row.content_erased_at is not None:
            continue
        targets.append({'target':{'kind':'database','adapter':row._meta.label_lower,
            'object_id':str(row.pk),'version':row_version(row)},'label':row._meta.verbose_name,
            'record_id':str(row.record_id) if getattr(row,'record_id',None) else None})
    for row in FileObject.objects.filter(patient=patient).exclude(status='disposed').order_by('id'):
        targets.append({'target':{'kind':'local_object','file_id':str(row.id),'object_version':row.object_version},
            'label':f'本地文件 · {row.purpose}','record_id':None})
    if len(targets)>5000:
        raise ApiError('CONTENT_INVENTORY_LIMIT_EXCEEDED',409)
    return Response({'schema_version':'content-disposition-options-1','patient_id':str(patient.id),
        'display_id':patient.display_id,'policies':policies,'hold':hold_value,'targets':targets,'default_policy':None})


@api_view(['GET'])
@handle
@transaction.atomic
def content_disposition_history(request,patient_id):
    from identity.models import Patient
    from retention.models import ContentDispositionPlan
    from retention.content_lifecycle import lifecycle
    from authorization.policy import Denied
    actor,epoch=support_principal(request)
    patient=Patient.objects.select_for_update().get(id=patient_id)
    _permitted_patient(actor,epoch,patient)
    plans=ContentDispositionPlan.objects.filter(patient=patient).order_by('-created_at','id')
    if plans.count()>500:
        raise ApiError('CONTENT_HISTORY_PAGINATION_REQUIRED',409)
    result=[]
    for plan in plans:
        try:
            read_plan(plan_id=plan.id,actor=actor,auth_epoch=epoch)
        except (Denied,QualificationRejected) as exc:
            if isinstance(exc,QualificationRejected) and exc.reason_code!='ASSESSMENT_FIELD_SCOPE_DENIED':
                raise
            continue
        result.append({'plan_id':str(plan.id),'status':plan.status,'created_at':plan.created_at.isoformat(),
            'mode':plan.manifest['mode'],'item_count':len(plan.manifest['items']),'lifecycle':lifecycle(plan)})
    return Response({'schema_version':'content-disposition-history-1','patient_id':str(patient_id),'plans':result})


@api_view(['GET','POST'])
@handle
@transaction.atomic
def content_disposition_review(request,plan_id):
    from retention.content_lifecycle import lifecycle,record_review
    actor,epoch=support_principal(request)
    plan=read_plan(plan_id=plan_id,actor=actor,auth_epoch=epoch)
    if request.method=='POST':
        payload=ContentReviewRequest.model_validate(request.data)
        record_review(plan_id=plan_id,actor=actor,auth_epoch=epoch,
            data=payload.attestation.model_dump(mode='json'),source_revision_id=payload.source_revision_id)
    return Response(lifecycle(plan))


@api_view(['GET'])
@handle
@transaction.atomic
def content_disposition_family_requests(request,patient_id):
    from identity.models import Patient
    from retention.models import ContentDispositionFamilyRequest
    from retention.content_family import family_request_view
    from authorization.policy import Denied
    from retention.content_schemas import ContentFamilyRequests,ContentPolicyRules
    actor,epoch=support_principal(request)
    patient=Patient.objects.select_for_update().get(id=patient_id)
    _permitted_patient(actor,epoch,patient)
    rows=ContentDispositionFamilyRequest.objects.filter(patient=patient).order_by('-created_at','id')
    if rows.count()>500:
        raise ApiError('CONTENT_HISTORY_PAGINATION_REQUIRED',409)
    result=[]
    for row in rows:
        try:
            rules=ContentPolicyRules.model_validate(row.policy.rules)
            _identity(actor,epoch,patient,rules.access_purpose)
        except (Denied,QualificationRejected) as exc:
            if isinstance(exc,QualificationRejected) and exc.reason_code!='ASSESSMENT_FIELD_SCOPE_DENIED':
                raise
            continue
        result.append(family_request_view(row))
    return Response(ContentFamilyRequests.model_validate({'schema_version':'content-disposition-family-requests-1',
        'patient_id':str(patient.id),'requests':result}).model_dump(mode='json'))
