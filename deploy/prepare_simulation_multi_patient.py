import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.db import transaction
from django.utils import timezone

from authorization.models import AccessGrant
from core.management.commands.seed_simulation import account, evidence, require_simulation
from identity.models import Patient, PrincipalCredential
from relations.models import Relation, RelationApplication
from retention.models import RetentionPolicy


@transaction.atomic
def prepare():
    require_simulation()
    actor = PrincipalCredential.objects.get(username='simulation-support').principal
    family = account('simulation-family-multi')
    retention = RetentionPolicy.objects.get(policy_key='simulation-configuration_evidence', status='active')
    patients = [Patient.objects.get(display_id='SIMULATION模拟患儿A'),
        Patient.objects.get(id='405514ce-9335-4705-a750-d811148bda4c')]
    source, reference = evidence(key='t30-multi-patient-simulation', payload={
        'mode': 'SIMULATION_ONLY', 'purpose': '两个已保存模拟患儿的浏览器切换与内容清理检查',
        'authorization': '2026-10-09用户授权全部使用模拟账号与资料完成开发',
        'patient_ids': [str(patient.id) for patient in patients], 'clinical_approval': 'NOT_RUN'},
        purposes=['family_observation', 'verification_evidence'], retention=retention)
    result = []
    for patient in patients:
        relation = Relation.objects.filter(patient=patient, guardian=family, status='active').first()
        if relation is None:
            application = RelationApplication.objects.create(applicant=family, patient=patient, version=1,
                evidence_ref=reference, status='approved', checked_by=actor, checked_at=timezone.now(), created_at=timezone.now())
            relation = Relation.objects.create(patient=patient, guardian=family, application=application,
                status='active', version=1, checked_by=actor, checked_at=timezone.now(), valid_from=timezone.now())
        actions = ['read_raw', 'read_history', 'read_file']
        if patient == patients[0]:
            actions.extend(['correct_record', 'write_record', 'manage_draft', 'view_request', 'read_summary', 'create_summary'])
        for action in actions:
            AccessGrant.objects.get_or_create(principal=family, patient=patient, action=action, relation_id=relation.id,
                field_codes=[], record_scope='all', purpose='family_observation', evidence_ref=reference, revoked_at=None,
                defaults={'granted_by': actor, 'valid_from': timezone.now()})
        reread = Relation.objects.get(id=relation.id, patient=patient, guardian=family, status='active')
        grants = list(AccessGrant.objects.filter(principal=family, patient=patient, relation_id=reread.id,
            purpose='family_observation', evidence_ref=reference, revoked_at=None).values_list('action', flat=True))
        if set(grants) != set(actions):
            raise RuntimeError('模拟多患儿关系及准确权限保存重读不一致')
        result.append({'patient_id': str(patient.id), 'relation_id': str(relation.id), 'actions': sorted(grants)})
    return {'mode': 'SIMULATION_ONLY', 'username': 'simulation-family-multi', 'principal_id': str(family.id),
        'source_revision_id': str(source.id), 'relations': result, 'clinical_approval': 'NOT_RUN'}


result = prepare()
target = ROOT / '.planning-work/coordination/root/t30-multi-patient.json'
content = json.dumps(result, ensure_ascii=False, indent=2)
target.write_text(content)
if json.loads(target.read_text()) != result:
    raise RuntimeError('模拟多患儿初始化报告保存重读不一致')
print(content)
