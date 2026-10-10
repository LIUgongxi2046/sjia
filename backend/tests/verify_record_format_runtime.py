import json
import os
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')
import django

django.setup()

from django.conf import settings
from django.db import transaction

from core.management.commands.seed_simulation import require_simulation
from identity.models import Patient, Principal
from records.models import Record, RecordVersion
from records.correction import correct_record
from records.service import create_record, read_record
from retention.models import RetentionPolicy
from sources.models import SourceRevision
from verification.models import FieldVerificationState, Verification


require_simulation()
fixture = json.loads((settings.SIMULATION_ROOT / 't29-business-fixture.json').read_text())
patient = Patient.objects.get(id=fixture['patient_id'])
actor = Principal.objects.get(id=fixture['owner_id'])
source = SourceRevision.objects.get(id=fixture['source_revision_id'])
policy = RetentionPolicy.objects.get(policy_key='simulation-report', status='active')
field = {'field_code': 'ferritin', 'concept_code': 'ferritin', 'original_name': 'SIMULATION格式校验',
    'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '1000', 'raw_unit': 'ng/mL',
    'qualifier': 'eq', 'source_file_id': fixture['file_ids'][1],
    'source_text': 'SIMULATION t29 fixture: ferritin current 1000 ng/mL', 'sample_time_origin': 'unknown'}
checks = []
with transaction.atomic():
    created = create_record(patient=patient, actor=actor, kind='report', capture_method='manual',
        origin_type='report_transcription', retention_policy=policy,
        use_authorization_id=fixture['authorization_ids']['clinical_policy'], source_revision=source,
        file_ids=[fixture['file_ids'][1]], fields=[field])
    read = read_record(created['record_id'], patient)
    assert read['quality_status'] == 'valid'
    assert not Verification.objects.filter(record_version_id=created['record_version_id']).exists()
    assert not FieldVerificationState.objects.filter(record_version_id=created['record_version_id']).exists()
    checks.append({'id': 'FORMAT_VALID_WITHOUT_PROFESSIONAL_VERIFICATION', 'status': 'PASS',
        'record_id': created['record_id'], 'record_version_id': created['record_version_id']})
    correction = correct_record(record=Record.objects.get(id=created['record_id']), expected_version=1, actor=actor,
        reason='SIMULATION明确将输入更正为未知', fields=[field | {'source_file_id': None,
            'value_state': 'unknown', 'raw_numeric': None, 'qualifier': None}],
        use_authorization_id=fixture['authorization_ids']['clinical_policy'], retention_policy=policy, source_revision=source)
    current = read_record(created['record_id'], patient)
    assert current['quality_status'] == 'needs_correction' and current['version'] == 2
    version = RecordVersion.objects.get(id=correction['record_version_id'])
    assert any('quality_reasons' in reason for reason in version.quality_reasons)
    assert RecordVersion.objects.get(id=created['record_version_id']).quality_status == 'valid'
    assert current['fields'][0]['raw_numeric'] is None and current['fields'][0]['value_state'] == 'unknown'
    checks.append({'id': 'UNKNOWN_REASON_MISSING_AND_PREVIOUS_VERSION_PRESERVED', 'status': 'PASS',
        'record_version_id': str(version.id), 'quality_reasons': version.quality_reasons})
    assert not Verification.objects.filter(record_version_id=version.id).exists()
    checks.append({'id': 'CORRECTION_DOES_NOT_CREATE_PROFESSIONAL_PROOF', 'status': 'PASS'})
    transaction.set_rollback(True)
report = {'mode': 'SIMULATION_ONLY', 'status': 'PASS', 'checks': checks,
    'database': settings.DATABASES['default']['NAME'], 'transaction_changes': 'ROLLED_BACK',
    'original_files_modified': False, 'model_calls': 0}
target = Path.cwd().parent / '.planning-work/coordination/root/record-format.json'
target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
assert json.loads(target.read_text()) == report
print(json.dumps(report, ensure_ascii=False))
