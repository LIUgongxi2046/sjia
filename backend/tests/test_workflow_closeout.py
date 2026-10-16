"""Synthetic-only remaining core regressions; no provider/network/clinical data."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from pydantic import ValidationError

from authorization.models import AccessGrant
from authorization.policy import Denied
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from relations.inputs import ApplicationDecisionInput
from requests.models import WriteIntent, WriteRequest
from requests.record_intents import prepare_record_intent, record_request_view, retry_record_intent
from requests.service import IntentRejected, commit_intent, fail_intent, prepare_intent, retry_intent
from retention.models import RetentionPolicy
from tasks.registry import require_worker_event
from tasks.service import TaskRejected


class WorkflowContractTests(SimpleTestCase):
    def test_application_decision_requires_explicit_version_and_decision(self):
        for value in ({}, {'decision': 'approved'}, {'expected_version': '1'},
                {'decision': 'approved', 'expected_version': True},
                {'decision': 'approved', 'expected_version': '9223372036854775807'}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ApplicationDecisionInput.model_validate(value)
        checked = ApplicationDecisionInput.model_validate({'decision': 'approved', 'expected_version': '1', 'reason': 'Synthetic verified identity'})
        self.assertIsNone(checked.action)  # Approval never silently grants read permission.
        with self.assertRaises(ValidationError):
            ApplicationDecisionInput.model_validate({'decision': 'approved', 'expected_version': '1', 'action': 'manage_config'})

    def test_unknown_event_never_reaches_broker(self):
        from tasks.dispatcher import publish_task
        event = SimpleNamespace(event_type='unimplemented.requested')
        with patch('tasks.dispatcher.app.send_task') as broker, self.assertRaisesRegex(TaskRejected, 'TASK_EVENT_UNREGISTERED'):
            publish_task(event)
        broker.assert_not_called()

    def test_unknown_event_does_not_return_worker_success(self):
        from tasks.worker import consume
        event = SimpleNamespace(event_type='unimplemented.requested')
        # Pure registry dispatch contract: native consumer admission is checked
        # separately using a real restored database and old outbox rows.
        with patch('tasks.worker.OutboxEvent.objects') as rows, \
                patch('retention.content_fences.content_reader',return_value=nullcontext()):
            rows.filter.return_value.first.return_value = event
            with self.assertRaisesRegex(TaskRejected, 'TASK_EVENT_UNREGISTERED'):
                consume.run(str(uuid4()))

    def test_dynamic_reconciliation_is_registered(self):
        require_worker_event('alert_evaluation.trigger_requested')
        require_worker_event('alert_evaluation.static_finished')


class RecordRecoveryTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id='SYNTHETIC-RECOVERY', status='active', created_at=now)
        self.policy = RetentionPolicy.objects.create(policy_key='synthetic-workflow', version=1, data_class='request',
            rules={'retention_days': None, 'disposition': 'retain'}, schema_version='retention-1', status='active', approved_at=now,
            approved_by=self.actor, evidence_ref='synthetic-test-only')
        EnvironmentGate.objects.create(environment=settings.SJIA_ENVIRONMENT, version=1, enabled=True, updated_at=now)
        self.grant = AccessGrant.objects.create(patient=self.patient, principal=self.actor, action='write_record',
            field_codes=['temp'], record_scope='own', purpose='family_observation', granted_by=self.actor,
            evidence_ref='synthetic-test-only', valid_from=now)
        self.payload = {'kind': 'observation', 'capture_method': 'manual',
            'fields': [{'field_code': 'temp', 'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '0'}]}
        self.data = {'operation_id': 'record_create', 'target_key': f'/api/patients/{self.patient.id}/records',
            'request_id': str(uuid4()), 'schema_version': 'back-json-1', 'payload': self.payload}

    def prepare(self):
        return prepare_record_intent(actor=self.actor, auth_epoch=self.actor.auth_epoch, audience='family',
            data=self.data, retention_policy=self.policy)

    def view(self, intent):
        return record_request_view(actor=self.actor, auth_epoch=self.actor.auth_epoch, audience='family', intent=intent)

    def test_patient_binding_and_original_payload_survive_new_instance(self):
        intent = self.prepare()
        reloaded = WriteIntent.objects.get(id=intent.id)
        self.assertEqual(reloaded.patient_id, self.patient.id)
        view = self.view(reloaded)
        self.assertEqual(view['record_input'], self.payload)
        self.assertFalse(view['may_retry'])
        self.assertEqual(view['request_id'], self.data['request_id'])

    def test_revoked_scope_hides_recovery_payload(self):
        intent = self.prepare()
        self.grant.revoked_at = timezone.now()
        self.grant.save(update_fields=['revoked_at'])
        with self.assertRaises(Denied):
            self.view(intent)
        with self.assertRaises(Denied):
            retry_record_intent(actor=self.actor, auth_epoch=self.actor.auth_epoch, audience='family',
                intent_id=intent.id, expected_generation='1')

    def test_expired_original_key_renews_once_and_rejects_stale_generation(self):
        intent = self.prepare()
        WriteIntent.objects.filter(id=intent.id).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertTrue(self.view(intent)['may_retry'])
        renewed = retry_record_intent(actor=self.actor, auth_epoch=self.actor.auth_epoch, audience='family',
            intent_id=intent.id, expected_generation='1')
        self.assertEqual((renewed.request_id, renewed.generation), (intent.request_id, 2))
        with self.assertRaisesRegex(IntentRejected, 'RECORD_RETRY_NOT_AVAILABLE'):
            retry_record_intent(actor=self.actor, auth_epoch=self.actor.auth_epoch, audience='family',
                intent_id=intent.id, expected_generation='1')
        self.assertEqual(WriteIntent.objects.count(), 1)

    def test_persisted_unknown_receipt_prevents_retry(self):
        intent = self.prepare()
        WriteIntent.objects.filter(id=intent.id).update(status='failed', completed_at=timezone.now(), error_code='SYNTHETIC_FAILURE')
        WriteRequest.objects.create(actor=self.actor, patient=self.patient, action=intent.operation_id,
            target_key=intent.target_key, request_id=intent.request_id, payload_sha256=intent.payload_sha256,
            normalization_version=intent.normalization_version, status='uncertain', created_at=timezone.now())
        with self.assertRaisesRegex(IntentRejected, 'RECONCILIATION_REQUIRED'):
            self.view(intent)
        with self.assertRaisesRegex(IntentRejected, 'RECONCILIATION_REQUIRED'):
            retry_intent(intent=intent, expected_generation=1)

    def test_generic_retry_uses_locked_current_generation(self):
        intent = self.prepare()
        fail_intent(intent=intent, error_code='SYNTHETIC_FAILURE')
        stale = WriteIntent.objects.get(id=intent.id)
        retry_intent(intent=intent, expected_generation=1)
        with self.assertRaises(IntentRejected):
            retry_intent(intent=stale, expected_generation=1)
        intent.refresh_from_db()
        self.assertEqual(intent.generation, 2)

    def test_target_patient_cannot_be_confused(self):
        intent = self.prepare()
        other = Patient.objects.create(display_id='SYNTHETIC-OTHER', status='active', created_at=timezone.now())
        WriteIntent.objects.filter(id=intent.id).update(patient=other)
        with self.assertRaisesRegex(IntentRejected, 'RECORD_INTENT_MISMATCH'):
            self.view(intent)


@override_settings(SJIA_ENVIRONMENT='test')
class RecordReceiptReplayTests(TestCase):
    """The reachable POST route returns the created version, never a later correction."""
    def setUp(self):
        from tests.test_record_access_boundaries import RecordAccessBoundaryTest
        from tests.synthetic_fixtures import SyntheticEvidence
        RecordAccessBoundaryTest.setUp(self)
        self.evidence = SyntheticEvidence(self, self.principal)
        self.retention.schema_version = 'retention-1'
        self.retention.rules = {'retention_days': None, 'disposition': 'retain'}
        self.retention.approved_by = self.principal
        self.retention.approved_at = timezone.now()
        self.retention.evidence_ref = self.evidence.reference
        self.retention.save()
        revision = self.use_auth.source_revision
        revision.status = 'approved'; revision.retention_policy = self.retention
        revision.rights_evidence_ref = 'synthetic-test-only'
        revision.save()
        revision.source.status = 'approved'; revision.source.save(update_fields=['status'])

    def test_committed_create_replay_after_correction_returns_original_version(self):
        from records.correction import correct_record
        from records.models import RecordVersion
        from tests.test_record_access_boundaries import RecordAccessBoundaryTest
        RecordAccessBoundaryTest._grant(self, action='write_record')
        record = RecordAccessBoundaryTest._record(self)
        first = RecordVersion.objects.get(record_id=record.id, version=1)
        payload = {'kind': 'observation', 'capture_method': 'manual', 'fields': [
            {'field_code': 'allowed', 'concept_code': 'allowed-concept', 'value_type': 'text',
                'value_state': 'known', 'raw_text': 'allowed-value', 'sample_time_origin': 'observation'}]}
        target = f'/api/patients/{self.patient.id}/records'
        intent = prepare_record_intent(actor=self.principal, auth_epoch=self.principal.auth_epoch, audience='family',
            data={'operation_id': 'record_create', 'target_key': target, 'request_id': str(uuid4()),
                'schema_version': 'back-json-1', 'payload': payload}, retention_policy=self.retention)
        commit_intent(intent=intent, action='record_create', resource_type='record', resource_id=record.id)
        changed = deepcopy(payload['fields'])
        changed[0]['raw_text'] = 'synthetic-newer-value'
        correct_record(record=record, expected_version=1, actor=self.principal, reason='synthetic correction', fields=changed,
            use_authorization_id=self.use_auth.id, retention_policy=self.retention, source_revision=self.use_auth.source_revision)
        token = self.client.get('/api/auth/bootstrap').json()['csrf_token']
        response = self.client.post(target, data=payload, content_type='application/json', HTTP_X_CSRFTOKEN=token,
            HTTP_X_WRITE_INTENT=str(intent.id), HTTP_X_INTENT_GENERATION='1', HTTP_IDEMPOTENCY_KEY=str(intent.request_id))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {'patient_id': str(self.patient.id), 'record_id': str(record.id), 'record_version_id': str(first.id), 'version': 1})


    def _correction(self, kind='observation'):
        from tests.test_record_access_boundaries import RecordAccessBoundaryTest
        RecordAccessBoundaryTest._grant(self, action='correct_record')
        record = RecordAccessBoundaryTest._record(self)
        record.kind = kind; record.save(update_fields=['kind'])
        self.retention.data_class = kind; self.retention.save(update_fields=['data_class'])
        field = {key: None for key in ('raw_numeric', 'categorical_value', 'qualifier', 'raw_unit',
            'method', 'reference_interval', 'report_flag', 'medical_time')}
        field.update(field_code='allowed', concept_code='allowed-concept', original_name='allowed-name',
            value_type='text', value_state='known', raw_text='SYNTHETIC CORRECTED', sample_time_origin='observation')
        body = {'reason': 'Synthetic correction', 'expected_version': 1, 'fields': [field]}
        target = f'/api/records/{record.id}/corrections'
        intent = prepare_record_intent(actor=self.principal, auth_epoch=self.principal.auth_epoch, audience='family',
            data={'operation_id': 'record_correct', 'target_key': target, 'request_id': str(uuid4()),
                'schema_version': 'back-json-1', 'payload': body}, retention_policy=self.retention)
        return record, body, intent

    def _post_intent(self, body, intent):
        token = self.client.get('/api/auth/bootstrap').json()['csrf_token']
        return self.client.post(intent.target_key, data=body, content_type='application/json', HTTP_X_CSRFTOKEN=token,
            HTTP_X_WRITE_INTENT=str(intent.id), HTTP_X_INTENT_GENERATION=str(intent.generation),
            HTTP_IDEMPOTENCY_KEY=str(intent.request_id))

    def test_observation_and_report_corrections_recover_exact_committed_version(self):
        from records.correction import correct_record
        from records.models import RecordVersion
        for kind in ('observation', 'report'):
            with self.subTest(kind=kind):
                record, body, intent = self._correction(kind)
                first = self._post_intent(body, intent)
                self.assertEqual(first.status_code, 201, first.content)
                receipt = first.json()
                intent.refresh_from_db()
                self.assertEqual(intent.status, 'committed')
                self.assertEqual(intent.write_request.resource_type, 'record_version')
                body_next = deepcopy(body['fields']); body_next[0]['raw_text'] = 'SYNTHETIC LATER CORRECTION'
                correct_record(record=record, expected_version=2, actor=self.principal, reason='Synthetic later change',
                    fields=body_next, use_authorization_id=self.use_auth.id, retention_policy=self.retention,
                    source_revision=self.use_auth.source_revision)
                replay = self._post_intent(body, intent)
                self.assertEqual(replay.status_code, 200, replay.content)
                self.assertEqual(replay.json(), receipt)
                view = record_request_view(actor=self.principal, auth_epoch=self.principal.auth_epoch,
                    audience='family', intent=intent)
                self.assertEqual(view['receipt'], receipt)
                self.assertFalse(view['may_retry'])
                self.assertEqual(RecordVersion.objects.filter(record_id=record.id).count(), 3)

    def test_restricted_patient_cannot_commit_prepared_correction(self):
        from records.models import RecordVersion
        record, body, intent = self._correction()
        Patient.objects.filter(id=self.patient.id).update(status='restricted')
        response = self._post_intent(body, intent)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['code'], 'PATIENT_UNAVAILABLE')
        self.assertEqual(RecordVersion.objects.filter(record_id=record.id).count(), 1)
        intent.refresh_from_db(); self.assertEqual(intent.status, 'prepared')

    def test_unprepared_correction_cannot_create_a_new_version(self):
        from records.models import RecordVersion
        record, body, intent = self._correction()
        token = self.client.get('/api/auth/bootstrap').json()['csrf_token']
        response = self.client.post(intent.target_key, data=body, content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(response.json()['code'], 'WRITE_INTENT_REQUIRED')
        self.assertEqual(RecordVersion.objects.filter(record_id=record.id).count(), 1)


class ApplicationDecisionTests(TestCase):
    def setUp(self):
        from relations.service import create_application
        now = timezone.now()
        self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id='SYNTHETIC-ASSOCIATION', status='active', created_at=now)
        self.application = create_application(applicant=self.actor, evidence_ref='SYNTHETIC TEST ONLY', requested_access='read')

    def test_unbound_application_requires_explicit_patient(self):
        from relations.service import RelationRejected, decide_application
        with self.assertRaisesRegex(RelationRejected, 'APPLICATION_PATIENT_REQUIRED'):
            decide_application(application=self.application, decision='approved', checked_by=self.actor, expected_version=1)
        self.application.refresh_from_db()
        self.assertEqual((self.application.status, self.application.version), ('pending', 1))
        relation = decide_application(application=self.application, decision='approved', checked_by=self.actor,
            expected_version=1, patient=self.patient, reason='Synthetic evidence checked')
        self.assertEqual(relation.patient_id, self.patient.id)
        self.assertFalse(AccessGrant.objects.exists())
        self.application.refresh_from_db()
        self.assertEqual((self.application.patient_id, self.application.version), (self.patient.id, 2))

    def test_prebound_patient_cannot_be_reassigned(self):
        from relations.service import RelationRejected, decide_application
        self.application.patient = self.patient; self.application.save(update_fields=['patient'])
        other = Patient.objects.create(display_id='SYNTHETIC-OTHER-ASSOCIATION', status='active', created_at=timezone.now())
        with self.assertRaisesRegex(RelationRejected, 'APPLICATION_PATIENT_MISMATCH'):
            decide_application(application=self.application, decision='approved', checked_by=self.actor,
                expected_version=1, patient=other)

    def test_rejection_retains_versioned_reason_without_relation(self):
        from relations.models import Relation
        from relations.service import decide_application
        result = decide_application(application=self.application, decision='rejected', checked_by=self.actor,
            expected_version=1, reason='Synthetic incomplete proof')
        self.assertIsNone(result)
        self.assertFalse(Relation.objects.exists())
        self.application.refresh_from_db()
        self.assertEqual(self.application.reason, 'Synthetic incomplete proof')


class ApplicationReviewHTTPTests(TestCase):
    def setUp(self):
        from authorization.models import ServiceRoleGrant
        from tests.synthetic_fixtures import ensure_synthetic_audit_policy
        ApplicationDecisionTests.setUp(self)
        ensure_synthetic_audit_policy(self.actor, case=self)
        self.gate = EnvironmentGate.objects.create(environment=settings.SJIA_ENVIRONMENT, version=1,
            enabled=True, updated_at=timezone.now())
        self.grant = ServiceRoleGrant.objects.create(principal=self.actor, environment=self.gate,
            action='decide_relation', granted_by=self.actor, evidence_ref='SYNTHETIC TEST ONLY', valid_from=timezone.now())
        session = self.client.session
        session['_sjia_principal_id'] = str(self.actor.id)
        session['_sjia_auth_epoch'] = self.actor.auth_epoch
        session['_sjia_audience'] = 'support'
        session.save()
        self.path = f'/api/support/relation-applications/{self.application.id}'
        self.csrf = self.client.get('/api/auth/bootstrap').json()['csrf_token']

    def test_exact_application_review_and_explicit_binding(self):
        review = self.client.get(self.path)
        self.assertEqual(review.status_code, 200, review.content)
        self.assertIsNone(review.json()['patient_id'])
        self.assertEqual(review.json()['version'], '1')
        for data in ({'decision': 'approved', 'expected_version': '1', 'patient_id': str(self.patient.id)},
                {'decision': 'rejected', 'expected_version': '1', 'reason': '   '}):
            rejected = self.client.post(self.path + '/decide', data, content_type='application/json', HTTP_X_CSRFTOKEN=self.csrf)
            self.assertEqual(rejected.status_code, 422, rejected.content)
        response = self.client.post(self.path + '/decide', {'decision': 'approved', 'expected_version': '1',
            'patient_id': str(self.patient.id), 'reason': 'Synthetic identity proof checked'},
            content_type='application/json', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['version'], '2')
        saved = self.client.get(self.path).json()
        self.assertEqual(saved['patient_id'], str(self.patient.id))
        self.assertEqual(saved['reason'], 'Synthetic identity proof checked')
        self.assertFalse(AccessGrant.objects.exists())
        from relations.models import Relation
        again = self.client.post(self.path + '/decide', {'decision': 'approved', 'expected_version': '1',
            'patient_id': str(self.patient.id), 'reason': 'Synthetic identity proof checked'},
            content_type='application/json', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertGreaterEqual(again.status_code, 400)
        self.assertEqual(Relation.objects.filter(application=self.application).count(), 1)
        self.assertFalse(AccessGrant.objects.exists())
        self.assertEqual(self.client.get(self.path).json()['version'], '2')

    def test_stale_pending_application_version_cannot_decide(self):
        response = self.client.post(self.path + '/decide', {'decision': 'rejected', 'expected_version': '2',
            'reason': 'Synthetic stale review'}, content_type='application/json', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertGreaterEqual(response.status_code, 400)
        self.application.refresh_from_db()
        self.assertEqual((self.application.status, self.application.version), ('pending', 1))

    def test_revoked_service_role_cannot_read_or_decide(self):
        self.grant.revoked_at = timezone.now(); self.grant.save(update_fields=['revoked_at'])
        self.assertEqual(self.client.get(self.path).status_code, 403)
        response = self.client.post(self.path + '/decide', {'decision': 'rejected', 'expected_version': '1',
            'reason': 'Synthetic incomplete proof'}, content_type='application/json', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 403, response.content)
        self.application.refresh_from_db(); self.assertEqual(self.application.status, 'pending')
