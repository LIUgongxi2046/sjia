"""Synthetic process admission tests. No provider, credentials or clinical approvals."""
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from config import runtime_admission as admission


def provider_entry(config_id='11111111-1111-1111-1111-111111111111'):
    return {'kind':'ocr','config_id':config_id,'enabled':True,'endpoint':'https://synthetic.invalid/v1',
        'secret_ref':'env:SJIA_ARK_API_KEY','region':'synthetic','processing_basis_ref':'SYNTHETIC_ONLY',
        'connect_timeout_ms':1000,'read_timeout_ms':2000,'total_timeout_ms':3000,'submit_retries':0,
        'query_attempts':0,'query_delays_seconds':[],'circuit_window_seconds':60,
        'circuit_failure_threshold':3,'circuit_open_seconds':60,'daily_budget':'10','currency':'CNY',
        'billing_scope_key':'synthetic-offline','reserved_cost':'0.01'}


def provider_payload(environment='synthetic', field_id='22222222-2222-2222-2222-222222222222'):
    return {'schema_version':'provider-1','environment':environment,'provider':'ark',
        'endpoint':'https://synthetic.invalid/v1','model_version':'synthetic-model',
        'field_config_id':field_id,'pricing_evidence_ref':'SYNTHETIC_ONLY',
        'input_per_million':'1','output_per_million':'2','currency':'CNY','evidence_refs':['SYNTHETIC_ONLY']}


