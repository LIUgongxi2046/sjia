"""Isolated synthetic software fixtures, never clinical or production approval.

Every file is generated into a TemporaryDirectory. Database writes are allowed
only inside a Django test database. Qualification is exercised, never patched.
The evidence describes synthetic schema/protocol checks, not clinical validity.
"""
import copy
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from django.conf import settings
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from PIL import Image

from authorization.models import AccessGrant, ServiceRoleGrant
from configurations.evidence import deployed_artifact_checksum
from configurations.models import ConfigApproval, ConfigValidation, ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.schemas import validate_payload
from configurations.service import compute_checksum, register_config
from family_content.models import EnvironmentGate
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision


def patch_permission_record_snapshot(case):
    """Permission/wire TestCase setup only; never claim real snapshot isolation.

    TransactionTestCase and LiveServerTestCase keep the production SQL intact.
    """
    from django.test import TestCase
    from unittest.mock import patch
    if not isinstance(case, TestCase):
        return False
    if getattr(case, '_permission_record_snapshot_patched', False):
        return True
    snapshot = patch('records.snapshot.begin_record_snapshot')
    snapshot.start()
    case.addCleanup(snapshot.stop)
    case._permission_record_snapshot_patched = True
    return True


def synthetic_png():
    buffer = io.BytesIO()
    Image.new('RGB', (12, 18), (241, 245, 249)).save(buffer, format='PNG')
    return buffer.getvalue()


