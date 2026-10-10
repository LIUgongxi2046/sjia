from django.conf import settings
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID

from archives.batches import batch_runtime
from files.errors import FileRejected
from files.source_pages import MEDIA_TYPES, PARSER_VERSION
from sources.models import DataUseAuthorization


class UploadGroupInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    authorization_id: UUID | None = Field(default=None, strict=False)
    client_group_id: UUID | None = Field(default=None, strict=False)


def upload_options(patient):
    runtime = batch_runtime()
    rows = []
    now = timezone.now()
    for auth in DataUseAuthorization.objects.select_related('source_revision__source', 'approved_by').filter(
            patient=patient, purpose='family_observation', environment=settings.SJIA_ENVIRONMENT,
            revoked_at__isnull=True, approved_at__lte=now).exclude(valid_until__lte=now).order_by('approved_at', 'id'):
        revision = auth.source_revision
        if (auth.evidence_ref.strip() and auth.approved_by.account_status == 'active'
                and revision.status == 'approved' and revision.source.status == 'approved'
                and revision.rights_evidence_ref and 'family_observation' in revision.allowed_use):
            rows.append({'authorization_id': str(auth.id), 'source_revision_id': str(revision.id),
                'evidence_ref': auth.evidence_ref, 'source_label': revision.source.source_key,
                'approved_at': auth.approved_at.isoformat(),
                'valid_until': auth.valid_until.isoformat() if auth.valid_until is not None else None})
    limits = runtime.payload['limits']
    types = [value for value in limits['media_types'] if value in MEDIA_TYPES]
    return {'patient_id': str(patient.id), 'runtime_config_id': str(runtime.id), 'runtime_checksum': runtime.checksum,
        'max_upload_bytes': limits['max_upload_bytes'], 'max_pixels': limits['max_pixels'],
        'max_pages': limits['max_pages'], 'media_types': types, 'parser_version': PARSER_VERSION,
        'authorizations': rows}


def select_upload_authorization(patient, payload):
    options = upload_options(patient)
    rows = options['authorizations']
    if payload.authorization_id is None:
        if len(rows) != 1:
            raise FileRejected('UPLOAD_SOURCE_SELECTION_REQUIRED')
        selected = rows[0]
    else:
        selected = next((row for row in rows if row['authorization_id'] == str(payload.authorization_id)), None)
        if selected is None:
            raise FileRejected('UPLOAD_SOURCE_AUTHORIZATION_DENIED')
    return DataUseAuthorization.objects.select_for_update().get(id=selected['authorization_id'])
