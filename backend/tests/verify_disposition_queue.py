import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from tests.verify_v2_migration import require
from retention.models import DispositionItem, DispositionRequest, RetentionPolicy
from tasks.models import OutboxEvent, Task


def main():
    counts = {'disposition_requests': DispositionRequest.objects.count(), 'disposition_items': DispositionItem.objects.count(),
        'disposition_tasks': Task.objects.filter(task_type='disposition').count(),
        'disposition_events': OutboxEvent.objects.filter(event_type='disposition.local_requested').count(),
        'approved_retention_policies': RetentionPolicy.objects.filter(status='active', approved_at__isnull=False,
            approved_by__isnull=False).exclude(evidence_ref__isnull=True).exclude(evidence_ref='').count(),
        'approved_local_delete_policies': RetentionPolicy.objects.filter(status='active', approved_at__isnull=False,
            approved_by__isnull=False, rules__has_key='local_delete').exclude(evidence_ref__isnull=True).exclude(evidence_ref='').count(),
        'pending_copy_events': OutboxEvent.objects.filter(event_type='purpose_copy.requested', published_at__isnull=True).count()}
    applicable_empty = all(value == 0 for key, value in counts.items() if key != 'approved_retention_policies')
    report = {'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'status': 'PASS' if applicable_empty else 'REVIEW_REQUIRED',
        'scope': 'CURRENT_DATABASE_QUEUE_AND_POLICY_INVENTORY', 'counts': counts,
        'clinical_data_created': False, 'objects_deleted': 0, 'provider_called': False,
        'business_validation': 'NOT_RUN'}
    path = ROOT / '.planning-work/t40-worker-start-scope.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    path.chmod(0o600)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    require(applicable_empty, '现有数据包含待执行处置或适用删除策略，需要按实际范围核验')


if __name__ == '__main__':
    main()
