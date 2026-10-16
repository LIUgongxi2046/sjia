"""Synthetic real DB/task/read HLH reference workflow, without admission mocks.

Uses generated test evidence and qualified records in an isolated test database.
It never supplies clinical approval, real patient data, treatment or deployment.
"""
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from archives.inputs import PROTOCOL, REGISTRY
from archives.models import ArchiveFactVersion
from assessment.hlh_inputs import materialize_hlh_inputs
from assessment.models import Assessment, AssessmentInput, AssessmentOutput
from assessment.options import assessment_creation_options
from assessment.qualified_facts import QualificationRejected
from assessment.service import (assessment_payload, create_assessment_task, execute_static_assessment,
    read_assessment, read_assessment_task)
from authorization.models import AccessGrant
from configurations.models import ConfigApproval
from dependencies.models import DependencyNode
from medical_time.models import MedicalTime
from records.models import RecordField, RecordVersion, Report
from records.service import create_record
from tasks.models import OutboxEvent
from verification.models import FieldVerificationState, Verification, VerificationField
from tests.synthetic_dynamic import synthetic_dynamic_native
from tests.test_hlh_2004 import FACT_CODES, synthetic_bindings, synthetic_policy


class HLHWorkflowTest(TestCase):
    def setUp(self):
        self.base = synthetic_dynamic_native(self, execute_current=False)
        self.fx, self.actor, self.patient = self.base.fixture, self.base.actor, self.base.patient
        self.configs = self.base.configs
        for action in ('execute_assessment', 'verify_field', 'read_raw'):
            AccessGrant.objects.create(patient=self.patient, principal=self.actor, relation_id=self.base.relation.id,
                action=action, field_codes=list(FACT_CODES), record_scope='all', purpose='software_test',
                granted_by=self.actor, evidence_ref=self.fx.reference, valid_from=timezone.now())
        # A new source-qualified professional contract is separate from the
        # bootstrap categorical dynamic example; no existing config is mutated.
        verification = deepcopy(self.configs['verification'].payload)
        verification['allowed_origin_types'] = ['authorized_clinical']
        self.verification_policy = self.fx.configuration('verification', verification)
        binding = synthetic_bindings()
        numeric = [item for item in binding['variables'] if item['value_type'] == 'decimal']
        self.mapping = self.fx.configuration('mapping', {'schema_version': 'unit-mapping-2',
            'mappings': [{'mapping_id': str(uuid4()), 'concept_code': item['concept_code'],
                'source_revision_id': str(self.fx.source.id), 'raw_unit': item['normalized_unit'],
                'normalized_unit': item['normalized_unit'], 'method_scope': 'synthetic-method',
                'multiplier': '1', 'additive': '0', 'exact_values_only': False} for item in numeric],
            'evidence_refs': [self.fx.reference]})
        for item in binding['variables']:
            if item['value_type'] == 'decimal': item['mapping_config_id'] = str(self.mapping.id)
        binding['evidence_refs'] = [self.fx.reference]
        self.input_rule = self.fx.configuration('rule', binding)
        rule = synthetic_policy()
        rule.update(source_revision_id=str(self.fx.source.id), evidence_refs=[self.fx.reference],
            semantics_evidence_refs=[self.fx.reference], applicability_policy_ref=self.fx.reference,
            clinical_assertion_policy_ref=self.fx.reference)
        rule['temporal_policy']['maximum_age_seconds'] = {code: 3600 for code in FACT_CODES}
        self.rule = self.fx.configuration('rule', rule)
        window = deepcopy(self.configs['window'].payload)
        window['variables'] = [{**window['variables'][0], 'variable_code': item['variable_code'],
            'comparison_policy_id': str(self.mapping.id if item['value_type'] == 'decimal' else self.verification_policy.id)}
            for item in binding['variables']]
        self.window = self.fx.configuration('window', window)
        measured = (timezone.now() - timedelta(minutes=1)).replace(microsecond=0)
        medical = {'precision': 'second', 'local_datetime': measured.replace(tzinfo=None).isoformat(),
            'timezone_name': 'UTC', 'resolution_status': 'local_only'}
        numeric_values = {'hemoglobin': '80', 'platelet_count': '90', 'neutrophil_count': '0.5',
            'triglycerides': '3', 'fibrinogen': '1.5', 'ferritin': '500', 'soluble_cd25': '2400', 'age_at_hemoglobin': '28'}
        fields = []
        for item in binding['variables']:
            code = item['variable_code']
            field = {'field_code': code, 'concept_code': item['concept_code'], 'value_type': item['value_type'],
                'value_state': 'known', 'source_file_id': str(self.base.file.id), 'source_text': 'SYNTHETIC HLH SOFTWARE TEST',
                'sample_time_origin': 'item', 'medical_time': medical}
            if item['value_type'] == 'decimal':
                field.update(raw_numeric=numeric_values[code], raw_unit=item['normalized_unit'],
                    qualifier='eq', method='synthetic-method')
            else:
                field.update(categorical_value='0' if code == 'molecular' else '1')
            if code == 'nk_activity': field['reference_interval'] = 'SYNTHETIC local laboratory reference'
            fields.append(field)
        created = create_record(patient=self.patient, actor=self.actor, kind='report', capture_method='authorized_import',
            origin_type='authorized_clinical', retention_policy=self.fx.retention,
            use_authorization_id=self.base.authorization.id, source_revision=self.fx.source,
            file_ids=[self.base.file.id], fields=fields, report_metadata={'sample_time': medical, 'report_status': 'final'})
        self.version = RecordVersion.objects.get(id=created['record_version_id'])
        self.report = Report.objects.get(id=self.version.report_id)
        self.fields = {field.field_code: field for field in RecordField.objects.filter(record_version_id=self.version.id)}
        self.verification = Verification.objects.create(patient=self.patient, record_version_id=self.version.id,
            verifier=self.actor, policy=self.verification_policy, purpose='software_test',
            evidence_source=self.fx.source, evidence_ref=self.fx.reference, verified_at=timezone.now())
        for code, field in self.fields.items():
            ArchiveFactVersion.objects.filter(field_id=field.id).update(context_type='current',
                assertion_state='absent' if code == 'molecular' else 'present')
            VerificationField.objects.create(verification_id=self.verification.id, record_version_id=self.version.id,
                patient_id=self.patient.id, field_id=field.id, result='consistent', reason='synthetic software fixture')
            FieldVerificationState.objects.create(field_id=field.id, record_version_id=self.version.id,
                patient_id=self.patient.id, purpose='software_test', version=1,
                current_verification_id=self.verification.id, status='verified')
        self.patient.refresh_from_db()

    def payload(self, **changes):
        point = timezone.now().isoformat()
        return {'kind': 'hlh', 'assessment_at': point, 'known_at': point, 'mode': 'current',
            'rule_id': str(self.rule.id), 'input_rule_id': str(self.input_rule.id),
            'window_policy_id': str(self.window.id), 'verification_policy_id': str(self.verification_policy.id),
            'observation_period_id': None, **changes}

    def create_task(self, **changes):
        task, _ = create_assessment_task(patient_id=self.patient.id, actor=self.actor, auth_epoch=self.actor.auth_epoch,
            environment=self.fx.gate.environment, payload=self.payload(**changes))
        return task

    def execute(self, task=None):
        task = task or self.create_task()
        execute_static_assessment(OutboxEvent.objects.get(task=task, event_type='assessment.requested'), 'synthetic-hlh-worker')
        task.refresh_from_db()
        self.assertEqual(task.status, 'completed')
        result = Assessment.objects.get(task_id=task.id)
        view = read_assessment(assessment_id=result.id, actor=self.actor, auth_epoch=self.actor.auth_epoch,
            environment=self.fx.gate.environment)
        return task, result, view

    def test_real_qualified_task_persistence_and_readback_keep_independent_eight_criteria(self):
        task, result, view = self.execute()
        self.assertEqual((result.kind, view['validity'], view['computation_status']), ('hlh', 'valid', 'completed'),
            {'missing': view['missing_items'], 'exclusions': view['exclusions'], 'version_quality': self.version.quality_reasons})
        output = view['output']['hlh_result']
        self.assertEqual(output['reference_result'], 'criteria_supported')
        self.assertEqual((output['criteria_met_lower_bound'], output['criteria_met_upper_bound']), (8, 8))
        self.assertEqual(output['supported_paths'], ['five_of_eight'])
        self.assertFalse(output['clinical_use_authorized'])
        self.assertEqual(AssessmentInput.objects.filter(assessment_id=result.id).count(), 16)
        stored = AssessmentOutput.objects.get(assessment=result)
        self.assertIsNone(stored.classification_result); self.assertIsNone(stored.ms_total)
        self.assertEqual(stored.hlh_result, output)
        config_ids = {item['config']['id'] for item in task.input_refs['snapshot']['configurations']}
        self.assertIn(str(self.input_rule.id), config_ids)
        self.assertEqual(task.input_refs['snapshot']['hlh_inputs']['age_at_hemoglobin']['qualification'], 'qualified')
        self.assertTrue(DependencyNode.objects.filter(resource_type='report', resource_id=self.report.id).exists())
        schema = Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/AssessmentView'}, registry=REGISTRY, format_checker=FormatChecker())
        self.assertTrue(schema.is_valid(view), list(schema.iter_errors(view)))
        task_view = read_assessment_task(task_id=task.id, actor=self.actor, auth_epoch=self.actor.auth_epoch,
            environment=self.fx.gate.environment)
        self.assertEqual(task_view['result_ref'], str(result.id))

    def test_creation_options_expose_approved_rule_input_mapping_pair_only(self):
        query = {'known_at': timezone.now().isoformat(), 'mode': 'current'}
        options = assessment_creation_options(self.patient.id, self.actor, self.actor.auth_epoch, self.fx.gate.environment, query)
        plans = [item for item in options['plans'] if item['kind'] == 'hlh']
        self.assertTrue(any(item['input_rule']['config_id'] == str(self.input_rule.id) for item in plans))
        ConfigApproval.objects.filter(config=self.mapping).update(revoked_at=timezone.now())
        options = assessment_creation_options(self.patient.id, self.actor, self.actor.auth_epoch, self.fx.gate.environment,
            {'known_at': timezone.now().isoformat(), 'mode': 'current'})
        self.assertFalse(any(item['kind'] == 'hlh' for item in options['plans']))

    def test_caller_cannot_supply_values_or_proofs_or_omit_hlh_input_policy(self):
        for addition in ({'hlh_inputs': {}}, {'molecular': True}, {'clinical_use_authorized': True}):
            with self.assertRaises(QualificationRejected): assessment_payload(self.payload(**addition))
        payload = self.payload(); del payload['input_rule_id']
        with self.assertRaises(QualificationRejected): assessment_payload(payload)
        with self.assertRaises(QualificationRejected): assessment_payload(self.payload(kind='classification'))

    def test_removed_professional_verification_causes_unknown_not_fabricated_negatives(self):
        FieldVerificationState.objects.filter(record_version_id=self.version.id).update(status='unverified', current_verification_id=None)
        _, _, view = self.execute()
        result = view['output']['hlh_result']
        self.assertEqual((result['criteria_met_lower_bound'], result['criteria_met_upper_bound']), (0, 8))
        self.assertEqual(result['reference_result'], 'insufficient')
        self.assertFalse(view['inputs'])

    def test_report_sample_change_removes_fasting_binding_and_invalidates_prior_result(self):
        _, result, _ = self.execute()
        original = MedicalTime.objects.get(id=self.report.sample_time_id)
        different = MedicalTime.objects.create(patient=self.patient, precision='second',
            local_datetime=original.local_datetime-timedelta(seconds=1), timezone_name='UTC', utc_offset_minutes=0,
            instant=original.instant-timedelta(seconds=1), resolution_status='resolved')
        self.report.sample_time_id = different.id; self.report.save(update_fields=['sample_time_id'])
        stale = read_assessment(assessment_id=result.id, actor=self.actor, auth_epoch=self.actor.auth_epoch,
            environment=self.fx.gate.environment)
        self.assertEqual(stale['validity'], 'invalid'); self.assertIsNone(stale['output'])
        task = self.create_task()
        self.assertIsNone(task.input_refs['snapshot']['hlh_inputs']['triglycerides_fasting']['proof']['sample_id'])
        _, _, view = self.execute(task)
        lipid = next(item for item in view['output']['hlh_result']['criteria'] if item['code'] == 'lipids_or_fibrinogen')
        self.assertEqual(lipid['subconditions']['fasting_triglycerides']['state'], 'unknown')
        self.assertEqual(lipid['state'], 'met')  # independently qualified Fbg still satisfies the OR

    def test_missing_nk_reference_or_wrong_method_is_unknown_and_not_auto_inferred(self):
        RecordField.objects.filter(id=self.fields['nk_activity'].id).update(reference_interval=None)
        RecordField.objects.filter(id=self.fields['ferritin'].id).update(method='unapproved-method')
        _, _, view = self.execute()
        result = view['output']['hlh_result']; indexed = {item['code']: item for item in result['criteria']}
        self.assertEqual(indexed['nk_activity']['state'], 'unknown')
        self.assertEqual(indexed['ferritin']['state'], 'unknown')
        self.assertEqual(result['criteria_met_lower_bound'], 6)

    def test_explicit_nonidentity_normalization_preserves_censored_fibrinogen_boundary(self):
        # Exact dimensional conversion in an explicitly approved *synthetic*
        # mapping. This does not approve TG's approximate cross-unit boundary.
        mapping = deepcopy(self.mapping.payload)
        for item in mapping['mappings']:
            item['mapping_id'] = str(uuid4())
            if item['concept_code'].endswith(':fibrinogen'):
                item.update(raw_unit='g/dL', multiplier='10')
        self.mapping = self.fx.configuration('mapping', mapping)
        binding = deepcopy(self.input_rule.payload)
        for item in binding['variables']:
            if item['value_type'] == 'decimal': item['mapping_config_id'] = str(self.mapping.id)
        self.input_rule = self.fx.configuration('rule', binding)
        window = deepcopy(self.window.payload)
        numeric_codes = {item['variable_code'] for item in binding['variables'] if item['value_type'] == 'decimal'}
        for item in window['variables']:
            if item['variable_code'] in numeric_codes: item['comparison_policy_id'] = str(self.mapping.id)
        self.window = self.fx.configuration('window', window)
        RecordField.objects.filter(id=self.fields['fibrinogen'].id).update(raw_numeric='0.15', raw_unit='g/dL', qualifier='le')
        _, saved, view = self.execute()
        lipid = next(item for item in view['output']['hlh_result']['criteria'] if item['code'] == 'lipids_or_fibrinogen')
        self.assertEqual(lipid['subconditions']['fibrinogen']['state'], 'met')
        used = AssessmentInput.objects.get(assessment_id=saved.id, variable_code='fibrinogen')
        self.assertEqual(used.input_snapshot['unit'], 'g/L')
        self.assertEqual(used.input_snapshot['value'], '1.50')
        self.assertEqual(used.input_snapshot['reference']['qualifier'], 'le')
        self.assertIsNotNone(used.normalization_id)

    def test_input_policy_approval_revocation_blocks_new_tasks(self):
        ConfigApproval.objects.filter(config=self.input_rule).update(revoked_at=timezone.now())
        with self.assertRaises(QualificationRejected): self.create_task()

    def test_evidence_for_unknown_semantics_is_resolved_and_not_merely_a_string(self):
        changed = deepcopy(self.rule.payload); changed['semantics_evidence_refs'] = ['unresolvable-claimed-approval']
        self.rule = self.fx.configuration('rule', changed)
        with self.assertRaises(QualificationRejected): self.create_task()

    def test_family_audience_cannot_read_support_reference_details(self):
        from tests.synthetic_fixtures import ensure_synthetic_audit_policy
        ensure_synthetic_audit_policy(self.actor, case=self)
        _, result, _ = self.execute()
        session = self.client.session
        session['_sjia_principal_id'] = str(self.actor.id)
        session['_sjia_auth_epoch'] = self.actor.auth_epoch
        session['_sjia_audience'] = 'family'
        session.save()
        response = self.client.get(f'/api/v2/support/assessments/{result.id}')
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('hlh_result', response.content.decode())

    def test_legacy_null_output_and_no_mixed_hlh_ms_storage(self):
        _, result, _ = self.execute()
        stored = AssessmentOutput.objects.get(assessment=result)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AssessmentOutput.objects.filter(assessment=result).update(ms_total='1', ms_threshold_result=True)
        self.assertIsNone(stored.ms_total)

    def test_input_policy_or_context_change_supersedes_pending_task_without_a_result(self):
        task = self.create_task()
        RecordField.objects.filter(id=self.fields['nk_activity'].id).update(reference_interval='SYNTHETIC changed local reference')
        execute_static_assessment(OutboxEvent.objects.get(task=task, event_type='assessment.requested'), 'synthetic-hlh-worker')
        task.refresh_from_db(); self.assertEqual(task.status, 'superseded')
        self.assertFalse(Assessment.objects.filter(task_id=task.id).exists())
