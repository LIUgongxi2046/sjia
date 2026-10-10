import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend'
sys.path.insert(0, str(BACKEND))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone as django_timezone
from archives.models import ArchiveBatch, ArchiveDocument, ArchiveDocumentPage, ArchiveDocumentVersion, ArchivePage, ArchivePageAttempt
from authorization.policy import matching_case_grants
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from family_content.models import EnvironmentGate
from files.models import FileObject, FilePage
from files.service import object_store
from files.storage import digest_bytes
from identity.models import Patient, PrincipalCredential
from records.models import RecordField, RecordVersion
from relations.models import Relation
from summaries.models import Summary, SummaryItem
from sources.models import SourceRegistry, SourceRevision
from tasks.models import ConsumerReceipt, OutboxEvent, Task, TaskAttempt
from tasks.outbox import claim_events, emit_event
from tasks.service import acquire_lease, cancel_task, complete_task, create_attempt, fail_task, finish_attempt, heartbeat, register_task
from tasks.worker import consume


def check_environment():
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_schema(), current_setting(%s)', ['search_path'])
        identity = cursor.fetchone()
        cursor.execute('SHOW server_version')
        postgres_version = cursor.fetchone()[0]
    if settings.DATABASES['default']['NAME'] != 'sjia_simulation' or identity != ('sjia_simulation', 'sjia', 'sjia,public'):
        raise RuntimeError('T57 只允许连接 sjia_simulation 的 sjia schema。')
    if settings.SJIA_ENVIRONMENT != 'simulation' or settings.FILE_OBJECT_ROOT != settings.SIMULATION_ROOT / 'object-store':
        raise RuntimeError('T57 模拟配置或对象目录身份错误。')
    return identity, postgres_version


def resume_event(event_id):
    check_environment()
    event = OutboxEvent.objects.get(id=event_id)
    claimed = claim_events(limit=1, owner=f'window4-restart-{uuid4()}', lease_duration_seconds=30,
        event_types=[event.event_type])
    if len(claimed) != 1 or claimed[0].id != event.id:
        raise RuntimeError('进程重启后未能重新领取指定模拟 Outbox 事件。')
    print(json.dumps({'event_id': str(event.id), 'lease_generation': claimed[0].lease_generation}))


def file_integrity():
    documents = list(ArchiveDocument.objects.order_by('id'))
    versions = list(ArchiveDocumentVersion.objects.order_by('document_id', 'version'))
    originals = list(FileObject.objects.filter(purpose='original', status='available').order_by('id'))
    verified = 0
    errors = 0
    shape_counts = {}
    for item in originals:
        valid = object_store.exists(item.object_key)
        if valid:
            content = object_store.get(item.object_key)
            valid = len(content) == item.size_bytes and digest_bytes(content) == item.checksum
        verified += int(valid)
        errors += int(not valid)
        shape = f'{item.media_type}:{item.size_bytes}'
        shape_counts[shape] = shape_counts.get(shape, 0) + 1
    dangling_versions = sum(not ArchiveDocument.objects.filter(id=row.document_id,
        current_version__gte=row.version).exists() for row in versions)
    return {'documents': len(documents), 'versions': len(versions), 'available_originals': len(originals),
        'original_bytes_verified': verified, 'original_integrity_errors': errors,
        'original_file_shapes': shape_counts, 'dangling_document_versions': dangling_versions}


