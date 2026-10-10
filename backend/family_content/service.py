from datetime import datetime, timezone

from django.conf import settings
from django.db import transaction
from django.db.models import Max

from configurations.models import ConfigApproval, ConfigValidation, ConfigVersion
from contacts.models import ContactVersion
from contacts.service import activate_saved_contact, save_contact
from family_content.models import EnvironmentGate, ReleaseBundle, ReleaseComponent
from family_content.gate_locks import read_environment_gate_shared
from family_content.qualification import REQUIRED_COMPONENTS, component_qualification_reason, release_qualification_reason


class ReleaseRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


@transaction.atomic
def create_contact_version(*, contact_id, contact_name, purpose, channel, channel_value, owner, source_revision, approval_config, valid_until) -> ContactVersion:
    gate = EnvironmentGate.objects.get(environment=settings.SJIA_ENVIRONMENT)
    return save_contact(owner=owner, gate=gate, approval_config=approval_config, payload={
        'contact_id': str(contact_id), 'contact_name': contact_name, 'purpose': purpose,
        'channel': channel, 'channel_value': channel_value, 'source_revision_id': str(source_revision.id),
        'valid_until': valid_until.isoformat()})


@transaction.atomic
def activate_contact(*, contact, expected_gate_version) -> ContactVersion:
    gate = EnvironmentGate.objects.get(environment=settings.SJIA_ENVIRONMENT)
    return activate_saved_contact(contact=contact, gate=gate, expected_gate_version=expected_gate_version)


@transaction.atomic
def create_release_bundle(*, environment, contact_version, validation_config, valid_until=None) -> ReleaseBundle:
    EnvironmentGate.objects.select_for_update().get(environment=environment)
    if validation_config.kind != ConfigVersion.Kind.VALIDATION:
        raise ReleaseRejected('RELEASE_VALIDATION_KIND_MISMATCH')
    if valid_until is not None and valid_until <= datetime.now(timezone.utc):
        raise ReleaseRejected('RELEASE_EXPIRED')
    latest = ReleaseBundle.objects.filter(environment=environment).aggregate(version=Max('version'))['version']
    return ReleaseBundle.objects.create(
        environment=environment,
        version=(latest or 0) + 1,
        publication_epoch=0,
        status=ReleaseBundle.Status.DRAFT,
        contact_version=contact_version,
        validation_config=validation_config,
        valid_until=valid_until,
    )


@transaction.atomic
def add_release_component(*, release, component_kind, config) -> ReleaseComponent:
    release = ReleaseBundle.objects.select_for_update().get(id=release.id)
    config = ConfigVersion.objects.get(id=config.id)
    if release.status != ReleaseBundle.Status.DRAFT:
        raise ReleaseRejected('RELEASE_NOT_DRAFT')
    if component_kind not in REQUIRED_COMPONENTS or config.kind != component_kind:
        raise ReleaseRejected('RELEASE_COMPONENT_KIND_MISMATCH')
    if ReleaseComponent.objects.filter(release=release, component_kind=component_kind).exists():
        raise ReleaseRejected('RELEASE_COMPONENT_DUPLICATE')
    return ReleaseComponent.objects.create(release=release, component_kind=component_kind, config=config)


@transaction.atomic
def approve_release(*, release) -> ReleaseBundle:
    ReleaseBundle.objects.select_for_update().get(id=release.id)
    release.refresh_from_db()
    if release.status != ReleaseBundle.Status.DRAFT:
        raise ReleaseRejected("RELEASE_NOT_DRAFT")
    components = list(ReleaseComponent.objects.filter(release=release).select_related('config'))
    reason = component_qualification_reason(components)
    if reason:
        raise ReleaseRejected(reason)
    release.status = ReleaseBundle.Status.APPROVED
    release.save(update_fields=["status"])
    return release


def lock_release_dependencies(release, environment_gate, *, read_only=False):
    if read_only:
        read_environment_gate_shared(environment_gate.environment)
    else:
        EnvironmentGate.objects.select_for_update().get(environment=environment_gate.environment)
    environment_gate.refresh_from_db()
    ReleaseBundle.objects.select_for_update().get(id=release.id)
    release.refresh_from_db()
    if release.environment != environment_gate.environment:
        raise ReleaseRejected('RELEASE_ENVIRONMENT_MISMATCH')
    contact = ContactVersion.objects.select_for_update().get(id=release.contact_version_id)
    config_ids = set(ReleaseComponent.objects.filter(release=release).values_list('config_id', flat=True))
    config_ids.update((release.validation_config_id, contact.approval_config_id))
    list(ConfigVersion.objects.select_for_update().filter(id__in=config_ids).order_by('id'))
    list(ConfigApproval.objects.select_for_update().filter(config_id__in=config_ids).order_by('id'))
    list(ConfigValidation.objects.select_for_update().filter(config_id__in=config_ids).order_by('id'))


@transaction.atomic
def enable_release(*, release, environment_gate, expected_gate_version) -> ReleaseBundle:
    lock_release_dependencies(release, environment_gate)
    now = datetime.now(timezone.utc)
    if release.status != ReleaseBundle.Status.APPROVED:
        raise ReleaseRejected("RELEASE_NOT_APPROVED")
    if environment_gate.version != expected_gate_version:
        raise ReleaseRejected("GATE_VERSION_MISMATCH")
    reason = release_qualification_reason(release, now, require_active=False)
    if reason:
        raise ReleaseRejected(reason)
    if environment_gate.active_release_id is not None or ReleaseBundle.objects.filter(
            environment=release.environment, status=ReleaseBundle.Status.ENABLED).exists():
        raise ReleaseRejected('RELEASE_ALREADY_ACTIVE')
    release.status = ReleaseBundle.Status.ENABLED
    release.activated_at = now
    release.publication_epoch = environment_gate.publication_epoch + 1
    release.save(update_fields=["status", "activated_at", "publication_epoch"])
    environment_gate.active_release = release
    environment_gate.publication_epoch = environment_gate.publication_epoch + 1
    environment_gate.version = environment_gate.version + 1
    environment_gate.updated_at = now
    environment_gate.save(update_fields=["active_release", "publication_epoch", "version", "updated_at"])
    return release


@transaction.atomic
def disable_release(*, release, environment_gate, expected_gate_version=None) -> ReleaseBundle:
    EnvironmentGate.objects.select_for_update().get(environment=environment_gate.environment)
    environment_gate.refresh_from_db()
    ReleaseBundle.objects.select_for_update().get(id=release.id)
    release.refresh_from_db()
    now = datetime.now(timezone.utc)
    if release.environment != environment_gate.environment:
        raise ReleaseRejected('RELEASE_ENVIRONMENT_MISMATCH')
    if expected_gate_version is not None and environment_gate.version != expected_gate_version:
        raise ReleaseRejected('GATE_VERSION_MISMATCH')
    if release.status != ReleaseBundle.Status.ENABLED:
        raise ReleaseRejected("RELEASE_NOT_ENABLED")
    if environment_gate.active_release_id != release.id:
        raise ReleaseRejected('RELEASE_PUBLICATION_CHANGED')
    release.status = ReleaseBundle.Status.DISABLED
    release.disabled_at = now
    release.save(update_fields=["status", "disabled_at"])
    environment_gate.active_release = None
    environment_gate.publication_epoch = environment_gate.publication_epoch + 1
    environment_gate.version = environment_gate.version + 1
    environment_gate.updated_at = now
    environment_gate.save(update_fields=["active_release", "publication_epoch", "version", "updated_at"])
    return release


def release_available(release, now=None) -> bool:
    return release_qualification_reason(release, now) is None
