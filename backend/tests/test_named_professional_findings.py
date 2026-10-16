"""O10 named findings through native software-only qualification and execution.

These names are explicitly configured synthetic concepts, not built-in diagnostic
codes. Persistent fever is a professional assertion: this fixture defines no
clinical duration and a temperature reading cannot create that assertion.
"""
from copy import deepcopy
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from alerting.inputs import collect_dynamic_inputs
from alerting.models import AlertEvaluation, AlertEvaluationInput, AlertEvent
from alerting.service import create_dynamic_task, execute_dynamic_evaluation, read_dynamic_evaluation
from archives.models import ArchiveFactVersion
from authorization.models import AccessGrant
from medical_time.models import MedicalTime
from records.models import RecordField
from records.service import create_record
from tasks.models import OutboxEvent
from verification.models import FieldVerificationState, Verification, VerificationField
from tests.synthetic_dynamic import synthetic_dynamic_native


NAMES = ('persistent_fever', 'splenomegaly', 'central_nervous_system_involvement', 'bleeding', 'arthritis')


class NamedProfessionalFindingsTests(TestCase):
    def setUp(self):
        self.base = synthetic_dynamic_native(self, execute_current=False)
        self.fx, self.actor, self.patient = self.base.fixture, self.base.actor, self.base.patient
        codes = ['synthetic_' + name for name in NAMES]
        for action in ('execute_assessment', 'verify_field'):
            AccessGrant.objects.create(patient=self.patient, principal=self.actor, relation_id=self.base.relation.id,
                action=action, field_codes=codes + ['synthetic_temperature'], record_scope='all',
                purpose='software_test', granted_by=self.actor, evidence_ref=self.fx.reference, valid_from=timezone.now())
        definition = deepcopy(self.base.input_rule.payload)
        for code in codes:
            definition['variables'].append({**deepcopy(definition['variables'][0]),
                'variable_code': code, 'concept_code': 'software-only:' + code})
        self.input_rule = self.fx.configuration('rule', definition)
        window = deepcopy(self.base.configs['window'].payload)
        window['variables'] += [{**deepcopy(window['variables'][0]), 'variable_code': code} for code in codes]
        self.window = self.fx.configuration('window', window)
        trigger = deepcopy(self.base.configs['rule'].payload)
        trigger['window_policy_id'] = str(self.window.id)
        trigger['conditions'] = [{**deepcopy(trigger['conditions'][0]), 'signal_id': name,
            'field_code': 'synthetic_' + name} for name in NAMES]
        trigger['combination'] = {'operator': 'all', 'signal_refs': list(NAMES)}
        self.trigger = self.fx.configuration('rule', trigger)
        self.measured = (timezone.now() - timedelta(minutes=2)).replace(microsecond=0)
        fields = [{'field_code': code, 'concept_code': 'software-only:' + code,
            'value_type': 'categorical', 'value_state': 'known', 'categorical_value': 'yes',
            'source_file_id': str(self.base.file.id), 'source_text': 'SYNTHETIC PROFESSIONAL ASSERTION: ' + name,
            'sample_time_origin': 'observation', 'medical_time': {'precision': 'second',
                'local_datetime': self.measured.replace(tzinfo=None).isoformat(), 'timezone_name': 'UTC',
                'resolution_status': 'local_only'}} for code, name in zip(codes, NAMES)]
        # A separate numeric observation is deliberately NOT mapped to a named finding.
        fields.append({**deepcopy(fields[0]), 'field_code': 'synthetic_temperature',
            'concept_code': 'software-only:temperature', 'value_type': 'decimal',
            'categorical_value': None, 'raw_numeric': '36.8', 'raw_unit': 'Cel', 'qualifier': 'eq'})
        result = create_record(patient=self.patient, actor=self.actor, kind='observation', capture_method='manual',
            origin_type='self_report', retention_policy=self.fx.retention, use_authorization_id=self.base.authorization.id,
            source_revision=self.fx.source, file_ids=[self.base.file.id], fields=fields)
        self.fields = {row.field_code.removeprefix('synthetic_'): row for row in
            RecordField.objects.filter(record_version_id=result['record_version_id'])}
        ArchiveFactVersion.objects.filter(record_version_id=result['record_version_id']).update(
            context_type='current', assertion_state='present')
        self.verification = Verification.objects.create(patient=self.patient,
            record_version_id=result['record_version_id'], verifier=self.actor, policy=self.base.configs['verification'],
            purpose='software_test', evidence_source=self.fx.source, evidence_ref=self.fx.reference,
            verified_at=timezone.now())
        for field in self.fields.values():
            VerificationField.objects.create(verification_id=self.verification.id, record_version_id=field.record_version_id,
                patient_id=self.patient.id, field_id=field.id, result='consistent', reason='SYNTHETIC SOFTWARE ONLY')
            FieldVerificationState.objects.create(field_id=field.id, record_version_id=field.record_version_id,
                patient_id=self.patient.id, purpose='software_test', version=1,
                current_verification_id=self.verification.id, status='verified')

    def payload(self):
        point = timezone.now().isoformat()
        return {**self.base.dynamic_payload, 'assessment_at': point, 'known_at': point,
            'policy_id': str(self.trigger.id), 'input_rule_id': str(self.input_rule.id),
            'window_policy_id': str(self.window.id)}

    def collect(self):
        return collect_dynamic_inputs(patient_id=self.patient.id, actor=self.actor, auth_epoch=self.actor.auth_epoch,
            environment=self.fx.gate.environment, payload=self.payload())

    def signal(self, inputs, name):
        return next(row for row in inputs.signals if row['signal_id'] == 'trigger:' + name)

    def test_named_verified_assertions_persist_source_time_and_exact_configuration(self):
        task, _ = create_dynamic_task(patient_id=self.patient.id, actor=self.actor, auth_epoch=self.actor.auth_epoch,
            environment=self.fx.gate.environment, payload=self.payload())
        execute_dynamic_evaluation(OutboxEvent.objects.get(task=task, event_type='alert_evaluation.requested'),
            'named-professional-software-test')
        evaluation = AlertEvaluation.objects.get(task_id=task.id)
        view = read_dynamic_evaluation(evaluation_id=evaluation.id, actor=self.actor,
            auth_epoch=self.actor.auth_epoch, environment=self.fx.gate.environment)
        self.assertEqual((view['status'], view['decision']), ('completed', 'met'))
        for name in NAMES:
            field = self.fields[name]
            reference = task.input_refs['snapshot']['selected_refs']['current:synthetic_' + name]
            self.assertEqual(reference['field_id'], str(field.id))
            self.assertEqual(reference['source_revision_id'], str(self.fx.source.id))
            self.assertEqual(reference['verification_id'], str(self.verification.id))
            self.assertEqual(reference['medical_time_id'], str(field.medical_time_id))
            self.assertEqual(reference['medical_interval']['lower'], self.measured.isoformat())
            self.assertTrue(AlertEvaluationInput.objects.filter(evaluation_id=evaluation.id, field_id=field.id).exists())
        configured = {row['config']['id'] for row in task.input_refs['snapshot']['configurations']}
        self.assertTrue({str(self.trigger.id), str(self.input_rule.id), str(self.window.id)} <= configured)
        self.assertFalse(AlertEvent.objects.filter(evaluation_id=evaluation.id).exists())

    def test_named_absent_and_unknown_are_distinct_and_temperature_cannot_fill_unknown_fever(self):
        for name in NAMES:
            field = self.fields[name]
            RecordField.objects.filter(id=field.id).update(categorical_value='no')
            ArchiveFactVersion.objects.filter(field_id=field.id).update(assertion_state='absent')
            self.assertEqual(self.signal(self.collect(), name)['state'], 'not_met')
            ArchiveFactVersion.objects.filter(field_id=field.id).update(assertion_state='unknown')
            value = self.collect()
            self.assertEqual(self.signal(value, name)['state'], 'unknown')
            self.assertIsNone(value.snapshot['selected_refs']['current:synthetic_' + name])
            candidate = next(row for row in value.snapshot['candidate_refs']
                if row['reference']['field_id'] == str(field.id))
            self.assertIn('FACT_ASSERTION_UNKNOWN', candidate['reason_codes'])
            self.assertNotIn('synthetic_temperature', {row['variable_code'] for row in value.snapshot['candidate_refs']})
            RecordField.objects.filter(id=field.id).update(categorical_value='yes')
            ArchiveFactVersion.objects.filter(field_id=field.id).update(assertion_state='present')

    def test_missing_source_missing_time_and_expired_time_never_use_record_save_clock(self):
        field = self.fields['persistent_fever']
        old_anchor, old_time = field.anchor_id, field.medical_time_id
        for field_change, fact_change, reason in (
            ({'anchor_id': None}, {'source_anchor_id': None}, 'FACT_SOURCE_REQUIRED'),
            ({'medical_time_id': None}, {'medical_time_id': None}, 'FACT_MEDICAL_TIME_REQUIRED')):
            RecordField.objects.filter(id=field.id).update(**field_change)
            ArchiveFactVersion.objects.filter(field_id=field.id).update(**fact_change)
            value = self.collect()
            self.assertEqual(self.signal(value, 'persistent_fever')['state'], 'unknown')
            candidate = next(row for row in value.snapshot['candidate_refs'] if row['reference']['field_id'] == str(field.id))
            self.assertIn(reason, candidate['reason_codes'])
            RecordField.objects.filter(id=field.id).update(anchor_id=old_anchor, medical_time_id=old_time)
            ArchiveFactVersion.objects.filter(field_id=field.id).update(source_anchor_id=old_anchor, medical_time_id=old_time)
        old = self.measured - timedelta(days=1)
        MedicalTime.objects.filter(id=old_time).update(instant=old, local_datetime=old.replace(tzinfo=None))
        value = self.collect()
        self.assertEqual(self.signal(value, 'persistent_fever')['state'], 'unknown')
        candidate = next(row for row in value.snapshot['candidate_refs'] if row['reference']['field_id'] == str(field.id))
        self.assertIn('MEDICAL_TIME_OUTSIDE_WINDOW', candidate['reason_codes'])

    def test_revoked_verification_and_assertion_value_conflict_do_not_supply_clinical_findings(self):
        self.verification.revoked_at = timezone.now()
        self.verification.save(update_fields=['revoked_at'])
        value = self.collect()
        self.assertTrue(all(self.signal(value, name)['state'] == 'unknown' for name in NAMES))
        self.verification.revoked_at = None
        self.verification.save(update_fields=['revoked_at'])
        field = self.fields['bleeding']
        RecordField.objects.filter(id=field.id).update(categorical_value='no')
        value = self.collect()
        self.assertEqual(self.signal(value, 'bleeding')['state'], 'unknown')
        candidate = next(row for row in value.snapshot['candidate_refs'] if row['reference']['field_id'] == str(field.id))
        self.assertIn('FACT_ASSERTION_VALUE_INCONSISTENT', candidate['reason_codes'])
