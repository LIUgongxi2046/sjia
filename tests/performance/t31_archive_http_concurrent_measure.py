import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPErrorProcessor, HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings
from django.db import connection, transaction
from django.utils.dateparse import parse_datetime
from django.utils.timezone import now
from archives.models import ArchiveDocument, ArchiveDocumentPage, ArchiveDocumentVersion, ArchivePage
from archives.search_schemas import VALIDATORS
from case_details.models import ClinicalEpisode
from files.models import FileObject
from files.storage import LocalFileStore


SAMPLE_COUNT = 20
CONCURRENCY = 4
EXPECTED_DOCUMENT_COUNT = 10
QUERY = {'limit': 20}


class KeepHttpStatusProcessor(HTTPErrorProcessor):
    def http_response(self, request, response):
        return response

    https_response = http_response


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class HttpSession:
    def __init__(self):
        self.opener = build_opener(KeepHttpStatusProcessor, NoRedirectHandler)
        self.opener.add_handler(HTTPCookieProcessor(CookieJar()))

    def request(self, method, url, payload=None, headers=None, timeout=30):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
        request_headers = {'Accept': 'application/json', **(headers or {})}
        if body is not None:
            request_headers['Content-Type'] = 'application/json'
        request = Request(url, data=body, headers=request_headers, method=method)
        response = self.opener.open(request, timeout=timeout)
        return response.status, response.read()


class RequestCounter:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.maximum = 0

    def start(self):
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)

    def finish(self):
        with self.lock:
            self.active -= 1


class RequestWave:
    def __init__(self):
        self.barriers = [threading.Barrier(CONCURRENCY) for _ in range(SAMPLE_COUNT // CONCURRENCY)]

    def wait(self, sample_number):
        self.barriers[(sample_number - 1) // CONCURRENCY].wait(timeout=30)


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))]


def canonical_sha256(value):
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(canonical).hexdigest()


def validate_environment(base_url):
    parsed = urlsplit(base_url)
    database = settings.DATABASES['default']
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
            or parsed.port != 8001 or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise RuntimeError('T31 并发档案测量只允许本机主窗口模拟 API 端口 8001。')
    if (database['NAME'] != 'sjia_simulation' or settings.SJIA_ENVIRONMENT != 'simulation'
            or database['HOST'] not in ('127.0.0.1', 'localhost')):
        raise RuntimeError('T31 并发档案测量只允许本机 sjia_simulation 模拟数据库。')
    return parsed.geturl().rstrip('/'), database


def connection_snapshot():
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION READ ONLY')
        cursor.execute('SELECT current_database(), current_schema(), current_setting(%s), current_setting(%s), current_user',
            ['search_path', 'server_version'])
        identity = cursor.fetchone()
        cursor.execute("SELECT current_setting('max_connections')::integer, current_setting('superuser_reserved_connections')::integer, current_setting('reserved_connections')::integer")
        limits = cursor.fetchone()
        cursor.execute("SELECT count(*) FILTER (WHERE datname = current_database()), count(*) FILTER (WHERE datname = current_database() AND state = 'active'), count(*) FILTER (WHERE datname = current_database() AND state = 'idle'), count(*) FROM pg_stat_activity")
        counts = cursor.fetchone()
    if (identity[0:2] != ('sjia_simulation', 'sjia')
            or identity[2].replace(' ', '') != 'sjia,public'):
        raise RuntimeError('T31 并发档案测量数据库身份不符。')
    usable = limits[0] - limits[1] - limits[2]
    return {'database': identity[0], 'schema': identity[1], 'search_path': identity[2],
        'postgresql': identity[3], 'database_user': identity[4],
        'max_connections': limits[0], 'superuser_reserved_connections': limits[1],
        'reserved_connections': limits[2], 'usable_non_reserved_connections': usable,
        'database_sessions': counts[0], 'database_active_sessions': counts[1],
        'database_idle_sessions': counts[2], 'server_sessions': counts[3],
        'snapshot_remaining_non_reserved_slots': usable - counts[3]}


def login(session, base_url):
    status, content = session.request('GET', f'{base_url}/api/auth/bootstrap', timeout=10)
    if status != 200:
        raise RuntimeError(f'模拟会话初始化失败：HTTP {status}')
    bootstrap = json.loads(content)
    csrf_token = bootstrap['csrf_token']
    status, content = session.request('POST', f'{base_url}/api/auth/login',
        payload={'username': 'simulation-family', 'password': os.environ['SJIA_SIMULATION_PASSWORD']},
        headers={'X-CSRFToken': csrf_token}, timeout=10)
    if status != 200:
        raise RuntimeError(f'模拟账号认证失败：HTTP {status}')
    return json.loads(content)['csrf_token']


