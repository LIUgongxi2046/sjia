from django.db.models import Q

from archives.models import (ArchiveBatch, ArchiveDocument, ArchiveDocumentEpisode, ArchiveDocumentPage,
    ArchiveDocumentVersion, ArchiveFact, ArchiveFactVersion, ArchivePage, PurposeCopy, PurposeCopyReview)
from authorization.models import AccessGrant
from case_details.models import ClinicalEpisode
from files.models import FileGroup, FileObject, FilePage
from medical_time.models import MedicalTime
from recognition.models import SourceAnchor
from records.models import Record, RecordField, RecordVersion
from relations.models import Relation
from sources.models import DataUseAuthorization
from verification.models import FieldVerificationState
from authorization.policy import _active_at, _scope_permits, _field_codes_permitted
from records.queries import QueryRejected


class ArchiveSearchContext:
    def __init__(self, patient, actor, versions):
        self.patient = patient
        self.grants_actor_id = actor.id
        self.tables = {}
        document_ids = {row.document_id for row in versions}
        version_ids = {row.id for row in versions}
        self.load(ArchiveDocument.objects.filter(patient_id=patient.id, id__in=document_ids))
        all_versions = self.load(ArchiveDocumentVersion.objects.filter(patient_id=patient.id,
            document_id__in=document_ids))
        self.load(ArchiveBatch.objects.filter(patient_id=patient.id,
            id__in={row.batch_id for row in all_versions}))
        page_links = self.load(ArchiveDocumentPage.objects.filter(patient_id=patient.id,
            document_version_id__in=version_ids).order_by('page_order', 'id'))
        pages = self.load(ArchivePage.objects.filter(patient_id=patient.id,
            id__in={row.page_id for row in page_links}))
        file_pages = self.load(FilePage.objects.filter(patient_id=patient.id,
            id__in={row.file_page_id for row in pages}))
        files = self.load(FileObject.objects.filter(patient_id=patient.id,
            id__in={row.file_id for row in pages}))
        groups = self.load(FileGroup.objects.filter(patient_id=patient.id,
            id__in={row.file_group_id for row in file_pages}).select_related('source_revision__source'))
        links = self.load(ArchiveDocumentEpisode.objects.filter(patient_id=patient.id,
            document_version_id__in=version_ids).order_by('episode_id'))
        episodes = self.load(ClinicalEpisode.objects.filter(patient_id=patient.id,
            id__in={row.episode_id for row in links}))
        histories = list(ArchiveFactVersion.objects.filter(patient_id=patient.id,
            document_version_id__in={row.id for row in all_versions}).order_by('id'))
        facts = self.load(ArchiveFact.objects.filter(patient_id=patient.id,
            id__in={row.fact_id for row in histories}))
        current_facts = list(ArchiveFactVersion.objects.filter(patient_id=patient.id,
            fact_id__in={row.id for row in facts}).order_by('id'))
        self.store(ArchiveFactVersion, histories + current_facts)
        source_version_ids = {row.record_version_id for row in histories + current_facts}
        source_version_ids.update(row.record_version_id for row in episodes)
        source_versions = list(RecordVersion.objects.filter(patient_id=patient.id, id__in=source_version_ids))
        records = self.load(Record.objects.filter(patient_id=patient.id,
            id__in={row.record_id for row in source_versions}))
        retained_versions = list(RecordVersion.objects.filter(patient_id=patient.id,
            record_id__in={row.id for row in records}).filter(
                Q(version=1) | Q(id__in=source_version_ids) | Q(version__in={row.current_version for row in records})))
        self.store(RecordVersion, source_versions + retained_versions)
        fields = self.load(RecordField.objects.filter(patient_id=patient.id,
            record_version_id__in={row.id for row in source_versions + retained_versions}).order_by('ordinal'))
        anchor_ids = {row.anchor_id for row in fields if row.anchor_id is not None}
        anchor_ids.update(row.evidence_anchor_id for row in links)
        anchor_ids.update(row.source_anchor_id for row in histories + current_facts if row.source_anchor_id is not None)
        self.load(SourceAnchor.objects.filter(patient_id=patient.id, id__in=anchor_ids))
        self.load(MedicalTime.objects.filter(patient_id=patient.id,
            id__in={row.documented_time_id for row in all_versions if row.documented_time_id is not None}))
        self.load(FieldVerificationState.objects.filter(patient_id=patient.id, purpose='family_observation',
            field_id__in={row.id for row in fields}))
        copies = self.load(PurposeCopy.objects.filter(patient_id=patient.id,
            original_file_id__in={row.id for row in files}).order_by('created_at', 'id'))
        self.load(PurposeCopyReview.objects.filter(patient_id=patient.id,
            copy_id__in={row.id for row in copies}).order_by('-reviewed_at', '-id'))
        self.load(DataUseAuthorization.objects.filter(patient_id=patient.id,
            source_revision_id__in={row.source_revision_id for row in groups}).order_by('purpose', 'environment', 'id'))
        grants = self.load(AccessGrant.objects.filter(patient_id=patient.id,
            principal_id=actor.id, action__in=['read_raw', 'read_file']).order_by('-valid_from'))
        relations = self.load(Relation.objects.filter(patient_id=patient.id, guardian_id=actor.id,
            id__in={row.relation_id for row in grants if row.relation_id is not None}))
        self.grants = grants
        self.relations = {row.id: row for row in relations}

    def store(self, model, rows):
        self.tables[model] = {row.id: row for row in rows}
        return list(self.tables[model].values())

    def load(self, query):
        return self.store(query.model, list(query))

    def rows(self, model, **scope):
        values = list(self.tables[model].values())
        for name, value in scope.items():
            values = [row for row in values if getattr(row, name) == value]
        return values

    def get(self, model, **scope):
        values = self.rows(model, **scope)
        if not values:
            raise model.DoesNotExist()
        if len(values) != 1:
            raise model.MultipleObjectsReturned()
        return values[0]

    def grants_for(self, actor, action, now, **scope):
        if actor.id != self.grants_actor_id:
            raise QueryRejected('ARCHIVE_SEARCH_ACTOR_INVALID')
        for grant in self.grants:
            if grant.action != action or grant.purpose != 'family_observation' or not grant.evidence_ref.strip():
                continue
            if scope.get('whole_resource') and grant.field_codes:
                continue
            if not _active_at(grant, now) or not _scope_permits(grant, scope.get('record_id'), scope.get('author_id')):
                continue
            if not _field_codes_permitted(grant, scope.get('field_codes')):
                continue
            relation = self.relations.get(grant.relation_id)
            if relation is None or relation.status != Relation.Status.ACTIVE or not _active_at(relation, now):
                continue
            yield grant

    def fact_source(self, version):
        patient_id = self.patient.id
        field = self.get(RecordField, id=version.field_id, record_version_id=version.record_version_id, patient_id=patient_id)
        record_version = self.get(RecordVersion, id=version.record_version_id, patient_id=patient_id)
        if (version.patient_id != patient_id or version.medical_time_id != field.medical_time_id
                or version.source_anchor_id != field.anchor_id or version.confirmation_id != field.confirmation_id):
            raise QueryRejected('FACT_SOURCE_INVALID')
        return field, record_version

    def record_scope(self, document, version):
        versions = self.rows(ArchiveDocumentVersion, document_id=document.id)
        document_ids = {row.id for row in versions}
        facts = self.rows(ArchiveFactVersion, document_version_id=version.id)
        source_ids = {row.record_version_id for row in facts}
        if version.version == document.current_version:
            fact_ids = {row.fact_id for row in self.rows(ArchiveFactVersion) if row.document_version_id in document_ids}
            for fact_id in fact_ids:
                fact = self.get(ArchiveFact, id=fact_id)
                current = self.get(ArchiveFactVersion, fact_id=fact.id, version=fact.current_version)
                if current.document_version_id in document_ids:
                    source_ids.add(current.record_version_id)
        for relation in self.rows(ArchiveDocumentEpisode, document_version_id=version.id):
            source_ids.add(self.get(ClinicalEpisode, id=relation.episode_id).record_version_id)
        record_ids = {self.get(RecordVersion, id=identifier).record_id for identifier in source_ids}
        return next(iter(record_ids)) if len(record_ids) == 1 else None

    def sources(self, document, version):
        patient_id = self.patient.id
        if document.patient_id != patient_id or version.document_id != document.id or version.patient_id != patient_id:
            raise QueryRejected('DOCUMENT_VERSION_INVALID')
        batch = self.get(ArchiveBatch, id=version.batch_id, patient_id=patient_id)
        links = sorted(self.rows(ArchiveDocumentPage, document_version_id=version.id), key=lambda row: (row.page_order, row.id))
        if not links or [row.page_order for row in links] != list(range(1, len(links) + 1)):
            raise QueryRejected('DOCUMENT_PAGE_ORDER_INVALID')
        pages, files, groups = [], {}, {}
        for link in links:
            page = self.get(ArchivePage, id=link.page_id, batch_id=batch.id)
            file_page = self.get(FilePage, id=page.file_page_id, file_id=page.file_id)
            source = self.get(FileObject, id=page.file_id)
            group = self.get(FileGroup, id=file_page.file_group_id)
            if (source.file_group_id != group.id or source.purpose != 'original'
                    or source.object_version != page.file_object_version or source.checksum != page.checksum):
                raise QueryRejected('DOCUMENT_SOURCE_INVALID')
            pages.append(page)
            files[source.id], groups[group.id] = source, group
        episodes = sorted(self.rows(ArchiveDocumentEpisode, document_version_id=version.id), key=lambda row: row.episode_id)
        for relation in episodes:
            episode = self.get(ClinicalEpisode, id=relation.episode_id)
            field = self.get(RecordField, id=episode.source_field_id, record_version_id=episode.record_version_id)
            anchor = self.get(SourceAnchor, id=relation.evidence_anchor_id)
            if field.anchor_id != anchor.id or not any(page.file_page_id == anchor.page_id for page in pages):
                raise QueryRejected('DOCUMENT_EPISODE_SOURCE_INVALID')
        page_by_id = {page.id: page for page in pages}
        facts = sorted(self.rows(ArchiveFactVersion, document_version_id=version.id), key=lambda row: row.id)
        for fact in facts:
            field, _ = self.fact_source(fact)
            if fact.page_id not in page_by_id or fact.source_anchor_id is None:
                raise QueryRejected('DOCUMENT_FACT_SOURCE_INVALID')
            anchor = self.get(SourceAnchor, id=fact.source_anchor_id)
            if anchor.page_id != page_by_id[fact.page_id].file_page_id or field.anchor_id != anchor.id:
                raise QueryRejected('DOCUMENT_FACT_SOURCE_INVALID')
        return batch, pages, list(files.values()), list(groups.values()), episodes, facts

    def uses(self, file, group, now):
        copies = []
        for copy in sorted(self.rows(PurposeCopy, original_file_id=file.id), key=lambda row: (row.created_at, row.id)):
            reviews = sorted(self.rows(PurposeCopyReview, copy_id=copy.id, copy_version=copy.version,
                original_checksum=file.checksum), key=lambda row: (row.reviewed_at, row.id), reverse=True)
            review = reviews[0] if reviews else None
            copies.append({'copy_id': str(copy.id), 'version': str(copy.version), 'purpose': copy.purpose, 'status': copy.status,
                'review_id': str(review.id) if review else None, 'reviewed_at': review.reviewed_at.isoformat() if review else None,
                'revoked_at': review.revoked_at.isoformat() if review and review.revoked_at else None,
                'identity_result': review.identity_result if review else None, 'clinical_result': review.clinical_result if review else None})
        authorizations = []
        if group.source_revision.status == 'approved' and group.source_revision.source.status == 'approved':
            for row in sorted(self.rows(DataUseAuthorization, source_revision_id=group.source_revision_id),
                    key=lambda row: (row.purpose, row.environment, row.id)):
                if row.approved_at > now or row.revoked_at is not None or row.valid_until is not None and row.valid_until <= now:
                    continue
                authorizations.append({'authorization_id': str(row.id), 'purpose': row.purpose, 'environment': row.environment,
                    'evidence_ref': row.evidence_ref, 'valid_until': row.valid_until.isoformat() if row.valid_until else None})
        return {'use_authorizations': authorizations, 'purpose_copies': copies}

    def scope(self, actor, document, version, now):
        initial = self.get(ArchiveDocumentVersion, document_id=document.id, version=1)
        owner = self.get(ArchiveBatch, id=initial.batch_id).submitted_by
        record_id = self.record_scope(document, version)
        grants = list(self.grants_for(actor, 'read_raw', now, record_id=record_id, author_id=owner, whole_resource=True))
        if not grants:
            return None
        batch, pages, files, groups, links, facts = self.sources(document, version)
        for group in groups:
            allowed = list(self.grants_for(actor, 'read_file', now, record_id=record_id, author_id=group.owner_id, whole_resource=True))
            if not allowed:
                return None
            grants.extend(allowed)
        episodes = []
        fields = [self.fact_source(fact) for fact in facts]
        for link in links:
            episode = self.get(ClinicalEpisode, id=link.episode_id)
            field = self.get(RecordField, id=episode.source_field_id, record_version_id=episode.record_version_id)
            source = self.get(RecordVersion, id=episode.record_version_id)
            fields.append((field, source))
            episodes.append({'episode_id': str(episode.id), 'label': episode.label, 'episode_type': episode.episode_type,
                'organization': episode.organization_text, 'record_version_id': str(episode.record_version_id)})
        for field, source in fields:
            initial = self.get(RecordVersion, record_id=source.record_id, version=1)
            allowed = list(self.grants_for(actor, 'read_raw', now, record_id=source.record_id,
                author_id=initial.author_id, field_codes=[field.field_code]))
            if not allowed:
                return None
            grants.extend(allowed)
        return {'grants': grants, 'batch': batch, 'pages': pages, 'files': files, 'episodes': episodes,
            'fields': fields, 'uses': {str(file.id): self.uses(file, next(group for group in groups
                if group.id == file.file_group_id), now) for file in files}}

    def current_fields(self, scope):
        result = []
        for field, version in scope['fields']:
            record = self.get(Record, id=version.record_id)
            if record.status != 'active':
                continue
            if version.version == record.current_version:
                result.append((field, version))
            else:
                current = self.get(RecordVersion, record_id=record.id, version=record.current_version)
                values = sorted(self.rows(RecordField, record_version_id=current.id, field_code=field.field_code),
                    key=lambda row: row.ordinal)
                result.extend((value, current) for value in values)
        return list({field.id: (field, version) for field, version in result}.values())

    def verification_status(self, fields):
        states = [row.status for row in self.rows(FieldVerificationState) if row.field_id in {field.id for field, _ in fields}]
        if not fields or len(states) != len(fields):
            return 'unverified'
        if set(states) == {'verified'}:
            return 'verified'
        for status in ('needs_correction', 'revoked', 'pending', 'unverified'):
            if status in states:
                return status
        return 'unverified'
