import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from unittest import TestCase
from uuid import uuid4

import django

django.setup()

from django.db import DatabaseError, connection, transaction
from retention.content_engine import verify_manifest
from retention.content_journal import checksum, configured_journal
from retention.content_recovery import configured_target, verify_restore
from retention.models import S3ContentRestoreState
from retention.s3_restore import load_context


parser = argparse.ArgumentParser(description="使用实际恢复数据库验证历史身份保护")
parser.add_argument("--output", required=True, type=Path)
parser.add_argument("--expected-generations", type=int, default=1)
arguments = parser.parse_args()
if arguments.output.exists():
    raise RuntimeError("S3_LINEAGE_SQL_REPORT_EXISTS")
states = list(S3ContentRestoreState.objects.order_by('generation'))
if (arguments.expected_generations < 1 or len(states) != arguments.expected_generations
        or any(state.generation != number or state.state != 'mapped' for number, state in enumerate(states, 1))):
    raise RuntimeError("S3_LINEAGE_SQL_ACTUAL_COMPLETE_GENERATIONS_REQUIRED")
state = states[-1]
root = Path(state.restore_root)
context = load_context()
journal = configured_journal()
checks = TestCase()


def snapshot():
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_jsonb(item) FROM sjia.s3_content_restore_state item ORDER BY generation")
        return cursor.fetchall()


def control_inventory():
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()}


def refuse(name, statement, parameters, expected):
    with checks.assertRaises(DatabaseError) as rejection:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(statement, parameters)
            raise AssertionError("S3_LINEAGE_INVALID_SQL_ACCEPTED")
    if expected not in str(rejection.exception):
        raise RuntimeError("S3_LINEAGE_SQL_REFUSAL_REASON_MISMATCH")
    return {"case": name, "reason_code": expected}