class SyntheticEvidence:
    def __init__(self, case, actor):
        if not str(connection.settings_dict['NAME']).startswith('test_'):
            raise RuntimeError('SyntheticEvidence refuses non-test databases')
        self.actor = actor
        patch_permission_record_snapshot(case)
        self.created = []
        self.directory = TemporaryDirectory(prefix='sjia-synthetic-tests-')
        case.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.override = override_settings(CONFIG_EVIDENCE_ROOT=str(self.root),
            CONFIG_EVIDENCE_MAX_BYTES=1024 * 1024, FILE_OBJECT_ROOT=self.root / 'objects')
        self.override.enable()
        case.addCleanup(self.override.disable)
        self.gate, created = EnvironmentGate.objects.get_or_create(environment=settings.SJIA_ENVIRONMENT,
            defaults={'version': 1, 'enabled': True, 'updated_at': timezone.now()})
        if created:
            self.created.append(self.gate)
        self.retention = self.create(RetentionPolicy, policy_key=f'synthetic-evidence-{uuid4()}',
            version=1, data_class='configuration_evidence', rules={'synthetic': True, 'clinical_validation': False},
            schema_version='retention-1', status='active', approved_by=actor,
            approved_at=timezone.now(), evidence_ref='synthetic-test-only')
        for action in ('register_validation', 'approve_config', 'publish_config', 'manage_configs'):
            grant, created = ServiceRoleGrant.objects.get_or_create(principal=actor, environment=self.gate,
                action=action, defaults={'granted_by': actor, 'evidence_ref': 'synthetic-test-only',
                    'valid_from': timezone.now()})
            if created:
                self.created.append(grant)
        self.source, self.reference = self.evidence({'mode': 'SYNTHETIC_TEST_ONLY',
            'clinical_validation': False, 'scope': 'configuration schema and software protocol fixture'},
            purposes=['configuration_approval', 'configuration_validation', 'family_observation', 'clinical_policy', 'software_test'])
        existing_request_policies = set(RetentionPolicy.objects.filter(data_class='request').values_list('id', flat=True))
        self.request_retention = ensure_synthetic_request_policy(actor, evidence_ref=self.reference)
        if self.request_retention.id not in existing_request_policies:
            self.created.append(self.request_retention)

    def create(self, model, **values):
        row = model.objects.create(**values)
        self.created.append(row)
        return row

    def evidence(self, payload, *, purposes, identity=None):
        content = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        checksum = hashlib.sha256(content).hexdigest()
        path = self.root / f'{uuid4()}.json'
        path.write_bytes(content)
        source = self.create(SourceRegistry, source_key=f'synthetic-test-{uuid4()}',
            owner='SYNTHETIC_TEST_ONLY', location=str(path), purpose='software_test',
            sensitivity='synthetic', update_frequency='never', status='approved')
        revision = self.create(SourceRevision, id=identity or uuid4(), source=source, version='1', checksum=checksum,
            license='synthetic-test-only', allowed_use=purposes, rights_evidence_ref='synthetic-test-only',
            retention_policy=self.retention, captured_at=timezone.now(), status='approved')
        return revision, f'urn:sjia:source-revision:{revision.id}'

    def configuration(self, kind, payload, *, key=None, qualify=True, schema_version=None, identity=None):
        schema_version = schema_version or payload['schema_version']
        validate_payload(kind, schema_version, payload)
        if identity is None:
            config = register_config(kind=kind, config_key=key or f'synthetic-{kind}-{uuid4()}',
                schema_version=schema_version, payload=payload, created_by=self.actor, source_revision=self.source)
            self.created.append(config)
        else:
            config = self.create(ConfigVersion, id=identity, kind=kind, config_key=key or f'synthetic-{kind}-{identity}',
                version=1, schema_version=schema_version, payload=payload, checksum=compute_checksum(payload),
                created_by=self.actor, source_revision=self.source, status='draft', created_at=timezone.now())
        if qualify:
            self.qualify(config)
        return config

    def qualify(self, config):
        validate_payload(config.kind, config.schema_version, config.payload)
        config.checksum = compute_checksum(config.payload)
        config.source_revision = self.source
        config.status = 'enabled'
        config.save(update_fields=['checksum', 'source_revision', 'status'])
        self.create(ConfigApproval, config=config, approved_by=self.actor,
            approval_role='synthetic_software_test', approved_at=timezone.now(), evidence_ref=self.reference,
            effective_scope={'environment': self.gate.environment, 'audience': ['family', 'support'],
                'config_checksum': config.checksum})
        executed_at = timezone.now()
        artifact = deployed_artifact_checksum(config)
        report = {'schema_version': 'config-validation-1', 'config_id': str(config.id),
            'config_checksum': config.checksum, 'environment': self.gate.environment,
            'artifact_sha256': artifact, 'dataset_revision_id': str(self.source.id),
            'outcome': 'pass', 'executed_by': str(self.actor.id), 'executed_at': executed_at.isoformat()}
        revision, reference = self.evidence(report, purposes=['configuration_validation'])
        self.create(ConfigValidation, config=config, config_checksum=config.checksum, environment=self.gate,
            artifact_sha256=artifact, dataset_revision=self.source, evidence_ref=reference,
            evidence_sha256=revision.checksum, outcome='pass', executed_by=self.actor, executed_at=executed_at)
        reason = configuration_qualification_reason(config, self.gate.environment)
        if reason:
            raise AssertionError(f'Synthetic fixture failed real qualification: {reason}; '
                f'registered_artifact={artifact}; current_artifact={deployed_artifact_checksum(config)}; '
                'freeze application edits during evidence-bound test runs')
        return config

    def runtime(self):
        file_policy = self.create(RetentionPolicy, policy_key=f'synthetic-file-{uuid4()}', version=1,
            data_class='file', rules={'synthetic': True}, schema_version='retention-1', status='active',
            approved_by=self.actor, approved_at=timezone.now(), evidence_ref='synthetic-test-only')
        payload = {'schema_version': 'backend-runtime-1', 'environment': self.gate.environment,
            'api': {'lock_timeout_ms': 5000, 'statement_timeout_ms': 30000, 'request_timeout_ms': 60000,
                'upload_timeout_ms': 60000, 'max_json_bytes': 1048576, 'allowed_origins': ['https://localhost'],
                'intent_ttl_seconds': 300, 'max_page_size': 100, 'poll_after_ms': 1000},
            'session': {'absolute_ttl_seconds': 3600, 'idle_ttl_seconds': 1800, 'same_site': 'Lax',
                'secure': True, 'http_only': True, 'csrf_use_sessions': True, 'csrf_header': 'X-CSRFToken',
                'cookie_name': '__Host-sjia_session', 'return_paths': ['/home', '/records', '/account', '/associations']},
            'limits': {'max_query_length': 200, 'max_text_length': 10000, 'max_decimal_digits': 20,
                'max_record_fields': 200, 'max_upload_bytes': 20971520, 'max_pixels': 40000000,
                'max_pages': 10, 'media_types': ['image/png', 'image/jpeg'],
                'rate_limits': [{'operation_id': 'synthetic-test', 'window_seconds': 60,
                    'max_requests': 100, 'max_inflight': 10}]},
            'tasks': [{'task_type': 'ocr', 'queue_wait_seconds': 60, 'execution_seconds': 60,
                'lease_seconds': 30, 'heartbeat_seconds': 5, 'visibility_timeout_seconds': 120,
                'max_attempts': 1, 'retry_delays_seconds': [], 'max_concurrency': 2}],
            'providers': [], 'retention_policy_ids': [str(self.retention.id), str(file_policy.id)],
            'operations_contact_ref': 'synthetic-test-only-no-external-contact'}
        for kind in ('summary', 'file_validation', 'reconcile'):
            payload['tasks'].append({**payload['tasks'][0], 'task_type': kind})
        config = self.configuration('provider', payload)
        self.gate.runtime_config = config
        self.gate.save(update_fields=['runtime_config'])
        return config


