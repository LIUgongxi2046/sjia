from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, current_principal, handle
from api.views_purpose import prepare_purpose_intent, retry_purpose_intent, read_purpose_request
from requests.archive_documents import prepare_document_intent, read_document_request, retry_document_intent
from requests.archive_facts import OPERATIONS as FACT_OPERATIONS
from requests.archive_facts import prepare_fact_intent, read_fact_request, retry_fact_intent
from requests.archive_batches import OPERATIONS as BATCH_OPERATIONS
from requests.archive_batches import prepare_batch_intent, read_batch_request, retry_batch_intent
from requests.material_reminders import OPERATIONS as REMINDER_OPERATIONS
from requests.material_reminders import prepare_reminder_intent, read_reminder_request, retry_reminder_intent
from requests.personal_record_tasks import OPERATIONS as PERSONAL_TASK_OPERATIONS
from requests.personal_record_tasks import prepare_task_intent, read_task_request, retry_task_intent
from requests.evaluation_datasets import OPERATIONS as EVALUATION_OPERATIONS
from requests.evaluation_datasets import prepare_evaluation_intent, read_evaluation_request, retry_evaluation_intent
from requests.evaluation_runs import OPERATIONS as RUN_OPERATIONS
from requests.evaluation_runs import prepare_run_intent, read_run_request, retry_run_intent
from requests.models import WriteIntent
from requests.dispositions import OPERATIONS as DISPOSITION_OPERATIONS
from requests.dispositions import prepare_disposition_intent, read_disposition_request, retry_disposition_intent
from requests.archive_delta import OPERATIONS as DELTA_OPERATIONS
from requests.archive_delta import prepare_delta_intent, read_delta_request, retry_delta_intent
from requests.observation_periods import OPERATIONS as PERIOD_OPERATIONS
from requests.observation_periods import prepare_period_intent, read_period_request, retry_period_intent
from requests.archive_relations import OPERATIONS as RELATION_OPERATIONS
from requests.archive_relations import prepare_relation_intent, read_relation_request, retry_relation_intent
from requests.dynamic import OPERATIONS as DYNAMIC_OPERATIONS
from requests.dynamic import prepare_dynamic_intent, read_dynamic_request, retry_dynamic_intent
from requests.assessments import OPERATIONS as ASSESSMENT_OPERATIONS
from requests.assessments import prepare_assessment_intent, read_assessment_request, retry_assessment_intent
from requests.verifications import OPERATIONS as VERIFICATION_OPERATIONS
from requests.verifications import prepare_verification_intent, read_verification_request, retry_verification_intent
from requests.notifications import OPERATIONS as NOTIFICATION_OPERATIONS
from requests.notifications import prepare_notification_intent, read_notification_request, retry_notification_intent, cancel_notification_intent


def request_audience(request):
    actor, _, audience = current_principal(request)
    if actor is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience not in ('family', 'support'):
        raise ApiError('AUDIENCE_INVALID', 403)
    return audience


def operation_request(request, intent_id, operations, *, lookup=False):
    if lookup and request.query_params.get('lookup') == 'request_id':
        return request.query_params.get('operation_id') in operations
    actor, _, _ = current_principal(request)
    return WriteIntent.objects.filter(id=intent_id, actor=actor, operation_id__in=operations).exists()


@api_view(['POST'])
@handle
@transaction.atomic
def v2_intent_prepare(request):
    request_audience(request)
    if isinstance(request.data, dict) and request.data.get('operation_id') in RELATION_OPERATIONS:
        return Response(prepare_relation_intent(request), status=201)
    if request_audience(request) == 'family':
        if isinstance(request.data, dict) and request.data.get('operation_id') in DISPOSITION_OPERATIONS:
            return Response(prepare_disposition_intent(request), status=201)
        if isinstance(request.data, dict) and request.data.get('operation_id') in FACT_OPERATIONS:
            return Response(prepare_fact_intent(request), status=201)
        if isinstance(request.data, dict) and request.data.get('operation_id') in PERIOD_OPERATIONS:
            return Response(prepare_period_intent(request), status=201)
        if isinstance(request.data, dict) and request.data.get('operation_id') in DELTA_OPERATIONS:
            return Response(prepare_delta_intent(request), status=201)
        if isinstance(request.data, dict) and request.data.get('operation_id') in PERSONAL_TASK_OPERATIONS:
            return Response(prepare_task_intent(request), status=201)
        if isinstance(request.data, dict) and request.data.get('operation_id') in BATCH_OPERATIONS:
            return Response(prepare_batch_intent(request), status=201)
        if isinstance(request.data, dict) and request.data.get('operation_id') in REMINDER_OPERATIONS:
            return Response(prepare_reminder_intent(request), status=201)
        return Response(prepare_document_intent(request), status=201)
    if isinstance(request.data, dict) and request.data.get('operation_id') in VERIFICATION_OPERATIONS:
        return Response(prepare_verification_intent(request), status=201)
    if isinstance(request.data, dict) and request.data.get('operation_id') in NOTIFICATION_OPERATIONS:
        return Response(prepare_notification_intent(request), status=201)
    if isinstance(request.data, dict) and request.data.get('operation_id') in ASSESSMENT_OPERATIONS:
        return Response(prepare_assessment_intent(request), status=201)
    if isinstance(request.data, dict) and request.data.get('operation_id') in DYNAMIC_OPERATIONS:
        return Response(prepare_dynamic_intent(request), status=201)
    if isinstance(request.data, dict) and request.data.get('operation_id') in RUN_OPERATIONS:
        return Response(prepare_run_intent(request), status=201)
    if isinstance(request.data, dict) and request.data.get('operation_id') in EVALUATION_OPERATIONS:
        return Response(prepare_evaluation_intent(request), status=201)
    return prepare_purpose_intent(request)


