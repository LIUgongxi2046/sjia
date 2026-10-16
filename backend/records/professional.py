"""Intent-bound single-record ClinicalDataset ingestion for designated professionals.

Exchange identities never create permissions or verification. Source, purpose,
originals and retention are rechecked under locks at preparation and commit.
"""
import copy
import json
import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from assessment.qualified_facts import lock_case_permissions
from authorization.policy import check_principal_session
from family_content.models import EnvironmentGate
from files.models import FileObject, FilePage
from files.purpose import checked_bytes, lock_file_access, lock_original, lock_use_authorization
from identity.models import Patient, Principal
from medical_time.models import MedicalTime
from recognition.models import SourceAnchor
from records.correction import correct_record
from records.medical_times import time_values
from records.models import Record, RecordAttachment, RecordField, RecordVersion, Report
from records.service import RecordRejected, create_record
from requests.models import WriteIntent, WriteRequest
from requests.service import compute_digest, commit_intent, prepare_intent
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = {}
for name in ('dataset.schema.json', 'case-extension.schema.json'):
    uri = (ROOT / 'data-design' / name).as_uri()
    schema = json.loads((ROOT / 'data-design' / name).read_text())
    schema['$id'] = uri
    SCHEMAS[uri] = schema
REGISTRY = Registry().with_resources((uri, Resource.from_contents(schema)) for uri, schema in SCHEMAS.items())
DATASET = SCHEMAS[(ROOT / 'data-design/dataset.schema.json').as_uri()]
VALIDATOR = Draft202012Validator({**DATASET, '$ref': '#/$defs/clinicalDataset'}, registry=REGISTRY, format_checker=FormatChecker())
PROFESSIONAL_OPERATIONS = ('professional_record_create', 'professional_record_correct')
UUID_PATTERN = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
FIELD_KEYS = ('field_code', 'concept_code', 'dictionary_entry_id', 'original_name', 'value_type', 'value_state',
    'raw_text', 'raw_numeric', 'categorical_value', 'qualifier', 'raw_unit', 'method', 'reference_interval',
    'report_flag', 'medical_time', 'sample_time_origin')


def professional_payload(operation, value):
    if operation not in PROFESSIONAL_OPERATIONS or not isinstance(value, dict):
        raise RecordRejected('PROFESSIONAL_INPUT_INVALID')
    dataset = value
    if operation == 'professional_record_correct':
        if set(value) != {'expected_version', 'reason', 'dataset'} or not isinstance(value['reason'], str) or not value['reason'].strip():
            raise RecordRejected('PROFESSIONAL_CORRECTION_INVALID')
        expected = value['expected_version']
        if not isinstance(expected, str) or not re.fullmatch(r'[1-9][0-9]{0,18}', expected) or int(expected) >= 9223372036854775807:
            raise RecordRejected('PROFESSIONAL_VERSION_INVALID')
        dataset = value['dataset']
    if not VALIDATOR.is_valid(dataset) or len(dataset['records']) != 1 or len(dataset['sources']) != 1:
        raise RecordRejected('PROFESSIONAL_SINGLE_RECORD_REQUIRED')
    record = dataset['records'][0]
    if (record['origin_type'] != 'authorized_clinical' or record['capture_method'] != 'authorized_import'
            or record['quality_status'] != 'pending' or record['quality_reasons']
            or len({field['field_id'] for field in record['fields']}) != len(record['fields'])
            or [field['ordinal'] for field in record['fields']] != list(range(1, len(record['fields']) + 1))):
        raise RecordRejected('PROFESSIONAL_RECORD_AUTHORITY_INVALID')
    entity_ids = [record['record_id'], record['record_version_id'], *[field['field_id'] for field in record['fields']]]
    if len(set(entity_ids)) != len(entity_ids):
        raise RecordRejected('PROFESSIONAL_ENTITY_ID_DUPLICATE')
    for field in record['fields']:
        ref = field['source_reference']
        if (ref['record_version_id'] != record['record_version_id'] or ref['field_id'] != field['field_id']
                or ref['source_revision_id'] != record['source_revision_id'] or ref['verification_ids'] or ref['mapping_id'] is not None
                or bool(ref['file_id']) != bool(ref['page_id']) or bool(ref['file_id']) != bool(ref['anchor_id'])):
            raise RecordRejected('PROFESSIONAL_FIELD_PROVENANCE_INVALID')
    if operation == 'professional_record_create' and record['version'] != 1:
        raise RecordRejected('PROFESSIONAL_VERSION_INVALID')
    if operation == 'professional_record_correct' and (record['version'] != int(value['expected_version']) + 1
            or record['supersedes_version'] != int(value['expected_version']) or record['correction_reason'] != value['reason']):
        raise RecordRejected('PROFESSIONAL_VERSION_INVALID')
    case = dataset.get('case_details')
    if case is not None:
        for rows in case.values():
            for entity in rows:
                if (entity['patient_id'] != record['patient_id'] or entity['record_version_id'] != record['record_version_id']
                        or entity['source_field_id'] not in {field['field_id'] for field in record['fields']}):
                    raise RecordRejected('PROFESSIONAL_ENTITY_CONTEXT_INVALID')
        if any(item.get('confirmed_by') is not None or item.get('confirmed_at') is not None
                for item in case['image_links']) or any(item.get('resolved_by') is not None or item.get('resolved_at') is not None
                for item in case['quality_issues']):
            raise RecordRejected('PROFESSIONAL_RECORD_AUTHORITY_INVALID')
    return dataset, record


