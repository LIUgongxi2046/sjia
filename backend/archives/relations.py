from itertools import combinations
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from api.base import ApiError, authorize_case, authorize_service, current_principal
from archives.models import ArchiveFact, ArchiveFactVersion, ArchiveRelationVersion
from archives.queries import fact_read, fact_source
from archives.relation_schemas import checked_relation
from archives.sampling import sampling_groups
from assessment.policies import QualificationPolicy
from assessment.provenance import entity_reference
from assessment.qualified_facts import lock_case_permissions, qualified_configuration
from authorization.policy import matching_case_grants, check_service_grant
from configurations.evidence import inspect_approved_evidence
from configurations.service import compute_checksum
from files.models import FileGroup, FileObject
from files.purpose import lock_original, checked_bytes
from files.purpose_dependencies import resource_node, connect, invalidate_resource
from identity.models import Patient
from records.models import Record, RecordVersion, Report
from verification.models import Verification, VerificationField


SAMPLING_TYPES = ('same_sampling', 'independent_sampling', 'formal_correction')


def current_relation(patient, relation_id, *, lock=True):
    query = ArchiveRelationVersion.objects.filter(patient_id=patient.id, relation_id=relation_id)
    if lock:
        query = query.select_for_update()
    row = query.order_by('-version').first()
    if row is None:
        raise ApiError('ARCHIVE_RELATION_NOT_FOUND', 404)
    return row


def relation_fields(row):
    return {key: str(getattr(row, key)) if getattr(row, key) is not None else None for key in (
        'left_fact_version_id', 'right_fact_version_id', 'left_file_id', 'right_file_id')} | {
        'relation_type': row.relation_type, 'status': row.status, 'evidence_ref': row.evidence_ref, 'reason': row.reason}


def source_scope(request, actor, patient, kind, identity, action, *, require_current=False):
    _, _, audience = current_principal(request)
    family = audience == 'family'
    if kind == 'fact':
        fact = ArchiveFactVersion.objects.get(id=identity, patient_id=patient.id)
        field, version = fact_source(fact, patient)
        original = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=version.record_id, patient_id=patient.id, version=1)
        authorize_case(request, actor, patient, action if family else 'read_case',
            record_id=version.record_id, author_id=original.author_id, field_codes=[field.field_code], require_relation=family)
        if require_current:
            archive_fact = ArchiveFact.objects.get(id=fact.fact_id, patient_id=patient.id)
            record = Record.objects.get(id=version.record_id, patient_id=patient.id)
            if archive_fact.current_version != fact.version or record.current_version != version.version or record.status != 'active':
                raise ApiError('ARCHIVE_RELATION_SOURCE_CHANGED', 409)
        return fact, field, version
    source = FileObject.objects.get(id=identity, patient_id=patient.id)
    group = FileGroup.objects.get(id=source.file_group_id, patient_id=patient.id)
    authorize_case(request, actor, patient, 'read_file', author_id=group.owner_id, require_relation=family, whole_resource=True)
    if action in ('write_record', 'correct_record'):
        authorize_case(request, actor, patient, action, author_id=group.owner_id, require_relation=family, whole_resource=True)
    if require_current:
        source = lock_original(source.id)
        if group.status != 'available':
            raise ApiError('ARCHIVE_RELATION_SOURCE_CHANGED', 409)
        checked_bytes(source)
    return source, group


