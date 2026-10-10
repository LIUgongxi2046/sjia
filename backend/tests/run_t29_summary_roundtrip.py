import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from calendar import monthrange
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import django
import httpx


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')
django.setup()

from django.db import connection, transaction
from django.db.models import Q
from medical_time.models import MedicalTime
from records.models import Record, RecordField, RecordVersion
from summaries.models import Summary, SummaryItem
from tasks.models import Task


PATIENT_ID = '405514ce-9335-4705-a750-d811148bda4c'
STATE = ROOT / '.planning-work' / 'parallel' / 'window1' / 't29-summary-state.json'
REPORT = ROOT / '.planning-work' / 'parallel' / 'window1' / 't29-summary-readback.json'
API_BASE = 'http://127.0.0.1:8001'
TARGET = f'/api/patients/{PATIENT_ID}/summaries'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + '\n'
    path.write_text(encoded, encoding='utf-8')
    reread = json.loads(path.read_text(encoding='utf-8'))
    require(reread == json.loads(encoded), f'Saved report readback differs: {path.name}.')


def family_client(family_username):
    client = httpx.Client(base_url=API_BASE, timeout=45, trust_env=False)
    bootstrap = client.get('/api/auth/bootstrap')
    require(bootstrap.status_code == 200, f'Family bootstrap returned HTTP {bootstrap.status_code}.')
    login = client.post('/api/auth/login', json={
        'username': family_username, 'password': os.environ['SJIA_SIMULATION_PASSWORD']},
        headers={'X-CSRFToken': bootstrap.json()['csrf_token']})
    require(login.status_code == 200 and login.json().get('audience') == 'family',
        f'Family login returned HTTP {login.status_code}: {login.text}')
    client.headers['X-CSRFToken'] = login.json()['csrf_token']
    authenticated = httpx.Client(base_url=API_BASE, timeout=45, trust_env=False)
    authenticated.cookies.update(client.cookies)
    authenticated.headers['X-CSRFToken'] = login.json()['csrf_token']
    client.close()
    return authenticated


def source_date_bounds(patient_id):
    dates = []
    record_ids = []
    kinds = set()
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
            require(cursor.fetchone() == ('sjia_simulation', 'sjia', 5433), 'Database identity differs from approved simulation.')
        records = list(Record.objects.filter(patient_id=patient_id, status='active',
            kind__in=('observation', 'report', 'therapy')).values('id', 'kind', 'current_version'))
        record_ids = [str(row['id']) for row in records]
        kinds = {row['kind'] for row in records}
        current_scope = Q(pk__in=[])
        for row in records:
            current_scope |= Q(record_id=row['id'], version=row['current_version'])
        current_versions = RecordVersion.objects.filter(patient_id=patient_id).filter(current_scope)
        current_version_ids = list(current_versions.values_list('id', flat=True))
        time_ids = list(RecordField.objects.filter(patient_id=patient_id,
            record_version_id__in=current_version_ids, medical_time_id__isnull=False)
            .values_list('medical_time_id', flat=True).distinct())
        times = MedicalTime.objects.filter(patient_id=PATIENT_ID, id__in=time_ids)
        zone = ZoneInfo('Asia/Shanghai')
        for medical_time in times:
            if medical_time.instant:
                dates.append(medical_time.instant.astimezone(zone).date())
            elif medical_time.local_date:
                dates.append(medical_time.local_date)
            elif medical_time.local_datetime:
                dates.append(medical_time.local_datetime.date())
            elif medical_time.local_year and medical_time.local_month:
                dates.append(date(medical_time.local_year, medical_time.local_month, 1))
                end_month = medical_time.end_month or medical_time.local_month
                end_year = medical_time.end_year or medical_time.local_year
                dates.append(date(end_year, end_month, monthrange(end_year, end_month)[1]))
    require(record_ids, 'No readable simulated observation, report or therapy records exist for this patient.')
    require(dates, 'Current simulated records do not contain dated fields; summary generation needs an evidence-based date scope.')
    return min(dates).isoformat(), max(dates).isoformat(), sorted(kinds), sorted(set(record_ids))