def _target(operation, target):
    pattern = (rf'/api/(?:v1/)?support/patients/({UUID_PATTERN})/records' if operation == 'professional_record_create'
        else rf'/api/(?:v1/)?support/records/({UUID_PATTERN})/corrections')
    match = re.fullmatch(pattern, target) if isinstance(target, str) else None
    if match is None:
        raise RecordRejected('PROFESSIONAL_TARGET_INVALID')
    if operation == 'professional_record_create':
        return Patient.objects.get(id=match[1]), None
    record = Record.objects.get(id=match[1])
    return record.patient, record


def _lock_session(actor, auth_epoch, audience):
    # All entrypoints share this prefix before reading mutable intent state.
    # A competing correction must observe the committed receipt after waiting.
    if audience != 'support':
        raise RecordRejected('SUPPORT_AUDIENCE_REQUIRED')
    gate = EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
    actor = Principal.objects.select_for_update().get(id=actor.id)
    check_principal_session(actor, auth_epoch)
    if not gate.enabled:
        raise RecordRejected('PROFESSIONAL_ENVIRONMENT_UNAVAILABLE')


def professional_scope(*, actor, auth_epoch, audience, operation, target, payload, replay=False):
    if audience != 'support':
        raise RecordRejected('SUPPORT_AUDIENCE_REQUIRED')
    dataset, incoming = professional_payload(operation, payload)
    patient, record = _target(operation, target)
    gate = EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
    actor = Principal.objects.select_for_update().get(id=actor.id)
    check_principal_session(actor, auth_epoch)
    patient = Patient.objects.select_for_update().get(id=patient.id)
    if not gate.enabled or patient.status != 'active' or dataset['environment'] != settings.SJIA_ENVIRONMENT:
        raise RecordRejected('PROFESSIONAL_ENVIRONMENT_UNAVAILABLE')
    if incoming['patient_id'] != str(patient.id) or incoming['author_id'] != str(actor.id):
        raise RecordRejected('PROFESSIONAL_IDENTITY_MISMATCH')
    authorization = DataUseAuthorization.objects.select_for_update().get(id=incoming['use_authorization_id'], patient=patient)
    revision = SourceRevision.objects.select_for_update().get(id=incoming['source_revision_id'])
    source = SourceRegistry.objects.select_for_update().get(id=revision.source_id)
    approver = Principal.objects.select_for_update().get(id=authorization.approved_by_id)
    now = timezone.now()
    if (authorization.source_revision_id != revision.id or authorization.environment != settings.SJIA_ENVIRONMENT
            or authorization.revoked_at is not None or authorization.approved_at > now
            or authorization.valid_until is not None and authorization.valid_until <= now
            or not authorization.evidence_ref.strip() or approver.account_status != 'active'
            or revision.status != 'approved' or source.status != 'approved' or not revision.rights_evidence_ref
            or not revision.rights_evidence_ref.strip() or not isinstance(revision.allowed_use, list)
            or authorization.purpose not in revision.allowed_use or authorization.purpose not in dataset['allowed_use']):
        raise RecordRejected('PROFESSIONAL_SOURCE_SCOPE_DENIED')
    declared = dataset['sources'][0]
    expected_source = {'id': str(revision.id), 'source_id': str(source.id), 'owner': source.owner,
        'location': source.location, 'purpose': source.purpose, 'license': revision.license,
        'allowed_use': revision.allowed_use, 'sensitivity': source.sensitivity, 'update_frequency': source.update_frequency,
        'version': revision.version, 'checksum': revision.checksum, 'retention_policy_id': str(revision.retention_policy_id),
        'rights_evidence_ref': revision.rights_evidence_ref, 'status': revision.status}
    if declared != expected_source:
        raise RecordRejected('PROFESSIONAL_SOURCE_DECLARATION_MISMATCH')
    policy = RetentionPolicy.objects.select_for_update().get(id=incoming['retention_policy_id'])
    if (policy.status != 'active' or policy.approved_at is None or policy.approved_at > now
            or policy.approved_by_id is None or not policy.evidence_ref or not policy.evidence_ref.strip()
            or policy.id != revision.retention_policy_id or policy.data_class not in (incoming['kind'], 'clinical', 'clinical_record')
            or Principal.objects.select_for_update().get(id=policy.approved_by_id).account_status != 'active'):
        raise RecordRejected('PROFESSIONAL_RETENTION_UNAVAILABLE')
    from records.admission import checked_retention
    policy = checked_retention(policy, data_classes=(incoming['kind'], 'clinical', 'clinical_record'))
    if record is not None:
        record = Record.objects.select_for_update().get(id=record.id, patient=patient)
        if (record.status != 'active' or incoming['record_id'] != str(record.id) or incoming['kind'] != record.kind
                or not replay and record.current_version != int(payload['expected_version'])):
            raise RecordRejected('PROFESSIONAL_VERSION_CONFLICT')
        previous = RecordVersion.objects.get(record_id=record.id, patient=patient, version=int(payload['expected_version']))
        if previous.origin_type != 'authorized_clinical' or previous.source_revision_id != revision.id:
            raise RecordRejected('PROFESSIONAL_SOURCE_CHANGED')
        author_id = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=record.id, patient=patient, version=1).author_id
    else:
        previous, author_id = None, actor.id
    for field in incoming['fields']:
        lock_case_permissions(actor, patient, 'write_professional', authorization.purpose, now,
            record_id=record.id if record else None, author_id=author_id, field_codes=[field['field_code']])
    if previous is not None:
        prior_fields = list(RecordField.objects.filter(record_version_id=previous.id, patient=patient).order_by('ordinal'))
        if len(prior_fields) != len(incoming['fields']):
            raise RecordRejected('PROFESSIONAL_CORRECTION_FIELD_MISMATCH')
        for old, new in zip(prior_fields, incoming['fields'], strict=True):
            if (new['source_reference']['anchor_id'] != (str(old.anchor_id) if old.anchor_id else None)
                    or new['dictionary_entry_id'] != (str(old.dictionary_entry_id) if old.dictionary_entry_id else None)):
                raise RecordRejected('PROFESSIONAL_CORRECTION_SOURCE_CHANGED')
    originals = {}
    for field in incoming['fields']:
        ref = field['source_reference']
        if ref['file_id']:
            original = lock_original(ref['file_id'])
            if original.patient_id != patient.id:
                raise RecordRejected('PROFESSIONAL_SOURCE_SCOPE_DENIED')
            lock_file_access(original, actor, auth_epoch, purpose=authorization.purpose)
            lock_use_authorization(authorization.id, original, authorization.purpose, settings.SJIA_ENVIRONMENT)
            checked_bytes(original)
            page = FilePage.objects.get(id=ref['page_id'], file_id=original.id, patient_id=patient.id)
            SourceAnchor.objects.get(id=ref['anchor_id'], page_id=page.id, patient_id=patient.id)
            originals[ref['file_id']] = original
    extra_files = set()
    if dataset.get('case_details'):
        extra_files.update(item['file_id'] for item in dataset['case_details']['image_links'])
        extra_files.update(ref['file_id'] for item in dataset['case_details']['quality_issues']
            for ref in item['references'] if ref['file_id'])
    for file_id in extra_files - set(originals):
        original = lock_original(file_id)
        if original.patient_id != patient.id:
            raise RecordRejected('PROFESSIONAL_SOURCE_SCOPE_DENIED')
        lock_file_access(original, actor, auth_epoch, purpose=authorization.purpose)
        lock_use_authorization(authorization.id, original, authorization.purpose, settings.SJIA_ENVIRONMENT)
        checked_bytes(original)
        originals[file_id] = original
    metadata = incoming['report']
    if metadata:
        groups = {str(item.file_group_id) for item in originals.values()}
        if (len(groups) > 1 or metadata['file_group_id'] != (next(iter(groups)) if groups else None)):
            raise RecordRejected('PROFESSIONAL_REPORT_GROUP_MISMATCH')
        if metadata['replaces_report_id'] is not None:
            Report.objects.get(id=metadata['replaces_report_id'], patient=patient, source_revision=revision)
    if previous is not None:
        old_files = {str(value) for value in RecordAttachment.objects.filter(record_version_id=previous.id,
            patient_id=patient.id).values_list('file_id', flat=True)}
        if old_files != set(originals):
            raise RecordRejected('PROFESSIONAL_CORRECTION_SOURCE_CHANGED')
    return SimpleNamespace(actor=actor, patient=patient, record=record, previous=previous, dataset=dataset,
        incoming=incoming, revision=revision, authorization=authorization, policy=policy, originals=originals)


