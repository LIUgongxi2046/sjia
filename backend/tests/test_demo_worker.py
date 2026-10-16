"""The bounded family consumer transports genuine local work, never fixtures."""
import ast
import copy
import io
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from core.demo_experience import KEY, identity, seed_family_experience
from core.demo_worker import (LOCAL_EVENTS, RUNTIME_TASKS, consume_family_events_once,
    admit_family_worker, execute_local_event, require_family_worker_admission,
    require_local_runtime, worker_scope)


class DemoWorkerStaticTests(SimpleTestCase):
    def test_closed_dispatch_has_only_two_existing_local_services(self):
        self.assertEqual(set(LOCAL_EVENTS), {'file_validation.requested', 'summary.requested'})
        for event_type, (_, schema) in LOCAL_EVENTS.items():
            target = 'files.validation.execute_validation' if event_type.startswith('file_') else 'summaries.jobs.execute_summary'
            event = SimpleNamespace(event_type=event_type, schema_version=schema)
            with patch(target) as execute:
                execute_local_event(event, 'test-owner')
                execute.assert_called_once_with(event, 'test-owner')
        from tasks.registry import WORKER_EVENTS
        for event_type in WORKER_EVENTS - set(LOCAL_EVENTS):
            with self.subTest(event=event_type), self.assertRaisesMessage(CommandError, 'DEMO_WORKER_EVENT_NOT_ALLOWED'):
                execute_local_event(SimpleNamespace(event_type=event_type, schema_version='1'), 'owner')
        with self.assertRaisesMessage(CommandError, 'DEMO_WORKER_EVENT_NOT_ALLOWED'):
            execute_local_event(SimpleNamespace(event_type='summary.requested', schema_version='old'), 'owner')

    def test_no_credential_session_role_creation_or_generic_queue_calls(self):
        root = Path(__file__).parents[1]
        paths = [root / 'core/demo_worker.py', root / 'core/management/commands/run_family_experience_worker.py']
        forbidden = {'PrincipalCredential', 'Session', 'register_account', 'make_password', 'authenticate_account',
            'establish_session', 'dispatch_once', 'publish_task', 'consume', 'create_attempt', 'record_receipt',
            'complete_task', 'register_task', 'execute_reconcile'}
        for path in paths:
            tree = ast.parse(path.read_text())
            names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            self.assertFalse(forbidden & names)
            self.assertFalse({'auth_epoch', 'send_task', 'delay', 'apply_async', 'create', 'get_or_create'} & attributes)

    def runtime(self, tasks=None, providers=None):
        return SimpleNamespace(kind='provider', schema_version='backend-runtime-1', config_key=f'{KEY}:runtime',
            status='enabled', source_revision_id=identity('source-revision'), payload={
                'environment': 'test', 'tasks': tasks if tasks is not None else [{'task_type': name} for name in RUNTIME_TASKS],
                'providers': [] if providers is None else providers})

    def test_runtime_whitelist_rejects_extra_duplicate_missing_and_provider_entries(self):
        expected = [{'task_type': name} for name in sorted(RUNTIME_TASKS)]
        for tasks, providers in [(expected + [{'task_type': 'ocr'}], []), (expected[:-1], []),
                ([expected[0]] * 3, []), (expected, [{'provider_id': 'external'}]),
                ([{'task_type': []}] * 3, []), (['summary'] * 3, [])]:
            with self.subTest(tasks=tasks, providers=providers), self.assertRaisesMessage(
                    CommandError, 'DEMO_WORKER_REQUIRES_RESTRICTED_LOCAL_RUNTIME'):
                require_local_runtime(self.runtime(tasks, providers), 'test')
        with patch('core.demo_worker.validate_payload') as validate:
            runtime = self.runtime()
            require_local_runtime(runtime, 'test')
            validate.assert_called_once_with('provider', 'backend-runtime-1', runtime.payload)
        for key, value in [('config_key', 'other-runtime'), ('source_revision_id', uuid4()), ('status', 'draft')]:
            runtime = self.runtime()
            setattr(runtime, key, value)
            with self.assertRaises(CommandError):
                require_local_runtime(runtime, 'test')

    def test_non_debug_wrong_environment_or_database_stops_before_database_use(self):
        for debug, environment, database in [(False, 'test', 'test_sjia'),
                (True, 'production', 'test_sjia'), (True, 'test', 'sjia_real')]:
            with override_settings(DEBUG=debug, SJIA_ENVIRONMENT=environment), patch.dict(
                    connection.settings_dict, NAME=database), self.assertRaisesMessage(
                    CommandError, 'DEMO_REQUIRES_EXPLICIT_DEBUG_TEST_DATABASE'):
                consume_family_events_once(username='family', principal_id=uuid4(), acknowledge_test_only=True)
        with self.assertRaisesMessage(CommandError, 'DEMO_REQUIRES_EXPLICIT_DEBUG_TEST_DATABASE'):
            consume_family_events_once(username='family', principal_id=uuid4())

    def test_command_requires_exact_account_arguments(self):
        with self.assertRaises(CommandError):
            call_command('run_family_experience_worker', once=True, stdout=io.StringIO())

    def test_once_command_calls_only_bounded_consumer_and_does_not_sleep(self):
        output = io.StringIO()
        expected = {'claimed': 0}
        with patch('core.management.commands.run_family_experience_worker.consume_family_events_once',
                return_value=expected) as consume, patch('core.management.commands.run_family_experience_worker.time.sleep') as sleep, patch(
                'core.management.commands.run_family_experience_worker.admit_family_worker') as admit, patch(
                'core.management.commands.run_family_experience_worker.require_family_worker_admission') as require:
            call_command('run_family_experience_worker', username='family', principal_id='exact-id',
                acknowledge_test_only=True, once=True, limit=3, stdout=output)
        consume.assert_called_once_with(username='family', principal_id='exact-id', acknowledge_test_only=True, limit=3)
        admit.assert_called_once_with(username='family', principal_id='exact-id', acknowledge_test_only=True)
        require.assert_called_once()
        sleep.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), expected)

    def test_loop_revalidates_on_every_poll_and_can_stop_cleanly(self):
        with patch('core.management.commands.run_family_experience_worker.consume_family_events_once',
                side_effect=[{'claimed': 0}, KeyboardInterrupt]) as consume, patch(
                'core.management.commands.run_family_experience_worker.time.sleep') as sleep, patch(
                'core.management.commands.run_family_experience_worker.admit_family_worker'), patch(
                'core.management.commands.run_family_experience_worker.require_family_worker_admission') as require:
            call_command('run_family_experience_worker', username='family', principal_id='exact-id',
                acknowledge_test_only=True, poll_seconds=0.2, stdout=io.StringIO())
        self.assertEqual(consume.call_count, 2)
        self.assertEqual(require.call_count, 2)
        sleep.assert_called_once_with(0.2)
        for interval in (0, float('nan'), float('inf'), 61):
            with self.assertRaisesMessage(CommandError, 'DEMO_WORKER_POLL_SECONDS_MUST_BE_0_1_TO_60'):
                call_command('run_family_experience_worker', username='family', principal_id='exact-id',
                    poll_seconds=interval, once=True, stdout=io.StringIO())

    @override_settings(REQUIRE_PROCESS_RUNTIME_ADMISSION=False, DEBUG=True)
    def test_local_entrypoint_always_calls_real_worker_process_admission(self):
        calls = []
        with patch('core.demo_worker.worker_scope', side_effect=lambda **kw: calls.append('scope')), patch(
                'config.runtime_admission.admit_process_runtime', side_effect=lambda kind: calls.append(kind)), patch(
                'core.demo_worker.require_family_worker_admission', side_effect=lambda: calls.append('status')):
            admit_family_worker(username='family', principal_id=uuid4(), acknowledge_test_only=True)
        self.assertEqual(calls, ['scope', 'worker', 'status'])
        with patch('core.demo_worker.worker_scope', side_effect=CommandError('blocked')), patch(
                'config.runtime_admission.admit_process_runtime') as admit:
            with self.assertRaises(CommandError):
                admit_family_worker(username='family', principal_id=uuid4(), acknowledge_test_only=True)
        admit.assert_not_called()

    def test_process_drift_or_missing_local_capability_fail_closed(self):
        from config.runtime_admission import RuntimeAdmissionRejected
        good = {'state': 'admitted_current', 'reason_code': None,
            'loaded_config_version': {'process_kind': 'worker'},
            'capabilities': {'file_validation': {'state': 'budget_configured'}, 'summary': {'state': 'configured'}}}
        with patch('config.runtime_admission.admission_status', return_value=good):
            require_family_worker_admission()
        for bad in ({**good, 'loaded_config_version': None},
                {**good, 'state': 'not_ready', 'reason_code': 'PROCESS_RUNTIME_CONFIGURATION_DRIFT'},
                {**good, 'loaded_config_version': {'process_kind': 'api'}},
                {**good, 'capabilities': {'summary': {'state': 'not_configured'}}}):
            with patch('config.runtime_admission.admission_status', return_value=bad), self.assertRaises(RuntimeAdmissionRejected):
                require_family_worker_admission()


