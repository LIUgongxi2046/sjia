import hashlib
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from django.apps import apps
from django.conf import settings
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from typing import Annotated, Literal

from authorization.models import ServiceRoleGrant
from configurations.service import ConfigRejected, compute_checksum
from retention.models import RetentionPolicy
from sources.models import SourceRegistry, SourceRevision


Text = Annotated[str, Field(min_length=1, pattern=r'.*\S.*')]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class ConfigValidationReport(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    schema_version: Literal['config-validation-1']
    config_id: UUID
    config_checksum: Digest
    environment: Text
    artifact_sha256: Digest
    dataset_revision_id: UUID
    outcome: Literal['pass', 'fail']
    executed_by: UUID
    executed_at: AwareDatetime


REPORT_VALIDATOR = Draft202012Validator(ConfigValidationReport.model_json_schema(), format_checker=FormatChecker())
UUID_VALIDATOR = Draft202012Validator({'type': 'string', 'format': 'uuid'}, format_checker=FormatChecker())
ARTIFACT_SCOPE = ContextVar('configuration_artifact_scope', default=None)


def deployed_artifact_checksum(config):
    scope = ARTIFACT_SCOPE.get()
    if scope is None:
        return actual_artifact_checksum(config)
    category = {'purpose-processing-2': 'image_processing',
        'purpose-processing-text-1': 'text_processing'}.get(config.schema_version, 'backend')
    if category not in scope:
        scope[category] = (config, actual_artifact_checksum(config))
    return scope[category][1]


@contextmanager
def artifact_verification_scope():
    if ARTIFACT_SCOPE.get() is not None:
        yield
        return
    scope = {}
    token = ARTIFACT_SCOPE.set(scope)
    try:
        yield
        for config, checksum in scope.values():
            if actual_artifact_checksum(config) != checksum:
                raise ConfigRejected('CONFIG_IMPLEMENTATION_CHANGED')
    finally:
        ARTIFACT_SCOPE.reset(token)


def actual_artifact_checksum(config):
    root = Path(__file__).resolve().parents[2]
    if config.schema_version == 'purpose-processing-2':
        from files.purpose_processing import processing_artifact_checksum

        return processing_artifact_checksum()
    if config.schema_version == 'purpose-processing-text-1':
        from files.purpose_text import text_processing_artifact_checksum

        return text_processing_artifact_checksum()
    paths = {root / 'backend/requirements.lock.txt', root / 'backend/manage.py'}
    directories = {Path(app.path) for app in apps.get_app_configs()
        if Path(app.path).is_relative_to(root / 'backend')}
    directories.add(root / 'backend/config')
    for directory in directories:
        suffixes = ('.py',) if directory == root / 'backend/config' else ('.py', '.json')
        for parent, _, filenames in os.walk(directory, onerror=raise_artifact_scan_error):
            paths.update(Path(parent) / name for name in filenames if name.endswith(suffixes))
    for directory in ('backend-design', 'alert-design', 'data-design'):
        paths.update((root / directory).glob('*.schema.json'))
    manifest = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}
    return compute_checksum(manifest)


def raise_artifact_scan_error(error):
    raise error


def evidence_reference(reference):
    if not isinstance(reference, str) or not reference.startswith('urn:sjia:source-revision:'):
        return None
    uri = urlsplit(reference)
    identifier = uri.path.removeprefix('sjia:source-revision:')
    if (uri.scheme != 'urn' or uri.netloc or uri.query or uri.fragment
            or not UUID_VALIDATOR.is_valid(identifier)):
        return None
    value = UUID(identifier)
    if reference != f'urn:sjia:source-revision:{value}':
        return None
    return value


def evidence_revision_id(reference):
    value = evidence_reference(reference)
    if value is None:
        raise ConfigRejected('CONFIG_EVIDENCE_REFERENCE_INVALID')
    return value


