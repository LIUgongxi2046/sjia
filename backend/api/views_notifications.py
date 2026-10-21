import json

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from alerting.models import AlertRecipient
from api.base import ApiError, authorize_case, authorize_service, current_principal, handle
from api.views_assessments import query_payload
from assessment.provenance import entity_reference
from authorization.models import AccessGrant
from authorization.policy import matching_case_grants
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from notifications.reading import DeliveryCursor, DeliveryQuery, notification_history, notification_record
from notifications.native import acknowledge_delivery, delivery_message
from pydantic import BaseModel, ConfigDict, Field
from relations.models import Relation


def notification_identity(request, support):
    actor, _, audience = current_principal(request)
    if actor is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience != ('support' if support else 'family'):
        raise ApiError('NOTIFICATION_AUDIENCE_REQUIRED', 403)
    gate = EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
    actor = Principal.objects.select_for_update(no_key=True).get(id=actor.id)
    if not gate.enabled:
        raise ApiError('ENVIRONMENT_DISABLED', 403)
    service = authorize_service(request, actor, 'manage_notifications').service_grant if support else None
    return actor, gate, service


def notification_scope(request, patient_id, support, actor, service):
    patient = Patient.objects.select_for_update(no_key=True).get(id=patient_id)
    grant = authorize_case(request, actor, patient, 'read_case' if support else 'receive_alert',
        whole_resource=support, require_relation=not support).grant
    if not grant.evidence_ref.strip():
        raise ApiError('NOTIFICATION_GRANT_EVIDENCE_REQUIRED', 403)
    rows = AlertRecipient.objects.filter(patient_id=patient.id)
    grants = [grant]
    if support:
        grants.append(service)
    else:
        relation = Relation.objects.select_for_update().get(id=grant.relation_id, patient=patient, guardian=actor)
        rows = rows.filter(relation_id=relation.id)
        grants.append(relation)
    return patient, grants, rows


def history_response(request, patient_id, support):
    actor, gate, service = notification_identity(request, support)
    query = query_payload(request, DeliveryQuery)
    patient, grants, rows = notification_scope(request, patient_id, support, actor, service)
    return Response(notification_history(request, actor, patient, gate, grants, rows, query['cursor']))


def record_response(request, recipient_id, support):
    actor, _, service = notification_identity(request, support)
    if request.query_params:
        raise ApiError('NOTIFICATION_QUERY_INVALID', 422)
    recipient = AlertRecipient.objects.get(id=recipient_id)
    _, _, rows = notification_scope(request, recipient.patient_id, support, actor, service)
    if not rows.filter(id=recipient.id).exists():
        raise ApiError('NOTIFICATION_RECIPIENT_SCOPE_DENIED', 403)
    return Response(notification_record(recipient))


@api_view(['GET'])
@handle
@transaction.atomic
def family_notification_history(request, patient_id):
    return history_response(request, patient_id, False)


@api_view(['GET'])
@handle
@transaction.atomic
def support_notification_history(request, patient_id):
    return history_response(request, patient_id, True)


@api_view(['GET'])
@handle
@transaction.atomic
def family_notification_record(request, recipient_id):
    return record_response(request, recipient_id, False)


@api_view(['GET'])
@handle
@transaction.atomic
def support_notification_record(request, recipient_id):
    return record_response(request, recipient_id, True)


