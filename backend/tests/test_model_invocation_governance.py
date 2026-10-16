"""Synthetic offline regression fixtures. Never calls a real model or proves clinical accuracy."""
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from recognition.invocation import (InvocationRejected, actual_cost, assert_authorized_result,
    canonical_hash, checked_candidates, contract_snapshot, failure_state, invoke_model, provider_timeout_seconds)
from recognition.invocation_runtime import provider_marker, require_authority
from recognition.provider_budget import _task_cost
from recognition.schemas import ArkCompletion, ReportCandidates


def provider():
    return SimpleNamespace(id='synthetic-provider', checksum='a' * 64, payload={
        'endpoint': 'https://synthetic.invalid/v1', 'model_version': 'synthetic-model-v1',
        'currency': 'CNY', 'input_per_million': '1', 'output_per_million': '2',
        'pricing_evidence_ref': 'SYNTHETIC-NOT-APPROVED'})


def completion(model='synthetic-model-v1', content='{"items": []}', finish='stop'):
    return ArkCompletion.model_validate({'id': 'synthetic-request', 'model': model,
        'usage': {'prompt_tokens': 10, 'completion_tokens': 5},
        'choices': [{'finish_reason': finish, 'message': {'content': content}}]})


@override_settings(ARK_BASE_URL='https://synthetic.invalid/v1', ARK_REQUEST_TIMEOUT_SECONDS=120,
    ARK_API_KEY='offline-fixture-unused')
