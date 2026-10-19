import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest import TestCase

import django
from pydantic import ValidationError

django.setup()

from django.db import connection, transaction
from django.db.migrations.recorder import MigrationRecorder
from files.models import FileObject, FilePage, S3StoredObject
from identity.models import Patient
from retention.content_engine import replay_manifest, verify_manifest
from retention.content_journal import checksum, configured_journal, durable_create
from retention.content_recovery import configured_target, replay_restore, verify_restore
from retention.content_schemas import parse_manifest
from retention.models import ContentErasure, S3ContentRestoreState
from retention.s3_backup import capture_source, S3RecoveryRejected, verify_package
from retention.s3_lineage import capture_current_lineage, validate_database_lineage
from retention.s3_lineage_schemas import S3LineageBackupPackage, S3RestoreLineage
from retention.s3_recovery_schemas import S3BackupPackage
from retention.s3_restore import load_context


parser = argparse.ArgumentParser(description='使用实际恢复环境核验原件、引用历史与并发重放')
parser.add_argument('--backup', type=Path)
parser.add_argument('--output', type=Path)
parser.add_argument('--expected-generations', type=int, required=True)
parser.add_argument('--expected-originals', type=int)
parser.add_argument('--expected-bytes', type=int)
parser.add_argument('--replay-only', action='store_true')
arguments = parser.parse_args()


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


context = load_context()
require(arguments.expected_generations >= 1
    and len(context.lineage.generations) == arguments.expected_generations,
    'S3_LINEAGE_RUNTIME_ACTUAL_COMPLETE_GENERATIONS_REQUIRED')
root = Path(context.state.restore_root)
target = configured_target(root)
journal = configured_journal()
if arguments.replay_only:
    proof = replay_restore(journal, root, target, replay=replay_manifest, verify=verify_manifest)
    print(json.dumps({'result': 'PASS', 'pid': os.getpid(), 'generation': context.state.generation,
        'verified_entries': proof['verified_entries'], 'checkpoint': proof['checkpoint']}))
    connection.close()
    sys.exit(0)
require(arguments.backup is not None and arguments.output is not None
    and arguments.expected_originals is not None and arguments.expected_bytes is not None,
    'S3_LINEAGE_RUNTIME_EXACT_INPUT_SCOPE_REQUIRED')
require(not arguments.output.exists(), 'S3_LINEAGE_RUNTIME_REPORT_EXISTS')
started = time.monotonic()
package = verify_package(arguments.backup)
require(package.source_snapshot == context.mapping.source_snapshot, 'S3_LINEAGE_RUNTIME_EXACT_BACKUP_SOURCE_REQUIRED')
if arguments.expected_generations == 1:
    require(type(package) is S3BackupPackage, 'S3_LINEAGE_RUNTIME_FIRST_BACKUP_FORMAT_REQUIRED')
else:
    require(isinstance(package, S3LineageBackupPackage)
        and package.restore_lineage.generations == context.lineage.generations[:-1],
        'S3_LINEAGE_RUNTIME_EXACT_BACKUP_HISTORY_REQUIRED')
states = list(S3ContentRestoreState.objects.order_by('generation'))
before_states = list(S3ContentRestoreState.objects.order_by('generation').values())
before_epochs = list(Patient.objects.order_by('id').values('id', 'data_epoch', 'verification_epoch'))
before_registrations = list(S3StoredObject.objects.order_by('id').values())
before_files = list(FileObject.objects.order_by('id').values())
controls = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(root.rglob('*')) if path.is_file() and path.name != 'CONTENT_REPLAY_VERIFIED.json'}
checks = TestCase()
raw = context.lineage.model_dump(mode='json')
invalid_histories = []
invalid_changes = [
        ('parent_restore_id', {'parent_restore_id': str(context.state.id)}),
        ('parent_mapping_sha256', {'parent_mapping_sha256': context.state.mapping_sha256}),
        ('ancestor_sha256', {'ancestor_sha256': context.state.mapping_sha256}),
        ('generation_sequence', {'generation': context.state.generation + 1})]
if arguments.expected_generations > 1:
    invalid_changes.append(('parent_required', {'parent_restore_id': None}))
for name, changes in invalid_changes:
    invalid = copy.deepcopy(raw)
    invalid['generations'][-1].update(changes)
    with checks.assertRaises(ValidationError):
        S3RestoreLineage.model_validate_json(json.dumps(invalid))
    invalid_histories.append(name)
invalid_sequences = [('missing_root', raw['generations'][1:])]
if arguments.expected_generations > 1:
    invalid_sequences.append(('reversed_generations', list(reversed(raw['generations']))))
for name, generations in invalid_sequences:
    with checks.assertRaises(ValidationError):
        S3RestoreLineage.model_validate_json(json.dumps({**raw, 'generations': generations}))
    invalid_histories.append(name)
with checks.assertRaises(S3RecoveryRejected) as rejected:
    validate_database_lineage(context.lineage, states[1:])
require(str(rejected.exception) == 'RESTORE_S3_HISTORY_STATE_MISMATCH', 'S3_LINEAGE_RUNTIME_STATE_REFUSAL_REQUIRED')
invalid_histories.append('missing_sql_ancestor')
with journal.locked():
    proof = verify_restore(journal, root, target, verify=verify_manifest)
    journal_state = journal._read()
    manifest_bindings = set()
    for entry in journal_state['entries']:
        manifest = parse_manifest(entry['envelope']['manifest'])
        context.validate_manifest(manifest)
        if manifest.schema_version == 'content-disposition-manifest-2':
            manifest_bindings.add(manifest.storage_binding_sha256)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('LOCK TABLE sjia.file_object,sjia.s3_stored_object,sjia.s3_content_restore_state IN SHARE MODE')
        source = capture_source(context.store)
        current_lineage = capture_current_lineage(context.store, source, proof['checkpoint'])
        require(current_lineage == context.lineage, 'S3_LINEAGE_RUNTIME_CURRENT_EXPORT_MISMATCH')
