"""提交意图、幂等与结果恢复。

同一业务键只产生一次效果：相同正文复用原意图，不同正文拒绝；
failed 且 generation 一致的意图可在证明未提交后重试。
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from django.db import transaction

from requests.models import WriteIntent, WriteRequest
from identity.models import Principal

NORMALIZATION_VERSION = "back-json-1"


class IdempotencyConflict(Exception):
    reason_code = 'IDEMPOTENCY_CONFLICT'


class IntentRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


def compute_digest(operation_id, target_key, schema_version, payload) -> str:
    canonical = json.dumps(
        {
            "operation_id": operation_id,
            "target_key": target_key,
            "schema_version": schema_version,
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_target_key(target_key: str) -> None:
    if not isinstance(target_key, str) or not target_key.startswith("/") or target_key.startswith("/api/v1"):
        raise IntentRejected("TARGET_KEY_INVALID")
    if "?" in target_key or "#" in target_key or "://" in target_key:
        raise IntentRejected("TARGET_KEY_INVALID")


def _lookup(actor_id, operation_id, target_key, request_id):
    row = WriteIntent.all_objects.filter(
        actor_id=actor_id,
        operation_id=operation_id,
        target_key=target_key,
        request_id=request_id,
    ).first()
    if row is not None:
        from retention.content_fences import assert_content_available
        assert_content_available('requests.writeintent', row.id)
    return row


@transaction.atomic
def prepare_intent(
    *,
    actor,
    operation_id,
    target_key,
    request_id,
    payload,
    schema_version,
    retention_policy,
    patient=None,
    ttl_seconds=300,
    authorization_context=None,
    recovery_payload=None,
) -> WriteIntent:
    validate_target_key(target_key)
    Principal.objects.select_for_update().get(id=actor.id)
    digest = compute_digest(operation_id, target_key, schema_version, payload)
    existing = _lookup(actor.id, operation_id, target_key, request_id)
    if existing is not None:
        if existing.patient_id != (patient.id if patient else None) or existing.payload_sha256 != digest or existing.authorization_context != authorization_context or existing.recovery_payload != recovery_payload:
            raise IdempotencyConflict()
        return existing
    now = datetime.now(timezone.utc)
    return WriteIntent.objects.create(
        actor=actor,
        patient=patient,
        operation_id=operation_id,
        target_key=target_key,
        request_id=request_id,
        payload_sha256=digest,
        normalization_version=NORMALIZATION_VERSION,
        generation=1,
        status=WriteIntent.Status.PREPARED,
        prepared_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        retention_policy=retention_policy,
        authorization_context=authorization_context,
        recovery_payload=recovery_payload,
    )


def query_intent(actor_id, operation_id, target_key, request_id):
    return _lookup(actor_id, operation_id, target_key, request_id)


@transaction.atomic
def commit_intent(*, intent, action, resource_type, resource_id, expected_version=None) -> WriteRequest:
    supplied = intent
    intent = WriteIntent.objects.select_for_update().get(id=intent.id)
    if supplied.generation != intent.generation:
        raise IntentRejected("GENERATION_MISMATCH")
    if intent.status != WriteIntent.Status.PREPARED:
        raise IntentRejected("INTENT_NOT_PREPARED")
    now = datetime.now(timezone.utc)
    write_request = WriteRequest.objects.create(
        actor_id=intent.actor_id,
        patient_id=intent.patient_id,
        action=action,
        target_key=intent.target_key,
        request_id=intent.request_id,
        payload_sha256=intent.payload_sha256,
        normalization_version=intent.normalization_version,
        expected_version=expected_version,
        status=WriteRequest.Status.COMMITTED,
        resource_type=resource_type,
        resource_id=resource_id,
        created_at=now,
        completed_at=now,
    )
    intent.write_request = write_request
    intent.status = WriteIntent.Status.COMMITTED
    intent.completed_at = now
    intent.save(update_fields=["write_request", "status", "completed_at"])
    supplied.refresh_from_db()
    return write_request


@transaction.atomic
def fail_intent(*, intent, error_code) -> WriteIntent:
    supplied = intent
    intent = WriteIntent.objects.select_for_update().get(id=intent.id)
    if supplied.generation != intent.generation:
        raise IntentRejected("GENERATION_MISMATCH")
    if intent.status != WriteIntent.Status.PREPARED:
        raise IntentRejected("INTENT_NOT_PREPARED")
    intent.status = WriteIntent.Status.FAILED
    intent.error_code = error_code
    intent.completed_at = datetime.now(timezone.utc)
    intent.save(update_fields=["status", "error_code", "completed_at"])
    return intent


@transaction.atomic
def retry_intent(*, intent, expected_generation, ttl_seconds=300) -> WriteIntent:
    supplied = intent
    intent = WriteIntent.objects.select_for_update().get(id=intent.id)
    if intent.write_request_id is not None or WriteRequest.objects.filter(actor_id=intent.actor_id,
            action=intent.operation_id, target_key=intent.target_key, request_id=intent.request_id).exists():
        raise IntentRejected("INTENT_RECEIPT_RECONCILIATION_REQUIRED")
    if intent.status != WriteIntent.Status.FAILED:
        raise IntentRejected("INTENT_NOT_FAILED")
    if intent.generation != expected_generation:
        raise IntentRejected("GENERATION_MISMATCH")
    intent.generation = intent.generation + 1
    intent.status = WriteIntent.Status.PREPARED
    intent.error_code = None
    intent.completed_at = None
    intent.prepared_at = datetime.now(timezone.utc)
    intent.expires_at = intent.prepared_at + timedelta(seconds=ttl_seconds)
    intent.save(update_fields=["generation", "status", "error_code", "completed_at", "prepared_at", "expires_at"])
    supplied.refresh_from_db()
    return intent


def request_recent_account(actor_id, operation_ids):
    return WriteIntent.objects.filter(actor_id=actor_id, patient_id=None, operation_id__in=operation_ids).order_by("-prepared_at")