def relation_materials(request, actor, patient, payload, previous=None, *, check_current=True):
    pairs = [payload['left_fact_version_id'], payload['right_fact_version_id']], [payload['left_file_id'], payload['right_file_id']]
    facts, files = pairs
    if bool(all(facts)) == bool(all(files)) or any(facts) != all(facts) or any(files) != all(files):
        raise ApiError('ARCHIVE_RELATION_SOURCE_INVALID', 422)
    kind, identities = ('fact', facts) if all(facts) else ('file', files)
    if identities[0] == identities[1] or (kind == 'file' and payload['relation_type'] in SAMPLING_TYPES):
        raise ApiError('ARCHIVE_RELATION_SOURCE_INVALID', 422)
    if kind == 'fact' and payload['relation_type'] in ('same_bytes', 'document_copy'):
        raise ApiError('ARCHIVE_RELATION_SOURCE_INVALID', 422)
    if previous is not None:
        saved = relation_fields(previous)
        if any(saved[key] != payload[key] for key in ('left_fact_version_id', 'right_fact_version_id', 'left_file_id', 'right_file_id')):
            raise ApiError('ARCHIVE_RELATION_SOURCE_CHANGED', 409)
    check_current = check_current and payload['status'] != 'released'
    values = [source_scope(request, actor, patient, kind, identity, 'write_record',
        require_current=check_current and payload['relation_type'] != 'formal_correction') for identity in identities]
    if kind == 'fact':
        if values[0][1].field_code != values[1][1].field_code and payload['relation_type'] in SAMPLING_TYPES:
            raise ApiError('ARCHIVE_RELATION_FIELD_MISMATCH', 422)
        if check_current and payload['relation_type'] == 'formal_correction':
            left, right = values[0][2], values[1][2]
            if left.record_id != right.record_id or left.version >= right.version or right.supersedes_version is None:
                raise ApiError('ARCHIVE_RELATION_CORRECTION_INVALID', 422)
            source_scope(request, actor, patient, kind, identities[1], 'write_record', require_current=True)
        retention = values[0][2].retention_policy
    else:
        retention = values[0][0].retention_policy
        if check_current and payload['status'] == 'confirmed' and payload['relation_type'] == 'same_bytes':
            left, right = values[0][0], values[1][0]
            if left.checksum != right.checksum or checked_bytes(left) != checked_bytes(right):
                raise ApiError('ARCHIVE_RELATION_BYTES_MISMATCH', 422)
    for group in values:
        policy = group[2].retention_policy if kind == 'fact' else group[0].retention_policy
        if policy.status != 'active' or policy.approved_at is None or policy.approved_by_id is None or not policy.evidence_ref.strip():
            raise ApiError('ARCHIVE_RELATION_RETENTION_UNAVAILABLE', 409)
    return kind, values, retention


def relation_view(row, *, allowed_actions=()):
    value = {'relation_id': str(row.relation_id), 'patient_id': str(row.patient_id), 'version_id': str(row.id),
        'version': str(row.version), **relation_fields(row),
        'verification_id': str(row.verification_id) if row.verification_id else None, 'allowed_actions': list(allowed_actions)}
    from archives.inputs import REGISTRY, PROTOCOL
    from jsonschema import Draft202012Validator, FormatChecker
    Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/RelationView'}, registry=REGISTRY, format_checker=FormatChecker()).validate(value)
    return value


def fact_source_view(fact, patient):
    field, version = fact_source(fact, patient)
    view = fact_read(fact=ArchiveFact.objects.get(id=fact.fact_id, patient_id=patient.id), patient=patient, version=fact)
    report = Report.objects.get(id=version.report_id, patient_id=patient.id) if version.report_id else None
    return {'kind': 'fact', 'id': str(fact.id), 'label': f'{field.original_name or field.field_code} · 记录版本 {version.version}',
        'record_id': str(version.record_id), 'record_version_id': str(version.id), 'field_code': field.field_code,
        'report_identifier': report.external_ref if report else None, 'medical_time': view['context']['medical_time'],
        'source': view['source'], 'object_version': None, 'checksum': None}


def file_source_view(source):
    return {'kind': 'file', 'id': str(source.id), 'label': f'{source.media_type} · {source.size_bytes} bytes · {source.id}',
        'record_id': None, 'record_version_id': None, 'field_code': None, 'report_identifier': None,
        'medical_time': None, 'source': None, 'object_version': source.object_version, 'checksum': source.checksum}


