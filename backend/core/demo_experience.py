"""A bounded, software-only family experience dataset for an existing account.

This module never reads or writes credentials or sessions. It is deliberately
unavailable outside a DEBUG test_* database in an explicit software-test environment. Clinical
approval, external providers and clinical results are not provisioned here.
"""
import copy
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5

from django.conf import settings
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.utils import timezone

from authorization.models import AccessGrant, ServiceRoleGrant
from configurations.evidence import artifact_verification_scope, inspect_approved_evidence
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.schemas import validate_payload
from configurations.service import compute_checksum
from family_content.models import EnvironmentGate
from family_content.qualification import release_qualification_reason
from identity.models import Patient, Principal, PrincipalIdentity
from relations.models import Relation, RelationApplication
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision


NAMESPACE = UUID('91752ecc-4d74-5c77-a976-f0108cf0fce4')
KEY = 'family-experience-v1'
RIGHTS = 'user-authorized-fictional-family-experience-only'
SOFTWARE_ENVIRONMENTS = frozenset({'test', 'synthetic-browser-demo'})
FAMILY_ACTIONS = ('read_raw', 'write_record', 'read_history', 'read_file', 'manage_draft',
    'receive_alert', 'create_summary', 'read_summary', 'request_correction',
    'request_disposition', 'view_request', 'correct_record')


def identity(key):
    return uuid5(NAMESPACE, key)


def require_test_context(*, acknowledge_test_only):
    if (acknowledge_test_only is not True or settings.SJIA_ENVIRONMENT not in SOFTWARE_ENVIRONMENTS
            or not settings.DEBUG or connection.vendor != 'postgresql'
            or not str(connection.settings_dict['NAME']).startswith('test_')):
        raise CommandError('DEMO_REQUIRES_EXPLICIT_DEBUG_TEST_DATABASE')
    maximum = settings.CONFIG_EVIDENCE_MAX_BYTES
    if type(maximum) is not int or maximum < 1:
        raise CommandError('DEMO_REQUIRES_EXPLICIT_EVIDENCE_SIZE_LIMIT')
    root = Path(settings.CONFIG_EVIDENCE_ROOT or '')
    if not root.is_absolute() or not root.is_dir():
        raise CommandError('DEMO_REQUIRES_EXISTING_EVIDENCE_DIRECTORY')
    return root.resolve(strict=True)


def _account(username, principal_id):
    if not isinstance(username, str) or not username.strip():
        raise CommandError('DEMO_REQUIRES_EXACT_EXISTING_USERNAME')
    try:
        principal_id = UUID(str(principal_id))
    except (ValueError, TypeError, AttributeError):
        raise CommandError('DEMO_REQUIRES_EXACT_EXISTING_PRINCIPAL') from None
    # PrincipalIdentity is the non-secret account identity. Credential material
    # is intentionally never queried, even to check that a password exists.
    user_identity = PrincipalIdentity.objects.filter(provider='self',
        provider_subject=username, principal_id=principal_id).first()
    if user_identity is None:
        raise CommandError('DEMO_ACCOUNT_IDENTITY_MISMATCH')
    actor = Principal.objects.select_for_update().get(id=principal_id)
    if actor.account_status != 'active':
        raise CommandError('DEMO_ACCOUNT_NOT_ACTIVE')
    if ServiceRoleGrant.objects.filter(principal=actor, revoked_at__isnull=True).exists():
        raise CommandError('DEMO_REQUIRES_FAMILY_ONLY_ACCOUNT')
    allowed_patients = [identity('patient:child_a'), identity('patient:child_b')]
    if (Relation.objects.filter(guardian=actor).exclude(patient_id__in=allowed_patients).exists()
            or AccessGrant.objects.filter(principal=actor).exclude(patient_id__in=allowed_patients).exists()):
        raise CommandError('DEMO_ACCOUNT_ALREADY_HAS_OTHER_PATIENT_SCOPE')
    return actor


def _strict_existing(row, expected, reason):
    if any(getattr(row, key) != value for key, value in expected.items()):
        raise CommandError(reason)
    return row


