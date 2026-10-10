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
from configurations.models import ConfigVersion
from core.management.commands.seed_simulation import require_simulation
from identity.models import PrincipalCredential


@transaction.atomic
def complete():
    require_simulation()
    actor = PrincipalCredential.objects.get(username='simulation-family-multi').principal
    fixture = json.loads((ROOT / '.planning-work/coordination/root/t30-multi-patient.json').read_text())
    if str(actor.id) != fixture['principal_id'] or fixture['mode'] != 'SIMULATION_ONLY':
        raise RuntimeError('模拟多患儿字段授权身份不匹配')
    configs = list(ConfigVersion.objects.filter(kind='content', schema_version='field-config-1', status='enabled',
        source_revision__source__source_key__startswith='simulation:'))
    codes = sorted({field['field_code'] for config in configs if config.payload.get('input_context') == 'family_record'
        for field in config.payload['fields']})
    if 'observation_note' not in codes:
        raise RuntimeError('明确模拟家庭字段配置缺少观察说明')
    grants = list(AccessGrant.objects.filter(principal=actor, field_codes=[],
        relation_id__in=[item['relation_id'] for item in fixture['relations']], revoked_at=None).order_by('id'))
    for grant in grants:
        AccessGrant.objects.get_or_create(principal=actor, patient=grant.patient, action=grant.action,
            field_codes=codes, relation_id=grant.relation_id, record_scope=grant.record_scope,
            purpose=grant.purpose, evidence_ref=grant.evidence_ref, revoked_at=None,
            defaults={'granted_by_id': grant.granted_by_id, 'valid_from': timezone.now()})
    count = AccessGrant.objects.filter(principal=actor, field_codes=codes,
        relation_id__in=[item['relation_id'] for item in fixture['relations']], revoked_at=None).count()
    if count != len(grants):
        raise RuntimeError('模拟字段授权保存重读不一致')
    return {'mode': 'SIMULATION_ONLY', 'username': 'simulation-family-multi', 'field_codes': codes,
        'saved_and_read_grants': count, 'original_account_isolation_preserved': True}


result = complete()
target = ROOT / '.planning-work/coordination/root/t30-multi-patient-fields.json'
content = json.dumps(result, ensure_ascii=False, indent=2)
target.write_text(content)
if json.loads(target.read_text()) != result:
    raise RuntimeError('模拟字段授权报告保存重读不一致')
print(content)
