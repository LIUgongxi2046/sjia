import json
import os
import uuid
from datetime import timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone

from alerting.models import AlertEvaluation, AlertEvent, AlertRecipient
from alerting.service import read_dynamic_evaluation
from assessment.provenance import entity_reference
from assessment.qualified_facts import lock_case_permissions, qualified_configuration
from audit.service import write_audit
from audit.models import AuditEvent
from authorization.policy import check_principal_session, check_service_grant, matching_case_grants
from configurations.models import ConfigVersion
from configurations.evidence import inspect_approved_evidence
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from family_content.alert_detail import alert_content
from family_content.models import EnvironmentGate, ReleaseComponent
from family_content.policies import NotificationPolicy
from identity.models import Patient, Principal
from notifications.errors import NotificationRejected
from notifications.models import NotificationAttempt, NotificationReceipt
from notifications.policies import NativeNotificationPolicy
from relations.models import Relation
from relations.service import lock_relation_for_access
from retention.models import RetentionPolicy
from tasks.models import ConsumerReceipt, Task, TaskAttempt
from tasks.outbox import emit_event, record_receipt


PROVIDER = 'sjia-in-app-2'
TASK_SCHEMA = 'native-notification-task-2'


def signing_key_availability_reason(reference):
    if not reference.startswith('env:') or reference.count(':') != 1:
        return 'NOTIFICATION_SECRET_REFERENCE_INVALID'
    value = os.environ.get(reference.removeprefix('env:'))
    if value is None or len(value.encode()) < 32:
        return 'NOTIFICATION_SIGNING_SECRET_UNAVAILABLE'
    return None


def signing_key(reference):
    reason = signing_key_availability_reason(reference)
    if reason is not None:
        raise NotificationRejected(reason)
    return os.environ[reference.removeprefix('env:')]


def sign(value, reference, salt):
    return signing.dumps(value, key=signing_key(reference), salt=salt)


def verify(token, reference, salt):
    return signing.loads(token, key=signing_key(reference), salt=salt, fallback_keys=[])


def native_configuration(gate, *, lock=True):
    if not gate.enabled or gate.runtime_config_id is None:
        raise NotificationRejected('NOTIFICATION_PROVIDER_UNAVAILABLE')
    configs = ConfigVersion.objects.select_for_update() if lock else ConfigVersion.objects
    runtime = configs.filter(id=gate.runtime_config_id,
        kind='provider', schema_version='backend-runtime-1').first()
    if runtime is None or configuration_qualification_reason(runtime, gate.environment, audience='support'):
        raise NotificationRejected('NOTIFICATION_CONFIGURATION_UNAVAILABLE')
    providers = [item for item in runtime.payload['providers'] if item['kind'] == 'notification' and item['enabled']]
    if len(providers) != 1:
        raise NotificationRejected('NOTIFICATION_PROVIDER_UNAVAILABLE')
    entry = providers[0]
    config = configs.filter(id=entry['config_id'], kind='provider', schema_version='notification-in-app-2').first()
    if config is None:
        raise NotificationRejected('NOTIFICATION_PROVIDER_PROTOCOL_UNAVAILABLE')
    config = qualified_configuration(config.id, kind='provider', schema_version='notification-in-app-2',
        environment=gate.environment, known_at=timezone.now(), mode='current', lock=lock)
    policy = NativeNotificationPolicy.model_validate_json(json.dumps(config.payload))
    for purpose in {policy.purpose, *policy.event_purposes}:
        for reference in policy.evidence_refs:
            _, _, reason = inspect_approved_evidence(reference, purpose=purpose, lock=lock)
            if reason:
                raise NotificationRejected(reason)
    if (policy.runtime_config_key != runtime.config_key or policy.environment != gate.environment
            or entry['endpoint'] != policy.endpoint or entry['secret_ref'] != policy.signing_secret_ref
            or entry['submit_retries'] != 0 or entry['processing_basis_ref'] not in policy.evidence_refs
            or str(policy.retention_policy_id) not in runtime.payload['retention_policy_ids']):
        raise NotificationRejected('NOTIFICATION_CONFIGURATION_SCOPE_MISMATCH')
    signing_key(policy.signing_secret_ref)
    definitions = [item for item in runtime.payload['tasks'] if item['task_type'] == 'notification']
    if len(definitions) != 1:
        raise NotificationRejected('NOTIFICATION_TASK_POLICY_UNAVAILABLE')
    policies = RetentionPolicy.objects.select_for_update() if lock else RetentionPolicy.objects
    retention = policies.get(id=policy.retention_policy_id)
    if (retention.status != 'active' or retention.approved_at is None or retention.approved_by_id is None
            or not retention.evidence_ref or not retention.evidence_ref.strip()):
        raise NotificationRejected('NOTIFICATION_RETENTION_UNAVAILABLE')
    return runtime, config, policy, retention


