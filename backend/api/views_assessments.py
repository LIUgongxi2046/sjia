from django.conf import settings
from django.db import transaction
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, handle
from assessment.service import assessment_task_view, create_assessment_task, lock_actor, read_assessment, read_assessment_task
from assessment.options import assessment_creation_options, clinical_directory
from audit.service import write_audit
from requests.assessments import committed_task, submitted_intent
from requests.evaluation_datasets import evaluation_identity
from requests.service import commit_intent


class AssessmentCreationQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    mode: Literal['current', 'replay']
    known_at: str = Field(min_length=1, max_length=128)


class AssessmentDirectoryQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['verify_field', 'execute_assessment']


def query_payload(request, model):
    if any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise ApiError('ASSESSMENT_QUERY_INVALID', 422)
    return model.model_validate(dict(request.query_params.items())).model_dump()


@api_view(['GET'])
@handle
@transaction.atomic
def clinical_directory_read(request):
    actor, epoch = evaluation_identity(request)
    query = query_payload(request, AssessmentDirectoryQuery)
    return Response(clinical_directory(actor, epoch, settings.SJIA_ENVIRONMENT, query['action']))


@api_view(['GET'])
@handle
@transaction.atomic
def assessment_creation_options_read(request, patient_id):
    actor, epoch = evaluation_identity(request)
    query = query_payload(request, AssessmentCreationQuery)
    return Response(assessment_creation_options(patient_id, actor, epoch, settings.SJIA_ENVIRONMENT, query))


@api_view(['POST'])
@handle
@transaction.atomic
def assessment_create(request, patient_id):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        raise ApiError('ASSESSMENT_QUERY_INVALID', 422)
    intent = submitted_intent(request, actor, epoch)
    if intent.status != 'committed':
        if intent.patient_id != patient_id:
            raise ApiError('ASSESSMENT_PATIENT_MISMATCH', 409)
        task, runtime = create_assessment_task(patient_id=patient_id, actor=actor, auth_epoch=epoch,
            environment=settings.SJIA_ENVIRONMENT, payload=request.data)
        saved = commit_intent(intent=intent, action='assessment_v2_create', resource_type='task', resource_id=task.id, expected_version=1)
        task.request_row = saved
        task.save(update_fields=['request_row'])
        write_audit(actor=actor, patient=task.patient, action='assessment_v2_create', object_type='task', object_id=task.id,
            object_version='1', request_id=intent.request_id, outcome='committed', retention_policy=intent.retention_policy,
            occurred_at=saved.completed_at)
    task = committed_task(intent)
    _, _, _, _, runtime = lock_actor(task.patient_id, actor, epoch, settings.SJIA_ENVIRONMENT, 'execute_assessment')
    return Response(assessment_task_view(task, runtime), status=202)


@api_view(['GET'])
@handle
@transaction.atomic
def assessment_read(request, assessment_id):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        raise ApiError('ASSESSMENT_QUERY_INVALID', 422)
    return Response(read_assessment(assessment_id=assessment_id, actor=actor, auth_epoch=epoch, environment=settings.SJIA_ENVIRONMENT))


@api_view(['GET'])
@handle
@transaction.atomic
def assessment_task_read(request, task_id):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        raise ApiError('ASSESSMENT_QUERY_INVALID', 422)
    return Response(read_assessment_task(task_id=task_id, actor=actor, auth_epoch=epoch, environment=settings.SJIA_ENVIRONMENT))