def _time(value):
    if value is None:
        return None
    value = {key: item for key, item in value.items() if key not in ('id', 'patient_id')}
    if value['resolution_status'] == 'resolved':
        claimed = value['instant']
        computed = time_values({**value, 'resolution_status': 'local_only'})
        from django.utils.dateparse import parse_datetime
        if computed['instant'] != parse_datetime(claimed):
            raise RecordRejected('PROFESSIONAL_TIME_MISMATCH')
        # The shared writer derives the same instant from explicit zone/offset.
        value['resolution_status'] = 'local_only'
        value['instant'] = None
    return value


def _fields(scope):
    return [{**{key: field[key] for key in FIELD_KEYS}, 'medical_time': _time(field['medical_time']),
        'anchor_id': field['source_reference']['anchor_id']} for field in scope.incoming['fields']]


def _domain_payload(scope, identity_map):
    """Every domain reference resolves to this input's new entities or authorized original."""
    dataset, incoming = scope.dataset, scope.incoming
    case = copy.deepcopy(dataset.get('case_details'))
    if case is None:
        return None
    allowed_external = {str(scope.patient.id): str(scope.patient.id), **{key: key for key in scope.originals}}
    mapping = {**allowed_external, **identity_map}
    def collect(value):
        if isinstance(value, dict):
            if 'id' in value:
                if value['id'] in mapping:
                    raise RecordRejected('PROFESSIONAL_ENTITY_ID_DUPLICATE')
                mapping[value['id']] = str(uuid4())
            for item in value.values(): collect(item)
        elif isinstance(value, list):
            for item in value: collect(item)
    for group in case.values():
        collect(group)
    for study in case['imaging_studies']:
        if study['report_id'] not in mapping:
            mapping[study['report_id']] = str(uuid4())
    for value in dataset.get('medical_times', []):
        if value['patient_id'] != str(scope.patient.id):
            raise RecordRejected('PROFESSIONAL_TIME_PATIENT_MISMATCH')
        if value['id'] in mapping:
            # Inline field/report times may also be listed in the exchange directory.
            existing = MedicalTime.objects.get(id=mapping[value['id']], patient=scope.patient)
            expected = time_values(_time(value))
            if any(getattr(existing, key) != item for key, item in expected.items()):
                raise RecordRejected('PROFESSIONAL_TIME_MISMATCH')
        else:
            row = MedicalTime.objects.create(patient=scope.patient, **time_values(_time(value)))
            mapping[value['id']] = str(row.id)
    def remap(value):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if (key == 'id' or key.endswith('_id')) and item is not None:
                    if item not in mapping:
                        raise RecordRejected('PROFESSIONAL_ENTITY_REFERENCE_INVALID')
                    result[key] = mapping[item]
                else:
                    result[key] = remap(item)
            return result
        if isinstance(value, list): return [remap(item) for item in value]
        return value
    result = remap(case)
    identity_map.update({key: value for key, value in mapping.items() if key not in allowed_external})
    return result