def locked_scope(recipient_id):
    gate = EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
    runtime, config, policy, retention = native_configuration(gate)
    recipient = AlertRecipient.objects.get(id=recipient_id)
    # 人员与患儿身份保持串行保护，审计外键可以并发引用这些稳定标识。
    patient = Patient.objects.select_for_update(no_key=True).get(id=recipient.patient_id)
    recipient = AlertRecipient.objects.select_for_update().get(id=recipient.id, patient_id=patient.id)
    event = AlertEvent.objects.select_for_update().get(id=recipient.alert_id, patient=patient)
    relation = lock_relation_for_access(recipient.relation_id, patient_id=patient.id)
    actors = {row.id: row for row in Principal.objects.select_for_update(no_key=True).filter(
        id__in=[relation.guardian_id, policy.executor_id]).order_by('id')}
    if policy.executor_id not in actors or relation.guardian_id not in actors:
        raise NotificationRejected('NOTIFICATION_PRINCIPAL_UNAVAILABLE')
    owner, executor = actors[relation.guardian_id], actors[policy.executor_id]
    now = timezone.now()
    if (patient.status != 'active' or owner.account_status != 'active' or executor.account_status != 'active'
            or relation.status != 'active' or relation.valid_from > now or relation.revoked_at is not None
            or relation.valid_until is not None and relation.valid_until <= now or recipient.channel != 'in_app'):
        raise NotificationRejected('NOTIFICATION_RECIPIENT_SCOPE_DENIED')
    role = check_service_grant(executor, 'manage_notifications', gate.environment, now)
    component = ReleaseComponent.objects.select_for_update().get(release_id=event.release_id, component_kind='notification')
    channels = NotificationPolicy.model_validate_json(json.dumps(component.config.payload))
    if channels.purpose not in {policy.purpose, *policy.event_purposes}:
        raise NotificationRejected('NOTIFICATION_CHANNEL_SCOPE_DENIED')
    sender_grants = lock_case_permissions(executor, patient, 'read_case', channels.purpose, now, whole_resource=True)
    receiver_grants = lock_case_permissions(owner, patient, 'receive_alert', channels.purpose, now, require_relation=True)
    receiver_grants = [row for row in receiver_grants if row.relation_id == relation.id and row.record_scope == 'all']
    if not receiver_grants:
        raise NotificationRejected('NOTIFICATION_RECIPIENT_SCOPE_DENIED')
    evaluation = AlertEvaluation.objects.select_for_update().get(id=event.evaluation_id, patient=patient)
    if evaluation.environment != gate.environment:
        raise NotificationRejected('NOTIFICATION_ENVIRONMENT_MISMATCH')
    evaluation_task = Task.objects.select_for_update().get(id=evaluation.task_id, schema_version='dynamic-evaluation-task-2')
    if evaluation_task.input_refs['authorization_context']['purpose'] != channels.purpose:
        raise NotificationRejected('NOTIFICATION_CHANNEL_SCOPE_DENIED')
    evaluation_owner = Principal.objects.select_for_update(no_key=True).get(id=evaluation_task.actor_id)
    # 实际重新核查当前计算证明和全部字段依赖，不采用历史有效标签作为发送许可。
    result = read_dynamic_evaluation(evaluation_id=evaluation.id, actor=evaluation_owner,
        auth_epoch=int(evaluation_task.input_refs['authorization_context']['auth_epoch']), environment=gate.environment)
    content = alert_content(event, [])
    expected = {'schema_version': 'dynamic-family-event-2', 'evaluation_id': str(evaluation.id),
        'input_digest': evaluation.input_digest, 'context_sequence': str(evaluation.context_sequence),
        'data_epoch': str(patient.data_epoch), 'verification_epoch': str(patient.verification_epoch),
        'publication_epoch': str(gate.publication_epoch)}
    if (result['validity'] != 'valid' or result['mode'] != 'current' or result['status'] != 'completed'
            or result['decision'] != 'met' or event.validity != 'valid'
            or any(event.input_snapshot.get(key) != value for key, value in expected.items())
            or content['validity'] != 'valid' or content['content_state'] != 'available'
            or content['content']['contact']['state'] != 'available'):
        raise NotificationRejected('NOTIFICATION_EVENT_INVALIDATED')
    if channels.environment != gate.environment or 'in_app' not in channels.channels:
        raise NotificationRejected('NOTIFICATION_CHANNEL_SCOPE_DENIED')
    task_policy = next(item for item in runtime.payload['tasks'] if item['task_type'] == 'notification')
    if (channels.max_attempts > task_policy['max_attempts']
            or channels.retry_delays_seconds != task_policy['retry_delays_seconds'][:channels.max_attempts - 1]):
        raise NotificationRejected('NOTIFICATION_TASK_POLICY_MISMATCH')
    context = {'gate': entity_reference(gate), 'patient': entity_reference(patient), 'event': entity_reference(event),
        'evaluation': entity_reference(evaluation), 'owner': entity_reference(owner), 'executor': entity_reference(executor),
        'relation': entity_reference(relation), 'role': entity_reference(role), 'runtime': entity_reference(runtime),
        'provider': entity_reference(config), 'retention': entity_reference(retention),
        'sender_grants': [entity_reference(row) for row in sender_grants],
        'receiver_grants': [entity_reference(row) for row in receiver_grants],
        'channel_policy': entity_reference(component.config), 'content_sha256': compute_checksum(content)}
    return recipient, event, owner, executor, gate, runtime, config, policy, retention, channels, content, context


