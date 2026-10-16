"""S02-B synthetic declarations only. No real identity, D02 proof or provider data."""
from copy import deepcopy
import json
import logging
from uuid import uuid4

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from pydantic import ValidationError

from audit.models import AuditEvent
from authorization.models import AccessGrant, ServiceRoleGrant
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from relations.application_requests import application_payload
from relations.inputs import ApplicationInput
from relations.models import Relation, RelationApplication
from requests.models import WriteIntent
from requests.service import compute_digest
from retention.models import RetentionPolicy


def declaration():
    return {'schema_version': '1.0.0', 'declared_patient_name': 'SYNTHETIC-PRIVATE-NAME',
        'declared_relationship': 'mother', 'guardian_scope_acknowledged': True}


def payload():
    return {'evidence_ref': 'SYNTHETIC APPLICATION EVIDENCE', 'note': None, 'requested_access': 'read_write',
        'applicant_declaration': declaration()}


class ApplicationDeclarationContractTests(SimpleTestCase):
    def test_legacy_payload_is_accepted_without_changing_canonical_hash(self):
        value = payload(); value.pop('applicant_declaration')
        normalized = application_payload('application_create', value)
        self.assertEqual(normalized, value)
        self.assertEqual(compute_digest('application_create', '/api/relation-applications', '1.0.0', normalized),
            compute_digest('application_create', '/api/relation-applications', '1.0.0', value))

    def test_new_complete_declaration_preserves_exact_self_report(self):
        self.assertEqual(application_payload('application_create', payload()), payload())

    def test_partial_null_false_coerced_unknown_and_empty_declarations_rejected(self):
        invalid = [None, {}, {**declaration(), 'guardian_scope_acknowledged': False},
            {**declaration(), 'guardian_scope_acknowledged': 1}, {**declaration(), 'guardian_scope_acknowledged': 'true'},
            {**declaration(), 'declared_patient_name': '  '}, {**declaration(), 'declared_patient_name': 'x' * 101},
            {**declaration(), 'declared_relationship': 'verified_mother'}, {**declaration(), 'schema_version': '2.0.0'},
            {key: value for key, value in declaration().items() if key != 'guardian_scope_acknowledged'}]
        for value in invalid:
            with self.subTest(kind=type(value).__name__), self.assertRaises(ValidationError):
                ApplicationInput.model_validate({**payload(), 'applicant_declaration': value})

    def test_applicant_cannot_inject_patient_or_verified_claims(self):
        for value in ({**payload(), 'patient_id': str(uuid4())},
                {**payload(), 'applicant_declaration': {**declaration(), 'patient_id': str(uuid4())}},
                {**payload(), 'declaration_verification': 'verified'},
                {**payload(), 'applicant_declaration': {**declaration(), 'verified': True}}):
            with self.assertRaises(ValidationError):
                ApplicationInput.model_validate(value)

    def test_validation_and_runtime_formatter_do_not_echo_declaration_values(self):
        value = {**payload(), 'applicant_declaration': {**declaration(), 'guardian_scope_acknowledged': False}}
        with self.assertRaises(ValidationError) as failure:
            ApplicationInput.model_validate(value)
        self.assertNotIn(declaration()['declared_patient_name'], str(failure.exception))
        from observability.middleware import SafeRuntimeFormatter
        record = logging.LogRecord('sjia.runtime', logging.INFO, '', 0, json.dumps(value), (), None)
        record.runtime_event = {'operation': 'application_list', 'status': 201, 'duration_seconds': 0.01,
            'payload': value, 'declared_patient_name': declaration()['declared_patient_name']}
        logged = SafeRuntimeFormatter().format(record)
        self.assertNotIn(declaration()['declared_patient_name'], logged)
        self.assertNotIn('applicant_declaration', logged)