def _put_json(root, key, payload):
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    checksum = hashlib.sha256(content).hexdigest()
    directory = root / KEY
    directory.mkdir(exist_ok=True)
    if directory.is_symlink() or not directory.resolve().is_relative_to(root):
        raise CommandError('DEMO_EVIDENCE_DIRECTORY_INVALID')
    path = directory / f'{key}-{checksum}.json'
    if path.exists():
        if path.is_symlink() or path.read_bytes() != content:
            raise CommandError('DEMO_EVIDENCE_CONTENT_CONFLICT')
    else:
        # Exclusive creation never replaces user files. A rollback may leave an
        # unreferenced content-addressed file; it never leaves a success claim.
        with path.open('xb') as output:
            output.write(content)
    return path, checksum


class ExperienceEvidence:
    """Persistent adapter for the existing strictly synthetic fixture protocol.

    It grants no service roles. The non-login runtime fixture operator must
    already hold the validation role before this bounded command can run.
    """
    def __init__(self, *, actor, gate, root, source, reference, retention):
        self.actor, self.gate, self.root = actor, gate, root
        self.source, self.reference, self.retention = source, reference, retention
        self.created = []

    def create(self, model, **values):
        row = model.objects.create(**values)
        self.created.append(row)
        return row

    def evidence(self, payload, *, purposes, identity=None):
        path, checksum = _put_json(self.root, 'evidence', payload)
        source_id = uuid5(NAMESPACE, f'evidence-source:{checksum}')
        revision_id = identity or uuid5(NAMESPACE, f'evidence-revision:{checksum}')
        source, _ = SourceRegistry.objects.get_or_create(id=source_id, defaults={
            'source_key': f'{KEY}:evidence:{checksum}', 'owner': KEY, 'location': str(path),
            'purpose': 'software_test', 'sensitivity': 'synthetic', 'update_frequency': 'never', 'status': 'approved'})
        _strict_existing(source, {'source_key': f'{KEY}:evidence:{checksum}', 'location': str(path),
            'purpose': 'software_test', 'sensitivity': 'synthetic', 'status': 'approved'}, 'DEMO_SOURCE_CONFLICT')
        revision, _ = SourceRevision.objects.get_or_create(id=revision_id, defaults={
            'source': source, 'version': '1', 'checksum': checksum, 'license': RIGHTS,
            'allowed_use': purposes, 'rights_evidence_ref': RIGHTS, 'retention_policy': self.retention,
            'captured_at': timezone.now(), 'status': 'approved'})
        _strict_existing(revision, {'source_id': source.id, 'checksum': checksum,
            'allowed_use': purposes, 'status': 'approved', 'rights_evidence_ref': RIGHTS}, 'DEMO_REVISION_CONFLICT')
        return revision, f'urn:sjia:source-revision:{revision.id}'

    def configuration(self, kind, payload, *, key=None, qualify=True, schema_version=None, identity=None):
        from tests.synthetic_fixtures import SyntheticEvidence
        schema_version = schema_version or payload['schema_version']
        validate_payload(kind, schema_version, payload)
        key = key or f'{KEY}:{kind}:{compute_checksum(payload)}'
        current = ConfigVersion.objects.filter(config_key=key).first()
        if current is not None:
            _strict_existing(current, {'source_revision_id': self.source.id, 'kind': kind,
                'schema_version': schema_version, 'checksum': compute_checksum(payload), 'payload': payload},
                'DEMO_CONFIGURATION_CONFLICT')
            if qualify and configuration_qualification_reason(current, self.gate.environment):
                raise CommandError('DEMO_CONFIGURATION_REVALIDATION_REQUIRED')
            return current
        return SyntheticEvidence.configuration(self, kind, payload, key=key, qualify=qualify,
            schema_version=schema_version, identity=identity)

    def qualify(self, config):
        from tests.synthetic_fixtures import SyntheticEvidence
        return SyntheticEvidence.qualify(self, config)