def inspect_approved_evidence(reference, *, purpose, expected_checksum=None, lock=False, now=None):
    revision_id = evidence_reference(reference)
    if revision_id is None:
        return None, None, 'CONFIG_EVIDENCE_REFERENCE_INVALID'
    revisions = SourceRevision.objects.all()
    if lock:
        revisions = revisions.select_for_update()
    else:
        revisions = revisions.select_related('source', 'retention_policy')
    revision = revisions.filter(id=revision_id).first()
    if revision is None:
        return None, None, 'CONFIG_EVIDENCE_UNAVAILABLE'
    if lock:
        revision.source = SourceRegistry.objects.select_for_update().get(id=revision.source_id)
        if revision.retention_policy_id is not None:
            revision.retention_policy = RetentionPolicy.objects.select_for_update().get(id=revision.retention_policy_id)
    now = now or timezone.now()
    if (revision.status != 'approved' or revision.source.status != 'approved'
            or revision.captured_at > now or purpose not in revision.allowed_use
            or not revision.license.strip() or not revision.rights_evidence_ref or not revision.rights_evidence_ref.strip()
            or revision.retention_policy_id is None or revision.retention_policy.status != 'active'):
        return None, None, 'CONFIG_EVIDENCE_NOT_APPROVED'
    root_value = settings.CONFIG_EVIDENCE_ROOT
    maximum = settings.CONFIG_EVIDENCE_MAX_BYTES
    if not root_value or maximum is None:
        return None, None, 'CONFIG_EVIDENCE_UNAVAILABLE'
    root = Path(root_value)
    if not root.is_absolute() or not root.is_dir():
        return None, None, 'CONFIG_EVIDENCE_UNAVAILABLE'
    root = root.resolve(strict=True)
    location = Path(revision.source.location)
    if not location.is_absolute() or not location.is_file():
        return None, None, 'CONFIG_EVIDENCE_UNAVAILABLE'
    location = location.resolve(strict=True)
    if not location.is_relative_to(root):
        return None, None, 'CONFIG_EVIDENCE_LOCATION_INVALID'
    if location.stat().st_size > maximum:
        return None, None, 'CONFIG_EVIDENCE_TOO_LARGE'
    with location.open('rb') as source:
        content = source.read(maximum + 1)
    if len(content) > maximum:
        return None, None, 'CONFIG_EVIDENCE_TOO_LARGE'
    checksum = hashlib.sha256(content).hexdigest()
    if checksum != revision.checksum or (expected_checksum is not None and checksum != expected_checksum):
        return None, None, 'CONFIG_EVIDENCE_CHECKSUM_MISMATCH'
    return revision, content, None


def read_approved_evidence(reference, *, purpose, expected_checksum=None):
    revision, content, reason = inspect_approved_evidence(reference, purpose=purpose,
        expected_checksum=expected_checksum, lock=True)
    if reason:
        raise ConfigRejected(reason)
    return revision, content


def inspect_validation_report(config, gate, data, *, lock=False, now=None, expected_artifact_sha256=None):
    now = now or timezone.now()
    revision, content, reason = inspect_approved_evidence(data['evidence_ref'], purpose='configuration_validation',
        expected_checksum=data['evidence_sha256'], lock=lock, now=now)
    if reason:
        return None, None, reason
    payload = json.loads(content)
    if not REPORT_VALIDATOR.is_valid(payload):
        return None, None, 'CONFIG_VALIDATION_REPORT_INVALID'
    report = ConfigValidationReport.model_validate_json(content)
    if (str(report.config_id) != str(config.id) or report.config_checksum != config.checksum
            or report.config_checksum != data['config_checksum'] or report.environment != gate.environment
            or report.environment != data['environment'] or str(report.dataset_revision_id) != data['dataset_revision_id']
            or report.artifact_sha256 != data['artifact_sha256']
            or report.artifact_sha256 != (deployed_artifact_checksum(config) if expected_artifact_sha256 is None else expected_artifact_sha256)
            or report.executed_at > now or report.executed_at < config.created_at
            or report.executed_at > revision.captured_at):
        return None, None, 'CONFIG_VALIDATION_REPORT_MISMATCH'
    datasets = SourceRevision.objects.select_related('source', 'retention_policy')
    grants = ServiceRoleGrant.objects.select_related('principal')
    if lock:
        datasets = datasets.select_for_update(of=('self',))
        grants = grants.select_for_update()
    dataset = datasets.filter(id=report.dataset_revision_id).first()
    if dataset is None:
        return None, None, 'CONFIG_VALIDATION_DATASET_UNAVAILABLE'
    if lock:
        dataset.source = SourceRegistry.objects.select_for_update().get(id=dataset.source_id)
        if dataset.retention_policy_id is not None:
            dataset.retention_policy = RetentionPolicy.objects.select_for_update().get(id=dataset.retention_policy_id)
    if (dataset.status != 'approved' or dataset.source.status != 'approved'
            or 'configuration_validation' not in dataset.allowed_use or not dataset.rights_evidence_ref
            or not dataset.rights_evidence_ref.strip() or not dataset.license.strip()
            or dataset.retention_policy_id is None or dataset.retention_policy.status != 'active'
            or dataset.captured_at > report.executed_at):
        return None, None, 'CONFIG_VALIDATION_DATASET_UNAVAILABLE'
    grant = grants.filter(principal_id=report.executed_by,
        environment=gate.environment, action='register_validation', valid_from__lte=report.executed_at).order_by('-valid_from', '-id').first()
    if (grant is None or grant.principal.account_status != 'active' or not grant.evidence_ref.strip()
            or (grant.valid_until is not None and grant.valid_until <= report.executed_at)
            or (grant.revoked_at is not None and grant.revoked_at <= now)):
        return None, None, 'CONFIG_VALIDATION_EXECUTOR_UNAVAILABLE'
    return report, dataset, None


def validation_report(config, gate, data):
    report, dataset, reason = inspect_validation_report(config, gate, data, lock=True)
    if reason:
        raise ConfigRejected(reason)
    return report, dataset
