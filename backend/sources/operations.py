from django.db import transaction
from django.utils import timezone
from pydantic import Field
from typing import Annotated, Literal

from assessment.policies import PolicyModel
from files.purpose_schemas import PurposeLocalDispositionRule
from configurations.evidence import read_approved_evidence
from configurations.schemas import validate_operation_input
from configurations.service import ConfigRejected
from retention.models import RetentionPolicy
from sources.models import SourceRegistry, SourceRevision
from sources.service import approve_revision, register_revision, register_source


SOURCE_OPERATIONS = {
    'source_register': ('manage_config', 'SourceInput', 'source_registry'),
    'source_revision_register': ('manage_config', 'SourceRevisionInput', 'source_revision'),
    'source_revision_approve': ('manage_config', 'StateAction', 'source_revision'),
    'source_revision_revoke': ('manage_config', 'StateAction', 'source_revision'),
    'retention_policy_create': ('manage_config', 'RetentionInput', 'retention_policy'),
    'retention_policy_activate': ('manage_config', 'StateAction', 'retention_policy'),
}


class RetentionRules(PolicyModel):
    retention_days: Annotated[int, Field(ge=0)] | None
    disposition: Literal['delete', 'retain']
    local_delete: PurposeLocalDispositionRule | None = None


def source_operation_ready(actor, gate, operation, keys, payload):
    validate_operation_input(SOURCE_OPERATIONS[operation][1], payload)
    if operation == 'source_register':
        read_approved_evidence(payload['evidence_ref'], purpose='configuration_approval')
        return not SourceRegistry.objects.filter(source_key=payload['source_key']).exists()
    if operation == 'source_revision_register':
        source = SourceRegistry.objects.select_for_update().get(id=keys['source_id'])
        policy = RetentionPolicy.objects.select_for_update().get(id=payload['retention_policy_id'])
        return (source.status != 'disabled' and policy.status == 'active'
            and not SourceRevision.objects.filter(source=source, version=payload['version']).exists())
    if operation == 'retention_policy_create':
        if payload['schema_version'] != 'retention-1':
            raise ConfigRejected('CONFIG_SCHEMA_UNAVAILABLE')
        RetentionRules.model_validate(payload['rules'])
        read_approved_evidence(payload['evidence_ref'], purpose='configuration_approval')
        return True
    if operation.startswith('source_revision_'):
        resource = SourceRevision.objects.select_for_update().get(id=keys['source_revision_id'])
        expected = 'pending' if operation == 'source_revision_approve' else 'approved'
        if operation == 'source_revision_approve':
            read_approved_evidence(resource.rights_evidence_ref, purpose='configuration_approval')
            if resource.retention_policy_id is None or resource.retention_policy.status != 'active':
                return False
    else:
        resource = RetentionPolicy.objects.select_for_update().get(id=keys['policy_id'])
        expected = 'draft'
        read_approved_evidence(resource.evidence_ref, purpose='configuration_approval')
        RetentionRules.model_validate(resource.rules)
    return (resource.status == expected and payload['expected_state'] == expected
        and ('expected_version' not in payload or payload['expected_version'] == str(resource.version)))


@transaction.atomic
def perform_source_operation(actor, gate, operation, keys, payload):
    if not source_operation_ready(actor, gate, operation, keys, payload):
        raise ConfigRejected('CONFIG_TARGET_CHANGED')
    if operation == 'source_register':
        return register_source(**{key: value for key, value in payload.items() if key != 'evidence_ref'}), 1
    if operation == 'source_revision_register':
        source = SourceRegistry.objects.get(id=keys['source_id'])
        return register_revision(source=source, retention_policy=RetentionPolicy.objects.get(id=payload['retention_policy_id']),
            **{key: value for key, value in payload.items() if key != 'retention_policy_id'}), 1
    if operation == 'retention_policy_create':
        revisions = RetentionPolicy.objects.select_for_update().filter(policy_key=payload['policy_key'])
        version = (revisions.order_by('-version').values_list('version', flat=True).first() or 0) + 1
        policy = RetentionPolicy.objects.create(**payload, version=version, status='draft')
        return policy, version
    if operation.startswith('source_revision_'):
        revision = SourceRevision.objects.select_for_update().get(id=keys['source_revision_id'])
        if operation == 'source_revision_approve':
            approve_revision(revision=revision, evidence_ref=revision.rights_evidence_ref)
        else:
            from files.purpose_dependencies import invalidate_resource

            revision.status = 'revoked'
            revision.save(update_fields=['status'])
            invalidate_resource('source_revision', revision.id, revision.version)
        return revision, 1
    policy = RetentionPolicy.objects.select_for_update().get(id=keys['policy_id'])
    policy.status, policy.approved_by, policy.approved_at = 'active', actor, timezone.now()
    policy.save(update_fields=['status', 'approved_by', 'approved_at'])
    return policy, policy.version


def source_receipt_exists(saved, operation, keys):
    models = {'source_registry': SourceRegistry, 'source_revision': SourceRevision, 'retention_policy': RetentionPolicy}
    if saved.resource_type not in models or not models[saved.resource_type].objects.filter(id=saved.resource_id).exists():
        return False
    if operation == 'source_revision_register':
        return SourceRevision.objects.filter(id=saved.resource_id, source_id=keys['source_id']).exists()
    return not keys or saved.resource_id in keys.values()
