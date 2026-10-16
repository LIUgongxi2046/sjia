"""Operator-only consistent backup and empty isolated restore.

Default is a write-free plan. --execute plus an exact admitted operation is required
for side effects. No service restart, destructive database cleanup, credential entry,
network/security configuration or external delivery is implemented here.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
WRITERS = ('nginx', 'api', 'beat', 'worker', 'worker-purpose', 'worker-assessment', 'worker-notification', 'migrate')
SAFE_NAME = re.compile(r'^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,79}$')
FORMAT = 'sjia-consistent-backup-1'


class RecoveryRejected(RuntimeError):
    """Fixed error codes only. Never print DB/tool errors or patient filenames."""


def reject(code):
    raise RecoveryRejected(code)


def name(value):
    if not isinstance(value, str) or not SAFE_NAME.fullmatch(value):
        reject('INVALID_ADMISSION_IDENTIFIER')
    return value


def admission(document, operation):
    if document.get('operation') != operation or document.get('operator_approved') is not True:
        reject('EXACT_OPERATION_APPROVAL_REQUIRED')
    for field in ('approval_reference', 'operator_reference', 'environment', 'database', 'database_service', 'cluster_id'):
        name(document.get(field))
    for field in ('destination_protected', 'scope_and_retention_approved'):
        if document.get(field) is not True:
            reject('BACKUP_DESTINATION_AND_RETENTION_ADMISSION_REQUIRED')
    deadline = document.get('expires_at')
    try:
        expiry = datetime.fromisoformat(deadline)
        valid = expiry.tzinfo is not None and expiry > datetime.now(timezone.utc)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        reject('ADMISSION_EXPIRED_OR_INVALID')
    timeout = document.get('timeout_seconds')
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 86400:
        reject('APPROVED_TIMEOUT_REQUIRED')
    if operation == 'backup':
        name(document.get('compose_project'))
        name(document.get('release_reference'))
        if document.get('exclusive_object_writer_scope_confirmed') is not True:
            reject('EXCLUSIVE_WRITER_SCOPE_REQUIRED')
        if document.get('external_effects_reconciled') is not True:
            reject('EXTERNAL_EFFECT_RECONCILIATION_REQUIRED')
    elif operation in ('restore', 'replay-content', 'admit-restored-runtime'):
        for field in ('isolated_cluster', 'outbound_blocked', 'application_and_workers_stopped',
                'empty_target_approved', 'backup_trusted_and_reviewed'):
            if document.get(field) is not True:
                reject('ISOLATED_RESTORE_ADMISSION_REQUIRED')
    else:
        reject('UNKNOWN_OPERATION')
    return document


def run(command, *, env=None, timeout=300):
    try:
        return subprocess.run(command, env=env, check=True, capture_output=True,
            text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        reject('OPERATOR_COMMAND_FAILED_SERVICES_REMAIN_FROZEN')


def pg_environment(service):
    # libpq service/passfile is provided by the operator. Never read/echo it.
    env = {key: value for key, value in os.environ.items()
        if key not in ('PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD', 'PGOPTIONS')}
    env['PGSERVICE'] = name(service)
    env['PGCONNECT_TIMEOUT'] = '5'
    return env


def compose(document, *arguments):
    return ['docker', 'compose', '--project-name', document['compose_project'],
        '--file', str(ROOT / 'deploy/compose.yaml'), *arguments]


def freeze_writers(document, runner=run):
    runner(compose(document, 'stop', '--timeout', str(document['timeout_seconds']), *WRITERS),
        timeout=document['timeout_seconds'] + 60)
    remaining = runner(compose(document, 'ps', '--status', 'running', '--services'), timeout=30).split()
    # Unknown services can also write or send. Their operator must stop them first.
    if set(remaining) - {'postgres', 'redis'}:
        reject('PROJECT_HAS_RUNNING_WRITERS_OR_UNKNOWN_SERVICES')


@contextmanager
def connection(document):
    import psycopg
    try:
        with psycopg.connect(service=document['database_service'], dbname=document['database'], connect_timeout=5) as conn:
            yield conn
    except RecoveryRejected:
        raise
    except Exception:
        reject('DATABASE_OPERATION_FAILED_SERVICES_REMAIN_FROZEN')


def check_identity(conn, document):
    row = conn.execute('SELECT current_database(), system_identifier::text FROM pg_control_system()').fetchone()
    if row != (document['database'], document['cluster_id']):
        reject('DATABASE_IDENTITY_MISMATCH')
    # No foreign sessions, idle sessions or active writers are admitted.
    count = conn.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
        "AND pid<>pg_backend_pid() AND backend_type='client backend'").fetchone()[0]
    if count:
        reject('DATABASE_HAS_OTHER_CLIENT_SESSIONS')


def database_tables(conn):
    return conn.execute("SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' "
        "AND c.relkind IN ('r','p') ORDER BY n.nspname,c.relname").fetchall()


def lock_writes(conn):
    from psycopg import sql
    conn.execute("SET LOCAL lock_timeout = '5s'")
    tables = database_tables(conn)
    if not tables or not any(schema == 'sjia' for schema, _ in tables):
        reject('APPLICATION_SCHEMA_MISSING')
    for schema, table in tables:
        conn.execute(sql.SQL('LOCK TABLE {}.{} IN SHARE MODE').format(sql.Identifier(schema), sql.Identifier(table)))


def sha256(path):
    result = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def inventory(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        reject('SOURCE_DIRECTORY_INVALID')
    result = []
    for path in sorted(root.rglob('*')):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            reject('UNSAFE_FILE_TYPE')
        if stat.S_ISREG(mode):
            result.append({'path': path.relative_to(root).as_posix(), 'sha256': sha256(path),
                'size_bytes': path.stat().st_size})
    return result


def copy_verified(source, destination):
    before = inventory(source)
    # Copy any raced-in link as a link and reject it; never follow it outside scope.
    shutil.copytree(source, destination, symlinks=True)
    if inventory(source) != before or inventory(destination) != before:
        reject('COPY_CHANGED_OR_CHECKSUM_MISMATCH')
    return before


def validate_references(references, objects):
    by_path = {entry['path']: entry for entry in objects}
    for reference in references:
        key = reference['object_key']
        parts = PurePosixPath(key)
        if not key or parts.is_absolute() or '..' in parts.parts or str(parts) != key:
            reject('INVALID_OBJECT_REFERENCE')
        entry = by_path.get(key)
        # Disposed metadata remains as history; already removed bytes are allowed.
        if entry is None and reference['status'] == 'disposed':
            continue
        if (entry is None or entry['sha256'] != reference['checksum'] or
                entry['size_bytes'] != reference['size_bytes']):
            reject('OBJECT_REFERENCE_INTEGRITY_FAILED')


def object_references(conn):
    rows = conn.execute('SELECT object_key,object_version,checksum,size_bytes,status FROM sjia.file_object '
        'ORDER BY object_key,object_version').fetchall()
    return [dict(zip(('object_key', 'object_version', 'checksum', 'size_bytes', 'status'), row)) for row in rows]


def write_json(path, content):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(content, stream, sort_keys=True, separators=(',', ':'))
        stream.flush()
        os.fsync(stream.fileno())


def distinct_paths(*paths):
    resolved = [Path(value).resolve() for value in paths]
    for i, left in enumerate(resolved):
        for right in resolved[i + 1:]:
            if left.is_relative_to(right) or right.is_relative_to(left):
                reject('SOURCE_DESTINATION_PATHS_OVERLAP')


def content_journal(document, *restorable_roots, required=False):
    """Independent configured store; never copy its checkpoint into the backup."""
    value = document.get('content_journal')
    if value is None and not required:
        return None
    if not isinstance(value, dict) or set(value) != {'root', 'checkpoint_path'}:
        reject('CONTENT_JOURNAL_CONFIGURATION_REQUIRED')
    sys.path.insert(0, str(ROOT / 'backend'))
    from retention.content_journal import ContentJournal
    return ContentJournal(value['root'], value['checkpoint_path'], forbidden_roots=restorable_roots)


def backup(document, destination, objects, evidence, *, runner=run, connector=connection):
    admission(document, 'backup')
    destination, objects, evidence = map(Path, (destination, objects, evidence))
    distinct_paths(destination, objects, evidence)
    for field, path in (('destination', destination), ('object_root', objects), ('evidence_root', evidence)):
        if document.get(field) != str(path.resolve()):
            reject('EXACT_PATH_ADMISSION_REQUIRED')
    if destination.exists():
        reject('DESTINATION_ALREADY_EXISTS')
    if destination.resolve().is_relative_to(ROOT):
        reject('SENSITIVE_BACKUP_DESTINATION_MUST_BE_OUTSIDE_CHECKOUT')
    inventory(objects)
    inventory(evidence)
    started = time.monotonic()
    journal = content_journal(document, destination, objects, evidence)
    if journal:
        journal.verify()
    freeze_writers(document, runner)
    content_checkpoint = journal.verify()['checkpoint'] if journal else None
    # An incomplete destination is deliberately preserved for the operator to inspect.
    destination.mkdir(parents=True, exist_ok=False)
    write_json(destination / 'WRITE_FREEZE.json', {'state': 'WRITE_FROZEN', 'automatic_restart': False})
    with connector(document) as conn:
        check_identity(conn, document)
        lock_writes(conn)
        refs = object_references(conn)
        frozen_at = datetime.now(timezone.utc).isoformat()
        # PostgreSQL ACCESS SHARE dump locks are compatible with our SHARE write freeze.
        runner(['pg_dump', '--format=custom', '--no-owner', '--no-acl', '--lock-wait-timeout=5000',
            '--dbname', 'service=' + document['database_service'] + ' dbname=' + document['database'],
            '--file', str(destination / 'database.dump')], env=pg_environment(document['database_service']),
            timeout=document['timeout_seconds'])
        object_files = copy_verified(objects, destination / 'objects')
        evidence_files = copy_verified(evidence, destination / 'config-evidence')
        validate_references(refs, object_files)
        manifest = {'format': FORMAT, 'state': 'COMPLETE', 'source_database': document['database'],
            'source_cluster_id': document['cluster_id'], 'environment': document['environment'],
            'approval_reference': document['approval_reference'], 'operator_reference': document['operator_reference'],
            'release_reference': document['release_reference'],
            'frozen_at': frozen_at, 'completed_at': datetime.now(timezone.utc).isoformat(),
            'elapsed_seconds': round(time.monotonic() - started, 3),
            'database_sha256': sha256(destination / 'database.dump'), 'objects': object_files,
            'config_evidence': evidence_files, 'object_references': refs,
            'content_checkpoint': content_checkpoint,
            'automatic_restart': False, 'clinical_or_capacity_acceptance': False}
        if journal and journal.verify()['checkpoint'] != content_checkpoint:
            reject('CONTENT_JOURNAL_CHANGED_DURING_BACKUP')
        write_json(destination / 'manifest.json', manifest)
        write_json(destination / 'COMPLETE.json', {'manifest_sha256': sha256(destination / 'manifest.json')})
    # This status marker changes no source state. Services stay stopped even on success.
    return manifest


def verify_backup(directory):
    directory = Path(directory)
    for name_ in ('manifest.json', 'COMPLETE.json', 'database.dump'):
        path = directory / name_
        if path.is_symlink() or not path.is_file():
            reject('BACKUP_NOT_COMPLETE')
    receipt = json.loads((directory / 'COMPLETE.json').read_text())
    if receipt.get('manifest_sha256') != sha256(directory / 'manifest.json'):
        reject('MANIFEST_CHECKSUM_MISMATCH')
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest.get('format') != FORMAT or manifest.get('state') != 'COMPLETE':
        reject('BACKUP_FORMAT_INVALID')
    if (sha256(directory / 'database.dump') != manifest['database_sha256'] or
            inventory(directory / 'objects') != manifest['objects'] or
            inventory(directory / 'config-evidence') != manifest['config_evidence']):
        reject('BACKUP_CHECKSUM_MISMATCH')
    validate_references(manifest['object_references'], manifest['objects'])
    return manifest


def restore(document, source, destination, *, runner=run, connector=connection):
    admission(document, 'restore')
    source, destination = Path(source), Path(destination)
    distinct_paths(source, destination)
    if document.get('source') != str(source.resolve()) or document.get('destination') != str(destination.resolve()):
        reject('EXACT_PATH_ADMISSION_REQUIRED')
    if destination.exists():
        reject('DESTINATION_ALREADY_EXISTS')
    if destination.resolve().is_relative_to(ROOT):
        reject('SENSITIVE_RESTORE_DESTINATION_MUST_BE_OUTSIDE_CHECKOUT')
    manifest = verify_backup(source)
    if (document['cluster_id'] == manifest['source_cluster_id'] or
            document['database'] == manifest['source_database']):
        reject('DISTINCT_ISOLATED_CLUSTER_AND_DATABASE_REQUIRED')
    started = time.monotonic()
    with connector(document) as conn:
        check_identity(conn, document)
        # Only an empty operator-created DB is accepted; no --clean/createdb/drop exists.
        if database_tables(conn):
            reject('RESTORE_DATABASE_NOT_EMPTY')
        namespaces = conn.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT IN "
            "('pg_catalog','information_schema','public') AND nspname NOT LIKE 'pg_toast%' "
            "AND nspname NOT LIKE 'pg_temp_%'").fetchall()
        if namespaces:
            reject('RESTORE_DATABASE_NOT_EMPTY')
        objects = conn.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public'").fetchone()[0]
        functions = conn.execute("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public'").fetchone()[0]
        extensions = conn.execute("SELECT count(*) FROM pg_extension WHERE extname<>'plpgsql'").fetchone()[0]
        if objects or functions or extensions:
            reject('RESTORE_DATABASE_NOT_EMPTY')
    destination.mkdir(parents=True, exist_ok=False)
    target = {'restore_id': str(uuid4()), 'database': document['database'],
        'cluster_id': document['cluster_id'], 'object_root': str((destination / 'objects').resolve())}
    restore_marker = {'state': 'ADOPTION_BLOCKED',
        'target': target, 'backup_manifest_sha256': sha256(source / 'manifest.json'),
        'content_checkpoint': manifest.get('content_checkpoint'),
        'replay_deletion_revocation_disabled_configuration_required': True,
        'external_effect_reconciliation_required': True, 'automatic_restart': False}
    write_json(destination / 'ADOPTION_BLOCKED.json', restore_marker)
    copy_verified(source / 'objects', destination / 'objects')
    copy_verified(source / 'config-evidence', destination / 'config-evidence')
    # Trusted dumps may contain executable SQL. Admission explicitly covers this input.
    runner(['pg_restore', '--single-transaction', '--exit-on-error', '--no-owner', '--no-acl',
        '--dbname', 'service=' + document['database_service'] + ' dbname=' + document['database'], str(source / 'database.dump')],
        env=pg_environment(document['database_service']), timeout=document['timeout_seconds'])
    with connector(document) as conn:
        check_identity(conn, document)
        register_restore_state(conn, restore_marker)
        refs = object_references(conn)
        if refs != manifest['object_references']:
            reject('RESTORED_REFERENCES_DIFFER')
        validate_references(refs, inventory(destination / 'objects'))
    result = {'state': 'RESTORED_ADOPTION_BLOCKED', 'approval_reference': document['approval_reference'],
        'elapsed_seconds': round(time.monotonic() - started, 3), 'source_frozen_at': manifest['frozen_at'],
        'automatic_restart': False, 'outbound_enabled': False, 'rto_rpo_acceptance': 'OPERATOR_REVIEW_REQUIRED'}
    write_json(destination / 'restore-result.json', result)
    if document.get('replay_content') is True:
        replay_result = replay_content({**document, 'operation': 'replay-content'}, destination)
        result['content_replay'] = replay_result['state']
    return result


def register_restore_state(conn, marker):
    # The DB marker is essential: an omitted restore-root mount must not turn a
    # restored DB into a falsely ordinary deployment. Old schemas fail closed.
    if conn.execute("SELECT to_regclass('sjia.content_restore_state')").fetchone()[0] is None:
        reject('RESTORE_STATE_SCHEMA_REQUIRED')
    target = marker['target']
    conn.execute('INSERT INTO sjia.content_restore_state '
        '(id,singleton,database_name,cluster_id,object_root,backup_manifest_sha256,content_checkpoint,created_at) '
        'VALUES (%s,true,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (singleton) DO UPDATE SET '
        'id=EXCLUDED.id,database_name=EXCLUDED.database_name,cluster_id=EXCLUDED.cluster_id,'
        'object_root=EXCLUDED.object_root,backup_manifest_sha256=EXCLUDED.backup_manifest_sha256,'
        'content_checkpoint=EXCLUDED.content_checkpoint,created_at=EXCLUDED.created_at',
        (target['restore_id'], target['database'], target['cluster_id'], target['object_root'],
            marker['backup_manifest_sha256'], json.dumps(marker['content_checkpoint']), datetime.now(timezone.utc)))
    # Persist the safety marker even if later restored-reference validation fails.
    conn.commit()


def restored_callbacks(document, destination):
    """Use the real controlled ORM/local-store adapters against the exact target."""
    if not os.environ.get('DJANGO_SETTINGS_MODULE'):
        reject('RESTORE_EXPLICIT_DJANGO_SETTINGS_REQUIRED')
    sys.path.insert(0, str(ROOT / 'backend'))
    import django
    django.setup()
    from retention.content_recovery import configured_target, configured_restore_root
    from retention.content_engine import replay_manifest, verify_manifest
    if configured_restore_root() != destination:
        reject('RESTORE_RUNTIME_TARGET_MISMATCH')
    target = configured_target(destination)
    if (target['database'] != document['database'] or target['cluster_id'] != document['cluster_id']):
        reject('RESTORE_RUNTIME_TARGET_MISMATCH')
    from retention.content_journal import configured_journal
    from files.service import object_store
    if Path(object_store.root).resolve() != Path(target['object_root']):
        reject('RESTORE_RUNTIME_OBJECT_STORE_MISMATCH')
    configured = configured_journal()
    explicit = content_journal(document, destination, required=True)
    if (configured.root != explicit.root or configured.checkpoint_path != explicit.checkpoint_path):
        reject('RESTORE_RUNTIME_JOURNAL_MISMATCH')
    return target, replay_manifest, verify_manifest


def replay_content(document, destination):
    admission(document, 'replay-content')
    destination = Path(destination).resolve()
    if document.get('destination') != str(destination):
        reject('EXACT_PATH_ADMISSION_REQUIRED')
    journal = content_journal(document, destination, required=True)
    target, replay, verify = restored_callbacks(document, destination)
    from retention.content_recovery import replay_restore
    return replay_restore(journal, destination, target, replay=replay, verify=verify)


def admit_restored_runtime(document, destination):
    admission(document, 'admit-restored-runtime')
    destination = Path(destination).resolve()
    if document.get('destination') != str(destination):
        reject('EXACT_PATH_ADMISSION_REQUIRED')
    journal = content_journal(document, destination, required=True)
    target, _, verify = restored_callbacks(document, destination)
    from retention.content_recovery import admit_restore
    return admit_restore(journal, destination, target, document, verify=verify)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('backup', 'restore', 'verify', 'replay-content', 'admit-restored-runtime'))
    parser.add_argument('--admission', type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--objects', type=Path)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print('PLAN_ONLY; no database access, service stop, backup or restore performed')
        print('backup: admit exact project/database; stop writers; verify identity; lock writes; dump; copy; verify; remain frozen')
        print('restore: admit trusted backup and isolated empty target; verify; copy; restore transaction; verify; remain blocked')
        return
    try:
        if args.operation == 'verify':
            if args.source is None:
                reject('SOURCE_REQUIRED')
            verify_backup(args.source)
            print('BACKUP_INTEGRITY_VERIFIED_ONLY')
            return
        if not args.admission or not args.destination:
            reject('ADMISSION_AND_DESTINATION_REQUIRED')
        document = json.loads(args.admission.read_text())
        if args.operation == 'backup':
            if not args.objects or not args.evidence:
                reject('OBJECT_AND_EVIDENCE_SOURCES_REQUIRED')
            backup(document, args.destination, args.objects, args.evidence)
            print('BACKUP_COMPLETE; SERVICES_REMAIN_FROZEN')
        elif args.operation == 'replay-content':
            replay_content(document, args.destination)
            print('CONTENT_REPLAY_VERIFIED; SEPARATE_RUNTIME_ADMISSION_REQUIRED; SERVICES_NOT_STARTED')
        elif args.operation == 'admit-restored-runtime':
            admit_restored_runtime(document, args.destination)
            print('RESTORE_RUNTIME_ADMISSION_RECORDED; SERVICES_NOT_STARTED')
        else:
            if not args.source:
                reject('SOURCE_REQUIRED')
            restore(document, args.source, args.destination)
            print('RESTORED_ADOPTION_BLOCKED; SERVICES_NOT_STARTED')
    except RecoveryRejected as error:
        parser.exit(2, str(error) + '\n')
    except Exception:
        parser.exit(2, 'RECOVERY_FAILED; NO_AUTOMATIC_RESTART; OPERATOR_REVIEW_REQUIRED\n')


if __name__ == '__main__':
    main()
