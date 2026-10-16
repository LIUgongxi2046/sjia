from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, get_patient, handle
from api.views_archive_batches import batch_reader, read_snapshot
from archives.batches import batch_grants, batch_runtime
from archives.fact_candidates import validate_value
from archives.models import ArchiveBatch, ArchivePage
from authorization.policy import matching_case_grants
from case_details.models import ClinicalEpisode
from recognition.models import SourceAnchor
from records.models import Record, RecordField, RecordVersion
from records.service import medical_time_view


@api_view(['GET'])
@handle
@transaction.atomic
def archive_document_inputs(request, batch_id):
    read_snapshot()
    actor, _ = batch_reader(request)
    if request.query_params:
        raise ApiError('DOCUMENT_QUERY_INVALID', 422)
    batch = ArchiveBatch.objects.get(id=batch_id)
    patient = get_patient(batch.patient_id)
    pages = list(ArchivePage.objects.filter(batch_id=batch.id, patient_id=patient.id).order_by('ordinal'))
    if not batch_grants(actor, patient, batch, pages):
        raise ApiError('CASE_ACTION_DENIED', 403)
    batch_runtime()
    anchors = SourceAnchor.objects.filter(patient_id=patient.id, page_id__in=[page.file_page_id for page in pages])
    source_ids = RecordField.objects.filter(patient=patient, anchor_id__in=anchors.values('id')).values('id')
    items = []
    for episode in ClinicalEpisode.objects.filter(patient_id=patient.id, source_field_id__in=source_ids).order_by('id'):
        field = RecordField.objects.get(id=episode.source_field_id, record_version_id=episode.record_version_id, patient=patient)
        version = RecordVersion.objects.get(id=episode.record_version_id, patient=patient)
        record = Record.objects.get(id=version.record_id, patient=patient)
        initial = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=record.id, patient=patient, version=1)
        grants = matching_case_grants(actor, patient, 'read_raw', timezone.now(), field_codes=[field.field_code],
            record_id=record.id, author_id=initial.author_id, require_relation=True)
        if record.status != 'active' or not any(grant.purpose == 'family_observation' and grant.evidence_ref.strip() for grant in grants):
            continue
        anchor = SourceAnchor.objects.get(id=field.anchor_id, patient_id=patient.id)
        source_page = next(page for page in pages if page.file_page_id == anchor.page_id)
        items.append({'episode_id': str(episode.id), 'source_page_id': str(source_page.id),
            'source_field_id': str(field.id), 'record_version_id': str(version.id),
            'episode_type': episode.episode_type, 'label': episode.label,
            'start_time': medical_time_view(episode.start_time_id, patient),
            'end_time': medical_time_view(episode.end_time_id, patient)})
    return Response(validate_value('DocumentInputsView', {'patient_id': str(patient.id),
        'batch_id': str(batch.id), 'episodes': items}))