class RuntimeAdmissionPureTests(SimpleTestCase):
    def tearDown(self):
        admission._ADMITTED = None

    def test_management_import_path_never_admits_database_state(self):
        root = Path(__file__).resolve().parents[1]
        self.assertNotIn('admit_process', (root/'manage.py').read_text())
        self.assertNotIn('admit_process', (root/'observability/apps.py').read_text())
        for name in ('wsgi','asgi'):
            self.assertIn('admit_configured_process("api")', (root/f'config/{name}.py').read_text())
        with override_settings(REQUIRE_PROCESS_RUNTIME_ADMISSION=False), \
                patch.object(admission, '_restore_admission') as restore_gate, \
                patch.object(admission,'admit_process_runtime') as call:
            self.assertIsNone(admission.admit_configured_process('api'))
        call.assert_not_called()
        restore_gate.assert_called_once()

    def test_production_entrypoint_propagates_fail_closed_sanitized_error(self):
        with override_settings(REQUIRE_PROCESS_RUNTIME_ADMISSION=True), \
                patch.object(admission, '_restore_admission'), \
                patch.object(admission,'admit_process_runtime',side_effect=ValueError('SECRET')):
            with self.assertRaisesRegex(admission.RuntimeAdmissionRejected,'RUNTIME_PROCESS_ADMISSION_FAILED') as failure:
                admission.admit_configured_process('api')
        self.assertNotIn('SECRET',str(failure.exception))

    def test_restore_gate_cannot_be_disabled_by_general_runtime_flag(self):
        with override_settings(REQUIRE_PROCESS_RUNTIME_ADMISSION=False), \
                patch.object(admission, '_restore_admission',
                    side_effect=admission.RuntimeAdmissionRejected('RESTORE_RUNTIME_NOT_ADMITTED')):
            for kind in ('api', 'worker', 'beat'):
                with self.assertRaisesRegex(admission.RuntimeAdmissionRejected, 'RESTORE_RUNTIME_NOT_ADMITTED'):
                    admission.admit_configured_process(kind)

    def test_worker_and_beat_rejection_exits_instead_of_being_swallowed_by_signal(self):
        for handler in (admission.celery_process_start,admission.celery_beat_start):
            with patch('django.setup'),patch.object(admission,'admit_configured_process',side_effect=RuntimeError('SECRET')):
                with self.assertRaisesRegex(SystemExit,'RUNTIME_PROCESS_ADMISSION_FAILED'):
                    handler()

    def test_snapshot_is_defensive_and_not_inherited_as_child_admission(self):
        admission._ADMITTED={'pid':os.getpid(),'identity':{'runtime_config_id':'synthetic'},'capabilities':{}}
        value=admission.process_snapshot();value['identity']['runtime_config_id']='changed'
        self.assertEqual(admission.process_snapshot()['identity']['runtime_config_id'],'synthetic')
        with patch.object(admission.os,'getpid',return_value=os.getpid()+1):
            self.assertIsNone(admission.process_snapshot())

    def test_desired_database_state_never_becomes_loaded_without_admission(self):
        current={'desired':{'runtime_config_id':'synthetic'},'capabilities':{},'runtime_payload':{}}
        with patch.object(admission,'inspect_runtime',return_value=current):
            result=admission.admission_status()
        self.assertIsNone(result['loaded_config_version'])
        self.assertEqual(result['desired_config_version'],current['desired'])
        self.assertEqual(result['reason_code'],'PROCESS_RUNTIME_NOT_ADMITTED')

    def test_drift_and_revocation_preserve_original_loaded_snapshot(self):
        old={'runtime_config_id':'old'}
        admission._ADMITTED={'pid':os.getpid(),'identity':old,'capabilities':{}}
        with patch.object(admission,'inspect_runtime',return_value={'desired':{'runtime_config_id':'new'},'capabilities':{}}):
            self.assertEqual(admission.admission_status()['reason_code'],'PROCESS_RUNTIME_CONFIGURATION_DRIFT')
        with patch.object(admission,'inspect_runtime',side_effect=admission.RuntimeAdmissionRejected('CONFIG_APPROVAL_UNAVAILABLE')):
            result=admission.admission_status()
        self.assertEqual(result['loaded_config_version']['identity'],old)
        self.assertEqual(result['reason_code'],'CONFIG_APPROVAL_UNAVAILABLE')

    def test_missing_local_dependency_refuses_before_runtime_inspection(self):
        with patch('observability.health.check_database',return_value=False),patch.object(admission,'inspect_runtime') as inspect:
            with self.assertRaisesRegex(admission.RuntimeAdmissionRejected,'RUNTIME_DEPENDENCY_UNAVAILABLE'):
                admission.admit_process_runtime('api')
        inspect.assert_not_called()

    def test_provider_endpoint_and_secret_are_exact_and_never_sent(self):
        provider=SimpleNamespace(kind='provider',schema_version='provider-1',payload={'endpoint':'https://synthetic.invalid/v1'})
        entry={'kind':'ocr','endpoint':'http://synthetic.invalid/v1','secret_ref':'env:SJIA_ARK_API_KEY'}
        with patch('configurations.qualification.configuration_qualification_reason',return_value=None), \
                override_settings(ARK_BASE_URL='https://synthetic.invalid/v1',ARK_API_KEY=''), \
                patch('urllib.request.urlopen',side_effect=AssertionError('PROVIDER_FORBIDDEN')):
            self.assertEqual(admission._provider_reason(entry,provider,'synthetic',{'ocr'}),'RUNTIME_PROVIDER_ENDPOINT_MISMATCH')
            entry['endpoint']='https://synthetic.invalid/v1'
            self.assertEqual(admission._provider_reason(entry,provider,'synthetic',{'ocr'}),'RUNTIME_PROVIDER_SECRET_UNAVAILABLE')

    def test_schema_valid_enabled_ocr_uses_runtime_reservation_and_family_scope(self):
        from configurations.schemas import RUNTIME, validate_payload
        from jsonschema import Draft202012Validator, FormatChecker
        entry=provider_entry()
        Draft202012Validator({**RUNTIME['properties']['providers']['items'],'$defs':RUNTIME['$defs']},
            format_checker=FormatChecker()).validate(entry)
        payload=provider_payload();validate_payload('provider','provider-1',payload)
        self.assertNotIn('reserved_cost',payload)
        provider=SimpleNamespace(kind='provider',schema_version='provider-1',payload=payload)
        field=SimpleNamespace()
        with patch('configurations.qualification.configuration_qualification_reason',return_value=None) as qualify, \
                patch('configurations.models.ConfigVersion.objects.filter') as query, \
                patch.dict(os.environ,{'SJIA_ARK_API_KEY':'synthetic-unused'}), \
                override_settings(ARK_BASE_URL=entry['endpoint'],ARK_API_KEY='synthetic-unused'), \
                patch('urllib.request.urlopen',side_effect=AssertionError('PROVIDER_FORBIDDEN')) as transport:
            query.return_value.first.return_value=field
            self.assertIsNone(admission._provider_reason(entry,provider,'synthetic',{'ocr'}))
            self.assertEqual(qualify.call_args_list[0].kwargs,{'audience':'family'})
            for key in ('reserved_cost','billing_scope_key'):
                missing={k:v for k,v in entry.items() if k!=key}
                with self.subTest(missing=key):
                    self.assertEqual(admission._provider_reason(missing,provider,'synthetic',{'ocr'}),
                        'RUNTIME_PROVIDER_BUDGET_INVALID')
            for reservation in ('0','NaN','Infinity',None):
                with self.subTest(reservation=reservation):
                    self.assertEqual(admission._provider_reason({**entry,'reserved_cost':reservation},provider,'synthetic',{'ocr'}),
                        'RUNTIME_PROVIDER_BUDGET_INVALID')
            qualify.side_effect=lambda row,environment,**kwargs: 'CONFIG_APPROVAL_UNAVAILABLE' if row is provider and kwargs.get('audience')=='family' else None
            self.assertEqual(admission._provider_reason(entry,provider,'synthetic',{'ocr'}),'CONFIG_APPROVAL_UNAVAILABLE')
            transport.assert_not_called()

    def test_disabled_same_kind_never_hides_enabled_block_in_either_order(self):
        for rows in ((True,False),(False,True)):
            capabilities={}
            for enabled in rows:
                admission._merge_provider_capability(capabilities,'ocr',enabled=enabled,
                    reason='CONFIG_APPROVAL_UNAVAILABLE' if enabled else None)
            self.assertEqual(capabilities['ocr']['state'],'blocked')
            self.assertTrue(capabilities['ocr']['required_for_startup'])

    def test_configured_same_kind_never_hides_enabled_block_in_either_order(self):
        for reasons in ((None,'CONFIG_APPROVAL_UNAVAILABLE'),('CONFIG_APPROVAL_UNAVAILABLE',None)):
            capabilities={}
            for reason in reasons:
                admission._merge_provider_capability(capabilities,'ocr',enabled=True,reason=reason)
            self.assertEqual(capabilities['ocr']['reason_code'],'CONFIG_APPROVAL_UNAVAILABLE')
            self.assertEqual(capabilities['ocr']['state'],'blocked')