@override_settings(SJIA_ENVIRONMENT='test-application-declarations')
class ApplicationDeclarationHTTPTests(TestCase):
    def setUp(self):
        from tests.synthetic_fixtures import ensure_synthetic_audit_policy
        now = timezone.now()
        self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.other = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        # Matching display text is deliberately present; it must never resolve a patient.
        self.patient = Patient.objects.create(display_id=declaration()['declared_patient_name'], status='active', created_at=now)
        self.gate = EnvironmentGate.objects.create(environment=settings.SJIA_ENVIRONMENT, version=1, enabled=True, updated_at=now)
        ensure_synthetic_audit_policy(self.actor, case=self)
        self.policy = RetentionPolicy.objects.create(policy_key='synthetic-app-request', version=1,
            data_class='request', status='active', schema_version='retention-1',
            rules={'retention_days': None, 'disposition': 'retain'}, approved_by=self.actor,
            approved_at=now, evidence_ref='SYNTHETIC REQUEST RETENTION')
        self.login(self.actor)
        self.csrf = self.client.get('/api/auth/bootstrap').json()['csrf_token']

    def login(self, actor, audience='family'):
        session = self.client.session
        session['_sjia_principal_id'] = str(actor.id)
        session['_sjia_auth_epoch'] = actor.auth_epoch
        session['_sjia_audience'] = audience
        session.save()

    def prepare(self, value, request_id=None):
        return self.client.post('/api/write-intents', {'operation_id': 'application_create',
            'target_key': '/api/relation-applications', 'request_id': request_id or str(uuid4()),
            'schema_version': '1.0.0', 'payload': value}, content_type='application/json', HTTP_X_CSRFTOKEN=self.csrf)

    def submit(self, value, intent):
        return self.client.post('/api/relation-applications', value, content_type='application/json',
            HTTP_X_CSRFTOKEN=self.csrf, HTTP_X_WRITE_INTENT=intent['intent_id'],
            HTTP_X_INTENT_GENERATION=intent['generation'], HTTP_IDEMPOTENCY_KEY=intent['request_id'])

    def create(self, value=None):
        value = value if value is not None else payload()
        prepared = self.prepare(value)
        self.assertEqual(prepared.status_code, 201, prepared.content)
        saved = self.submit(value, prepared.json())
        self.assertEqual(saved.status_code, 201, saved.content)
        return prepared.json(), saved.json()

    def test_creation_is_unbound_unverified_and_audit_has_no_declaration_text(self):
        _, receipt = self.create()
        application = RelationApplication.objects.get(id=receipt['resource_id'])
        self.assertEqual(application.applicant_declaration, declaration())
        self.assertIsNone(application.patient_id)
        self.assertEqual((application.status, application.version), ('pending', 1))
        self.assertFalse(Relation.objects.filter(application=application).exists())
        self.assertFalse(AccessGrant.objects.filter(principal=self.actor).exists())
        detail = self.client.get('/api/relation-applications/' + receipt['resource_id'])
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail.json()['applicant_declaration'], declaration())
        self.assertEqual(detail.json()['declaration_verification'], 'unverified')
        self.assertIsNone(detail.json()['patient_id'])
        listing = self.client.get('/api/relation-applications').json()
        self.assertEqual(listing['applications'][0]['declaration_verification'], 'unverified')
        audits = list(AuditEvent.objects.filter(actor=self.actor).values())
        self.assertTrue(audits)
        self.assertNotIn(declaration()['declared_patient_name'], str(audits))
        self.login(self.other)
        denied = self.client.get('/api/relation-applications/' + receipt['resource_id'])
        self.assertEqual(denied.status_code, 404)
        self.assertNotIn(declaration()['declared_patient_name'], denied.content.decode())

    def test_legacy_request_readback_and_hash_remain_compatible(self):
        value = payload(); value.pop('applicant_declaration')
        intent, receipt = self.create(value)
        row = WriteIntent.objects.get(id=intent['intent_id'])
        self.assertEqual(row.recovery_payload, value)
        self.assertEqual(row.payload_sha256, compute_digest('application_create', '/api/relation-applications', '1.0.0', value))
        detail = self.client.get('/api/relation-applications/' + receipt['resource_id']).json()
        self.assertIsNone(detail['applicant_declaration'])
        self.assertEqual(detail['declaration_verification'], 'not_provided')
        self.assertEqual(self.submit(value, intent).json(), receipt)

    def test_original_payload_replays_after_lost_response_without_duplicate(self):
        intent, receipt = self.create()
        query = self.client.get('/api/requests/' + intent['intent_id'])
        self.assertEqual(query.status_code, 200, query.content)
        self.assertEqual(query.json()['application_input'], payload())
        self.assertEqual(query.json()['receipt'], receipt)
        self.assertFalse(query.json()['may_retry'])
        self.assertEqual(self.submit(payload(), intent).json(), receipt)
        self.assertEqual(RelationApplication.objects.filter(applicant=self.actor).count(), 1)

    def test_changed_declaration_is_rejected_against_original_intent(self):
        prepared = self.prepare(payload()); self.assertEqual(prepared.status_code, 201, prepared.content)
        changed = deepcopy(payload()); changed['applicant_declaration']['declared_relationship'] = 'father'
        refused = self.submit(changed, prepared.json())
        self.assertEqual(refused.status_code, 409, refused.content)
        self.assertEqual(refused.json()['code'], 'APPLICATION_INTENT_MISMATCH')
        self.assertFalse(RelationApplication.objects.filter(applicant=self.actor).exists())
        self.assertEqual(WriteIntent.objects.get(id=prepared.json()['intent_id']).recovery_payload, payload())

    def test_invalid_acknowledgement_and_patient_injection_never_write_or_echo_name(self):
        bad = [{**payload(), 'patient_id': str(self.patient.id)},
            {**payload(), 'applicant_declaration': {**declaration(), 'patient_id': str(self.patient.id)}},
            {**payload(), 'applicant_declaration': {**declaration(), 'guardian_scope_acknowledged': False}},
            {**payload(), 'applicant_declaration': {key: value for key, value in declaration().items() if key != 'guardian_scope_acknowledged'}}]
        for value in bad:
            refused = self.prepare(value)
            self.assertEqual(refused.status_code, 422, refused.content)
            self.assertEqual(refused.json()['code'], 'APPLICATION_INPUT_INVALID')
            self.assertNotIn(declaration()['declared_patient_name'], refused.content.decode())
        self.assertFalse(RelationApplication.objects.filter(applicant=self.actor).exists())
        self.assertFalse(WriteIntent.objects.filter(actor=self.actor).exists())
        self.assertNotIn(declaration()['declared_patient_name'], str(list(AuditEvent.objects.filter(actor=self.actor).values())))

    def test_reviewer_requires_explicit_target_and_never_verifies_declared_name(self):
        _, receipt = self.create()
        self.login(self.actor, 'support')
        path = '/api/support/relation-applications/' + receipt['resource_id']
        self.assertEqual(self.client.get(path).status_code, 403)
        ServiceRoleGrant.objects.create(principal=self.actor, environment=self.gate, action='decide_relation',
            granted_by=self.actor, evidence_ref='SYNTHETIC REVIEW SCOPE', valid_from=timezone.now())
        current = self.client.get(path)
        self.assertEqual(current.status_code, 200, current.content)
        self.assertEqual(current.json()['applicant_declaration'], declaration())
        self.assertEqual(current.json()['declaration_verification'], 'unverified')
        action = {'decision': 'approved', 'expected_version': '1', 'reason': 'SYNTHETIC separate identity check'}
        rejected = self.client.post(path + '/decide', action, content_type='application/json', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(rejected.status_code, 422, rejected.content)
        action['patient_id'] = str(self.patient.id)
        decided = self.client.post(path + '/decide', action, content_type='application/json', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(decided.status_code, 200, decided.content)
        saved = self.client.get(path).json()
        self.assertEqual(saved['declaration_verification'], 'unverified')
        self.assertEqual(saved['applicant_declaration'], declaration())
        self.assertEqual(saved['patient_id'], str(self.patient.id))
        self.assertFalse(AccessGrant.objects.filter(principal=self.actor).exists())
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.display_id, declaration()['declared_patient_name'])
