from django.utils import timezone

from configurations.models import ConfigVersion
from configurations.evidence import deployed_artifact_checksum, inspect_approved_evidence, inspect_validation_report
from configurations.qualification import configuration_qualification_reason
from contacts.models import ContactVersion
from contacts.approval import contact_content_reason
from family_content.models import EnvironmentGate, ReleaseBundle, ReleaseComponent


REQUIRED_COMPONENTS = frozenset({'rule', 'window', 'verification', 'mapping', 'visibility', 'content', 'notification'})


def publication_policy_reason(release, components, gate, now):
    policies = {item.component_kind: item.config for item in components}
    content, visibility, notification = (policies[kind] for kind in ('content', 'visibility', 'notification'))
    validation = release.validation_config
    if (content.schema_version != 'content-1' or visibility.schema_version != 'visibility-1'
            or notification.schema_version != 'notification-1' or validation.schema_version != 'validation-1'):
        return 'RELEASE_POLICY_SCHEMA_MISMATCH'
    purpose = content.payload['purpose']
    for config in (content, visibility, notification, validation):
        if config.payload['environment'] != gate.environment or config.payload['purpose'] != purpose:
            return 'RELEASE_POLICY_SCOPE_MISMATCH'
        for reference in config.payload['evidence_refs']:
            _, _, reason = inspect_approved_evidence(reference, purpose='clinical_policy', now=now)
            if reason:
                return reason
    for config in (content, validation):
        if (config.payload['rule_id'] != str(policies['rule'].id)
                or config.payload['window_policy_id'] != str(policies['window'].id)):
            return 'RELEASE_POLICY_BINDING_MISMATCH'
    rule = policies['rule']
    if (rule.schema_version != 'alert-trigger-1'
            or policies['verification'].payload.get('purpose') != purpose
            or rule.payload['publication']['level'] != content.payload['level']):
        return 'RELEASE_POLICY_SCOPE_MISMATCH'
    artifact = deployed_artifact_checksum(rule)
    report, _, reason = inspect_validation_report(rule, gate, {'config_checksum': rule.checksum,
        'environment': gate.environment, 'artifact_sha256': artifact,
        'dataset_revision_id': validation.payload['dataset_revision_id'],
        'evidence_ref': validation.payload['report_ref'], 'evidence_sha256': validation.payload['report_sha256']},
        now=now, expected_artifact_sha256=artifact)
    if reason:
        return reason
    if report.outcome != 'pass':
        return 'RELEASE_INDEPENDENT_VALIDATION_FAILED'
    return None


def config_qualification_reason(config, environment, now):
    return configuration_qualification_reason(config, environment, now)


def component_qualification_reason(components):
    kinds = [item.component_kind for item in components]
    if len(kinds) != len(REQUIRED_COMPONENTS) or set(kinds) != REQUIRED_COMPONENTS:
        return 'RELEASE_COMPONENTS_INCOMPLETE'
    if any(item.config.kind != item.component_kind for item in components):
        return 'RELEASE_COMPONENT_KIND_MISMATCH'
    return None


def contact_qualification_reason(contact, environment, now):
    if contact.status != ContactVersion.Status.ACTIVE or contact.valid_until <= now:
        return 'CONTACT_INVALID'
    if contact.verified_at is None or contact.verified_at > now or contact.owner.account_status != 'active':
        return 'CONTACT_VERIFICATION_UNAVAILABLE'
    if contact.source_revision.status != 'approved' or contact.source_revision.source.status != 'approved':
        return 'CONTACT_SOURCE_UNAVAILABLE'
    reason = contact_content_reason(contact, environment)
    if reason:
        return reason
    return config_qualification_reason(contact.approval_config, environment, now)


def release_qualification_reason(release, now=None, *, require_active=True):
    now = now or timezone.now()
    current = ReleaseBundle.objects.select_related('validation_config', 'contact_version__approval_config',
        'contact_version__owner', 'contact_version__source_revision__source').filter(id=release.id).first()
    if current is None:
        return 'RELEASE_NOT_FOUND'
    if current.status != ('enabled' if require_active else 'approved'):
        return 'RELEASE_NOT_ENABLED' if require_active else 'RELEASE_NOT_APPROVED'
    if current.valid_until is not None and current.valid_until <= now:
        return 'RELEASE_EXPIRED'
    gate = EnvironmentGate.objects.filter(environment=current.environment).first()
    if gate is None or not gate.enabled:
        return 'RELEASE_ENVIRONMENT_UNAVAILABLE'
    if require_active and (gate.active_release_id != current.id or gate.publication_epoch != current.publication_epoch):
        return 'RELEASE_PUBLICATION_CHANGED'
    components = list(ReleaseComponent.objects.filter(release=current).select_related('config'))
    reason = component_qualification_reason(components)
    if reason:
        return reason
    if current.validation_config.kind != ConfigVersion.Kind.VALIDATION:
        return 'RELEASE_VALIDATION_KIND_MISMATCH'
    for config in [item.config for item in components] + [current.validation_config]:
        reason = config_qualification_reason(config, current.environment, now)
        if reason:
            return reason
    reason = publication_policy_reason(current, components, gate, now)
    if reason:
        return reason
    return contact_qualification_reason(current.contact_version, current.environment, now)
