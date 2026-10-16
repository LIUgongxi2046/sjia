"""Explicit retention for recoverable request payloads; no default retention period."""
from uuid import UUID

from django.utils import timezone
from pydantic import ValidationError

from authorization.policy import Denied
from family_content.models import EnvironmentGate
from identity.models import Principal
from requests.models import WriteIntent
from requests.service import IntentRejected
from retention.models import RetentionPolicy
from retention.policy_locks import read_retention_policy_shared
from sources.operations import RetentionRules


def request_retention(environment):
    """Caller holds an atomic admission transaction through the intent write."""
    gate = EnvironmentGate.objects.select_for_update().get(environment=environment)
    if not gate.enabled:
        raise Denied('ENVIRONMENT_DISABLED')
    now = timezone.now()
    candidates = list(RetentionPolicy.objects.filter(data_class='request', status='active',
        approved_by__account_status='active', approved_at__lte=now).order_by('id'))
    candidates = [row for row in candidates if isinstance(row.evidence_ref, str) and row.evidence_ref.strip()]
    if len(candidates) != 1:
        raise IntentRejected('REQUEST_RETENTION_UNAVAILABLE')
    policy = read_retention_policy_shared(candidates[0].id)
    if (policy.status != 'active' or policy.data_class != 'request' or policy.schema_version != 'retention-1'
            or policy.approved_at is None or policy.approved_at > now or policy.approved_by_id is None
            or not policy.evidence_ref or not policy.evidence_ref.strip()
            or not Principal.objects.filter(id=policy.approved_by_id, account_status='active').exists()):
        raise IntentRejected('REQUEST_RETENTION_UNAVAILABLE')
    try:
        rules = RetentionRules.model_validate(policy.rules)
        if rules.disposition == 'delete' and rules.retention_days is None:
            raise ValueError('explicit delete interval required')
    except (ValidationError, ValueError):
        raise IntentRejected('REQUEST_RETENTION_UNAVAILABLE') from None
    return policy


def require_request_policy_binding(*, actor, data, policy):
    # Malformed business identity is rejected by its operation-specific validator.
    try:
        request_id = UUID(data.get('request_id'))
    except (AttributeError, TypeError, ValueError):
        return
    existing = WriteIntent.objects.filter(actor=actor, operation_id=data.get('operation_id'),
        target_key=data.get('target_key'), request_id=request_id).first()
    if existing is not None and existing.retention_policy_id != policy.id:
        raise IntentRejected('REQUEST_RETENTION_CHANGED')
