from django.conf import settings
from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, handle
from alerting.service import create_dynamic_task, dynamic_task_view, read_dynamic_evaluation, read_dynamic_task
from alerting.options import dynamic_creation_options
from alerting.triggers import DynamicTriggerQuery, read_dynamic_triggers
from api.views_assessments import AssessmentCreationQuery, query_payload
from assessment.service import lock_actor
from audit.service import write_audit
from requests.dynamic import committed_task, submitted_intent, OPERATIONS
from requests.evaluation_datasets import evaluation_identity
from requests.service import commit_intent


@api_view(['GET'])
@handle
@transaction.atomic
def dynamic_options_read(request, patient_id):
    actor, epoch = evaluation_identity(request)
    query = query_payload(request, AssessmentCreationQuery)
    return Response(dynamic_creation_options(patient_id, actor, epoch, settings.SJIA_ENVIRONMENT, query))


@api_view(['POST'])
@handle
@transaction.atomic
def dynamic_create(request, patient_id):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        raise ApiError('DYNAMIC_QUERY_INVALID', 422)
    intent = submitted_intent(request, actor, epoch)
    if intent.status != 'committed':
        if intent.patient_id != patient_id:
            raise ApiError('DYNAMIC_PATIENT_MISMATCH', 409)
        task, _ = create_dynamic_task(patient_id=patient_id, actor=actor, auth_epoch=epoch,
            environment=settings.SJIA_ENVIRONMENT, payload=request.data)
        saved = commit_intent(intent=intent, action=OPERATIONS[0], resource_type='task', resource_id=task.id, expected_version=1)
        task.request_row = saved
        task.save(update_fields=['request_row'])
        write_audit(actor=actor, patient=task.patient, action=OPERATIONS[0], object_type='task', object_id=task.id,
            object_version='1', request_id=intent.request_id, outcome='committed', retention_policy=intent.retention_policy,
            occurred_at=saved.completed_at)
    task = committed_task(intent)
    _, _, _, _, runtime = lock_actor(task.patient_id, actor, epoch, settings.SJIA_ENVIRONMENT, 'execute_assessment')
    return Response(dynamic_task_view(task, runtime), status=202)


@api_view(['GET'])
@handle
@transaction.atomic
def dynamic_read(request, evaluation_id):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        raise ApiError('DYNAMIC_QUERY_INVALID', 422)
    return Response(read_dynamic_evaluation(evaluation_id=evaluation_id, actor=actor, auth_epoch=epoch, environment=settings.SJIA_ENVIRONMENT))


@api_view(['GET'])
@handle
@transaction.atomic
def dynamic_task_read(request, task_id):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        raise ApiError('DYNAMIC_QUERY_INVALID', 422)
    return Response(read_dynamic_task(task_id=task_id, actor=actor, auth_epoch=epoch, environment=settings.SJIA_ENVIRONMENT))


@api_view(['GET'])
@handle
@transaction.atomic
def dynamic_triggers_read(request, patient_id):
    actor, epoch = evaluation_identity(request)
    query = query_payload(request, DynamicTriggerQuery)
    return Response(read_dynamic_triggers(patient_id, actor, epoch, settings.SJIA_ENVIRONMENT,
        query, request.session.get_expiry_date().timestamp()))
