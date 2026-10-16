"""Explicit, schema-checked admission for the legacy domain projection.

The caller must select an artifact and its exact digest. No bundled patient
projection or manifest is opened implicitly, and originals must already exist.
"""
import hashlib
import json
import re
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from authorization.policy import evaluate
from files.purpose import checked_bytes, lock_file_access, lock_original, lock_use_authorization
from identity.models import Patient
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, IngestionRun, SourceRevision

ROOT = Path(__file__).resolve().parents[2]
PROJECTION_SCHEMA_VERSION = 'case-design-projection-1'


class CaseImportRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


def load_projection(path, expected_sha256, patient_id):
    if path is None or not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise CaseImportRejected('CASE_EXPLICIT_PROJECTION_REQUIRED')
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise CaseImportRejected('CASE_PROJECTION_UNAVAILABLE')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise CaseImportRejected('CASE_PROJECTION_CHECKSUM_MISMATCH')
    try:
        value = json.loads(raw)
        required = {'dataset_version', 'status', 'patient_id', 'source_sha256', 'scope', 'medical_times', 'fields', 'case_details'}
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError()
        if str(UUID(value['patient_id'])) != str(patient_id):
            raise CaseImportRejected('CASE_PATIENT_MISMATCH')
        if (value['status'] != 'DESIGN_PROJECTION_VALIDATED' or not isinstance(value['dataset_version'], str)
                or not value['dataset_version'].strip() or not isinstance(value['scope'], str)
                or not re.fullmatch('[0-9a-f]{64}', value['source_sha256'])
                or not isinstance(value['fields'], list) or not value['fields']
                or not isinstance(value['medical_times'], list)):
            raise ValueError()
        case_schema = json.loads((ROOT / 'data-design/case-extension.schema.json').read_text())
        Draft202012Validator(case_schema, format_checker=FormatChecker()).validate(value['case_details'])
        dataset = json.loads((ROOT / 'data-design/dataset.schema.json').read_text())
        time_schema = {'$ref': '#/$defs/medicalTime', '$defs': dataset['$defs']}
        validator = Draft202012Validator(time_schema, format_checker=FormatChecker())
        times = set()
        for item in value['medical_times']:
            validator.validate(item)
            if item['patient_id'] != value['patient_id'] or item['id'] in times:
                raise ValueError()
            times.add(item['id'])
        fields = set()
        keys = {'id','patient_id','record_version_id','source_id','original_name','raw_text','value_type',
            'raw_numeric','qualifier','raw_unit','report_flag','reference_interval'}
        for item in value['fields']:
            if not isinstance(item, dict) or not keys <= set(item) or set(item) - keys - {'medical_time_id'}:
                raise ValueError()
            UUID(item['id']); UUID(item['record_version_id'])
            if item['patient_id'] != value['patient_id'] or item['id'] in fields or item['value_type'] not in ('text','decimal'):
                raise ValueError()
            if not isinstance(item['original_name'], str) or not item['original_name'].strip():
                raise ValueError()
            if item['value_type'] == 'decimal' and (not isinstance(item['raw_numeric'], str)
                    or not re.fullmatch(r'-?(0|[1-9][0-9]*)(\.[0-9]+)?', item['raw_numeric'])):
                raise ValueError()
            if item['value_type'] == 'text' and item['raw_numeric'] is not None:
                raise ValueError()
            if item.get('medical_time_id') is not None and item['medical_time_id'] not in times:
                raise ValueError()
            if item['qualifier'] not in (None, 'eq', 'lt', 'le', 'gt', 'ge'):
                raise ValueError()
            if any(item[key] is not None and not isinstance(item[key], str)
                    for key in ('source_id','raw_text','raw_unit','report_flag','reference_interval')):
                raise ValueError()
            fields.add(item['id'])
        for collection in value['case_details'].values():
            for item in collection:
                if item['patient_id'] != value['patient_id'] or item['source_field_id'] not in fields:
                    raise ValueError()
    except CaseImportRejected:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, ValidationError):
        # Schema errors can contain raw clinical input; expose only this code.
        raise CaseImportRejected('CASE_PROJECTION_INVALID') from None
    return value


