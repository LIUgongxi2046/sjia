import argparse
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
from authorization.models import AccessGrant
from core.management.commands.seed_simulation import require_simulation
from identity.models import Patient, Principal, PrincipalCredential
from records.models import RecordField
from relations.models import Relation

parser = argparse.ArgumentParser()
parser.add_argument('--scenario', required=True, choices=('voice-correction', 'first-alert-fields'))
args = parser.parse_args()
require_simulation()
fixture_name = 't55-voice-fixture.json' if args.scenario == 'voice-correction' else 't29-first-alert-business-fixture.json'
fixture = json.loads((ROOT / '.simulation-work' / fixture_name).read_text())
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
        if cursor.fetchone() != ('sjia_simulation', 'sjia', 5433):
            raise RuntimeError('模拟数据库身份不匹配')
    patient = Patient.objects.select_for_update().get(id=fixture['patient_id'], status='active')
    owner = Principal.objects.get(id=fixture['owner_id'], account_status='active')
    reviewer = PrincipalCredential.objects.get(username='simulation-support').principal
    relation = Relation.objects.get(patient=patient, guardian=owner, status='active')
    created_ids = []
    if args.scenario == 'voice-correction':
        reference = AccessGrant.objects.get(patient=patient, principal=owner, action='write_record',
            purpose='family_observation', field_codes=[], revoked_at=None).evidence_ref
        for codes in ([], ['temp', 'observation_note']):
            grant, _ = AccessGrant.objects.get_or_create(patient=patient, principal=owner,
                action='correct_record', purpose='family_observation', relation_id=relation.id,
                record_scope='own', field_codes=codes, evidence_ref=reference, revoked_at=None,
                defaults={'granted_by': reviewer, 'valid_from': timezone.now()})
            created_ids.append(str(grant.id))
    else:
        codes = sorted(set(RecordField.objects.filter(patient=patient).values_list('field_code', flat=True)))
        originals = list(AccessGrant.objects.filter(patient=patient,
            principal_id__in=[fixture['owner_id'], fixture['support_id']], field_codes=[],
            evidence_ref=fixture['evidence_ref'], revoked_at=None))
        for original in originals:
            grant, _ = AccessGrant.objects.get_or_create(patient=patient, principal=original.principal,
                action=original.action, purpose=original.purpose, relation_id=original.relation_id,
                record_scope=original.record_scope, field_codes=codes, evidence_ref=original.evidence_ref,
                revoked_at=None, defaults={'granted_by': reviewer, 'valid_from': timezone.now()})
            created_ids.append(str(grant.id))
    if AccessGrant.objects.filter(id__in=created_ids, revoked_at=None).count() != len(created_ids):
        raise RuntimeError('模拟授权保存重读不一致')
report = {'mode': 'SIMULATION_ONLY', 'status': 'PASS', 'scenario': args.scenario,
    'patient_id': str(patient.id), 'grant_ids': created_ids, 'saved_and_read': True,
    'clinical': 'NOT_RUN'}
target = ROOT / f'.planning-work/coordination/root/{args.scenario}-grants.json'
target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
if json.loads(target.read_text()) != report:
    raise RuntimeError('模拟授权报告保存重读不一致')
print(json.dumps(report))