def _apply(scope, operation, payload):
    incoming = scope.incoming
    metadata = incoming['report']
    report_input = ({key: metadata[key] for key in ('external_ref', 'report_status')}
        | {key: _time(metadata[key]) for key in ('sample_time', 'result_time')}) if metadata else None
    if operation == 'professional_record_create':
        result = create_record(patient=scope.patient, actor=scope.actor, kind=incoming['kind'],
            capture_method='authorized_import', origin_type='authorized_clinical', retention_policy=scope.policy,
            use_authorization_id=scope.authorization.id, source_revision=scope.revision,
            fields=_fields(scope), file_ids=list(scope.originals), report_metadata=report_input,
            source_organization=metadata['source_organization'] if metadata else None)
    else:
        result = correct_record(record=scope.record, expected_version=int(payload['expected_version']), actor=scope.actor,
            reason=payload['reason'], fields=_fields(scope), use_authorization_id=scope.authorization.id,
            retention_policy=scope.policy, capture_method='authorized_import', source_revision=scope.revision,
            origin_type='authorized_clinical')
    version = RecordVersion.objects.get(id=result['record_version_id'], patient=scope.patient)
    saved_fields = list(RecordField.objects.filter(record_version_id=version.id, patient=scope.patient).order_by('ordinal'))
    mapping = {incoming['record_id']: result['record_id'], incoming['record_version_id']: result['record_version_id']}
    for original, saved in zip(incoming['fields'], saved_fields, strict=True):
        mapping[original['field_id']] = str(saved.id)
        saved.quality_reasons = original['quality_reasons']
        saved.save(update_fields=['quality_reasons'])
        if original['medical_time'] and original['medical_time'].get('id') and saved.medical_time_id:
            mapping[original['medical_time']['id']] = str(saved.medical_time_id)
    if metadata is not None:
        if operation == 'professional_record_correct' or version.report_id is None:
            from records.service import _create_medical_time
            sample = _create_medical_time(scope.patient, report_input['sample_time'])
            result_time = _create_medical_time(scope.patient, report_input['result_time'])
            saved_report = Report.objects.create(patient=scope.patient, source_revision=scope.revision,
                external_ref=metadata['external_ref'], source_organization=metadata['source_organization'],
                report_status=metadata['report_status'], sample_time_id=sample.id if sample else None,
                result_time_id=result_time.id if result_time else None, file_group_id=metadata['file_group_id'],
                replaces_report_id=metadata['replaces_report_id'], created_at=timezone.now())
            version.report_id = saved_report.id
            version.save(update_fields=['report_id'])
        else:
            saved_report = Report.objects.get(id=version.report_id, patient=scope.patient)
            saved_report.replaces_report_id = metadata['replaces_report_id']
            saved_report.save(update_fields=['replaces_report_id'])
        mapping[metadata['report_id']] = str(saved_report.id)
        for key in ('sample_time', 'result_time'):
            if metadata[key] and metadata[key].get('id') and getattr(saved_report, f'{key}_id'):
                mapping[metadata[key]['id']] = str(getattr(saved_report, f'{key}_id'))
    case = _domain_payload(scope, mapping)
    if case is not None:
        from case_details.domain_import import import_domain_entities
        import_domain_entities(case=case, patient=scope.patient, source_revision=scope.revision,
            original_files=scope.originals, now=timezone.now(),
            existing_report_ids={version.report_id} if version.report_id else set())
    version.quality_status = 'pending'
    version.quality_reasons = []
    version.save(update_fields=['quality_status', 'quality_reasons'])
    return result, mapping


