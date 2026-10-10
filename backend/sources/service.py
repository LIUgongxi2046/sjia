from datetime import datetime, timezone
from django.db import transaction

from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision


class SourceRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


def register_source(
    *,
    source_key,
    owner,
    location,
    purpose,
    sensitivity,
    update_frequency,
) -> SourceRegistry:
    return SourceRegistry.objects.create(
        source_key=source_key,
        owner=owner,
        location=location,
        purpose=purpose,
        sensitivity=sensitivity,
        update_frequency=update_frequency,
        status=SourceRegistry.Status.PENDING,
    )


def register_revision(
    *,
    source,
    version,
    checksum,
    license,
    allowed_use,
    retention_policy=None,
    rights_evidence_ref=None,
) -> SourceRevision:
    return SourceRevision.objects.create(
        source=source,
        version=version,
        checksum=checksum,
        license=license,
        allowed_use=allowed_use,
        rights_evidence_ref=rights_evidence_ref,
        retention_policy=retention_policy,
        captured_at=datetime.now(timezone.utc),
        status=SourceRevision.Status.PENDING,
    )


@transaction.atomic
def approve_revision(*, revision, evidence_ref) -> SourceRevision:
    if not evidence_ref:
        raise SourceRejected("EVIDENCE_REQUIRED")
    from configurations.evidence import read_approved_evidence

    read_approved_evidence(evidence_ref, purpose='configuration_approval')
    revision = SourceRevision.objects.select_for_update().get(id=revision.id)
    source = SourceRegistry.objects.select_for_update().get(id=revision.source_id)
    if (revision.status != 'pending' or source.status == 'disabled' or not revision.license.strip()
            or not revision.allowed_use or revision.retention_policy_id is None or revision.retention_policy.status != 'active'):
        raise SourceRejected('SOURCE_APPROVAL_NOT_AVAILABLE')
    source.status = 'approved'
    source.save(update_fields=['status'])
    revision.rights_evidence_ref = evidence_ref
    revision.status = SourceRevision.Status.APPROVED
    revision.save(update_fields=['status', 'rights_evidence_ref'])
    return revision


def create_use_authorization(
    *,
    patient,
    source_revision,
    purpose,
    environment,
    evidence_ref,
    approved_by,
    deidentification_ref=None,
    valid_until=None,
) -> DataUseAuthorization:
    return DataUseAuthorization.objects.create(
        patient=patient,
        source_revision=source_revision,
        purpose=purpose,
        environment=environment,
        evidence_ref=evidence_ref,
        deidentification_ref=deidentification_ref,
        approved_by=approved_by,
        approved_at=datetime.now(timezone.utc),
        valid_until=valid_until,
    )
