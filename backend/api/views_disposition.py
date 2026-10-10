from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.views_purpose import locked_intent, support_principal, write_receipt
from audit.service import write_audit
from files.purpose import runtime_environment
from files.purpose_schemas import PurposeDispositionApprovalInput, PurposeDispositionExecuteInput, PurposeDispositionRetainInput, PurposeDispositionRetryInput, PurposeDispositionScope
from requests.service import commit_intent
from retention.disposition import DispositionRejected, approve_disposition_request, lock_environment, lock_request_access, locked_item, mark_item_retained, scope_sha256
from retention.jobs import retry_local_disposition, submit_local_disposition, submit_simulated_provider_disposition, validate_refs
from retention.models import DispositionItem, DispositionRequest
from tasks.models import Task
from api.base import handle


def authorize_disposition_target(principal, epoch, operation_id, route):
    lock_environment()
    if operation_id == 'disposition_retry':
        task = Task.objects.get(id=route.kwargs['task_id'], actor=principal, task_type='disposition')
        refs, item, request, policy, receipt_policy = validate_refs(task, principal, epoch, require_actor_epoch=False)
    else:
        request = DispositionRequest.objects.get(id=route.kwargs['disposition_id'])
        if 'item_id' in route.kwargs:
            item, request = locked_item(DispositionItem.objects.get(id=route.kwargs['item_id'], request=request))
        request, policy, receipt_policy, rule, decision, file_grants, file_versions = lock_request_access(
            request, principal, epoch, require_approval=operation_id != 'disposition_approve')
    context = {'patient_id': str(request.patient_id), 'disposition_id': str(request.id),
        'scope_sha256': scope_sha256(request.scope), 'environment': runtime_environment()}
    return request, policy, receipt_policy, context


@api_view(['GET'])
@handle
@transaction.atomic
def support_disposition_read(request, disposition_id):
    principal, epoch = support_principal(request)
    disposition, policy, receipt_policy, rule, decision, file_grants, file_versions = lock_request_access(
        DispositionRequest.objects.get(id=disposition_id), principal, epoch, require_approval=False)
    parsed = PurposeDispositionScope.model_validate(disposition.scope)
    tasks = Task.objects.filter(task_type='disposition', patient=disposition.patient, actor=principal,
        input_refs__disposition_id=str(disposition.id)).order_by('created_at', 'id')
    items = DispositionItem.objects.filter(request=disposition).order_by('target_type', 'target_id', 'target_version')
    write_audit(actor=principal, patient=disposition.patient, action='disposition_read', object_type='disposition_request',
        object_id=disposition.id, object_version=scope_sha256(disposition.scope), outcome='allowed', retention_policy=receipt_policy)
    return Response({'disposition_id': str(disposition.id), 'patient_id': str(disposition.patient_id),
        'status': disposition.status, 'policy_id': str(policy.id), 'policy_version': str(policy.version),
        'scope_sha256': scope_sha256(disposition.scope), 'targets': [target.model_dump(mode='json') for target in parsed.targets],
        'approved_by': str(disposition.approved_by_id) if disposition.approved_by_id is not None else None,
        'approved_at': disposition.approved_at.isoformat() if disposition.approved_at is not None else None,
        'completed_at': disposition.completed_at.isoformat() if disposition.completed_at is not None else None,
        'task_ids': [str(task.id) for task in tasks],
        'items': [{'item_id': str(item.id), 'target_type': item.target_type, 'target_id': item.target_id,
            'target_version': item.target_version, 'status': item.status, 'reason': item.reason,
            'receipt_sha256': item.receipt_sha256,
            'executed_at': item.executed_at.isoformat() if item.executed_at is not None else None,
            'recheck_at': item.recheck_at.isoformat() if item.recheck_at is not None else None} for item in items]})


