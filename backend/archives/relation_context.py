from collections import defaultdict

from django.db.models import Q

from archives.models import ArchiveDocumentPage, ArchiveDocumentVersion, ArchiveFact, ArchiveFactVersion, ArchivePage
from archives.queries import checked_fact_source
from files.models import FileGroup, FileObject, FilePage
from recognition.models import SourceAnchor
from records.models import RecordField, RecordVersion, Report
from verification.models import FieldVerificationState, Verification, VerificationField


class ArchiveRelationReadContext:
    def __init__(self, patient, facts, files, relations):
        self.patient = patient
        self.tables = {}
        self.store(ArchiveFact, facts)
        related_fact_ids = {identity for row in relations
            for identity in (row.left_fact_version_id, row.right_fact_version_id) if identity is not None}
        versions = self.load(ArchiveFactVersion.objects.filter(patient_id=patient.id).filter(
            Q(fact_id__in=[row.id for row in facts]) | Q(id__in=related_fact_ids)).order_by('version', 'id'))
        self.fact_versions = defaultdict(list)
        for version in versions:
            self.fact_versions[version.fact_id].append(version)
        fields = self.load(RecordField.objects.filter(patient_id=patient.id,
            id__in={row.field_id for row in versions}))
        records = self.load(RecordVersion.objects.filter(patient_id=patient.id,
            id__in={row.record_version_id for row in versions}))
        # 原始作者仅用于来源权限，继续使用原有受保护元数据查询。
        self.original_records = {row.record_id: row for row in RecordVersion.all_objects.filter(
            patient_id=patient.id, record_id__in={row.record_id for row in records}, version=1)
            .only('id', 'patient_id', 'record_id', 'version', 'author_id')}
        self.load(Report.objects.filter(patient_id=patient.id,
            id__in={row.report_id for row in records if row.report_id is not None}))
        anchors = self.load(SourceAnchor.objects.filter(patient_id=patient.id,
            id__in={row.anchor_id for row in fields if row.anchor_id is not None}))
        file_pages = self.load(FilePage.objects.filter(patient_id=patient.id,
            id__in={row.page_id for row in anchors}))
        file_ids = {row.id for row in files} | {row.file_id for row in file_pages}
        file_ids.update(identity for row in relations
            for identity in (row.left_file_id, row.right_file_id) if identity is not None)
        source_files = self.load(FileObject.objects.filter(patient_id=patient.id, id__in=file_ids))
        self.load(FileGroup.objects.filter(patient_id=patient.id,
            id__in={row.file_group_id for row in source_files}))
        self.load(ArchivePage.objects.filter(patient_id=patient.id,
            id__in={row.page_id for row in versions if row.page_id is not None}))
        document_ids = {row.document_version_id for row in versions if row.document_version_id is not None}
        self.load(ArchiveDocumentVersion.objects.filter(patient_id=patient.id, id__in=document_ids))
        self.load(ArchiveDocumentPage.objects.filter(patient_id=patient.id, document_version_id__in=document_ids,
            page_id__in={row.page_id for row in versions if row.page_id is not None}))
        states = self.load(FieldVerificationState.objects.filter(patient_id=patient.id,
            field_id__in={row.id for row in fields}, record_version_id__in={row.id for row in records},
            purpose='family_observation').order_by('id'))
        proof_ids = {row.current_verification_id for row in states if row.current_verification_id is not None}
        self.load(Verification.objects.filter(patient_id=patient.id, id__in=proof_ids))
        self.load(VerificationField.objects.filter(patient_id=patient.id, verification_id__in=proof_ids))

    def store(self, model, rows):
        values = list(rows)
        self.tables[model] = {row.id: row for row in values}
        return values

    def load(self, query):
        return self.store(query.model, query)

    def rows(self, model, **scope):
        table = self.tables[model]
        if 'id' in scope:
            row = table.get(scope['id'])
            values = [] if row is None else [row]
        else:
            values = table.values()
        return [row for row in values if all(getattr(row, name) == value for name, value in scope.items())]

    def get(self, model, **scope):
        values = self.rows(model, **scope)
        if not values:
            raise model.DoesNotExist()
        if len(values) != 1:
            raise model.MultipleObjectsReturned()
        return values[0]

    def first(self, model, **scope):
        values = self.rows(model, **scope)
        return values[0] if values else None

    def exists(self, model, **scope):
        return bool(self.rows(model, **scope))

    def original_record(self, record_id):
        original = self.original_records.get(record_id)
        if original is None:
            raise RecordVersion.DoesNotExist('事实的原始记录作者无法读取。')
        return original

    def fact_source(self, version):
        return checked_fact_source(version, self.patient, self.tables[RecordField].get(version.field_id),
            self.tables[RecordVersion].get(version.record_version_id))
