from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, handle
from audit.service import write_audit
from case_details.evaluation_runs import create_evaluation_run, lock_run_read, read_evaluation_run
from requests.evaluation_datasets import evaluation_identity
from requests.evaluation_runs import run_receipt, submitted_run_intent
from requests.service import commit_intent


@api_view(['POST'])
@handle
@transaction.atomic
def dataset_run_create(request, dataset_id):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        raise ApiError('DATASET_QUERY_INVALID', 422)
    intent = submitted_run_intent(request, actor, epoch)
    if intent.status != 'committed':
        run, task = create_evaluation_run(dataset_id=dataset_id, payload=request.data, actor=actor, auth_epoch=epoch)
        saved = commit_intent(intent=intent, action='dataset_run_create', resource_type='evaluation_run', resource_id=run.id, expected_version=1)
        task.request_row = saved
        task.save(update_fields=['request_row'])
        write_audit(actor=actor, action='dataset_run_create', object_type='evaluation_run', object_id=run.id,
            object_version='1', request_id=intent.request_id, outcome='committed',
            retention_policy=intent.retention_policy, occurred_at=saved.completed_at)
    return Response(run_receipt(intent), status=202)


@api_view(['GET'])
@handle
@transaction.atomic
def dataset_run_read(request, run_id):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        raise ApiError('DATASET_QUERY_INVALID', 422)
    return Response(read_evaluation_run(run_id=run_id, actor=actor, auth_epoch=epoch))


@api_view(['GET'])
@handle
@transaction.atomic
def dataset_run_denominators(request, run_id):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        raise ApiError('DATASET_QUERY_INVALID', 422)
    run, task, _, _ = lock_run_read(run_id=run_id, actor=actor, auth_epoch=epoch)
    return Response({'schema_version': 'evaluation-denominators-1', 'run_id': str(run.id),
        'denominators': run.denominators, 'limitations': run.limitations,
        'input_sha256': task.input_sha256})


@api_view(['GET'])
@handle
@transaction.atomic
def dataset_run_results(request, run_id):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        raise ApiError('DATASET_QUERY_INVALID', 422)
    run, task, _, _ = lock_run_read(run_id=run_id, actor=actor, auth_epoch=epoch)
    if run.status not in ('completed', 'insufficient') or task.status != 'completed' or run.finished_at is None:
        raise ApiError('DATASET_RUN_RESULT_UNAVAILABLE', 409)
    from tasks.models import TaskAttempt
    attempt = TaskAttempt.objects.get(task=task, status='completed', lease_generation=task.lease_generation)
    clinical = attempt.usage_evidence.get('clinical_adoption')
    return Response({'clinical_report': clinical['report'] if clinical else None, 'schema_version': 'evaluation-results-1', 'run_id': str(run.id),
        'status': run.status, 'results': run.result_refs, 'rules': run.rules_refs, 'limitations': run.limitations,
        'implementation_sha256': run.implementation_sha256, 'input_sha256': task.input_sha256,
        'finished_at': run.finished_at.isoformat()})
