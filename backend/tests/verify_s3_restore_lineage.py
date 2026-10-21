import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from unittest import TestCase

import django
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
from referencing import Registry, Resource


def deny_network(event, values):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        raise RuntimeError("S3_LINEAGE_DOCUMENT_NETWORK_FORBIDDEN")


sys.addaudithook(deny_network)
django.setup()

from retention.content_journal import checksum
from files.s3_storage import S3StorageRejected, canonical_key
from retention.s3_lineage_schemas import BACKUP_PACKAGE, S3LineageBackupPackage, S3RestoreGeneration, S3RestoreLineage
from retention.s3_recovery_schemas import BackupCheckpoint, S3BackupPackage, S3BackupSource


parser = argparse.ArgumentParser(description="使用实际恢复记录核验完整历史文档")
parser.add_argument("--generation", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
parser.add_argument("--source", required=True, type=Path)
parser.add_argument("--checkpoint", required=True, type=Path)
parser.add_argument("--backup", required=True, type=Path)
parser.add_argument("--lineage-backup", type=Path)
parser.add_argument("--runtime-report", type=Path)
arguments = parser.parse_args()
if arguments.output.exists():
    raise RuntimeError("S3_LINEAGE_VERIFICATION_OUTPUT_EXISTS")
raw = json.loads(arguments.generation.read_bytes())
generation = S3RestoreGeneration.model_validate_json(json.dumps(raw))
if generation.generation != 1:
    raise RuntimeError("S3_LINEAGE_ACTUAL_FIRST_GENERATION_REQUIRED")
document = {"schema_version": "s3-restore-lineage-1", "generations": [raw]}
lineage = S3RestoreLineage.model_validate_json(json.dumps(document))
if lineage.generations != [generation] or checksum(generation.mapping.model_dump(mode="json")) != generation.mapping_sha256:
    raise RuntimeError("S3_LINEAGE_ORIGINAL_MAPPING_REQUIRED")
source_raw = json.loads(arguments.source.read_bytes())
checkpoint_raw = json.loads(arguments.checkpoint.read_bytes())
source = S3BackupSource.model_validate_json(json.dumps(source_raw))
checkpoint = BackupCheckpoint.model_validate_json(json.dumps(checkpoint_raw))
lineage.validate_source(source, checkpoint)
backup_bytes = (arguments.backup / "manifest.json").read_bytes()
original_backup = S3BackupPackage.model_validate_json(backup_bytes)
if BACKUP_PACKAGE.validate_json(backup_bytes) != original_backup:
    raise RuntimeError("S3_LINEAGE_FIRST_BACKUP_FORMAT_CHANGED")
root = Path(__file__).resolve().parents[2]
schema_paths = [root / name for name in (
    "backend/files/s3-object-reference.schema.json", "backend/files/s3-stored-object.schema.json",
    "data-design/s3-backup-source.schema.json", "data-design/s3-object-absence.schema.json",
    "data-design/s3-restore-mapping.schema.json", "data-design/s3-backup-package.schema.json",
    "data-design/s3-restore-lineage.schema.json", "data-design/s3-lineage-backup-package.schema.json")]
schemas = [json.loads(path.read_bytes()) for path in schema_paths]
for schema in schemas:
    Draft202012Validator.check_schema(schema)
registry = Registry().with_resources((schema["$id"], Resource.from_contents(schema)) for schema in schemas)
checker = FormatChecker()
if "date-time" not in checker.checkers:
    raise RuntimeError("S3_LINEAGE_SCHEMA_FORMAT_DEPENDENCIES_REQUIRED")


@checker.checks("sjia-canonical-object-key", raises=S3StorageRejected)
def object_key(value):
    return isinstance(value, str) and canonical_key(value) == value


@checker.checks("sjia-absolute-restore-root", raises=ValueError)
def restore_root(value):
    return isinstance(value, str) and S3RestoreGeneration.absolute_restore_root(value) == value


validators = {schema["$id"]: Draft202012Validator(schema, registry=registry, format_checker=checker) for schema in schemas}
formal = validators["urn:sjia:schema:s3-restore-lineage-1"]
formal.validate(document)
validators["urn:sjia:schema:s3-consistent-backup-1"].validate(json.loads(backup_bytes))
second_backup = None
if arguments.lineage_backup is not None:
    second_bytes = (arguments.lineage_backup / "manifest.json").read_bytes()
    completion = json.loads((arguments.lineage_backup / "COMPLETE.json").read_bytes())
    if hashlib.sha256(second_bytes).hexdigest() != completion["manifest_sha256"]:
        raise RuntimeError("S3_LINEAGE_SECOND_BACKUP_DIGEST_REQUIRED")
    second_backup = BACKUP_PACKAGE.validate_json(second_bytes)
    validators["urn:sjia:schema:s3-consistent-backup-2"].validate(json.loads(second_bytes))
    if (not isinstance(second_backup, S3LineageBackupPackage) or second_backup.restore_lineage != lineage
            or second_backup.source_snapshot.model_dump(exclude={"captured_at"}) != source.model_dump(exclude={"captured_at"})
            or second_backup.content_checkpoint != checkpoint):
        raise RuntimeError("S3_LINEAGE_SECOND_BACKUP_ACTUAL_SOURCE_REQUIRED")
complete_lineage = None
if arguments.runtime_report is not None:
    runtime = json.loads(arguments.runtime_report.read_bytes())
    complete_lineage = S3RestoreLineage.model_validate_json(json.dumps(runtime['lineage']))
    formal.validate(runtime['lineage'])
    if (runtime.get('result') != 'PASS' or second_backup is None
            or complete_lineage.generations[:-1] != lineage.generations
            or complete_lineage.generations[-1].mapping.source_snapshot != second_backup.source_snapshot
            or complete_lineage.generations[-1].mapping.backup_manifest_sha256
                != hashlib.sha256((arguments.lineage_backup / 'manifest.json').read_bytes()).hexdigest()
            or checksum(complete_lineage.model_dump(mode='json')) != runtime['lineage_sha256']):
        raise RuntimeError('S3_LINEAGE_COMPLETE_ACTUAL_RUNTIME_REQUIRED')
checks = TestCase()
negative = []
changes = {
    "mapping_digest": {"mapping_sha256": generation.mapping.source_snapshot.access_configuration_sha256},
    "generation_zero": {"generation": 0},
    "parent_in_first_generation": {"parent_restore_id": str(generation.mapping.restore_id)},
    "ancestor_in_first_generation": {"ancestor_sha256": generation.mapping_sha256},
    "parent_digest_in_first_generation": {"parent_mapping_sha256": generation.mapping_sha256},
    "missing_parent": {"generation": 2},
    "relative_restore_root": {"restore_root": "restore-target"},
    "completion_before_creation": {"mapped_at": generation.created_at.isoformat()},
    "creation_after_mapping": {"created_at": generation.mapped_at.isoformat()},
    "creation_before_source": {"created_at": min(row.created_at for row in generation.mapping.source_snapshot.registrations).isoformat()},
}
for name, values in changes.items():
    invalid = {**copy.deepcopy(raw), **values}
    with checks.assertRaises(ValidationError):
        S3RestoreGeneration.model_validate_json(json.dumps(invalid))
    negative.append(name)
for name, generations in (("empty_lineage", []), ("duplicate_generation", [raw, raw])):
    with checks.assertRaises(ValidationError):
        S3RestoreLineage.model_validate_json(json.dumps({**document, "generations": generations}))
    negative.append(name)
source_changes = []
missing_file = copy.deepcopy(source_raw)
missing_file["file_references"].pop()
source_changes.append(("missing_historical_file", missing_file))
changed_version = copy.deepcopy(source_raw)
changed_version["file_references"][0]["object_version"] = next(
    row["object_version"] for row in source_raw["file_references"]
    if row["object_version"] != changed_version["file_references"][0]["object_version"])
source_changes.append(("historical_file_version", changed_version))
changed_registration = copy.deepcopy(source_raw)
copied = next(item for item in generation.mapping.objects if item.kind == "copied")
target_row = next(row for row in changed_registration["registrations"] if row["id"] == str(copied.target.id))
target_row["id"] = str(copied.source.id)
source_changes.append(("historical_registration_id", changed_registration))
changed_reference = copy.deepcopy(source_raw)
target_row = next(row for row in changed_reference["registrations"] if row["id"] == str(copied.target.id))
target_row["reference"]["version_id"] = copied.source.reference.version_id
source_changes.append(("historical_registration_reference", changed_reference))
changed_disposition = copy.deepcopy(source_raw)
disposed = next(row for row in source.registrations if row.status == "disposed")
target_row = next(row for row in changed_disposition["registrations"] if row["id"] == str(disposed.id))
target_row["disposed_at"] = disposed.created_at.isoformat()
source_changes.append(("disposition_before_parent_mapping", changed_disposition))
for name, invalid in source_changes:
    parsed = S3BackupSource.model_validate_json(json.dumps(invalid))
    with checks.assertRaises(ValueError):
        lineage.validate_source(parsed, checkpoint)
    negative.append(name)
stale_checkpoint = BackupCheckpoint.model_validate_json(json.dumps({**checkpoint_raw, "sequence": 0,
    "entry_sha256": "0" * 64}))
with checks.assertRaises(ValueError):
    lineage.validate_source(source, stale_checkpoint)
negative.append("checkpoint_before_parent")
formal_negative = []
for name, changed in (("unknown_field", {**document, "mapping": raw["mapping"]}),
        ("empty_lineage", {**document, "generations": []}),
        ("duplicate_generation", {**document, "generations": [raw, raw]}),
        ("invalid_generation_type", {**document, "generations": [{**raw, "generation": "1"}]}),
        ("invalid_generation_date", {**document, "generations": [{**raw, "mapped_at": raw["mapping_sha256"]}]}),
        ("invalid_restore_root", {**document, "generations": [{**raw, "restore_root": "restore-target"}]})):
    if not list(formal.iter_errors(changed)):
        raise RuntimeError("S3_LINEAGE_FORMAL_INVALID_DOCUMENT_ACCEPTED")
    formal_negative.append(name)
with checks.assertRaises(ValidationError):
    BACKUP_PACKAGE.validate_json(json.dumps({**json.loads(backup_bytes), "schema_version": "s3-consistent-backup-2"}))
negative.append("second_backup_requires_lineage")
report = {"result": "PASS", "measured_at": datetime.now(timezone.utc).isoformat(),
    "input_sha256": hashlib.sha256(arguments.generation.read_bytes()).hexdigest(),
    "mapping_sha256": generation.mapping_sha256, "lineage_sha256": checksum(lineage.model_dump(mode="json")),
    "mapping_objects": len(generation.mapping.objects), "negative": negative, "formal_negative": formal_negative,
    "actual_source_files": len(source.file_references), "actual_source_registrations": len(source.registrations),
    "actual_disposed_registrations": sum(row.status == "disposed" for row in source.registrations),
    "first_backup_compatibility": "PASS", "current_checkpoint_sequence": checkpoint.sequence,
    "second_backup_schema": "PASS" if second_backup is not None else "NOT_RUN",
    "complete_lineage_schema": "PASS" if complete_lineage is not None else "NOT_RUN",
    "complete_lineage_generations": len(complete_lineage.generations) if complete_lineage is not None else None,
    "schemas": {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in schema_paths},
    "implementation_sha256": hashlib.sha256((root / "backend/retention/s3_lineage_schemas.py").read_bytes()).hexdigest(),
    "network": "FORBIDDEN", "clinical_facts_created": 0, "full_lineage_acceptance": "INCOMPLETE"}
with arguments.output.open("x") as output:
    json.dump(report, output, indent=2)
    output.write("\n")
print(json.dumps(report))
