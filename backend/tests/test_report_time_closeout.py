"""Opt-in v3 report times: synthetic contracts only, no database or provider calls."""
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from pydantic import ValidationError

from recognition.invocation import contract_snapshot, checked_candidates, InvocationRejected
from recognition.report_times import ReportTimeCandidates, report_candidate_model
from recognition.schemas import ArkCompletion, ReportCandidates


def time_candidate(**changes):
    value = {'raw_text':'2026-02','precision':'month','local_year':2026,'local_month':2,
        'end_year':None,'end_month':None,'relative_text':None,'local_date':None,'local_datetime':None,
        'timezone_name':None,'utc_offset_minutes':None,'instant':None,'resolution_status':'local_only'}
    return {**value,**changes}


def candidates():
    return {'schema_version':'report-recognition-3','source_text':'SYNTHETIC report date 2026-02',
        'sample_time':time_candidate(),'result_time':None,'items':[{'name':'synthetic item','value':'1.2500',
            'comparator':'=','unit':'synthetic-unit','reference':None,'method':None,
            'source_text':'SYNTHETIC item date 2026-02','sample_time':time_candidate()}]}


class ReportTimeContractTests(SimpleTestCase):
    def setUp(self):
        self.provider = SimpleNamespace(payload={'report_schema_version':'report-recognition-3','model_version':'synthetic-model'})

    def test_v2_contract_baseline_is_unchanged_and_v3_is_explicitly_distinct(self):
        baseline = json.loads((Path(__file__).parent/'fixtures/model_contracts.synthetic.json').read_text())
        self.assertEqual(contract_snapshot('report'),baseline['contracts']['report'])
        legacy = SimpleNamespace(payload={})
        self.assertIs(report_candidate_model(legacy),ReportCandidates)
        self.assertEqual(contract_snapshot('report',legacy),contract_snapshot('report'))
        selected = contract_snapshot('report',self.provider)
        for key in ('prompt_sha256','schema_sha256'):
            self.assertNotEqual(selected[key],contract_snapshot('report')[key])

    def test_exact_month_unknown_and_offsets_are_retained_without_instant(self):
        value = ReportTimeCandidates.model_validate(candidates()).model_dump()
        self.assertEqual(value['sample_time'],time_candidate())
        self.assertEqual(value['items'][0]['value'],'1.2500')
        unknown = candidates(); unknown['result_time']=time_candidate(raw_text='unknown',precision='unknown',
            local_year=None,local_month=None,resolution_status='unknown'); unknown['source_text']+=' unknown'
        self.assertIsNone(ReportTimeCandidates.model_validate(unknown).result_time.instant)
        minute = candidates(); minute['sample_time']=time_candidate(raw_text='2026-02-03 12:34 +08:00',precision='minute',
            local_year=None,local_month=None,local_datetime='2026-02-03T12:34',utc_offset_minutes=480)
        minute['source_text']=minute['sample_time']['raw_text']
        parsed=ReportTimeCandidates.model_validate(minute)
        self.assertEqual(parsed.sample_time.utc_offset_minutes,480)
        self.assertIsNone(parsed.sample_time.timezone_name)
        self.assertIsNone(parsed.sample_time.instant)

    def test_invalid_or_fabricated_precision_content_fails_closed(self):
        invalid = [dict(local_date='2026-02-01'),dict(timezone_name='Asia/Shanghai'),dict(instant='2026-02-01T00:00:00Z'),
            dict(resolution_status='resolved'),dict(local_month=13),dict(extra='unregistered')]
        for changes in invalid:
            value=candidates();value['sample_time'].update(changes)
            with self.subTest(changes=changes),self.assertRaises(ValidationError):
                ReportTimeCandidates.model_validate(value)

    def test_item_and_report_time_need_their_own_source_quote(self):
        for location in ('sample_time','item'):
            value=candidates()
            selected=value['items'][0]['sample_time'] if location=='item' else value['sample_time']
            selected['raw_text']='not quoted in source'
            with self.assertRaises(ValidationError):
                ReportTimeCandidates.model_validate(value)

    def test_actual_candidate_parser_selects_opted_in_schema_and_retains_raw_fields(self):
        value=candidates()
        body=ArkCompletion.model_validate({'id':'synthetic-receipt','model':'synthetic-model',
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]})
        parsed=checked_candidates({'completion':body,'model':body.model},self.provider,report_candidate_model(self.provider))
        self.assertEqual(parsed,value)
        with self.assertRaises(ValidationError):
            checked_candidates({'completion':body,'model':body.model},self.provider,ReportCandidates)

    def test_frozen_contract_cannot_switch_report_versions_before_adoption(self):
        from recognition.invocation_runtime import validate_invocation
        from django.conf import settings
        refs={'environment':settings.SJIA_ENVIRONMENT,'invocation_contract':contract_snapshot('report'),
            'field_kind':'report','provider_config_id':'synthetic','field_config_id':'synthetic'}
        task=SimpleNamespace(input_refs=refs)
        with patch('recognition.invocation_runtime._runtime',return_value=(self.provider,None,None,None,None)):
            with self.assertRaisesRegex(InvocationRejected,'MODEL_INVOCATION_CONTRACT_CHANGED'):
                validate_invocation.__wrapped__(task,'report')

    def test_provider_schema_registers_optional_version_without_activation(self):
        from archives.provider_policy import ArchiveProviderPolicy
        field=ArchiveProviderPolicy.model_fields['report_schema_version']
        self.assertEqual(field.default,'report-recognition-2')
        with self.assertRaisesRegex(InvocationRejected,'MODEL_CONTRACT_UNREGISTERED'):
            report_candidate_model(SimpleNamespace(payload={'report_schema_version':'report-recognition-99'}))
