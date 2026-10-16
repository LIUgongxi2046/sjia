"""Synthetic controlled correction write/recovery and professional queue contracts."""
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from django.http import QueryDict
from django.test import TestCase, override_settings
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from audit.models import AuditEvent
from records.correction import CorrectionRejected
from requests.models import WriteIntent, WriteRequest
from tests.test_clinical_closeout import SyntheticClinicalFixture
from verification.corrections import PROTOCOL
from verification.correction_requests import (prepare_correction_intent, submit_correction, correction_request_view,
    retry_correction_intent, receipt)
from verification.correction_review import read_review, review_queue
from verification.models import CorrectionRequest


@override_settings(SJIA_ENVIRONMENT='test')
class CorrectionControlledRequestTest(SyntheticClinicalFixture, TestCase):
    def identity(self, support=False):
        return {'actor': self.reviewer if support else self.owner, 'epoch': 0,
            'audience': 'support' if support else 'family', 'environment': 'test'}

    def data(self):
        return {'request_id': str(uuid4()), 'operation_id': 'correction_request_create',
            'target_key': f'/api/records/{self.record.id}/correction-requests', 'schema_version': '1.0.0',
            'payload': {'record_version_id': str(self.original.id), 'field_ids': [str(self.field.id)],
                'reason': 'Synthetic request', 'proposed_text': None}}

    def submit(self, data, intent, support=False, **changes):
        values = {'operation': data['operation_id'], 'target': data['target_key'], 'payload': data['payload'],
            'headers': {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'generation': str(intent.generation)}}
        values.update(changes)
        return submit_correction(**values, **self.identity(support))

    def prepare_create(self):
        data = self.data()
        return data, prepare_correction_intent(data=data, **self.identity())

    def authorize_support(self):
        self.role(); self.grant(self.reviewer, 'verify_field'); self.grant(self.reviewer, 'correct_record')

    def resolution(self, row):
        return {'request_id': str(uuid4()), 'operation_id': 'correction_request_resolve',
            'target_key': f'/api/support/correction-requests/{row.id}/resolve', 'schema_version': '1.0.0',
            'payload': {'expected_version': str(row.version), 'decision': 'rejected', 'reason': 'Synthetic review', 'resolution_record_version_id': None}}

    def assert_schema(self, name, value):
        validator = Draft202012Validator({**PROTOCOL, '$ref': f'#/$defs/{name}'}, format_checker=FormatChecker())
        self.assertEqual(list(validator.iter_errors(value)), [])

    def test_create_commits_exact_audit_and_recoverable_receipt(self):
        data, intent = self.prepare_create()
        saved = self.submit(data, intent)
        intent.refresh_from_db()
        self.assert_schema('WriteReceipt', saved)
        view = correction_request_view(intent, **self.identity())
        self.assert_schema('RequestView', view)
        self.assertEqual(view['receipt'], saved)
        self.assertEqual(WriteRequest.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(request_id=intent.request_id, outcome='committed').count(), 1)
        self.assertEqual(self.submit(data, intent), saved)
        self.assertEqual(prepare_correction_intent(data=data, **self.identity()).id, intent.id)
        self.assertEqual(CorrectionRequest.objects.count(), 1)

    def test_prepare_does_not_accept_extra_fields_wrong_schema_or_route(self):
        for patch in ({'schema_version': '2.0.0'}, {'unexpected': True}, {'target_key': f'/api/records/{self.record.id}'},
                {'target_key': f'/api/v1/records/{self.record.id}/correction-requests'}):
            data = self.data(); data.update(patch)
            with self.assertRaises(Exception):
                prepare_correction_intent(data=data, **self.identity())
        self.assertFalse(WriteIntent.objects.exists())

    def test_wrong_audience_is_denied(self):
        with self.assertRaises(Exception):
            prepare_correction_intent(data=self.data(), **{**self.identity(), 'audience': 'support'})
        self.assertFalse(WriteIntent.objects.exists())

    def test_mutated_body_and_headers_have_no_effect(self):
        data, intent = self.prepare_create()
        payload = {**data['payload'], 'reason': 'Different'}
        with self.assertRaises(CorrectionRejected): self.submit(data, intent, payload=payload)
        for key, value in [('intent_id', str(uuid4())), ('request_id', str(uuid4())), ('generation', '2')]:
            headers = {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'generation': '1'}; headers[key] = value
            with self.assertRaises(Exception): self.submit(data, intent, headers=headers)
        self.assertFalse(CorrectionRequest.objects.exists())

    def test_same_identity_different_payload_is_rejected(self):
        data, intent = self.prepare_create(); data['payload']['reason'] = 'Mutated'
        with self.assertRaises(CorrectionRejected): prepare_correction_intent(data=data, **self.identity())
        self.assertEqual(WriteIntent.objects.count(), 1)

    def test_revoked_relation_blocks_submission_and_recovery(self):
        data, intent = self.prepare_create(); self.relation.status = 'revoked'; self.relation.save(update_fields=['status'])
        with self.assertRaises(Exception): self.submit(data, intent)
        with self.assertRaises(Exception): correction_request_view(intent, **self.identity())
        self.assertFalse(CorrectionRequest.objects.exists())

    def test_epoch_change_requires_new_generation_and_revalidation(self):
        data, intent = self.prepare_create(); self.patient.data_epoch += 1; self.patient.save(update_fields=['data_epoch'])
        with self.assertRaises(CorrectionRejected): self.submit(data, intent)
        view = correction_request_view(intent, **self.identity()); self.assertTrue(view['may_retry'])
        renewed = retry_correction_intent(intent, data={'expected_generation': '1'}, **self.identity())
        self.assertEqual(renewed.generation, 2)
        saved = self.submit(data, renewed); self.assertEqual(saved['version'], '1')

    def test_expired_intent_retry_exact_generation_once(self):
        data, intent = self.prepare_create(); intent.expires_at = timezone.now() - timedelta(seconds=1); intent.save(update_fields=['expires_at'])
        with self.assertRaises(CorrectionRejected): self.submit(data, intent)
        renewed = retry_correction_intent(intent, data={'expected_generation': '1'}, **self.identity())
        with self.assertRaises(CorrectionRejected): retry_correction_intent(renewed, data={'expected_generation': '1'}, **self.identity())
        self.submit(data, renewed)
        with self.assertRaises(CorrectionRejected): retry_correction_intent(renewed, data={'expected_generation': '2'}, **self.identity())
        self.assertEqual(CorrectionRequest.objects.count(), 1)

    def test_committed_receipt_remains_historical_after_record_revision(self):
        data, intent = self.prepare_create(); saved = self.submit(data, intent); intent.refresh_from_db()
        replacement = self.version(2); self.add_field(replacement); self.record.current_version = 2; self.record.save(update_fields=['current_version'])
        self.assertEqual(correction_request_view(intent, **self.identity())['receipt'], saved)

    def test_forged_saved_digest_or_missing_audit_rejects_receipt(self):
        data, intent = self.prepare_create(); self.submit(data, intent); intent.refresh_from_db()
        original = intent.payload_sha256; intent.payload_sha256 = 'b' * 64
        with self.assertRaises(CorrectionRejected): receipt(intent)
        intent.payload_sha256 = original
        AuditEvent.objects.filter(request_id=intent.request_id).update(outcome='denied')
        with self.assertRaises(CorrectionRejected): receipt(intent)

    def test_professional_resolution_receipt_and_queue_filters(self):
        data, intent = self.prepare_create(); saved = self.submit(data, intent)
        self.authorize_support()
        queue = review_queue(parameters=QueryDict(), **self.identity(True)); self.assert_schema('CorrectionReviewQueue', queue)
        self.assertEqual(len(queue['items']), 1)
        row = CorrectionRequest.objects.get(id=saved['resource_id']); data = self.resolution(row)
        intent = prepare_correction_intent(data=data, **self.identity(True))
        resolved = self.submit(data, intent, True); intent.refresh_from_db()
        self.assertEqual(resolved['version'], '2'); self.assertEqual(self.submit(data, intent, True), resolved)
        self.assertEqual(review_queue(parameters=QueryDict(), **self.identity(True))['items'], [])
        self.assert_schema('CorrectionReviewDetail', read_review(row.id, **self.identity(True)))

    def test_queue_excludes_missing_case_scope_and_family_audience(self):
        data, intent = self.prepare_create(); self.submit(data, intent); self.role()
        self.assertEqual(review_queue(parameters=QueryDict(), **self.identity(True))['items'], [])
        with self.assertRaises(Exception): review_queue(parameters=QueryDict(), **self.identity())

    def test_resolution_options_bind_actual_saved_current_version(self):
        data, intent = self.prepare_create(); saved = self.submit(data, intent); self.authorize_support()
        replacement = self.version(2); self.add_field(replacement); self.record.current_version = 2; self.record.save(update_fields=['current_version'])
        detail = read_review(saved['resource_id'], **self.identity(True)); self.assert_schema('CorrectionReviewDetail', detail)
        self.assertEqual(detail['resolution_options'][0]['record_version_id'], str(replacement.id))
        row = CorrectionRequest.objects.get(id=saved['resource_id']); data = self.resolution(row)
        data['payload'].update(decision='resolved', resolution_record_version_id=str(replacement.id))
        intent = prepare_correction_intent(data=data, **self.identity(True)); self.submit(data, intent, True)
        row.refresh_from_db(); self.assertEqual(row.resolution_record_version_id, replacement.id)

    def test_revoked_source_blocks_professional_read_and_submit(self):
        data, intent = self.prepare_create(); saved = self.submit(data, intent); self.authorize_support()
        row = CorrectionRequest.objects.get(id=saved['resource_id']); data = self.resolution(row)
        intent = prepare_correction_intent(data=data, **self.identity(True))
        self.authorization.revoked_at = timezone.now(); self.authorization.save(update_fields=['revoked_at'])
        with self.assertRaises(Exception): self.submit(data, intent, True)
        with self.assertRaises(Exception): read_review(row.id, **self.identity(True))
        self.assertEqual(review_queue(parameters=QueryDict(), **self.identity(True))['items'], [])