def qualify_demo(case, actor, patient):
    """Upgrade only rows in the test transaction; seed_demo remains unchanged."""
    fixture = SyntheticEvidence(case, actor)
    case.synthetic = fixture
    config = ConfigVersion.objects.get(config_key='field-config-observation', kind='content')
    fixture.qualify(config)
    authorization = DataUseAuthorization.objects.get(patient=patient)
    revision = authorization.source_revision
    revision.rights_evidence_ref = 'synthetic-test-only'
    revision.retention_policy = fixture.retention
    revision.save(update_fields=['rights_evidence_ref', 'retention_policy'])
    RetentionPolicy.objects.filter(status='active').update(approved_by=actor,
        approved_at=timezone.now(), evidence_ref=fixture.reference)
    drafts = list(RetentionPolicy.objects.filter(data_class='draft', status='active'))
    if len(drafts) > 1:
        raise AssertionError('Ambiguous synthetic draft policies')
    if drafts:
        RetentionPolicy.objects.filter(id=drafts[0].id).update(schema_version='retention-1',
            rules={'retention_days': 7, 'disposition': 'delete'})
    else:
        fixture.create(RetentionPolicy, policy_key=f'synthetic-draft-{uuid4()}', version=1, data_class='draft',
            status='active', schema_version='retention-1', rules={'retention_days': 7, 'disposition': 'delete'},
            approved_by=actor, approved_at=timezone.now(), evidence_ref='synthetic-test-only')
    fixture.runtime()
    AccessGrant.objects.filter(patient=patient, principal=actor, action='read_file').update(field_codes=[])
    original = AccessGrant.objects.filter(patient=patient, principal=actor, action='write_record').first()
    AccessGrant.objects.create(patient=patient, principal=actor, relation_id=original.relation_id,
        action='write_record', field_codes=[], record_scope='all', purpose='family_observation',
        granted_by=actor, evidence_ref='synthetic-whole-upload-only', valid_from=timezone.now())
    return fixture


def current_record_payload(patient, payload):
    result = copy.deepcopy(payload)
    config = ConfigVersion.objects.filter(config_key='field-config-' + result['kind'],
        schema_version='field-config-1', status='enabled').first()
    if config is not None:
        result.setdefault('field_config_version', str(config.id))
        required_time = {item['field_code'] for item in config.payload['fields'] if item['time_required']}
        for item in result['fields']:
            if item['field_code'] in required_time:
                item.setdefault('medical_time', {'precision': 'date', 'local_date': '2026-09-27',
                    'resolution_status': 'local_only'})
    return result


def ensure_synthetic_request_policy(actor, *, evidence_ref='synthetic-test-only-request-retention'):
    """Explicit typed request-retention prerequisite for isolated software tests."""
    if not str(connection.settings_dict['NAME']).startswith('test_'):
        raise RuntimeError('Synthetic request policy refuses non-test databases')
    policies = list(RetentionPolicy.objects.filter(data_class='request', status='active'))
    if len(policies) > 1:
        raise AssertionError('Test fixture has ambiguous request retention policies')
    values = {'schema_version': 'retention-1', 'rules': {'retention_days': None, 'disposition': 'retain'},
        'approved_by': actor, 'approved_at': timezone.now(), 'evidence_ref': evidence_ref}
    if policies:
        policy = policies[0]
        for name, value in values.items():
            setattr(policy, name, value)
        policy.save(update_fields=list(values))
        return policy
    return RetentionPolicy.objects.create(policy_key=f'synthetic-request-{uuid4()}', version=1,
        data_class='request', status='active', **values)


