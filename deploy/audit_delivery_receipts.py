import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ledger = json.loads((ROOT / 'planning/delivery-ledger.json').read_text())
evidence = {item['id']: item for item in ledger['evidence']}
rows = []
for acceptance in ledger['acceptances']:
    if acceptance['status'] != 'PASS':
        continue
    changes = []
    for evidence_id in acceptance['evidence_ids']:
        item = evidence[evidence_id]
        receipt_path = (ROOT / 'planning' / item['receipt_path']).resolve()
        if hashlib.sha256(receipt_path.read_bytes()).hexdigest() != item['receipt_sha256']:
            changes.append({'path': str(receipt_path), 'reason': 'RECEIPT_CHANGED'})
        receipt = json.loads(receipt_path.read_text())
        for source in receipt['inputs'] + receipt['artifacts']:
            path = Path(source['path'])
            if not path.is_file():
                changes.append({'path': str(path), 'reason': 'FILE_UNAVAILABLE'})
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != source['sha256']:
                changes.append({'path': str(path), 'reason': 'FILE_CHANGED', 'current_sha256': actual})
    rows.append({'acceptance_id': acceptance['id'], 'current': not changes, 'changes': changes})
print(json.dumps({'scope': 'READ_ONLY_EVIDENCE_AUDIT', 'acceptances': rows}, ensure_ascii=False, indent=2))