with journal.locked():
    proof = verify_restore(journal, root, configured_target(root), verify=verify_manifest)
    before = snapshot()
    controls = control_inventory()
    results = []
    for name, field, value in (
            ("generation_identity", "generation", state.generation + 1), ("parent_identity", "parent_id", state.id),
            ("parent_mapping_identity", "parent_mapping_sha256", state.backup_manifest_sha256),
            ("ancestor_digest_identity", "ancestor_sha256", state.backup_manifest_sha256),
            ("source_binding_identity", "source_binding_sha256", state.target_binding_sha256),
            ("target_binding_identity", "target_binding_sha256", state.source_binding_sha256),
            ("creation_identity", "created_at", state.mapped_at)):
        results.append(refuse(name, f"UPDATE sjia.s3_content_restore_state SET {field}=%s WHERE id=%s",
            [value, state.id], "S3_RESTORE_STATE_IDENTITY_IMMUTABLE"))
    results.append(refuse("mapped_digest", "UPDATE sjia.s3_content_restore_state SET mapping_sha256=%s WHERE id=%s",
        [state.backup_manifest_sha256, state.id], "S3_RESTORE_MAPPING_IMMUTABLE"))
    results.append(refuse("mapped_state", "UPDATE sjia.s3_content_restore_state SET state='preparing', "
        "mapping_sha256=NULL,mapped_at=NULL WHERE id=%s", [state.id], "S3_RESTORE_MAPPING_IMMUTABLE"))
    results.append(refuse("delete_history", "DELETE FROM sjia.s3_content_restore_state WHERE id=%s",
        [state.id], "S3_RESTORE_STATE_DELETE_FORBIDDEN"))
    for historical in states[:-1]:
        results.append(refuse(f"generation_{historical.generation}_identity",
            "UPDATE sjia.s3_content_restore_state SET parent_id=%s WHERE id=%s",
            [state.id, historical.id], "S3_RESTORE_STATE_IDENTITY_IMMUTABLE"))
        results.append(refuse(f"generation_{historical.generation}_mapping",
            "UPDATE sjia.s3_content_restore_state SET mapping_sha256=%s WHERE id=%s",
            [state.mapping_sha256, historical.id], "S3_RESTORE_MAPPING_IMMUTABLE"))
        results.append(refuse(f"generation_{historical.generation}_delete",
            "DELETE FROM sjia.s3_content_restore_state WHERE id=%s",
            [historical.id], "S3_RESTORE_STATE_DELETE_FORBIDDEN"))
    base = {"id": uuid4(), "generation": state.generation + 1, "parent_id": state.id,
        "parent_mapping_sha256": state.mapping_sha256, "ancestor_sha256": state.backup_manifest_sha256,
        "database_name": state.database_name, "cluster_id": state.cluster_id, "restore_root": state.restore_root,
        "source_binding_sha256": state.target_binding_sha256, "target_binding_sha256": state.source_binding_sha256,
        "source_snapshot_sha256": state.source_snapshot_sha256, "backup_manifest_sha256": state.backup_manifest_sha256,
        "content_checkpoint": json.dumps(state.content_checkpoint), "state": "preparing",
        "created_at": datetime.now(timezone.utc), "mapping_sha256": None, "mapped_at": None}
    columns = tuple(base)
    statement = "INSERT INTO sjia.s3_content_restore_state (" + ",".join(columns) + ") VALUES (" + ",".join(
        "%s::jsonb" if name == "content_checkpoint" else "%s" for name in columns) + ")"
    cases = (
        ("actual_database", {"database_name": context.mapping.source_snapshot.database}, "S3_RESTORE_DATABASE_IDENTITY_MISMATCH"),
        ("actual_cluster", {"cluster_id": context.mapping.source_snapshot.cluster_id}, "S3_RESTORE_CLUSTER_IDENTITY_MISMATCH"),
        ("root_generation_exists", {"generation": 1, "parent_id": None,
            "parent_mapping_sha256": None, "ancestor_sha256": None}, "S3_RESTORE_ROOT_GENERATION_ALREADY_EXISTS"),
        ("missing_parent", {"parent_id": uuid4()}, "S3_RESTORE_PARENT_IDENTITY_MISMATCH"),
        ("parent_digest", {"parent_mapping_sha256": state.backup_manifest_sha256}, "S3_RESTORE_PARENT_IDENTITY_MISMATCH"),
        ("generation_gap", {"generation": state.generation + 2}, "S3_RESTORE_PARENT_IDENTITY_MISMATCH"),
        ("parent_source_binding", {"source_binding_sha256": state.source_binding_sha256}, "S3_RESTORE_PARENT_IDENTITY_MISMATCH"),
        ("parent_completion_time", {"created_at": state.created_at}, "S3_RESTORE_PARENT_TIME_INVALID"),
        ("same_parent_target", {}, "S3_RESTORE_DISTINCT_PARENT_TARGET_REQUIRED"),
    )
    for name, values, expected in cases:
        request = {**base, **values}
        results.append(refuse(name, statement, [request[column] for column in columns], expected))
    current_proof = verify_restore(journal, root, configured_target(root), verify=verify_manifest)
    if snapshot() != before or control_inventory() != controls or current_proof != proof:
        raise RuntimeError("S3_LINEAGE_SQL_OR_CONTROL_STATE_CHANGED")
report = {"result": "PASS", "measured_at": datetime.now(timezone.utc).isoformat(),
    "database": state.database_name, "cluster_id": state.cluster_id, "restore_id": str(state.id),
    "generations": len(states),
    "sql_refusals": results, "restore_state_sha256": checksum(before), "control_files": len(controls),
    "mapping_sha256": state.mapping_sha256, "content_entries": proof["verified_entries"],
    "runtime_admission": "BLOCKED", "new_model_calls": 0, "full_lineage_acceptance": "INCOMPLETE"}
with arguments.output.open("x") as output:
    json.dump(report, output, indent=2)
    output.write("\n")
connection.close()
print(json.dumps(report))
