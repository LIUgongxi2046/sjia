"""Real task/persistence/approval paths for synthetic weighted sensitivity rules.

No patient data or clinical rule defaults. Existing numeric fixture prerequisites
are reused, but qualification, computation and publication are not mocked.
"""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from alerting.inputs import collect_dynamic_inputs
from alerting.models import AlertEvaluation, AlertEvent
from alerting.options import dynamic_creation_options
from alerting.service import create_dynamic_task, execute_dynamic_evaluation, read_dynamic_evaluation
from archives.models import ArchiveFactVersion
from assessment.qualified_facts import QualificationRejected
from configurations.models import ConfigApproval, ConfigValidation
from configurations.qualification import configuration_qualification_reason
from contacts.models import ContactVersion
from family_content.service import add_release_component, approve_release, create_release_bundle, enable_release, disable_release
from tasks.models import OutboxEvent
from tests import test_dynamic_numeric_integration as numeric_fixtures
from tests.test_dynamic_weighted_contract import weighted_combination, weighted_trigger


class WeightedDynamicIntegrationTest(TestCase):
    def setUp(self):
        # Reuse setup only, not its TestCase inheritance or test collection.
        numeric_fixtures.DynamicNumericIntegrationTest.setUp(self)
        self.numeric_trigger = self.trigger
        payload = weighted_trigger(self.trigger.payload)
        payload['conditions'] = [deepcopy(self.base.configs['rule'].payload['conditions'][0]),
            deepcopy(self.numeric_trigger.payload['conditions'][0])]
        payload['combination'] = weighted_combination(members=[
            {'signal_ref': payload['conditions'][0]['signal_id'], 'classification': 'core', 'weight': '2'},
            {'signal_ref': payload['conditions'][1]['signal_id'], 'classification': 'general', 'weight': '3'}],
            evidence_refs=self.evidence)
        self.trigger = self.fx.configuration('rule', payload)

    def payload(self, **changes):
        return numeric_fixtures.DynamicNumericIntegrationTest.payload(self, **changes)

    def collect(self, **changes):
        return collect_dynamic_inputs(patient_id=self.patient.id, actor=self.actor, auth_epoch=self.actor.auth_epoch,
            environment=self.fx.gate.environment, payload=self.payload(**changes))

    def create_task(self, **changes):
        return create_dynamic_task(patient_id=self.patient.id, actor=self.actor, auth_epoch=self.actor.auth_epoch,
            environment=self.fx.gate.environment, payload=self.payload(**changes))[0]

    def execute(self, task):
        requested = OutboxEvent.objects.get(task=task, event_type='alert_evaluation.requested')
        execute_dynamic_evaluation(requested, 'synthetic-weighted-worker')
        task.refresh_from_db()
        self.assertEqual(task.status, 'completed')
        evaluation = AlertEvaluation.objects.get(task_id=task.id)
        view = read_dynamic_evaluation(evaluation_id=evaluation.id, actor=self.actor,
            auth_epoch=self.actor.auth_epoch, environment=self.fx.gate.environment)
        return evaluation, view

    def replace_trigger(self, payload):
        self.trigger = self.fx.configuration('rule', payload)

    def matching_publication(self):
        """Build an explicitly qualified synthetic release for this exact rule."""
        content_id = uuid4()
        payload = deepcopy(self.trigger.payload)
        payload['publication']['content_config_id'] = str(content_id)
        self.replace_trigger(payload)
        content_payload = deepcopy(self.configs['content'].payload)
        content_payload.update(rule_id=str(self.trigger.id), window_policy_id=str(self.window.id))
        content = self.fx.configuration('content', content_payload, identity=content_id)
        report = self.trigger.validations.get()
        validation_payload = deepcopy(self.configs['validation'].payload)
        validation_payload.update(rule_id=str(self.trigger.id), window_policy_id=str(self.window.id),
            report_ref=report.evidence_ref, report_sha256=report.evidence_sha256)
        validation = self.fx.configuration('validation', validation_payload)
        contact = ContactVersion.objects.get(id=self.trigger.payload['publication']['contact_version_id'])
        release = create_release_bundle(environment=self.fx.gate.environment, contact_version=contact,
            validation_config=validation)
        self.fx.created.append(release)
        components = {'rule': self.trigger, 'window': self.window, 'mapping': self.mapping,
            'verification': self.configs['verification'], 'content': content,
            'visibility': self.configs['visibility'], 'notification': self.configs['notification']}
        for kind, config in components.items():
            self.fx.created.append(add_release_component(release=release, component_kind=kind, config=config))
        approve_release(release=release)
        self.fx.gate.refresh_from_db()
        if self.fx.gate.active_release_id is not None:
            disable_release(release=self.fx.gate.active_release, environment_gate=self.fx.gate,
                expected_gate_version=self.fx.gate.version)
            self.fx.gate.refresh_from_db()
        enable_release(release=release, environment_gate=self.fx.gate, expected_gate_version=self.fx.gate.version)
        self.fx.gate.refresh_from_db()
        return release

    def test_v2_current_task_freezes_named_preset_exact_weighted_explanation_and_proofs(self):
        task = self.create_task()
        snapshot = task.input_refs['snapshot']
        self.assertEqual(snapshot['sensitivity_preset'], {**self.trigger.payload['sensitivity_preset'],
            'config_id': str(self.trigger.id), 'config_checksum': self.trigger.checksum})
        group = snapshot['combinations']['trigger']['groups'][0]
        self.assertEqual((group['operator'], group['weighted']['denominator_weight']), ('weighted', '5'))
        self.assertEqual(group['weighted']['lower_fraction'], {'numerator': '1', 'denominator': '1'})
        proof = next(row for row in snapshot['configurations'] if row['config']['id'] == str(self.trigger.id))
        self.assertTrue(proof['approval']['id'])
        self.assertTrue(proof['validation']['id'])
        evaluation, view = self.execute(task)
        self.assertEqual((view['status'], view['validity'], view['decision']), ('completed', 'valid', 'met'))
        self.assertEqual(view['sensitivity_preset'], snapshot['sensitivity_preset'])
        self.assertEqual(view['combinations']['trigger'], snapshot['combinations']['trigger'])
        self.assertFalse(AlertEvent.objects.filter(evaluation_id=evaluation.id).exists())
        self.assertIn('DYNAMIC_RELEASE_REFERENCE_MISMATCH', view['lifecycle']['reason_codes'])

    def test_invalidated_result_does_not_expose_previous_preset_or_weighted_explanation(self):
        evaluation, view = self.execute(self.create_task())
        self.assertIsNotNone(view['sensitivity_preset'])
        self.patient.data_epoch += 1
        self.patient.save(update_fields=['data_epoch'])
        invalid = read_dynamic_evaluation(evaluation_id=evaluation.id, actor=self.actor,
            auth_epoch=self.actor.auth_epoch, environment=self.fx.gate.environment)
        self.assertEqual((invalid['validity'], invalid['decision']), ('invalid', 'unknown'))
        self.assertIsNone(invalid['sensitivity_preset'])
        self.assertIsNone(invalid['combinations'])

    def test_matching_explicit_release_can_publish_weighted_event(self):
        release = self.matching_publication()
        evaluation, view = self.execute(self.create_task())
        self.assertEqual(view['lifecycle']['action'], 'create_event')
        event = AlertEvent.objects.get(evaluation_id=evaluation.id)
        self.assertEqual((event.trigger_policy_id, event.release_id), (self.trigger.id, release.id))
        self.assertEqual(view['combinations']['trigger']['groups'][0]['weighted']['members'][0]['classification'], 'core')

    def test_unknown_numeric_general_remains_unknown_and_cannot_publish(self):
        self.matching_publication()
        ArchiveFactVersion.objects.filter(id__in=[fact.id for fact in self.facts]).update(assertion_state='unknown')
        evaluation, view = self.execute(self.create_task())
        detail = view['combinations']['trigger']['groups'][0]['weighted']
        self.assertEqual((view['status'], view['decision']), ('insufficient', 'unknown'))
        self.assertEqual((detail['denominator_weight'], detail['known_weight'], detail['unknown_weight']), ('5', '2', '3'))
        self.assertEqual(detail['members'][1]['state'], 'unknown')
        self.assertFalse(AlertEvent.objects.filter(evaluation_id=evaluation.id).exists())

    def test_unknown_core_blocks_even_sufficient_general_lower_bound(self):
        payload = deepcopy(self.trigger.payload)
        payload['combination']['minimum_known_fraction'] = '0.5'
        self.replace_trigger(payload)
        ArchiveFactVersion.objects.filter(field_id=self.base.field.id).update(assertion_state='unknown')
        snapshot = self.collect().snapshot
        detail = snapshot['combinations']['trigger']['groups'][0]['weighted']
        self.assertEqual((detail['threshold_state'], detail['coverage_state'], detail['core_state']), ('met', 'met', 'unknown'))
        self.assertEqual(snapshot['combinations']['trigger']['state'], 'unknown')

    def test_core_definite_negative_blocks_sufficient_general_weight(self):
        payload = deepcopy(self.trigger.payload)
        payload['conditions'][0]['required_state'] = 'absent'
        self.replace_trigger(payload)
        snapshot = self.collect().snapshot
        detail = snapshot['combinations']['trigger']['groups'][0]['weighted']
        self.assertEqual(detail['threshold_state'], 'met')
        self.assertEqual((detail['core_state'], snapshot['combinations']['trigger']['state']), ('not_met', 'not_met'))

    def test_v1_and_v2_discovery_is_backward_compatible_and_revoked_v2_is_not_offered(self):
        result = dynamic_creation_options(self.patient.id, self.actor, self.actor.auth_epoch,
            self.fx.gate.environment, {'known_at': timezone.now().isoformat(), 'mode': 'current'})
        ids = {row['rule']['config_id'] for row in result['plans']}
        self.assertTrue({str(self.numeric_trigger.id), str(self.trigger.id)} <= ids)
        ConfigApproval.objects.filter(config=self.trigger).update(revoked_at=timezone.now())
        result = dynamic_creation_options(self.patient.id, self.actor, self.actor.auth_epoch,
            self.fx.gate.environment, {'known_at': timezone.now().isoformat(), 'mode': 'current'})
        self.assertNotIn(str(self.trigger.id), {row['rule']['config_id'] for row in result['plans']})

    def test_explicit_preset_change_and_rollback_execute_the_selected_immutable_version(self):
        first = self.trigger
        payload = deepcopy(first.payload)
        payload['sensitivity_preset'].update(preset_key='synthetic-other', display_name='OTHER SOFTWARE PRESET')
        payload['conditions'][0]['required_state'] = 'absent'
        self.replace_trigger(payload)
        second = self.trigger
        for trigger, expected in ((first, 'met'), (second, 'not_met'), (first, 'met')):
            self.trigger = trigger
            snapshot = self.collect().snapshot
            self.assertEqual(snapshot['sensitivity_preset']['config_id'], str(trigger.id))
            self.assertEqual(snapshot['combinations']['trigger']['state'], expected)

    def test_revoked_rule_approval_or_validation_blocks_queued_task(self):
        for model, reason in ((ConfigApproval, 'CONFIG_APPROVAL_UNAVAILABLE'), (ConfigValidation, 'CONFIG_VALIDATION_UNAVAILABLE')):
            with self.subTest(model=model.__name__):
                task = self.create_task()
                rows = model.objects.filter(config=self.trigger)
                rows.update(revoked_at=timezone.now())
                with self.assertRaisesMessage(QualificationRejected, reason):
                    self.execute(task)
                self.assertFalse(AlertEvaluation.objects.filter(task_id=task.id).exists())
                rows.update(revoked_at=None)

    def test_numeric_mapping_revocation_still_blocks_weighted_rule(self):
        task = self.create_task()
        ConfigApproval.objects.filter(config=self.mapping).update(revoked_at=timezone.now())
        with self.assertRaisesMessage(QualificationRejected, 'CONFIG_APPROVAL_UNAVAILABLE'):
            self.execute(task)
        self.assertFalse(AlertEvaluation.objects.filter(task_id=task.id).exists())

    def test_weighted_implementation_change_invalidates_existing_validation(self):
        original = Path.read_bytes
        target = Path(__file__).resolve().parents[1] / 'alerting' / 'computation.py'
        def changed(path):
            value = original(path)
            return value + b'\nSYNTHETIC-WEIGHTED-IMPLEMENTATION' if path == target else value
        with patch.object(Path, 'read_bytes', changed):
            self.assertEqual(configuration_qualification_reason(self.trigger, self.fx.gate.environment),
                'CONFIG_VALIDATION_UNAVAILABLE')
            with self.assertRaisesMessage(QualificationRejected, 'CONFIG_VALIDATION_UNAVAILABLE'):
                self.collect()

    def test_tampered_preset_or_weighted_snapshot_cannot_execute(self):
        for field in ('sensitivity_preset', 'combinations'):
            with self.subTest(field=field):
                task = self.create_task()
                if field == 'sensitivity_preset':
                    task.input_refs['snapshot'][field]['config_id'] = str(uuid4())
                else:
                    task.input_refs['snapshot'][field]['trigger']['groups'][0]['weighted']['denominator_weight'] = '1'
                task.save(update_fields=['input_refs'])
                with self.assertRaisesMessage(QualificationRejected, 'DYNAMIC_TASK_INPUT_CHANGED'):
                    self.execute(task)
                self.assertFalse(AlertEvaluation.objects.filter(task_id=task.id).exists())

    def test_weighted_rules_keep_legacy_lifecycle_and_replay_no_publication(self):
        self.matching_publication()
        evaluation, view = self.execute(self.create_task(mode='replay'))
        self.assertEqual((view['decision'], view['lifecycle']['state'], view['lifecycle']['action']), ('met', 'replay', 'blocked'))
        for role in ('reset', 'retrigger'):
            self.assertEqual(view['combinations'][role]['groups'][0]['operator'], 'all')
        self.assertFalse(AlertEvent.objects.filter(evaluation_id=evaluation.id).exists())

    def test_task_does_not_accept_guardian_numerical_or_unapproved_preset_override(self):
        for addition in ({'weight': '99'}, {'threshold_fraction': '0.01'}, {'sensitivity_preset': 'unapproved-name'}):
            with self.subTest(addition=addition), self.assertRaisesMessage(QualificationRejected, 'DYNAMIC_INPUT_INVALID'):
                self.create_task(**addition)
