"""Synthetic admission tests for explicitly governed request recovery payloads."""
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import patch
from uuid import uuid4

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from api.views_family2 import intent_prepare
from authorization.models import AccessGrant
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from requests.models import WriteIntent
from requests.policy import request_retention, require_request_policy_binding
from requests.service import IntentRejected
from retention.models import RetentionPolicy
from tests.synthetic_fixtures import ensure_synthetic_audit_policy


@override_settings(SJIA_ENVIRONMENT='test')
class RequestRetentionAdmissionTest(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.actor = Principal.objects.create(account_status='active', created_at=self.now, updated_at=self.now)
        ensure_synthetic_audit_policy(self.actor, case=self)
        self.gate = EnvironmentGate.objects.create(environment='test', version=1, enabled=True, updated_at=self.now)
        self.patient = Patient.objects.create(display_id='SYNTHETIC-REQUEST-POLICY', status='active', created_at=self.now)
        AccessGrant.objects.create(principal=self.actor, patient=self.patient, action='write_record', field_codes=['temp'],
            record_scope='own', purpose='family_observation', granted_by=self.actor, valid_from=self.now,
            evidence_ref='synthetic-test-only://request-scope')
        self.payload = {'kind': 'observation', 'capture_method': 'manual',
            'fields': [{'field_code': 'temp', 'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '0'}]}
        self.data = {'operation_id': 'record_create', 'target_key': f'/api/patients/{self.patient.id}/records',
            'request_id': str(uuid4()), 'schema_version': 'back-json-1', 'payload': self.payload}

    def policy(self, **changes):
        values = {'policy_key': 'synthetic-request-' + str(uuid4()), 'version': 1, 'data_class': 'request',
            'schema_version': 'retention-1', 'rules': {'retention_days': None, 'disposition': 'retain'},
            'status': 'active', 'approved_by': self.actor, 'approved_at': self.now,
            'evidence_ref': 'synthetic-test-only://request-retention'}
        values.update(changes)
        return RetentionPolicy.objects.create(**values)

    def prepare(self, data=None):
        request = RequestFactory().post('/api/write-intents', data or self.data, content_type='application/json')
        request.session = {}
        with patch('api.views_family2.current_principal', return_value=(self.actor, self.actor.auth_epoch, 'family')):
            return intent_prepare(request)

    def test_unrelated_active_policy_is_not_used_for_recovery_payload(self):
        self.policy(data_class='observation')
        response = self.prepare()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data['code'], 'REQUEST_RETENTION_UNAVAILABLE')
        self.assertFalse(WriteIntent.objects.exists())

    def test_actual_approval_and_nonblank_provenance_are_required(self):
        for change in ({'approved_by': None}, {'approved_at': None}, {'approved_at': self.now + timedelta(days=1)},
                {'evidence_ref': ''}, {'evidence_ref': ' '}, {'status': 'draft'}):
            policy = self.policy(**change)
            with self.assertRaisesMessage(IntentRejected, 'REQUEST_RETENTION_UNAVAILABLE'): request_retention('test')
            policy.delete()
        self.policy(); self.actor.account_status = 'disabled'; self.actor.save(update_fields=['account_status'])
        with self.assertRaisesMessage(IntentRejected, 'REQUEST_RETENTION_UNAVAILABLE'): request_retention('test')

    def test_wrong_schema_and_untyped_rules_are_rejected(self):
        for changes in ({'schema_version': 'old'}, {'rules': {}}, {'rules': {'synthetic': True}},
                {'rules': {'retention_days': None, 'disposition': 'delete'}},
                {'rules': {'retention_days': True, 'disposition': 'delete'}},
                {'rules': {'retention_days': -1, 'disposition': 'delete'}}):
            policy = self.policy(**changes)
            with self.assertRaisesMessage(IntentRejected, 'REQUEST_RETENTION_UNAVAILABLE'): request_retention('test')
            policy.delete()

    def test_multiple_approved_request_policies_are_ambiguous(self):
        self.policy(); self.policy()
        response = self.prepare()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data['code'], 'REQUEST_RETENTION_UNAVAILABLE')
        self.assertFalse(WriteIntent.objects.exists())

    def test_recovery_payload_binds_only_exact_request_policy(self):
        unrelated = self.policy(data_class='observation')
        selected = self.policy()
        response = self.prepare()
        self.assertEqual(response.status_code, 201, response.data)
        intent = WriteIntent.objects.get(id=response.data['intent_id'])
        self.assertEqual(intent.retention_policy_id, selected.id)
        self.assertNotEqual(intent.retention_policy_id, unrelated.id)
        self.assertEqual(intent.recovery_payload, self.payload)
        self.assertEqual(intent.patient_id, self.patient.id)
        self.assertEqual(self.prepare().data['intent_id'], str(intent.id))
        self.assertEqual(WriteIntent.objects.count(), 1)

    def test_existing_identity_cannot_silently_change_retention_binding(self):
        original = self.policy(); response = self.prepare(); self.assertEqual(response.status_code, 201)
        original.status = 'disabled'; original.save(update_fields=['status'])
        replacement = self.policy()
        response = self.prepare()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'REQUEST_RETENTION_CHANGED')
        self.assertEqual(WriteIntent.objects.get().retention_policy_id, original.id)
        with self.assertRaisesMessage(IntentRejected, 'REQUEST_RETENTION_CHANGED'):
            require_request_policy_binding(actor=self.actor, data=self.data, policy=replacement)

    def test_retention_period_is_never_invented(self):
        policy = self.policy()
        self.assertEqual(request_retention('test').rules, {'retention_days': None, 'disposition': 'retain'})
        policy.rules = {'retention_days': 17, 'disposition': 'delete'}; policy.save(update_fields=['rules'])
        self.assertEqual(request_retention('test').rules['retention_days'], 17)

    def test_own_policy_operations_bypass_unused_generic_selector(self):
        fake = NS(id=uuid4(), request_id=uuid4(), generation=1, status='prepared',
            expires_at=self.now + timedelta(minutes=1), payload_sha256='a' * 64)
        for operation, service in [('correction_request_create', 'prepare_correction_intent'),
                ('professional_record_create', 'prepare_professional_intent')]:
            with patch('requests.policy.request_retention', side_effect=AssertionError('unused selector must not run')):
                with patch('api.views_family2.' + service, return_value=fake) as prepare:
                    response = self.prepare({**self.data, 'operation_id': operation})
                    self.assertEqual(response.status_code, 201, response.data)
                    prepare.assert_called_once()
