import hashlib
import json
import os
import sys
from pathlib import Path

import django

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.simulation'
django.setup()

from archives.models import PurposeCopy
from django.conf import settings
from files.models import FileObject
from files.purpose import checked_bytes
from identity.models import Patient
from retention.external_deletion import ExternalDeletionRequest, _paths, query_simulated_external_deletion
from retention.models import DispositionItem, DispositionRequest, SimulatedProviderCopy
from tasks.models import Task, TaskAttempt


if settings.DATABASES['default']['NAME'] != 'sjia_simulation' or settings.SJIA_ENVIRONMENT != 'simulation':
    raise RuntimeError('T40保存重读检查仅允许sjia_simulation')
source_report_path = ROOT / '.planning-work/parallel/window1/t40-positive-provider-disposition.json'
source_report = json.loads(source_report_path.read_text())
patient = Patient.objects.get(id=source_report['patient_id'], display_id='SIMULATION模拟患儿A')
task = Task.objects.get(id=source_report['task_id'], patient=patient, task_type='disposition', status='completed')
attempts = list(TaskAttempt.objects.filter(task=task).order_by('attempt_number'))
provider_copy = SimulatedProviderCopy.objects.get(id=source_report['provider_copy_id'], patient_id=patient.id,
    status='deleted', receipt_sha256=source_report['receipt_sha256'])
request = ExternalDeletionRequest.model_validate_json(json.dumps(task.input_refs['request']))
provider_state = query_simulated_external_deletion(request)
_, object_path, request_path, receipt_path, _ = _paths(request)
disposition = DispositionRequest.objects.get(id=source_report['disposition_id'], status='completed')
item = DispositionItem.objects.get(request=disposition, target_type='provider_copy',
    target_id=str(provider_copy.object_id), target_version=provider_copy.object_version, status='deleted')
purpose_copy = PurposeCopy.objects.get(id=source_report['purpose_copy_id'], status='approved')
original = FileObject.objects.get(id=source_report['file_id'], patient=patient, status='available')
derived = FileObject.objects.get(id=provider_copy.processing_evidence['derived_file_id'],
    patient=patient, status='available')
original_bytes = checked_bytes(original)
derived_bytes = checked_bytes(derived)

checks = {
    'task_and_attempts_saved': task.status == 'completed' and len(attempts) == 1
        and attempts[0].status == TaskAttempt.Status.COMPLETED,
    'provider_object_removed': provider_state['state'] == 'deleted' and not object_path.exists(),
    'request_and_receipt_files_saved': request_path.is_file() and receipt_path.is_file(),
    'receipt_reread_matches_database': provider_state['receipt']['evidence_sha256'] == provider_copy.receipt_sha256
        and provider_state['receipt']['request_id'] == str(provider_copy.deletion_request_id)
        and provider_state['receipt']['checksum'] == provider_copy.checksum
        and provider_state['receipt']['size_bytes'] == provider_copy.size_bytes,
    'disposition_item_saved': disposition.status == 'completed' and item.status == 'deleted'
        and item.receipt_sha256 == provider_copy.receipt_sha256,
    'approved_purpose_copy_retained': purpose_copy.status == 'approved',
    'original_file_bytes_retained': original.status == 'available'
        and len(original_bytes) == original.size_bytes,
    'derived_file_bytes_retained': derived.status == 'available'
        and len(derived_bytes) == derived.size_bytes and hashlib.sha256(derived_bytes).hexdigest() == derived.checksum,
}
if not all(checks.values()):
    raise RuntimeError(f'T40保存重读核查未通过：{checks}')
report = {'mode': 'SIMULATION', 'database': settings.DATABASES['default']['NAME'], 'status': 'PASS',
    'checks': checks, 'task_id': str(task.id), 'attempt_id': str(attempts[0].id),
    'disposition_id': str(disposition.id), 'item_id': str(item.id), 'provider_copy_id': str(provider_copy.id),
    'provider_object_path_exists': object_path.exists(), 'receipt_sha256': provider_copy.receipt_sha256,
    'purpose_copy_id': str(purpose_copy.id), 'original_file_id': str(original.id),
    'original_checksum': original.checksum, 'derived_file_id': str(derived.id),
    'derived_checksum': derived.checksum, 'real_provider': 'NOT_RUN', 'clinical': 'NOT_RUN',
    'visual': 'NOT_RUN'}
output = ROOT / '.planning-work/parallel/window1/t40-positive-provider-disposition-readback.json'
output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
output.chmod(0o600)
if json.loads(output.read_text()) != report:
    raise RuntimeError('T40保存重读核查报告未能重新读取')
print(json.dumps({'status': report['status'], 'checks': len(checks), 'task_id': str(task.id),
    'provider_object_removed': not object_path.exists(), 'original_retained': original.status == 'available',
    'derived_retained': derived.status == 'available'}, ensure_ascii=False))
