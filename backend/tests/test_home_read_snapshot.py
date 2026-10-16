"""Real synthetic home projection, including incomplete manual records.

No clinical reason/time is inferred. Strict fresh clinical fields retain their
contract; historical read snapshots explicitly distinguish an unrecorded reason.
"""
from copy import deepcopy
import json
from pathlib import Path

from django.core.management import call_command
from django.test import TransactionTestCase
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from api.family_home_details import build_home
from authorization.models import AccessGrant
from identity.models import Patient, PrincipalCredential
from records.models import RecordField
from records.service import create_record
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from tests.isolated_rows import IsolatedSchemaRows
from tests.synthetic_fixtures import ensure_synthetic_audit_policy

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / 'backend-design/protocol.schema.json'
SCHEMA = json.loads(SCHEMA_PATH.read_text())
DATA_PATH = ROOT / 'data-design/dataset.schema.json'
DATA = json.loads(DATA_PATH.read_text())
REGISTRY = Registry().with_resources([(SCHEMA_PATH.as_uri(), Resource.from_contents(SCHEMA)),
    (DATA_PATH.as_uri(), Resource.from_contents(DATA))])

def validator(name):
    return Draft202012Validator({'$ref': SCHEMA_PATH.as_uri() + '#/$defs/' + name},
        registry=REGISTRY, format_checker=FormatChecker())


class HomeReadSnapshotTests(TransactionTestCase):
    reset_sequences = False

    def setUp(self):
        rows = IsolatedSchemaRows(require_empty=True)
        self.addCleanup(rows.cleanup)
        call_command('seed_demo', verbosity=0)
        self.actor = PrincipalCredential.objects.get(username='guardian-1').principal
        self.patient = Patient.objects.get(display_id='demo-child')
        self.authorization = DataUseAuthorization.objects.get(patient=self.patient)
        self.retention = RetentionPolicy.objects.get(policy_key='demo-retention')
        ensure_synthetic_audit_policy(self.actor)
        self.codes = ['temp', 'rash', 'report_item', 'medication_name', 'therapy_status', 'therapy_source', 'therapy_start', 'therapy_end']
        AccessGrant.objects.filter(patient=self.patient, principal=self.actor, action='read_raw').update(field_codes=self.codes)
        session = self.client.session
        session['_sjia_principal_id'] = str(self.actor.id)
        session['_sjia_auth_epoch'] = self.actor.auth_epoch
        session['_sjia_audience'] = 'family'
        session.save()

    def record(self, kind, fields):
        return create_record(patient=self.patient, actor=self.actor, kind=kind, capture_method='manual',
            origin_type='self_report', retention_policy=self.retention,
            use_authorization_id=self.authorization.id, fields=fields)

    def read_home(self):
        response = self.client.get(f'/api/patients/{self.patient.id}/home')
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()
        errors = list(validator('HomeView').iter_errors(result))
        self.assertFalse(errors, [(list(item.absolute_path), item.message) for item in errors])
        return result

    def test_three_manual_record_types_preserve_unknowns_and_missing_reason_snapshot(self):
        self.record('observation', [
            {'field_code': 'temp', 'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '36.7', 'raw_unit': 'C'},
            {'field_code': 'rash', 'value_type': 'categorical', 'value_state': 'unknown'},
        ])
        self.record('report', [{'field_code': 'report_item', 'original_name': 'SYNTHETIC REPORT',
            'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '12.3', 'raw_unit': 'SYNTHETIC'}])
        self.record('therapy', [
            {'field_code': 'medication_name', 'value_type': 'text', 'value_state': 'known', 'raw_text': 'SYNTHETIC NOT A MEDICINE'},
            {'field_code': 'therapy_status', 'value_type': 'categorical', 'value_state': 'unknown'},
            {'field_code': 'therapy_source', 'value_type': 'text', 'value_state': 'known', 'raw_text': 'SYNTHETIC SELF REPORT'},
            {'field_code': 'therapy_start', 'value_type': 'time', 'value_state': 'unknown'},
            {'field_code': 'therapy_end', 'value_type': 'time', 'value_state': 'unknown'},
        ])
        before = list(RecordField.objects.filter(patient=self.patient).order_by('id').values('id', 'quality_reasons', 'medical_time_id'))
        home = self.read_home()
        self.assertEqual({row['kind'] for row in home['recent_records']}, {'observation', 'report', 'therapy'})
        self.assertEqual(home['data_state'], 'incomplete')
        for record in home['recent_records']:
            for snapshot in record['fields']:
                field = snapshot['field']
                self.assertIsNone(field['medical_time'])
                self.assertEqual(field['quality_reasons'], [])
                self.assertEqual(snapshot['quality_reasons_snapshot_state'],
                    'not_recorded' if field['value_state'] == 'unknown' else 'recorded')
        self.assertEqual(before, list(RecordField.objects.filter(patient=self.patient).order_by('id').values('id', 'quality_reasons', 'medical_time_id')))
        self.assertEqual(self.read_home(), home)

    def test_recorded_reason_is_preserved_and_partial_time_never_becomes_save_time(self):
        saved = self.record('observation', [
            {'field_code': 'rash', 'value_type': 'categorical', 'value_state': 'unknown'},
            {'field_code': 'temp', 'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '36.7', 'raw_unit': 'C',
             'medical_time': {'precision': 'month', 'local_year': 2026, 'local_month': 9, 'resolution_status': 'local_only'}},
        ])
        RecordField.objects.filter(record_version_id=saved['record_version_id'], field_code='rash').update(
            quality_reasons=['SYNTHETIC_RECORDED_UNKNOWN_REASON'])
        home = self.read_home()
        fields = {row['field']['field_code']: row for row in home['recent_records'][0]['fields']}
        self.assertEqual(fields['rash']['quality_reasons_snapshot_state'], 'recorded')
        self.assertEqual(fields['rash']['field']['quality_reasons'], ['SYNTHETIC_RECORDED_UNKNOWN_REASON'])
        self.assertIsNone(fields['rash']['field']['medical_time'])
        time = fields['temp']['field']['medical_time']
        self.assertEqual((time['precision'], time['local_year'], time['local_month']), ('month', 2026, 9))
        self.assertIsNone(time['instant']); self.assertIsNone(time['local_date'])

    def test_read_snapshot_exception_is_explicit_and_fresh_clinical_contract_stays_strict(self):
        self.record('observation', [{'field_code': 'rash', 'value_type': 'categorical', 'value_state': 'unknown'}])
        field = build_home(self.actor, self.patient, 'test')['recent_records'][0]['fields'][0]
        self.assertTrue(validator('FamilyField').is_valid(field))
        legacy_unknown = deepcopy(field); legacy_unknown.pop('quality_reasons_snapshot_state')
        self.assertFalse(validator('FamilyField').is_valid(legacy_unknown))
        wrong = deepcopy(field); wrong['quality_reasons_snapshot_state'] = 'recorded'
        self.assertFalse(validator('FamilyField').is_valid(wrong))
        wrong = deepcopy(field); wrong['field']['value_state'] = 'known'; wrong['field']['categorical_value'] = 'yes'
        self.assertFalse(validator('FamilyField').is_valid(wrong))
        fresh = Draft202012Validator({'$ref': DATA_PATH.as_uri() + '#/$defs/clinicalField'}, registry=REGISTRY, format_checker=FormatChecker())
        self.assertFalse(fresh.is_valid(field['field']))
        complete = deepcopy(field['field']); complete['quality_reasons'] = ['SYNTHETIC_RECORDED_UNKNOWN_REASON']
        self.assertTrue(fresh.is_valid(complete))