@transaction.atomic
def prepare_professional_intent(*, actor, auth_epoch, audience, data):
    if (not isinstance(data, dict) or set(data) != {'operation_id', 'target_key', 'request_id', 'schema_version', 'payload'}
            or data['schema_version'] != '1.0.0'):
        raise RecordRejected('PROFESSIONAL_INTENT_INVALID')
    try:
        if not isinstance(data['request_id'], str) or str(UUID(data['request_id'])) != data['request_id']:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise RecordRejected('PROFESSIONAL_INTENT_INVALID') from None
    _lock_session(actor, auth_epoch, audience)
    existing = WriteIntent.objects.filter(actor=actor, operation_id=data['operation_id'], target_key=data['target_key'], request_id=data['request_id']).first()
    scope = professional_scope(actor=actor, auth_epoch=auth_epoch, audience=audience, operation=data['operation_id'],
        target=data['target_key'], payload=data['payload'], replay=existing is not None and existing.status == 'committed')
    if existing is not None:
        existing = WriteIntent.objects.select_for_update().get(id=existing.id)
        if existing.payload_sha256 != compute_digest(data['operation_id'], data['target_key'], '1.0.0', data['payload']):
            raise RecordRejected('PROFESSIONAL_INTENT_MISMATCH')
        return existing
    return prepare_intent(actor=scope.actor, patient=scope.patient, operation_id=data['operation_id'], target_key=data['target_key'],
        request_id=UUID(data['request_id']), payload=data['payload'], schema_version='1.0.0', retention_policy=scope.policy,
        recovery_payload=data['payload'])