copied = [item for item in context.mapping.objects if item.kind == 'copied']
require(len(copied) == len(package.payloads) == arguments.expected_originals,
    'S3_LINEAGE_RUNTIME_COMPLETE_ORIGINAL_SCOPE_REQUIRED')
payloads = {item.registration_id: item for item in package.payloads}
total_bytes = 0
range_count = 0
for index, item in enumerate(copied, 1):
    payload = payloads[item.source.id]
    with transaction.atomic():
        context.store._lock(item.target.reference.object_key)
        reference = context.store.reference_for(item.target.reference.object_key)
        require(reference == item.target.reference and reference.checksum == payload.checksum
            and reference.size_bytes == payload.size_bytes, 'S3_LINEAGE_RUNTIME_CURRENT_REFERENCE_MISMATCH')
        with open(os.devnull, 'wb') as sink:
            total_bytes += context.store.driver.read_to(reference, sink)
    size = reference.size_bytes
    require(size > 0, 'S3_LINEAGE_RUNTIME_NONEMPTY_ORIGINAL_REQUIRED')
    width = min(1024, size)
    starts = (0, size // 2, size - width)
    with (arguments.backup / 'objects' / payload.filename).open('rb') as original:
        for start in starts:
            end = min(size - 1, start + width - 1)
            original.seek(start)
            require(context.store.get_range(reference.object_key, start, end) == original.read(end - start + 1),
                'S3_LINEAGE_RUNTIME_ORIGINAL_RANGE_MISMATCH')
            range_count += 1
    print(json.dumps({'originals_read': index, 'payload_bytes_read': total_bytes,
        'ranges_checked': range_count}), flush=True)
require(total_bytes == arguments.expected_bytes, 'S3_LINEAGE_RUNTIME_COMPLETE_ORIGINAL_BYTES_REQUIRED')
children = [subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--replay-only',
    '--expected-generations', str(arguments.expected_generations)], stdout=subprocess.PIPE,
    stderr=subprocess.PIPE, text=True, env=os.environ.copy()) for _ in range(2)]
print(json.dumps({'concurrent_replay_pids': [child.pid for child in children]}), flush=True)
concurrent = []
for child in children:
    stdout, stderr = child.communicate(timeout=180)
    require(child.returncode == 0, 'S3_LINEAGE_RUNTIME_CONCURRENT_REPLAY_FAILED:' + stderr)
    result = json.loads(stdout)
    require(result['result'] == 'PASS' and result['generation'] == context.state.generation
        and result['verified_entries'] == proof['verified_entries'] and result['checkpoint'] == proof['checkpoint'],
        'S3_LINEAGE_RUNTIME_CONCURRENT_CONTENT_SCOPE_MISMATCH')
    concurrent.append(result)
require(list(S3ContentRestoreState.objects.order_by('generation').values()) == before_states
    and list(Patient.objects.order_by('id').values('id', 'data_epoch', 'verification_epoch')) == before_epochs
    and list(S3StoredObject.objects.order_by('id').values()) == before_registrations
    and list(FileObject.objects.order_by('id').values()) == before_files,
    'S3_LINEAGE_RUNTIME_REPEATED_REPLAY_CHANGED_DATABASE')
after_controls = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(root.rglob('*')) if path.is_file() and path.name != 'CONTENT_REPLAY_VERIFIED.json'}
require(after_controls == controls and not (root / 'ADOPTION_ADMITTED.json').exists(),
    'S3_LINEAGE_RUNTIME_CONTROLS_MUST_REMAIN')
current_proof = verify_restore(journal, root, target, verify=verify_manifest)
require(current_proof['checkpoint'] == proof['checkpoint']
    and current_proof['verified_entries'] == proof['verified_entries'], 'S3_LINEAGE_RUNTIME_CURRENT_PROOF_REQUIRED')
report = {'result': 'PASS', 'measured_at': datetime.now(timezone.utc).isoformat(),
    'elapsed_seconds': round(time.monotonic() - started, 3), 'target': target,
    'generations': len(context.lineage.generations), 'lineage': raw, 'lineage_sha256': checksum(raw),
    'invalid_histories': invalid_histories, 'originals': len(copied), 'read_bytes': total_bytes,
    'ranges': range_count, 'concurrent_replay': concurrent, 'epochs_unchanged': True,
    'registrations_unchanged': True, 'files_unchanged': True, 'control_files_unchanged': len(controls),
    'files': FileObject.objects.count(), 'registrations': S3StoredObject.objects.count(),
    'pages': FilePage.objects.count(), 'erasures': ContentErasure.objects.count(),
    'migrations': MigrationRecorder.Migration.objects.count(),
    'verified_content_entries': current_proof['verified_entries'],
    'manifest_source_bindings': sorted(manifest_bindings), 'runtime_admission': 'BLOCKED', 'new_model_calls': 0}
durable_create(arguments.output, report)
connection.close()
print(json.dumps({name: report[name] for name in ('result', 'generations', 'originals', 'read_bytes', 'ranges',
    'verified_content_entries', 'runtime_admission')}))