def authorized_patient(session, base_url):
    status, content = session.request('GET', f'{base_url}/api/relations', timeout=10)
    if status != 200:
        raise RuntimeError(f'本人关联读取失败：HTTP {status}')
    relation_rows = json.loads(content)['relations']
    current_time = now()
    active = [row for row in relation_rows if row['status'] == 'active'
        and parse_datetime(row['valid_from']) <= current_time
        and (row['valid_until'] is None or parse_datetime(row['valid_until']) > current_time)]
    if not active:
        raise RuntimeError('模拟账号没有有效本人关联。')
    return active[0]['patient_id']


def database_source_check(payload, patient_id, object_store):
    if payload['patient_id'] != patient_id or len(payload['items']) != EXPECTED_DOCUMENT_COUNT:
        return {'valid': False, 'reason': 'PATIENT_OR_DOCUMENT_COUNT_MISMATCH', 'document_count': len(payload['items']),
            'document_version_matches': 0, 'page_references_checked': 0, 'original_files_checked': 0,
            'original_file_bytes_checked': 0, 'original_checksum_matches': 0, 'items': []}
    item_results = []
    page_references_checked = 0
    original_file_bytes_checked = 0
    original_checksum_matches = 0
    for item in payload['items']:
        view = item['document']
        document = ArchiveDocument.objects.filter(id=view['document_id'], patient_id=patient_id).first()
        version = ArchiveDocumentVersion.objects.filter(id=view['version_id'], patient_id=patient_id,
            document_id=view['document_id']).first()
        if document is None or version is None:
            raise RuntimeError('档案回应引用的文书或文书版本在模拟数据库中不存在。')
        document_version_valid = (document.current_version == version.version
            and view['patient_id'] == patient_id
            and view['version'] == str(version.version)
            and view['batch_id'] == str(version.batch_id)
            and view['document_type'] == version.document_type
            and view['organization'] == version.organization
            and view['confirmed_text'] == version.confirmed_text
            and view['proposed_metadata'] == version.proposed_metadata
            and view['grouping_status'] == version.grouping_status
            and parse_datetime(view['available_at']) == version.available_at)
        links = list(ArchiveDocumentPage.objects.filter(patient_id=patient_id,
            document_version_id=version.id).order_by('page_order', 'id'))
        if [str(link.page_id) for link in links] != view['page_ids']:
            document_version_valid = False
        pages = []
        for link, returned in zip(links, item['pages'], strict=True):
            page = ArchivePage.objects.filter(id=link.page_id, patient_id=patient_id).first()
            if page is None:
                raise RuntimeError('档案回应引用的档案页在模拟数据库中不存在。')
            file = FileObject.objects.filter(id=page.file_id, patient_id=patient_id).first()
            if file is None:
                raise RuntimeError('档案回应引用的原件在模拟数据库中不存在。')
            page_matches = (returned['page_id'] == str(page.id)
                and returned['file_page_id'] == str(page.file_page_id)
                and returned['file_id'] == str(file.id)
                and returned['page_order'] == link.page_order
                and returned['object_version'] == page.file_object_version == file.object_version
                and returned['checksum'] == page.checksum == file.checksum
                and returned['status'] == file.status
                and returned['media_type'] == file.media_type
                and parse_datetime(returned['created_at']) == file.created_at
                and file.purpose == FileObject.Purpose.ORIGINAL)
            page_references_checked += 1
            object_digest = hashlib.sha256(object_store.get(file.object_key)).hexdigest()
            original_file_bytes_checked += file.size_bytes
            checksum_matches = object_digest == file.checksum
            original_checksum_matches += int(checksum_matches)
            pages.append({'page_id': str(page.id), 'file_id': str(file.id),
                'file_object_version': file.object_version, 'checksum': file.checksum,
                'page_response_matches_database': page_matches,
                'stored_bytes_sha256_matches_database': checksum_matches,
                'size_bytes': file.size_bytes})
        episode_ids = []
        episodes = []
        for episode_view in item['episodes']:
            episode = ClinicalEpisode.objects.filter(id=episode_view['episode_id'], patient_id=patient_id).first()
            if episode is None:
                raise RuntimeError('档案回应引用的就诊在模拟数据库中不存在。')
            episode_valid = (episode_view['record_version_id'] == str(episode.record_version_id)
                and episode_view['label'] == episode.label
                and episode_view['episode_type'] == episode.episode_type
                and episode_view['organization'] == episode.organization_text)
            episode_ids.append(str(episode.id))
            episodes.append({'episode_id': str(episode.id), 'record_version_id': str(episode.record_version_id),
                'response_matches_database': episode_valid})
            document_version_valid = document_version_valid and episode_valid
        if sorted(episode_ids) != sorted(view['episode_ids']):
            document_version_valid = False
        item_results.append({'document_id': str(document.id), 'document_version_id': str(version.id),
            'document_version': version.version, 'record_version_and_page_refs_valid': document_version_valid,
            'pages': pages, 'episodes': episodes,
            'original_result_sha256': canonical_sha256([{'file_id': page['file_id'],
                'object_version': page['file_object_version'], 'checksum': page['checksum'],
                'stored_bytes_sha256_matches_database': page['stored_bytes_sha256_matches_database']}
                for page in pages])})
    document_valid_count = sum(row['record_version_and_page_refs_valid'] for row in item_results)
    page_count = sum(len(row['pages']) for row in item_results)
    return {'valid': document_valid_count == EXPECTED_DOCUMENT_COUNT
            and original_checksum_matches == page_references_checked,
        'document_count': len(item_results), 'document_version_matches': document_valid_count,
        'page_references_checked': page_references_checked,
        'original_files_checked': len({page['file_id'] for row in item_results for page in row['pages']}),
        'original_file_bytes_checked': original_file_bytes_checked,
        'original_checksum_matches': original_checksum_matches,
        'page_count': page_count, 'items': item_results,
        'source_reference_sha256': canonical_sha256(item_results)}


