"""Native PostgreSQL T26 recovery proof, restricted to new disposable fixtures.

Run only under the central synthetic services lock, never alongside another run.
Default is plan-only. Every dump, restore, row mutation and object deletion on the
positive path is real. No demo DB, provider, credential file or production policy
is accessed. All created databases/directories and captured receipts are retained.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend'
ARTIFACT_PARENT = Path('/workspace/shared/sjia-test-snapshots')
SOURCE_PORT = 5477


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def load(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, default=str)
        stream.flush()
        os.fsync(stream.fileno())


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def install_network_guard(ports):
    """Actual in-process outbound denial, independent of provider test doubles."""
    ports = set(ports)
    def guard(event, arguments):
        if event == 'socket.connect':
            address = arguments[1]
            if not isinstance(address, tuple) or address[0] != '127.0.0.1' or address[1] not in ports:
                raise RuntimeError('SYNTHETIC_RUN_OUTBOUND_CONNECTION_REFUSED')
        elif event == 'socket.getaddrinfo':
            if arguments[0] not in ('127.0.0.1', b'127.0.0.1', None):
                raise RuntimeError('SYNTHETIC_RUN_EXTERNAL_DNS_REFUSED')
    sys.addaudithook(guard)


def clean_environment(context, *, restored=False):
    # No inherited service/password files, provider secrets or .env loading.
    env = {key: os.environ[key] for key in ('PATH', 'LD_LIBRARY_PATH', 'LANG', 'LC_ALL') if key in os.environ}
    env.update(PYTHONPATH=os.pathsep.join((str(BACKEND), str(ROOT))),
        PYTHON_DOTENV_DISABLED='1', PYTHONUNBUFFERED='1', DJANGO_SETTINGS_MODULE='config.settings.development',
        SJIA_ENVIRONMENT=context['environment'], SJIA_DB_HOST='127.0.0.1',
        SJIA_DB_PORT=str(context['restore_port'] if restored else SOURCE_PORT),
        SJIA_DB_NAME=context['target_database'] if restored else context['source_database'],
        SJIA_DB_USER='sjia', SJIA_DB_PASSWORD='', SJIA_DB_CONN_MAX_AGE='0',
        SJIA_SECRET_KEY='synthetic-t26-only-not-a-production-credential',
        SJIA_ALLOWED_HOSTS='testserver,127.0.0.1', SJIA_DEBUG='0',
        SJIA_REDIS_URL='redis://127.0.0.1:6477/0', SJIA_CELERY_BROKER_URL='redis://127.0.0.1:6477/0',
        SJIA_ARK_API_KEY='', SJIA_OCR_API_KEY='', SJIA_OTEL_EXPORT_ENABLED='0',
        PGPASSFILE='/dev/null', PGSERVICEFILE=context['service_file'], PGCONNECT_TIMEOUT='5')
    if restored:
        state = load(Path(context['run_root']) / 'source-state.json')
        env.update(SJIA_FILE_OBJECT_ROOT=str(Path(context['restore_root']) / 'objects'),
            SJIA_CONFIG_EVIDENCE_ROOT=str(Path(context['restore_root']) / 'config-evidence'),
            SJIA_CONFIG_EVIDENCE_MAX_BYTES=str(1024 * 1024),
            SJIA_CONTENT_JOURNAL_ROOT=state['journal_root'], SJIA_CONTENT_CHECKPOINT_PATH=state['checkpoint_path'],
            SJIA_CONTENT_DISPOSITION_ENABLED='0', SJIA_RESTORE_ROOT=context['restore_root'])
    else:
        env['SJIA_FILE_OBJECT_ROOT'] = str(Path(context['run_root']) / 'bootstrap-objects')
        env['SJIA_CONTENT_DISPOSITION_ENABLED'] = '0'
    return env


def native(command, context, label, *, restored=False):
    root = Path(context['run_root'])
    with (root / (label + '.log')).open('xb') as stream:
        result = subprocess.run(command, env=clean_environment(context, restored=restored),
            stdout=stream, stderr=subprocess.STDOUT, timeout=300, check=False)
        stream.flush()
        os.fsync(stream.fileno())
    with (root / 'commands.jsonl').open('a') as stream:
        stream.write(json.dumps({'command': command, 'exit_code': result.returncode,
            'log': label + '.log', 'at': datetime.now(timezone.utc).isoformat()}) + '\n')
    require(result.returncode == 0, 'NATIVE_COMMAND_FAILED:' + label)


def postgres_identity(port, database):
    import psycopg
    with psycopg.connect(host='127.0.0.1', port=port, user='sjia', password='', dbname=database,
            passfile='/dev/null', connect_timeout=5) as conn:
        row = conn.execute('SELECT current_database(), system_identifier::text FROM pg_control_system()').fetchone()
    require(row[0] == database, 'SYNTHETIC_DATABASE_IDENTITY_MISMATCH')
    return row[1]


def django_start(context, *, restored=False):
    require(os.environ.get('PYTHON_DOTENV_DISABLED') == '1', 'DOTENV_MUST_BE_DISABLED')
    expected = context['target_database'] if restored else context['source_database']
    require(expected.startswith('test_t26_'), 'DISPOSABLE_TEST_DATABASE_REQUIRED')
    require(os.environ.get('SJIA_DB_NAME') == expected, 'EXACT_TEST_DATABASE_REQUIRED')
    require(os.environ.get('SJIA_DB_HOST') == '127.0.0.1', 'LOOPBACK_ONLY')
    require(os.environ.get('SJIA_DB_PASSWORD') == '', 'PASSWORDLESS_SYNTHETIC_CLUSTER_ONLY')
    sys.path.insert(0, str(BACKEND))
    sys.path.insert(0, str(ROOT))
    import django
    django.setup()
    from django.db import connection
    require(connection.settings_dict['NAME'] == expected, 'DJANGO_TEST_DATABASE_MISMATCH')
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), system_identifier::text FROM pg_control_system()')
        database, cluster = cursor.fetchone()
    require(database == expected, 'ACTUAL_TEST_DATABASE_MISMATCH')
    return cluster


def source_phase(context):
    save(Path(context['run_root']) / 'source-phase-started.json', {'database': context['source_database']})
    install_network_guard([SOURCE_PORT, 6477])
    cluster = django_start(context)
    root = Path(context['run_root'])
    from django.core.management import call_command
    from django.db import connection
    from django.test import override_settings
    from django.conf import settings
    from tests.test_content_disposition_engine import ContentDispositionNativeTests, CANARY, OTHER
    from retention.content_journal import configured_journal
    from retention.content_registry import REGISTRY_SHA256, PRESERVATION_SHA256
    from records.queries import record_version_read
    from files.deletion import prepare_local_deletion
    from deploy import recovery

    # This uniquely created DB has no application tables before real migrations.
    call_command('migrate', interactive=False, verbosity=1)
    fixture_parent = root / 'fixture-files'
    fixture_parent.mkdir()
    tempfile.tempdir = str(fixture_parent)

    class RetainedFixture(ContentDispositionNativeTests):
        def addCleanup(self, function, *args, **kwargs):
            owner = getattr(function, '__self__', None)
            if isinstance(owner, tempfile.TemporaryDirectory):
                # Evidence retention only. The data/adapters/assertions are not
                # mocked; detach cleanup of exclusively new synthetic artifacts.
                owner._finalizer.detach()
            super().addCleanup(function, *args, **kwargs)

    fixture = RetainedFixture(methodName='test_native_chain_canaries_real_bytes_erased_and_relationships_preserved')
    fixture.setUp()
    fixture.add_all_case_domains()
    # Separate object and evidence trees, as required by the real backup protocol.
    source_objects = root / 'source-objects'
    shutil.move(str(fixture.store.root), source_objects)
    fixture.store.root = source_objects
    source_override = override_settings(FILE_OBJECT_ROOT=source_objects)
    source_override.enable()
    before = record_version_read(patient=fixture.patient, record_version_id=fixture.first.id)
    require(before['fields'][0]['raw_text'] == CANARY + ':raw', 'SOURCE_CLINICAL_CANARY_MISSING')
    plan = fixture.approve(fixture.preview())
    envelope = fixture.envelope(plan)
    checkpoint = configured_journal().verify()['checkpoint']
    require(checkpoint['sequence'] == 0, 'BACKUP_MUST_PRECEDE_ERASURE')
    local_items = [item for item in plan.manifest['items'] if item['kind'] == 'local_object']
    # Exercise a genuine prepared-but-not-yet-unlinked local-object journal.
    prepared = local_items[0]
    prepare_local_deletion(fixture.store, operation_id=prepared['operation_id'],
        object_key=prepared['object_key'], checksum=prepared['checksum'], size_bytes=prepared['size_bytes'])
    backup = root / 'backup'
    backup.mkdir()
    connection.close()
    native(['pg_dump', '--format=custom', '--no-owner', '--no-acl', '--no-password',
        '--host', '127.0.0.1', '--port', str(SOURCE_PORT), '--username', 'sjia',
        '--dbname', context['source_database'], '--file', str(backup / 'database.dump')], context, 'pg-dump')
    object_inventory = recovery.copy_verified(source_objects, backup / 'objects')
    evidence_inventory = recovery.copy_verified(fixture.evidence.root, backup / 'config-evidence')
    import psycopg
    with psycopg.connect(host='127.0.0.1', port=SOURCE_PORT, user='sjia', password='',
            dbname=context['source_database'], passfile='/dev/null') as conn:
        refs = recovery.object_references(conn)
    recovery.validate_references(refs, object_inventory)
    manifest = {'format': recovery.FORMAT, 'state': 'COMPLETE',
        'source_database': context['source_database'], 'source_cluster_id': cluster,
        'environment': context['environment'], 'approval_reference': 'synthetic-native-backup',
        'operator_reference': 'synthetic-isolated-runner', 'release_reference': 'synthetic-frozen-source',
        'frozen_at': datetime.now(timezone.utc).isoformat(),
        'capture_method': 'native_pg_dump_owned_single_writer_fixture',
        'database_sha256': recovery.sha256(backup / 'database.dump'),
        'objects': object_inventory, 'config_evidence': evidence_inventory, 'object_references': refs,
        'content_checkpoint': checkpoint, 'automatic_restart': False,
        'clinical_or_capacity_acceptance': False}
    recovery.write_json(backup / 'manifest.json', manifest)
    recovery.write_json(backup / 'COMPLETE.json', {'manifest_sha256': recovery.sha256(backup / 'manifest.json')})
    recovery.verify_backup(backup)
    state = {'schema_version': 'synthetic-native-content-restore-fixture-1',
        'envelope': envelope, 'patient_id': str(fixture.patient.id), 'actor_id': str(fixture.actor.id),
        'first_version_id': str(fixture.first.id), 'field_id': str(fixture.field.id),
        'summary_id': str(fixture.summary.id), 'summary_task_id': str(fixture.summary_task.id),
        'attempt_id': str(fixture.attempt.id), 'outbox_id': str(fixture.outbox.id),
        'intent_id': str(fixture.intent.id), 'request_id': str(fixture.request.id),
        'other_time_id': str(fixture.other_time.id), 'other_canary': OTHER + ':other_patient_time',
        'journal_root': str(settings.SJIA_CONTENT_JOURNAL_ROOT),
        'checkpoint_path': str(settings.SJIA_CONTENT_CHECKPOINT_PATH),
        'source_objects': str(source_objects), 'source_evidence': str(fixture.evidence.root),
        'source_cluster_id': cluster, 'local_items': local_items,
        'prepared_operation_id': prepared['operation_id'],
        'intent_args': {key: str(value.pk) if hasattr(value, 'pk') else str(value) if isinstance(value, UUID) else value
            for key, value in fixture.intent_args.items()},
        'registry_sha256': REGISTRY_SHA256, 'preservation_sha256': PRESERVATION_SHA256}
    save(root / 'source-state.json', state)
    result = fixture.execute(plan)
    fixture.assert_payload_erased(result)
    require(result.status == 'logical_erasure_complete', 'SOURCE_ERASURE_NOT_COMPLETE')
    require(configured_journal().verify()['checkpoint']['sequence'] == 1, 'LATEST_JOURNAL_NOT_DURABLE')
    save(root / 'source-erasure-report.json', {'status': 'PASS', 'mode': 'actual_orm_and_local_object_erasure',
        'native_backup_sha256': recovery.sha256(backup / 'database.dump'),
        'backup_checkpoint': checkpoint, 'latest_checkpoint': configured_journal().verify()['checkpoint'],
        'receipt': result.receipt, 'item_count': len(result.manifest['items']),
        'adapter_count': len({item['adapter'] for item in result.manifest['items'] if item['kind'] == 'database'}),
        'source_receipts': recovery.inventory(source_objects), 'all_physical_copies_erased': False})
    connection.close()


def restore_document(context, cluster):
    return {'operation': 'restore', 'operator_approved': True, 'approval_reference': 'synthetic-native-restore',
        'operator_reference': 'synthetic-isolated-runner', 'environment': context['environment'],
        'database': context['target_database'], 'database_service': 't26_target', 'cluster_id': cluster,
        'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), 'timeout_seconds': 300,
        'source': str(Path(context['run_root']) / 'backup'), 'destination': context['restore_root'],
        **{field: True for field in ('destination_protected', 'scope_and_retention_approved', 'isolated_cluster',
            'outbound_blocked', 'application_and_workers_stopped', 'empty_target_approved', 'backup_trusted_and_reviewed')}}


def expect_blocked(function, code, *, allowed=('CONTENT_JOURNAL_DOCUMENT_UNAVAILABLE',)):
    try:
        function()
    except Exception as error:
        reason = getattr(error, 'reason_code', type(error).__name__)
        require(reason in allowed, 'UNEXPECTED_REFUSAL:' + reason)
        return reason
    raise AssertionError(code)


def restored_phase(context):
    save(Path(context['run_root']) / 'restored-phase-started.json', {'database': context['target_database']})
    install_network_guard([context['restore_port'], 6477])
    cluster = django_start(context, restored=True)
    root = Path(context['run_root'])
    state = load(root / 'source-state.json')
    require(cluster != state['source_cluster_id'], 'RESTORE_REQUIRES_DISTINCT_PHYSICAL_CLUSTER')
    from django.conf import settings
    from django.db import IntegrityError, connection, transaction
    from django.utils import timezone as django_timezone
    from config.runtime_admission import admit_configured_process
    from retention.content_recovery import configured_restore_root, assert_configured_restore_admission
    from retention.content_journal import checksum
    from retention.content_engine import verify_manifest
    from retention.content_registry import adapter, patient_rows, row_values
    from retention.erasure_models import ContentDisposed
    from retention.models import ContentDispositionPlan, RetentionPolicy
    from records.models import RecordField, RecordVersion
    from records.queries import record_version_read
    from files.service import object_store
    from files.deletion import deletion_keys, read_deletion_receipt
    from identity.models import Patient, Principal
    from medical_time.models import MedicalTime
    from requests.models import WriteIntent
    from requests.service import prepare_intent
    from tasks.models import Task, TaskAttempt
    from tasks.service import complete_task, fail_task, finish_attempt, heartbeat
    from tasks.worker import consume
    from deploy import recovery
    from tests.test_content_disposition_engine import CANARY

    require(settings.SJIA_CONTENT_DISPOSITION_ENABLED is False, 'REPLAY_MUST_WORK_WITH_LIVE_ENGINE_DISABLED')
    require(RecordField.all_objects.get(id=state['field_id']).raw_text == CANARY + ':raw', 'STALE_BACKUP_PAYLOAD_NOT_RESTORED')
    require(CANARY in json.dumps(Task.all_objects.get(id=state['summary_task_id']).input_refs), 'STALE_TASK_SNAPSHOT_NOT_RESTORED')
    require(all(object_store.exists(item['object_key']) for item in state['local_items']), 'STALE_OBJECTS_NOT_RESTORED')
    blocks_before = {kind: expect_blocked(lambda kind=kind: admit_configured_process(kind), 'MISSING_REPLAY_ADMITTED')
        for kind in ('api', 'worker', 'beat')}
    worker_before = expect_blocked(lambda: consume.run(event_id=state['outbox_id']), 'OLD_TASK_RAN_BEFORE_REPLAY')
    require(configured_restore_root() == Path(context['restore_root']), 'DATABASE_RESTORE_MARKER_NOT_CONSUMED')
    document = restore_document(context, cluster)
    document['content_journal'] = {'root': state['journal_root'], 'checkpoint_path': state['checkpoint_path']}
    proof = recovery.replay_content({**document, 'operation': 'replay-content'}, Path(context['restore_root']))
    require(proof['checkpoint']['sequence'] == 1, 'LATEST_CHECKPOINT_NOT_REPLAYED')
    require(verify_manifest(state['envelope']) is True, 'REAL_ADAPTER_REPLAY_VERIFICATION_FAILED')
    for item in state['envelope']['manifest']['items']:
        if item['kind'] == 'database':
            model, spec = adapter(item['adapter'])
            row = model.all_objects.get(pk=item['object_id'])
            require(all(getattr(row, field) is None for field in spec['payload_fields']), 'RESTORED_PAYLOAD_PRESENT')
    require(CANARY not in json.dumps([row_values(row) for row in patient_rows(state['patient_id'])], default=str),
        'RESTORED_PATIENT_CANARY_PRESENT')
    require(MedicalTime.objects.get(id=state['other_time_id']).raw_text == state['other_canary'], 'OTHER_PATIENT_CHANGED')
    marker = load(Path(context['restore_root']) / 'ADOPTION_BLOCKED.json')
    restore_id = marker['target']['restore_id']
    from uuid import uuid5
    restored_receipts = []
    for item in state['local_items']:
        operation_id = uuid5(UUID(item['operation_id']), 'restore:' + restore_id)
        key, data, receipt = read_deletion_receipt(object_store, operation_id=operation_id,
            object_key=item['object_key'], checksum=item['checksum'], size_bytes=item['size_bytes'])
        restored_receipts.append({'receipt_key': key, 'receipt_sha256': hashlib.sha256(data).hexdigest(),
            'outcome': receipt.outcome})
    old_journal, _ = deletion_keys(state['prepared_operation_id'])
    require(digest_file(Path(context['restore_root']) / 'objects' / old_journal)
        == digest_file(root / 'backup/objects' / old_journal), 'ORIGINAL_LOCAL_JOURNAL_WAS_REWRITTEN')
    blocks_after_replay = {kind: expect_blocked(lambda kind=kind: admit_configured_process(kind),
        'CONTENT_PROOF_BYPASSED_SEPARATE_RELEASE') for kind in ('api', 'worker', 'beat')}
    release = {**document, 'operation': 'admit-restored-runtime', 'target': marker['target'],
        'content_proof_sha256': checksum(proof), 'approval_reference': 'synthetic-explicit-runtime-release',
        **{field: True for field in ('revocations_replayed', 'disabled_configuration_reconciled',
            'external_effects_reconciled', 'permissions_revalidated', 'release_qualified',
            'isolated_content_validation_complete')}}
    incomplete_release = expect_blocked(lambda: recovery.admit_restored_runtime(
        {**release, 'external_effects_reconciled': False}, Path(context['restore_root'])), 'INCOMPLETE_RELEASE_ADMITTED',
        allowed=('RESTORE_SEPARATE_RELEASE_APPROVAL_REQUIRED',))
    admitted = recovery.admit_restored_runtime(release, Path(context['restore_root']))
    for kind in ('api', 'worker', 'beat'):
        admit_configured_process(kind)
    assert_configured_restore_admission()
    actor = Principal.objects.get(id=state['actor_id'])
    patient = Patient.objects.get(id=state['patient_id'])
    args = dict(state['intent_args'])
    args.update(actor=actor, patient=patient, request_id=UUID(args['request_id']),
        retention_policy=RetentionPolicy.objects.get(id=args['retention_policy']))
    before_intents = WriteIntent.all_objects.count()
    try:
        prepare_intent(**args)
    except ContentDisposed:
        pass
    else:
        raise AssertionError('OLD_REQUEST_RECONSTRUCTED_CONTENT')
    require(WriteIntent.all_objects.count() == before_intents, 'OLD_REQUEST_CREATED_NEW_IDENTITY')
    attempt = TaskAttempt.all_objects.get(id=state['attempt_id'])
    for terminal in ('completed', 'failed'):
        try:
            finish_attempt(attempt, terminal, usage_evidence={'late': CANARY})
        except ContentDisposed:
            pass
        else:
            raise AssertionError('LATE_ATTEMPT_RECONSTRUCTED_CONTENT')
    require(complete_task(state['summary_task_id'], 1, 'summary', state['summary_id']) == 0, 'STALE_TASK_COMPLETED')
    require(fail_task(state['summary_task_id'], 1, 'SYNTHETIC_LATE_FAILURE') == 0, 'STALE_TASK_FAILED_WRITE')
    require(heartbeat(state['summary_task_id'], 1, django_timezone.now() + timedelta(minutes=1)) == 0,
        'STALE_TASK_LEASE_REVIVED')
    # A filtered disposed outbox identity is deliberately an idempotent no-op.
    old_task = consume.run(event_id=state['outbox_id'])
    require(old_task is None, 'OLD_OUTBOX_REPLAY_PRODUCED_RESULT')
    require(Task.all_objects.get(id=state['summary_task_id']).input_refs is None, 'OLD_TASK_RESTORED_INPUT')
    require(TaskAttempt.all_objects.get(id=state['attempt_id']).usage_evidence is None, 'OLD_TASK_RESTORED_EVIDENCE')
    try:
        with transaction.atomic():
            RecordField.all_objects.filter(id=state['field_id']).update(raw_text=CANARY + ':resurrected')
    except IntegrityError:
        pass
    else:
        raise AssertionError('DATABASE_TRIGGER_ALLOWED_RESURRECTION')
    try:
        record_version_read(patient=patient, record_version_id=state['first_version_id'])
    except ContentDisposed:
        pass
    else:
        raise AssertionError('READ_EXPOSED_ERASED_CONTENT')
    require(verify_manifest(state['envelope']) is True, 'LATE_REPLAY_CHANGED_ERASURE')
    source_receipts_before = load(root / 'source-erasure-report.json')['source_receipts']
    require(recovery.inventory(Path(state['source_objects'])) == source_receipts_before, 'SOURCE_RECEIPTS_CHANGED')
    save(root / 'restored-verification-report.json', {'status': 'PASS',
        'actual_native_stale_backup_restored': True, 'actual_controlled_adapters_replayed': True,
        'live_engine_disabled_during_replay': True, 'backup_checkpoint_sequence': 0,
        'replayed_checkpoint': proof['checkpoint'], 'database_cluster_distinct': True,
        'startup_refusals_before_replay': blocks_before, 'worker_refusal_before_replay': worker_before,
        'startup_refusals_pending_release': blocks_after_replay, 'incomplete_release_refused': incomplete_release,
        'explicit_release_state': admitted['state'], 'old_outbox_replay_outcome': 'disposed_event_no_op',
        'old_request_and_late_attempt_blocked': True, 'database_resurrection_trigger_blocked': True,
        'unrelated_patient_preserved': True, 'original_receipts_preserved': True,
        'restored_local_receipts': restored_receipts,
        'unsupported_scope': ['physical_database_pages', 'WAL', 'replicas', 'storage_snapshots',
            'real_provider_copies', 'delivered_exports', 'forensic_media_erasure'],
        'all_physical_copies_erased': False})
    connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path)
    parser.add_argument('--restore-port', type=int, default=5478)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--phase', choices=('source', 'restored'), help=argparse.SUPPRESS)
    parser.add_argument('--context', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.phase:
        require(args.context is not None, 'RUN_CONTEXT_REQUIRED')
        context = load(args.context)
        root = Path(context['run_root']).resolve()
        require(root.is_relative_to(ARTIFACT_PARENT) and root.name.startswith('t26-native-restore-'), 'SYNTHETIC_RUN_ROOT_REQUIRED')
        require(args.context.resolve() == root / 'run-context.json', 'EXACT_RUN_CONTEXT_REQUIRED')
        (source_phase if args.phase == 'source' else restored_phase)(context)
        return
    if not args.execute:
        print('PLAN_ONLY; central runner lock required; no database, service or file changed')
        return
    require(args.run_root is not None, 'NEW_SYNTHETIC_RUN_ROOT_REQUIRED')
    root = args.run_root.absolute()
    require(not root.exists() and not any(path.is_symlink() for path in (root, *root.parents)), 'RUN_ROOT_MUST_BE_NEW')
    root = root.resolve()
    require(root.is_relative_to(ARTIFACT_PARENT) and root.name.startswith('t26-native-restore-'), 'SYNTHETIC_RUN_ROOT_REQUIRED')
    require(os.environ.get('SJIA_DB_PORT') == str(SOURCE_PORT) and os.environ.get('SJIA_DB_HOST') == '127.0.0.1'
        and os.environ.get('SJIA_DB_NAME') == 'sjia_extension_tests', 'CENTRAL_SYNTHETIC_WRAPPER_REQUIRED')
    require(os.environ.get('SJIA_DB_USER') == 'sjia' and os.environ.get('SJIA_DB_PASSWORD', '') == '',
        'PASSWORDLESS_SYNTHETIC_CLUSTER_REQUIRED')
    require(1024 < args.restore_port < 65536 and args.restore_port not in (SOURCE_PORT, 6477), 'DISTINCT_RESTORE_PORT_REQUIRED')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.restore_port))
    for binary in ('initdb', 'pg_ctl', 'pg_dump', 'pg_restore', 'createdb'):
        require(shutil.which(binary) is not None, 'NATIVE_POSTGRES_BINARY_REQUIRED:' + binary)
    root.mkdir(parents=True, exist_ok=False)
    require(not any(root.iterdir()), 'NEW_EMPTY_RUN_ROOT_REQUIRED')
    suffix = uuid4().hex[:16]
    context = {'schema_version': 'synthetic-native-restore-run-1', 'run_root': str(root),
        'source_database': 'test_t26_source_' + suffix, 'target_database': 'test_t26_restored_' + suffix,
        'environment': 'test-t26-native-' + suffix, 'restore_root': str(root / 'restored'),
        'restore_port': args.restore_port, 'service_file': str(root / 'synthetic-services.conf')}
    save(root / 'run-context.json', context)
    Path(context['service_file']).write_text('[t26_target]\nhost=127.0.0.1\nport=' + str(args.restore_port)
        + '\nuser=sjia\ndbname=' + context['target_database'] + '\nconnect_timeout=5\n')
    install_network_guard([SOURCE_PORT, args.restore_port, 6477])
    os.environ['PGPASSFILE'] = '/dev/null'
    os.environ['PGSERVICEFILE'] = context['service_file']
    source_cluster = postgres_identity(SOURCE_PORT, 'postgres')
    native(['createdb', '--no-password', '--host', '127.0.0.1', '--port', str(SOURCE_PORT),
        '--username', 'sjia', context['source_database']], context, 'create-source-db')
    native([sys.executable, str(Path(__file__).resolve()), '--phase', 'source', '--context',
        str(root / 'run-context.json')], context, 'source-fixture-and-erasure')
    target_data = root / 'target-pgdata'
    binary_dir = Path(shutil.which('initdb')).resolve().parent
    share = binary_dir.parents[3] / 'share/postgresql' / binary_dir.parent.name
    require(share.is_dir(), 'POSTGRES_SHARED_DATA_REQUIRED')
    native(['initdb', '-D', str(target_data), '-U', 'sjia', '--auth-local=trust', '--auth-host=trust',
        '--encoding=UTF8', '--locale=C.UTF-8', '-L', str(share)], context, 'init-target-cluster')
    started = False
    try:
        # Recheck immediately before start; a raced occupied port is never seized.
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', args.restore_port))
        native(['pg_ctl', '-D', str(target_data), '-l', str(root / 'target-postgres.log'), '-o',
            f"-h 127.0.0.1 -p {args.restore_port} -c unix_socket_directories=''", '-w', 'start'],
            context, 'start-target-cluster')
        started = True
        native(['createdb', '--no-password', '--host', '127.0.0.1', '--port', str(args.restore_port),
            '--username', 'sjia', context['target_database']], context, 'create-target-db')
        target_cluster = postgres_identity(args.restore_port, context['target_database'])
        require(source_cluster != target_cluster, 'DISTINCT_PHYSICAL_CLUSTER_REQUIRED')
        sys.path.insert(0, str(ROOT))
        from deploy import recovery
        document = restore_document(context, target_cluster)
        save(root / 'synthetic-restore-approval.json', document)
        # Decisive restore path: actual production recovery function and real
        # psycopg/pg_restore. No connector, command runner or callback is replaced.
        restored = recovery.restore(document, root / 'backup', Path(context['restore_root']))
        require(restored['state'] == 'RESTORED_ADOPTION_BLOCKED', 'RESTORE_UNEXPECTEDLY_ADMITTED')
        native([sys.executable, str(Path(__file__).resolve()), '--phase', 'restored', '--context',
            str(root / 'run-context.json')], context, 'restored-replay-and-admission', restored=True)
        report = load(root / 'restored-verification-report.json')
        final_report = {**report, 'run_root': str(root),
            'source_database': context['source_database'], 'target_database': context['target_database'],
            'source_cluster_id': source_cluster, 'target_cluster_id': target_cluster,
            'database_dump_sha256': digest_file(root / 'backup/database.dump'),
            'retained_artifacts': ['backup', 'fixture-files', 'source-objects', 'restored', 'target-pgdata',
                'source-state.json', 'source-erasure-report.json', 'commands.jsonl'],
            'source_database_retained': True, 'target_database_retained': True}
    finally:
        if started or (target_data / 'postmaster.pid').is_file():
            native(['pg_ctl', '-D', str(target_data), '-m', 'fast', '-w', 'stop'], context, 'stop-target-cluster')
    save(root / 'report.json', {**final_report, 'target_cluster_stopped': True})
    print(json.dumps({'status': 'PASS', 'report': str(root / 'report.json')}))


if __name__ == '__main__':
    main()
