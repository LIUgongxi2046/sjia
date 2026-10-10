import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent.parent
ledger = json.loads((root / 'planning/delivery-ledger.json').read_text())
results = []
for evidence in ledger['evidence']:
    receipt_path = root / 'planning' / evidence['receipt_path']
    receipt = json.loads(receipt_path.read_text())
    changed = []
    for item in receipt['inputs'] + receipt['artifacts']:
        path = Path(item['path'])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            changed.append(str(path.relative_to(root)))
    results.append({'evidence_id': evidence['id'], 'changed_files': changed, 'matches_current_files': not changed})
(root / '.planning-work').mkdir(exist_ok=True)
(root / '.planning-work/v2-evidence-review.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
print(json.dumps(results, ensure_ascii=False, indent=2))