def _foundation(actor, root):
    # PostgreSQL cannot FOR UPDATE the nullable side of the runtime relation.
    # Lock each authoritative base row explicitly, without a nullable join.
    environment = settings.SJIA_ENVIRONMENT
    gate = EnvironmentGate.objects.select_for_update().filter(environment=environment, enabled=True).first()
    if gate is None or gate.runtime_config_id is None:
        raise CommandError('DEMO_EXISTING_TEST_RUNTIME_REQUIRED')
    runtime = ConfigVersion.objects.select_for_update().get(id=gate.runtime_config_id)
    if (runtime.schema_version != 'backend-runtime-1' or runtime.payload.get('environment') != environment
            or runtime.payload.get('providers') != [] or runtime.source_revision_id is None
            or runtime.source_revision.source.sensitivity not in ('synthetic', 'simulation')):
        raise CommandError('DEMO_REQUIRES_LOCAL_PROVIDER_FREE_SOFTWARE_RUNTIME')
    validate_payload(runtime.kind, runtime.schema_version, runtime.payload)
    operator = runtime.created_by
    if (operator.id == actor.id or operator.account_status != 'active'
            or PrincipalIdentity.objects.filter(principal=operator).exists()):
        raise CommandError('DEMO_REQUIRES_SEPARATE_EXISTING_SOFTWARE_OPERATOR')
    now = timezone.now()
    grants = ServiceRoleGrant.objects.filter(principal=operator, environment=gate,
        action='register_validation', revoked_at__isnull=True, valid_from__lte=now)
    if not any(grant.evidence_ref and (grant.valid_until is None or grant.valid_until > now) for grant in grants):
        raise CommandError('DEMO_EXISTING_SOFTWARE_VALIDATOR_REQUIRED')
    source_id, revision_id = identity('source'), identity('source-revision')
    existing = SourceRevision.objects.select_related('source').filter(id=revision_id).first()
    if existing is not None:
        _strict_existing(existing, {'source_id': source_id, 'status': 'approved', 'rights_evidence_ref': RIGHTS},
            'DEMO_REVISION_CONFLICT')
        _strict_existing(existing.source, {'source_key': KEY, 'sensitivity': 'synthetic',
            'purpose': 'software_test', 'status': 'approved'}, 'DEMO_SOURCE_CONFLICT')
        _, content, reason = inspect_approved_evidence(f'urn:sjia:source-revision:{existing.id}',
            purpose='configuration_approval')
        if reason:
            raise CommandError(f'DEMO_SOURCE_UNAVAILABLE:{reason}')
        payload = json.loads(content)
        if (payload.get('account_principal_id') != str(actor.id) or payload.get('namespace') != str(NAMESPACE)
                or payload.get('clinical_validation') is not False or payload.get('environment') != environment):
            raise CommandError('DEMO_DATASET_OWNER_MISMATCH')
        source, revision, anchor = existing.source, existing, datetime.fromisoformat(payload['as_of'])
        retention = existing.retention_policy
    else:
        if SourceRegistry.objects.filter(id=source_id).exists():
            raise CommandError('DEMO_SOURCE_WITHOUT_REVISION')
        anchor = now
        payload = {'schema_version': 'family-experience-source-1', 'namespace': str(NAMESPACE),
            'mode': 'fictional_family_experience', 'environment': environment, 'purpose': 'software_test',
            'clinical_validation': False, 'medical_approval': False, 'external_providers': [],
            'account_principal_id': str(actor.id), 'as_of': anchor.isoformat(),
            'authorization': 'User requested one normal H5 and an experience account containing complete sample data.',
            'scope': 'Fictional input records and schema/protocol checks only; no real patients or clinical approval.'}
        path, checksum = _put_json(root, 'dataset', payload)
        source = SourceRegistry.objects.create(id=source_id, source_key=KEY, owner=KEY, location=str(path),
            purpose='software_test', sensitivity='synthetic', update_frequency='never', status='approved')
        reference = f'urn:sjia:source-revision:{revision_id}'
        retention = RetentionPolicy.objects.create(id=identity('policy:configuration_evidence'),
            policy_key=f'{KEY}:configuration_evidence', version=1, data_class='configuration_evidence',
            schema_version='retention-1', rules={'retention_days': None, 'disposition': 'retain'},
            status='active', approved_by=operator, approved_at=now, evidence_ref=reference)
        revision = SourceRevision.objects.create(id=revision_id, source=source, version='1', checksum=checksum,
            license=RIGHTS, rights_evidence_ref=RIGHTS, retention_policy=retention,
            allowed_use=['configuration_approval', 'configuration_validation', 'family_observation',
                'clinical_policy', 'software_test'], captured_at=now, status='approved')
    fixture = ExperienceEvidence(actor=operator, gate=gate, root=root, source=revision,
        reference=f'urn:sjia:source-revision:{revision.id}', retention=retention)
    return fixture, runtime, anchor