@override_settings(DEBUG=True, SJIA_ENVIRONMENT='synthetic-browser-demo')
class DemoWorkerDatabaseTests(TestCase):
    """Run only on the coordinator's isolated PostgreSQL test environment."""
    def setUp(self):
        # Reuse fixture setup without inheriting or collecting its test methods.
        from tests.test_family_experience_seed import FamilyExperienceSeedTests
        from tasks.models import OutboxEvent
        FamilyExperienceSeedTests.setUp(self)
        self.options = {'username': 'family-experience', 'principal_id': self.actor.id,
            'acknowledge_test_only': True}
        self.dataset = seed_family_experience(**self.options)
        from identity.models import Patient
        self.patient = Patient.objects.get(id=identity('patient:child_a'))
        # Seed services already executed these events genuinely; exercise the
        # same consumer's receipt-based transport acknowledgement before tests.
        drained = consume_family_events_once(**self.options)
        self.assertGreater(drained['completed'], 0)
        self.assertFalse(OutboxEvent.objects.filter(event_type__in=LOCAL_EVENTS, published_at__isnull=True).exists())

    def request_summary(self):
        from requests.policy import request_retention
        from summaries.submission import prepare_summary_intent, submit_summary
        from tasks.models import OutboxEvent, Task
        today = timezone.now().date()
        payload = {'from_date': (today - timedelta(days=14)).isoformat(), 'to_date': today.isoformat(),
            'scope_timezone': 'Asia/Shanghai', 'include_unknown_time': False,
            'record_kinds': ['observation', 'report', 'therapy'], 'include_source_status': True}
        target = f'/api/patients/{self.patient.id}/summaries'
        intent = prepare_summary_intent(actor=self.actor, auth_epoch=self.actor.auth_epoch, audience='family',
            data={'operation_id': 'summary_create', 'target_key': target, 'request_id': str(uuid4()),
                'schema_version': '1.0.0', 'payload': payload}, retention_policy=request_retention(settings.SJIA_ENVIRONMENT))
        view = submit_summary(actor=self.actor, patient=self.patient, auth_epoch=self.actor.auth_epoch,
            audience='family', target=target, data=payload, headers={'intent_id': str(intent.id),
                'generation': str(intent.generation), 'request_id': str(intent.request_id)})
        task = Task.objects.get(id=view['task_id'])
        return task, OutboxEvent.objects.get(task=task, event_type='summary.requested')

    def run_once(self, **changes):
        return consume_family_events_once(**{**self.options, **changes})

    def test_new_summary_requests_keep_completing_with_exactly_one_real_attempt(self):
        from summaries.models import Summary
        from summaries.service import read_summary
        from tasks.models import ConsumerReceipt, TaskAttempt
        for _ in range(2):
            task, event = self.request_summary()
            admission = copy.deepcopy(task.input_refs)
            self.assertEqual(task.status, 'accepted')
            self.assertFalse(Summary.objects.filter(task_id=task.id).exists())
            result = self.run_once()
            self.assertEqual((result['claimed'], result['completed'], result['errors']), (1, 1, 0))
            task.refresh_from_db(); event.refresh_from_db()
            self.assertEqual(task.status, 'completed')
            self.assertEqual(task.input_refs, admission)
            self.assertIsNotNone(event.published_at)
            summary = Summary.objects.get(task_id=task.id)
            self.assertTrue(read_summary(summary.id, self.patient)['items'])
            self.assertEqual(TaskAttempt.objects.filter(task=task, status='completed').count(), 1)
            self.assertEqual(ConsumerReceipt.objects.get(event=event, consumer='summary').effect_resource_id, summary.id)
            self.assertEqual(self.run_once()['claimed'], 0)

    def test_new_upload_runs_real_file_validation_without_ocr(self):
        from core.demo_files_summary import _attachment_upload, render_demo_report_png
        from files.models import FileGroup
        from files.validation import prepare_validation_intent, submit_validation
        from requests.policy import request_retention
        from retention.models import RetentionPolicy
        from sources.models import DataUseAuthorization, SourceRevision
        from tasks.models import ConsumerReceipt, OutboxEvent, Task, TaskAttempt
        from core.demo_experience import NAMESPACE
        source = SourceRevision.objects.get(id=identity('source-revision'))
        authorization = DataUseAuthorization.objects.get(id=identity('use:child_a'))
        content = render_demo_report_png(report_key='new-browser-upload', report_date=timezone.now().date(),
            rows=[('Platelets', '200', '10^9/L')])
        group, original = _attachment_upload(actor=self.actor, patient=self.patient, source_revision=source,
            use_authorization=authorization, retention_policy=RetentionPolicy.objects.get(id=identity('policy:file')),
            namespace=NAMESPACE, report_key='new-browser-upload', content=content)
        payload = {'expected_version': str(group.version), 'file_ids': [str(original.id)]}
        target = f'/api/uploads/{group.id}/complete'
        intent = prepare_validation_intent(actor=self.actor, auth_epoch=self.actor.auth_epoch, audience='family',
            data={'operation_id': 'file_group_complete', 'target_key': target, 'request_id': str(uuid4()),
                'schema_version': '1.0.0', 'payload': payload}, retention_policy=request_retention(settings.SJIA_ENVIRONMENT))
        view = submit_validation(actor=self.actor, auth_epoch=self.actor.auth_epoch, audience='family',
            target=target, payload=payload, headers={'X-Write-Intent': str(intent.id),
                'X-Intent-Generation': str(intent.generation), 'Idempotency-Key': str(intent.request_id)})
        task = Task.objects.get(id=view['task_id'])
        event = OutboxEvent.objects.get(task=task)
        self.assertEqual(task.status, 'accepted')
        self.assertEqual(self.run_once()['completed'], 1)
        task.refresh_from_db(); group.refresh_from_db()
        self.assertEqual((task.status, group.status), ('completed', FileGroup.Status.AVAILABLE))
        self.assertEqual(TaskAttempt.objects.filter(task=task, status='completed').count(), 1)
        self.assertEqual(ConsumerReceipt.objects.get(event=event, consumer='file_validation').effect_resource_id, group.id)
        self.assertFalse(Task.objects.filter(task_type='ocr').exists())

    def test_other_actor_patient_event_type_and_snapshot_are_never_claimed(self):
        from configurations.service import compute_checksum
        from identity.models import Patient, Principal
        from tasks.models import OutboxEvent, Task
        from tasks.outbox import emit_event
        base, _ = self.request_summary()
        now = timezone.now()
        other_actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        other_patient = Patient.objects.create(display_id='unrelated-worker-test', status='active', created_at=now)
        excluded = []
        for changes, context in [({'actor_id': other_actor.id}, {'actor_id': str(other_actor.id)}),
                ({'patient_id': other_patient.id}, {'patient_id': str(other_patient.id)}),
                ({}, {'audience': 'support'}), ({}, {'patient_id': str(identity('patient:child_b'))}),
                ({}, {'runtime_id': str(uuid4())})]:
            inputs = copy.deepcopy(base.input_refs)
            inputs['context'].update(context)
            values = {'actor_id': base.actor_id, 'patient_id': base.patient_id, 'task_type': 'summary',
                'schema_version': base.schema_version, 'input_refs': inputs, 'input_sha256': compute_checksum(inputs),
                'status': 'accepted', 'status_version': 1, 'created_at': now, 'deadline_at': base.deadline_at, **changes}
            task = Task.objects.create(**values)
            excluded.append(emit_event(event_type='summary.requested', schema_version=base.schema_version,
                aggregate_id=task.id, aggregate_version=1, correlation_id=uuid4(), task=task,
                resource_refs={'task_id': str(task.id)}).id)
        for event_type in ('recognition.requested', 'notification.native_requested', 'notification.material_requested',
                'disposition.local_requested', 'disposition.provider_requested', 'reconcile.requested'):
            excluded.append(emit_event(event_type=event_type, schema_version=base.schema_version,
                aggregate_id=base.id, aggregate_version=1, correlation_id=uuid4(), task=base,
                resource_refs={'task_id': str(base.id)}).id)
        result = self.run_once()
        self.assertEqual((result['claimed'], result['completed']), (1, 1))
        self.assertEqual(OutboxEvent.objects.filter(id__in=excluded, published_at__isnull=True,
            lease_generation=0, lease_owner__isnull=True).count(), len(excluded))

    def test_identity_mismatch_and_widened_runtime_fail_before_claim(self):
        from configurations.models import ConfigVersion
        from tasks.models import OutboxEvent
        _, event = self.request_summary()
        with self.assertRaisesMessage(CommandError, 'DEMO_ACCOUNT_IDENTITY_MISMATCH'):
            self.run_once(username='other-account')
        runtime = ConfigVersion.objects.get(id=self.dataset['runtime_config_id'])
        for change in ({'providers': [{'provider_id': 'external'}]},
                {'tasks': runtime.payload['tasks'] + [{**runtime.payload['tasks'][0], 'task_type': 'ocr'}]}):
            payload = {**runtime.payload, **change}
            ConfigVersion.objects.filter(id=runtime.id).update(payload=payload)
            with self.assertRaisesMessage(CommandError, 'DEMO_WORKER_REQUIRES_RESTRICTED_LOCAL_RUNTIME'):
                self.run_once()
        event.refresh_from_db()
        self.assertEqual(event.lease_generation, 0)
        self.assertFalse(OutboxEvent.objects.filter(id=event.id, published_at__isnull=False).exists())

    def test_revoked_family_permission_uses_real_failed_attempt_not_fake_success(self):
        from authorization.models import AccessGrant
        from summaries.models import Summary
        from tasks.models import TaskAttempt
        task, event = self.request_summary()
        AccessGrant.objects.filter(principal=self.actor, patient=self.patient, action='create_summary').update(revoked_at=timezone.now())
        result = self.run_once()
        task.refresh_from_db(); event.refresh_from_db()
        self.assertEqual((result['failed'], result['completed'], result['errors']), (1, 0, 1))
        self.assertEqual(task.status, 'failed')
        self.assertIsNotNone(event.published_at)
        self.assertEqual(TaskAttempt.objects.get(task=task).status, 'failed')
        self.assertFalse(Summary.objects.filter(task_id=task.id).exists())

    def test_deferred_event_is_delayed_without_task_mutation_then_retried(self):
        from summaries.jobs import SummaryDeferred
        from tasks.models import OutboxEvent, TaskAttempt
        task, event = self.request_summary()
        with patch('core.demo_worker.execute_local_event', side_effect=SummaryDeferred(5, 2)):
            result = self.run_once()
        task.refresh_from_db(); event.refresh_from_db()
        self.assertEqual((result['claimed'], result['deferred']), (1, 1))
        self.assertEqual(task.status, 'accepted')
        self.assertFalse(TaskAttempt.objects.filter(task=task).exists())
        self.assertIsNone(event.published_at)
        self.assertIsNone(event.lease_owner)
        self.assertGreater(event.available_at, timezone.now())
        self.assertEqual(self.run_once()['claimed'], 0)
        OutboxEvent.objects.filter(id=event.id).update(available_at=timezone.now())
        self.assertEqual(self.run_once()['completed'], 1)

    def test_crash_after_service_commit_preserves_exactly_once_effect(self):
        from summaries.models import Summary
        from tasks.models import OutboxEvent, TaskAttempt
        task, event = self.request_summary()
        with patch('core.demo_worker._settle', side_effect=RuntimeError('simulated process loss')):
            with self.assertRaises(RuntimeError):
                self.run_once()
        task.refresh_from_db(); event.refresh_from_db()
        self.assertEqual(task.status, 'completed')
        self.assertIsNone(event.published_at)
        self.assertIsNotNone(event.lease_owner)
        self.assertEqual(self.run_once()['claimed'], 0)
        OutboxEvent.objects.filter(id=event.id).update(lease_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.run_once()['completed'], 1)
        self.assertEqual(TaskAttempt.objects.filter(task=task).count(), 1)
        self.assertEqual(Summary.objects.filter(task_id=task.id).count(), 1)

    def test_scope_checks_are_read_only_and_never_query_credential_or_session_tables(self):
        statements = []
        def inspect(execute, sql, params, many, context):
            statements.append(sql)
            return execute(sql, params, many, context)
        with connection.execute_wrapper(inspect):
            scope = worker_scope(**self.options)
        self.assertEqual(scope.patient_ids, (identity('patient:child_a'), identity('patient:child_b')))
        for sql in statements:
            self.assertNotIn('principal_credential', sql.lower())
            self.assertNotIn('django_session', sql.lower())
            self.assertFalse(sql.lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')), sql)