def measure_one(session, endpoint, patient_id, csrf_token, counter, wave, sample_number):
    wave.wait(sample_number)
    counter.start()
    started = time.perf_counter()
    try:
        status, content = session.request('POST', endpoint, payload=QUERY,
            headers={'X-CSRFToken': csrf_token}, timeout=30)
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        counter.finish()
    payload = json.loads(content)
    schema_errors = list(VALIDATORS['ArchiveSearchView'].iter_errors(payload)) if status == 200 else []
    items = payload.get('items') if isinstance(payload, dict) else None
    items_digest = canonical_sha256(items) if isinstance(items, list) else None
    schema_valid = status == 200 and not schema_errors
    return {'sample': sample_number, 'http_status': status, 'elapsed_ms': elapsed_ms,
        'response_bytes': len(content), 'error_code': payload.get('code') if isinstance(payload, dict) and status >= 400 else None,
        'patient_id': payload.get('patient_id') if isinstance(payload, dict) else None,
        'patient_identity_valid': isinstance(payload, dict) and payload.get('patient_id') == patient_id,
        'schema_valid': schema_valid, 'schema_error': schema_errors[0].message if schema_errors else None,
        'matched_count': payload.get('matched_count') if isinstance(payload, dict) else None,
        'item_count': len(items) if isinstance(items, list) else None,
        'items_sha256': items_digest,
        'snapshot_version': payload.get('snapshot_version') if isinstance(payload, dict) else None,
        'items': items if schema_valid else None,
        'successful': status == 200 and schema_valid and isinstance(items, list)
            and len(items) == EXPECTED_DOCUMENT_COUNT and payload.get('matched_count') == EXPECTED_DOCUMENT_COUNT
            and payload.get('patient_id') == patient_id}