def _receipt(intent):
    row = intent.write_request
    if (row is None or row.status != 'committed' or row.patient_id != intent.patient_id or row.actor_id != intent.actor_id
            or row.action != intent.operation_id or row.target_key != intent.target_key or row.request_id != intent.request_id
            or row.payload_sha256 != intent.payload_sha256 or row.resource_type != 'record_version' or row.completed_at is None):
        raise RecordRejected('PROFESSIONAL_RECEIPT_INVALID')
    version = RecordVersion.objects.get(id=row.resource_id, patient_id=intent.patient_id)
    return {'resource_type': 'record_version', 'resource_id': str(version.id), 'version': str(version.version),
        'saved_at': row.completed_at.isoformat(), 'request_id': str(row.request_id)}


@transaction.atomic
def submit_professional_record(*, actor, auth_epoch, audience, operation, target, payload, headers):
    try:
        for key in ('X-Write-Intent', 'Idempotency-Key'):
            if not isinstance(headers.get(key), str) or str(UUID(headers[key])) != headers[key]:
                raise ValueError()
        if not isinstance(headers.get('X-Intent-Generation'), str) or not re.fullmatch(r'[1-9][0-9]{0,18}', headers['X-Intent-Generation']):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise RecordRejected('PROFESSIONAL_HEADERS_INVALID') from None
    _lock_session(actor, auth_epoch, audience)
    initial = WriteIntent.objects.get(id=headers.get('X-Write-Intent'), actor=actor, operation_id=operation, target_key=target)
    scope = professional_scope(actor=actor, auth_epoch=auth_epoch, audience=audience, operation=operation, target=target,
        payload=payload, replay=initial.status == 'committed')
    intent = WriteIntent.objects.select_for_update().get(id=initial.id)
    if (intent.patient_id != scope.patient.id or str(intent.generation) != headers.get('X-Intent-Generation')
            or str(intent.request_id) != headers.get('Idempotency-Key') or intent.recovery_payload != payload
            or intent.payload_sha256 != compute_digest(operation, target, '1.0.0', payload)):
        raise RecordRejected('PROFESSIONAL_INTENT_MISMATCH')
    if intent.status == 'committed':
        return _receipt(intent)
    if intent.status != 'prepared' or intent.expires_at <= timezone.now():
        raise RecordRejected('PROFESSIONAL_INTENT_EXPIRED')
    result, mapping = _apply(scope, operation, payload)
    commit_intent(intent=intent, action=operation, resource_type='record_version', resource_id=result['record_version_id'],
        expected_version=int(payload['expected_version']) if operation == 'professional_record_correct' else None)
    intent.authorization_context = {'identity_map': mapping}
    intent.save(update_fields=['authorization_context'])
    return _receipt(intent)


