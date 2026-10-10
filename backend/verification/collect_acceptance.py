import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator


root = Path(__file__).resolve().parents[2]
work = root / '.qa-work'
destination = root / 'docs/testing/evidence/QA01'
destination.mkdir(parents=True, exist_ok=True)
http_report = json.loads((work / 'http-security.json').read_text())
schema = json.loads((root / 'docs/testing/api-negative.schema.json').read_text())
Draft202012Validator(schema).validate(http_report)
for name in ['http-security.json', 'design-check.json', 'build.log', 'django-check.log', 'migrations.log', 'dataset.log']:
    shutil.copyfile(work / name, destination / name)
folders = ['guardian-h5/src', 'guardian-h5/scripts', 'guardian-h5/dist', 'backend/api', 'backend/records', 'backend/drafts', 'backend/summaries', 'backend/files', 'backend/requests', 'backend/config/settings', 'backend/verification']
sources = {}
for folder in folders:
    for path in sorted((root / folder).rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix in {'.py', '.tsx', '.ts', '.mjs', '.css', '.js', '.html'}:
            sources[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
manifest = {'captured_at': datetime.now(timezone.utc).isoformat(), 'source_sha256': sources, 'evidence_sha256': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(destination.iterdir()) if path.is_file() and path.name != 'manifest.json'}, 'clinical_data_written': False, 'synthetic_patients_used': False, 'screenshots_taken': False}
(destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
print(json.dumps({'schema': 'PASS', 'source_files': len(sources), 'output': str(destination)}, ensure_ascii=False))