def checked_permit(attempt):
    task = Task.objects.select_for_update().get(id=attempt.send_permit_id, schema_version=TASK_SCHEMA,
        task_type='notification', patient_id=attempt.patient_id)
    refs = task.input_refs
    if (compute_checksum(refs) != task.input_sha256 or refs['recipient_id'] != str(attempt.recipient_id)
            or refs['attempt_id'] != str(attempt.id) or refs['provider_request_id'] != attempt.provider_idempotency_key
            or refs['executor_id'] != str(task.actor_id) or refs['task_id'] != str(task.id)
            or refs['principal_auth_epoch'] != str(attempt.principal_auth_epoch)
            or refs['relation_auth_epoch'] != str(attempt.relation_auth_epoch)
            or refs['publication_epoch'] != str(attempt.publication_epoch)
            or refs['authorized_at'] != attempt.authorized_at.isoformat() or refs['expires_at'] != attempt.permit_expires_at.isoformat()):
        raise NotificationRejected('NOTIFICATION_PERMIT_PROOF_MISMATCH')
    signed = verify(refs['permit_proof'], refs['signing_secret_ref'], 'native-notification-permit')
    if signed != {key: value for key, value in refs.items() if key != 'permit_proof'}:
        raise NotificationRejected('NOTIFICATION_PERMIT_PROOF_MISMATCH')
    return task


