"""Select explicitly approved draft retention; never invent a retention period."""
from datetime import timedelta

from django.utils import timezone
from pydantic import ValidationError

from drafts.service import DraftRejected
from identity.models import Principal
from retention.models import RetentionPolicy
from retention.policy_locks import read_retention_policy_shared
from sources.operations import RetentionRules


def draft_retention():
    now = timezone.now()
    candidates = list(RetentionPolicy.objects.filter(data_class='draft', status='active',
        approved_by__account_status='active', approved_at__lte=now).order_by('id'))
    candidates = [row for row in candidates if row.evidence_ref and row.evidence_ref.strip()]
    if len(candidates) != 1:
        raise DraftRejected('DRAFT_RETENTION_UNAVAILABLE')
    policy = read_retention_policy_shared(candidates[0].id)
    if (policy.status != 'active' or policy.data_class != 'draft' or policy.schema_version != 'retention-1'
            or policy.approved_at is None or policy.approved_at > now or policy.approved_by_id is None
            or not policy.evidence_ref or not policy.evidence_ref.strip()
            or not Principal.objects.filter(id=policy.approved_by_id, account_status='active').exists()):
        raise DraftRejected('DRAFT_RETENTION_UNAVAILABLE')
    try:
        rules = RetentionRules.model_validate(policy.rules)
        if rules.disposition == 'delete' and rules.retention_days is None:
            raise ValueError()
        expires_at = now + timedelta(days=rules.retention_days) if rules.disposition == 'delete' else None
    except (ValidationError, ValueError, OverflowError):
        raise DraftRejected('DRAFT_RETENTION_UNAVAILABLE') from None
    return policy, expires_at
