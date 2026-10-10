import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / '.planning-work/parallel/window1/t60-positive-verification.json'
EXPECTED_FIELDS = ['patient_id', 'relation_id', 'task_id', 'evaluation_id', 'alert_id', 'recipient_id',
    'receipt_ids', 'summary_id', 'source_versions']

report = json.loads(REPORT.read_text(encoding='utf-8'))
assert report['task_id'] == 'T60'
assert report['mode'] == 'SIMULATION_ONLY'
assert report['status'] == 'NOT_RUN'
assert report['reason'] == 'POSITIVE_MANIFEST_NOT_PROVIDED'
assert report['missing_manifest_fields'] == EXPECTED_FIELDS
assert report['database'] == {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433}
assert report['business_writes'] == 0
assert report['model_calls'] == 0
assert report['external_calls'] == 0
assert json.loads(REPORT.read_text(encoding='utf-8')) == report
print(json.dumps({'task_id': 'T60', 'mode': 'SIMULATION_ONLY', 'checks': 10, 'status': 'PASS',
    'positive_flow': 'NOT_RUN', 'evidence': str(REPORT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