def checked_receipts(attempt, task):
    proofs = list(TaskAttempt.objects.select_for_update().filter(task=task, status='completed', attempt_number=1))
    rows = list(NotificationReceipt.objects.filter(attempt=attempt).order_by('received_at', 'id'))
    if not rows:
        return rows
    if len(proofs) != 1 or proofs[0].provider_request_id != attempt.provider_idempotency_key:
        raise NotificationRejected('NOTIFICATION_RECEIPT_PROOF_MISMATCH')
    evidence = proofs[0].usage_evidence
    if (evidence['provider'] != PROVIDER or evidence['mailbox_sha256'] != compute_checksum(task.input_refs['body'])
            or attempt.provider_request_id != attempt.provider_idempotency_key):
        raise NotificationRejected('NOTIFICATION_RECEIPT_PROOF_MISMATCH')
    for row in rows:
        token = evidence['receipt_proofs'].get(str(row.id))
        if token is None or sha256(token.encode()).hexdigest() != row.evidence_sha256:
            raise NotificationRejected('NOTIFICATION_RECEIPT_PROOF_MISMATCH')
        body = verify(token, task.input_refs['signing_secret_ref'], 'native-notification-receipt')
        if body != {'provider': PROVIDER, 'provider_request_id': attempt.provider_idempotency_key,
                'attempt_id': str(attempt.id), 'recipient_id': str(attempt.recipient_id), 'receipt_id': str(row.id),
                'provider_event_id': row.provider_event_id, 'status': row.status, 'occurred_at': row.occurred_at.isoformat(),
                'received_at': row.received_at.isoformat(), 'delivery_proof': body.get('delivery_proof')} or row.provider != PROVIDER:
            raise NotificationRejected('NOTIFICATION_RECEIPT_PROOF_MISMATCH')
        if row.status == 'accepted' and body['delivery_proof'] is not None:
            raise NotificationRejected('NOTIFICATION_RECEIPT_PROOF_MISMATCH')
        if row.status == 'delivered':
            ticket = verify(body['delivery_proof'], task.input_refs['signing_secret_ref'], 'native-notification-delivery')
            if (ticket['attempt_id'] != str(attempt.id) or ticket['recipient_id'] != str(attempt.recipient_id)
                    or ticket['owner_id'] != task.input_refs['owner_id'] or ticket['input_sha256'] != task.input_sha256
                    or not ticket['issued_at'] <= row.occurred_at.timestamp() <= ticket['expires_at']):
                raise NotificationRejected('NOTIFICATION_RECEIPT_PROOF_MISMATCH')
    return rows


