import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / '.qa-work/QA04'
OUTPUT = ROOT / 'docs/testing/evidence/QA04'
OUTPUT.mkdir(parents=True, exist_ok=True)
files = ['feedback.json', 'feedback.log', 'audio-browser.json', 'audio-browser.log',
    'photo-regression.json', 'photo-regression.log', 'network.json', 'network.log',
    'therapy-evals.json', 'therapy-evals.log', 'timeout.json', 'timeout.log',
    'input-tests.log', 'backend-tests.log', 'build.log', 'pip-check.log', 'inputs.json',
    'report.png', 'therapy.png', 'blank.png', 'observation.wav', 'browser-recording.webm']
for name in files:
    shutil.copy2(WORK / name, OUTPUT / name)
for source in (WORK / 'therapy-evals').glob('*.png'):
    target = OUTPUT / 'therapy-evals' / source.name
    target.parent.mkdir(exist_ok=True)
    shutil.copy2(source, target)

sources = ['guardian-h5/src/components/VoiceEntry.tsx', 'guardian-h5/src/components/PhotoReview.tsx',
    'guardian-h5/src/components/UploadedPhotos.tsx', 'guardian-h5/src/components/SourceGallery.tsx',
    'guardian-h5/src/api/client.ts', 'guardian-h5/src/api/endpoints.ts',
    'guardian-h5/src/features/home/home.css', 'guardian-h5/src/features/registration/ReportsPage.tsx',
    'guardian-h5/src/features/registration/TherapiesPage.tsx', 'guardian-h5/src/styles/forms.css',
    'guardian-h5/src/styles/globals.css', 'backend/recognition/speech.py', 'backend/recognition/ark.py',
    'backend/recognition/schemas.py', 'backend/api/base.py', 'backend/api/views_family3.py', 'backend/api/urls.py',
    'backend/config/settings/base.py', 'backend/config/settings/qa.py', 'backend/requirements.txt',
    'backend/requirements.lock.txt', 'backend/tests/test_qa04_inputs.py',
    'guardian-h5/scripts/qa04-feedback.mjs', 'guardian-h5/scripts/qa04-audio-browser.mjs',
    'guardian-h5/scripts/qa04-network.mjs', 'backend/verification/qa04_inputs.py',
    'backend/verification/qa04_therapy_evals.py', 'backend/verification/qa04_timeout.py']


def fingerprint(path):
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'path': str(path.relative_to(ROOT)), 'bytes': path.stat().st_size, 'sha256': digest}


manifest = {'generated_at': datetime.now(timezone.utc).isoformat(), 'database': 'sjia_qa03',
    'preview': 'http://127.0.0.1:5174', 'api': 'http://127.0.0.1:8001',
    'data_source': '用户授权的独立测试数据和无患者信息图片，合成普通话音频；实际服务响应',
    'sources': [fingerprint(ROOT / name) for name in sources],
    'evidence': [fingerprint(path) for path in sorted(OUTPUT.rglob('*')) if path.is_file() and path.name != 'manifest.json'],
    'speech_model': [fingerprint(path) for path in sorted((WORK / 'models/small').iterdir()) if path.is_file()]}
(OUTPUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
print(json.dumps({'source_files': len(manifest['sources']), 'evidence_files': len(manifest['evidence'])}))
