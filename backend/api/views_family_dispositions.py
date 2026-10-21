from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, handle
from archives.models import PurposeCopy
from authorization.policy import evaluate
from files.models import FileObject
from files.purpose import current_file_grants, runtime_environment
from files.purpose_schemas import PurposeDispositionTarget
from identity.models import Patient
from requests.archive_documents import family_identity
from requests.dispositions import disposition_receipt, submitted_disposition_intent
from requests.service import commit_intent
from retention.disposition import approved_policy, create_disposition_request, local_rule, scope_sha256
from retention.jobs import disposition_tasks
from retention.models import DispositionItem, DispositionRequest, RetentionPolicy, SimulatedProviderCopy


def read_identity(request, patient_id):
    actor, epoch = family_identity(request)
    patient = Patient.objects.select_for_update().get(id=patient_id, status='active')
    decision = evaluate(principal=actor, patient=patient, action='request_disposition', purpose='family_observation',
        environment=runtime_environment(), session_auth_epoch=epoch, author_id=actor.id, require_relation=True, whole_resource=True)
    if decision.grant.purpose != 'family_observation' or not decision.grant.evidence_ref.strip():
        raise ApiError('DISPOSITION_SCOPE_DENIED', 403)
    return actor, patient


@api_view(['GET'])
@handle
@transaction.atomic
def family_disposition_options(request, patient_id):
    actor, patient = read_identity(request, patient_id)
    policies = list(RetentionPolicy.objects.filter(status='active', approved_at__lte=timezone.now(), approved_by__isnull=False,
        rules__has_key='local_delete').order_by('policy_key', 'version'))
    rules = {}
    for policy in policies:
        approved_policy(policy)
        rules[policy.id] = local_rule(policy)
    originals = list(FileObject.objects.filter(patient=patient, purpose='original', status='available', retention_policy_id__in=rules)
        .order_by('created_at', 'id'))
    accessible = {file.id: file for file in originals if any(current_file_grants(file, actor, 'family_observation', timezone.now()))}
    choices = []
    for file in accessible.values():
        if 'file_object' in rules[file.retention_policy_id].target_types:
            choices.append({'target_type': 'file_object', 'target_id': str(file.id), 'target_version': file.object_version,
                'policy_id': str(file.retention_policy_id), 'label': f'原件 · {file.media_type} · {file.id}'})
    for copy in PurposeCopy.objects.filter(patient_id=patient.id, original_file_id__in=accessible).exclude(status='disposed').order_by('created_at', 'id'):
        original = accessible[copy.original_file_id]
        if 'purpose_copy' in rules[original.retention_policy_id].target_types:
            choices.append({'target_type': 'purpose_copy', 'target_id': str(copy.id), 'target_version': str(copy.version),
                'policy_id': str(original.retention_policy_id), 'label': f'用途副本 · {copy.purpose} · {copy.id}'})
    if runtime_environment() == 'simulation':
        for provider_copy in SimulatedProviderCopy.objects.filter(patient_id=patient.id, environment='simulation',
                status='available', purpose_copy__status='approved', purpose_copy__original_file_id__in=accessible).order_by('created_at', 'id'):
            original = accessible[provider_copy.purpose_copy.original_file_id]
            if 'provider_copy' in rules[original.retention_policy_id].target_types:
                choices.append({'target_type': 'provider_copy', 'target_id': str(provider_copy.object_id),
                    'target_version': provider_copy.object_version, 'policy_id': str(original.retention_policy_id),
                    'label': f'模拟提供方副本 · {provider_copy.provider_id} · {provider_copy.object_id}'})
    return Response({'patient_id': str(patient.id), 'policies': [{'policy_id': str(policy.id), 'policy_key': policy.policy_key,
        'version': str(policy.version), 'evidence_ref': policy.evidence_ref} for policy in policies], 'targets': choices})


@api_view(['POST'])
@handle
@transaction.atomic
def family_disposition_create(request, patient_id):
    actor, patient, policy, intent, payload = submitted_disposition_intent(request)
    if intent.status != 'committed':
        row = create_disposition_request(patient=patient, requested_by=actor, policy=policy, evidence_ref=payload.evidence_ref,
            scope={'targets': [target.model_dump(mode='json') for target in payload.targets]})
        commit_intent(intent=intent, action='disposition_create', resource_type='disposition', resource_id=row.id, expected_version=policy.version)
    return Response(disposition_receipt(intent), status=201)


@api_view(['GET'])
@handle
@transaction.atomic
def family_disposition_read(request, disposition_id):
    actor, epoch = family_identity(request)
    row = DispositionRequest.objects.select_for_update().get(id=disposition_id, requested_by=actor)
    read_identity(request, row.patient_id)
    items = DispositionItem.objects.filter(request=row).order_by('target_type', 'target_id', 'target_version')
    task_ids = disposition_tasks(row).values_list('id', flat=True)
    return Response({'disposition_id': str(row.id), 'patient_id': str(row.patient_id), 'status': row.status,
        'policy_id': str(row.policy_id), 'policy_version': str(row.policy.version), 'scope_sha256': scope_sha256(row.scope),
        'targets': [PurposeDispositionTarget.model_validate(target).model_dump(mode='json') for target in row.scope['targets']],
        'approved_by': str(row.approved_by_id) if row.approved_by_id else None,
        'approved_at': row.approved_at.isoformat() if row.approved_at else None,
        'completed_at': row.completed_at.isoformat() if row.completed_at else None, 'task_ids': [str(task_id) for task_id in task_ids],
        'items': [{'item_id': str(item.id), 'target_type': item.target_type, 'target_id': item.target_id, 'target_version': item.target_version,
            'status': item.status, 'reason': item.reason, 'receipt_sha256': item.receipt_sha256,
            'executed_at': item.executed_at.isoformat() if item.executed_at else None, 'recheck_at': item.recheck_at.isoformat() if item.recheck_at else None} for item in items]})
