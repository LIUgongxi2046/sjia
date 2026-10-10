from datetime import datetime, timezone

from django.db import transaction
from django.db.models import F

from identity.models import Patient
from verification.models import FieldVerificationState, Verification, VerificationField


class VerificationRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


@transaction.atomic
def verify_fields(*, patient, record_version_id, verifier, policy, purpose, evidence_ref, evidence_source, field_results) -> Verification:
    """field_results: [(field_id, result), ...]；result 为 consistent/needs_correction/uncertain。"""
    if not field_results:
        raise VerificationRejected("FIELDS_REQUIRED")
    patient = Patient.objects.select_for_update().get(id=patient.id)
    verification = Verification.objects.create(
        patient=patient,
        record_version_id=record_version_id,
        verifier=verifier,
        policy=policy,
        purpose=purpose,
        evidence_ref=evidence_ref,
        evidence_source=evidence_source,
        verified_at=datetime.now(timezone.utc),
    )
    for field_id, result in field_results:
        VerificationField.objects.create(
            verification_id=verification.id,
            record_version_id=record_version_id,
            patient_id=patient.id,
            field_id=field_id,
            result=result,
        )
        status = (
            FieldVerificationState.Status.VERIFIED
            if result == VerificationField.Result.CONSISTENT
            else FieldVerificationState.Status.NEEDS_CORRECTION
        )
        existing = FieldVerificationState.objects.filter(field_id=field_id, purpose=purpose).first()
        if existing is None:
            FieldVerificationState.objects.create(
                field_id=field_id,
                purpose=purpose,
                record_version_id=record_version_id,
                patient_id=patient.id,
                current_verification_id=verification.id,
                status=status,
                version=1,
            )
        else:
            FieldVerificationState.objects.filter(field_id=field_id, purpose=purpose).update(
                record_version_id=record_version_id,
                patient_id=patient.id,
                current_verification_id=verification.id,
                status=status,
                version=F("version") + 1,
            )
    Patient.objects.filter(id=patient.id).update(verification_epoch=F("verification_epoch") + 1)
    return verification


@transaction.atomic
def revoke_verification(*, verification, revoked_by, reason) -> Verification:
    if verification.revoked_at is not None:
        raise VerificationRejected("ALREADY_REVOKED")
    if not reason:
        raise VerificationRejected("REASON_REQUIRED")
    now = datetime.now(timezone.utc)
    verification.revoked_at = now
    verification.revoked_by = revoked_by
    verification.revocation_reason = reason
    verification.save(update_fields=["revoked_at", "revoked_by", "revocation_reason"])
    FieldVerificationState.objects.filter(current_verification_id=verification.id).update(
        status=FieldVerificationState.Status.REVOKED,
        current_verification_id=None,
        version=F("version") + 1,
    )
    Patient.objects.filter(id=verification.patient_id).update(verification_epoch=F("verification_epoch") + 1)
    return verification


def is_field_verified(*, field_id, record_version_id, patient, purpose) -> bool:
    return FieldVerificationState.objects.filter(
        field_id=field_id,
        record_version_id=record_version_id,
        patient_id=patient.id,
        purpose=purpose,
        status=FieldVerificationState.Status.VERIFIED,
    ).exists()
