"""Family applications contain exact record identities, never engine authority."""
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from authorization.policy import evaluate,Denied
from identity.models import Patient
from records.models import Record,RecordVersion
from retention.content_registry import digest
from retention.content_schemas import ContentFamilyRequestInput,ContentFamilyRequestView,ContentFamilyTarget
from retention.disposition import DispositionRejected
from retention.models import ContentDispositionFamilyRequest,ContentDispositionPlan


def family_permission(actor,epoch,patient,version=None):
    if version is not None:
        first=RecordVersion.all_objects.only('id','author_id').get(patient=patient,record_id=version.record_id,version=1)
        scope={'record_id':version.record_id,'author_id':first.author_id}
    else:
        # A person may have only an assigned-record grant; patient-level
        # admission must not require an unrelated whole-patient grant.
        from authorization.models import AccessGrant
        candidates=AccessGrant.objects.filter(principal=actor,patient=patient,action='request_disposition')
        for grant in candidates:
            try:
                decision=evaluate(principal=actor,patient=patient,action='request_disposition',purpose='family_observation',
                    environment=settings.SJIA_ENVIRONMENT,session_auth_epoch=epoch,require_relation=True,whole_resource=True,
                    record_id=grant.assigned_record_id,author_id=actor.id)
                if decision.grant.evidence_ref.strip():
                    return
            except Denied:
                continue
        raise Denied('CASE_ACTION_DENIED')
    def permitted(action):
        decision=evaluate(principal=actor,patient=patient,action=action,purpose='family_observation',
            environment=settings.SJIA_ENVIRONMENT,session_auth_epoch=epoch,require_relation=True,whole_resource=True,**scope)
        if not decision.grant.evidence_ref.strip():
            raise Denied('CASE_ACTION_DENIED')
    permitted('request_disposition')
    current=Record.objects.only('current_version').get(id=version.record_id,patient=patient)
    # The ordinary detail route authorizes the current snapshot with read_raw;
    # the explicit version route authorizes snapshots with read_history. An
    # application must follow those same readable boundaries, including when a
    # selected version becomes historical before staff processes the request.
    actions=('read_raw','read_history') if version.version==current.current_version else ('read_history',)
    for action in actions:
        try:
            permitted(action)
            return
        except Denied:
            continue
    raise Denied('CASE_ACTION_DENIED')


def request_targets(row,*,check_available):
    from retention.content_fences import assert_content_available
    # The saved epoch is application-time audit evidence, not a promise that
    # the original login session will remain alive. Re-evaluate the current
    # principal and grants; logout/relogin alone does not withdraw consent.
    from identity.models import Principal
    requester=Principal.objects.select_for_update().get(id=row.requested_by_id)
    targets=[]
    for value in row.targets:
        target=ContentFamilyTarget.model_validate(value)
        version=RecordVersion.all_objects.only('id','record_id','patient_id','version').get(
            id=target.record_version_id,patient_id=row.patient_id)
        family_permission(requester,requester.auth_epoch,row.patient,version)
        if str(version.version)!=target.version:
            raise DispositionRejected('CONTENT_FAMILY_REQUEST_CHANGED')
        if check_available:
            assert_content_available('records.recordversion',version.id)
        targets.append({'kind':'database','adapter':'records.recordversion','object_id':str(version.id),'version':str(version.version)})
    return targets


def request_digest(row):
    return digest({'patient_id':str(row.patient_id),'requested_by':str(row.requested_by_id),
        'requested_auth_epoch':row.requested_auth_epoch,'client_request_id':str(row.client_request_id),
        'policy_id':str(row.policy_id),'policy_version':row.policy_version,'policy_sha256':row.policy_sha256,
        'targets':row.targets})


@transaction.atomic
def create_family_request(*,actor,auth_epoch,patient_id,data):
    from retention.content_engine import write_barrier,_policy
    from retention.content_fences import assert_content_available
    body=ContentFamilyRequestInput.model_validate(data)
    write_barrier()
    patient=Patient.objects.select_for_update().get(id=patient_id,status='active')
    family_permission(actor,auth_epoch,patient)
    previous=ContentDispositionFamilyRequest.objects.select_for_update().filter(
        requested_by=actor,client_request_id=body.request_id).first()
    targets=[item.model_dump(mode='json') for item in body.targets]
    if previous:
        if previous.patient_id!=patient.id or previous.policy_id!=body.policy_id or previous.targets!=targets or request_digest(previous)!=previous.request_sha256:
            raise DispositionRejected('CONTENT_FAMILY_REQUEST_CHANGED')
        # Exact old application remains recoverable after content erasure; this
        # branch cannot create another body or implicitly renew consent.
        request_targets(previous,check_available=False)
        return previous
    policy,rules=_policy(body.policy_id)
    if 'records.recordversion' not in rules.allowed_adapters:
        raise DispositionRejected('CONTENT_FAMILY_POLICY_SCOPE_DENIED')
    for target in body.targets:
        version=RecordVersion.all_objects.only('id','record_id','patient_id','version').get(id=target.record_version_id,patient=patient)
        family_permission(actor,auth_epoch,patient,version)
        assert_content_available('records.recordversion',version.id)
        if str(version.version)!=target.version:
            raise DispositionRejected('CONTENT_VERSION_CONFLICT')
    row=ContentDispositionFamilyRequest(patient=patient,requested_by=actor,requested_auth_epoch=auth_epoch,
        client_request_id=body.request_id,policy=policy,policy_version=policy.version,policy_sha256=policy.checksum,
        targets=targets,created_at=timezone.now())
    row.request_sha256=request_digest(row);row.save(force_insert=True)
    return row


def bind_family_request(*,request_id,patient_id,policy,roots,check_available=True):
    row=ContentDispositionFamilyRequest.objects.select_for_update().get(id=request_id,patient_id=patient_id)
    if (row.policy_id!=policy.id or row.policy_version!=policy.version or row.policy_sha256!=policy.checksum
            or request_digest(row)!=row.request_sha256):
        raise DispositionRejected('CONTENT_FAMILY_REQUEST_CHANGED')
    requested=request_targets(row,check_available=check_available)
    actual=[item.model_dump(mode='json') if hasattr(item,'model_dump') else item for item in roots]
    if sorted(actual,key=lambda item:digest(item))!=sorted(requested,key=lambda item:digest(item)):
        raise DispositionRejected('CONTENT_FAMILY_REQUEST_SCOPE_CHANGED')
    return row


def family_request_view(row):
    plan=ContentDispositionPlan.objects.filter(family_request_id=row.id).order_by('-created_at','id').first()
    return ContentFamilyRequestView.model_validate({'schema_version':'content-disposition-family-view-1',
        'request_id':str(row.id),'client_request_id':str(row.client_request_id),'patient_id':str(row.patient_id),'policy_id':str(row.policy_id),
        'created_at':row.created_at,'targets':row.targets,'status':plan.status if plan else 'requested',
        'linked_plan_id':str(plan.id) if plan else None,
        'logical_payload_erased':bool(plan and plan.status=='logical_erasure_complete'),
        'all_copies_erased':False}).model_dump(mode='json')
