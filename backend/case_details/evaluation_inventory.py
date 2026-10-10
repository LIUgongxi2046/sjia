import hashlib
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from case_details.evaluation_units import inspect_unit_manifest


Text = Annotated[str, Field(min_length=1, pattern=r'\S', strict=True)]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$', strict=True)]


class EvaluationInventoryRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


class SourceArtifact(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: Text
    path: Text
    sha256: Digest
    size_bytes: Annotated[int, Field(ge=0)]
    format: Text | None = None
    width: Annotated[int, Field(gt=0)] | None = None
    height: Annotated[int, Field(gt=0)] | None = None

    @field_validator('path')
    @classmethod
    def relative_source_path(cls, value):
        path = PurePosixPath(value)
        if path.is_absolute() or '..' in path.parts or path.as_posix() != value or '\\' in value:
            raise ValueError('来源路径必须为清单目录内的规范相对路径')
        return value

    @model_validator(mode='after')
    def complete_dimensions(self):
        if (self.width is None) != (self.height is None):
            raise ValueError('图片尺寸必须同时登记宽度与高度')
        return self


class SourceManifest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    dataset_version: Text
    original_attachment_comparison: Literal['PASS', 'FAIL', 'NOT_RUN']
    sources: Annotated[list[SourceArtifact], Field(min_length=1)]

    @model_validator(mode='after')
    def unique_sources(self):
        if len({source.id for source in self.sources}) != len(self.sources):
            raise ValueError('来源标识必须唯一')
        if len({source.path for source in self.sources}) != len(self.sources):
            raise ValueError('来源路径必须唯一')
        return self


def inspect_evaluation_sources(manifest_path, *, unit_manifest_path=None):
    location = Path(manifest_path).resolve(strict=True)
    if not location.is_file():
        raise EvaluationInventoryRejected('SOURCE_MANIFEST_UNAVAILABLE')
    manifest_bytes = location.read_bytes()
    manifest = SourceManifest.model_validate_json(manifest_bytes)
    directory = location.parent
    artifacts, content_groups, paths = [], defaultdict(list), set()
    for source in manifest.sources:
        path = (directory / source.path).resolve(strict=True)
        if not path.is_relative_to(directory) or not path.is_file():
            raise EvaluationInventoryRejected('SOURCE_FILE_OUTSIDE_MANIFEST')
        if path in paths:
            raise EvaluationInventoryRejected('SOURCE_FILE_REPEATED')
        paths.add(path)
        before = path.stat()
        if before.st_size != source.size_bytes:
            raise EvaluationInventoryRejected('SOURCE_FILE_SIZE_MISMATCH')
        digest, observed_size = hashlib.sha256(), 0
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
                observed_size += len(chunk)
        after = path.stat()
        if (before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ino != after.st_ino or observed_size != source.size_bytes):
            raise EvaluationInventoryRejected('SOURCE_FILE_CHANGED')
        if digest.hexdigest() != source.sha256:
            raise EvaluationInventoryRejected('SOURCE_FILE_CHECKSUM_MISMATCH')
        artifacts.append({'source_id': source.id, 'path': source.path, 'sha256': source.sha256,
            'size_bytes': observed_size})
        content_groups[source.sha256].append(source.id)
    counts = {'files': len(artifacts), 'pages': None, 'documents': None, 'patients': None,
        'encounters': None, 'sampling_events': None, 'mas_events': None, 'observation_periods': None,
        'observation_person_hours': None, 'confirmed_labels': None, 'unknown_labels': None}
    result = {'schema_version': 'evaluation-source-inventory-1', 'dataset_version': manifest.dataset_version,
        'source_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
        'file_integrity': 'verified', 'purpose_qualification': 'not_evaluated',
        'counts': counts, 'unrecorded_units': [unit for unit, count in counts.items() if count is None],
        'files': artifacts, 'total_bytes': sum(item['size_bytes'] for item in artifacts),
        'identical_content_groups': [identifiers for identifiers in content_groups.values() if len(identifiers) > 1]}
    result['unit_mapping'] = inspect_unit_manifest(result, unit_manifest_path)
    return result
