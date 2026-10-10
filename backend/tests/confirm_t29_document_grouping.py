import argparse
import json
import os
import sys
import uuid
from pathlib import Path

import django
import httpx


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')
django.setup()

from django.db import connection, transaction
from archives.models import ArchiveDocument, ArchiveDocumentVersion


API_BASE = 'http://127.0.0.1:8001'
REPORT_ROOT = ROOT / '.planning-work' / 'parallel' / 'window1'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + '\n'
    path.write_text(encoded, encoding='utf-8')
    reread = json.loads(path.read_text(encoding='utf-8'))
    require(reread == json.loads(encoded), f'Saved report readback differs: {path.name}.')


def family_client():
    client = httpx.Client(base_url=API_BASE, timeout=45, trust_env=False)
    bootstrap = client.get('/api/auth/bootstrap')
    require(bootstrap.status_code == 200, f'Family bootstrap returned HTTP {bootstrap.status_code}.')
    login = client.post('/api/auth/login', json={
        'username': 'simulation-family', 'password': os.environ['SJIA_SIMULATION_PASSWORD']},
        headers={'X-CSRFToken': bootstrap.json()['csrf_token']})
    require(login.status_code == 200 and login.json().get('audience') == 'family',
        f'Family login returned HTTP {login.status_code}: {login.text}')
    authenticated = httpx.Client(base_url=API_BASE, timeout=45, trust_env=False)
    authenticated.cookies.update(client.cookies)
    authenticated.headers['X-CSRFToken'] = login.json()['csrf_token']
    client.close()
    return authenticated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--document-id', required=True)
    parser.add_argument('--episode-id', required=True, action='append')
    args = parser.parse_args()
    document_id = str(uuid.UUID(args.document_id))
    episode_ids = sorted({str(uuid.UUID(value)) for value in args.episode_id})
    require(episode_ids, 'At least one source-linked simulation episode is required.')
    require(connection.settings_dict['NAME'] == 'sjia_simulation', 'Database must be sjia_simulation.')
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
            require(cursor.fetchone() == ('sjia_simulation', 'sjia', 5433),
                'Database identity differs from approved simulation.')
        document = ArchiveDocument.objects.get(id=document_id)
        current_version = ArchiveDocumentVersion.objects.get(document_id=document.id,
            patient_id=document.patient_id, version=document.current_version)
        patient_id = str(document.patient_id)

    state_path = REPORT_ROOT / f't29-document-grouping-{document_id}.json'
    report_path = REPORT_ROOT / f't29-document-grouping-readback-{document_id}.json'
    target = f'/archive-documents/{document_id}/grouping'
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else None
    if state is not None:
        require(state['document_id'] == document_id and state['episode_ids'] == episode_ids,
            'Existing grouping state binds another document or episode set.')

    with family_client() as client:
        document_response = client.get(f'/api/v2/archive-documents/{document_id}')
        require(document_response.status_code == 200,
            f'Current document read returned HTTP {document_response.status_code}: {document_response.text}')
        current = document_response.json()
        require(current['patient_id'] == patient_id and current['document_id'] == document_id,
            'Document read returned another simulation resource.')
        already_saved = current['grouping_status'] == 'confirmed'
        if already_saved:
            require(state is not None and current['version'] == state['payload']['expected_version'] + 1
                and current['episode_ids'] == episode_ids,
                'Current confirmation does not match this saved grouping request.')
        elif state is not None:
            require(current['version'] == state['payload']['expected_version'],
                'Document version changed after the grouping request was prepared.')
        require('archive_document_group' in current['allowed_actions'],
            'Current family scope does not permit document grouping.')
        inputs_response = client.get(f"/api/v2/archive-batches/{current['batch_id']}/document-inputs")
        require(inputs_response.status_code == 200,
            f'Document input read returned HTTP {inputs_response.status_code}: {inputs_response.text}')
        options = inputs_response.json()
        require(options['patient_id'] == patient_id and options['batch_id'] == current['batch_id'],
            'Document input response belongs to another patient or batch.')
        selected = {row['episode_id']: row for row in options['episodes'] if row['episode_id'] in episode_ids}
        require(set(selected) == set(episode_ids), 'A selected episode is absent from current qualified document inputs.')
        require(all(row['source_page_id'] in current['page_ids'] for row in selected.values()),
            'A selected episode source page is outside the document pages.')
        require(current['document_type'] != 'unknown', 'Document type needs explicit confirmation before grouping.')
        require(current['proposed_metadata'] is None or not current['proposed_metadata']['episodes'],
            'Pending proposed episode metadata needs explicit review before grouping.')

        if state is None:
            payload = {key: current[key] for key in ('batch_id', 'page_ids', 'document_type',
                'organization', 'documented_time', 'confirmed_text', 'proposed_metadata')}
            payload.update({'episode_ids': episode_ids, 'expected_version': current['version'],
                'reason': 'SIMULATION family confirmed the source-linked document grouping.'})
            state = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY', 'database': 'sjia_simulation',
                'patient_id': patient_id, 'document_id': document_id, 'episode_ids': episode_ids,
                'request_id': str(uuid.uuid4()), 'target_key': target, 'payload': payload,
                'intent_id': None, 'generation': None, 'submitted': False}
            save_json(state_path, state)

        if state['intent_id'] is None:
            prepared = client.post('/api/v2/write-intents', json={'operation_id': 'archive_document_group',
                'target_key': target, 'request_id': state['request_id'], 'schema_version': '2.0.0',
                'payload': state['payload']})
            require(prepared.status_code == 201,
                f'Document grouping intent returned HTTP {prepared.status_code}: {prepared.text}')
            state.update({'intent_id': prepared.json()['intent_id'], 'generation': prepared.json()['generation']})
            save_json(state_path, state)

        request_response = client.get(f"/api/v2/requests/{state['intent_id']}", params={
            'lookup': 'request_id', 'operation_id': 'archive_document_group', 'target_key': target})
        require(request_response.status_code == 200,
            f'Document grouping request lookup returned HTTP {request_response.status_code}.')
        request_state = request_response.json()
        require(request_state['request_id'] == state['request_id'], 'Request lookup returned another request.')
        require(request_state['state'] != 'unknown', 'Unknown grouping result must not be retried.')
        if request_state['state'] != 'committed':
            require(request_state['state'] == 'pending' and request_state['may_retry'] is True,
                f'Grouping request is not safe to submit: {request_state["state"]}.')
            require(not state['submitted'], 'Grouping request was already submitted; inspect saved state before retry.')
            response = client.post(f'/api/v2{target}', json=state['payload'], headers={
                'X-Write-Intent': state['intent_id'], 'Idempotency-Key': state['request_id'],
                'X-Intent-Generation': state['generation']})
            require(response.status_code == 200,
                f'Document grouping returned HTTP {response.status_code}: {response.text}')
            state.update({'submitted': True, 'write_response': response.json()})
            save_json(state_path, state)
            request_response = client.get(f"/api/v2/requests/{state['intent_id']}", params={
                'lookup': 'request_id', 'operation_id': 'archive_document_group', 'target_key': target})
            require(request_response.status_code == 200 and request_response.json()['state'] == 'committed',
                'Document grouping request did not read back as committed.')
            request_state = request_response.json()

        reread_response = client.get(f'/api/v2/archive-documents/{document_id}')
        require(reread_response.status_code == 200, 'Grouped document could not be reread.')
        saved = reread_response.json()
        require(saved['grouping_status'] == 'confirmed'
            and saved['version'] == state['payload']['expected_version'] + 1,
            'Document grouping status or version did not persist.')
        require(saved['episode_ids'] == episode_ids, 'Saved episode links differ from the selected source episodes.')

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
            require(cursor.fetchone() == ('sjia_simulation', 'sjia', 5433),
                'Database identity differs from approved simulation.')
        saved_document = ArchiveDocument.objects.get(id=document_id, patient_id=patient_id)
        saved_version = ArchiveDocumentVersion.objects.get(document_id=document_id, patient_id=patient_id,
            version=saved_document.current_version)
        require(saved_version.grouping_status == 'confirmed'
            and saved_document.current_version == state['payload']['expected_version'] + 1,
            'Database document grouping readback differs from the API.')

    report = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY', 'status': 'DOCUMENT_GROUPING_SAVED_AND_READ',
        'database': {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433},
        'patient_id': patient_id, 'document_id': document_id, 'episode_ids': episode_ids,
        'request_id': state['request_id'], 'intent_id': state['intent_id'],
        'document_version': saved['version'], 'grouping_status': saved['grouping_status'],
        'request_state': request_state['state'], 'api_readback': saved,
        'database_readback': {'document_version': saved_document.current_version,
            'grouping_status': saved_version.grouping_status},
        'business_write_count': 1 if state['submitted'] else 0,
        'model_calls': 0, 'external_calls': 0}
    save_json(report_path, report)
    print(json.dumps({'status': report['status'], 'document_id': document_id,
        'version': report['document_version'], 'grouping_status': report['grouping_status'],
        'saved_and_read': True, 'report_path': str(report_path)}, sort_keys=True))


if __name__ == '__main__':
    main()
