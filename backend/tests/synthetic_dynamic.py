"""Synthetic dynamic-to-native integration inputs, no admission mocking.

Artificial categorical finding, source image, verification and software policy
records are test prerequisites, not clinical judgments. The actual dynamic task
runner builds all evaluation, dependency and publication proofs from these rows.
"""
import copy
import os
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.utils import timezone

from alerting.models import AlertEvent, AlertEvaluation, AlertRecipient
from alerting.service import create_dynamic_task, execute_dynamic_evaluation, read_dynamic_evaluation
from archives.models import ArchiveFactVersion
from authorization.models import AccessGrant, ServiceRoleGrant
from files.models import FileGroup, FileObject, FilePage
from files.service import object_store
from files.storage import digest_bytes
from identity.models import Patient, Principal
from records.models import RecordField
from records.service import create_record
from relations.models import Relation, RelationApplication
from sources.models import DataUseAuthorization
from tasks.models import OutboxEvent
from verification.models import Verification, VerificationField, FieldVerificationState
from tests.synthetic_fixtures import SyntheticEvidence, synthetic_png, synthetic_publication


def synthetic_dynamic_native(case, *, purpose='software_test', execute_current=True, shared=None):
    now = timezone.now()
    actor = shared.actor if shared is not None else Principal.objects.create(account_status='active', created_at=now, updated_at=now)
    patient = Patient.objects.create(display_id=f'SYNTHETIC-NATIVE-{uuid4()}', status='active', created_at=now)
    fixture = shared.fixture if shared is not None else SyntheticEvidence(case, actor)
    if purpose not in fixture.source.allowed_use:
        fixture.source.allowed_use.append(purpose)
        fixture.source.save(update_fields=['allowed_use'])
    if shared is None:
        release, contact, configs = synthetic_publication(fixture, purpose=purpose, field_codes=['synthetic_test'], dynamic=True)
    else:
        configs = shared.configs
    application = RelationApplication.objects.create(version=1, applicant=actor, patient=patient,
        evidence_ref='synthetic-test-only', status='approved', checked_by=actor, checked_at=now, created_at=now)
    relation = Relation.objects.create(patient=patient, guardian=actor, application=application,
        status='active', version=1, checked_by=actor, checked_at=now, valid_from=now)
    if shared is None:
        for action in ('execute_assessment', 'read_assessment', 'verify_field', 'manage_notifications'):
            ServiceRoleGrant.objects.create(principal=actor, environment=fixture.gate, action=action,
                granted_by=actor, evidence_ref='synthetic-test-only', valid_from=now)
    for action in ('execute_assessment', 'read_assessment', 'verify_field', 'read_case', 'receive_alert', 'read_file'):
        AccessGrant.objects.create(patient=patient, principal=actor, relation_id=relation.id, action=action,
            field_codes=[], record_scope='all', purpose=purpose, granted_by=actor,
            evidence_ref='synthetic-test-only', valid_from=now)
    for action in ('execute_assessment', 'verify_field'):
        AccessGrant.objects.create(patient=patient, principal=actor, relation_id=relation.id, action=action,
            field_codes=['synthetic_test'], record_scope='all', purpose=purpose, granted_by=actor,
            evidence_ref='synthetic-field-scope', valid_from=now)
    authorization = DataUseAuthorization.objects.create(patient=patient, source_revision=fixture.source,
        purpose=purpose, environment=fixture.gate.environment, evidence_ref=fixture.reference,
        approved_by=actor, approved_at=now)
    if shared is None:
        base = fixture.runtime()
        secret = patch.dict(os.environ, {'SJIA_SYNTHETIC_NATIVE_KEY': 'synthetic-only-not-a-real-credential-' + 'x' * 48})
        secret.start()
        case.addCleanup(secret.stop)
        provider = fixture.configuration('provider', {'schema_version': 'notification-in-app-2',
            'environment': fixture.gate.environment, 'purpose': purpose, 'channel': 'in_app',
            'endpoint': 'urn:sjia:notification:in-app:2', 'runtime_config_key': base.config_key,
            'executor_id': str(actor.id), 'signing_secret_ref': 'env:SJIA_SYNTHETIC_NATIVE_KEY',
            'permit_seconds': 300, 'delivery_ticket_seconds': 120, 'max_daily_messages': 10,
            'max_recipients_per_event': 1, 'timezone': 'UTC', 'retention_policy_id': str(fixture.retention.id),
            'evidence_refs': [fixture.reference]})
        payload = copy.deepcopy(base.payload)
        payload['tasks'] = [{**payload['tasks'][0], 'task_type': kind, 'queue_wait_seconds': 120,
            'execution_seconds': 120, 'lease_seconds': 120, 'heartbeat_seconds': 50, 'visibility_timeout_seconds': 300}
            for kind in ('alert_evaluation', 'notification', 'assessment')]
        payload['providers'] = [{'kind': 'notification', 'config_id': str(provider.id), 'enabled': True,
            'endpoint': 'urn:sjia:notification:in-app:2', 'secret_ref': 'env:SJIA_SYNTHETIC_NATIVE_KEY',
            'region': 'local-test-only', 'processing_basis_ref': fixture.reference,
            'connect_timeout_ms': 1000, 'read_timeout_ms': 1000, 'total_timeout_ms': 3000,
            'submit_retries': 0, 'query_attempts': 0, 'query_delays_seconds': [],
            'circuit_window_seconds': 60, 'circuit_failure_threshold': 3, 'circuit_open_seconds': 60,
            'daily_budget': None, 'currency': None}]
        runtime = fixture.configuration('provider', payload, key=base.config_key)
        base.status = 'disabled'
        base.save(update_fields=['status'])
        fixture.gate.runtime_config = runtime
        fixture.gate.save(update_fields=['runtime_config'])
        rule = fixture.configuration('rule', {'schema_version': 'dynamic-input-policy-2', 'purpose': purpose,
            'variables': [{'variable_code': 'synthetic_test', 'concept_code': 'synthetic_test', 'value_type': 'categorical',
                'normalized_unit': None, 'mapping_config_id': None, 'accepted_context_types': ['current'],
                'categorical_values': [{'value': 'yes', 'encoded': 1, 'assertion_state': 'present'},
                    {'value': 'no', 'encoded': 0, 'assertion_state': 'absent'}]}],
            'comparisons': [], 'max_combination_depth': 4, 'max_combination_operands': 10,
            'evidence_refs': [fixture.reference]})
    else:
        runtime, provider, rule = shared.runtime, shared.provider, shared.input_rule
    group = FileGroup.objects.create(patient=patient, owner=actor, version=1, status='available',
        source_revision=fixture.source, use_authorization_id=authorization.id, created_at=now)
    data = synthetic_png()
    key = f'synthetic-dynamic/{uuid4()}/original'
    object_store.put_immutable(key, data)
    file = FileObject.objects.create(patient=patient, file_group_id=group.id, object_key=key,
        object_version=digest_bytes(data), checksum=digest_bytes(data), size_bytes=len(data), media_type='image/png',
        purpose='original', status='available', retention_policy=fixture.retention, created_at=now)
    FilePage.objects.create(patient=patient, file_group_id=group.id, file_id=file.id, ordinal=1, width=12, height=18, rotation=0)
    measured = (timezone.now() - timedelta(minutes=1)).replace(microsecond=0)
    result = create_record(patient=patient, actor=actor, kind='observation', capture_method='manual',
        origin_type='self_report', retention_policy=fixture.retention, use_authorization_id=authorization.id,
        source_revision=fixture.source, file_ids=[file.id], fields=[{
            'field_code': 'synthetic_test', 'concept_code': 'synthetic_test', 'value_type': 'categorical',
            'value_state': 'known', 'categorical_value': 'yes', 'source_file_id': str(file.id),
            'source_text': 'SYNTHETIC SOFTWARE TEST yes', 'sample_time_origin': 'observation',
            'medical_time': {'precision': 'second', 'local_datetime': measured.replace(tzinfo=None).isoformat(),
                'timezone_name': 'UTC', 'resolution_status': 'local_only'}}])
    field = RecordField.objects.get(record_version_id=result['record_version_id'])
    ArchiveFactVersion.objects.filter(field_id=field.id).update(context_type='current', assertion_state='present')
    verification = Verification.objects.create(patient=patient, record_version_id=result['record_version_id'],
        verifier=actor, policy=configs['verification'], purpose=purpose, evidence_source=fixture.source,
        evidence_ref=fixture.reference, verified_at=timezone.now())
    VerificationField.objects.create(verification_id=verification.id, record_version_id=result['record_version_id'],
        patient_id=patient.id, field_id=field.id, result='consistent', reason='synthetic test fixture consistency')
    FieldVerificationState.objects.create(field_id=field.id, record_version_id=result['record_version_id'],
        patient_id=patient.id, purpose=purpose, version=1, current_verification_id=verification.id, status='verified')
    patient.refresh_from_db()
    point = timezone.now().isoformat()
    value = {'mode': 'current', 'assessment_at': point, 'known_at': point, 'policy_id': str(configs['rule'].id),
        'input_rule_id': str(rule.id), 'verification_policy_id': str(configs['verification'].id),
        'use_authorization_id': str(authorization.id), 'window_policy_id': str(configs['window'].id),
        'observation_period_id': None}
    base_scope = dict(fixture=fixture, actor=actor, patient=patient, relation=relation, provider=provider,
        runtime=runtime, verification=verification, configs=configs, input_rule=rule, file=file,
        authorization=authorization, field=field, dynamic_payload=value)
    if not execute_current:
        return SimpleNamespace(**base_scope)
    task, _ = create_dynamic_task(patient_id=patient.id, actor=actor, auth_epoch=actor.auth_epoch,
        environment=fixture.gate.environment, payload=value)
    requested = OutboxEvent.objects.get(task=task, event_type='alert_evaluation.requested')
    execute_dynamic_evaluation(requested, 'synthetic-test-worker')
    task.refresh_from_db()
    case.assertEqual(task.status, 'completed')
    evaluation = AlertEvaluation.objects.get(task_id=task.id)
    computed = read_dynamic_evaluation(evaluation_id=evaluation.id, actor=actor,
        auth_epoch=actor.auth_epoch, environment=fixture.gate.environment)
    case.assertEqual((computed['validity'], computed['status'], computed['decision']), ('valid', 'completed', 'met'), computed)
    event = AlertEvent.objects.get(evaluation_id=evaluation.id)
    recipient = AlertRecipient.objects.create(patient_id=patient.id, alert_id=event.id,
        relation_id=relation.id, channel='in_app', delivery_status='not_sent')
    return SimpleNamespace(**base_scope, evaluation=evaluation, task=task, event=event, recipient=recipient)