class RuntimeAdmissionDatabaseTests(TestCase):
    def setUp(self):
        from uuid import uuid4
        from identity.models import Principal
        from retention.models import RetentionPolicy
        from tests.synthetic_fixtures import SyntheticEvidence
        setting=self.settings(SJIA_ENVIRONMENT=f'synthetic-process-{uuid4()}',REQUIRE_PROCESS_RUNTIME_ADMISSION=False)
        setting.enable();self.addCleanup(setting.disable)
        now=timezone.now()
        self.actor=Principal.objects.create(account_status='active',created_at=now,updated_at=now)
        self.fixture=SyntheticEvidence(self,self.actor)
        self.audit=self.fixture.create(RetentionPolicy,policy_key=f'synthetic-process-audit-{uuid4()}',version=1,
            data_class='audit',rules={'retention_days':None,'disposition':'retain'},schema_version='retention-1',
            status='active',approved_by=self.actor,approved_at=now,evidence_ref=self.fixture.reference)
        self.request=self.fixture.create(RetentionPolicy,policy_key=f'synthetic-process-request-{uuid4()}',version=1,
            data_class='request',rules={'retention_days':None,'disposition':'retain'},schema_version='retention-1',
            status='active',approved_by=self.actor,approved_at=now,evidence_ref=self.fixture.reference)
        self.runtime=self.fixture.runtime()
        self.runtime.payload['tasks']=[row for row in self.runtime.payload['tasks'] if row['task_type']=='reconcile']
        self.runtime.payload['retention_policy_ids']=[str(self.audit.id),str(self.request.id)]
        self.runtime.save(update_fields=['payload']);self.fixture.qualify(self.runtime)
        self.addCleanup(lambda:setattr(admission,'_ADMITTED',None))

    def admit(self):
        with patch('observability.health.check_broker',return_value=True):
            return admission.admit_process_runtime('api')

    def enable_ocr(self):
        field=self.fixture.configuration('content',{'schema_version':'field-config-1','kind':'report',
            'watch_field_codes':[],'fields':[{'field_code':'synthetic_note','label':'Synthetic note',
            'value_type':'text','required':False,'writable':True,'allowed_values':[],'allowed_units':[],
            'dictionary_category':None,'minimum':None,'maximum':None,'max_length':100,
            'max_occurrences':1,'time_required':False}]})
        provider=self.fixture.configuration('provider',provider_payload(self.fixture.gate.environment,str(field.id)))
        entry=provider_entry(str(provider.id))
        self.runtime.payload['providers']=[entry]
        self.runtime.payload['tasks'].append({**self.runtime.payload['tasks'][0],'task_type':'ocr'})
        self.runtime.save(update_fields=['payload']);self.fixture.qualify(self.runtime)
        (self.fixture.root/'objects').mkdir()
        environment=patch.dict(os.environ,{'SJIA_ARK_API_KEY':'synthetic-unused'})
        environment.start();self.addCleanup(environment.stop)
        configured=self.settings(ARK_BASE_URL=entry['endpoint'],ARK_API_KEY='synthetic-unused')
        configured.enable();self.addCleanup(configured.disable)
        network=patch('urllib.request.urlopen',side_effect=AssertionError('PROVIDER_FORBIDDEN'))
        network.start();self.addCleanup(network.stop)
        return provider

    def test_qualified_enabled_ocr_admits_without_provider_calls(self):
        self.enable_ocr()
        current=admission.inspect_runtime()
        self.assertEqual(current['capabilities']['ocr']['state'],'configured')
        loaded=self.admit()
        self.assertEqual(loaded['capabilities']['ocr']['state'],'configured')
        self.assertEqual(admission.admission_status()['state'],'admitted_current')

    def test_enabled_ocr_requires_runtime_and_provider_family_approval(self):
        from configurations.models import ConfigApproval
        provider=self.enable_ocr()
        for config in (provider,self.runtime):
            with self.subTest(config_kind=config.schema_version):
                approvals=ConfigApproval.objects.filter(config=config)
                for approval in approvals:
                    approval.effective_scope['audience']=['support']
                    approval.save(update_fields=['effective_scope'])
                state=admission.admission_status()
                self.assertEqual(state['reason_code'],'CONFIG_APPROVAL_UNAVAILABLE')
                self.assertNotEqual(state['state'],'admitted_current')
                with self.assertRaises(admission.RuntimeAdmissionRejected):
                    self.admit()
                self.fixture.qualify(config)

    def test_qualified_configuration_is_loaded_only_by_explicit_admission(self):
        from configurations.service import loaded_config_version
        self.assertIsNone(loaded_config_version(self.fixture.gate))
        current=admission.inspect_runtime()
        self.assertEqual(current['capabilities']['reconcile']['state'],'budget_configured')
        self.assertEqual(current['capabilities']['ocr']['state'],'disabled')
        loaded=self.admit()
        self.assertEqual(loaded['identity']['runtime_config_id'],str(self.runtime.id))
        self.assertEqual(admission.admission_status()['state'],'admitted_current')
        self.assertNotIn('runtime_payload',loaded)
        self.assertNotIn('operations_contact_ref',json.dumps(loaded))

    def test_gate_drift_requires_restart_without_rewriting_snapshot(self):
        loaded=self.admit()
        self.fixture.gate.version+=1;self.fixture.gate.save(update_fields=['version'])
        state=admission.admission_status()
        self.assertEqual(state['reason_code'],'PROCESS_RUNTIME_CONFIGURATION_DRIFT')
        self.assertEqual(state['loaded_config_version'],loaded)
        with self.assertRaisesRegex(admission.RuntimeAdmissionRejected,'PROCESS_RUNTIME_RESTART_REQUIRED'):
            self.admit()

    def test_revoked_configuration_blocks_current_readiness_and_retains_loaded_identity(self):
        from configurations.models import ConfigApproval
        loaded=self.admit()
        ConfigApproval.objects.filter(config=self.runtime).update(revoked_at=timezone.now())
        state=admission.admission_status()
        self.assertEqual(state['reason_code'],'CONFIG_APPROVAL_UNAVAILABLE')
        self.assertEqual(state['loaded_config_version'],loaded)

    def test_present_runtime_fk_without_qualification_is_not_ready(self):
        self.runtime.status='draft';self.runtime.save(update_fields=['status'])
        self.assertEqual(admission.admission_status()['reason_code'],'CONFIG_NOT_ENABLED')
        with self.assertRaisesRegex(admission.RuntimeAdmissionRejected,'CONFIG_NOT_ENABLED'):
            self.admit()

    def test_missing_retention_and_enabled_capability_policy_refuse_admission(self):
        self.audit.status='disabled';self.audit.save(update_fields=['status'])
        with self.assertRaisesRegex(admission.RuntimeAdmissionRejected,'RUNTIME_RETENTION_UNAVAILABLE'):
            self.admit()
        self.audit.status='active';self.audit.save(update_fields=['status'])
        self.runtime.payload['tasks'].append({**self.runtime.payload['tasks'][0],'task_type':'summary'})
        self.runtime.save(update_fields=['payload']);self.fixture.qualify(self.runtime)
        state=admission.admission_status()
        self.assertEqual(state['capabilities']['summary']['state'],'not_configured')
        self.assertFalse(state['capabilities']['summary']['required_for_startup'])
        self.assertIsNotNone(self.admit(), 'A task budget is not an enabled clinical capability')