def _policies(fixture):
    policies = {'configuration_evidence': fixture.retention}
    for kind in ('audit', 'request', 'file', 'observation', 'report', 'therapy', 'draft', 'archive'):
        query = RetentionPolicy.objects.filter(data_class=kind, status='active')
        # File admission selects from the runtime's explicit policy IDs. An
        # unrelated old fixture's file policy remains untouched and unselected.
        if kind == 'file':
            query = query.filter(id=identity('policy:file'))
        rows = list(query)
        if len(rows) > 1:
            raise CommandError(f'DEMO_AMBIGUOUS_RETENTION:{kind}')
        if rows:
            policy = rows[0]
            if (policy.approved_by_id != fixture.actor.id or policy.approved_at is None
                    or policy.schema_version != 'retention-1' or not policy.evidence_ref):
                raise CommandError(f'DEMO_RETENTION_NOT_SOFTWARE_OWNED:{kind}')
        else:
            policy = RetentionPolicy.objects.create(id=identity(f'policy:{kind}'), policy_key=f'{KEY}:{kind}',
                version=1, data_class=kind, schema_version='retention-1',
                rules={'retention_days': None, 'disposition': 'retain'}, status='active',
                approved_by=fixture.actor, approved_at=timezone.now(), evidence_ref=fixture.reference)
        policies[kind] = policy
    return policies


def _runtime(fixture, original, policies):
    payload = copy.deepcopy(original.payload)
    task = {'queue_wait_seconds': 60, 'execution_seconds': 120, 'lease_seconds': 60,
        'heartbeat_seconds': 5, 'visibility_timeout_seconds': 180, 'max_attempts': 1,
        'retry_delays_seconds': [], 'max_concurrency': 2}
    payload['tasks'] = [{**task, 'task_type': kind} for kind in ('reconcile', 'file_validation', 'summary')]
    payload['providers'] = []
    payload['retention_policy_ids'] = list(dict.fromkeys(str(row.id) for row in policies.values()))
    runtime = fixture.configuration('provider', payload, key=f'{KEY}:runtime')
    fixture.gate.runtime_config = runtime
    fixture.gate.save(update_fields=['runtime_config'])
    return runtime


def _publication(fixture, fields):
    from tests.synthetic_fixtures import synthetic_publication
    fixture.gate.refresh_from_db()
    release = fixture.gate.active_release
    if release is not None:
        if (release.validation_config.source_revision_id != fixture.source.id
                or release.validation_config.payload.get('purpose') != 'software_test'):
            raise CommandError('DEMO_REFUSES_OTHER_ACTIVE_PUBLICATION')
        reason = release_qualification_reason(release)
        if reason:
            raise CommandError(f'DEMO_SOFTWARE_PUBLICATION_UNAVAILABLE:{reason}')
    else:
        release, _, _ = synthetic_publication(fixture, field_codes=fields, purpose='software_test')
    template = fixture.configuration('summary', {'schema_version': 'summary-1', 'environment': fixture.gate.environment,
        'purpose': 'software_test', 'field_codes': fields, 'evidence_refs': [fixture.reference]}, key=f'{KEY}:summary')
    return release, template