@api_view(['GET'])
@handle
@transaction.atomic
def v2_request_read(request, intent_id):
    request_audience(request)
    if operation_request(request, intent_id, RELATION_OPERATIONS, lookup=True):
        return Response(read_relation_request(request, intent_id))
    if request_audience(request) == 'family':
        if operation_request(request, intent_id, DISPOSITION_OPERATIONS):
            return Response(read_disposition_request(request, intent_id))
        if operation_request(request, intent_id, FACT_OPERATIONS, lookup=True):
            return Response(read_fact_request(request, intent_id))
        if operation_request(request, intent_id, PERIOD_OPERATIONS, lookup=True):
            return Response(read_period_request(request, intent_id))
        if operation_request(request, intent_id, DELTA_OPERATIONS, lookup=True):
            return Response(read_delta_request(request, intent_id))
        if operation_request(request, intent_id, PERSONAL_TASK_OPERATIONS, lookup=True):
            return Response(read_task_request(request, intent_id))
        if operation_request(request, intent_id, BATCH_OPERATIONS, lookup=True):
            return Response(read_batch_request(request, intent_id))
        if operation_request(request, intent_id, REMINDER_OPERATIONS, lookup=True):
            return Response(read_reminder_request(request, intent_id))
        return Response(read_document_request(request, intent_id))
    if operation_request(request, intent_id, VERIFICATION_OPERATIONS, lookup=True):
        return Response(read_verification_request(request, intent_id))
    if operation_request(request, intent_id, NOTIFICATION_OPERATIONS, lookup=True):
        return Response(read_notification_request(request, intent_id))
    if operation_request(request, intent_id, ASSESSMENT_OPERATIONS, lookup=True):
        return Response(read_assessment_request(request, intent_id))
    if operation_request(request, intent_id, DYNAMIC_OPERATIONS, lookup=True):
        return Response(read_dynamic_request(request, intent_id))
    if operation_request(request, intent_id, RUN_OPERATIONS, lookup=True):
        return Response(read_run_request(request, intent_id))
    if operation_request(request, intent_id, EVALUATION_OPERATIONS, lookup=True):
        return Response(read_evaluation_request(request, intent_id))
    return read_purpose_request(request, intent_id)


@api_view(['POST'])
@handle
@transaction.atomic
def v2_intent_retry(request, intent_id):
    request_audience(request)
    if operation_request(request, intent_id, RELATION_OPERATIONS):
        return Response(retry_relation_intent(request, intent_id))
    if request_audience(request) == 'family':
        if operation_request(request, intent_id, DISPOSITION_OPERATIONS):
            return Response(retry_disposition_intent(request, intent_id))
        if operation_request(request, intent_id, FACT_OPERATIONS):
            return Response(retry_fact_intent(request, intent_id))
        if operation_request(request, intent_id, PERIOD_OPERATIONS):
            return Response(retry_period_intent(request, intent_id))
        if operation_request(request, intent_id, DELTA_OPERATIONS):
            return Response(retry_delta_intent(request, intent_id))
        if operation_request(request, intent_id, PERSONAL_TASK_OPERATIONS):
            return Response(retry_task_intent(request, intent_id))
        if operation_request(request, intent_id, BATCH_OPERATIONS):
            return Response(retry_batch_intent(request, intent_id))
        if operation_request(request, intent_id, REMINDER_OPERATIONS):
            return Response(retry_reminder_intent(request, intent_id))
        return Response(retry_document_intent(request, intent_id))
    if operation_request(request, intent_id, VERIFICATION_OPERATIONS):
        return Response(retry_verification_intent(request, intent_id))
    if operation_request(request, intent_id, NOTIFICATION_OPERATIONS):
        return Response(retry_notification_intent(request, intent_id))
    if operation_request(request, intent_id, ASSESSMENT_OPERATIONS):
        return Response(retry_assessment_intent(request, intent_id))
    if operation_request(request, intent_id, DYNAMIC_OPERATIONS):
        return Response(retry_dynamic_intent(request, intent_id))
    if operation_request(request, intent_id, RUN_OPERATIONS):
        return Response(retry_run_intent(request, intent_id))
    if operation_request(request, intent_id, EVALUATION_OPERATIONS):
        return Response(retry_evaluation_intent(request, intent_id))
    return retry_purpose_intent(request, intent_id)


@api_view(['POST'])
@handle
@transaction.atomic
def v2_notification_intent_cancel(request, intent_id):
    request_audience(request)
    return Response(cancel_notification_intent(request, intent_id))
