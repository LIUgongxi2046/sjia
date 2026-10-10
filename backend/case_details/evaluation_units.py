import hashlib
from pathlib import Path
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


Text = Annotated[str, Field(min_length=1, pattern=r'\S', strict=True)]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$', strict=True)]
Version = Annotated[str, Field(pattern=r'^[1-9][0-9]*$', max_length=19, strict=True)]
References = Annotated[list[Text], Field(min_length=1)]
Item = TypeVar('Item')
UNIT_NAMES = ('files', 'pages', 'documents', 'patients', 'encounters', 'sampling_events',
    'mas_events', 'observation_periods', 'labels')


class EvaluationUnitsRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


class UnitModel(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', allow_inf_nan=False)

    @field_validator('evidence_refs', 'source_ids', 'page_ids', check_fields=False)
    @classmethod
    def unique_references(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('单位引用不能重复')
        return value

    @field_validator('version', check_fields=False)
    @classmethod
    def bounded_version(cls, value):
        if int(value) > 9223372036854775807:
            raise ValueError('单位版本超出数据库范围')
        return value


class EvidenceUnit(UnitModel):
    id: UUID
    evidence_refs: References


class FileUnit(EvidenceUnit):
    patient_id: UUID
    source_id: Text
    object_version: Text
    file_group_id: UUID
    source_revision_id: UUID
    purpose_copy_id: UUID
    purpose_copy_version: Version
    copy_file_id: UUID
    copy_object_version: Text
    copy_sha256: Digest

    @field_validator('purpose_copy_version')
    @classmethod
    def bounded_copy_version(cls, value):
        return cls.bounded_version(value)


class PageUnit(EvidenceUnit):
    source_id: Text
    file_id: UUID
    file_group_id: UUID
    file_object_version: Text
    ordinal: Annotated[int, Field(ge=1)]


class PatientUnit(EvidenceUnit):
    source_ids: References


class PatientSourceUnit(PatientUnit):
    patient_id: UUID


class DocumentUnit(PatientSourceUnit):
    document_id: UUID
    version: Version
    page_ids: Annotated[list[UUID], Field(min_length=1)]


class EncounterUnit(PatientSourceUnit):
    pass


class SamplingUnit(PatientSourceUnit):
    encounter_id: UUID | None
    source_field_ids: Annotated[list[UUID], Field(min_length=1)]
    event_time_id: UUID | None
    confirmed_by: UUID
    confirmed_at: AwareDatetime
    confirmation_ref: Text

    @field_validator('source_field_ids')
    @classmethod
    def unique_fields(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('事件来源字段不能重复')
        return value


class MasUnit(SamplingUnit):
    reference_answer_ref: Text


class ObservationUnit(PatientSourceUnit):
    period_id: UUID
    version: Version
    start_time_id: UUID
    end_time_id: UUID


class LabelUnit(PatientSourceUnit):
    observation_period_version_id: UUID
    label_state: Literal['positive', 'negative', 'unknown']
    reference_answer_ref: Text | None
    reference_recognition_at: AwareDatetime | None
    confirmed_by: UUID | None
    confirmed_at: AwareDatetime | None
    limitations: list[Text]

    @model_validator(mode='after')
    def independent_answer(self):
        if (self.confirmed_by is None) != (self.confirmed_at is None):
            raise ValueError('标签确认人员与时间必须同时登记')
        if self.label_state != 'unknown' and (self.reference_answer_ref is None or self.confirmed_by is None):
            raise ValueError('确定标签需要独立答案与确认记录')
        if (self.label_state == 'unknown' or self.reference_recognition_at is None) and not self.limitations:
            raise ValueError('未知标签或缺少识别时间需要登记局限')
        return self


class UnitCoverage(UnitModel, Generic[Item]):
    state: Literal['complete', 'partial']
    evidence_refs: References
    items: list[Item]
    limitations: list[Text]

    @model_validator(mode='after')
    def coverage_proof(self):
        if self.state == 'partial' and not self.limitations:
            raise ValueError('部分覆盖需要登记缺口')
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError('同类单位身份不能重复')
        return self


class EvaluationUnitManifest(UnitModel):
    schema_version: Literal['evaluation-unit-manifest-1']
    raw_source_manifest_ref: Text | None
    source_manifest_sha256: Digest
    files: UnitCoverage[FileUnit] | None
    pages: UnitCoverage[PageUnit] | None
    documents: UnitCoverage[DocumentUnit] | None
    patients: UnitCoverage[PatientUnit] | None
    encounters: UnitCoverage[EncounterUnit] | None
    sampling_events: UnitCoverage[SamplingUnit] | None
    mas_events: UnitCoverage[MasUnit] | None
    observation_periods: UnitCoverage[ObservationUnit] | None
    labels: UnitCoverage[LabelUnit] | None

    def entries(self, name):
        coverage = getattr(self, name)
        return [] if coverage is None else coverage.items

    @model_validator(mode='after')
    def linked_identities(self):
        files = {item.id: item for item in self.entries('files')}
        sources = {item.source_id: item for item in files.values()}
        pages = {item.id: item for item in self.entries('pages')}
        patients = {item.id: item for item in self.entries('patients')}
        encounters = {item.id: item for item in self.entries('encounters')}
        periods = {item.id: item for item in self.entries('observation_periods')}
        if len(sources) != len(files) or len({item.purpose_copy_id for item in files.values()}) != len(files):
            raise ValueError('来源身份及用途副本不能重复')
        for page in pages.values():
            file = files.get(page.file_id)
            if (file is None or file.source_id != page.source_id or file.file_group_id != page.file_group_id
                    or file.object_version != page.file_object_version):
                raise ValueError('页面必须关联准确原件及文件组版本')
        for patient in patients.values():
            if any(source_id not in sources or sources[source_id].patient_id != patient.id for source_id in patient.source_ids):
                raise ValueError('患儿来源必须关联本人原件')
        page_positions = {(item.file_group_id, item.ordinal) for item in pages.values()}
        if len(page_positions) != len(pages):
            raise ValueError('同一来源的页面位置不能重复')
        for name in ('documents', 'encounters', 'sampling_events', 'mas_events', 'observation_periods', 'labels'):
            for item in self.entries(name):
                patient = patients.get(item.patient_id)
                if patient is None or not set(item.source_ids) <= set(patient.source_ids):
                    raise ValueError('单位必须关联已登记患儿及其来源')
        for item in self.entries('documents'):
            if any(page_id not in pages or pages[page_id].source_id not in item.source_ids for page_id in item.page_ids):
                raise ValueError('文书必须关联已登记页面及准确来源')
        for name in ('sampling_events', 'mas_events'):
            for item in self.entries(name):
                if item.encounter_id is not None and (item.encounter_id not in encounters
                        or encounters[item.encounter_id].patient_id != item.patient_id):
                    raise ValueError('事件就诊必须属于同一患儿')
        for item in self.entries('labels'):
            period = periods.get(item.observation_period_version_id)
            if period is None or period.patient_id != item.patient_id:
                raise ValueError('标签观察版本必须属于同一患儿')
        if self.labels is not None and self.labels.state == 'complete':
            if {item.observation_period_version_id for item in self.labels.items} != set(periods):
                raise ValueError('完整标签范围必须覆盖已登记观察版本')
        for name, attribute in (('documents', 'document_id'), ('observation_periods', 'period_id'),
                ('labels', 'observation_period_version_id')):
            items = self.entries(name)
            if len({getattr(item, attribute) for item in items}) != len(items):
                raise ValueError('固定集合内同一对象只能引用一个版本或标签')
        return self


def inspect_unit_manifest(inventory, manifest_path):
    counts = dict(inventory['counts'])
    if manifest_path is None:
        return {'state': 'unrecorded', 'qualification': 'not_evaluated', 'manifest_sha256': None,
            'declared_counts': counts, 'coverage': {name: None for name in UNIT_NAMES}}
    content = Path(manifest_path).resolve(strict=True).read_bytes()
    manifest = EvaluationUnitManifest.model_validate_json(content)
    return unit_manifest_view(inventory, manifest, hashlib.sha256(content).hexdigest())


def validate_unit_sources(inventory, manifest):
    if manifest.source_manifest_sha256 != inventory['source_manifest_sha256']:
        raise EvaluationUnitsRejected('UNIT_SOURCE_VERSION_MISMATCH')
    source_ids = {item['source_id'] for item in inventory['files']}
    for name in UNIT_NAMES:
        for item in manifest.entries(name):
            references = {item.source_id} if name in ('files', 'pages') else set(item.source_ids)
            if not references <= source_ids:
                raise EvaluationUnitsRejected('UNIT_SOURCE_REFERENCE_INVALID')
    if manifest.files is not None and manifest.files.state == 'complete':
        if {item.source_id for item in manifest.files.items} != source_ids:
            raise EvaluationUnitsRejected('UNIT_FILE_COVERAGE_MISMATCH')


def unit_manifest_view(inventory, manifest, manifest_checksum):
    validate_unit_sources(inventory, manifest)
    counts = dict(inventory['counts'])
    coverage_view = {}
    for name in UNIT_NAMES:
        coverage = getattr(manifest, name)
        coverage_view[name] = None if coverage is None else {
            'state': coverage.state, 'registered_items': len(coverage.items),
            'evidence_refs': coverage.evidence_refs, 'limitations': coverage.limitations}
        if coverage is not None and coverage.state == 'complete':
            if name == 'labels':
                counts['confirmed_labels'] = sum(item.label_state != 'unknown' for item in coverage.items)
                counts['unknown_labels'] = sum(item.label_state == 'unknown' for item in coverage.items)
            elif name != 'files':
                counts[name] = len(coverage.items)
    # 映射声明还需要当前业务对象、用途和独立答案核验，不能覆盖文件核验的实际统计。
    return {'state': 'provided', 'qualification': 'not_evaluated',
        'manifest_sha256': manifest_checksum, 'declared_counts': counts, 'coverage': coverage_view}