@transaction.atomic
def authorize_native(recipient_id, *, initiator=None):
    recipient, event, owner, executor, gate, runtime, config, policy, retention, channels, content, context = locked_scope(recipient_id)
    previous = NotificationAttempt.objects.select_for_update().filter(recipient_id=recipient.id).order_by('-attempt_number').first()
    now = timezone.now()
    if previous is not None:
        task = checked_permit(previous)
        checked_receipts(previous, task)
        if previous.status in ('authorized', 'started', 'accepted', 'uncertain'):
            return previous, task
        if previous.attempt_number >= channels.max_attempts or now < previous.finished_at + timedelta(seconds=channels.retry_delays_seconds[previous.attempt_number - 1]):
            raise NotificationRejected('NOTIFICATION_RETRY_UNAVAILABLE')
    elif NotificationAttempt.objects.filter(recipient_id=recipient.id).exists():
        raise NotificationRejected('NOTIFICATION_RESULT_UNCERTAIN')
    if AlertRecipient.objects.filter(alert_id=event.id).count() > policy.max_recipients_per_event:
        raise NotificationRejected('NOTIFICATION_RECIPIENT_BUDGET_EXCEEDED')
    start = now.astimezone(ZoneInfo(policy.timezone)).replace(hour=0, minute=0, second=0, microsecond=0)
    if Task.objects.filter(task_type='notification',
            input_refs__provider_config_id=str(config.id), created_at__gte=start).count() >= policy.max_daily_messages:
        raise NotificationRejected('NOTIFICATION_DAILY_BUDGET_EXCEEDED')
    task_policy = next(item for item in runtime.payload['tasks'] if item['task_type'] == 'notification')
    if Task.objects.filter(task_type='notification', status__in=['accepted', 'queued', 'running'],
            input_refs__provider_config_id=str(config.id)).count() >= task_policy['max_concurrency']:
        raise NotificationRejected('NOTIFICATION_CONCURRENCY_BUDGET_EXCEEDED')
    identifier, task_id = uuid.uuid4(), uuid.uuid4()
    request_id = f'{PROVIDER}:{identifier}'
    expiry = now + timedelta(seconds=policy.permit_seconds)
    relation_epoch = Relation.objects.get(id=recipient.relation_id).auth_epoch
    refs = {'schema_version': TASK_SCHEMA, 'task_id': str(task_id), 'recipient_id': str(recipient.id), 'attempt_id': str(identifier),
        'alert_id': str(event.id), 'owner_id': str(owner.id), 'executor_id': str(executor.id),
        'initiator_id': str(initiator.id) if initiator is not None else str(executor.id),
        'provider_config_id': str(config.id), 'provider_request_id': request_id,
        'signing_secret_ref': policy.signing_secret_ref, 'retention_policy_id': str(retention.id),
        'authorized_at': now.isoformat(), 'expires_at': expiry.isoformat(),
        'principal_auth_epoch': str(owner.auth_epoch), 'relation_auth_epoch': str(relation_epoch),
        'publication_epoch': str(gate.publication_epoch),
        'body': {'approved_text': content['content']['approved_text'], 'deep_link': f'/alerts/{event.id}',
            'content_id': content['content']['content_id'], 'content_version': content['content']['content_version']},
        'context': context}
    refs['permit_proof'] = sign(refs, policy.signing_secret_ref, 'native-notification-permit')
    if len(json.dumps(refs, separators=(',', ':')).encode()) > runtime.payload['api']['max_json_bytes']:
        raise NotificationRejected('NOTIFICATION_PERMIT_LIMIT_EXCEEDED')
    task = Task.objects.create(id=task_id, patient_id=recipient.patient_id, actor=executor, task_type='notification',
        schema_version=TASK_SCHEMA, input_refs=refs, input_sha256=compute_checksum(refs), status='accepted',
        status_version=1, created_at=now, deadline_at=expiry)
    attempt = NotificationAttempt.objects.create(id=identifier, patient_id=recipient.patient_id,
        recipient_id=recipient.id, attempt_number=previous.attempt_number + 1 if previous is not None else 1,
        send_permit_id=task.id, provider_idempotency_key=request_id, authorized_at=now, permit_expires_at=expiry,
        principal_auth_epoch=owner.auth_epoch, relation_auth_epoch=relation_epoch,
        publication_epoch=gate.publication_epoch, status='authorized')
    recipient.delivery_status = 'pending'
    recipient.save(update_fields=['delivery_status'])
    write_audit(actor=executor, patient=event.patient, action='notification_authorize', object_type='notification_attempt',
        object_id=attempt.id, object_version=str(attempt.attempt_number), outcome='authorized', retention_policy=retention, occurred_at=now)
    emit_event(event_type='notification.native_requested', schema_version=TASK_SCHEMA, aggregate_id=task.id,
        aggregate_version=1, correlation_id=task.id, resource_refs={'recipient_id': str(recipient.id), 'attempt_id': str(attempt.id)}, task=task)
    return attempt, task


