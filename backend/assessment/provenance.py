import json

from django.core.serializers.json import DjangoJSONEncoder

from archives.models import ArchiveDocument, ArchiveDocumentPage, ArchiveDocumentVersion, ArchivePage
from configurations.service import compute_checksum


def entity_reference(value):
    content = {field.attname: getattr(value, field.attname) for field in value._meta.concrete_fields}
    saved = json.loads(json.dumps(content, cls=DjangoJSONEncoder))
    return {'model': value._meta.label_lower, 'id': str(value.pk), 'sha256': compute_checksum(saved)}


def document_provenance(fact, file_page, original, *, known_at, mode):
    references = []
    if fact.page_id is None:
        if fact.document_version_id is not None:
            return references, 'FACT_DOCUMENT_PAGE_REQUIRED'
        return references, None
    page = ArchivePage.objects.select_for_update().get(id=fact.page_id, patient_id=fact.patient_id)
    if (page.file_page_id != file_page.id or page.file_id != original.id
            or page.file_object_version != original.object_version or page.checksum != original.checksum):
        return references, 'FACT_PAGE_SOURCE_CHANGED'
    references.append(entity_reference(page))
    if page.status not in ('uploaded', 'awaiting_confirmation', 'confirmed', 'archived') or page.available_at > known_at:
        return references, 'FACT_PAGE_UNAVAILABLE'
    if fact.document_version_id is None:
        return references, None
    version = ArchiveDocumentVersion.objects.select_for_update().get(id=fact.document_version_id, patient_id=fact.patient_id)
    document = ArchiveDocument.objects.select_for_update().get(id=version.document_id, patient_id=fact.patient_id)
    link = ArchiveDocumentPage.objects.select_for_update().filter(document_version_id=version.id,
        page_id=page.id, patient_id=fact.patient_id).first()
    if link is None or version.batch_id != page.batch_id:
        return references, 'FACT_DOCUMENT_PAGE_CHANGED'
    references.extend(entity_reference(value) for value in (version, link))
    if (version.grouping_status != 'confirmed' or version.confirmed_by is None or version.confirmed_at is None
            or version.available_at > known_at or version.confirmed_at > known_at):
        return references, 'FACT_DOCUMENT_CONFIRMATION_REQUIRED'
    if mode == 'current' and document.current_version != version.version:
        return references, 'FACT_DOCUMENT_SUPERSEDED'
    if mode == 'replay' and ArchiveDocumentVersion.objects.filter(document_id=document.id, patient_id=fact.patient_id,
            available_at__lte=known_at, version__gt=version.version).exists():
        return references, 'FACT_DOCUMENT_SUPERSEDED_BY_CUTOFF'
    return references, None
