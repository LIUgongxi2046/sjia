import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.db import connection, transaction
from django.utils import timezone
from assessment.qualified_facts import lock_case_permissions
from configurations.models import ConfigVersion
from core.management.commands.seed_simulation import require_simulation
from identity.models import Patient, Principal
from records.models import RecordField
from sources.models import SourceRevision
from verification.models import FieldVerificationState
from verification.service import verify_fields

require_simulation()
fixture = json.loads((ROOT / '.simulation-work/t29-first-alert-business-fixture.json').read_text())
target = ROOT / '.planning-work/coordination/root/first-alert-current-verification.json'
if target.exists():
    raise RuntimeError('当前核对报告已经存在，禁止重复核对')
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
        if cursor.fetchone() != ('sjia_simulation', 'sjia', 5433):
            raise RuntimeError('模拟数据库身份不匹配')
    patient = Patient.objects.select_for_update().get(id=fixture['patient_id'])
    verifier = Principal.objects.get(id=fixture['support_id'])
    policy = ConfigVersion.objects.get(id=fixture['config_ids']['qualification'], status='enabled')
    source = SourceRevision.objects.get(id=fixture['source_revision_id'], status='approved')
    results = []
    for record in fixture['records']:
        fields = list(RecordField.objects.filter(patient=patient, record_version_id=record['record_version_id']))
        for field in fields:
            lock_case_permissions(verifier, patient, 'verify_field', 'clinical_policy', timezone.now(),
                record_id=record['record_id'], author_id=fixture['owner_id'], field_codes=[field.field_code])
        proof = verify_fields(patient=patient, record_version_id=record['record_version_id'], verifier=verifier,
            policy=policy, purpose='clinical_policy', evidence_ref=fixture['evidence_ref'], evidence_source=source,
            field_results=[(field.id, 'consistent') for field in fields])
        if FieldVerificationState.objects.filter(patient_id=patient.id,
                current_verification_id=proof.id, status='verified').count() != len(fields):
            raise RuntimeError('当前核对结果保存重读不一致')
        results.append({'record_version_id': record['record_version_id'], 'verification_id': str(proof.id),
            'verified_at': proof.verified_at.isoformat(), 'field_count': len(fields)})
report = {'mode': 'SIMULATION_ONLY', 'status': 'PASS', 'patient_id': fixture['patient_id'],
    'verifications': results, 'clinical': 'NOT_RUN', 'saved_and_read': True}
target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
if json.loads(target.read_text()) != report:
    raise RuntimeError('当前核对报告保存重读不一致')
print(json.dumps(report, ensure_ascii=False))