def save_native_receipt(attempt, task, proof, status, *, delivery_proof=None):
    event_id = f'{PROVIDER}:{attempt.id}:{status}'
    current = NotificationReceipt.objects.filter(provider=PROVIDER, provider_event_id=event_id).first()
    if current is not None:
        checked_receipts(attempt, task)
        return current
    now, identifier = timezone.now(), uuid.uuid4()
    body = {'provider': PROVIDER, 'provider_request_id': attempt.provider_idempotency_key,
        'attempt_id': str(attempt.id), 'recipient_id': str(attempt.recipient_id), 'receipt_id': str(identifier),
        'provider_event_id': event_id, 'status': status, 'occurred_at': now.isoformat(),
        'received_at': now.isoformat(), 'delivery_proof': delivery_proof}
    token = sign(body, task.input_refs['signing_secret_ref'], 'native-notification-receipt')
    row = NotificationReceipt.objects.create(id=identifier, attempt=attempt, provider=PROVIDER,
        provider_event_id=event_id, status=status, occurred_at=now, received_at=now, evidence_sha256=sha256(token.encode()).hexdigest())
    proof.usage_evidence['receipt_proofs'][str(row.id)] = token
    proof.save(update_fields=['usage_evidence'])
    return row


@transaction.atomic
def submit_native(attempt_id):
    initial = NotificationAttempt.objects.get(id=attempt_id)
    recipient, event, owner, executor, gate, _, _, _, retention, _, _, context = locked_scope(initial.recipient_id)
    attempt = NotificationAttempt.objects.select_for_update().get(id=initial.id)
    task = checked_permit(attempt)
    receipts = checked_receipts(attempt, task)
    if attempt.status == 'accepted':
        if not any(row.status == 'accepted' for row in receipts):
            raise NotificationRejected('NOTIFICATION_RECEIPT_PROOF_MISMATCH')
        return attempt
    if attempt.status in ('started', 'uncertain'):
        raise NotificationRejected('NOTIFICATION_RESULT_UNCERTAIN')
    if (attempt.status != 'authorized' or attempt.permit_expires_at <= timezone.now()
            or attempt.principal_auth_epoch != owner.auth_epoch or attempt.relation_auth_epoch != Relation.objects.get(id=recipient.relation_id).auth_epoch
            or attempt.publication_epoch != gate.publication_epoch or task.input_refs['context'] != context):
        raise NotificationRejected('NOTIFICATION_PERMIT_CHANGED')
    now = timezone.now()
    attempt.status, attempt.started_at, attempt.provider_request_id = 'started', now, attempt.provider_idempotency_key
    attempt.save(update_fields=['status', 'started_at', 'provider_request_id'])
    proof = TaskAttempt.objects.create(task=task, attempt_number=1, lease_generation=task.lease_generation,
        provider_request_id=attempt.provider_request_id, started_at=now, finished_at=now, status='completed',
        usage_evidence={'provider': PROVIDER, 'mailbox_sha256': compute_checksum(task.input_refs['body']), 'receipt_proofs': {}})
    # 原生 Provider 的受理表示准确正文和许可已持久保存；送达由本人浏览器另行确认。
    receipt = save_native_receipt(attempt, task, proof, 'accepted')
    proof.finished_at = receipt.occurred_at
    proof.save(update_fields=['finished_at'])
    attempt.status, attempt.finished_at = 'accepted', receipt.occurred_at
    attempt.save(update_fields=['status', 'finished_at'])
    task.status, task.status_version = 'completed', task.status_version + 1
    task.save(update_fields=['status', 'status_version'])
    recipient.delivery_status = 'accepted'
    recipient.save(update_fields=['delivery_status'])
    write_audit(actor=executor, patient=event.patient, action='notification_accept', object_type='notification_attempt',
        object_id=attempt.id, object_version=str(attempt.attempt_number), outcome='accepted', retention_policy=retention, occurred_at=now)
    return attempt