@api_view(['POST'])
@handle
@transaction.atomic
def disposition_approve(request, disposition_id):
    principal, epoch = support_principal(request)
    payload = PurposeDispositionApprovalInput.model_validate(request.data)
    disposition = DispositionRequest.objects.get(id=disposition_id)
    disposition, policy, receipt_policy, rule, decision, file_grants, file_versions = lock_request_access(
        disposition, principal, epoch, require_approval=False)
    intent = locked_intent(request, principal, 'disposition_approve', disposition.patient)
    if intent.status != 'committed':
        approve_disposition_request(request=disposition, approved_by=principal, auth_epoch=epoch, payload=payload)
        commit_intent(intent=intent, action='disposition_approve', resource_type='disposition', resource_id=disposition.id,
            expected_version=str(policy.version))
    return Response(write_receipt(intent, version=intent.write_request.expected_version))


@api_view(['POST'])
@handle
@transaction.atomic
def disposition_execute(request, disposition_id, item_id):
    principal, epoch = support_principal(request)
    payload = PurposeDispositionExecuteInput.model_validate(request.data)
    item, disposition = locked_item(DispositionItem.objects.get(id=item_id, request_id=disposition_id))
    lock_request_access(disposition, principal, epoch, require_approval=True)
    intent = locked_intent(request, principal, 'disposition_execute', disposition.patient)
    if intent.status == 'committed':
        task = Task.objects.get(id=intent.write_request.resource_id, actor=principal, task_type='disposition', patient=disposition.patient)
    elif item.target_type == 'provider_copy':
        task = submit_simulated_provider_disposition(item=item, actor=principal, auth_epoch=epoch, payload=payload)
        saved = commit_intent(intent=intent, action='disposition_execute', resource_type='task', resource_id=task.id, expected_version='1')
        task.request_row = saved
        task.save(update_fields=['request_row'])
    else:
        task = submit_local_disposition(item=item, actor=principal, auth_epoch=epoch, payload=payload)
        saved = commit_intent(intent=intent, action='disposition_execute', resource_type='task', resource_id=task.id, expected_version='1')
        task.request_row = saved
        task.save(update_fields=['request_row'])
    return Response(write_receipt(intent, version='1', task_id=task.id), status=202)


@api_view(['POST'])
@handle
@transaction.atomic
def disposition_retain(request, disposition_id, item_id):
    principal, epoch = support_principal(request)
    payload = PurposeDispositionRetainInput.model_validate(request.data)
    item, disposition = locked_item(DispositionItem.objects.get(id=item_id, request_id=disposition_id))
    disposition, policy, receipt_policy, rule, decision, file_grants, file_versions = lock_request_access(
        disposition, principal, epoch, require_approval=True)
    if (payload.policy_version != str(policy.version) or payload.expected_scope_sha256 != scope_sha256(disposition.scope)
            or payload.expected_target_version != item.target_version):
        raise DispositionRejected('VERSION_CONFLICT')
    intent = locked_intent(request, principal, 'disposition_retain', disposition.patient)
    if intent.status != 'committed':
        mark_item_retained(item=item, retention_evidence_ref=payload.retention_evidence_ref, recheck_at=payload.recheck_at,
            executed_by=principal, auth_epoch=epoch)
        commit_intent(intent=intent, action='disposition_retain', resource_type='disposition', resource_id=disposition.id,
            expected_version=str(policy.version))
    return Response(write_receipt(intent, version=intent.write_request.expected_version))


@api_view(['POST'])
@handle
@transaction.atomic
def disposition_retry(request, task_id):
    principal, epoch = support_principal(request)
    payload = PurposeDispositionRetryInput.model_validate(request.data)
    task = Task.objects.get(id=task_id, actor=principal, task_type='disposition')
    refs, item, disposition, policy, receipt_policy = validate_refs(task, principal, epoch, require_actor_epoch=False)
    intent = locked_intent(request, principal, 'disposition_retry', disposition.patient)
    if intent.status == 'committed':
        new_task = Task.objects.get(id=intent.write_request.resource_id, actor=principal, task_type='disposition', patient=disposition.patient)
    else:
        new_task = retry_local_disposition(task=task, actor=principal, auth_epoch=epoch, expected_status_version=payload.expected_status_version)
        saved = commit_intent(intent=intent, action='disposition_retry', resource_type='task', resource_id=new_task.id, expected_version='1')
        new_task.request_row = saved
        new_task.save(update_fields=['request_row'])
    return Response(write_receipt(intent, version='1', task_id=new_task.id), status=202)
