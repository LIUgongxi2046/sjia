import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.db import connection, transaction
from django.utils import timezone
from alerting.lifecycle import lifecycle_condition
from alerting.models import AlertDecision, AlertEpisode, AlertEvaluation, AlertEvent
from core.management.commands.seed_simulation import require_simulation
from family_content.models import EnvironmentGate
from tasks.models import Task

require_simulation()
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
        if cursor.fetchone() != ('sjia_simulation', 'sjia', 5433):
            raise RuntimeError('预警周期检查仅允许准确模拟数据库')
    task = Task.objects.get(id='7f5783b6-a11e-4335-9735-711d5d290a2d', status='completed')
    evaluation = AlertEvaluation.objects.get(task_id=task.id)
    decision = AlertDecision.objects.get(evaluation_id=evaluation.id)
    episode = AlertEpisode.objects.get(id=decision.episode_id, patient_id=evaluation.patient_id)
    event = AlertEvent.objects.get(id=episode.last_event_id, patient_id=evaluation.patient_id)
    event_evaluation = AlertEvaluation.objects.get(id=event.evaluation_id, patient_id=evaluation.patient_id)
    gate = EnvironmentGate.objects.get(environment='simulation', enabled=True)
    snapshot = task.input_refs['snapshot']
    report = {
        'mode': 'SIMULATION_ONLY', 'status': 'READ_ONLY_DIAGNOSIS',
        'checked_at': timezone.now().isoformat(), 'task_id': str(task.id),
        'patient_id': str(evaluation.patient_id),
        'evaluation': {'id': str(evaluation.id), 'validity': evaluation.validity,
            'decision': evaluation.decision, 'publication_epoch': evaluation.publication_epoch,
            'context_sequence': evaluation.context_sequence},
        'lifecycle': {'action': decision.action, 'reason_codes': decision.reason_codes,
            'episode_id': str(episode.id), 'episode_status': episode.status,
            'last_evaluation_id': str(episode.last_evaluation_id),
            'last_event_id': str(event.id)},
        'last_event': {'validity': event.validity, 'release_id': str(event.release_id),
            'evaluation_id': str(event_evaluation.id),
            'evaluation_publication_epoch': event_evaluation.publication_epoch,
            'release_status': event.release.status},
        'current_publication': {'release_id': str(gate.active_release_id),
            'publication_epoch': gate.publication_epoch},
        'event_matches_current_publication': event.release_id == gate.active_release_id
            and event_evaluation.publication_epoch == gate.publication_epoch,
        'approved_episode_policy': evaluation.policy.payload['episode'],
        'approved_reset': snapshot['lifecycle']['reset'],
        'approved_retrigger': snapshot['lifecycle']['retrigger'],
        'reset_conditions_met': lifecycle_condition(snapshot, 'reset', snapshot['signals']),
        'retrigger_conditions_met': lifecycle_condition(snapshot, 'retrigger', snapshot['signals']),
        'combinations': snapshot['combinations'], 'signals': snapshot['signals'],
        'business_writes': 0, 'external_calls': 0, 'visual': 'NOT_RUN',
        'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
target = ROOT / '.planning-work/coordination/root/episode-publication-diagnosis.json'
target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
if json.loads(target.read_text()) != report:
    raise RuntimeError('周期诊断报告保存重读不一致')
print(json.dumps({key: report[key] for key in ('status', 'task_id',
    'event_matches_current_publication', 'reset_conditions_met', 'retrigger_conditions_met')}, ensure_ascii=False))
