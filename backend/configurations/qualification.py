from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from configurations.evidence import deployed_artifact_checksum, inspect_approved_evidence, inspect_validation_report
from configurations.models import ConfigApproval, ConfigValidation, ConfigVersion
from configurations.schemas import payload_reason
from configurations.service import compute_checksum
from family_content.models import EnvironmentGate


def configuration_qualification_reason(config, environment, now=None, *, audience='family', allow_historical=False,
        approval_id=None, validation_id=None, artifact_sha256=None):
    now = now or timezone.now()
    if environment != settings.SJIA_ENVIRONMENT:
        return 'CONFIG_ENVIRONMENT_MISMATCH'
    gate = EnvironmentGate.objects.filter(environment=environment).first()
    if gate is None or not gate.enabled:
        return 'CONFIG_ENVIRONMENT_UNAVAILABLE'
    current = ConfigVersion.objects.select_related('source_revision__source', 'source_revision__retention_policy').filter(id=config.id).first()
    if current is None:
        return 'CONFIG_NOT_AVAILABLE'
    states = ('enabled', 'disabled') if allow_historical else ('enabled',)
    if current.status not in states:
        return 'CONFIG_NOT_ENABLED'
    if current.checksum != config.checksum or compute_checksum(current.payload) != current.checksum:
        return 'CONFIG_CHECKSUM_MISMATCH'
    reason = payload_reason(current.kind, current.schema_version, current.payload)
    if reason:
        return reason
    if 'environment' in current.payload and current.payload['environment'] != environment:
        return 'CONFIG_ENVIRONMENT_MISMATCH'
    if current.source_revision_id is not None:
        revision = current.source_revision
        if (revision.status != 'approved' or revision.source.status != 'approved'
                or revision.captured_at > now or not revision.rights_evidence_ref or not revision.rights_evidence_ref.strip()
                or revision.retention_policy_id is None or revision.retention_policy.status != 'active'):
            return 'CONFIG_SOURCE_UNAVAILABLE'
    approvals = ConfigApproval.objects.select_related('approved_by').filter(config=current,
        revoked_at__isnull=True, approved_at__lte=now, effective_scope__environment=environment,
        effective_scope__config_checksum=current.checksum).filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now))
    if audience is not None:
        approvals = approvals.filter(effective_scope__audience__contains=[audience])
    if approval_id is not None:
        approvals = approvals.filter(id=approval_id)
    approval = approvals.order_by('-approved_at', '-id').first()
    if (approval is None or approval.approved_by.account_status != 'active'
            or not approval.approval_role.strip()):
        return 'CONFIG_APPROVAL_UNAVAILABLE'
    _, _, reason = inspect_approved_evidence(approval.evidence_ref, purpose='configuration_approval', now=now)
    if reason:
        return reason
    artifact = deployed_artifact_checksum(current) if artifact_sha256 is None else artifact_sha256
    validations = ConfigValidation.objects.filter(config=current, environment=gate, config_checksum=current.checksum,
        outcome='pass', revoked_at__isnull=True, executed_at__lte=now,
        artifact_sha256=artifact)
    if validation_id is not None:
        validations = validations.filter(id=validation_id)
    validation = validations.order_by('-executed_at', '-id').first()
    if validation is None:
        return 'CONFIG_VALIDATION_UNAVAILABLE'
    report, _, reason = inspect_validation_report(current, gate, {'config_checksum': current.checksum,
        'environment': environment, 'artifact_sha256': validation.artifact_sha256,
        'dataset_revision_id': str(validation.dataset_revision_id), 'evidence_ref': validation.evidence_ref,
        'evidence_sha256': validation.evidence_sha256}, now=now, expected_artifact_sha256=artifact)
    if reason:
        return reason
    if (report.outcome != validation.outcome or report.executed_by != validation.executed_by_id
            or report.executed_at != validation.executed_at):
        return 'CONFIG_VALIDATION_REPORT_MISMATCH'
    return None
