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
from archives.models import ArchiveRelationVersion
from core.management.commands.seed_simulation import require_simulation
from verification.models import Verification

require_simulation()
fixture = json.loads((ROOT / '.simulation-work/t29-first-alert-business-fixture.json').read_text())
report_path = ROOT / '.planning-work/coordination/root/first-alert-current-sampling.json'
if report_path.exists():
    raise RuntimeError('当前采样核对报告已经存在')
proofs = json.loads((ROOT / '.planning-work/coordination/root/first-alert-current-verification.json').read_text())
with transaction.atomic():
    previous = ArchiveRelationVersion.objects.select_for_update().get(
        id=fixture['sampling_relation_version_id'], patient_id=fixture['patient_id'])
    if ArchiveRelationVersion.objects.filter(relation_id=previous.relation_id, version__gt=previous.version).exists():
        raise RuntimeError('当前采样关系已经更新')
    proof = Verification.objects.get(id=proofs['verifications'][1]['verification_id'],
        patient_id=fixture['patient_id'], verifier_id=fixture['support_id'], revoked_at=None)
    current = ArchiveRelationVersion.objects.create(patient_id=previous.patient_id,
        relation_id=previous.relation_id, version=previous.version + 1,
        left_fact_version_id=previous.left_fact_version_id, right_fact_version_id=previous.right_fact_version_id,
        relation_type='independent_sampling', status='confirmed', evidence_ref=previous.evidence_ref,
        reason='SIMULATION依据原件与当前专业核对确认两次独立采样', author_id=proof.verifier_id,
        created_at=timezone.now(), verification_id=proof.id)
    current.refresh_from_db()
    report = {'mode': 'SIMULATION_ONLY', 'status': 'PASS', 'patient_id': fixture['patient_id'],
        'relation_version_id': str(current.id), 'version': current.version,
        'verification_id': str(proof.id), 'author_id': str(current.author_id),
        'clinical': 'NOT_RUN', 'saved_and_read': True}
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
if json.loads(report_path.read_text()) != report:
    raise RuntimeError('采样核对报告保存重读不一致')
print(json.dumps(report))