@api_view(['GET'])
@handle
@transaction.atomic
def notification_case_directory(request):
    actor, gate, role = notification_identity(request, True)
    query = query_payload(request, DeliveryQuery)
    runtime = ConfigVersion.objects.filter(id=gate.runtime_config_id, kind='provider', schema_version='backend-runtime-1').first()
    value = {'items': [], 'next_cursor': None, 'snapshot_version': compute_checksum(entity_reference(gate)),
        'capability_state': 'unavailable', 'capability_reason': 'NOTIFICATION_RUNTIME_UNAVAILABLE'}
    if runtime is None or configuration_qualification_reason(runtime, gate.environment, audience='support') is not None:
        if query['cursor'] is not None:
            raise ApiError('NOTIFICATION_DIRECTORY_CHANGED', 409)
        return Response(value)
    now = timezone.now()
    grants = list(AccessGrant.objects.select_for_update().filter(principal=actor, action='read_case').order_by('id'))
    relations = list(Relation.objects.select_for_update().filter(id__in=[row.relation_id for row in grants if row.relation_id is not None]).order_by('id'))
    rows = [patient for patient in Patient.objects.select_for_update().filter(status='active',
        id__in=[row.patient_id for row in grants]).order_by('id')
        if any(grant.evidence_ref.strip() for grant in matching_case_grants(actor, patient, 'read_case', now, whole_resource=True))]
    snapshot = compute_checksum({'actor': entity_reference(actor), 'role': entity_reference(role), 'gate': entity_reference(gate),
        'runtime': entity_reference(runtime), 'grants': [entity_reference(row) for row in grants],
        'relations': [entity_reference(row) for row in relations], 'patients': [entity_reference(row) for row in rows]})
    offset, expiry = 0, request.session.get_expiry_date().timestamp()
    if expiry <= now.timestamp():
        raise ApiError('AUTH_REQUIRED', 401)
    if query['cursor'] is not None:
        cursor = DeliveryCursor.model_validate_json(json.dumps(signing.loads(query['cursor'], salt='notification-cases')))
        if cursor.snapshot_version != snapshot or cursor.offset >= len(rows) or not now.timestamp() < cursor.expires_at <= expiry:
            raise ApiError('NOTIFICATION_DIRECTORY_CHANGED', 409)
        offset = cursor.offset
    selected = rows[offset:offset + runtime.payload['api']['max_page_size']]
    following = offset + len(selected)
    value.update({'items': [{'patient_id': str(row.id), 'display_id': row.display_id} for row in selected],
        'snapshot_version': snapshot, 'capability_state': 'available', 'capability_reason': None,
        'next_cursor': signing.dumps({'snapshot_version': snapshot, 'offset': following, 'expires_at': expiry}, salt='notification-cases') if following < len(rows) else None})
    if len(json.dumps(value, separators=(',', ':')).encode()) > runtime.payload['api']['max_json_bytes']:
        raise ApiError('NOTIFICATION_DIRECTORY_LIMIT_EXCEEDED', 422)
    return Response(value)


class NotificationAcknowledgement(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    delivery_ticket: str = Field(min_length=1, max_length=65536)


@api_view(['POST'])
@handle
@transaction.atomic
def notification_send(request, recipient_id):
    from requests.notifications import submit_notification

    return Response(submit_notification(request, recipient_id), status=201)


@api_view(['GET'])
@handle
@transaction.atomic
def family_notification_message(request, recipient_id):
    actor, _, _ = notification_identity(request, False)
    if request.query_params:
        raise ApiError('NOTIFICATION_QUERY_INVALID', 422)
    recipient = AlertRecipient.objects.get(id=recipient_id)
    _, _, rows = notification_scope(request, recipient.patient_id, False, actor, None)
    if not rows.filter(id=recipient.id).exists():
        raise ApiError('NOTIFICATION_RECIPIENT_SCOPE_DENIED', 403)
    return Response(delivery_message(request, actor, recipient.id))


@api_view(['POST'])
@handle
@transaction.atomic
def family_notification_delivered(request, recipient_id):
    actor, _, _ = notification_identity(request, False)
    data = NotificationAcknowledgement.model_validate(request.data)
    if request.query_params:
        raise ApiError('NOTIFICATION_QUERY_INVALID', 422)
    recipient = AlertRecipient.objects.get(id=recipient_id)
    _, _, rows = notification_scope(request, recipient.patient_id, False, actor, None)
    if not rows.filter(id=recipient.id).exists():
        raise ApiError('NOTIFICATION_RECIPIENT_SCOPE_DENIED', 403)
    return Response(notification_record(acknowledge_delivery(request, actor, recipient.id, data.delivery_ticket)))


@api_view(['GET'])
@handle
@transaction.atomic
def family_notification_alert(request, alert_id):
    from alerting.models import AlertEvent

    actor, _, _ = notification_identity(request, False)
    if request.query_params:
        raise ApiError('NOTIFICATION_QUERY_INVALID', 422)
    event = AlertEvent.objects.get(id=alert_id)
    patient, _, rows = notification_scope(request, event.patient_id, False, actor, None)
    recipient = rows.filter(alert_id=event.id, channel='in_app').first()
    return Response({'patient_id': str(patient.id), 'alert_id': str(event.id),
        'record': notification_record(recipient) if recipient is not None else None})