def admit_case(*, projection, digest, patient, operator, auth_epoch, retention_policy,
        use_authorization_id, source_revision, file_bindings):
    environment = settings.SJIA_ENVIRONMENT
    evaluate(principal=operator, patient=None, action='import_case', purpose='case_import',
        environment=environment, session_auth_epoch=auth_epoch)
    patient = Patient.objects.select_for_update().get(id=patient.id)
    if patient.status != 'active':
        raise CaseImportRejected('CASE_PATIENT_UNAVAILABLE')
    decision = evaluate(principal=operator, patient=patient, action='read_case', purpose='case_import',
        environment=environment, session_auth_epoch=auth_epoch, whole_resource=True)
    type(decision.grant).objects.select_for_update().get(id=decision.grant.id)
    evaluate(principal=operator, patient=patient, action='read_case', purpose='case_import',
        environment=environment, session_auth_epoch=auth_epoch, whole_resource=True)
    now = timezone.now()
    revision = SourceRevision.objects.select_for_update().select_related('source').get(id=source_revision.id)
    authorization = DataUseAuthorization.objects.select_for_update().select_related('approved_by').get(id=use_authorization_id)
    # approved_by is nullable: FOR UPDATE on a select_related outer join is
    # rejected by PostgreSQL. Lock only the policy row, then inspect its owner.
    policy = RetentionPolicy.objects.select_for_update().get(id=retention_policy.id)
    if (revision.status != 'approved' or revision.source.status != 'approved' or revision.captured_at > now
            or not revision.rights_evidence_ref or not revision.rights_evidence_ref.strip()
            or revision.checksum != projection['source_sha256'] or not isinstance(revision.allowed_use, list)
            or 'case_import' not in revision.allowed_use
            or authorization.patient_id != patient.id or authorization.source_revision_id != revision.id
            or authorization.purpose != 'case_import' or authorization.environment != environment
            or authorization.revoked_at is not None or authorization.approved_at > now
            or authorization.valid_until is not None and authorization.valid_until <= now
            or authorization.approved_by.account_status != 'active' or not authorization.evidence_ref.strip()):
        raise CaseImportRejected('CASE_SOURCE_AUTHORIZATION_UNAVAILABLE')
    if (policy.status != 'active' or policy.data_class != 'case' or policy.approved_at is None
            or policy.approved_at > now or policy.approved_by_id is None or policy.approved_by.account_status != 'active'
            or not policy.evidence_ref or not policy.evidence_ref.strip()):
        raise CaseImportRejected('CASE_RETENTION_UNAVAILABLE')
    required_files = {item['file_id'] for item in projection['case_details']['image_links']}
    required_files.update(reference['file_id'] for item in projection['case_details']['quality_issues']
        for reference in item['references'] if reference['file_id'] is not None)
    if not isinstance(file_bindings, dict) or set(file_bindings) != required_files:
        raise CaseImportRejected('CASE_ORIGINAL_BINDINGS_REQUIRED')
    bound = {}
    for expected, actual in file_bindings.items():
        file = lock_original(actual)
        if file.patient_id != patient.id:
            raise CaseImportRejected('CASE_ORIGINAL_PATIENT_MISMATCH')
        lock_file_access(file, operator, auth_epoch, purpose='case_import')
        lock_use_authorization(authorization.id, file, 'case_import', environment)
        checked_bytes(file)
        bound[expected] = file
    binding = json.dumps({'patient_id': str(patient.id), 'retention_policy_id': str(policy.id),
        'use_authorization_id': str(authorization.id), 'files': {key: str(file.id) for key, file in bound.items()}}, sort_keys=True)
    run = IngestionRun.objects.select_for_update().filter(source_revision=revision, dataset_version=projection['dataset_version']).first()
    if run is not None:
        if run.checksum != digest or run.validation_report_ref != binding or run.schema_version != PROJECTION_SCHEMA_VERSION:
            raise CaseImportRejected('CASE_IMPORT_IDEMPOTENCY_CONFLICT')
        if run.status != 'completed':
            raise CaseImportRejected('CASE_IMPORT_RESULT_UNCERTAIN')
        return run, bound, True
    run = IngestionRun.objects.create(source_revision=revision, dataset_version=projection['dataset_version'],
        checksum=digest, schema_version=PROJECTION_SCHEMA_VERSION, operator=operator, environment=environment,
        status='importing', started_at=now, validation_report_ref=binding)
    return run, bound, False