def ensure_synthetic_audit_policy(actor, *, case=None):
    """Explicit audit prerequisite; optional TestCase permission-only read setup."""
    if case is not None:
        patch_permission_record_snapshot(case)
    if not str(connection.settings_dict['NAME']).startswith('test_'):
        raise RuntimeError('Synthetic audit policy refuses non-test databases')
    policies = list(RetentionPolicy.objects.filter(data_class='audit', status='active'))
    if len(policies) > 1:
        raise AssertionError('Test fixture has ambiguous audit retention policies')
    if policies:
        policy = policies[0]
        policy.approved_by = actor
        policy.approved_at = timezone.now()
        policy.evidence_ref = 'synthetic-test-only-audit-retention'
        policy.save(update_fields=['approved_by', 'approved_at', 'evidence_ref'])
        return policy
    return RetentionPolicy.objects.create(policy_key=f'synthetic-audit-{uuid4()}', version=1,
        data_class='audit', status='active', schema_version='retention-1', rules={},
        approved_by=actor, approved_at=timezone.now(), evidence_ref='synthetic-test-only-audit-retention')


def synthetic_publication(fixture, field_codes=('temp',), *, add_components=True, enable=True, dynamic=False, purpose='software_test'):
    """Qualified software-only publication fixture, no patient assessment is run.

    The policy/evidence records are synthetic inputs to lifecycle tests. Their
    schema/hash reports must never be presented as independent clinical proof.
    """
    from datetime import timedelta
    from family_content.service import (create_contact_version, activate_contact,
        create_release_bundle, add_release_component, approve_release, enable_release)
    environment = fixture.gate.environment
    evidence = [fixture.reference]
    configs = {}
    for role in ('population', 'therapy', 'clinical_finding', 'late_result'):
        configs[role] = fixture.configuration('rule', {'schema_version': 'assessment-context-policy-2',
            'role': role, 'purpose': purpose, 'conditions': [],
            'unknown_action': 'insufficient' if role == 'population' else 'record_limitation',
            'not_met_action': 'not_applicable' if role == 'population' else 'record_limitation',
            'require_available_and_verified_by_cutoff': True, 'evidence_refs': evidence})
    configs['verification'] = fixture.configuration('verification', {'schema_version': 'qualification-policy-2',
        'purpose': purpose, 'allowed_origin_types': ['self_report'],
        'verification_evidence_purpose': purpose, 'evidence_refs': evidence})
    configs['mapping'] = fixture.configuration('mapping', {'schema_version': 'mapping-1', 'mappings': [{
        'mapping_id': str(uuid4()), 'concept_code': 'synthetic_test', 'source_revision_id': str(fixture.source.id),
        'raw_unit': 'SYNTHETIC', 'normalized_unit': 'SYNTHETIC', 'method_scope': 'synthetic',
        'multiplier': '1', 'additive': '0', 'exact_values_only': True}], 'evidence_refs': evidence})
    scope = {'population_config_id': str(configs['population'].id), 'therapy_policy_id': str(configs['therapy'].id),
        'evidence_refs': evidence}
    if dynamic:
        for role in ('reset', 'retrigger'):
            configs[role] = fixture.configuration('rule', {'schema_version': 'alert-lifecycle-2',
                'purpose': purpose, 'kind': role, 'conditions': [{
                    'signal_id': 'synthetic_' + role, 'kind': 'professional_finding',
                    'field_code': 'synthetic_test', 'required_state': 'absent' if role == 'reset' else 'present',
                    'verification_policy_id': str(configs['verification'].id), 'evidence_refs': evidence}],
                'combination': {'operator': 'all', 'signal_refs': ['synthetic_' + role]},
                'minimum_sample_count': 1, 'minimum_duration_seconds': 1,
                'allow_rule_version_change': False, 'evidence_refs': evidence})
    configs['window'] = fixture.configuration('window', {'schema_version': 'alert-window-1', 'scope': scope,
        'variables': [{'variable_code': 'synthetic_test', 'current_max_age_seconds': 3600,
            'baseline_max_age_seconds': 7200, 'minimum_separation_seconds': 1,
            'accepted_precision': ['second'], 'current_selection': 'latest_eligible_sample',
            'baseline_selection': 'previous_eligible_sample', 'date_interval_policy': 'require_full_containment_and_strict_order',
            'lower_bound_inclusive': True, 'upper_bound_inclusive': True, 'conflict_policy': 'require_verification',
            'comparison_policy_id': str(configs['verification' if dynamic else 'mapping'].id)}], 'max_cross_analyte_gap_seconds': 3600,
        'clinical_finding_validity_policy_id': str(configs['clinical_finding'].id),
        'late_result_policy_id': str(configs['late_result'].id), 'known_at_policy': 'only_available_and_verified_by_cutoff',
        'evidence_refs': evidence})
    contact_id = uuid4()
    until = timezone.now() + timedelta(days=2)
    contact_payload = {'contact_id': str(contact_id), 'contact_name': 'SYNTHETIC TEST CONTACT',
        'purpose': purpose, 'channel': 'in_app', 'channel_value': 'synthetic-only',
        'owner_id': str(fixture.actor.id), 'environment': environment,
        'source_revision_id': str(fixture.source.id), 'valid_until': until.isoformat()}
    contact_config = fixture.configuration('content', contact_payload, schema_version='contact-1')
    contact = create_contact_version(contact_id=contact_id, contact_name=contact_payload['contact_name'],
        purpose=purpose, channel='in_app', channel_value='synthetic-only', owner=fixture.actor,
        source_revision=fixture.source, approval_config=contact_config, valid_until=until)
    fixture.created.append(contact)
    activate_contact(contact=contact, expected_gate_version=fixture.gate.version)
    fixture.gate.refresh_from_db()
    ids = {key: uuid4() for key in ('rule', 'content', 'visibility', 'notification')}
    publication = {'level': 'attention', 'priority': 1, 'content_config_id': str(ids['content']),
        'visibility_config_id': str(ids['visibility']), 'contact_version_id': str(contact.id),
        'notification_config_id': str(ids['notification']), 'valid_for_seconds': 3600}
    configs['rule'] = fixture.configuration('rule', {'schema_version': 'alert-trigger-1',
        'rule_key': 'synthetic-never-clinical', 'scope': scope, 'window_policy_id': str(configs['window'].id),
        'conditions': [{'signal_id': 'synthetic_signal', 'kind': 'professional_finding',
            'field_code': 'synthetic_test', 'required_state': 'present',
            'verification_policy_id': str(configs['verification'].id), 'evidence_refs': evidence}],
        'combination': {'operator': 'all', 'signal_refs': ['synthetic_signal']}, 'publication': publication,
        'episode': {'rule_family_key': 'synthetic-test', 'repeat_policy': 'once_until_reset',
            'escalation_bypasses_cooldown': False, 'reset_policy_id': str(configs['reset' if dynamic else 'clinical_finding'].id),
            'retrigger_policy_id': str(configs['retrigger' if dynamic else 'late_result'].id)}, 'unknown_policy': 'no_event_from_unknown',
        'replay_policy': 'no_publication', 'evidence_refs': evidence}, identity=ids['rule'])
    configs['content'] = fixture.configuration('content', {'schema_version': 'content-1', 'environment': environment,
        'purpose': purpose, 'rule_id': str(configs['rule'].id), 'window_policy_id': str(configs['window'].id),
        'approved_text': 'SYNTHETIC TEST CONTENT; NO CLINICAL APPROVAL', 'level': 'attention',
        'evidence_refs': evidence}, identity=ids['content'])
    configs['visibility'] = fixture.configuration('visibility', {'schema_version': 'visibility-1',
        'environment': environment, 'purpose': purpose, 'field_codes': list(field_codes),
        'clinical_fields': [], 'evidence_refs': evidence}, identity=ids['visibility'])
    configs['notification'] = fixture.configuration('notification', {'schema_version': 'notification-1',
        'environment': environment, 'purpose': purpose, 'channels': ['in_app'], 'max_attempts': 1,
        'retry_delays_seconds': [], 'evidence_refs': evidence}, identity=ids['notification'])
    report = configs['rule'].validations.get()
    configs['validation'] = fixture.configuration('validation', {'schema_version': 'validation-1',
        'environment': environment, 'purpose': purpose, 'rule_id': str(configs['rule'].id),
        'window_policy_id': str(configs['window'].id), 'dataset_revision_id': str(fixture.source.id),
        'report_ref': report.evidence_ref, 'report_sha256': report.evidence_sha256, 'evidence_refs': evidence})
    release = create_release_bundle(environment=environment, contact_version=contact, validation_config=configs['validation'])
    fixture.created.append(release)
    if add_components:
        for kind in ('rule', 'window', 'verification', 'mapping', 'visibility', 'content', 'notification'):
            component = add_release_component(release=release, component_kind=kind, config=configs[kind])
            fixture.created.append(component)
    if enable:
        approve_release(release=release)
        fixture.gate.refresh_from_db()
        enable_release(release=release, environment_gate=fixture.gate, expected_gate_version=fixture.gate.version)
    return release, contact, configs