def source_hashes():
    paths = ('backend/archives/search.py', 'backend/archives/search_context.py',
        'backend/configurations/qualification.py', 'backend/configurations/evidence.py',
        'backend-design/archive-search.schema.json')
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8001')
    parser.add_argument('--report', default=str(ROOT / 'docs/testing/T31-archive-concurrent-simulation-report.json'))
    args = parser.parse_args()
    base_url, database = validate_environment(args.base_url)
    connections_before = connection_snapshot()
    session = HttpSession()
    csrf_token = login(session, base_url)
    patient_id = authorized_patient(session, base_url)
    endpoint = f'{base_url}/api/v2/patients/{patient_id}/archives/search'
    counter = RequestCounter()
    wave = RequestWave()
    results = {}
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {pool.submit(measure_one, session, endpoint, patient_id, csrf_token,
            counter, wave, sample): sample for sample in range(1, SAMPLE_COUNT + 1)}
        for future in as_completed(futures):
            sample = futures[future]
            results[sample] = future.result()
    ordered = [results[index] for index in range(1, SAMPLE_COUNT + 1)]
    successful_rows = [row for row in ordered if row['successful']]
    baseline = successful_rows[0] if successful_rows else None
    source_baseline = database_source_check({'patient_id': baseline['patient_id'], 'items': baseline['items']},
        patient_id, LocalFileStore()) if baseline else None
    baseline_digest = baseline['items_sha256'] if baseline else None
    baseline_snapshot = baseline['snapshot_version'] if baseline else None
    for row in ordered:
        row['matches_first_successful_items'] = baseline_digest is not None and row['items_sha256'] == baseline_digest
        row['matches_first_successful_snapshot'] = baseline_snapshot is not None and row['snapshot_version'] == baseline_snapshot
        row['source_references_match_database_verified_baseline'] = bool(source_baseline and source_baseline['valid']
            and row['successful'] and row['matches_first_successful_items'])
        row['response_items_sha256'] = row.pop('items_sha256')
        row.pop('items', None)
    connections_after = connection_snapshot()
    logout_status, _ = session.request('POST', f'{base_url}/api/session/logout', payload={},
        headers={'X-CSRFToken': csrf_token}, timeout=10)
    times = [row['elapsed_ms'] for row in ordered]
    successes = sum(row['successful'] and row['source_references_match_database_verified_baseline']
        for row in ordered)
    failures = SAMPLE_COUNT - successes
    response_bytes = [row['response_bytes'] for row in ordered]
    report = {'task': 'T31', 'classification': 'SIMULATION_ONLY_ARCHIVE_HTTP_CONCURRENT_SMALL_LOAD',
        'measured_at': datetime.now(timezone.utc).isoformat(),
        'runtime_versions': {'python': sys.version.split()[0], 'django': django.get_version(),
            'postgresql': connections_before['postgresql']},
        'target': {'api_base_url': base_url, 'api_process_reference': 'main-window session 11496',
            'database': database['NAME'], 'database_port': database['PORT'], 'schema': 'sjia',
            'account': 'simulation-family', 'patient_id': patient_id,
            'classification': 'SIMULATION_ONLY', 'external_requests': 0},
        'authentication': {'login_count_outside_samples': 1, 'login_status': 200,
            'relation_read_count_outside_samples': 1, 'logout_status': logout_status,
            'same_authenticated_session_used_by_all_requests': True},
        'load': {'total_requests': SAMPLE_COUNT, 'configured_concurrency': CONCURRENCY,
            'observed_maximum_in_flight': counter.maximum,
            'concurrency_waves': SAMPLE_COUNT // CONCURRENCY, 'automatic_retries': 0,
            'api_interception': False, 'fixed_query_body': QUERY,
            'expected_documents_per_response': EXPECTED_DOCUMENT_COUNT,
            'query_sha256': canonical_sha256(QUERY)},
        'connection_budget': {'before': connections_before, 'after': connections_after,
            'connection_snapshot_is_not_capacity_acceptance': True},
        'response': {'successes': successes, 'failures': failures, 'failure_rate': failures / SAMPLE_COUNT,
            'http_statuses': sorted({row['http_status'] for row in ordered}),
            'schema_valid_count': sum(row['schema_valid'] for row in ordered),
            'patient_identity_valid_count': sum(row['patient_identity_valid'] for row in ordered),
            'document_count_values': sorted({row['item_count'] for row in ordered if row['item_count'] is not None}),
            'matched_count_values': sorted({row['matched_count'] for row in ordered if row['matched_count'] is not None}),
            'all_successful_items_match_baseline': all(row['matches_first_successful_items'] for row in ordered if row['successful']),
            'all_successful_snapshots_match_baseline': all(row['matches_first_successful_snapshot'] for row in ordered if row['successful']),
            'mean_response_bytes': statistics.mean(response_bytes)},
        'source_verification': {'verified_against_simulation_database': bool(source_baseline and source_baseline['valid']),
            'verified_document_count': source_baseline['document_version_matches'] if source_baseline else 0,
            'verified_page_reference_count': source_baseline['page_references_checked'] if source_baseline else 0,
            'original_file_count': source_baseline['original_files_checked'] if source_baseline else 0,
            'original_file_bytes_read': source_baseline['original_file_bytes_checked'] if source_baseline else 0,
            'original_checksum_matches': source_baseline['original_checksum_matches'] if source_baseline else 0,
            'reference_digest_sha256': source_baseline['source_reference_sha256'] if source_baseline else None,
            'verified_references': source_baseline['items'] if source_baseline else []},
        'latency_ms': {'p50': percentile(times, .50), 'p95': percentile(times, .95),
            'p99': percentile(times, .99), 'minimum': min(times), 'maximum': max(times),
            'samples': times, 'measurement_scope': 'HTTP request round trip only; JSON parsing, Schema, and source checks excluded'},
        'samples': ordered, 'source_sha256': source_hashes(),
        'side_effects': {'records_written': 0, 'worker_or_beat_started': False,
            'queue_consumption': 0, 'provider_calls': 0, 'notifications_sent': 0},
        'scope': {'development_target': 'SIMULATION_ONLY', 'formal_capacity_slo': 'NOT_RUN',
            'real_capacity': 'NOT_RUN', 'clinical_scale': 'NOT_RUN', 'approved_capacity_target': 'NOT_PROVIDED'},
        'saved_and_reread': True}
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if json.loads(report_path.read_text()) != report:
        raise RuntimeError('并发档案测量报告保存后回读内容不一致。')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
