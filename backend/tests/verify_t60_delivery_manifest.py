import argparse
import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / '.planning-work' / 'parallel' / 'window1' / 't60-delivery-manifest.json'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


parser = argparse.ArgumentParser()
parser.add_argument('--run-tag', required=True)
args = parser.parse_args()
require(re.fullmatch(r'[a-z0-9][a-z0-9-]{2,47}', args.run_tag) is not None,
    'Run tag must be a unique lowercase alphanumeric label.')
OUTPUT_PATH = ROOT / '.planning-work' / 'parallel' / 'window1' / f't60-delivery-manifest-verification-{args.run_tag}.json'
require(not OUTPUT_PATH.exists(), 'Verification report already exists; choose a new run tag.')
manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
require(manifest['task_id'] == 'T60'
    and manifest['mode'] == 'SIMULATION_ONLY'
    and manifest['manifest_version'] == 't60-local-delivery-1',
    'T60交付清单身份不匹配。')
require(manifest['acceptance_boundaries'] == {
    'clinical_evaluation': 'NOT_RUN',
    'real_device': 'NOT_RUN',
    'wechat': 'NOT_RUN',
    'visual_review_for_all_39_pages_and_states': 'NOT_RUN',
    'production_environment': 'NOT_RUN',
    'official_rules_submission_and_display_permission': 'NOT_PROVIDED',
}, 'T60交付清单缺少未执行范围。')
require(any(item['path'] == 'docs/development/T54.md' and item['status'] == 'REFERENCE_ONLY'
    for item in manifest['source_files']), '39页与交互状态引用缺失。')
require(any(item['path'] == '.planning-work/coordination/root/provider-budget-reservations.json'
    for item in manifest['evidence_files']), '模拟预算门禁回执缺失。')
require(any(item['path'] == '.planning-work/coordination/root/voice-budget-current-qualification.log'
    for item in manifest['evidence_files']), '语音配置资格回执缺失。')
require(any(item['path'] == '.planning-work/coordination/root/voice-task-26a9a814-350f-4495-bfd9-d2f87085bf19.json'
    for item in manifest['evidence_files']), '语音单事件模型处理回执缺失。')
for required_path in (
    '.planning-work/parallel/window1/t29-common-scope-final-20261009T091845Z.json',
    '.planning-work/coordination/root/task-12481825-d23c-4f1a-bf8b-f94929e56400.json',
    '.planning-work/coordination/root/task-7d063afd-e9c1-4eeb-b19b-5ee7cf3ff796.json',
    '.planning-work/coordination/root/task-7f5783b6-a11e-4335-9735-711d5d290a2d.json',
    '.planning-work/parallel/window1/t23-native-delivery-final-20261009-091845-readback.json',
    '.planning-work/parallel/window1/t29-summary-final-20261009-091845-summary-readback.json',
    '.planning-work/parallel/window1/t33-delivery-final-20261009.json',
    '.planning-work/parallel/window3/T55-voice-adoption-roundtrip-evidence.json',
):
    require(any(item['path'] == required_path for item in manifest['evidence_files']),
        f'当前共同模拟范围来源缺失：{required_path}')
require(any(item['path'] == 'backend/tests/continue_t23_native_notification.py'
    for item in manifest['source_files']), 'T23当前通知读取程序缺失。')
for required_path in (
    'backend/tests/run_t23_simulation_notification.py',
    'backend/tests/create_t29_static_assessment_tasks.py',
    'backend/tests/create_t29_dynamic_evaluation.py',
    'backend/tests/run_t29_summary_roundtrip.py',
    'backend/tests/read_window1_current_qualification.py',
    'backend/tests/create_t29_common_scope.py',
    'deploy/execute_simulation_task.py',
    'deploy/execute_simulation_notification.py',
):
    require(any(item['path'] == required_path for item in manifest['source_files']),
        f'T29/T23 explicit simulation-fixture runner is missing: {required_path}')

verified_files = []
for category in ('source_files', 'evidence_files'):
    for item in manifest[category]:
        path = (ROOT / item['path']).resolve()
        require(path.is_relative_to(ROOT), f'清单路径越出项目目录：{item["path"]}')
        require(path != ROOT / '.planning-work/parallel/window1/t60-simulation-evidence-index.json',
            '生成的证据索引必须作为交付输出，不能同时作为自身输入。')
        content = path.read_bytes()
        verified_files.append({'category': category, 'path': item['path'],
            'status': item['status'], 'bytes': len(content),
            'sha256': hashlib.sha256(content).hexdigest()})

report = {'task_id': 'T60', 'mode': 'SIMULATION_ONLY', 'status': 'PASS',
    'manifest_path': str(MANIFEST_PATH.relative_to(ROOT)),
    'manifest_sha256': hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest(),
    'source_file_count': len(manifest['source_files']),
    'evidence_file_count': len(manifest['evidence_files']),
    'verified_files': verified_files,
    'acceptance_boundaries': manifest['acceptance_boundaries'],
    'saved_and_read': True}
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
saved = json.loads(OUTPUT_PATH.read_text(encoding='utf-8'))
require(saved == report, 'T60清单核验报告保存后读取不一致。')
print(json.dumps({'task_id': report['task_id'], 'status': report['status'],
    'source_file_count': report['source_file_count'],
    'evidence_file_count': report['evidence_file_count'],
    'saved_and_read': report['saved_and_read'],
    'output': str(OUTPUT_PATH.relative_to(ROOT))}, ensure_ascii=False, sort_keys=True))
