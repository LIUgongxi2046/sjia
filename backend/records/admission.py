"""Current, unambiguous family-record source and retention admission.

No duration, source choice or approval is inferred from an arbitrary first row.
"""
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from pydantic import ValidationError

from configurations.evidence import read_approved_evidence
from identity.models import Principal
from records.service import RecordRejected
from retention.models import RetentionPolicy
from retention.policy_locks import read_retention_policy_shared
from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision
from sources.operations import RetentionRules


def checked_retention(policy, *, data_classes):
    policy = read_retention_policy_shared(policy.id)
    now = timezone.now()
    if (policy.status != 'active' or policy.data_class not in data_classes
            or policy.schema_version != 'retention-1' or policy.approved_at is None
            or policy.approved_at > now or not policy.approved_by_id
            or not policy.evidence_ref or not policy.evidence_ref.strip()
            or not Principal.objects.filter(id=policy.approved_by_id, account_status='active').exists()):
        raise RecordRejected('RECORD_RETENTION_UNAVAILABLE')
    try:
        rules = RetentionRules.model_validate(policy.rules)
        if rules.disposition == 'delete' and rules.retention_days is None:
            raise ValueError()
    except (ValidationError, ValueError):
        raise RecordRejected('RECORD_RETENTION_UNAVAILABLE') from None
    read_approved_evidence(policy.evidence_ref, purpose='configuration_approval')
    return policy


def family_record_admission(patient, kind, *, source_revision_id=None):
    now = timezone.now()
    candidates = RetentionPolicy.objects.filter(data_class=kind, status='active',
        approved_by__account_status='active', approved_at__lte=now).order_by('id')
    policies = []
    for policy in candidates:
        try:
            policies.append(checked_retention(policy, data_classes=(kind,)))
        except RecordRejected:
            continue
    if len(policies) != 1:
        raise RecordRejected('RECORD_RETENTION_UNAVAILABLE')
    authorizations = DataUseAuthorization.objects.filter(patient=patient, purpose='family_observation',
        environment=settings.SJIA_ENVIRONMENT, revoked_at__isnull=True, approved_at__lte=now,
        approved_by__account_status='active').filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now))
    if source_revision_id is not None:
        authorizations = authorizations.filter(source_revision_id=source_revision_id)
    candidates = []
    # Lock only base rows; a nullable joined approval must not become an OF alias.
    for identity in authorizations.order_by('id').values_list('id', flat=True):
        use = DataUseAuthorization.objects.select_for_update().get(id=identity)
        revision = SourceRevision.objects.select_for_update().get(id=use.source_revision_id)
        source = SourceRegistry.objects.select_for_update().get(id=revision.source_id)
        if (use.revoked_at is not None or use.approved_at > now or use.valid_until is not None and use.valid_until <= now
                or not use.evidence_ref.strip() or revision.status != 'approved' or source.status != 'approved'
                or revision.captured_at > now or 'family_observation' not in revision.allowed_use
                or not revision.rights_evidence_ref or not revision.rights_evidence_ref.strip()
                or revision.retention_policy_id is None):
            continue
        source_retention = read_retention_policy_shared(revision.retention_policy_id)
        if source_retention.status != 'active':
            continue
        candidates.append(use)
    if len(candidates) != 1:
        raise RecordRejected('RECORD_SOURCE_AUTHORIZATION_UNAVAILABLE')
    return policies[0], candidates[0]
