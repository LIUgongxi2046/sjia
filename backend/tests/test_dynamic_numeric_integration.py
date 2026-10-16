"""Isolated approved-contract software fixtures for dynamic numeric inputs.

Only synthetic evidence is approved in the test database. No clinical approval,
admission or task execution is mocked. New numeric rules deliberately have no
matching publication release; computation does not authorize publication.
"""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from alerting.inputs import collect_dynamic_inputs
from alerting.models import AlertEvaluation, AlertEvaluationInput, AlertEvent, AlertSignalResult
from alerting.options import dynamic_creation_options
from alerting.service import create_dynamic_task, execute_dynamic_evaluation, read_dynamic_evaluation
from archives.models import ArchiveFactVersion, ArchiveRelationVersion
from assessment.qualified_facts import QualificationRejected
from authorization.models import AccessGrant
from configurations.models import ConfigApproval, ConfigValidation
from configurations.qualification import configuration_qualification_reason
from records.models import NormalizedMeasurement, RecordField
from records.service import create_record
from tasks.models import OutboxEvent
from verification.models import FieldVerificationState, Verification, VerificationField
from tests.synthetic_dynamic import synthetic_dynamic_native
from tests.test_dynamic_numeric_contract import CODE, CONCEPT, UNIT, numeric_condition, numeric_policy