def discover_candidates(sources):
    result = []
    for left, right in combinations(sources, 2):
        if left['kind'] != right['kind']:
            continue
        kinds, reasons = [], []
        if left['kind'] == 'file' and left['checksum'] == right['checksum']:
            kinds, reasons = ['same_bytes'], ['FILE_CHECKSUM_MATCH_REQUIRES_CONFIRMATION']
        elif left['kind'] == 'fact' and left['field_code'] == right['field_code']:
            if left['record_id'] == right['record_id']:
                kinds, reasons = ['formal_correction'], ['RECORD_VERSION_RELATION_REQUIRES_CONFIRMATION']
            else:
                kinds, reasons = ['same_sampling', 'independent_sampling'], ['SAMPLING_IDENTITY_REQUIRES_CONFIRMATION']
                if left['report_identifier'] and left['report_identifier'] == right['report_identifier']:
                    reasons.append('REPORT_IDENTIFIER_MATCH')
                if left['medical_time'] and right['medical_time'] and all(left['medical_time'][key] == right['medical_time'][key]
                        for key in left['medical_time'] if key not in ('id', 'patient_id')):
                    reasons.append('MEDICAL_TIME_MATCH')
        if kinds:
            result.append({'source_kind': left['kind'], 'left_id': left['id'], 'right_id': right['id'],
                'suggested_types': kinds, 'reason_codes': reasons, 'status': 'proposed'})
    return result


def relation_actions(request, actor, patient, row, audience):
    if audience == 'support':
        return ['archive_relation_verify'] if row.status == 'confirmed' and row.relation_type in SAMPLING_TYPES else []
    kind = 'fact' if row.left_fact_version_id else 'file'
    for identity in ([row.left_fact_version_id, row.right_fact_version_id] if kind == 'fact' else [row.left_file_id, row.right_file_id]):
        if kind == 'fact':
            fact = ArchiveFactVersion.objects.get(id=identity, patient_id=patient.id)
            field, version = fact_source(fact, patient)
            original = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=version.record_id, patient_id=patient.id, version=1)
            scope = {'record_id': version.record_id, 'author_id': original.author_id, 'field_codes': [field.field_code]}
        else:
            source = FileObject.objects.get(id=identity, patient_id=patient.id)
            group = FileGroup.objects.get(id=source.file_group_id, patient_id=patient.id)
            scope = {'author_id': group.owner_id, 'whole_resource': True}
        if not any(matching_case_grants(actor, patient, 'write_record', timezone.now(), require_relation=True, **scope)):
            return []
    return ['archive_relation_update']


def relation_verification(request, actor, patient, previous, payload):
    authorize_service(request, actor, 'verify_field')
    if previous.status != 'confirmed' or previous.relation_type not in SAMPLING_TYPES:
        raise ApiError('ARCHIVE_RELATION_VERIFICATION_SCOPE_DENIED', 403)
    proof = Verification.objects.select_for_update().get(id=payload['verification_id'], patient_id=patient.id)
    if proof.verifier_id != actor.id or proof.revoked_at is not None or proof.evidence_ref != payload['evidence_ref'] or proof.verified_at > timezone.now():
        raise ApiError('ARCHIVE_RELATION_VERIFICATION_CHANGED', 409)
    for point in (proof.verified_at, timezone.now()):
        check_service_grant(actor, 'verify_field', settings.SJIA_ENVIRONMENT, point)
    policy = qualified_configuration(proof.policy_id, kind='verification', schema_version='qualification-policy-2',
        environment=settings.SJIA_ENVIRONMENT, known_at=timezone.now(), mode='current')
    definition = QualificationPolicy.model_validate(policy.payload)
    evidence, _, reason = inspect_approved_evidence(payload['evidence_ref'], purpose=definition.verification_evidence_purpose, lock=True)
    if reason:
        raise ApiError(reason, 409)
    if proof.purpose != definition.purpose or proof.evidence_source_id != evidence.id or proof.purpose not in evidence.allowed_use:
        raise ApiError('ARCHIVE_RELATION_VERIFICATION_SCOPE_DENIED', 403)
    fields = []
    for identity in (previous.left_fact_version_id, previous.right_fact_version_id):
        fact, field, version = source_scope(request, actor, patient, 'fact', identity, 'read_case',
            require_current=previous.relation_type != 'formal_correction' or identity == previous.right_fact_version_id)
        original = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=version.record_id, patient_id=patient.id, version=1)
        for point in (proof.verified_at, timezone.now()):
            lock_case_permissions(actor, patient, 'verify_field', proof.purpose, point,
                record_id=version.record_id, author_id=original.author_id, field_codes=[field.field_code])
        fields.append(field.id)
    if not VerificationField.objects.filter(verification_id=proof.id, patient_id=patient.id,
            field_id__in=fields, result='consistent').exists():
        raise ApiError('ARCHIVE_RELATION_VERIFICATION_CHANGED', 409)
    return proof