def _patients(fixture, actor, fields):
    patients, uses = {}, {}
    for key, name in (('child_a', '安安（示例）'), ('child_b', '乐乐（示例）')):
        patient, created = Patient.objects.get_or_create(id=identity(f'patient:{key}'),
            defaults={'display_id': name, 'status': 'active', 'created_at': timezone.now()})
        _strict_existing(patient, {'display_id': name, 'status': 'active'}, 'DEMO_PATIENT_CONFLICT')
        if not created and not DataUseAuthorization.objects.filter(id=identity(f'use:{key}'),
                patient=patient, source_revision=fixture.source, environment=fixture.gate.environment,
                purpose='family_observation', evidence_ref=fixture.reference).exists():
            raise CommandError('DEMO_EXISTING_PATIENT_NOT_OWNED_BY_DATASET')
        application, _ = RelationApplication.objects.get_or_create(id=identity(f'application:{key}'), defaults={
            'version': 1, 'applicant': actor, 'patient': patient, 'evidence_ref': fixture.reference,
            'note': '体验账号 · 示例资料', 'requested_access': 'read_write', 'status': 'approved',
            'checked_by': fixture.actor, 'checked_at': timezone.now(), 'created_at': timezone.now()})
        _strict_existing(application, {'applicant_id': actor.id, 'patient_id': patient.id,
            'status': 'approved', 'evidence_ref': fixture.reference}, 'DEMO_RELATION_APPLICATION_CONFLICT')
        relation, _ = Relation.objects.get_or_create(id=identity(f'relation:{key}'), defaults={
            'patient': patient, 'guardian': actor, 'application': application, 'status': 'active', 'version': 1,
            'checked_by': fixture.actor, 'checked_at': timezone.now(), 'valid_from': timezone.now()})
        _strict_existing(relation, {'patient_id': patient.id, 'guardian_id': actor.id, 'application_id': application.id,
            'status': 'active', 'revoked_at': None}, 'DEMO_RELATION_CONFLICT')
        for action in FAMILY_ACTIONS:
            scopes = [fields]
            if action in ('read_file',):
                scopes = [[]]
            elif action in ('write_record', 'read_raw', 'read_history'):
                # Original-file/document scope is separate from field access and
                # remains restricted to these two dedicated example patients.
                scopes.append([])
            for codes in scopes:
                suffix = 'whole' if not codes else 'fields'
                grant, _ = AccessGrant.objects.get_or_create(id=identity(f'grant:{key}:{action}:{suffix}'), defaults={
                    'patient': patient, 'principal': actor, 'relation_id': relation.id, 'action': action,
                    'field_codes': codes, 'record_scope': 'all', 'purpose': 'family_observation',
                    'granted_by': fixture.actor, 'evidence_ref': fixture.reference, 'valid_from': timezone.now()})
                _strict_existing(grant, {'patient_id': patient.id, 'principal_id': actor.id, 'relation_id': relation.id,
                    'action': action, 'field_codes': codes, 'purpose': 'family_observation', 'record_scope': 'all',
                    'revoked_at': None, 'evidence_ref': fixture.reference}, 'DEMO_ACCESS_GRANT_CONFLICT')
        use, _ = DataUseAuthorization.objects.get_or_create(id=identity(f'use:{key}'), defaults={
            'patient': patient, 'source_revision': fixture.source, 'purpose': 'family_observation',
            'environment': fixture.gate.environment, 'evidence_ref': fixture.reference, 'approved_by': fixture.actor,
            'approved_at': timezone.now()})
        _strict_existing(use, {'patient_id': patient.id, 'source_revision_id': fixture.source.id,
            'environment': fixture.gate.environment, 'purpose': 'family_observation', 'revoked_at': None,
            'evidence_ref': fixture.reference}, 'DEMO_USE_AUTHORIZATION_CONFLICT')
        patients[key], uses[key] = patient, use
    return patients, uses


