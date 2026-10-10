import json

from django.core import signing
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from alerting.models import AlertEvent, AlertRecipient
from api.base import ApiError
from assessment.provenance import entity_reference
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from family_content.alert_detail import alert_content
from notifications.models import NotificationAttempt, NotificationReceipt
from tasks.models import Task, TaskAttempt
from notifications.native import checked_permit, checked_receipts


class DeliveryQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    cursor: str | None = Field(default=None, min_length=1, max_length=65536)


class DeliveryCursor(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    snapshot_version: str = Field(pattern=r'^[0-9a-f]{64}$')
    offset: int = Field(ge=0)
    expires_at: float


def notification_record(recipient):
    event = AlertEvent.objects.get(id=recipient.alert_id, patient_id=recipient.patient_id)
    content = alert_content(event, [])
    attempts = list(NotificationAttempt.objects.filter(recipient_id=recipient.id, patient_id=recipient.patient_id).order_by('attempt_number', 'id'))
    values, verified = [], True
    for attempt in attempts:
        task = Task.objects.filter(id=attempt.send_permit_id, patient_id=recipient.patient_id,
            task_type='notification', schema_version='native-notification-task-2').first()
        proof = task is not None and task.input_refs.get('recipient_id') == str(recipient.id) and compute_checksum(task.input_refs) == task.input_sha256
        if task is not None and not proof:
            raise ApiError('NOTIFICATION_PERMIT_PROOF_MISMATCH', 409)
        if proof:
            task = checked_permit(attempt)
        verified = verified and proof
        receipts = checked_receipts(attempt, task) if proof else list(NotificationReceipt.objects.filter(attempt=attempt).order_by('received_at', 'id'))
        values.append({'attempt_id': str(attempt.id), 'attempt_number': attempt.attempt_number,
            'permit_id': str(attempt.send_permit_id), 'authorized_at': attempt.authorized_at.isoformat(),
            'expires_at': attempt.permit_expires_at.isoformat(), 'status': attempt.status,
            'provider_request_id': attempt.provider_request_id,
            'started_at': attempt.started_at.isoformat() if attempt.started_at else None,
            'finished_at': attempt.finished_at.isoformat() if attempt.finished_at else None,
            'proof_state': 'available' if proof else 'unavailable', 'receipts': [{
                'receipt_id': str(row.id), 'provider': row.provider, 'provider_event_id': row.provider_event_id,
                'status': row.status, 'occurred_at': row.occurred_at.isoformat(), 'received_at': row.received_at.isoformat(),
                'evidence_sha256': row.evidence_sha256} for row in receipts]})
    status = 'not_sent' if not attempts and recipient.delivery_status == 'not_sent' else 'unknown'
    if attempts and verified:
        last = values[-1]
        receipt_statuses = {row['status'] for row in last['receipts']}
        if 'delivered' in receipt_statuses:
            status = 'delivered'
        elif 'accepted' in receipt_statuses:
            status = 'accepted'
        elif last['status'] in ('authorized', 'started'):
            status = 'pending'
        elif last['status'] in ('failed', 'cancelled', 'uncertain'):
            status = last['status']
    value = {'recipient_id': str(recipient.id), 'patient_id': str(recipient.patient_id), 'alert_id': str(event.id),
        'relation_id': str(recipient.relation_id), 'channel': recipient.channel, 'created_at': event.created_at.isoformat(),
        'delivery_status': status, 'read_at': recipient.read_at.isoformat() if recipient.read_at else None,
        'content_state': content['content_state'], 'current_validity': content['validity'],
        'trace_state': 'available' if attempts and verified else 'unavailable', 'attempts': values}
    value['snapshot_version'] = compute_checksum(value)
    return value


def notification_history(request, actor, patient, gate, grants, recipient_rows, cursor):
    runtime = ConfigVersion.objects.filter(id=gate.runtime_config_id, kind='provider', schema_version='backend-runtime-1').first()
    if runtime is None or configuration_qualification_reason(runtime, gate.environment, audience='support') is not None:
        if cursor is not None:
            raise ApiError('NOTIFICATION_DIRECTORY_CHANGED', 409)
        return {'patient_id': str(patient.id), 'items': [], 'next_cursor': None,
            'snapshot_version': compute_checksum({'gate': entity_reference(gate), 'actor': entity_reference(actor)}),
            'capability_state': 'unavailable', 'capability_reason': 'NOTIFICATION_RUNTIME_UNAVAILABLE'}
    rows = list(recipient_rows.order_by('-alert_id', 'id'))
    records = [notification_record(row) for row in rows]
    attempts = NotificationAttempt.objects.filter(recipient_id__in=[row.id for row in rows]).order_by('id')
    receipts = NotificationReceipt.objects.filter(attempt_id__in=attempts.values('id')).order_by('id')
    events = AlertEvent.objects.filter(id__in=[row.alert_id for row in rows]).order_by('id')
    tasks = Task.objects.filter(id__in=attempts.values('send_permit_id')).order_by('id')
    proofs = TaskAttempt.objects.filter(task_id__in=tasks.values('id')).order_by('id')
    snapshot = compute_checksum({'actor': entity_reference(actor), 'patient': entity_reference(patient), 'gate': entity_reference(gate),
        'grants': [entity_reference(row) for row in grants], 'runtime': entity_reference(runtime),
        'rows': [entity_reference(row) for row in rows], 'attempts': [entity_reference(row) for row in attempts],
        'receipts': [entity_reference(row) for row in receipts], 'events': [entity_reference(row) for row in events],
        'tasks': [entity_reference(row) for row in tasks], 'proofs': [entity_reference(row) for row in proofs]})
    snapshot = compute_checksum({'resources': snapshot, 'current_records': records})
    offset, expiry = 0, request.session.get_expiry_date().timestamp()
    now = timezone.now().timestamp()
    if expiry <= now:
        raise ApiError('AUTH_REQUIRED', 401)
    if cursor is not None:
        page = DeliveryCursor.model_validate_json(json.dumps(signing.loads(cursor, salt='notification-history')))
        if page.snapshot_version != snapshot or page.offset >= len(rows) or not now < page.expires_at <= expiry:
            raise ApiError('NOTIFICATION_DIRECTORY_CHANGED', 409)
        offset = page.offset
    selected = rows[offset:offset + runtime.payload['api']['max_page_size']]
    following = offset + len(selected)
    value = {'patient_id': str(patient.id), 'items': records[offset:following],
        'next_cursor': signing.dumps({'snapshot_version': snapshot, 'offset': following, 'expires_at': expiry}, salt='notification-history') if following < len(rows) else None,
        'snapshot_version': snapshot, 'capability_state': 'available', 'capability_reason': None}
    if len(json.dumps(value, separators=(',', ':')).encode()) > runtime.payload['api']['max_json_bytes']:
        raise ApiError('NOTIFICATION_DIRECTORY_LIMIT_EXCEEDED', 422)
    return value
