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
from assessment.policies import QualificationPolicy
from assessment.qualified_facts import lock_case_permissions, verification_proof
from configurations.models import ConfigVersion
from core.management.commands.seed_simulation import require_simulation
from files.purpose import checked_bytes
from files.models import FileObject
from identity.models import Patient, Principal
from records.models import RecordField, RecordVersion
from recognition.models import SourceAnchor
from sources.models import SourceRevision
from verification.models import FieldVerificationState, Verification
from verification.service import verify_fields


@transaction.atomic
def verify():
    require_simulation()
    target = ROOT / '.simulation-work/t29-business-fixture.json'
    saved = json.loads(target.read_text())
    if saved['mode'] != 'SIMULATION_ONLY':
        raise RuntimeError('模拟输入身份不匹配')
    patient = Patient.objects.select_for_update().get(id=saved['patient_id'])
    actor = Principal.objects.select_for_update().get(id=saved['support_id'])
    policy = ConfigVersion.objects.get(id=saved['config_ids']['qualification'], status='enabled')
    definition = QualificationPolicy.model_validate_json(json.dumps(policy.payload))
    source = SourceRevision.objects.get(id=saved['source_revision_id'], status='approved')
    proofs = []
    for item, file_id in zip(saved['records'], saved['file_ids'], strict=True):
        original = FileObject.objects.get(id=file_id, patient=patient, purpose='original')
        source_text = checked_bytes(original).decode('utf-8')
        version = RecordVersion.objects.get(id=item['record_version_id'], patient=patient, quality_status='valid')
        fields = list(RecordField.objects.filter(record_version_id=version.id).order_by('ordinal'))
        anchors = [SourceAnchor.objects.get(id=field.anchor_id, patient=patient).original_text for field in fields]
        if not fields or any(not text or text not in source_text for text in anchors):
            raise RuntimeError('模拟核对字段必须逐项追溯实际原文')
        for field in fields:
            lock_case_permissions(actor, patient, 'verify_field', definition.purpose, timezone.now(),
                record_id=version.record_id, author_id=version.author_id, field_codes=[field.field_code])
        proof = verify_fields(patient=patient, record_version_id=version.id, verifier=actor, policy=policy,
            purpose=definition.purpose, evidence_ref=saved['evidence_ref'], evidence_source=source,
            field_results=[(field.id, 'consistent') for field in fields])
        for field in fields:
            actual, reason = verification_proof(field, version, policy, definition, environment='simulation',
                known_at=timezone.now(), mode='current')
            if reason or actual.id != proof.id:
                raise RuntimeError(reason or '模拟核对证明重读不一致')
        proofs.append(str(proof.id))
    previous = ArchiveRelationVersion.objects.select_for_update().get(id=saved['sampling_relation_version_id'])
    relation = ArchiveRelationVersion.objects.create(patient_id=patient.id, relation_id=previous.relation_id,
        version=previous.version + 1, left_fact_version_id=previous.left_fact_version_id,
        right_fact_version_id=previous.right_fact_version_id, relation_type=previous.relation_type,
        status='confirmed', evidence_ref=saved['evidence_ref'], reason='SIMULATION两份原件的独立采样经当前字段权限核对',
        author_id=actor.id, created_at=timezone.now(), verification_id=proofs[1])
    saved.update(verification_ids=proofs, sampling_relation_version_id=str(relation.id), known_at=timezone.now().isoformat())
    transaction.on_commit(lambda: target.write_text(json.dumps(saved, ensure_ascii=False, indent=2)))
    return {'mode': 'SIMULATION_ONLY', 'verification_ids': proofs, 'sampling_relation_version_id': str(relation.id),
        'verified_fields': FieldVerificationState.objects.filter(patient_id=patient.id,
            current_verification_id__in=proofs, status='verified').count(),
        'verified_at': [Verification.objects.get(id=identifier).verified_at.isoformat() for identifier in proofs],
        'clinical_approval': 'NOT_RUN', 'historical_versions_preserved': True}


print(json.dumps(verify(), ensure_ascii=False, indent=2))
