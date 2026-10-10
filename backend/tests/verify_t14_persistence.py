import json
import os
import sys
from datetime import datetime, timezone
from uuid import uuid4

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.base')

import django
django.setup()

from identity.models import Patient, Principal
from records.models import Record
from records.service import create_record
from records.therapy import read_therapy_record
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from sources.service import register_revision, register_source


def create_simulated_record():
    now = datetime.now(timezone.utc)
    marker = uuid4().hex[:12]
    principal = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
    patient = Patient.objects.create(display_id=f'SIM-T14-{marker}', status='active', created_at=now)
    retention = RetentionPolicy.objects.create(policy_key=f'sim-t14-{marker}', version=1,
        data_class='therapy', rules={'simulation_only': True}, schema_version='sim-window2-v1', status='active')
    source = register_source(source_key=f'sim-t14-{marker}', owner='simulation-only', location='local',
        purpose='simulation-only', sensitivity='synthetic', update_frequency='manual')
    revision = register_revision(source=source, version='sim-window2-v1', checksum=marker.ljust(64, '0'),
        license='simulation-only', allowed_use=['family_observation'])
    authorization = DataUseAuthorization.objects.create(patient=patient, source_revision=revision,
        purpose='family_observation', environment='test', evidence_ref='SIMULATION-ONLY-window2',
        approved_by=principal, approved_at=now)
    result = create_record(patient=patient, actor=principal, kind='therapy', capture_method='manual',
        origin_type='self_report', retention_policy=retention, use_authorization_id=authorization.id,
        fields=[
            {'field_code': 'medication_name', 'value_type': 'text', 'value_state': 'known', 'raw_text': f'模拟药物-{marker}'},
            {'field_code': 'therapy_status', 'value_type': 'categorical', 'value_state': 'unknown', 'categorical_value': None},
            {'field_code': 'therapy_start', 'value_type': 'time', 'value_state': 'unknown', 'medical_time': None},
            {'field_code': 'therapy_end', 'value_type': 'time', 'value_state': 'unknown', 'medical_time': None},
            {'field_code': 'dose', 'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '2.5'},
            {'field_code': 'dose_unit', 'value_type': 'text', 'value_state': 'known', 'raw_text': 'mg'},
            {'field_code': 'concentration_text', 'value_type': 'text', 'value_state': 'known', 'raw_text': 'SIMULATION ONLY 1 mg/mL'},
            {'field_code': 'route_text', 'value_type': 'text', 'value_state': 'known', 'raw_text': 'SIMULATION ONLY oral'},
            {'field_code': 'frequency_text', 'value_type': 'text', 'value_state': 'known', 'raw_text': 'SIMULATION ONLY twice daily'},
            {'field_code': 'interval_value', 'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '12'},
            {'field_code': 'interval_unit', 'value_type': 'categorical', 'value_state': 'known', 'categorical_value': 'hour'},
        ])
    print(json.dumps({'mode': 'SIMULATION_ONLY', 'action': 'saved', **result}, ensure_ascii=False))


def reread_record(record_id):
    record = Record.objects.get(id=record_id, kind='therapy')
    result = read_therapy_record(record.id, record.patient)
    if result['version'] != record.current_version or result['status']['value_state'] != 'unknown':
        raise AssertionError('治疗记录的版本或未知状态回读不一致')
    if result['start_time']['value_state'] != 'unknown' or result['end_time']['value_state'] != 'unknown':
        raise AssertionError('未知起止时间回读不一致')
    if len(result['events']) != 1 or len(result['events'][0]['items']) != 1:
        raise AssertionError('MedicationEvent 或子项回读数量不一致')
    event = result['events'][0]
    item = event['items'][0]
    if event['action'] != 'regimen_record' or event['medical_time'] is not None:
        raise AssertionError('未知使用状态不得生成已使用事件时间')
    expected = {'dose': '2.5', 'dose_unit': 'mg', 'concentration_text': 'SIMULATION ONLY 1 mg/mL',
        'route_text': 'SIMULATION ONLY oral', 'frequency_text': 'SIMULATION ONLY twice daily',
        'interval_value': '12', 'interval_unit': 'hour'}
    if any(item[key] != value for key, value in expected.items()):
        raise AssertionError('MedicationEventItem 子项保存后重读不一致')
    print(json.dumps({'mode': 'SIMULATION_ONLY', 'action': 'reread', 'record_id': str(record.id),
        'record_version_id': result['record_version_id'], 'version': result['version'],
        'status': result['status'], 'start_time': result['start_time'], 'end_time': result['end_time'],
        'event_count': len(result['events']), 'item_count': len(event['items']),
        'medication_name': item['medication_name'], 'action': event['action'], 'medical_time': event['medical_time'],
        'item_details': {key: item[key] for key in expected}}, ensure_ascii=False))


if sys.argv[1] == 'create':
    create_simulated_record()
elif sys.argv[1] == 'read' and len(sys.argv) == 3:
    reread_record(sys.argv[2])
else:
    raise ValueError('用法：verify_t14_persistence.py create 或 read RECORD_ID')