@transaction.atomic
def consume_native(event):
    gate = EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
    consumer = 'native_notifications'
    prior = ConsumerReceipt.objects.filter(consumer=consumer, event=event).first()
    if prior is not None:
        return prior
    if event.event_type == 'alert.event_created':
        runtime, _, policy, _ = native_configuration(gate)
        alert = AlertEvent.objects.select_for_update().get(id=event.aggregate_id)
        if (event.schema_version != 'dynamic-family-event-2' or event.resource_refs['alert_id'] != str(alert.id)
                or event.resource_refs['evaluation_id'] != str(alert.evaluation_id) or event.resource_refs['release_id'] != str(alert.release_id)):
            raise NotificationRejected('NOTIFICATION_EVENT_PROOF_MISMATCH')
        patient = Patient.objects.select_for_update(no_key=True).get(id=alert.patient_id)
        component = ReleaseComponent.objects.select_for_update().get(release_id=alert.release_id, component_kind='notification')
        channels = NotificationPolicy.model_validate_json(json.dumps(component.config.payload))
        if channels.environment != gate.environment or channels.purpose not in {policy.purpose, *policy.event_purposes}:
            raise NotificationRejected('NOTIFICATION_CHANNEL_SCOPE_DENIED')
        now = timezone.now()
        relations = list(Relation.objects.select_for_update().filter(patient=patient, status='active').order_by('id'))
        qualified = []
        for relation in relations:
            owner = Principal.objects.select_for_update(no_key=True).get(id=relation.guardian_id)
            if (owner.account_status == 'active' and relation.valid_from <= now and relation.revoked_at is None
                    and (relation.valid_until is None or relation.valid_until > now)
                    and any(row.relation_id == relation.id and row.purpose == channels.purpose and row.evidence_ref.strip()
                        and row.record_scope == 'all' for row in matching_case_grants(owner, patient, 'receive_alert', now, require_relation=True))):
                qualified.append(relation)
        if not qualified or len(qualified) > min(policy.max_recipients_per_event, runtime.payload['api']['max_page_size']):
            raise NotificationRejected('NOTIFICATION_RECIPIENT_BUDGET_EXCEEDED')
        for relation in qualified:
            recipient, _ = AlertRecipient.objects.get_or_create(patient_id=patient.id, alert_id=alert.id,
                relation_id=relation.id, channel='in_app', defaults={'delivery_status': 'not_sent'})
            attempt, _ = authorize_native(recipient.id)
            submit_native(attempt.id)
        return record_receipt(consumer, event, alert.id)
    if event.event_type != 'notification.native_requested' or event.schema_version != TASK_SCHEMA:
        raise NotificationRejected('NOTIFICATION_EVENT_PROOF_MISMATCH')
    task = Task.objects.select_for_update().get(id=event.task_id, schema_version=TASK_SCHEMA)
    if (event.aggregate_id != task.id or event.resource_refs != {'recipient_id': task.input_refs['recipient_id'], 'attempt_id': task.input_refs['attempt_id']}):
        raise NotificationRejected('NOTIFICATION_EVENT_PROOF_MISMATCH')
    attempt = NotificationAttempt.objects.select_for_update().get(id=task.input_refs['attempt_id'])
    checked_permit(attempt)
    receipts = checked_receipts(attempt, task)
    if attempt.status != 'accepted' or not any(row.status == 'accepted' for row in receipts):
        submit_native(attempt.id)
    return record_receipt(consumer, event, task.id)