@transaction.atomic
def save_relation(*, request, actor, patient, payload, previous=None, verification=None):
    patient = Patient.objects.select_for_update().get(id=patient.id)
    if previous is not None:
        current = current_relation(patient, previous.relation_id)
        if current.id != previous.id or str(current.version) != payload['expected_version']:
            raise ApiError('VERSION_CONFLICT', 409)
    if verification is None:
        relation_materials(request, actor, patient, payload, previous)
        values = {key: payload[key] for key in relation_fields(previous).keys()} if previous else {
            key: payload[key] for key in ('left_fact_version_id', 'right_fact_version_id', 'left_file_id', 'right_file_id', 'relation_type', 'status', 'evidence_ref', 'reason')}
    else:
        values = relation_fields(previous) | {'evidence_ref': payload['evidence_ref'], 'reason': payload['reason']}
    if previous is None and values['status'] == 'released':
        raise ApiError('ARCHIVE_RELATION_STATE_INVALID', 422)
    kind = 'fact' if values['left_fact_version_id'] else 'file'
    left, right = values[f'left_{kind}_version_id' if kind == 'fact' else 'left_file_id'], values[f'right_{kind}_version_id' if kind == 'fact' else 'right_file_id']
    latest = {}
    for row in ArchiveRelationVersion.objects.filter(patient_id=patient.id).order_by('relation_id', '-version'):
        latest.setdefault(row.relation_id, row)
    for row in latest.values():
        existing = {str(row.left_fact_version_id), str(row.right_fact_version_id)} if kind == 'fact' else {str(row.left_file_id), str(row.right_file_id)}
        if row.relation_id != (previous.relation_id if previous else None) and existing == {str(left), str(right)} and row.status != 'released' and values['status'] != 'released':
            raise ApiError('ARCHIVE_RELATION_ALREADY_EXISTS', 409)
    if kind == 'fact' and values['status'] == 'confirmed':
        rows = [row for row in latest.values() if row.status == 'confirmed'
            and row.relation_id != (previous.relation_id if previous else None) and row.relation_type in SAMPLING_TYPES]
        same = [(str(row.left_fact_version_id), str(row.right_fact_version_id)) for row in rows if row.relation_type == 'same_sampling']
        independent = [(str(row.left_fact_version_id), str(row.right_fact_version_id)) for row in rows if row.relation_type == 'independent_sampling']
        if values['relation_type'] == 'same_sampling':
            same.append((str(left), str(right)))
        if values['relation_type'] == 'independent_sampling':
            independent.append((str(left), str(right)))
        groups = sampling_groups({identity for pair in same + independent for identity in pair}, same)
        if any(any(a in group and b in group for group in groups) for a, b in independent):
            raise ApiError('ARCHIVE_RELATION_SAMPLING_CONFLICT', 409)
    saved = ArchiveRelationVersion.objects.create(patient_id=patient.id, relation_id=previous.relation_id if previous else uuid4(),
        version=previous.version + 1 if previous else 1, **values, author_id=actor.id, created_at=timezone.now(),
        verification_id=verification.id if verification else None, supersedes_version=previous.version if previous else None)
    node = resource_node('archive_relation_version', saved.id, entity_reference(saved)['sha256'], patient.id)
    if kind == 'fact':
        for identity in (saved.left_fact_version_id, saved.right_fact_version_id):
            fact = ArchiveFactVersion.objects.get(id=identity, patient_id=patient.id)
            connect(node, resource_node('archive_fact_version', fact.id, entity_reference(fact)['sha256'], patient.id), 'source')
    else:
        for identity in (saved.left_file_id, saved.right_file_id):
            source = FileObject.objects.get(id=identity, patient_id=patient.id)
            connect(node, resource_node('file_object', source.id, source.object_version, patient.id), 'source')
    if verification:
        connect(node, resource_node('verification', verification.id, entity_reference(verification)['sha256'], patient.id), 'verification')
    Patient.objects.filter(id=patient.id).update(data_epoch=F('data_epoch') + 1)
    if previous:
        invalidate_resource('archive_relation_version', previous.id)
    return saved
