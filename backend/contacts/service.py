from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from configurations.models import ConfigApproval, ConfigValidation, ConfigVersion
from contacts.approval import ContactApprovalPayload, contact_content_reason
from contacts.models import ContactVersion
from family_content.models import EnvironmentGate
from family_content.qualification import config_qualification_reason
from identity.models import Principal
from sources.models import SourceRegistry, SourceRevision


class ContactRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


@transaction.atomic
def save_contact(*, owner, gate, payload, approval_config):
    EnvironmentGate.objects.select_for_update().get(environment=gate.environment)
    owner = Principal.objects.select_for_update().get(id=owner.id)
    if owner.account_status != 'active':
        raise ContactRejected('CONTACT_OWNER_UNAVAILABLE')
    data = ContactApprovalPayload.model_validate({**payload, 'owner_id': str(owner.id), 'environment': gate.environment})
    if data.valid_until <= timezone.now():
        raise ContactRejected('CONTACT_EXPIRED')
    if ContactVersion.objects.filter(contact_id=data.contact_id).exclude(owner=owner).exists():
        raise ContactRejected('CONTACT_OWNER_MISMATCH')
    latest = ContactVersion.objects.filter(contact_id=data.contact_id).aggregate(version=Max('version'))['version']
    contact = ContactVersion(contact_id=data.contact_id, version=(latest or 0) + 1, contact_name=data.contact_name,
        purpose=data.purpose, channel=data.channel, channel_value=data.channel_value, owner=owner,
        source_revision=SourceRevision.objects.get(id=data.source_revision_id),
        approval_config=ConfigVersion.objects.get(id=approval_config.id), verified_at=None,
        updated_at=timezone.now(), valid_until=data.valid_until, status='draft')
    reason = contact_content_reason(contact, gate.environment)
    if reason:
        raise ContactRejected(reason)
    contact.save(force_insert=True)
    return contact


def lock_contact(contact, gate):
    EnvironmentGate.objects.select_for_update().get(environment=gate.environment)
    gate.refresh_from_db()
    ContactVersion.objects.select_for_update().get(id=contact.id)
    contact.refresh_from_db()
    config = ConfigVersion.objects.select_for_update().get(id=contact.approval_config_id)
    list(ConfigApproval.objects.select_for_update().filter(config=config).order_by('id'))
    list(ConfigValidation.objects.select_for_update().filter(config=config).order_by('id'))
    revision = SourceRevision.objects.select_for_update().get(id=contact.source_revision_id)
    SourceRegistry.objects.select_for_update().get(id=revision.source_id)


def advance_contact_gate(gate):
    gate.version += 1
    gate.publication_epoch += 1
    gate.updated_at = timezone.now()
    gate.save(update_fields=['version', 'publication_epoch', 'updated_at'])


@transaction.atomic
def activate_saved_contact(*, contact, gate, expected_gate_version):
    lock_contact(contact, gate)
    now = timezone.now()
    if gate.version != expected_gate_version:
        raise ContactRejected('GATE_VERSION_MISMATCH')
    if not gate.enabled:
        raise ContactRejected('CONTACT_ENVIRONMENT_UNAVAILABLE')
    if contact.status not in ('draft', 'approved'):
        raise ContactRejected('CONTACT_NOT_ACTIVATABLE')
    if contact.owner.account_status != 'active' or contact.valid_until <= now:
        raise ContactRejected('CONTACT_INVALID')
    if contact.source_revision.status != 'approved' or contact.source_revision.source.status != 'approved':
        raise ContactRejected('CONTACT_SOURCE_UNAVAILABLE')
    reason = contact_content_reason(contact, gate.environment) or config_qualification_reason(contact.approval_config, gate.environment, now)
    if reason:
        raise ContactRejected(reason)
    contact.status = 'active'
    contact.verified_at = now
    contact.updated_at = now
    contact.save(update_fields=['status', 'verified_at', 'updated_at'])
    advance_contact_gate(gate)
    return contact


@transaction.atomic
def disable_saved_contact(*, contact, gate, expected_state, expected_version=None):
    lock_contact(contact, gate)
    if contact.status != expected_state or (expected_version is not None and str(contact.version) != expected_version):
        raise ContactRejected('VERSION_CONFLICT')
    if contact.status == 'disabled':
        raise ContactRejected('CONTACT_ALREADY_DISABLED')
    contact.status = 'disabled'
    contact.updated_at = timezone.now()
    contact.save(update_fields=['status', 'updated_at'])
    advance_contact_gate(gate)
    return contact