def restored_reference_integrity():
    documents = list(ArchiveDocument.objects.values('id', 'patient_id', 'current_version'))
    versions = list(ArchiveDocumentVersion.objects.values('id', 'patient_id', 'document_id', 'version', 'batch_id'))
    pages = list(ArchivePage.objects.values('id', 'patient_id', 'batch_id', 'file_id', 'file_page_id'))
    document_pages = list(ArchiveDocumentPage.objects.values('patient_id', 'document_version_id', 'page_id'))
    batches = set(ArchiveBatch.objects.values_list('id', flat=True))
    files = set(FileObject.objects.values_list('id', flat=True))
    file_pages = set(FilePage.objects.values_list('id', flat=True))
    document_version_ids = {(row['id'], row['patient_id']) for row in versions}
    document_version_keys = {(row['document_id'], row['patient_id'], row['version']) for row in versions}
    document_keys = {(row['id'], row['patient_id']) for row in documents}
    page_keys = {(row['id'], row['patient_id']) for row in pages}
    current_version_missing = sum((row['id'], row['patient_id'], row['current_version']) not in document_version_keys
        for row in documents)
    missing_document_refs = sum((row['document_id'], row['patient_id']) not in document_keys for row in versions)
    missing_document_batches = sum(row['batch_id'] not in batches for row in versions)
    missing_page_batches = sum(row['batch_id'] not in batches for row in pages)
    missing_page_files = sum(row['file_id'] not in files or row['file_page_id'] not in file_pages for row in pages)
    missing_document_page_refs = sum((row['document_version_id'], row['patient_id']) not in document_version_ids
        or (row['page_id'], row['patient_id']) not in page_keys for row in document_pages)
    summaries = set(Summary.objects.values_list('id', flat=True))
    record_versions = set(RecordVersion.objects.values_list('id', flat=True))
    record_fields = set(RecordField.objects.values_list('id', flat=True))
    summary_items = list(SummaryItem.objects.values('summary_id', 'record_version_id', 'field_id'))
    missing_summary_item_refs = sum(row['summary_id'] not in summaries or row['record_version_id'] not in record_versions
        or row['field_id'] not in record_fields for row in summary_items)
    return {'documents': len(documents), 'document_versions': len(versions), 'document_pages': len(document_pages),
        'archive_pages': len(pages), 'summaries': len(summaries), 'summary_items': len(summary_items),
        'missing_document_references': missing_document_refs, 'missing_current_document_versions': current_version_missing,
        'missing_document_batches': missing_document_batches, 'missing_archive_page_batches': missing_page_batches,
        'missing_archive_page_files': missing_page_files, 'missing_document_page_references': missing_document_page_refs,
        'missing_summary_item_references': missing_summary_item_refs}


def restored_permission_check():
    actor = PrincipalCredential.objects.get(username='simulation-family').principal
    relation = Relation.objects.filter(guardian=actor, status='active').order_by('id').first()
    if relation is None:
        return {'account_active': actor.account_status == 'active', 'active_relation': False,
            'directory_grants': 0, 'permission_replay': 'DENIED_NO_ACTIVE_RELATION'}
    patient = Patient.objects.get(id=relation.patient_id)
    grants = list(matching_case_grants(actor, patient, 'read_raw', django_timezone.now(), require_relation=True))
    return {'account_active': actor.account_status == 'active', 'active_relation': True,
        'directory_grants': len(grants), 'permission_replay': 'ALLOW' if grants else 'DENY'}


def restored_configuration_evidence_check():
    root = Path(settings.CONFIG_EVIDENCE_ROOT).resolve(strict=True)
    checked_sources = 0
    checked_revisions = 0
    missing_files = 0
    checksum_mismatches = 0
    for source in SourceRegistry.objects.order_by('id'):
        location = Path(source.location)
        if not location.is_absolute() or not location.resolve(strict=False).is_relative_to(root):
            continue
        checked_sources += 1
        if not location.is_file():
            missing_files += 1
            continue
        digest = hashlib.sha256(location.read_bytes()).hexdigest()
        revisions = list(SourceRevision.objects.filter(source=source).values_list('checksum', flat=True))
        checked_revisions += len(revisions)
        checksum_mismatches += sum(value != digest for value in revisions)
    return {'sources_with_local_evidence': checked_sources, 'revisions_checked': checked_revisions,
        'missing_files': missing_files, 'checksum_mismatches': checksum_mismatches,
        'integrity': 'PASS' if missing_files == 0 and checksum_mismatches == 0 else 'FAIL'}


