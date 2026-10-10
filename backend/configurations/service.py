import hashlib
import json
from datetime import datetime, timezone

from django.db import connection, transaction

from configurations.models import ConfigApproval, ConfigValidation, ConfigVersion
from family_content.models import EnvironmentGate


SUPPORTED_SCHEMAS = {
    "dictionary": {"dictionary-1"},
    "mapping": {"mapping-1", "unit-mapping-2"},
    "verification": {"verification-1", "qualification-policy-2"},
    "window": {"window-1", "alert-window-1", "sampling-frequency-1"},
    "rule": {"rule-1", "alert-trigger-1", "assessment-rule-2", "assessment-context-policy-2", "dynamic-input-policy-2", "alert-lifecycle-2"},
    "visibility": {"visibility-1"},
    "content": {"field-config-1", "content-1", "contact-1"},
    "summary": {"summary-1"},
    "notification": {"notification-1", "material-reminder-channel-2"},
    "provider": {"backend-runtime-1", "provider-1", "purpose-processing-2", "purpose-processing-text-1", "archive-quota-1", "dynamic-schedule-2", "notification-in-app-2"},
    "validation": {"validation-1"},
}


class ConfigRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


def compute_checksum(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _next_version(config_key: str) -> int:
    latest = ConfigVersion.objects.filter(config_key=config_key).order_by("-version").first()
    return (latest.version + 1) if latest else 1


@transaction.atomic
def register_config(
    *,
    kind,
    config_key,
    schema_version,
    payload,
    created_by,
    source_revision=None,
) -> ConfigVersion:
    from configurations.schemas import validate_payload

    allowed = SUPPORTED_SCHEMAS.get(kind)
    if allowed is None:
        raise ConfigRejected("CONFIG_KIND_UNREGISTERED")
    if schema_version not in allowed:
        raise ConfigRejected("CONFIG_SCHEMA_UNAVAILABLE")
    validate_payload(kind, schema_version, payload)
    lock_key = int.from_bytes(hashlib.sha256(config_key.encode()).digest()[:8], signed=True)
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_key])
    return ConfigVersion.objects.create(
        config_key=config_key,
        kind=kind,
        version=_next_version(config_key),
        schema_version=schema_version,
        payload=payload,
        checksum=compute_checksum(payload),
        source_revision=source_revision,
        status=ConfigVersion.Status.DRAFT,
        created_by=created_by,
        created_at=datetime.now(timezone.utc),
    )


@transaction.atomic
def approve_config(
    *,
    config,
    approved_by,
    approval_role,
    evidence_ref,
    effective_scope,
    valid_until=None,
) -> ConfigApproval:
    from authorization.policy import evaluate
    from configurations.lifecycle import perform_configuration_operation

    if not isinstance(effective_scope, dict) or set(effective_scope) != {'environment', 'config_checksum', 'audience'}:
        raise ConfigRejected('CONFIG_APPROVAL_SCOPE_INVALID')
    gate = EnvironmentGate.objects.select_for_update().get(environment=effective_scope['environment'])
    evaluate(principal=approved_by, patient=None, action='approve_config', purpose='clinical',
        environment=gate.environment, session_auth_epoch=approved_by.auth_epoch)
    approval, _ = perform_configuration_operation(actor=approved_by, gate=gate, operation='config_approve',
        keys={'config_id': config.id}, payload={'config_checksum': effective_scope['config_checksum'],
            'environment': gate.environment, 'audience': effective_scope['audience'], 'approval_role': approval_role,
            'evidence_ref': evidence_ref, 'valid_until': valid_until.isoformat() if valid_until else None})
    return approval


@transaction.atomic
def validate_config(
    *,
    config,
    environment,
    artifact_sha256,
    dataset_revision,
    evidence_ref,
    evidence_sha256,
    executed_by,
) -> ConfigValidation:
    from authorization.policy import evaluate
    from configurations.lifecycle import perform_configuration_operation

    evaluate(principal=executed_by, patient=None, action='register_validation', purpose='clinical',
        environment=environment.environment, session_auth_epoch=executed_by.auth_epoch)
    validation, _ = perform_configuration_operation(actor=executed_by, gate=environment, operation='config_validate',
        keys={'config_id': config.id}, payload={'config_checksum': config.checksum, 'environment': environment.environment,
            'artifact_sha256': artifact_sha256, 'dataset_revision_id': str(dataset_revision.id),
            'evidence_ref': evidence_ref, 'evidence_sha256': evidence_sha256})
    return validation


@transaction.atomic
def enable_config(*, config, environment: EnvironmentGate, actor, expected_gate_version, reason, expected_checksum) -> EnvironmentGate:
    from authorization.policy import evaluate
    from configurations.lifecycle import perform_configuration_operation

    if expected_checksum != config.checksum:
        raise ConfigRejected("CONFIG_CHECKSUM_MISMATCH")
    current = ConfigVersion.objects.get(id=config.id)
    if current.checksum != expected_checksum:
        raise ConfigRejected('CONFIG_CHECKSUM_MISMATCH')
    evaluate(principal=actor, patient=None, action='publish_config', purpose='clinical',
        environment=environment.environment, session_auth_epoch=actor.auth_epoch)
    perform_configuration_operation(actor=actor, gate=environment, operation='config_enable',
        keys={'config_id': config.id}, payload={'expected_gate_version': str(expected_gate_version), 'reason': reason})
    environment.refresh_from_db()
    return environment


def loaded_config_version(environment: EnvironmentGate) -> dict:
    return {
        "environment": environment.environment,
        "gate_version": environment.version,
        "publication_epoch": environment.publication_epoch,
        "runtime_config_id": str(environment.runtime_config_id) if environment.runtime_config_id else None,
    }