from django.test import TransactionTestCase


class CorrectionQueueLockOrderTest(TransactionTestCase):
    """Two PostgreSQL connections exercise the queue versus correction lock prefix."""
    def setUp(self):
        from identity.models import Principal
        from family_content.models import EnvironmentGate
        from authorization.models import ServiceRoleGrant
        self.environment = 'synthetic-correction-lock-' + str(uuid4())
        self.actor = Principal.objects.create(account_status='active', created_at=timezone.now(), updated_at=timezone.now())
        self.gate = EnvironmentGate.objects.create(environment=self.environment, version=1, enabled=True, updated_at=timezone.now())
        self.role = ServiceRoleGrant.objects.create(principal=self.actor, environment=self.gate, action='verify_field',
            granted_by=self.actor, evidence_ref='synthetic-test-only://lock-order', valid_from=timezone.now())

    def tearDown(self):
        # Explicitly remove only these synthetic rows; schema-qualified tables are not found by flush.
        self.role.delete(); self.gate.delete(); self.actor.delete()
        super().tearDown()

    def test_queue_and_correction_lock_prefix_acquire_gate_before_service_grant(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from django.db import close_old_connections, connection, transaction
        from family_content.models import EnvironmentGate
        from identity.models import Principal
        from authorization.policy import check_service_grant
        self.assertEqual(connection.vendor, 'postgresql')
        gate_locked, queue_attempted_gate = Event(), Event()

        def correction_lock_prefix():
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor: cursor.execute("SET LOCAL lock_timeout = '3s'")
                    EnvironmentGate.objects.select_for_update().get(environment=self.environment)
                    actor = Principal.objects.select_for_update().get(id=self.actor.id)
                    gate_locked.set()
                    if not queue_attempted_gate.wait(5): raise AssertionError('queue did not attempt the environment lock')
                    # This is the same gate/actor -> verify_field role edge as correction_scope.
                    check_service_grant(actor, 'verify_field', self.environment, timezone.now())
                    return True
            finally:
                gate_locked.set()
                connection.close()

        def queue_read():
            close_old_connections()
            def observe(execute, sql, params, many, context):
                if 'environment_gate' in sql and 'FOR UPDATE' in sql.upper(): queue_attempted_gate.set()
                return execute(sql, params, many, context)
            try:
                if not gate_locked.wait(5): raise AssertionError('correction lock was not acquired')
                with transaction.atomic(), connection.execute_wrapper(observe):
                    with connection.cursor() as cursor: cursor.execute("SET LOCAL lock_timeout = '3s'")
                    return review_queue(parameters=QueryDict(), actor=self.actor, epoch=0,
                        audience='support', environment=self.environment)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(correction_lock_prefix), pool.submit(queue_read)
            self.assertTrue(first.result(timeout=10))
            self.assertEqual(second.result(timeout=10), {'items': [], 'next_cursor': None})
