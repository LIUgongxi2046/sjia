"""T14 治疗资料与照片登记逻辑验证。

运行：backend/.venv/bin/python backend/manage.py test tests.test_T14 --keepdb
"""
from datetime import datetime, timezone

from django.test import TestCase

from case_details.models import MedicationEvent, MedicationEventItem
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from records.models import Record
from records.correction import correct_record
from records.service import create_record, read_record
from records.therapy import TherapyRejected, create_therapy_record, read_therapy_record
from retention.models import RetentionPolicy
from relations.service import create_application, decide_application, revoke_relation
from sources.models import DataUseAuthorization
from sources.service import register_revision, register_source


def _now():
    return datetime.now(timezone.utc)


class TherapyServiceTest(TestCase):
    def setUp(self):
        now = _now()
        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-T14", status="active", created_at=now)
        EnvironmentGate.objects.create(environment='test', version=1, enabled=True, updated_at=now)
        self.retention = RetentionPolicy.objects.create(policy_key="therapy", version=1, data_class="therapy", rules={}, schema_version="r-1", status="active")
        source = register_source(source_key="tx-src", owner="o", location="l", purpose="p", sensitivity="s", update_frequency="d")
        revision = register_revision(source=source, version="v1", checksum="c" * 64, license="l", allowed_use=["family_observation"])
        self.use_auth = DataUseAuthorization.objects.create(patient=self.patient, source_revision=revision, purpose="family_observation", environment="test", evidence_ref="e", approved_by=self.principal, approved_at=now)

    def _create(self, **kwargs):
        defaults = dict(
            patient=self.patient, actor=self.principal, retention_policy=self.retention, use_authorization_id=self.use_auth.id,
            medication_name="托珠单抗", action="ongoing",
        )
        defaults.update(kwargs)
        return create_therapy_record(**defaults)

    def test_create_therapy_record(self):
        result = self._create(dose="8", dose_unit="mg/kg", interval_value="2", interval_unit="week")
        record = Record.objects.get(id=result["record_id"])
        self.assertEqual(record.kind, "therapy")
        event = MedicationEvent.objects.get(id=result["medication_event_id"])
        self.assertEqual(event.action, "ongoing")
        item = MedicationEventItem.objects.get(id=result["medication_event_item_id"])
        self.assertEqual(item.medication_name, "托珠单抗")
        self.assertEqual(str(item.dose), "8")
        self.assertEqual(item.interval_unit, "week")

    def test_read_therapy_preserves_original_name(self):
        result = self._create(dose="8", dose_unit="mg/kg")
        read = read_therapy_record(result["record_id"], self.patient)
        self.assertEqual(read["kind"], "therapy")
        self.assertEqual(read["items"][0]["medication_name"], "托珠单抗")

    def test_medication_name_required(self):
        with self.assertRaises(TherapyRejected):
            self._create(medication_name="")

    def test_ongoing_has_null_end_time(self):
        result = self._create(action="ongoing")
        event = MedicationEvent.objects.get(id=result["medication_event_id"])
        # 结束时间不在家庭输入中出现，服务端不推断持续，也不补齐结束时间
        self.assertIsNone(event.medical_time_id)

    def test_dose_is_not_current_dose(self):
        # 既往剂量独立保存为分项，不成为当前剂量；多次登记产生独立事件
        first = self._create(action="historical_treatment", dose="6", dose_unit="mg/kg")
        second = self._create(action="ongoing", dose="8", dose_unit="mg/kg")
        first_item = MedicationEventItem.objects.get(id=first["medication_event_item_id"])
        second_item = MedicationEventItem.objects.get(id=second["medication_event_item_id"])
        self.assertEqual(str(first_item.dose), "6")
        self.assertEqual(str(second_item.dose), "8")

    def _create_family_record(self, status='ongoing', start=True, end=False, item_fields=None):
        from records.models import RecordField

        fields = [
            {'field_code': 'medication_name', 'value_type': 'text', 'value_state': 'known', 'raw_text': '模拟药物甲'},
            {'field_code': 'therapy_status', 'value_type': 'categorical', 'value_state': 'known' if status else 'unknown', 'categorical_value': status},
            {'field_code': 'therapy_start', 'value_type': 'time', 'value_state': 'known' if start else 'unknown',
             'medical_time': {'precision': 'date', 'local_date': '2026-09-01', 'timezone_name': 'Asia/Shanghai', 'resolution_status': 'local_only'} if start else None},
            {'field_code': 'therapy_end', 'value_type': 'time', 'value_state': 'known' if end else 'unknown',
             'medical_time': {'precision': 'date', 'local_date': '2026-09-08', 'timezone_name': 'Asia/Shanghai', 'resolution_status': 'local_only'} if end else None},
        ]
        fields.extend(item_fields or [])
        result = create_record(patient=self.patient, actor=self.principal, kind='therapy', capture_method='manual',
            origin_type='self_report', retention_policy=self.retention, use_authorization_id=self.use_auth.id, fields=fields)
        return result, fields

    def test_family_create_record_persists_unknowns_and_event_subitem(self):
        from case_details.models import MedicationEvent

        result, _ = self._create_family_record(status=None, start=False, end=False)
        read = read_therapy_record(result['record_id'], self.patient)
        event = MedicationEvent.objects.get(record_version_id=result['record_version_id'])
        self.assertEqual(event.action, 'regimen_record')
        self.assertIsNone(event.medical_time_id)
        self.assertEqual(read['status'], {'value_state': 'unknown', 'value': None})
        self.assertEqual(read['start_time'], {'value_state': 'unknown', 'value': None})
        self.assertEqual(read['end_time'], {'value_state': 'unknown', 'value': None})
        self.assertEqual(read['events'][0]['items'][0]['medication_name'], '模拟药物甲')
        self.assertEqual(read['events'][0]['previous_event_id'], None)

    def test_family_event_item_persists_confirmed_subitem_details(self):
        details = [
            {'field_code': 'dose', 'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '240'},
            {'field_code': 'dose_unit', 'value_type': 'text', 'value_state': 'known', 'raw_text': 'mg'},
            {'field_code': 'concentration_text', 'value_type': 'text', 'value_state': 'known', 'raw_text': '20 mg/mL'},
            {'field_code': 'route_text', 'value_type': 'text', 'value_state': 'known', 'raw_text': '静脉输注'},
            {'field_code': 'frequency_text', 'value_type': 'text', 'value_state': 'known', 'raw_text': '每两周一次'},
            {'field_code': 'interval_value', 'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '2'},
            {'field_code': 'interval_unit', 'value_type': 'categorical', 'value_state': 'known', 'categorical_value': 'week'},
        ]
        result, _ = self._create_family_record(item_fields=details)
        read = read_therapy_record(result['record_id'], self.patient)
        item = read['events'][0]['items'][0]
        self.assertEqual(item['dose'], '240')
        self.assertEqual(item['dose_unit'], 'mg')
        self.assertEqual(item['concentration_text'], '20 mg/mL')
        self.assertEqual(item['route_text'], '静脉输注')
        self.assertEqual(item['frequency_text'], '每两周一次')
        self.assertEqual(item['interval_value'], '2')
        self.assertEqual(item['interval_unit'], 'week')
        self.assertEqual(item['source_field_id'], read['events'][0]['source_field_id'])

    def test_family_event_rejects_partial_dose_pair(self):
        with self.assertRaises(TherapyRejected) as caught:
            self._create_family_record(item_fields=[
                {'field_code': 'dose', 'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '240'},
                {'field_code': 'dose_unit', 'value_type': 'text', 'value_state': 'unknown'},
            ])
        self.assertEqual(caught.exception.reason_code, 'THERAPY_DOSE_AND_UNIT_REQUIRED')

    def test_ended_event_uses_end_time_without_replacing_start_time(self):
        from case_details.models import MedicationEvent
        from records.models import RecordField

        result, _ = self._create_family_record(status='ended', start=True, end=True)
        read = read_therapy_record(result['record_id'], self.patient)
        event = MedicationEvent.objects.get(record_version_id=result['record_version_id'])
        self.assertEqual(event.action, 'stopped_by_family')
        end_field = RecordField.objects.get(record_version_id=result['record_version_id'], field_code='therapy_end')
        start_field = RecordField.objects.get(record_version_id=result['record_version_id'], field_code='therapy_start')
        self.assertEqual(event.medical_time_id, end_field.medical_time_id)
        self.assertNotEqual(event.medical_time_id, start_field.medical_time_id)
        self.assertEqual(read['start_time']['value']['local_date'], '2026-09-01')
        self.assertEqual(read['end_time']['value']['local_date'], '2026-09-08')

    def test_family_correction_keeps_event_history_and_new_version(self):
        from case_details.models import MedicationEvent
        from records.models import RecordField

        item_fields = [
            {'field_code': 'dose', 'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '6'},
            {'field_code': 'dose_unit', 'value_type': 'text', 'value_state': 'known', 'raw_text': 'mg'},
            {'field_code': 'frequency_text', 'value_type': 'text', 'value_state': 'known', 'raw_text': '每天一次'},
        ]
        result, fields = self._create_family_record(item_fields=item_fields)
        record = Record.objects.get(id=result['record_id'])
        current = list(RecordField.objects.filter(record_version_id=result['record_version_id']).order_by('ordinal'))
        corrected = [dict(field) for field in fields]
        corrected[0] = {**corrected[0], 'raw_text': '模拟药物乙'}
        corrected[4] = {**corrected[4], 'raw_numeric': '12'}
        corrected[6] = {**corrected[6], 'raw_text': '每周一次'}
        next_version = correct_record(record=record, expected_version=1, actor=self.principal, reason='模拟更正', fields=corrected,
            use_authorization_id=self.use_auth.id, retention_policy=self.retention)
        current_read = read_therapy_record(result['record_id'], self.patient)
        self.assertEqual(next_version['version'], 2)
        self.assertEqual(current_read['version'], 2)
        self.assertEqual(current_read['items'][0]['medication_name'], '模拟药物乙')
        self.assertEqual(MedicationEvent.objects.filter(record_version_id=result['record_version_id']).count(), 1)
        current_event = MedicationEvent.objects.get(record_version_id=next_version['record_version_id'])
        self.assertEqual(current_event.action, 'ongoing')
        self.assertIsNotNone(current_event.previous_event_id)
        self.assertEqual(current[0].raw_text, '模拟药物甲')
        current_item = MedicationEventItem.objects.get(event_id=current_event.id)
        self.assertEqual(str(current_item.dose), '12')
        self.assertEqual(current_item.frequency_text, '每周一次')

    def test_revoke_blocks_family_read_without_deleting_source_record(self):
        from records.models import Record

        application = create_application(applicant=self.principal, evidence_ref='SIMULATION-ONLY T14 access', patient=self.patient)
        relation = decide_application(application=application, decision='approved', checked_by=self.principal,
            expected_version=1, action='read_raw', field_codes=['medication_name', 'therapy_status', 'therapy_start', 'therapy_end'],
            purpose='family_observation')
        session = self.client.session
        session['_sjia_principal_id'] = str(self.principal.id)
        session['_sjia_auth_epoch'] = self.principal.auth_epoch
        session['_sjia_audience'] = 'family'
        session.save()
        result, _ = self._create_family_record()
        path = f"/api/records/{result['record_id']}"
        self.assertEqual(self.client.get(path).status_code, 200)
        revoke_relation(relation=relation, reason='SIMULATION-ONLY revoke check', revoked_by=self.principal)
        self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(Record.objects.get(id=result['record_id']).status, 'active')