def configured_budget_check():
    batches = list(ArchiveBatch.objects.order_by('received_at'))
    page_to_batch = dict(ArchivePage.objects.values_list('id', 'batch_id'))
    attempts = list(ArchivePageAttempt.objects.order_by('started_at').values(
        'page_id', 'started_at', 'cost', 'currency', 'usage_evidence'))
    grouped = defaultdict(list)
    for attempt in attempts:
        batch_id = page_to_batch.get(attempt['page_id'])
        if batch_id is not None:
            grouped[batch_id].append(attempt)
    batch_results = []
    daily_usage = defaultdict(lambda: {'known_cost': Decimal('0'), 'unknown_cost_attempts': 0})
    quotas = {}
    for batch in batches:
        quota = ConfigVersion.objects.get(id=batch.quota_policy_id)
        quotas[quota.id] = quota
        payload = quota.payload
        rows = grouped[batch.id]
        known = sum((Decimal(row['cost']) for row in rows if row['cost'] is not None), Decimal('0'))
        unknown = sum(row['cost'] is None and row['usage_evidence'] is not None for row in rows)
        batch_results.append({'batch_id': str(batch.id), 'attempts': len(rows),
            'priced_attempts': sum(row['cost'] is not None for row in rows),
            'unpriced_started_attempts': unknown, 'known_cost': str(known), 'currency': payload['currency'],
            'per_batch_budget': payload['per_batch_budget'],
            'known_cost_within_budget': known <= Decimal(payload['per_batch_budget']),
            'status': 'INDETERMINATE' if unknown else 'WITHIN' if known <= Decimal(payload['per_batch_budget']) else 'EXCEEDED'})
        zone = ZoneInfo(payload['budget_timezone'])
        for row in rows:
            day = row['started_at'].astimezone(zone).date().isoformat()
            entry = daily_usage[(quota.id, day)]
            if row['cost'] is None and row['usage_evidence'] is not None:
                entry['unknown_cost_attempts'] += 1
            elif row['cost'] is not None:
                entry['known_cost'] += Decimal(row['cost'])
    daily_results = []
    for (quota_id, day), value in sorted(daily_usage.items(), key=lambda item: (str(item[0][0]), item[0][1])):
        limit = Decimal(quotas[quota_id].payload['daily_budget'])
        daily_results.append({'quota_id': str(quota_id), 'local_date': day, 'known_cost': str(value['known_cost']),
            'unpriced_started_attempts': value['unknown_cost_attempts'], 'daily_budget': str(limit),
            'known_cost_within_budget': value['known_cost'] <= limit,
            'status': 'INDETERMINATE' if value['unknown_cost_attempts'] else 'WITHIN' if value['known_cost'] <= limit else 'EXCEEDED'})
    return {'batch_checks': batch_results, 'daily_checks': daily_results,
        'known_over_budget_count': sum(row['status'] == 'EXCEEDED' for row in batch_results + daily_results),
        'indeterminate_count': sum(row['status'] == 'INDETERMINATE' for row in batch_results + daily_results)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resume-event')
    parser.add_argument('--report', default=str(ROOT / 'docs/testing/T57-simulation-report.json'))
    args = parser.parse_args()
    identity, postgres_version = check_environment()
    if args.resume_event:
        resume_event(args.resume_event)
        return
    actor = PrincipalCredential.objects.get(username='simulation-support').principal
    namespace = f'window4-t57-{uuid4()}'
    event_type = f'{namespace}.recovery_probe'
    event = emit_event(event_type=event_type, schema_version='window4-recovery-probe-1', aggregate_id=uuid4(),
        aggregate_version=1, correlation_id=uuid4(), resource_refs={'simulation_only': True, 'external_send': False})
    first_claim = claim_events(limit=1, owner=f'{namespace}-before-restart', lease_duration_seconds=1,
        event_types=[event_type])
    if len(first_claim) != 1 or first_claim[0].id != event.id:
        raise RuntimeError('无法领取仅供 T57 使用的 Outbox 事件。')
    OutboxEvent.objects.filter(id=event.id).update(lease_until=django_timezone.now() - timedelta(seconds=1))
    recovery_started = time.perf_counter()
    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--resume-event', str(event.id)],
        cwd=ROOT, capture_output=True, text=True, check=True)
    recovery_ms = (time.perf_counter() - recovery_started) * 1000
    recovered = json.loads(child.stdout.strip().splitlines()[-1])
    event.refresh_from_db()
    stale_publish = OutboxEvent.objects.filter(id=event.id, lease_generation=first_claim[0].lease_generation,
        published_at__isnull=True).update(published_at=django_timezone.now())
    current_publish = OutboxEvent.objects.filter(id=event.id, lease_generation=recovered['lease_generation'],
        published_at__isnull=True).update(published_at=django_timezone.now())

    cancelled = register_task(actor=actor, task_type=Task.TaskType.OCR, schema_version='window4-cancel-probe-1',
        input_refs={'simulation_marker': namespace, 'external_send': False})
    cancel_generation = acquire_lease(cancelled.id, f'{namespace}-cancel-worker', django_timezone.now() + timedelta(seconds=30))
    cancel_attempt = create_attempt(cancelled, lease_generation=1)
    first_cancel = cancel_task(cancelled.id)
    duplicate_cancel = cancel_task(cancelled.id)
    cancelled_completion = complete_task(cancelled.id, lease_generation=1, result_type='probe', result_id=uuid4())
    cancelled.refresh_from_db()
    cancel_attempt.refresh_from_db()

    duplicate = register_task(actor=actor, task_type=Task.TaskType.PURPOSE_COPY,
        schema_version='window4-consumer-probe-1', input_refs={'simulation_marker': namespace, 'external_send': False})
    cancel_task(duplicate.id)
    duplicate_event = emit_event(event_type='purpose_copy.requested', schema_version='purpose-copy-2',
        aggregate_id=duplicate.id, aggregate_version=1, correlation_id=duplicate.id,
        resource_refs={'simulation_only': True, 'external_send': False}, task=duplicate)
    consume.run(event_id=str(duplicate_event.id))
    consume.run(event_id=str(duplicate_event.id))
    duplicate.refresh_from_db()
    duplicate_receipts = ConsumerReceipt.objects.filter(consumer='purpose_copy', event=duplicate_event).count()

    leased = register_task(actor=actor, task_type=Task.TaskType.OCR, schema_version='window4-lease-probe-1',
        input_refs={'simulation_marker': namespace, 'external_send': False})
    first_generation = acquire_lease(leased.id, f'{namespace}-worker-1', django_timezone.now() + timedelta(seconds=1))
    time.sleep(1.1)
    expired_reacquire = acquire_lease(leased.id, f'{namespace}-worker-2', django_timezone.now() + timedelta(seconds=30))
    expired_heartbeat = heartbeat(leased.id, lease_generation=1, lease_until=django_timezone.now() + timedelta(seconds=30))
    stale_completion = complete_task(leased.id, lease_generation=1, result_type='probe', result_id=uuid4())
    leased.refresh_from_db()

    running_local = register_task(actor=actor, task_type=Task.TaskType.ASSESSMENT,
        schema_version='window4-local-expiry-probe-1', input_refs={'simulation_marker': namespace, 'external_send': False})
    local_first_acquire = acquire_lease(running_local.id, f'{namespace}-local-worker-1', django_timezone.now() + timedelta(seconds=1))
    local_attempt = create_attempt(running_local, lease_generation=1)
    Task.objects.filter(id=running_local.id).update(lease_until=django_timezone.now() - timedelta(seconds=1))
    local_reacquire = acquire_lease(running_local.id, f'{namespace}-local-worker-2', django_timezone.now() + timedelta(seconds=30))
    running_local.refresh_from_db()
    local_attempt.refresh_from_db()

    running_external = register_task(actor=actor, task_type=Task.TaskType.OCR,
        schema_version='window4-external-expiry-probe-1', input_refs={'simulation_marker': namespace, 'external_send': False})
    external_first_acquire = acquire_lease(running_external.id, f'{namespace}-external-worker-1', django_timezone.now() + timedelta(seconds=1))
    external_attempt = create_attempt(running_external, lease_generation=1)
    Task.objects.filter(id=running_external.id).update(lease_until=django_timezone.now() - timedelta(seconds=1))
    external_reacquire = acquire_lease(running_external.id, f'{namespace}-external-worker-2', django_timezone.now() + timedelta(seconds=30))
    running_external.refresh_from_db()
    external_attempt.refresh_from_db()

    retry = register_task(actor=actor, task_type=Task.TaskType.OCR, schema_version='window4-retry-probe-1',
        input_refs={'simulation_marker': namespace, 'external_send': False})
    retry_generation = acquire_lease(retry.id, f'{namespace}-retry-worker-1', django_timezone.now() + timedelta(seconds=30))
    retry_attempt = create_attempt(retry, lease_generation=1)
    finish_attempt(retry_attempt, TaskAttempt.Status.FAILED, error_code='WINDOW4_SIMULATED_RETRY_PROBE')
    failure_rows = fail_task(retry.id, lease_generation=1, error_code='WINDOW4_SIMULATED_RETRY_PROBE')
    retry_reacquire = acquire_lease(retry.id, f'{namespace}-retry-worker-2', django_timezone.now() + timedelta(seconds=30))
    retry.refresh_from_db()
    retry_attempt.refresh_from_db()

    gate = EnvironmentGate.objects.filter(environment='simulation').first()
    active_configs = ConfigVersion.objects.filter(status='enabled').count()
    integrity = file_integrity()
    reference_integrity = restored_reference_integrity()
    permission_result = restored_permission_check()
    evidence_integrity = restored_configuration_evidence_check()
    runtime_config = ConfigVersion.objects.filter(id=gate.runtime_config_id).first() if gate and gate.runtime_config_id else None
    qualification = configuration_qualification_reason(runtime_config, 'simulation') if runtime_config else 'RUNTIME_CONFIG_NOT_LINKED'
    configured_budgets = configured_budget_check()
    page_attempts = list(ArchivePageAttempt.objects.order_by('started_at').values('status', 'duration_ms', 'cost', 'currency'))
    priced = [row for row in page_attempts if row['cost'] is not None]
    actual_cost = str(sum((row['cost'] for row in priced), start=0))
    budget = {'recorded_page_attempts': len(page_attempts), 'priced_attempts': len(priced),
        'recorded_cost_total': actual_cost, 'currencies': sorted({row['currency'] for row in priced if row['currency']}),
        'configured_budget_checks': configured_budgets,
        'external_calls_by_this_check': 0, 'provider_spend_by_this_check': '0', 'provider_spend_status': 'SIMULATION_ONLY'}
    task_lease_result = {'first_acquire_rows': first_generation, 'expired_reacquire_rows': expired_reacquire,
        'expired_owner_heartbeat_rows': expired_heartbeat, 'expired_owner_completion_rows': stale_completion,
        'status_after_expiry': leased.status,
        'expected_expired_reacquire': 'one_new_generation', 'expected_expired_completion': 0,
        'finding': 'EXPIRED_RUNNING_TASK_NOT_RECLAIMED' if expired_reacquire == 0 else 'RECLAIMED',
        'expired_heartbeat_finding': 'EXPIRED_OWNER_HEARTBEAT_ACCEPTED' if expired_heartbeat == 1 else 'REJECTED',
        'stale_completion_finding': 'EXPIRED_OWNER_COMMITTED' if stale_completion == 1 else 'REJECTED'}
    retry_result = {'first_acquire_rows': retry_generation, 'failure_rows': failure_rows,
        'retry_acquire_rows': retry_reacquire, 'final_status': retry.status,
        'attempt_rows': TaskAttempt.objects.filter(task=retry).count(), 'attempt_status': retry_attempt.status,
        'finding': 'FAILED_TASK_NOT_RETRYABLE_IN_PLACE' if retry_reacquire == 0 else 'RETRY_ACQUIRED'}
    side_effect_recovery = {'local': {'first_acquire_rows': local_first_acquire, 'reacquire_rows': local_reacquire,
        'lease_generation': running_local.lease_generation, 'previous_attempt_status': local_attempt.status,
        'status': running_local.status},
        'external_or_object': {'first_acquire_rows': external_first_acquire, 'reacquire_rows': external_reacquire,
            'lease_generation': running_external.lease_generation, 'previous_attempt_status': external_attempt.status,
            'status': running_external.status}}
    report = {'task': 'T57', 'classification': 'SIMULATION_RECOVERY_CHECK_ONLY',
        'measured_at': datetime.now(timezone.utc).isoformat(),
        'runtime_versions': {'python': sys.version.split()[0], 'django': django.get_version(), 'postgresql': postgres_version},
        'database': {'name': identity[0], 'schema': identity[1], 'search_path': identity[2]},
        'process_restart': {'subprocess_restarted': True, 'event_id': str(event.id),
            'generation_before_restart': first_claim[0].lease_generation, 'generation_after_restart': recovered['lease_generation'],
            'recovery_ms': recovery_ms, 'stale_generation_publish_rejected': stale_publish == 0,
            'current_generation_publish_rows': current_publish, 'event_type_isolated': event_type},
        'task_lease': task_lease_result,
        'cancellation': {'task_id': str(cancelled.id), 'task_type': cancelled.task_type,
            'task_created_at': cancelled.created_at.isoformat(), 'attempt_id': str(cancel_attempt.id),
            'attempt_started_at': cancel_attempt.started_at.isoformat(),
            'provider_request_id_present': bool(cancel_attempt.provider_request_id),
            'provider_request_id': cancel_attempt.provider_request_id,
            'lease_acquire_rows': cancel_generation, 'attempt_status_after_cancel': cancel_attempt.status,
            'first_cancel_rows': first_cancel,
            'duplicate_cancel_rows': duplicate_cancel, 'cancelled_owner_completion_rows': cancelled_completion,
            'final_status': cancelled.status, 'duplicate_cancellation_noop': duplicate_cancel == 0,
            'cancelled_owner_completion_rejected': cancelled_completion == 0},
        'duplicate_consumption': {'actual_worker_entry_called_twice': True, 'consumer_receipts': duplicate_receipts,
            'task_terminal_status': duplicate.status, 'terminal_task_skipped_business_effect': duplicate.status == 'cancelled',
            'duplicate_receipt_suppressed': duplicate_receipts == 1,
            'scope': 'Worker receipt deduplication for a terminal simulated task; active purpose-copy execution is configuration-gated.'},
        'retry': retry_result,
        'expired_attempt_recovery': side_effect_recovery,
        'data_integrity': integrity,
        'permissions': {'simulated_family_api_authorization': 'MEASURED_IN_T31_REPORT',
            'restored_permission_replay': permission_result},
        'configuration': {'simulation_gate_enabled': bool(gate and gate.enabled), 'enabled_configuration_count': active_configs,
            'runtime_config_linked': runtime_config is not None,
            'runtime_config': {'id': str(runtime_config.id), 'kind': runtime_config.kind,
                'schema_version': runtime_config.schema_version, 'status': runtime_config.status,
                'checksum': runtime_config.checksum} if runtime_config else None,
            'runtime_qualification': 'QUALIFIED' if qualification is None else qualification,
            'restore_configuration_replay': 'MEASURED', 'restored_evidence_integrity': evidence_integrity},
        'result_and_reference_integrity': reference_integrity,
        'budget_and_cost': budget,
        'external_send_count': 0, 'restored_environment_external_sends': 0,
        'RTO_RPO_target': 'NOT_PROVIDED', 'real_backup_restore': 'NOT_RUN', 'real_capacity': 'NOT_RUN',
        'saved_and_reread': True}

    with transaction.atomic():
        ConsumerReceipt.objects.filter(event=duplicate_event).delete()
        OutboxEvent.objects.filter(id__in=[event.id, duplicate_event.id]).delete()
        TaskAttempt.objects.filter(task_id__in=[cancelled.id, duplicate.id, leased.id, running_local.id,
            running_external.id, retry.id]).delete()
        Task.objects.filter(id__in=[cancelled.id, duplicate.id, leased.id, running_local.id,
            running_external.id, retry.id]).delete()
    report['recovery_probe_records_removed_after_readback'] = True
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    reread = json.loads(path.read_text())
    if reread != report:
        raise RuntimeError('T57 结果报告保存后回读不一致。')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
