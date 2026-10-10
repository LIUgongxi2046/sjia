import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend'
sys.path.insert(0, str(BACKEND))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings
from django.db import transaction
from django.utils import timezone as django_timezone
from archives.models import PurposeCopy
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from family_content.models import EnvironmentGate
from files.models import FileGroup, FileObject
from files.purpose import lock_processing_config, lock_use_authorization
from files.purpose_jobs import submit_copy
from files.purpose_schemas import PurposeCopyInput, PurposeProcessingPlan
from files.service import object_store
from files.storage import digest_bytes
from identity.models import PrincipalCredential
from sources.models import DataUseAuthorization
from tasks.models import ConsumerReceipt, OutboxEvent, Task, TaskAttempt
from tasks.worker import consume


def require_isolated_simulation():
    root = Path(settings.SIMULATION_ROOT).resolve()
    parent = (ROOT / '.planning-work' / 'parallel' / 'window4').resolve()
    if (settings.DATABASES['default']['NAME'] != 'sjia_simulation'
            or settings.SJIA_ENVIRONMENT != 'simulation'
            or root.parent != parent
            or not root.name.startswith('isolated')):
        raise RuntimeError('用途副本探针只允许窗口四独立模拟副本。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', required=True)
    parser.add_argument('--recovery-report', required=True)
    args = parser.parse_args()
    require_isolated_simulation()
    gate = EnvironmentGate.objects.get(environment='simulation', enabled=True)
    runtime_config = ConfigVersion.objects.get(id=gate.runtime_config_id)
    qualification = configuration_qualification_reason(runtime_config, 'simulation')
    if qualification is not None:
        raise RuntimeError(f'隔离模拟环境运行资格未通过：{qualification}')

    config = None
    plan = None
    original = None
    group = None
    authorization = None
    now = django_timezone.now()
    configs = ConfigVersion.objects.filter(kind='provider', schema_version='purpose-processing-2',
        status='enabled', payload__environment='simulation').order_by('id')
    for candidate in configs:
        candidate_plan = PurposeProcessingPlan.model_validate(candidate.payload)
        candidate_original = FileObject.objects.filter(id=candidate_plan.original_file_id,
            patient_id=candidate_plan.patient_id, purpose='original', status='available').first()
        if candidate_original is None:
            continue
        candidate_group = FileGroup.objects.get(id=candidate_original.file_group_id,
            patient_id=candidate_original.patient_id)
        candidate_authorization = DataUseAuthorization.objects.filter(patient=candidate_original.patient,
            source_revision_id=candidate_group.source_revision_id, purpose=candidate_plan.purpose,
            environment='simulation', revoked_at__isnull=True, approved_at__lte=now).exclude(
                valid_until__lte=now).order_by('-approved_at', 'id').first()
        if candidate_authorization is not None:
            config = candidate
            plan = candidate_plan
            original = candidate_original
            group = candidate_group
            authorization = candidate_authorization
            break
    if authorization is None:
        raise RuntimeError('已核准的模拟用途授权记录不存在。')
    with transaction.atomic():
        lock_use_authorization(authorization.id, original, plan.purpose, 'simulation')
        validated_config, _, _, _ = lock_processing_config(config.id, original, plan.purpose,
            plan.target, 'simulation')
    if validated_config.checksum != config.checksum:
        raise RuntimeError('用途处理配置摘要在验证时发生变化。')

    actor = PrincipalCredential.objects.get(username='simulation-support').principal
    initial_copy_count = PurposeCopy.objects.filter(original_file_id=original.id, patient_id=original.patient_id).count()
    task = submit_copy(file_id=original.id, actor=actor, auth_epoch=actor.auth_epoch,
        payload=PurposeCopyInput.model_validate({'purpose': plan.purpose, 'target': plan.target,
            'authorization_id': str(authorization.id), 'processing_config_id': str(config.id)}))
    event = OutboxEvent.objects.get(task=task, event_type='purpose_copy.requested')
    first_worker_id = str(uuid4())
    first_result = consume.apply(args=[str(event.id)], task_id=first_worker_id).get(propagate=True)
    task.refresh_from_db()
    attempt = TaskAttempt.objects.get(task=task)
    copy = PurposeCopy.objects.get(patient_id=task.patient_id, processing_evidence__task_id=str(task.id))
    derived = FileObject.objects.get(id=copy.derived_file_id, patient_id=task.patient_id)
    derived_bytes = object_store.get(derived.object_key)
    if (task.status != Task.Status.COMPLETED or attempt.status != TaskAttempt.Status.COMPLETED
            or derived.status != FileObject.Status.AVAILABLE
            or len(derived_bytes) != derived.size_bytes or digest_bytes(derived_bytes) != derived.checksum
            or attempt.usage_evidence.get('provider_called') is not False):
        raise RuntimeError('用途副本 Worker 未保存完整的本地处理结果。')

    second_worker_id = str(uuid4())
    second_result = consume.apply(args=[str(event.id)], task_id=second_worker_id).get(propagate=True)
    task.refresh_from_db()
    task_copy_count = PurposeCopy.objects.filter(patient_id=task.patient_id,
        processing_evidence__task_id=str(task.id)).count()
    total_copy_count = PurposeCopy.objects.filter(original_file_id=original.id, patient_id=original.patient_id).count()
    receipt_count = ConsumerReceipt.objects.filter(consumer='purpose_copy', event=event).count()
    attempt_count = TaskAttempt.objects.filter(task=task).count()
    if (task.status != Task.Status.COMPLETED or task_copy_count != 1
            or total_copy_count != initial_copy_count + 1 or receipt_count != 1 or attempt_count != 1):
        raise RuntimeError('重复用途副本事件产生了重复保存或缺少消费回执。')

    report = {
        'task': 'T57',
        'classification': 'SIMULATION_ACTIVE_PURPOSE_COPY_REPLAY',
        'measured_at': datetime.now(timezone.utc).isoformat(),
        'database': {'name': 'sjia_simulation', 'environment': 'simulation',
            'isolated_root': str(Path(settings.SIMULATION_ROOT).resolve())},
        'runtime_qualification': 'QUALIFIED',
        'input': {'purpose': plan.purpose, 'configuration_id': str(config.id),
            'authorization_id': str(authorization.id), 'original_file_id': str(original.id),
            'original_checksum': original.checksum, 'original_bytes': original.size_bytes},
        'event': {'event_id': str(event.id), 'event_type': event.event_type,
            'delivery_count': 2, 'worker_ids': [first_worker_id, second_worker_id],
            'worker_results': [first_result, second_result]},
        'task': {'task_id': str(task.id), 'task_type': task.task_type, 'final_status': task.status,
            'attempt_id': str(attempt.id), 'attempt_status': attempt.status, 'attempt_count': attempt_count,
            'provider_request_id': attempt.provider_request_id,
            'provider_called': attempt.usage_evidence.get('provider_called')},
        'saved_copy': {'copy_id': str(copy.id), 'copy_count_for_event': task_copy_count,
            'total_copy_count_before': initial_copy_count, 'total_copy_count_after': total_copy_count,
            'derived_file_id': str(derived.id), 'derived_file_status': derived.status,
            'derived_bytes': len(derived_bytes), 'derived_sha256': digest_bytes(derived_bytes),
            'readback_matches_saved_metadata': len(derived_bytes) == derived.size_bytes
                and digest_bytes(derived_bytes) == derived.checksum},
        'consumer_receipts': receipt_count,
        'external_provider_calls': 0,
        'external_sends': 0,
        'saved_and_reread': True,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if json.loads(report_path.read_text()) != report:
        raise RuntimeError('用途副本报告保存后回读内容不一致。')

    recovery_path = Path(args.recovery_report)
    recovery_report = json.loads(recovery_path.read_text())
    recovery_report['duplicate_consumption']['active_purpose_copy'] = {
        'event_id': str(event.id), 'task_id': str(task.id), 'attempt_id': str(attempt.id),
        'delivery_count': 2, 'consumer_receipts': receipt_count, 'saved_copies_for_event': task_copy_count,
        'task_status': task.status, 'attempt_status': attempt.status,
        'provider_request_id': attempt.provider_request_id, 'provider_called': attempt.usage_evidence.get('provider_called'),
        'derived_file_checksum_readback': report['saved_copy']['readback_matches_saved_metadata'],
        'external_provider_calls': 0, 'external_sends': 0,
    }
    recovery_report['duplicate_consumption']['scope'] = 'Terminal cancellation receipt deduplication and active simulated purpose-copy replay were both executed.'
    recovery_path.write_text(json.dumps(recovery_report, ensure_ascii=False, indent=2) + '\n')
    if json.loads(recovery_path.read_text()) != recovery_report:
        raise RuntimeError('T57 综合恢复报告更新后回读内容不一致。')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
