from django.db import connection

from files.models import S3StoredObject
from files.s3_registered import S3StoredObjectRecord
from retention.content_journal import canonical, checksum, read_json, safe_path
from retention.models import ContentRestoreState, S3ContentRestoreState
from retention.s3_backup import S3RecoveryRejected
from retention.s3_lineage_schemas import S3RestoreGeneration, S3RestoreLineage
from retention.s3_recovery_schemas import BackupCheckpoint

LINEAGE_FILE = 'S3_RESTORE_LINEAGE.json'


def generation_document(state, mapping):
    return S3RestoreGeneration(generation=state.generation, parent_restore_id=state.parent_id,
        parent_mapping_sha256=state.parent_mapping_sha256, ancestor_sha256=state.ancestor_sha256,
        restore_root=state.restore_root, mapping=mapping, mapping_sha256=state.mapping_sha256,
        content_checkpoint=BackupCheckpoint.model_validate_json(canonical(state.content_checkpoint)),
        created_at=state.created_at, mapped_at=state.mapped_at)


def validate_database_lineage(lineage, states):
    entries = lineage.generations if lineage is not None else []
    if len(entries) != len(states) or ContentRestoreState.objects.exists():
        raise S3RecoveryRejected('RESTORE_S3_HISTORY_STATE_MISMATCH')
    for entry, state in zip(entries, states):
        mapping = entry.mapping
        if (state.id != mapping.restore_id or state.generation != entry.generation
                or state.parent_id != entry.parent_restore_id or state.parent_mapping_sha256 != entry.parent_mapping_sha256
                or state.ancestor_sha256 != entry.ancestor_sha256 or state.restore_root != entry.restore_root
                or state.state != 'mapped' or state.mapping_sha256 != entry.mapping_sha256
                or state.database_name != mapping.database or state.cluster_id != mapping.cluster_id
                or state.source_binding_sha256 != mapping.source_snapshot.binding_sha256
                or state.target_binding_sha256 != mapping.target_binding_sha256
                or state.source_snapshot_sha256 != checksum(mapping.source_snapshot.model_dump(mode='json'))
                or state.backup_manifest_sha256 != mapping.backup_manifest_sha256
                or state.content_checkpoint != entry.content_checkpoint.model_dump(mode='json')
                or state.created_at != entry.created_at or state.mapped_at != entry.mapped_at):
            raise S3RecoveryRejected('RESTORE_S3_HISTORY_STATE_MISMATCH')


def ancestor_lineage(state):
    path = safe_path(state.restore_root) / LINEAGE_FILE
    if state.generation == 1:
        if path.exists() or state.parent_id is not None or state.ancestor_sha256 is not None or state.parent_mapping_sha256 is not None:
            raise S3RecoveryRejected('RESTORE_S3_FIRST_GENERATION_HISTORY_CONFLICT')
        return None
    document = read_json(path)
    if checksum(document) != state.ancestor_sha256:
        raise S3RecoveryRejected('RESTORE_S3_HISTORY_CHECKSUM_MISMATCH')
    history = S3RestoreLineage.model_validate_json(canonical(document))
    parent = history.generations[-1]
    if (len(history.generations) != state.generation - 1 or parent.mapping.restore_id != state.parent_id
            or parent.mapping_sha256 != state.parent_mapping_sha256):
        raise S3RecoveryRejected('RESTORE_S3_HISTORY_PARENT_MISMATCH')
    return history


def complete_lineage(state, mapping, states):
    ancestors = ancestor_lineage(state)
    entries = list(ancestors.generations) if ancestors is not None else []
    entries.append(generation_document(state, mapping))
    history = S3RestoreLineage(generations=entries)
    validate_database_lineage(history, states)
    return history


def validate_registration_history(store, lineage, source=None):
    expected = {}

    def include(records):
        for record in records:
            previous = expected.get(record.id)
            if previous is not None and (
                    previous.model_dump(exclude={'status', 'disposed_at'}) != record.model_dump(exclude={'status', 'disposed_at'})
                    or previous.status == 'disposed' and record != previous):
                raise S3RecoveryRejected('RESTORE_S3_HISTORICAL_REGISTRATION_CHANGED')
            expected[record.id] = record

    if lineage is not None:
        for entry in lineage.generations:
            include(entry.mapping.source_snapshot.registrations)
            include(item.target for item in entry.mapping.objects if item.kind == 'copied')
    actual = {row.id: S3StoredObjectRecord(id=row.id, reference=store._reference(row), status=row.status,
        created_at=row.created_at, disposed_at=row.disposed_at) for row in S3StoredObject.objects.order_by('id')}
    if source is not None:
        include(source.registrations)
    else:
        include(row for row in actual.values() if row.reference.binding_sha256 == store.driver.configuration.binding_sha256)
    if actual != expected:
        raise S3RecoveryRejected('RESTORE_S3_HISTORICAL_REGISTRATION_SCOPE_MISMATCH')


def capture_current_lineage(store, source, checkpoint):
    if not S3ContentRestoreState.objects.exists():
        validate_registration_history(store, None, source)
        return None
    if not connection.in_atomic_block:
        raise S3RecoveryRejected('S3_BACKUP_HISTORY_DATABASE_FREEZE_REQUIRED')
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(DISTINCT relation) FROM pg_locks WHERE pid=pg_backend_pid() "
            "AND mode='ShareLock' AND granted AND relation IN ('sjia.file_object'::regclass,"
            "'sjia.s3_stored_object'::regclass,'sjia.s3_content_restore_state'::regclass)")
        if cursor.fetchone() != (3,):
            raise S3RecoveryRejected('S3_BACKUP_HISTORY_DATABASE_FREEZE_REQUIRED')
    from retention.content_engine import verify_manifest
    from retention.content_journal import configured_journal
    from retention.content_recovery import verify_restore
    from retention.s3_restore import load_context, target_document
    context = load_context()
    history = context.lineage
    proof = verify_restore(configured_journal(), context.state.restore_root,
        target_document(context.state), verify=verify_manifest)
    parsed_checkpoint = BackupCheckpoint.model_validate_json(canonical(checkpoint))
    if proof['checkpoint'] != parsed_checkpoint.model_dump(mode='json'):
        raise S3RecoveryRejected('S3_BACKUP_HISTORY_CONTENT_CHECKPOINT_MISMATCH')
    history.validate_source(source, parsed_checkpoint)
    validate_registration_history(store, history, source)
    return history