class ModelContractTests(SimpleTestCase):
    def setUp(self):
        # Any accidentally unmocked provider access fails the test immediately.
        self.network = patch('urllib.request.urlopen', side_effect=AssertionError('REAL_PROVIDER_FORBIDDEN'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_contracts_hash_actual_templates_and_validators(self):
        from recognition.ark import OCR_SYSTEM, OCR_PROMPT
        report = contract_snapshot('report')
        self.assertEqual(report['prompt_sha256'], canonical_hash({'system': OCR_SYSTEM, 'prompt_template': OCR_PROMPT}))
        self.assertEqual(report['schema_sha256'], canonical_hash(ReportCandidates.model_json_schema()))
        for kind in ('report', 'therapy', 'voice', 'archive'):
            with self.subTest(kind=kind):
                first = contract_snapshot(kind)
                self.assertEqual(first, contract_snapshot(kind))
                self.assertEqual(len(first['prompt_sha256']), 64)
                self.assertEqual(len(first['schema_sha256']), 64)
        with patch('recognition.ark.OCR_PROMPT', OCR_PROMPT + ' changed'):
            self.assertNotEqual(report['prompt_sha256'], contract_snapshot('report')['prompt_sha256'])

    def invoke(self, body=None, marker=None, messages=None, **overrides):
        body = body or completion()
        kwargs = dict(before_provider_call=marker or Mock(), timeout_seconds=4.25, max_tokens=600)
        kwargs.update(overrides)
        def transport(*args, **options):
            options['before_provider_call']()
            return body, body.model_dump_json().encode()
        with patch('recognition.ark._completion', side_effect=transport) as mocked:
            result = invoke_model(messages or [{'role': 'user', 'content': 'synthetic'}], provider(), 'report', **kwargs)
        return result, mocked

    def test_precall_evidence_matches_rendered_messages_and_exact_model(self):
        marker = Mock()
        messages = [{'role': 'user', 'content': 'synthetic input A'}]
        result, transport = self.invoke(marker=marker, messages=messages)
        evidence = marker.call_args.args[0]
        self.assertEqual(evidence['messages_sha256'], canonical_hash(messages))
        self.assertEqual(evidence['requested_model'], provider().payload['model_version'])
        self.assertTrue(evidence['provider_called'])
        self.assertEqual(transport.call_args.kwargs['timeout_seconds'], 4.25)
        self.assertFalse(transport.call_args.kwargs['validate_output'])
        self.assertTrue(result['invocation']['model_authorized'])
        self.assertEqual(checked_candidates(result, provider(), ReportCandidates), {'items': []})
        changed, _ = self.invoke(messages=[{'role': 'user', 'content': 'synthetic input B'}])
        self.assertNotEqual(evidence['messages_sha256'], changed['invocation']['messages_sha256'])
        self.assertEqual(evidence['prompt_sha256'], changed['invocation']['prompt_sha256'])

    def test_unapproved_returned_model_keeps_raw_receipt_but_blocks_candidates(self):
        result, _ = self.invoke(body=completion(model='synthetic-model-v2'))
        self.assertIn(b'synthetic-model-v2', result['raw_response'])
        self.assertFalse(result['invocation']['model_authorized'])
        with self.assertRaisesMessage(InvocationRejected, 'MODEL_RETURNED_IDENTITY_UNAUTHORIZED'):
            checked_candidates(result, provider(), ReportCandidates)

    def test_marker_failure_prevents_call_and_is_not_retried(self):
        body = completion()
        sent = []
        def transport(*args, **kwargs):
            kwargs['before_provider_call']()
            sent.append(True)
            return body, b''
        with patch('recognition.ark._completion', side_effect=transport):
            with self.assertRaisesMessage(ValueError, 'cancelled'):
                invoke_model([], provider(), 'report', before_provider_call=Mock(side_effect=ValueError('cancelled')),
                    timeout_seconds=2, max_tokens=600)
        self.assertEqual(sent, [])

    def test_invalid_deadline_endpoint_and_ungoverned_routes_never_send(self):
        from recognition.ark import _completion, recognize_report_image, structure_voice_text
        for timeout in (0, -1, True, float('nan'), float('inf')):
            with self.subTest(timeout=timeout), self.assertRaises(InvocationRejected):
                invoke_model([], provider(), 'report', before_provider_call=Mock(), timeout_seconds=timeout, max_tokens=600)
        with self.assertRaisesMessage(Exception, 'MODEL_GOVERNED_INVOCATION_REQUIRED'):
            _completion([])
        with self.assertRaisesMessage(Exception, 'MODEL_GOVERNED_INVOCATION_REQUIRED'):
            recognize_report_image(b'fake', 'image/png')
        with self.assertRaisesMessage(Exception, 'VOICE_CONFIRMED_INPUT_REQUIRED'):
            structure_voice_text('synthetic')
        wrong = provider()
        wrong.payload['endpoint'] = 'https://other.invalid'
        with self.assertRaisesMessage(InvocationRejected, 'MODEL_PROVIDER_ENDPOINT_MISMATCH'):
            invoke_model([], wrong, 'report', before_provider_call=Mock(), timeout_seconds=1, max_tokens=600)

    def test_malformed_provider_envelope_returns_raw_receipt_before_rejection(self):
        response = Mock()
        response.read.return_value = b'{"not":"a provider envelope"}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch('urllib.request.urlopen', return_value=response):
            result = invoke_model([], provider(), 'report', before_provider_call=Mock(),
                timeout_seconds=2, max_tokens=600)
        self.assertEqual(result['raw_response'], b'{"not":"a provider envelope"}')
        self.assertIsNone(result['provider_request_id'])
        self.assertFalse(result['invocation']['response_envelope_valid'])
        with self.assertRaisesMessage(InvocationRejected, 'MODEL_RESPONSE_ENVELOPE_INVALID'):
            checked_candidates(result, provider(), ReportCandidates)

    def test_bad_schema_refusal_and_truncation_are_never_candidates(self):
        for content, finish in [('{"diagnosis":"invented"}', 'stop'), ('{"items":[]}', 'length')]:
            with self.subTest(content=content, finish=finish):
                result, _ = self.invoke(body=completion(content=content, finish=finish))
                self.assertTrue(result['raw_response'])
                with self.assertRaises(Exception):
                    checked_candidates(result, provider(), ReportCandidates)

    def test_timeout_uses_minimum_remaining_execution_runtime_and_client_limit(self):
        now = datetime(2026, 10, 10, tzinfo=dt_timezone.utc)
        task = SimpleNamespace(status='running', lease_until=now + timedelta(seconds=3),
            deadline_at=now + timedelta(seconds=90))
        with patch('recognition.invocation.timezone.now', return_value=now):
            self.assertEqual(provider_timeout_seconds(task, now + timedelta(seconds=10)), 10)
            self.assertEqual(provider_timeout_seconds(task, now + timedelta(seconds=10), {'total_timeout_ms': 2000}), 2)
            task.lease_until = now
            with self.assertRaises(InvocationRejected):
                provider_timeout_seconds(task, now + timedelta(seconds=10))

    def test_actual_cost_is_exact_and_unknown_usage_not_free(self):
        self.assertEqual(actual_cost({'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}, provider()), Decimal('0.00002'))
        self.assertIsNone(actual_cost({'usage': None}, provider()))
        self.assertIsNone(actual_cost({'usage': {}}, provider()))
        for invalid in (-1, True, '12'):
            with self.subTest(invalid=invalid), self.assertRaises(InvocationRejected):
                actual_cost({'usage': {'prompt_tokens': invalid, 'completion_tokens': 5}}, provider())

    def test_cancellation_distinguishes_unstarted_known_and_unknown_requests(self):
        self.assertEqual(failure_state({'provider_called': False}, True), 'cancelled')
        started = {'provider_called': True, 'phase': 'provider_started'}
        self.assertEqual(failure_state(started, True), 'uncertain')
        self.assertEqual(failure_state(started), 'uncertain')
        known = {**started, 'provider_request_id': 'synthetic', 'raw_response_file_id': 'synthetic-file'}
        self.assertEqual(failure_state(known, True), 'cancelled')
        self.assertEqual(failure_state(known), 'failed')
        self.assertEqual(failure_state({'provider_calls': [known, started]}, True), 'uncertain')

    def test_authority_rejects_stale_generation_cancel_and_expiry(self):
        now = datetime(2026, 10, 10, tzinfo=dt_timezone.utc)
        task = SimpleNamespace(status='running', lease_generation=1, lease_owner='owner',
            lease_until=now + timedelta(seconds=5), deadline_at=now + timedelta(seconds=20))
        attempt = SimpleNamespace(status='running', lease_generation=1)
        with patch('recognition.invocation_runtime.timezone.now', return_value=now):
            require_authority(task, attempt, 'owner', now + timedelta(seconds=10))
            for attribute, invalid in [('status', 'cancelled'), ('lease_generation', 2), ('lease_owner', 'other'), ('lease_until', now)]:
                previous = getattr(task, attribute)
                setattr(task, attribute, invalid)
                with self.subTest(attribute=attribute), self.assertRaises(InvocationRejected):
                    require_authority(task, attempt, 'owner', now + timedelta(seconds=10))
                setattr(task, attribute, previous)

    def test_archive_receipt_lock_order_matches_failure_path(self):
        from archives.page_execution import lock_receipt_attempts
        order = []
        with patch('archives.page_execution.Task.objects.get', return_value=SimpleNamespace(id='synthetic-task')), \
                patch('archives.page_execution.lock_receipt_identity', side_effect=lambda task: order.append('gate_patient_actor')), \
                patch('archives.page_execution.lock_task_page', side_effect=lambda task: order.append('batch_page')), \
                patch('archives.page_execution.Task.objects.select_for_update') as task_lock, \
                patch('archives.page_execution.TaskAttempt.objects.select_for_update') as attempt_lock, \
                patch('archives.page_execution.ArchivePageAttempt.objects.select_for_update') as page_lock:
            task_lock.return_value.get.side_effect = lambda **kwargs: order.append('task')
            attempt_lock.return_value.get.side_effect = lambda **kwargs: order.append('task_attempt')
            page_lock.return_value.get.side_effect = lambda **kwargs: order.append('page_attempt')
            lock_receipt_attempts('synthetic-task', 'synthetic-attempt', 'synthetic-page-attempt')
        self.assertEqual(order, ['gate_patient_actor', 'batch_page', 'task', 'task_attempt', 'page_attempt'])

    def test_explicit_workflow_bindings_support_one_provider_without_implicit_selection(self):
        from recognition.invocation_runtime import select_workflow_field_config
        selected = provider()
        selected.payload.update(field_config_id='report-id', workflow_field_config_ids={'therapy': 'therapy-id'})
        report = SimpleNamespace(id='report-id', checksum='b' * 64)
        therapy = SimpleNamespace(id='therapy-id', checksum='c' * 64)
        def query(**kwargs):
            field = {'report-id': ('report', report), 'therapy-id': ('therapy', therapy)}.get(kwargs['id'])
            return SimpleNamespace(first=lambda: field[1] if field and field[0] == kwargs['payload__kind'] else None)
        with patch('recognition.invocation_runtime.ConfigVersion.objects.filter', side_effect=query), \
                patch('recognition.invocation_runtime.configuration_qualification_reason', return_value=None):
            self.assertIs(select_workflow_field_config(selected, 'report'), report)
            self.assertIs(select_workflow_field_config(selected, 'therapy'), therapy)
            with self.assertRaisesMessage(InvocationRejected, 'MODEL_FIELD_CONFIGURATION_BINDING_CHANGED'):
                select_workflow_field_config(selected, 'therapy', 'unbound-id')
            del selected.payload['workflow_field_config_ids']
            with self.assertRaisesMessage(InvocationRejected, 'MODEL_FIELD_CONFIGURATION_BINDING_REQUIRED'):
                select_workflow_field_config(selected, 'therapy')
        selected.payload['workflow_field_config_ids'] = {'therapy': 'therapy-id'}
        with patch('recognition.invocation_runtime.ConfigVersion.objects.filter', side_effect=query), \
                patch('recognition.invocation_runtime.configuration_qualification_reason', return_value='CONFIG_APPROVAL_UNAVAILABLE'):
            with self.assertRaisesMessage(InvocationRejected, 'MODEL_FIELD_CONFIGURATION_UNQUALIFIED'):
                select_workflow_field_config(selected, 'therapy')

    def test_provider_schema_accepts_only_explicit_known_workflow_bindings(self):
        from archives.provider_policy import ArchiveProviderPolicy
        from configurations.schemas import validate_payload
        from configurations.service import compute_checksum
        payload = {**provider().payload, 'schema_version': 'provider-1', 'environment': 'test', 'provider': 'ark',
            'field_config_id': '11111111-1111-1111-1111-111111111111', 'evidence_refs': ['SYNTHETIC-ONLY']}
        self.assertEqual(ArchiveProviderPolicy.model_validate(payload).workflow_field_config_ids, {})
        original_checksum = compute_checksum(payload)
        payload['workflow_field_config_ids'] = {'therapy': '22222222-2222-2222-2222-222222222222'}
        validate_payload('provider', 'provider-1', payload)
        self.assertNotEqual(compute_checksum(payload), original_checksum)
        self.assertEqual(ArchiveProviderPolicy.model_validate(payload).workflow_field_config_ids, payload['workflow_field_config_ids'])
        payload['workflow_field_config_ids']['unregistered-workflow'] = '33333333-3333-3333-3333-333333333333'
        with self.assertRaises(Exception):
            ArchiveProviderPolicy.model_validate(payload)

    def test_stale_completed_cache_allows_fresh_governed_admission(self):
        from recognition.sync_images import replay_candidates
        for reason in ('MODEL_RUNTIME_CONFIGURATION_CHANGED', 'MODEL_PROVIDER_SELECTION_REQUIRED',
                'MODEL_FIELD_CONFIGURATION_BINDING_CHANGED'):
            with self.subTest(reason=reason), \
                    patch('recognition.sync_images.validate_invocation', side_effect=InvocationRejected(reason)), \
                    patch('recognition.sync_images.recognize_image_with_evidence') as provider_call:
                self.assertIsNone(replay_candidates(task=SimpleNamespace(), original=SimpleNamespace(),
                    actor=SimpleNamespace(), auth_epoch=1, kind='report'))
                provider_call.assert_not_called()
        with patch('recognition.sync_images.validate_invocation',
                side_effect=InvocationRejected('MODEL_RUNTIME_CONFIGURATION_UNQUALIFIED')):
            with self.assertRaises(InvocationRejected):
                replay_candidates(task=SimpleNamespace(), original=SimpleNamespace(),
                    actor=SimpleNamespace(), auth_epoch=1, kind='report')


class MultiPageBudgetTests(SimpleTestCase):
    def task(self, state):
        return SimpleNamespace(id='synthetic-task', schema_version='recognition-1', status=state,
            input_refs={'call_limit': 3, 'per_call_reserved_cost': '2', 'reserved_cost': '6',
                'provider_entry': {'currency': 'CNY', 'total_timeout_ms': 120000}})

    def cost(self, state, calls):
        attempt = SimpleNamespace(id='synthetic-attempt', usage_evidence={'provider_calls': calls})
        unknown = []
        cost = _task_cost(self.task(state), [attempt], 'CNY', InvocationRejected, unknown)
        return cost, unknown

    def test_inflight_reserves_remaining_pages_plus_measured_and_unknown_calls(self):
        measured = {'call_index': 0, 'reserved_cost': '2', 'actual_cost': '0.5', 'currency': 'CNY'}
        unknown = {'call_index': 1, 'reserved_cost': '2', 'actual_cost': None, 'provider_called': True}
        cost, retained = self.cost('running', [measured, unknown])
        self.assertEqual(cost, Decimal('4.5'))
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0]['call_index'], 1)

    def test_cancel_releases_unstarted_only_and_never_releases_unknown_cost(self):
        calls = [{'call_index': 0, 'reserved_cost': '2', 'actual_cost': '0.5', 'currency': 'CNY'},
            {'call_index': 1, 'reserved_cost': '2', 'actual_cost': None}]
        cost, retained = self.cost('cancelled', calls)
        self.assertEqual(cost, Decimal('2.5'))
        self.assertEqual(len(retained), 1)
        self.assertEqual(self.cost('cancelled', [])[0], 0)
        self.assertEqual(self.cost('running', [])[0], 6)

    def test_currency_mismatch_and_extra_call_fail_closed(self):
        with self.assertRaises(InvocationRejected):
            self.cost('completed', [{'call_index': 0, 'reserved_cost': '2', 'actual_cost': '0.5', 'currency': 'USD'}])
        with self.assertRaises(InvocationRejected):
            self.cost('completed', [{'call_index': i, 'reserved_cost': '2', 'actual_cost': None} for i in range(4)])


from django.test import TestCase
from django.utils import timezone
from uuid import uuid4


class DurableInvocationReceiptTests(TestCase):
    """PostgreSQL fixtures exercise persistence, never production approvals or HTTP."""
    def setUp(self):
        from files.models import FileGroup, FileObject
        from identity.models import Patient, Principal
        from retention.models import RetentionPolicy
        from sources.models import DataUseAuthorization
        from sources.service import register_source, register_revision
        from tasks.service import register_task, acquire_lease, create_attempt
        now = timezone.now()
        self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id='SIM-INVOCATION-RECEIPT', status='active', created_at=now)
        policy = RetentionPolicy.objects.create(policy_key='synthetic-invocation', version=1,
            data_class='report', rules={}, schema_version='retention-1', status='draft')
        source = register_source(source_key='synthetic-invocation', owner='simulation', location='local',
            purpose='test', sensitivity='synthetic', update_frequency='test')
        revision = register_revision(source=source, version='1', checksum='c' * 64,
            license='synthetic-test-only', allowed_use=['family_observation'])
        authorization = DataUseAuthorization.objects.create(patient=self.patient, source_revision=revision,
            purpose='family_observation', environment='test', evidence_ref='synthetic-test-fixture',
            approved_by=self.actor, approved_at=now)
        group = FileGroup.objects.create(patient=self.patient, owner=self.actor, version=1, status='available',
            source_revision=revision, use_authorization_id=authorization.id, created_at=now)
        self.original = FileObject.objects.create(patient=self.patient, file_group_id=group.id,
            object_key='synthetic-receipt-original', object_version='1', checksum='b' * 64, size_bytes=1,
            media_type='image/png', purpose='original', status='available', retention_policy=policy, created_at=now)
        self.task = register_task(actor=self.actor, patient=self.patient, task_type='ocr', schema_version='recognition-1',
            input_refs={'invocation_contract': contract_snapshot('report'),
                'provider_config_id': 'synthetic-provider', 'provider_checksum': 'a' * 64,
                'call_limit': 2, 'per_call_reserved_cost': '2', 'reserved_cost': '4',
                'provider_entry': {'currency': 'CNY', 'total_timeout_ms': 120000}})
        acquire_lease(self.task.id, 'synthetic-worker', now + timedelta(seconds=100))
        self.task.refresh_from_db()
        self.attempt = create_attempt(self.task, self.task.lease_generation)
        self.attempt.usage_evidence = {'phase': 'pre_provider', 'provider_called': False, 'provider_calls': []}
        self.attempt.save(update_fields=['usage_evidence'])
        self.until = now + timedelta(seconds=90)
        self.network = patch('urllib.request.urlopen', side_effect=AssertionError('REAL_PROVIDER_FORBIDDEN'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def start(self, call_index=0):
        marker = provider_marker(self.task, self.attempt, 'synthetic-worker', self.until, call_index=call_index)
        marker({**contract_snapshot('report'), 'provider_config_id': 'synthetic-provider',
            'provider_checksum': 'a' * 64, 'provider_called': True, 'phase': 'provider_started',
            'requested_model': 'synthetic-model-v1'})

    def test_precall_marker_is_durable_and_duplicate_call_is_rejected(self):
        self.start()
        self.attempt.refresh_from_db()
        self.assertTrue(self.attempt.usage_evidence['provider_calls'][0]['provider_called'])
        with self.assertRaisesMessage(InvocationRejected, 'MODEL_CALL_LIMIT_EXCEEDED'):
            self.start()

    def test_cancellation_before_marker_prevents_call(self):
        from tasks.service import cancel_task
        cancel_task(self.task.id)
        with self.assertRaisesMessage(InvocationRejected, 'MODEL_PROVIDER_CALL_AUTHORITY_UNAVAILABLE'):
            self.start()
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.usage_evidence['provider_calls'], [])

    def test_unknown_result_failure_and_cancel_retain_reservation(self):
        from recognition.invocation_runtime import record_invocation_failure
        from tasks.service import cancel_task
        self.start()
        cancel_task(self.task.id)
        record_invocation_failure(self.task.id, 'synthetic-worker', 'SYNTHETIC_NETWORK_FAILURE')
        self.task.refresh_from_db()
        self.attempt.refresh_from_db()
        self.assertEqual(self.task.status, 'uncertain')
        retained = []
        self.assertEqual(_task_cost(self.task, [self.attempt], 'CNY', InvocationRejected, retained), Decimal('2'))
        self.assertEqual(len(retained), 1)

    def persist(self, usage=None, returned_model='synthetic-model-v1'):
        from recognition.invocation_runtime import persist_call_receipt
        body = completion(model=returned_model)
        result = {'raw_response': body.model_dump_json().encode(), 'provider_request_id': body.id,
            'model': body.model, 'usage': usage, 'completion': body,
            'invocation': {'phase': 'provider_finished', 'provider_called': True,
                'requested_model': 'synthetic-model-v1', 'returned_model': body.model}}
        with patch('recognition.invocation_runtime.object_store.put_immutable') as store:
            response = persist_call_receipt(task=self.task, attempt=self.attempt,
                original=self.original, result=result, provider=provider(), call_index=0)
        self.assertEqual(store.call_count, 1)
        return response, result

    def test_unknown_model_is_persisted_then_rejected_and_billed(self):
        self.start()
        response, result = self.persist({'prompt_tokens': 10, 'completion_tokens': 5}, 'synthetic-unapproved')
        with self.assertRaisesMessage(InvocationRejected, 'MODEL_RETURNED_IDENTITY_UNAUTHORIZED'):
            checked_candidates(result, provider(), ReportCandidates)
        self.attempt.refresh_from_db()
        call = self.attempt.usage_evidence['provider_calls'][0]
        self.assertEqual(call['raw_response_file_id'], str(response.id))
        self.assertEqual(call['actual_cost'], '0.00002')
        self.assertEqual(call['returned_model'], 'synthetic-unapproved')

    def test_invalid_usage_cannot_erase_known_raw_receipt(self):
        from files.models import FileObject
        self.start()
        with self.assertRaisesMessage(InvocationRejected, 'MODEL_PROVIDER_USAGE_INVALID'):
            self.persist({'prompt_tokens': -1, 'completion_tokens': 5})
        self.attempt.refresh_from_db()
        call = self.attempt.usage_evidence['provider_calls'][0]
        self.assertTrue(FileObject.objects.filter(id=call['raw_response_file_id']).exists())
        self.assertIsNone(call['actual_cost'])
        self.assertEqual(failure_state(self.attempt.usage_evidence), 'failed')

    def test_late_receipt_after_cancellation_is_saved_without_candidates(self):
        from tasks.service import cancel_task
        from recognition.invocation_runtime import record_invocation_failure
        self.start()
        cancel_task(self.task.id)
        response, _ = self.persist({'prompt_tokens': 10, 'completion_tokens': 5})
        record_invocation_failure(self.task.id, 'synthetic-worker', 'SYNTHETIC_CANCEL')
        self.task.refresh_from_db()
        self.attempt.refresh_from_db()
        self.assertEqual(self.task.status, 'cancelled')
        self.assertEqual(self.attempt.usage_evidence['provider_calls'][0]['raw_response_file_id'], str(response.id))
        with self.assertRaises(InvocationRejected):
            require_authority(self.task, self.attempt, 'synthetic-worker', self.until)

    def test_repeated_sync_request_cannot_send_after_unknown_result(self):
        from files.models import FileGroup
        from recognition.sync_images import existing_sync_invocation
        self.task.schema_version = 'recognition-sync-1'
        self.task.input_refs.update(original_file_id=str(self.original.id),
            original_checksum=self.original.checksum, file_group_version='1')
        self.task.save(update_fields=['schema_version', 'input_refs'])
        self.start()
        for state in ('running', 'uncertain', 'cancelled'):
            self.task.status = state
            self.task.save(update_fields=['status'])
            with self.subTest(state=state), self.assertRaisesMessage(InvocationRejected, 'MODEL_PRIOR_INVOCATION_UNRESOLVED'):
                existing_sync_invocation(original=self.original, actor=self.actor,
                    group=FileGroup.objects.get(id=self.original.file_group_id), kind='report')
        self.task.input_refs['invocation_contract']['prompt_sha256'] = '0' * 64
        self.task.save(update_fields=['input_refs'])
        with self.assertRaisesMessage(InvocationRejected, 'MODEL_PRIOR_INVOCATION_UNRESOLVED'):
            existing_sync_invocation(original=self.original, actor=self.actor,
                group=FileGroup.objects.get(id=self.original.file_group_id), kind='report')
        # The helper is admission-only; no second provider marker or task appears.
        self.attempt.refresh_from_db()
        self.assertEqual(len(self.attempt.usage_evidence['provider_calls']), 1)

    def test_completed_sync_result_replays_receipt_without_provider_call(self):
        from recognition.sync_images import replay_candidates
        self.start()
        response, result = self.persist({'prompt_tokens': 10, 'completion_tokens': 5})
        reference = {'copy_id': 'synthetic-copy', 'checksum': 'f' * 64}
        self.task.input_refs['purpose_copy'] = reference
        self.task.status = 'completed'
        self.task.save(update_fields=['input_refs', 'status'])
        self.attempt.status = 'completed'
        self.attempt.save(update_fields=['status'])
        with patch('recognition.sync_images.validate_invocation', return_value=provider()) as policy_check, \
                patch('recognition.sync_images.model_copy_ref', return_value=reference) as purpose_check, \
                patch('recognition.sync_images.checked_bytes', return_value=result['raw_response']), \
                patch('recognition.sync_images.recognize_image_with_evidence') as provider_call:
            cached = replay_candidates(task=self.task, original=self.original, actor=self.actor,
                auth_epoch=self.actor.auth_epoch, kind='report')
        self.assertEqual(cached, {'items': []})
        policy_check.assert_called_once()
        purpose_check.assert_called_once()
        provider_call.assert_not_called()

    def test_contract_drift_is_rejected_before_marker_or_adoption(self):
        from recognition.invocation_runtime import validate_invocation
        self.task.input_refs['invocation_contract']['prompt_sha256'] = '0' * 64
        self.task.save(update_fields=['input_refs'])
        with self.assertRaisesMessage(InvocationRejected, 'MODEL_INVOCATION_CONTRACT_CHANGED'):
            self.start()
        with self.assertRaisesMessage(InvocationRejected, 'MODEL_INVOCATION_CONTRACT_CHANGED'):
            validate_invocation(self.task, 'report')
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.usage_evidence['provider_calls'], [])


    def test_generic_retry_cannot_convert_sync_therapy_into_async_report(self):
        from recognition.jobs import retry_recognition
        from recognition.service import RecognitionRejected
        self.task.schema_version = 'recognition-sync-1'
        self.task.status = 'cancelled'
        self.task.save(update_fields=['schema_version', 'status'])
        with self.assertRaisesMessage(RecognitionRejected, 'TASK_NOT_RETRYABLE'):
            retry_recognition(self.task, self.actor)