@transaction.atomic
def professional_request_view(*, actor, auth_epoch, audience, intent):
    _lock_session(actor, auth_epoch, audience)
    intent = WriteIntent.objects.get(id=intent.id, actor=actor)
    professional_scope(actor=actor, auth_epoch=auth_epoch, audience=audience, operation=intent.operation_id,
        target=intent.target_key, payload=intent.recovery_payload, replay=intent.status == 'committed')
    intent = WriteIntent.objects.select_for_update().get(id=intent.id, actor=actor)
    rows = WriteRequest.objects.filter(actor=actor, action=intent.operation_id, target_key=intent.target_key, request_id=intent.request_id)
    if intent.status != 'committed' and (intent.write_request_id or rows.exists()):
        raise RecordRejected('PROFESSIONAL_RECEIPT_RECONCILIATION_REQUIRED')
    return {'patient_id': str(intent.patient_id), 'intent_id': str(intent.id), 'request_id': str(intent.request_id),
        'operation_id': intent.operation_id, 'target_key': intent.target_key, 'status': intent.status,
        'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(),
        'receipt': _receipt(intent) if intent.status == 'committed' else None,
        'may_retry': intent.status != 'committed' and (intent.status == 'failed' or intent.expires_at <= timezone.now()),
        'professional_input': intent.recovery_payload,
        'identity_map': (intent.authorization_context or {}).get('identity_map', {})}


@transaction.atomic
def retry_professional_intent(*, actor, auth_epoch, audience, intent, expected_generation):
    view = professional_request_view(actor=actor, auth_epoch=auth_epoch, audience=audience, intent=intent)
    intent.refresh_from_db()
    if not view['may_retry'] or str(intent.generation) != str(expected_generation) or intent.generation >= 9223372036854775807:
        raise RecordRejected('PROFESSIONAL_RETRY_NOT_AVAILABLE')
    intent.generation += 1
    intent.status, intent.error_code, intent.completed_at = 'prepared', None, None
    intent.prepared_at = timezone.now()
    intent.expires_at = intent.prepared_at + timedelta(seconds=300)
    intent.save(update_fields=['generation', 'status', 'error_code', 'completed_at', 'prepared_at', 'expires_at'])
    return intent
