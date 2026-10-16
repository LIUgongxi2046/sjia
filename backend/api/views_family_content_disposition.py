"""Restricted family application bridge; no family approval or execution route."""
from django.conf import settings
from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response
from api.base import handle,ApiError
from requests.archive_documents import family_identity
from authorization.policy import Denied
from identity.models import Patient
from records.models import RecordVersion
from retention.content_engine import _policy
from retention.content_family import family_permission,create_family_request,family_request_view
from retention.content_schemas import ContentFamilyOptions,ContentFamilyRequests
from retention.disposition import DispositionRejected
from retention.models import ContentDispositionPolicy,ContentDispositionFamilyRequest


def identity(request,patient_id):
    actor,epoch=family_identity(request)
    patient=Patient.objects.select_for_update().get(id=patient_id,status='active')
    family_permission(actor,epoch,patient)
    return actor,epoch,patient


def visible_request(request,row):
    actor,epoch,patient=identity(request,row.patient_id)
    if row.requested_by_id!=actor.id:
        raise ApiError('RESOURCE_NOT_FOUND',404)
    for target in row.targets:
        version=RecordVersion.all_objects.only('id','record_id','patient_id').get(id=target['record_version_id'],patient=patient)
        family_permission(actor,epoch,patient,version)
    return family_request_view(row)


@api_view(['GET'])
@handle
@transaction.atomic
def family_content_disposition_options(request,patient_id):
    actor,epoch,patient=identity(request,patient_id)
    policies=[]
    for candidate in ContentDispositionPolicy.objects.filter(environment=settings.SJIA_ENVIRONMENT).order_by('created_at','id'):
        try:
            policy,rules=_policy(candidate.id)
        except (DispositionRejected,Denied):
            continue
        if 'records.recordversion' not in rules.allowed_adapters:
            continue
        policies.append({'policy_id':str(policy.id),'version':policy.version,'mode':rules.mode,
            'execution_enabled':policy.enabled,'retention_seconds':rules.retention_seconds,'clock':rules.clock,
            'access_purpose':rules.access_purpose,'recheck_at':rules.recheck_at})
    versions=RecordVersion.objects.filter(patient=patient).only('id','record_id','patient_id','version','recorded_at').order_by('-recorded_at','id')
    if versions.count()>1000:
        raise ApiError('CONTENT_INVENTORY_LIMIT_EXCEEDED',409)
    targets=[]
    for version in versions:
        try:
            family_permission(actor,epoch,patient,version)
        except Denied:
            continue
        targets.append({'record_version_id':str(version.id),'record_id':str(version.record_id),
            'version':str(version.version),'recorded_at':version.recorded_at})
    return Response(ContentFamilyOptions.model_validate({'schema_version':'content-disposition-family-options-1',
        'patient_id':str(patient.id),'policies':policies,'targets':targets,'default_policy':None}).model_dump(mode='json'))


@api_view(['GET','POST'])
@handle
@transaction.atomic
def family_content_disposition_requests(request,patient_id):
    actor,epoch,patient=identity(request,patient_id)
    if request.method=='POST':
        row=create_family_request(actor=actor,auth_epoch=epoch,patient_id=patient.id,data=request.data)
        return Response(visible_request(request,row),status=201)
    rows=ContentDispositionFamilyRequest.objects.filter(patient=patient,requested_by=actor).order_by('-created_at','id')
    if rows.count()>500:
        raise ApiError('CONTENT_HISTORY_PAGINATION_REQUIRED',409)
    result=[]
    for row in rows:
        try:
            result.append(visible_request(request,row))
        except Denied:
            continue
    return Response(ContentFamilyRequests.model_validate({'schema_version':'content-disposition-family-requests-1',
        'patient_id':str(patient.id),'requests':result}).model_dump(mode='json'))


@api_view(['GET'])
@handle
@transaction.atomic
def family_content_disposition_read(request,request_id):
    actor,_=family_identity(request)
    row=ContentDispositionFamilyRequest.objects.get(id=request_id,requested_by=actor)
    return Response(visible_request(request,row))