def main():
    global PATIENT_ID, STATE, REPORT, TARGET
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-tag', required=True)
    parser.add_argument('--fixture-file', default=str(ROOT / '.simulation-work' / 't29-business-fixture.json'))
    args = parser.parse_args()
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{2,47}', args.run_tag) is not None,
        'Run tag must be a unique lowercase alphanumeric label with optional hyphens.')
    report_root = ROOT / '.planning-work' / 'parallel' / 'window1'
    STATE = report_root / f't29-summary-{args.run_tag}-state.json'
    REPORT = report_root / f't29-summary-{args.run_tag}-readback.json'
    fixture_path = Path(args.fixture_file).resolve()
    require(fixture_path.is_relative_to((ROOT / '.simulation-work').resolve()),
        'T29 fixture must be saved in the simulation-work directory.')
    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes)
    require(fixture.get('mode') == 'SIMULATION_ONLY', 'T29 requires an explicitly marked simulation fixture.')
    PATIENT_ID = str(uuid.UUID(fixture['patient_id']))
    require(isinstance(fixture.get('family_username'), str) and fixture['family_username'].strip(),
        'T29 fixture must identify its simulation family account.')
    fixture_sha256 = hashlib.sha256(fixture_bytes).hexdigest()
    TARGET = f'/api/patients/{PATIENT_ID}/summaries'
    require(connection.settings_dict['NAME'] == 'sjia_simulation', 'Database must be sjia_simulation.')
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding='utf-8'))
        require(state['patient_id'] == PATIENT_ID
            and state.get('business_fixture_sha256') == fixture_sha256,
            'Existing summary state belongs to another simulation fixture.')
    else:
        state = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY', 'patient_id': PATIENT_ID,
            'request_id': str(uuid.uuid4()), 'intent_id': None, 'generation': None,
            'summary_id': None, 'task_id_value': None, 'model_calls': 0, 'external_calls': 0,
            'run_tag': args.run_tag, 'business_fixture_sha256': fixture_sha256,
            'business_fixture_path': str(fixture_path.relative_to(ROOT)),
            'family_username': fixture['family_username'],
            'submission_attempted': False}
        save_json(STATE, state)

    with family_client(fixture['family_username']) as client:
        if state['intent_id'] is None:
            from_date, to_date, record_kinds, record_ids = source_date_bounds(PATIENT_ID)
            payload = {'from_date': from_date, 'to_date': to_date, 'scope_timezone': 'Asia/Shanghai',
                'include_unknown_time': True, 'record_kinds': record_kinds, 'include_source_status': True}
            state.update({'from_date': from_date, 'to_date': to_date, 'source_record_ids': record_ids,
                'payload': payload, 'target_key': TARGET})
            save_json(STATE, state)
            prepared = client.post('/api/write-intents', json={'operation_id': 'summary_create',
                'target_key': TARGET, 'request_id': state['request_id'], 'schema_version': '1.0.0', 'payload': payload})
            require(prepared.status_code == 201, f'Summary intent preparation returned HTTP {prepared.status_code}: {prepared.text}')
            state.update({'intent_id': prepared.json()['intent_id'], 'generation': prepared.json()['generation']})
            save_json(STATE, state)

        request_response = client.get(f"/api/requests/{state['intent_id']}")
        require(request_response.status_code == 200, f'Summary request read returned HTTP {request_response.status_code}.')
        request_state = request_response.json()
        require(request_state['request_id'] == state['request_id'], 'Summary request read returned another request.')
        if request_state['state'] == 'committed':
            state['summary_id'] = request_state['receipt']['resource_id']
        else:
            require(request_state['state'] == 'pending' and request_state['may_retry'] is False
                and state.get('submission_attempted', False) is False,
                f'Summary request is not safe for its first submission: {request_state["state"]}.')
            state['submission_attempted'] = True
            save_json(STATE, state)
            response = client.post(TARGET, json=state['payload'], headers={
                'X-Write-Intent': state['intent_id'], 'Idempotency-Key': state['request_id'],
                'X-Intent-Generation': state['generation']})
            require(response.status_code == 202, f'Summary create returned HTTP {response.status_code}: {response.text}')
            task_view = response.json()
            state['task_id_value'] = task_view['task_id']
            state['create_response'] = task_view
            require(task_view['status'] == 'completed' and task_view['result_ref'],
                f'Summary task did not complete synchronously: {task_view["status"]}.')
            state['summary_id'] = task_view['result_ref']
            save_json(STATE, state)
            request_response = client.get(f"/api/requests/{state['intent_id']}")
            require(request_response.status_code == 200 and request_response.json()['state'] == 'committed',
                'Summary request did not read back as committed.')
            request_state = request_response.json()

        summary_response = client.get(f"/api/summaries/{state['summary_id']}")
        require(summary_response.status_code == 200, f'Summary read returned HTTP {summary_response.status_code}.')
        summary_value = summary_response.json()
        require(summary_value['summary_id'] == state['summary_id'] and summary_value['patient_id'] == PATIENT_ID,
            'Summary read returned another resource or patient.')
        task_id = state['task_id_value'] or summary_value['task_id']
        task_response = client.get(f'/api/tasks/{task_id}')
        require(task_response.status_code == 200, f'Summary task read returned HTTP {task_response.status_code}.')
        listing = client.get(f'/api/patients/{PATIENT_ID}/summaries')
        require(listing.status_code == 200, f'Summary list read returned HTTP {listing.status_code}.')
        listed = [item for item in listing.json()['summaries'] if item['summary_id'] == state['summary_id']]
        require(len(listed) == 1, 'Saved summary is absent or duplicated in the patient summary list.')
        state.update({'request_read': request_state, 'task_read': task_response.json(),
            'summary_read': summary_value, 'summary_list_item': listed[0]})
        save_json(STATE, state)

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
            require(cursor.fetchone() == ('sjia_simulation', 'sjia', 5433), 'Database identity differs from approved simulation.')
        summary = Summary.objects.get(id=state['summary_id'], patient_id=PATIENT_ID)
        task = Task.objects.get(id=summary.task_id, patient_id=PATIENT_ID, task_type='summary')
        items = list(SummaryItem.objects.filter(summary_id=summary.id).order_by('ordinal').values(
            'id', 'record_version_id', 'field_id', 'ordinal', 'snapshot'))
        if not state['summary_read']['needs_regeneration']:
            require(len(items) == len(state['summary_read']['items']), 'Database and API summary item counts differ.')
        require({str(item['snapshot']['record_id']) for item in items} <= set(state['source_record_ids']),
            'Summary contains a source record outside the API-derived scope.')
        inventory = {'summary_id': str(summary.id), 'task_id': str(task.id), 'task_status': task.status,
            'request_id': str(task.request_row.request_id), 'summary_item_count': len(items),
            'api_visible_item_count': len(state['summary_read']['items']),
            'summary_needs_regeneration': state['summary_read']['needs_regeneration'],
            'source_record_ids': sorted({str(item['snapshot']['record_id']) for item in items}),
            'source_version_ids': sorted({str(item['record_version_id']) for item in items}),
            'item_ids': [str(item['id']) for item in items]}

    report = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY', 'status': 'SUMMARY_CREATED_AND_READ',
        'database': {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433}, 'api_base': API_BASE,
        'run_tag': args.run_tag, 'business_fixture_sha256': fixture_sha256,
        'patient_id': PATIENT_ID, 'request_id': state['request_id'], 'from_date': state['from_date'],
        'to_date': state['to_date'], 'source_record_ids': state['source_record_ids'], 'summary': inventory,
        'summary_api_readback': state['summary_read'], 'summary_task_api_readback': state['task_read'],
        'model_calls': 0, 'external_calls': 0}
    save_json(REPORT, report)
    print(json.dumps({'status': report['status'], 'summary_id': inventory['summary_id'],
        'task_id': inventory['task_id'], 'task_status': inventory['task_status'],
        'summary_item_count': inventory['summary_item_count'], 'saved_and_read': True,
        'report_path': str(REPORT)}, sort_keys=True))


if __name__ == '__main__':
    main()
