import hashlib
import json
from pathlib import Path

from prepare_simulation_voice import guard
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from recognition.models import VoiceInput
from records.models import Record, RecordVersion
from records.service import read_version
from tasks.models import Task

ROOT = Path(__file__).resolve().parents[1]
guard()
fixture = json.loads((ROOT / '.simulation-work/t55-voice-fixture.json').read_text())
source = ROOT / '.planning-work/parallel/window3/T55-voice-adoption-api-response-first-save.json'
actual = json.loads(source.read_text())
if actual['data_mode'] != 'SIMULATION' or actual['http_status'] != 200:
    raise RuntimeError('本人采用没有已保存的实际成功响应')
schemas = {'voice-input.schema.json': ROOT / 'backend-design/voice-input.schema.json',
    'family-record.schema.json': ROOT / 'backend/records/search.schema.json'}
registry = Registry().with_resources((name, Resource.from_contents(json.loads(path.read_text())))
    for name, path in schemas.items())
validator = Draft202012Validator({'$ref': 'voice-input.schema.json#/$defs/AdoptionView'},
    registry=registry, format_checker=FormatChecker())
body = actual['response_body']
validator.validate(body)
view = body['voice_input']
voice = VoiceInput.objects.get(id=view['id'], actor_id=fixture['owner_id'], patient_id=fixture['patient_id'],
    request_id=actual['request_id'], input_mode='SIMULATION_TEXT_ONLY')
version = RecordVersion.objects.get(id=body['record']['record_version_id'], patient_id=voice.patient_id,
    author_id=voice.actor_id, capture_method='voice_confirmed')
record = Record.objects.get(id=body['record']['record_id'], patient_id=voice.patient_id)
if version.record_id != record.id or str(voice.saved_record_version_id) != str(version.id):
    raise RuntimeError('本人采用回执与保存版本不一致')
reread = read_version(version, record)
if reread != body['record'] or voice.decisions != view['decisions']:
    raise RuntimeError('保存记录及本人五项决定重新读取不一致')
task = Task.objects.get(id=voice.task_id, patient_id=voice.patient_id, actor_id=voice.actor_id, status='completed')
if len(voice.decisions) != 5 or any(field['source_anchor']['file_id'] != fixture['original_file_id']
        or field['source_anchor']['original_text'] != fixture['confirmed_text'] for field in reread['fields']):
    raise RuntimeError('本人五项决定及准确原件出处不完整')
report = {'status': 'PASS', 'mode': 'SIMULATION_TEXT_ONLY', 'strict_adoption_schema': 'PASS',
    'record_and_decisions_saved_reread': True, 'voice_input_id': str(voice.id), 'task_id': str(task.id),
    'record_id': str(record.id), 'record_version_id': str(version.id), 'decisions': len(voice.decisions),
    'record_fields': len(reread['fields']), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    'actual_invoice': 'NOT_RUN', 'audio_transcription': 'NOT_RUN', 'clinical_approval': 'NOT_RUN', 'visual': 'NOT_RUN'}
target = ROOT / '.planning-work/coordination/root/voice-adoption-strict-readback.json'
data = json.dumps(report, ensure_ascii=False, indent=2).encode()
target.write_bytes(data)
if target.read_bytes() != data:
    raise RuntimeError('本人采用共享检查报告保存重读不一致')
print(json.dumps({**report, 'report_sha256': hashlib.sha256(data).hexdigest()}, ensure_ascii=False))
