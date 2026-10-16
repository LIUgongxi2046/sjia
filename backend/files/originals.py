from django.utils import timezone

from archives.models import ArchiveDocumentPage, ArchiveDocumentVersion, ArchiveFactVersion, ArchivePage, PurposeCopy, PurposeCopyReview
from files.models import FileGroup, FileObject, FilePage
from recognition.models import SourceAnchor
from records.models import RecordAttachment, RecordField
from records.queries import QueryRejected
from records.service import medical_time_view
from sources.models import DataUseAuthorization


def original_use_view(file, group, patient):
    copies = []
    for copy in PurposeCopy.objects.filter(patient_id=patient.id, original_file_id=file.id).order_by('created_at', 'id'):
        review = PurposeCopyReview.objects.filter(patient_id=patient.id, copy_id=copy.id,
            copy_version=copy.version, original_checksum=file.checksum).order_by('-reviewed_at', '-id').first()
        copies.append({'copy_id': str(copy.id), 'version': str(copy.version), 'purpose': copy.purpose, 'status': copy.status,
            'review_id': str(review.id) if review else None, 'reviewed_at': review.reviewed_at.isoformat() if review else None,
            'revoked_at': review.revoked_at.isoformat() if review and review.revoked_at else None,
            'identity_result': review.identity_result if review else None, 'clinical_result': review.clinical_result if review else None})
    now = timezone.now()
    authorizations = []
    if group.source_revision.status == 'approved' and group.source_revision.source.status == 'approved':
        for authorization in DataUseAuthorization.objects.filter(patient=patient, source_revision=group.source_revision,
            approved_at__lte=now, revoked_at__isnull=True).exclude(valid_until__lte=now).order_by('purpose', 'environment', 'id'):
            authorizations.append({'authorization_id': str(authorization.id), 'purpose': authorization.purpose,
                'environment': authorization.environment, 'evidence_ref': authorization.evidence_ref,
                'valid_until': authorization.valid_until.isoformat() if authorization.valid_until else None})
    return {'use_authorizations': authorizations, 'purpose_copies': copies}


def original_source_view(version, field_codes):
    from retention.content_fences import assert_content_available
    assert_content_available('records.recordversion', version.id)
    patient = version.patient
    fields = list(RecordField.objects.filter(patient=patient, record_version_id=version.id,
        field_code__in=field_codes).order_by('ordinal'))
    anchors = {anchor.id: anchor for anchor in SourceAnchor.objects.filter(patient=patient,
        id__in=[field.anchor_id for field in fields if field.anchor_id])}
    facts = list(ArchiveFactVersion.objects.filter(patient_id=patient.id, record_version_id=version.id,
        field_id__in=[field.id for field in fields]))
    originals = []
    for attachment in RecordAttachment.objects.filter(patient_id=patient.id, record_version_id=version.id).order_by('ordinal'):
        file = FileObject.objects.get(id=attachment.file_id, patient=patient)
        if file.purpose != 'original':
            raise QueryRejected('ORIGINAL_SOURCE_INVALID')
        group = FileGroup.objects.select_related('source_revision__source').get(id=file.file_group_id, patient=patient)
        pages = list(FilePage.objects.filter(patient=patient, file_id=file.id, file_group_id=group.id).order_by('ordinal'))
        page_ids = {page.id for page in pages}
        documents = {}
        for fact in facts:
            if fact.document_version_id is None:
                continue
            anchor = anchors.get(fact.source_anchor_id)
            if anchor is None or anchor.page_id not in page_ids:
                continue
            if fact.page_id is None or not ArchiveDocumentPage.objects.filter(patient_id=patient.id,
                document_version_id=fact.document_version_id, page_id=fact.page_id).exists():
                raise QueryRejected('ORIGINAL_DOCUMENT_SOURCE_INVALID')
            archive_page = ArchivePage.objects.get(id=fact.page_id, patient_id=patient.id)
            if (archive_page.file_page_id != anchor.page_id or archive_page.file_id != file.id
                or archive_page.file_object_version != file.object_version or archive_page.checksum != file.checksum):
                raise QueryRejected('ORIGINAL_DOCUMENT_SOURCE_INVALID')
            document = ArchiveDocumentVersion.objects.get(id=fact.document_version_id, patient_id=patient.id)
            documents[str(document.id)] = {'document_id': str(document.document_id),
                'document_version_id': str(document.id), 'document_type': document.document_type,
                'source_organization': document.organization,
                'documented_time': medical_time_view(document.documented_time_id, patient)}
        use = original_use_view(file, group, patient)
        originals.append({'file_id': str(file.id), 'object_version': file.object_version, 'checksum': file.checksum,
            'status': file.status, 'media_type': file.media_type, 'ordinal': attachment.ordinal,
            'created_at': file.created_at.isoformat(), 'pages': [{'page_id': str(page.id), 'ordinal': page.ordinal} for page in pages],
            'documents': list(documents.values()), **use})
    return {'patient_id': str(patient.id), 'record_id': str(version.record_id), 'record_version_id': str(version.id),
        'record_version': version.version, 'source_organization': version.source_organization, 'originals': originals}
