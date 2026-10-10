import hashlib
import json
from pathlib import Path

from prepare_simulation_voice import guard
from django.db import transaction
from django.utils import timezone

from archives.models import PurposeCopy
from configurations.models import ConfigValidation, ConfigVersion
from core.management.commands.seed_simulation import account, evidence
from family_content.models import EnvironmentGate
from files.models import FileObject
from files.purpose import checked_bytes, lock_prepared_image
from files.purpose_schemas import ConfirmedTextProcessingPlan
from files.purpose_text import process_confirmed_text, text_processing_artifact_checksum
from retention.models import RetentionPolicy

ROOT = Path(__file__).resolve().parents[1]


@transaction.atomic
def refresh():
    guard()
    gate = EnvironmentGate.objects.select_for_update().get(environment='simulation', enabled=True)
    reviewer = account('simulation-support')
    retention = RetentionPolicy.objects.get(policy_key='simulation-configuration_evidence', status='active')
    configs = list(ConfigVersion.objects.filter(status='enabled', schema_version='purpose-processing-text-1',
        source_revision__source__source_key__startswith='simulation:').select_related('source_revision'))
    if not configs:
        raise RuntimeError('没有已批准的模拟文字用途配置')
    results = []
    for config in configs:
        plan = ConfirmedTextProcessingPlan.model_validate(config.payload)
        original = FileObject.objects.select_for_update().get(id=plan.original_file_id, patient_id=plan.patient_id,
            purpose='original', status='available')
        prepared = lock_prepared_image(plan, original)
        output, actual = process_confirmed_text(plan=plan, original_bytes=checked_bytes(original),
            prepared_bytes=checked_bytes(prepared), original_media_type=original.media_type,
            limits=gate.runtime_config.payload['limits'])
        copies = list(PurposeCopy.objects.filter(original_file_id=original.id, patient_id=original.patient_id,
            status='approved', processing_evidence__config_id=str(config.id)))
        if not copies:
            raise RuntimeError('模拟文字用途缺少已批准的保存副本')
        for copy in copies:
            derived = FileObject.objects.get(id=copy.derived_file_id, patient_id=original.patient_id,
                derived_from_id=original.id, status='available')
            if checked_bytes(derived) != output:
                raise RuntimeError('实际重新处理与已保存文字副本不一致')
        proof_source, proof_ref = evidence(key=f'voice-purpose-current-{config.id}-{timezone.now().isoformat()}',
            payload={'mode': 'SIMULATION_TEXT_ONLY', 'actual_processing': actual, 'saved_bytes_equal': True,
                'artifact_sha256': text_processing_artifact_checksum(), 'config_checksum': config.checksum,
                'copy_ids': [str(copy.id) for copy in copies], 'clinical_approval': 'NOT_RUN'},
            purposes=['configuration_validation'], retention=retention)
        validation = ConfigValidation.objects.create(config=config, config_checksum=config.checksum,
            environment=gate, artifact_sha256=text_processing_artifact_checksum(), dataset_revision=config.source_revision,
            evidence_ref=proof_ref, evidence_sha256=proof_source.checksum, outcome='pass',
            executed_by=reviewer, executed_at=timezone.now())
        results.append({'config_id': str(config.id), 'validation_id': str(validation.id),
            'copy_ids': [str(copy.id) for copy in copies], 'actual_processing': actual, 'saved_reread': True})
    return {'status': 'PASS', 'mode': 'SIMULATION_TEXT_ONLY', 'results': results,
        'artifact_sha256': text_processing_artifact_checksum(), 'model_calls': 0,
        'audio_transcription': 'NOT_RUN', 'clinical_approval': 'NOT_RUN', 'visual': 'NOT_RUN'}


value = refresh()
target = ROOT / '.planning-work/coordination/root/text-processing-current.json'
data = json.dumps(value, ensure_ascii=False, indent=2).encode()
target.write_bytes(data)
if target.read_bytes() != data:
    raise RuntimeError('当前文字用途验证报告保存重读不一致')
print(json.dumps({'status': value['status'], 'configs': len(value['results']),
    'artifact_sha256': value['artifact_sha256'], 'report_sha256': hashlib.sha256(data).hexdigest()}, ensure_ascii=False))
