"""Only synthetic policy/identity rows; real qualification, intents and tasks run."""
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone
from pydantic import ValidationError

from alerting.models import AlertPresetChoice
from alerting.policies import DynamicSchedulePolicy
from alerting.preset_policies import PatientPresetAssignment, PresetSelectionInput
from alerting.presets import directory, schedule_binding
from alerting.triggers import fanout_dynamic_change, execute_dynamic_trigger
from api.base import ApiError
from assessment.qualified_facts import QualificationRejected
from authorization.models import AccessGrant
from authorization.policy import Denied
from configurations.models import ConfigApproval
from requests.alert_presets import prepare_preset_intent, submit_preset, preset_request_view, retry_preset_intent
from requests.models import WriteIntent
from tasks.models import OutboxEvent, Task
from tasks.outbox import emit_event
from tests.synthetic_dynamic import synthetic_dynamic_native
from tests import test_dynamic_weighted_integration as weighted_fixtures
from tests.test_dynamic_weighted_contract import weighted_trigger, weighted_combination


class PatientAlertPresetTest(TestCase):
    def setUp(self):
        self.base = synthetic_dynamic_native(self, purpose='family_observation', execute_current=False)
        self.fx, self.actor, self.patient = self.base.fixture, self.base.actor, self.base.patient
        self.configs = self.base.configs
        self.window, self.mapping = self.configs['window'], self.configs['mapping']
        self.grant = AccessGrant.objects.create(patient=self.patient, principal=self.actor, relation_id=self.base.relation.id,
            action='select_alert_preset', field_codes=[], record_scope='all', purpose='family_observation',
            granted_by=self.actor, evidence_ref='SYNTHETIC SOFTWARE TEST ONLY', valid_from=timezone.now())
        payload = weighted_trigger(self.configs['rule'].payload)
        payload['combination'] = weighted_combination(members=[{'signal_ref': payload['conditions'][0]['signal_id'],
            'classification': 'core', 'weight': '1'}], evidence_refs=[self.fx.reference])
        self.trigger = self.fx.configuration('rule', payload)
        weighted_fixtures.WeightedDynamicIntegrationTest.matching_publication(self)
        self.schedules = [self.fx.configuration('provider', self.schedule_payload()) for _ in range(2)]
        self.assignment = self.fx.configuration('provider', self.assignment_payload())
        self.target = f'/api/patients/{self.patient.id}/alert-preset-selection'

    def replace_trigger(self, payload):
        self.trigger = self.fx.configuration('rule', payload)

    def schedule_payload(self):
        return {'schema_version': 'dynamic-schedule-2', 'environment': self.fx.gate.environment,
            'purpose': 'family_observation', 'patient_id': str(self.patient.id), 'actor_id': str(self.actor.id),
            'policy_id': str(self.trigger.id), 'input_rule_id': str(self.base.input_rule.id),
            'window_policy_id': str(self.window.id), 'verification_policy_id': str(self.configs['verification'].id),
            'use_authorization_id': str(self.base.authorization.id), 'observation_period_id': None,
            'static_assessments': [], 'event_types': ['clinical.context_changed'], 'watch_config_ids': [],
            'interval_seconds': None, 'starts_at': None, 'trigger_deadline_seconds': 120,
            'evidence_refs': [self.fx.reference]}

    def assignment_payload(self):
        return {'schema_version': 'patient-alert-presets-1', 'environment': self.fx.gate.environment,
            'patient_id': str(self.patient.id), 'purpose': 'family_observation', 'display_name': 'SYNTHETIC APPROVED CHOICES',
            'options': [{'schedule_config_id': str(row.id), 'label': f'SYNTHETIC {index}',
                'description': 'SOFTWARE TEST ONLY'} for index, row in enumerate(self.schedules)],
            'default_schedule_config_id': str(self.schedules[0].id), 'stale_selection_action': 'require_reselection',
            'evidence_refs': [self.fx.reference]}

    def payload(self, index=1, version='0'):
        return {'assignment_id': str(self.assignment.id), 'assignment_checksum': self.assignment.checksum,
            'expected_version': version, 'schedule_config_id': str(self.schedules[index].id)}

    def prepare(self, payload=None, request_id=None):
        return prepare_preset_intent(actor=self.actor, auth_epoch=self.actor.auth_epoch, audience='family',
            data={'operation_id': 'alert_preset_select', 'target_key': self.target,
                'request_id': str(request_id or uuid4()), 'schema_version': '1.0.0', 'payload': payload or self.payload()},
            retention_policy=self.fx.retention)

    def submit(self, intent, payload=None):
        return submit_preset(actor=self.actor, patient_id=self.patient.id, auth_epoch=self.actor.auth_epoch,
            audience='family', target=self.target, data=payload or self.payload(),
            headers={'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'generation': str(intent.generation)})

    def directory(self):
        return directory(actor=self.actor, patient_id=self.patient.id, auth_epoch=self.actor.auth_epoch, audience='family')

    def event(self):
        return emit_event(event_type='clinical.context_changed', schema_version='clinical-change-2',
            aggregate_id=self.patient.id, aggregate_version=self.patient.data_epoch, correlation_id=uuid4(),
            resource_refs={'patient_id': str(self.patient.id), 'environment': self.fx.gate.environment})

    def test_directory_requires_actual_grant_and_only_approved_ids(self):
        result = self.directory()
        self.assertEqual((result['selection_version'], result['selection_state']), ('0', 'professional_default'))
        self.assertEqual(result['selected_schedule_config_id'], str(self.schedules[0].id))
        self.assertEqual(len(result['options']), 2)
        self.grant.revoked_at = timezone.now(); self.grant.save()
        with self.assertRaises(Denied):
            self.directory()

    def test_idempotent_select_commit_receipt_query_and_old_intent_replay(self):
        intent = self.prepare()
        saved = self.submit(intent)
        self.assertEqual(saved['version'], '1')
        self.assertEqual(self.submit(intent), saved)
        duplicate = self.prepare(request_id=intent.request_id)
        self.assertEqual(duplicate.id, intent.id)
        state = preset_request_view(actor=self.actor, auth_epoch=self.actor.auth_epoch, audience='family', intent=duplicate)
        self.assertEqual((state['state'], state['receipt']), ('committed', saved))
        self.assertEqual(AlertPresetChoice.objects.count(), 1)
        self.assertEqual(self.directory()['selected_schedule_config_id'], str(self.schedules[1].id))

    def test_same_generation_race_loser_and_changed_payload_fail_closed(self):
        first, second = self.prepare(), self.prepare()
        self.submit(first)
        with self.assertRaisesMessage(ApiError, 'PRESET_VERSION_CONFLICT'):
            self.submit(second)
        with self.assertRaisesMessage(ApiError, 'IDEMPOTENCY_CONFLICT'):
            self.prepare(self.payload(0), first.request_id)
        self.assertEqual(AlertPresetChoice.objects.count(), 1)
        state = preset_request_view(actor=self.actor, auth_epoch=self.actor.auth_epoch, audience='family', intent=second)
        self.assertEqual((state['state'], state['may_retry'], state['error_code']),
            ('failed', False, 'PRESET_SELECTION_SUPERSEDED'))

    def test_expired_uncommitted_intent_has_explicit_same_key_retry(self):
        intent = self.prepare()
        WriteIntent.objects.filter(id=intent.id).update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaisesMessage(ApiError, 'PRESET_INTENT_EXPIRED'):
            self.submit(intent)
        intent.refresh_from_db()
        result = retry_preset_intent(actor=self.actor, auth_epoch=self.actor.auth_epoch, audience='family',
            intent=intent, expected_generation='1')
        self.assertEqual((result.id, result.generation), (intent.id, 2))
        self.assertEqual(self.submit(result)['version'], '1')

    def test_changed_field_scope_or_purpose_cannot_expand_selection_permission(self):
        for field, value in (('field_codes', ['only_this_field']), ('purpose', 'different_purpose')):
            old = getattr(self.grant, field); setattr(self.grant, field, value); self.grant.save()
            with self.subTest(field=field), self.assertRaises(Denied):
                self.prepare()
            setattr(self.grant, field, old); self.grant.save()
        with self.assertRaises(ValidationError):
            PresetSelectionInput.model_validate({**self.payload(), 'threshold_fraction': '0.1'})

    def test_revoked_assignment_or_schedule_is_never_restored_by_selection(self):
        for config in (self.assignment, self.schedules[1]):
            config.status = 'disabled'; config.save()
            with self.subTest(config=config.id), self.assertRaises(QualificationRejected):
                self.prepare()
            config.status = 'enabled'; config.save()
        ConfigApproval.objects.filter(config=self.assignment).update(revoked_at=timezone.now())
        with self.assertRaisesMessage(QualificationRejected, 'CONFIG_APPROVAL_UNAVAILABLE'):
            self.directory()

    def test_selected_guardian_grant_revocation_marks_choice_stale_without_default_fallback(self):
        self.submit(self.prepare())
        plan = DynamicSchedulePolicy.model_validate_json(__import__('json').dumps(self.schedules[1].payload))
        self.assertEqual(schedule_binding(self.schedules[1], plan)['selection_version'], '1')
        self.grant.revoked_at = timezone.now(); self.grant.save()
        with self.assertRaisesMessage(QualificationRejected, 'PRESET_SELECTION_STALE'):
            schedule_binding(self.schedules[1], plan)

    def test_fanout_only_selected_schedule_and_switch_fences_queued_old_task(self):
        source = self.event()
        fanout_dynamic_change(source)
        task = Task.objects.get(schema_version='dynamic-trigger-task-2')
        self.assertEqual(task.input_refs['schedule']['id'], str(self.schedules[0].id))
        self.assertEqual(task.input_refs['preset_selection']['selection_version'], '0')
        self.submit(self.prepare())
        requested = OutboxEvent.objects.get(task=task, event_type='alert_evaluation.trigger_requested')
        with self.assertRaises(QualificationRejected):
            execute_dynamic_trigger(requested, 'SYNTHETIC WORKER')
        self.assertFalse(Task.objects.filter(schema_version='dynamic-evaluation-task-2').exists())

    def test_default_and_selected_tasks_use_frozen_assignment_and_choice(self):
        for index, version in ((0, '0'), (1, '1')):
            if index:
                self.submit(self.prepare())
            source = self.event(); fanout_dynamic_change(source)
            task = Task.objects.filter(schema_version='dynamic-trigger-task-2').order_by('-created_at').first()
            requested = OutboxEvent.objects.get(task=task, event_type='alert_evaluation.trigger_requested')
            execute_dynamic_trigger(requested, 'SYNTHETIC WORKER')
            evaluation_task = Task.objects.filter(schema_version='dynamic-evaluation-task-2').order_by('-created_at').first()
            binding = evaluation_task.input_refs['snapshot']['automatic_trigger']['preset_selection']
            self.assertEqual((binding['schedule_id'], binding['selection_version']), (str(self.schedules[index].id), version))

    def test_replaced_assignment_requires_reselection_and_old_manual_tasks_cannot_bypass(self):
        from alerting.service import create_dynamic_task
        from alerting.triggers import schedule_payload
        import json
        intent = self.prepare(); self.submit(intent)
        self.assignment.status = 'disabled'; self.assignment.save()
        old = self.assignment
        self.assignment = self.fx.configuration('provider', self.assignment_payload())
        result = self.directory()
        self.assertEqual((result['selection_state'], result['selected_schedule_config_id']), ('requires_reselection', None))
        plan = DynamicSchedulePolicy.model_validate_json(json.dumps(self.schedules[1].payload))
        with self.assertRaisesMessage(QualificationRejected, 'PRESET_EVALUATION_SCOPE_INVALID'):
            create_dynamic_task(patient_id=self.patient.id, actor=self.actor, auth_epoch=self.actor.auth_epoch,
                environment=self.fx.gate.environment, payload=schedule_payload(plan, timezone.now().isoformat()))
        payload = self.payload(1, '1')
        self.submit(self.prepare(payload), payload)
        self.assertEqual(self.directory()['selection_state'], 'selected')

    def test_unlinked_write_request_is_unknown_and_never_retryable(self):
        from requests.models import WriteRequest
        intent = self.prepare()
        WriteRequest.objects.create(actor=self.actor, patient=self.patient, action='alert_preset_select',
            target_key=self.target, request_id=intent.request_id, payload_sha256=intent.payload_sha256,
            normalization_version=intent.normalization_version, expected_version=0, status='committed',
            resource_type='alert_preset_choice', resource_id=__import__('uuid').uuid4(),
            created_at=timezone.now(), completed_at=timezone.now())
        state = preset_request_view(actor=self.actor, auth_epoch=self.actor.auth_epoch, audience='family', intent=intent)
        self.assertEqual((state['state'], state['may_retry'], state['receipt']), ('unknown', False, None))
        with self.assertRaisesMessage(ApiError, 'PRESET_RESULT_UNKNOWN'):
            self.submit(intent)

    def test_actual_api_routes_directory_intent_commit_recovery_and_revocation(self):
        from tests.synthetic_fixtures import ensure_synthetic_audit_policy, ensure_synthetic_request_policy
        ensure_synthetic_audit_policy(self.actor, case=self)
        ensure_synthetic_request_policy(self.actor)
        session = self.client.session
        session['_sjia_principal_id'] = str(self.actor.id)
        session['_sjia_auth_epoch'] = self.actor.auth_epoch
        session['_sjia_audience'] = 'family'
        session.save()
        response = self.client.get(f'/api/patients/{self.patient.id}/alert-presets')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['selection_state'], 'professional_default')
        request_id = str(uuid4())
        response = self.client.post('/api/write-intents', {'operation_id': 'alert_preset_select',
            'target_key': self.target, 'request_id': request_id, 'schema_version': '1.0.0', 'payload': self.payload()},
            content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        intent = response.json()
        response = self.client.post(self.target, self.payload(), content_type='application/json',
            HTTP_X_WRITE_INTENT=intent['intent_id'], HTTP_IDEMPOTENCY_KEY=request_id,
            HTTP_X_INTENT_GENERATION=intent['generation'])
        self.assertEqual(response.status_code, 200, response.content)
        response = self.client.get(f'/api/requests/{request_id}', {'lookup': 'request_id',
            'operation_id': 'alert_preset_select', 'target_key': self.target})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['state'], 'committed')
        self.grant.revoked_at = timezone.now(); self.grant.save()
        response = self.client.get(f'/api/requests/{intent["intent_id"]}')
        self.assertEqual(response.status_code, 403, response.content)
        self.assertNotIn('preset_input', response.json())

    def published_for_separate_selector(self, boundary=None):
        from identity.models import Principal
        from relations.models import Relation, RelationApplication
        from alerting.models import AlertEvaluation, AlertEvent, AlertRecipient
        from alerting.service import execute_dynamic_evaluation
        now = timezone.now()
        professional = self.actor
        selector = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        application = RelationApplication.objects.create(version=1, applicant=selector, patient=self.patient,
            evidence_ref='SYNTHETIC SELECTOR', status='approved', checked_by=professional, checked_at=now, created_at=now)
        relation = Relation.objects.create(patient=self.patient, guardian=selector, application=application,
            status='active', version=1, checked_by=professional, checked_at=now, valid_from=now)
        changes = {boundary: now + timedelta(seconds=30)} if boundary else {}
        grant = AccessGrant.objects.create(patient=self.patient, principal=selector, relation_id=relation.id,
            action='select_alert_preset', field_codes=[], record_scope='all', purpose='family_observation',
            granted_by=professional, evidence_ref='SYNTHETIC SELECTOR', valid_from=now, **changes)
        self.actor = selector
        self.submit(self.prepare())
        self.actor = professional
        source = self.event(); fanout_dynamic_change(source)
        trigger = Task.objects.get(schema_version='dynamic-trigger-task-2')
        execute_dynamic_trigger(OutboxEvent.objects.get(task=trigger, event_type='alert_evaluation.trigger_requested'), 'synthetic')
        task = Task.objects.get(schema_version='dynamic-evaluation-task-2')
        execute_dynamic_evaluation(OutboxEvent.objects.get(task=task, event_type='alert_evaluation.requested'), 'synthetic')
        evaluation = AlertEvaluation.objects.get(task_id=task.id)
        event = AlertEvent.objects.get(evaluation_id=evaluation.id)
        recipient = AlertRecipient.objects.create(patient_id=self.patient.id, alert_id=event.id,
            relation_id=self.base.relation.id, channel='in_app', delivery_status='not_sent')
        return grant, relation, evaluation, event, recipient, now

    def assert_published_choice_unavailable(self, event, recipient):
        from family_content.alert_detail import alert_content, current_event_context
        from api.family_home import home_projection
        from notifications import native
        from notifications.errors import NotificationRejected
        self.fx.gate.refresh_from_db(); self.patient.refresh_from_db()
        _, reason = current_event_context(event, self.fx.gate, timezone.now())
        self.assertEqual(reason, 'PRESET_PUBLICATION_CHANGED')
        self.assertEqual(alert_content(event, ['synthetic_test'])['content_state'], 'unavailable')
        home = home_projection(patient=self.patient, principal=self.actor, field_codes=['synthetic_test'], environment=self.fx.gate.environment)
        self.assertEqual((home['current_alert_id'], home['assessment_state']), (None, 'stale'))
        with self.assertRaises((NotificationRejected, QualificationRejected)):
            native.authorize_native(recipient.id)

    def test_published_choice_fails_closed_on_selector_grant_revoke_without_support_read(self):
        from family_content.alert_detail import alert_content
        grant, _, _, event, recipient, _ = self.published_for_separate_selector()
        self.assertEqual(alert_content(event, ['synthetic_test'])['content_state'], 'available')
        grant.revoked_at = timezone.now(); grant.save()
        self.assert_published_choice_unavailable(event, recipient)

    def test_published_choice_fails_closed_on_selector_relation_revoke(self):
        from relations.service import revoke_relation
        _, relation, _, event, recipient, _ = self.published_for_separate_selector()
        revoke_relation(relation=relation, reason='SYNTHETIC REVOKE', revoked_by=self.actor)
        self.assert_published_choice_unavailable(event, recipient)

    def test_published_choice_checks_expiry_and_scheduled_revocation_boundaries(self):
        from unittest.mock import patch
        # Separate transactions for the two independent policy boundaries.
        from django.db import transaction
        for boundary in ('valid_until', 'revoked_at'):
            with self.subTest(boundary=boundary), transaction.atomic():
                grant, _, _, event, recipient, point = self.published_for_separate_selector(boundary)
                with patch('django.utils.timezone.now', return_value=point + timedelta(seconds=31)):
                    self.assert_published_choice_unavailable(event, recipient)
                transaction.set_rollback(True)

    def test_pre_authorized_native_send_rechecks_selector_before_accepting(self):
        from notifications import native
        from notifications.errors import NotificationRejected
        from notifications.models import NotificationReceipt
        grant, _, _, _, recipient, _ = self.published_for_separate_selector()
        attempt, _ = native.authorize_native(recipient.id)
        grant.revoked_at = timezone.now(); grant.save()
        with self.assertRaises((NotificationRejected, QualificationRejected)):
            native.submit_native(attempt.id)
        self.assertFalse(NotificationReceipt.objects.filter(attempt_id=attempt.id).exists())

    def test_accepted_native_mailbox_rechecks_selector_before_delivering(self):
        from notifications import native
        from tests.synthetic_fixtures import ensure_synthetic_audit_policy
        grant, _, _, _, recipient, _ = self.published_for_separate_selector()
        attempt, _ = native.authorize_native(recipient.id)
        native.submit_native(attempt.id)
        ensure_synthetic_audit_policy(self.actor, case=self)
        session = self.client.session
        session['_sjia_principal_id'] = str(self.actor.id)
        session['_sjia_auth_epoch'] = self.actor.auth_epoch
        session['_sjia_audience'] = 'family'
        session.save()
        url = f'/api/v2/notification-records/{recipient.id}/message'
        before = self.client.get(url)
        self.assertEqual(before.status_code, 200, before.content)
        grant.revoked_at = timezone.now(); grant.save()
        after = self.client.get(url)
        self.assertEqual(after.status_code, 422, after.content)
        self.assertEqual(after.json()['code'], 'PRESET_EVALUATION_SCOPE_INVALID')
        self.assertNotIn('body', after.json())