@transaction.atomic
@artifact_verification_scope()
def seed_family_experience(*, username, principal_id, acknowledge_test_only=False):
    root = require_test_context(acknowledge_test_only=acknowledge_test_only)
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [73109520261012])
    # Match ordinary admission's gate -> actor lock order. The seed advisory
    # lock serializes only this command; it cannot order concurrent app writes.
    if not EnvironmentGate.objects.select_for_update().filter(environment=settings.SJIA_ENVIRONMENT, enabled=True).exists():
        raise CommandError('DEMO_EXISTING_TEST_RUNTIME_REQUIRED')
    actor = _account(username, principal_id)
    from core.demo_records import demo_field_config_payloads, demo_report_plan, seed_demo_records
    from core.demo_files_summary import ensure_demo_report_attachment, ensure_demo_summary
    from files.models import FileObject
    fixture, old_runtime, anchor = _foundation(actor, root)
    policies = _policies(fixture)
    runtime = _runtime(fixture, old_runtime, policies)
    payloads = demo_field_config_payloads(fixture.reference)
    fields = sorted({field['field_code'] for payload in payloads.values() for field in payload['fields']})
    configs = {}
    for kind, payload in payloads.items():
        unrelated = ConfigVersion.objects.filter(kind='content', schema_version='field-config-1',
            status='enabled', payload__kind=kind).exclude(source_revision=fixture.source)
        if unrelated.exists():
            raise CommandError(f'DEMO_REFUSES_OTHER_FIELD_CONFIGURATION:{kind}')
        configs[kind] = fixture.configuration('content', payload, key=f'{KEY}:fields:{kind}')
    release, _ = _publication(fixture, fields)
    patients, uses = _patients(fixture, actor, fields)
    attachments = {}
    ascii_names = {'血小板计数': 'Platelets', '铁蛋白': 'Ferritin', 'C反应蛋白': 'CRP'}
    for item in demo_report_plan(anchor):
        child_key = item['patient_key']
        report_date = datetime.fromisoformat(item['report_metadata']['sample_time']['local_date']).date()
        rows = []
        for field in item['fields']:
            interval = (field.get('reference_interval') or '').replace('（示例范围）', '').replace('–', '-')
            label = ascii_names[field['original_name']] + (f' (ref {interval})' if interval else '')
            value = field.get('raw_numeric') or 'Not provided'
            if field.get('report_flag') == '↑':
                value += ' High'
            rows.append((label, value, field.get('raw_unit') or ''))
        group = ensure_demo_report_attachment(actor=actor, patient=patients[child_key],
            source_revision=fixture.source, use_authorization=uses[child_key], retention_policy=policies['file'],
            request_retention_policy=policies['request'], namespace=NAMESPACE, report_key=item['key'].replace('/', '-'),
            report_date=report_date, rows=rows)
        file = FileObject.objects.get(file_group_id=group.id, purpose='original', status='available')
        source_text = '\n'.join(['EXAMPLE REPORT / NOT FOR CLINICAL USE',
            f'Sample date: {report_date.isoformat()}', *[' | '.join(row) for row in rows]])
        attachments[item['key']] = {'file_ids': [file.id], 'source_file_id': file.id, 'source_text': source_text}
    records = seed_demo_records(actor=actor, patients=patients, retention_policies=policies,
        source_revision=fixture.source, use_authorizations=uses, field_configs=configs,
        report_attachments=attachments, as_of=anchor)
    from core.demo_visit_files import ensure_demo_visit_attachment
    from core.demo_archives import ensure_demo_manual_archive
    archives = {}
    for child_key, patient in patients.items():
        group, note = ensure_demo_visit_attachment(actor=actor, patient=patient, child_key=child_key,
            as_of=anchor.date(), source_revision=fixture.source, use_authorization=uses[child_key],
            retention_policy=policies['file'], request_retention_policy=policies['request'])
        archives[child_key] = ensure_demo_manual_archive(actor=actor, patient=patient,
            file_group=group, proposed_episodes=note['proposed_episodes'],
            documented_time=note['documented_time'], organization=note['organization'],
            confirmed_text=note['confirmed_text'], request_key=f'{child_key}-family-visits')
    summaries = []
    for patient in patients.values():
        summary = ensure_demo_summary(actor=actor, patient=patient, retention_policy=policies['request'],
            namespace=NAMESPACE, from_date=(anchor - timedelta(days=14)).date(), to_date=anchor.date())
        summaries.append(str(summary.id))
    return {'account': username, 'principal_id': str(actor.id), 'namespace': str(NAMESPACE), 'environment': fixture.gate.environment,
        'patients': {key: {'id': str(value.id), 'display_id': value.display_id} for key, value in patients.items()},
        'records': records, 'report_files': len(attachments), 'visit_note_files': len(archives),
        'archives': archives, 'summary_ids': summaries,
        'runtime_config_id': str(runtime.id), 'software_publication_id': str(release.id),
        'data_label': '体验账号 · 示例资料', 'clinical_validation': False,
        'capabilities': {'records': 'real_local_service', 'files': 'real_local_validation',
            'summaries': 'real_local_execution_software_test_qualification',
            'archives': 'real_manual_documents_family_proposed_visits', 'ocr': 'disabled', 'voice': 'disabled',
            'clinical_assessment': 'not_run', 'clinical_alerts': 'not_created', 'external_delivery': 'disabled'},
        'credential_operations': 'none', 'session_operations': 'none'}