class DynamicNumericIntegrationTest(TestCase):
    def setUp(self):
        self.base = synthetic_dynamic_native(self, execute_current=False)
        self.fx, self.actor, self.patient = self.base.fixture, self.base.actor, self.base.patient
        self.configs = self.base.configs
        self.evidence = [self.fx.reference]
        for action in ('execute_assessment', 'verify_field', 'read_raw'):
            AccessGrant.objects.create(patient=self.patient, principal=self.actor, relation_id=self.base.relation.id,
                action=action, field_codes=[CODE, 'unconfigured_report_field'], record_scope='all',
                purpose='software_test', granted_by=self.actor, evidence_ref=self.fx.reference, valid_from=timezone.now())
        self.mapping = self.fx.configuration('mapping', {'schema_version': 'unit-mapping-2',
            'mappings': [{'mapping_id': str(uuid4()), 'concept_code': CONCEPT,
                'source_revision_id': str(self.fx.source.id), 'raw_unit': UNIT, 'normalized_unit': UNIT,
                'method_scope': 'synthetic', 'multiplier': '1', 'additive': '0', 'exact_values_only': True}],
            'evidence_refs': self.evidence})
        payload = numeric_policy(self.mapping.id)
        payload['variables'] += deepcopy(self.base.input_rule.payload['variables'])
        payload['evidence_refs'] = self.evidence
        self.input_rule = self.fx.configuration('rule', payload)
        window = deepcopy(self.configs['window'].payload)
        window['variables'].append({**window['variables'][0], 'variable_code': CODE,
            'comparison_policy_id': str(self.mapping.id)})
        self.window = self.fx.configuration('window', window)
        self.condition = numeric_condition(evidence_refs=self.evidence)
        trigger = deepcopy(self.configs['rule'].payload)
        trigger.update(window_policy_id=str(self.window.id), conditions=[self.condition],
            combination={'operator': 'all', 'signal_refs': [self.condition['signal_id']]})
        self.trigger = self.fx.configuration('rule', trigger)
        self.fields, self.facts, self.verifications = [], [], []
        measured = (timezone.now() - timedelta(minutes=3)).replace(microsecond=0)
        for index, value in enumerate(('10', '12')):
            point = measured + timedelta(minutes=index)
            field = {'field_code': CODE, 'concept_code': CONCEPT, 'value_type': 'decimal',
                'value_state': 'known', 'raw_numeric': value, 'raw_unit': UNIT, 'method': 'synthetic', 'qualifier': 'eq',
                'source_file_id': str(self.base.file.id), 'source_text': 'SYNTHETIC SOFTWARE TEST ONLY',
                'sample_time_origin': 'observation', 'medical_time': {'precision': 'second',
                    'local_datetime': point.replace(tzinfo=None).isoformat(), 'timezone_name': 'UTC',
                    'resolution_status': 'local_only'}}
            fields = [field]
            if index == 0:
                fields.append({**deepcopy(field), 'field_code': 'unconfigured_report_field',
                    'concept_code': 'synthetic:unconfigured-report-field', 'raw_numeric': '999'})
            created = create_record(patient=self.patient, actor=self.actor, kind='observation', capture_method='manual',
                origin_type='self_report', retention_policy=self.fx.retention,
                use_authorization_id=self.base.authorization.id, source_revision=self.fx.source,
                file_ids=[self.base.file.id], fields=fields)
            saved = RecordField.objects.get(record_version_id=created['record_version_id'], field_code=CODE)
            ArchiveFactVersion.objects.filter(field_id=saved.id).update(context_type='current', assertion_state='present')
            fact = ArchiveFactVersion.objects.get(field_id=saved.id)
            verification = Verification.objects.create(patient=self.patient, record_version_id=saved.record_version_id,
                verifier=self.actor, policy=self.configs['verification'], purpose='software_test',
                evidence_source=self.fx.source, evidence_ref=self.fx.reference, verified_at=timezone.now())
            VerificationField.objects.create(verification_id=verification.id, record_version_id=saved.record_version_id,
                patient_id=self.patient.id, field_id=saved.id, result='consistent', reason='synthetic software fixture')
            FieldVerificationState.objects.create(field_id=saved.id, record_version_id=saved.record_version_id,
                patient_id=self.patient.id, purpose='software_test', version=1,
                current_verification_id=verification.id, status='verified')
            self.fields.append(saved)
            self.facts.append(fact)
            self.verifications.append(verification)
        self.sampling = ArchiveRelationVersion.objects.create(patient_id=self.patient.id, relation_id=uuid4(), version=1,
            left_fact_version_id=self.facts[0].id, right_fact_version_id=self.facts[1].id,
            relation_type='independent_sampling', status='confirmed', evidence_ref=self.fx.reference,
            reason='synthetic independent samples', author_id=self.actor.id, created_at=timezone.now(),
            verification_id=self.verifications[-1].id)

    def payload(self, **changes):
        point = timezone.now().isoformat()
        return {**deepcopy(self.base.dynamic_payload), 'assessment_at': point, 'known_at': point,
            'policy_id': str(self.trigger.id), 'input_rule_id': str(self.input_rule.id),
            'window_policy_id': str(self.window.id), **changes}

    def collect(self, **changes):
        return collect_dynamic_inputs(patient_id=self.patient.id, actor=self.actor, auth_epoch=self.actor.auth_epoch,
            environment=self.fx.gate.environment, payload=self.payload(**changes))

    def create_task(self, **changes):
        task, _ = create_dynamic_task(patient_id=self.patient.id, actor=self.actor, auth_epoch=self.actor.auth_epoch,
            environment=self.fx.gate.environment, payload=self.payload(**changes))
        return task

    def replace_trigger_condition(self, **changes):
        payload = deepcopy(self.trigger.payload)
        payload['conditions'][0].update(changes)
        self.trigger = self.fx.configuration('rule', payload)

    def replace_mapping(self, **changes):
        payload = deepcopy(self.mapping.payload)
        payload['mappings'][0].update(changes)
        payload['mappings'][0]['mapping_id'] = str(uuid4())
        self.mapping = self.fx.configuration('mapping', payload)
        rule = deepcopy(self.input_rule.payload)
        rule['variables'][0]['mapping_config_id'] = str(self.mapping.id)
        self.input_rule = self.fx.configuration('rule', rule)
        window = deepcopy(self.window.payload)
        window['variables'][-1]['comparison_policy_id'] = str(self.mapping.id)
        self.window = self.fx.configuration('window', window)
        trigger = deepcopy(self.trigger.payload)
        trigger['window_policy_id'] = str(self.window.id)
        self.trigger = self.fx.configuration('rule', trigger)

    def test_current_task_persists_generic_numeric_provenance_without_autoenabling_other_fields(self):
        task = self.create_task()
        snapshot = task.input_refs['snapshot']
        self.assertEqual(snapshot['combinations']['trigger']['state'], 'met')
        self.assertEqual({row['variable_code'] for row in snapshot['candidate_refs']}, {CODE, 'synthetic_test'})
        unknown = RecordField.objects.get(field_code='unconfigured_report_field', patient=self.patient)
        self.assertNotIn(str(unknown.id), [row['reference']['field_id'] for row in snapshot['candidate_refs']])
        mapped = NormalizedMeasurement.objects.filter(field_id__in=[field.id for field in self.fields])
        self.assertEqual(mapped.count(), 2)
        self.assertEqual({row.mapping.config_id for row in mapped}, {self.mapping.id})
        requested = OutboxEvent.objects.get(task=task, event_type='alert_evaluation.requested')
        execute_dynamic_evaluation(requested, 'synthetic-numeric-worker')
        task.refresh_from_db()
        self.assertEqual(task.status, 'completed')
        evaluation = AlertEvaluation.objects.get(task_id=task.id)
        view = read_dynamic_evaluation(evaluation_id=evaluation.id, actor=self.actor,
            auth_epoch=self.actor.auth_epoch, environment=self.fx.gate.environment)
        self.assertEqual((view['validity'], view['status'], view['decision']), ('valid', 'completed', 'met'))
        signal = AlertSignalResult.objects.get(evaluation_id=evaluation.id, signal_id='trigger:synthetic_numeric')
        self.assertEqual(signal.pair_results[0]['absolute_delta'], '2')
        saved = AlertEvaluationInput.objects.filter(evaluation_id=evaluation.id, variable_code=CODE)
        self.assertEqual(set(saved.values_list('input_role', flat=True)), {'baseline', 'current'})
        self.assertTrue(all(row.normalization_id is not None for row in saved))
        self.assertFalse(AlertEvent.objects.filter(evaluation_id=evaluation.id).exists())

    def test_operator_creation_options_expose_explicit_plan_after_governance(self):
        result = dynamic_creation_options(self.patient.id, self.actor, self.actor.auth_epoch,
            self.fx.gate.environment, {'known_at': timezone.now().isoformat(), 'mode': 'current'})
        self.assertTrue(any(plan['rule']['config_id'] == str(self.trigger.id)
            and plan['input_rule']['config_id'] == str(self.input_rule.id) for plan in result['plans']))
        ConfigApproval.objects.filter(config=self.mapping).update(revoked_at=timezone.now())
        result = dynamic_creation_options(self.patient.id, self.actor, self.actor.auth_epoch,
            self.fx.gate.environment, {'known_at': timezone.now().isoformat(), 'mode': 'current'})
        self.assertFalse(any(plan['input_rule']['config_id'] == str(self.input_rule.id) for plan in result['plans']))

    def test_unconfigured_condition_cannot_select_available_report_field(self):
        self.replace_trigger_condition(variable_code='unconfigured_report_field')
        with self.assertRaisesMessage(QualificationRejected, 'DYNAMIC_CONDITION_VARIABLE_REQUIRED'):
            self.collect()

    def test_separately_loaded_categorical_context_cannot_reuse_numeric_identity(self):
        original_window = deepcopy(self.window.payload)
        for collision in ('variable_code', 'concept_code'):
            with self.subTest(collision=collision):
                variable = deepcopy(self.base.input_rule.payload['variables'][0])
                variable.update(variable_code='synthetic_other_context', concept_code='synthetic:other-context')
                variable[collision] = self.input_rule.payload['variables'][0][collision]
                context = deepcopy(self.configs['population'].payload)
                context['conditions'] = [variable]
                approved_context = self.fx.configuration('rule', context)
                window = deepcopy(original_window)
                window['scope']['population_config_id'] = str(approved_context.id)
                self.window = self.fx.configuration('window', window)
                trigger = deepcopy(self.trigger.payload)
                trigger.update(window_policy_id=str(self.window.id), scope=deepcopy(window['scope']))
                self.trigger = self.fx.configuration('rule', trigger)
                with self.assertRaisesMessage(QualificationRejected, 'DYNAMIC_CONTEXT_VARIABLE_DUPLICATE'):
                    self.collect()

    def test_bad_threshold_unit_fails_before_samples_and_relative_uses_percent(self):
        # Removing eligibility must not conceal an invalid configuration unit.
        ArchiveFactVersion.objects.filter(id__in=[fact.id for fact in self.facts]).update(assertion_state='unknown')
        for metric in ('absolute_delta', 'relative_delta'):
            self.replace_trigger_condition(metric=metric, unit='unconfigured-unit')
            with self.assertRaisesMessage(QualificationRejected, 'DYNAMIC_THRESHOLD_UNIT_MISMATCH'):
                self.collect()
        self.replace_trigger_condition(metric='relative_delta', unit='%')
        self.assertEqual(self.collect().snapshot['combinations']['trigger']['state'], 'unknown')
        self.replace_trigger_condition(metric='absolute_delta', unit=UNIT)
        self.assertEqual(self.collect().snapshot['combinations']['trigger']['state'], 'unknown')

    def test_reset_and_retrigger_unit_mismatches_fail_before_missing_sample_selection(self):
        ArchiveFactVersion.objects.filter(id__in=[fact.id for fact in self.facts]).update(assertion_state='unknown')
        original_trigger = deepcopy(self.trigger.payload)
        for role in ('reset', 'retrigger'):
            for metric in ('absolute_delta', 'relative_delta'):
                with self.subTest(role=role, metric=metric):
                    lifecycle = deepcopy(self.configs[role].payload)
                    condition = numeric_condition(metric=metric, unit='unconfigured-unit', evidence_refs=self.evidence)
                    lifecycle.update(conditions=[condition], combination={
                        'operator': 'all', 'signal_refs': [condition['signal_id']]})
                    approved_lifecycle = self.fx.configuration('rule', lifecycle)
                    trigger = deepcopy(original_trigger)
                    trigger['episode'][role + '_policy_id'] = str(approved_lifecycle.id)
                    self.trigger = self.fx.configuration('rule', trigger)
                    with self.assertRaisesMessage(QualificationRejected, 'DYNAMIC_THRESHOLD_UNIT_MISMATCH'):
                        self.collect()

    def test_mapping_must_match_exact_concept_source_unit_and_method(self):
        for changes in ({'concept_code': 'synthetic:other-concept'}, {'source_revision_id': str(uuid4())},
                {'raw_unit': 'other-unit'}, {'method_scope': 'other-method'}):
            with self.subTest(changes=changes):
                # Start each configuration from the original required scope.
                self.replace_mapping(**{'concept_code': CONCEPT, 'source_revision_id': str(self.fx.source.id),
                    'raw_unit': UNIT, 'method_scope': 'synthetic', **changes})
                inputs = self.collect()
                self.assertEqual(inputs.snapshot['combinations']['trigger']['state'], 'unknown')
                numeric = [row for row in inputs.snapshot['candidate_refs'] if row['variable_code'] == CODE]
                self.assertTrue(all('FIELD_UNIT_MAPPING_UNAVAILABLE' in row['reason_codes'] for row in numeric))

    def test_mismatched_normalized_unit_is_ineligible_not_silently_converted(self):
        self.replace_mapping(normalized_unit='unapproved-normalized-unit')
        inputs = self.collect()
        self.assertEqual(inputs.snapshot['combinations']['trigger']['state'], 'unknown')
        numeric = [row for row in inputs.snapshot['candidate_refs'] if row['variable_code'] == CODE]
        self.assertTrue(all('FIELD_UNIT_NOT_COMPARABLE' in row['reason_codes'] for row in numeric))

    def test_absent_mapping_configuration_blocks_evaluation(self):
        rule = deepcopy(self.input_rule.payload)
        rule['variables'][0]['mapping_config_id'] = str(uuid4())
        self.input_rule = self.fx.configuration('rule', rule)
        window = deepcopy(self.window.payload)
        window['variables'][-1]['comparison_policy_id'] = rule['variables'][0]['mapping_config_id']
        self.window = self.fx.configuration('window', window)
        trigger = deepcopy(self.trigger.payload)
        trigger['window_policy_id'] = str(self.window.id)
        self.trigger = self.fx.configuration('rule', trigger)
        with self.assertRaisesMessage(QualificationRejected, 'ASSESSMENT_CONFIGURATION_UNAVAILABLE'):
            self.collect()

    def test_revoked_mapping_approval_or_validation_blocks_evaluation(self):
        for model, reason in ((ConfigApproval, 'CONFIG_APPROVAL_UNAVAILABLE'),
                (ConfigValidation, 'CONFIG_VALIDATION_UNAVAILABLE')):
            with self.subTest(model=model.__name__):
                rows = model.objects.filter(config=self.mapping)
                rows.update(revoked_at=timezone.now())
                with self.assertRaisesMessage(QualificationRejected, reason):
                    self.collect()
                rows.update(revoked_at=None)

    def test_stale_implementation_proof_is_rejected_without_carrying_old_validation(self):
        task = self.create_task()
        requested = OutboxEvent.objects.get(task=task, event_type='alert_evaluation.requested')
        config = self.mapping
        original = Path.read_bytes
        target = Path(__file__).resolve().parents[1] / 'alerting' / 'policies.py'
        def changed_bytes(path):
            data = original(path)
            return data + b'\nSYNTHETIC-NEW-IMPLEMENTATION' if path == target else data
        with patch.object(Path, 'read_bytes', changed_bytes):
            self.assertEqual(configuration_qualification_reason(config, self.fx.gate.environment),
                'CONFIG_VALIDATION_UNAVAILABLE')
            with self.assertRaisesMessage(QualificationRejected, 'CONFIG_VALIDATION_UNAVAILABLE'):
                self.collect()
            with self.assertRaisesMessage(QualificationRejected, 'CONFIG_VALIDATION_UNAVAILABLE'):
                execute_dynamic_evaluation(requested, 'synthetic-numeric-worker')
        self.assertFalse(AlertEvaluation.objects.filter(task_id=task.id).exists())

    def test_changed_numeric_task_snapshot_cannot_be_executed(self):
        task = self.create_task()
        task.input_refs['snapshot']['selected_refs']['current:' + CODE]['normalized_unit'] = 'tampered-unit'
        task.save(update_fields=['input_refs'])
        requested = OutboxEvent.objects.get(task=task, event_type='alert_evaluation.requested')
        with self.assertRaisesMessage(QualificationRejected, 'DYNAMIC_TASK_INPUT_CHANGED'):
            execute_dynamic_evaluation(requested, 'synthetic-numeric-worker')
        self.assertFalse(AlertEvaluation.objects.filter(task_id=task.id).exists())

    def test_queued_task_cannot_execute_after_mapping_revocation(self):
        task = self.create_task()
        ConfigApproval.objects.filter(config=self.mapping).update(revoked_at=timezone.now())
        requested = OutboxEvent.objects.get(task=task, event_type='alert_evaluation.requested')
        with self.assertRaisesMessage(QualificationRejected, 'CONFIG_APPROVAL_UNAVAILABLE'):
            execute_dynamic_evaluation(requested, 'synthetic-numeric-worker')
        self.assertFalse(AlertEvaluation.objects.filter(task_id=task.id).exists())
        self.assertFalse(AlertEvent.objects.filter(patient=self.patient).exists())