@transaction.atomic
def delivery_message(request, actor, recipient_id):
    recipient, event, owner, _, gate, _, _, policy, _, _, _, _ = locked_scope(recipient_id)
    if actor.id != owner.id:
        raise NotificationRejected('NOTIFICATION_RECIPIENT_SCOPE_DENIED')
    attempt = NotificationAttempt.objects.select_for_update().filter(recipient_id=recipient.id, status='accepted').order_by('-attempt_number').first()
    if attempt is None:
        raise NotificationRejected('NOTIFICATION_MAILBOX_UNAVAILABLE')
    task = checked_permit(attempt)
    rows = checked_receipts(attempt, task)
    if (not any(row.status == 'accepted' for row in rows) or task.status != 'completed'
            or task.input_refs['context']['gate'] != entity_reference(gate)
            or task.input_refs['body']['approved_text'] != alert_content(event, [])['content']['approved_text']):
        raise NotificationRejected('NOTIFICATION_MAILBOX_CHANGED')
    now = timezone.now().timestamp()
    expiry = min(now + policy.delivery_ticket_seconds, request.session.get_expiry_date().timestamp())
    if expiry <= now:
        raise NotificationRejected('NOTIFICATION_SESSION_EXPIRED')
    ticket = sign({'schema_version': 'native-notification-delivery-2', 'attempt_id': str(attempt.id),
        'recipient_id': str(recipient.id), 'owner_id': str(owner.id), 'input_sha256': task.input_sha256,
        'auth_epoch': str(owner.auth_epoch), 'relation_auth_epoch': str(attempt.relation_auth_epoch),
        'publication_epoch': str(gate.publication_epoch), 'issued_at': now, 'expires_at': expiry},
        task.input_refs['signing_secret_ref'], 'native-notification-delivery')
    return {'recipient_id': str(recipient.id), 'patient_id': str(recipient.patient_id), 'alert_id': str(event.id),
        'attempt_id': str(attempt.id), 'body': task.input_refs['body'], 'delivery_ticket': ticket,
        'expires_at': timezone.datetime.fromtimestamp(expiry, tz=timezone.get_current_timezone()).isoformat()}


@transaction.atomic
def acknowledge_delivery(request, actor, recipient_id, token):
    recipient, event, owner, _, gate, _, _, _, retention, _, _, _ = locked_scope(recipient_id)
    if actor.id != owner.id:
        raise NotificationRejected('NOTIFICATION_RECIPIENT_SCOPE_DENIED')
    attempt = NotificationAttempt.objects.select_for_update().filter(recipient_id=recipient.id, status='accepted').order_by('-attempt_number').first()
    if attempt is None:
        raise NotificationRejected('NOTIFICATION_MAILBOX_UNAVAILABLE')
    task = checked_permit(attempt)
    checked_receipts(attempt, task)
    body = verify(token, task.input_refs['signing_secret_ref'], 'native-notification-delivery')
    now = timezone.now()
    if (set(body) != {'schema_version', 'attempt_id', 'recipient_id', 'owner_id', 'input_sha256', 'auth_epoch',
            'relation_auth_epoch', 'publication_epoch', 'issued_at', 'expires_at'}
            or body['schema_version'] != 'native-notification-delivery-2' or body['attempt_id'] != str(attempt.id)
            or body['recipient_id'] != str(recipient.id) or body['owner_id'] != str(owner.id)
            or body['input_sha256'] != task.input_sha256 or body['auth_epoch'] != str(owner.auth_epoch)
            or body['relation_auth_epoch'] != str(Relation.objects.get(id=recipient.relation_id).auth_epoch)
            or body['publication_epoch'] != str(gate.publication_epoch)
            or not body['issued_at'] <= now.timestamp() < body['expires_at'] <= request.session.get_expiry_date().timestamp()):
        raise NotificationRejected('NOTIFICATION_DELIVERY_TICKET_CHANGED')
    proof = TaskAttempt.objects.select_for_update().get(task=task, status='completed', attempt_number=1)
    receipt = save_native_receipt(attempt, task, proof, 'delivered', delivery_proof=token)
    recipient.delivery_status = 'delivered'
    recipient.save(update_fields=['delivery_status'])
    if not AuditEvent.objects.filter(actor=owner, patient=event.patient, action='notification_delivered',
            object_type='notification_receipt', object_id=receipt.id, object_version='1', outcome='delivered').exists():
        write_audit(actor=owner, patient=event.patient, action='notification_delivered', object_type='notification_receipt',
            object_id=receipt.id, object_version='1', outcome='delivered', retention_policy=retention, occurred_at=now)
    return recipient
