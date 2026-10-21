import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def arguments():
    parser = argparse.ArgumentParser(description="在独立PostgreSQL中核验真实S3版本登记")
    parser.add_argument("--settings", required=True)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--reference-manifest", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--control-directory", type=Path, required=True)
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=["adopt", "verify", "negative", "write-probe"], required=True)
    parser.add_argument("--probe-key")
    parser.add_argument("--case", choices=["metadata-update", "metadata-delete", "duplicate-key", "invalid-key",
        "invalid-version", "invalid-state", "invalid-size", "wrong-content", "reactivate", "disposed-read",
        "disposed-write", "restricted-control"])
    return parser.parse_args()


def load(selected):
    os.environ["DJANGO_SETTINGS_MODULE"] = selected.settings
    import django
    django.setup()
    from django.db import connection
    from archives.models import ArchiveFact, ArchiveFactVersion
    from files.models import FileObject, FilePage
    from identity.models import Patient
    from records.models import RecordVersion, RecordField
    from tasks.models import Task
    from verification.models import Verification
    from assessment.models import Assessment
    from case_details.evaluation_inventory import SourceManifest
    from files.s3_storage import S3ObjectReference, S3StorageConfiguration, S3VersionedStore
    from files.s3_registered import S3RegisteredStore

    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_user, current_setting('server_version')")
        identity = cursor.fetchone()
        if identity[0] != selected.database_name:
            raise ValueError("S3_REGISTERED_TEST_DATABASE_MISMATCH")
        cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")
        if cursor.fetchone() != (False,):
            raise ValueError("S3_REGISTERED_TEST_SUPERUSER_FORBIDDEN")
    clinical = (FileObject, FilePage, Patient, RecordVersion, RecordField, ArchiveFact, ArchiveFactVersion,
        Verification, Assessment, Task)
    if any(model.objects.exists() for model in clinical):
        raise ValueError("S3_REGISTERED_TEST_EMPTY_CLINICAL_DATABASE_REQUIRED")
    if selected.credentials.stat().st_mode & 0o077:
        raise ValueError("S3_REGISTERED_TEST_CREDENTIAL_PERMISSIONS_INVALID")
    credentials = json.loads(selected.credentials.read_text())
    configuration = S3StorageConfiguration(provider_id=selected.provider_id,
        endpoint_url=credentials["endpoint_url"], region=credentials["region"], bucket=credentials["bucket"],
        prefix=selected.prefix, owner_id=selected.owner_id, access_key=credentials["access_key"],
        secret_key=credentials["secret_key"], ca_bundle=Path(credentials["ca_file"]))
    driver = S3VersionedStore(configuration)
    store = S3RegisteredStore(driver, control_directory=selected.control_directory)
    source_path = selected.source_manifest.resolve(strict=True)
    source = SourceManifest.model_validate_json(source_path.read_bytes())
    if source.original_attachment_comparison != "PASS":
        raise ValueError("S3_REGISTERED_TEST_SOURCE_COMPARISON_REQUIRED")
    saved = json.loads(selected.reference_manifest.read_text())
    if (saved["binding_sha256"] != configuration.binding_sha256
            or saved["source_manifest_sha256"] != hashlib.sha256(source_path.read_bytes()).hexdigest()):
        raise ValueError("S3_REGISTERED_TEST_REFERENCE_SCOPE_MISMATCH")
    references = [S3ObjectReference.model_validate(item) for item in saved["objects"]]
    originals = {item.id + "/original": item for item in source.sources}
    if set(originals) != {item.object_key for item in references} or len(originals) != len(references):
        raise ValueError("S3_REGISTERED_TEST_SOURCE_SCOPE_MISMATCH")
    ordered = sorted(references, key=lambda item: item.size_bytes)
    first = ordered[0]
    second = next(item for item in ordered if item.checksum != first.checksum)
    return store, source_path, originals, references, first, second, identity


def source_bytes(manifest_path, source):
    path = (manifest_path.parent / source.path).resolve(strict=True)
    if not path.is_relative_to(manifest_path.parent):
        raise ValueError("S3_REGISTERED_TEST_SOURCE_PATH_INVALID")
    data = path.read_bytes()
    if len(data) != source.size_bytes or hashlib.sha256(data).hexdigest() != source.sha256:
        raise ValueError("S3_REGISTERED_TEST_SOURCE_BYTES_MISMATCH")
    return data


def worker_command(selected, mode):
    return [sys.executable, str(Path(__file__).resolve()), "--settings", selected.settings,
        "--database-name", selected.database_name, "--credentials", str(selected.credentials),
        "--reference-manifest", str(selected.reference_manifest), "--source-manifest", str(selected.source_manifest),
        "--control-directory", str(selected.control_directory), "--provider-id", selected.provider_id,
        "--prefix", selected.prefix, "--owner-id", selected.owner_id, "--output", str(selected.output), "--mode", mode]


def save_new(path, data):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)


def negative(selected):
    store, manifest_path, originals, _, first, second, _ = load(selected)
    from files.models import S3StoredObject

    row = S3StoredObject.objects.get(binding_sha256=store.driver.configuration.binding_sha256,
        object_key=selected.probe_key or first.object_key)
    values = {name: getattr(row, name) for name in (
        "binding_sha256", "object_key", "version_id", "etag", "checksum", "size_bytes", "status", "created_at", "disposed_at")}
    if selected.case == "metadata-update":
        S3StoredObject.objects.filter(id=row.id).update(checksum=second.checksum)
    elif selected.case == "metadata-delete":
        S3StoredObject.objects.filter(id=row.id).delete()
    elif selected.case == "duplicate-key":
        S3StoredObject.objects.create(**values)
    elif selected.case == "invalid-key":
        S3StoredObject.objects.create(**{**values, "object_key": "."})
    elif selected.case == "invalid-version":
        S3StoredObject.objects.create(**{**values, "object_key": "constraint-probe/" + str(uuid4()), "version_id": "null"})
    elif selected.case == "invalid-state":
        S3StoredObject.objects.create(**{**values, "object_key": "constraint-probe/" + str(uuid4()),
            "status": "disposed", "disposed_at": None})
    elif selected.case == "invalid-size":
        S3StoredObject.objects.create(**{**values, "object_key": "constraint-probe/" + str(uuid4()), "size_bytes": -row.size_bytes})
    elif selected.case == "wrong-content":
        store.put_immutable(first.object_key, source_bytes(manifest_path, originals[second.object_key]))
    elif selected.case == "reactivate":
        S3StoredObject.objects.filter(id=row.id).update(status="available", disposed_at=None)
    elif selected.case == "disposed-read":
        store.get_range(row.object_key, 0, 1023)
    elif selected.case == "disposed-write":
        store.put_immutable(row.object_key, source_bytes(manifest_path, originals[first.object_key]))
    elif selected.case == "restricted-control":
        store.get_range(first.object_key, 0, 1023)
    raise AssertionError("S3_REGISTERED_NEGATIVE_INPUT_ACCEPTED")


def observe_failure(selected, name, expected, *, probe_key=None, control_directory=None):
    command = worker_command(selected, "negative") + ["--case", name]
    if probe_key is not None:
        command.extend(["--probe-key", probe_key])
    if control_directory is not None:
        command[command.index("--control-directory") + 1] = str(control_directory)
    result = subprocess.run(command, capture_output=True, text=True, timeout=45)
    if result.returncode != 1 or expected not in result.stderr or "S3_REGISTERED_NEGATIVE_INPUT_ACCEPTED" in result.stderr:
        raise AssertionError({"case": name, "exit_code": result.returncode, "stderr": result.stderr})
    print(json.dumps({"refused_case": name, "exit_code": result.returncode}), flush=True)
    return {"case": name, "exit_code": result.returncode, "expected_error": expected,
        "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest()}


def schema_validator():
    from files.s3_storage import S3StorageRejected, canonical_key
    root = Path(__file__).resolve().parents[1] / "files"
    schemas = [json.loads((root / name).read_text()) for name in (
        "s3-object-reference.schema.json", "s3-stored-object.schema.json")]
    for schema in schemas:
        Draft202012Validator.check_schema(schema)
    registry = Registry().with_resources((schema["$id"], Resource.from_contents(schema)) for schema in schemas)
    formats = FormatChecker()

    @formats.checks("sjia-canonical-object-key", raises=S3StorageRejected)
    def valid_key(value):
        return isinstance(value, str) and canonical_key(value) == value

    return Draft202012Validator(schemas[1], registry=registry, format_checker=formats)


def main(selected):
    if selected.output.exists():
        raise FileExistsError(selected.output)
    store, manifest_path, originals, references, first, second, identity = load(selected)
    from django.db import transaction
    from files.models import S3StoredObject
    from files.s3_registered import S3StoredObjectRecord
    from case_details.evaluation_inventory import inspect_evaluation_sources

    started = time.perf_counter()
    validator = schema_validator()
    snapshots = []
    if selected.mode == "adopt":
        if S3StoredObject.objects.exists():
            raise ValueError("S3_REGISTERED_TEST_EMPTY_STORAGE_REQUIRED")
        for index, reference in enumerate(references, 1):
            row = store.adopt_reference(reference)
            saved = store.registration_for(reference.object_key)
            validator.validate(saved.model_dump(mode="json"))
            if saved.reference != reference or saved.id != row.id:
                raise AssertionError("S3_REGISTERED_TEST_ADOPTION_MISMATCH")
            snapshots.append(saved.model_dump(mode="json"))
            if index == 1 or index % 10 == 0 or index == len(references):
                print(json.dumps({"adopted_objects": index, "total_objects": len(references)}), flush=True)
        report = {"result": "PASS", "mode": "adopt", "database": identity[0], "server_version": identity[2],
            "files": len(references), "objects": snapshots, "clinical_rows": 0,
            "source_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "reference_manifest_sha256": hashlib.sha256(selected.reference_manifest.read_bytes()).hexdigest(),
            "measured_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": time.perf_counter() - started}
        save_new(selected.output, report)
        print(json.dumps({key: report[key] for key in ("result", "mode", "database", "files", "elapsed_seconds")}), flush=True)
        return
    if selected.mode != "verify":
        raise ValueError("S3_REGISTERED_TEST_MODE_INVALID")
    if S3StoredObject.objects.count() != len(references):
        raise ValueError("S3_REGISTERED_TEST_BASELINE_COUNT_MISMATCH")
    inventory = inspect_evaluation_sources(manifest_path)
    before = {reference.object_key: store.registration_for(reference.object_key).model_dump(mode="json") for reference in references}
    for index, reference in enumerate(references, 1):
        current = store.registration_for(reference.object_key)
        validator.validate(current.model_dump(mode="json"))
        if S3StoredObjectRecord.model_validate_json(current.model_dump_json()) != current:
            raise AssertionError("S3_REGISTERED_TEST_RECORD_SERIALIZATION_MISMATCH")
        if store.reference_for(reference.object_key) != reference or not store.exists(reference.object_key):
            raise AssertionError("S3_REGISTERED_TEST_REFERENCE_MISMATCH")
        store.adopt_reference(reference)
        path = (manifest_path.parent / originals[reference.object_key].path).resolve(strict=True)
        with path.open("rb") as stream:
            expected = stream.read(1024)
        if store.get_range(reference.object_key, 0, 1023) != expected:
            raise AssertionError("S3_REGISTERED_TEST_RANGE_MISMATCH")
        if index == 1 or index % 10 == 0 or index == len(references):
            print(json.dumps({"verified_registrations": index, "total_objects": len(references)}), flush=True)
    first_bytes = source_bytes(manifest_path, originals[first.object_key])
    if store.get(first.object_key) != first_bytes:
        raise AssertionError("S3_REGISTERED_TEST_COMPLETE_READ_MISMATCH")
    failures = []
    for name, expected in [("metadata-update", "S3_STORED_OBJECT_IDENTITY_IMMUTABLE"),
            ("metadata-delete", "S3_STORED_OBJECT_DELETE_FORBIDDEN"), ("duplicate-key", "s3_stored_object_binding_key_uniq"),
            ("invalid-key", "s3_stored_object_key_check"), ("invalid-version", "s3_stored_object_version_check"),
            ("invalid-state", "s3_stored_object_state_check"), ("invalid-size", "s3_stored_object_size_bytes_check"),
            ("wrong-content", "ObjectConflict")]:
        failures.append(observe_failure(selected, name, expected))
    rollback_key = "registry-probes/" + str(uuid4())
    with transaction.atomic():
        store.put_immutable(rollback_key, first_bytes)
        published = store.reference_for(rollback_key)
        transaction.set_rollback(True)
    if S3StoredObject.objects.filter(object_key=rollback_key).exists():
        raise AssertionError("S3_REGISTERED_TEST_TRANSACTION_NOT_REVERTED")
    store.driver.verify_reference(published)
    store.put_immutable(rollback_key, first_bytes)
    if store.reference_for(rollback_key) != published or store.get(rollback_key) != first_bytes:
        raise AssertionError("S3_REGISTERED_TEST_UPLOAD_RECOVERY_MISMATCH")
    concurrency_key = "registry-probes/" + str(uuid4())
    commands = [worker_command(selected, "write-probe") + ["--probe-key", concurrency_key] for _ in range(2)]
    children = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for command in commands]
    process_report = {"probe_key": concurrency_key, "pids": [child.pid for child in children],
        "started_at": datetime.now(timezone.utc).isoformat()}
    save_new(selected.output.with_name(selected.output.stem + "-processes.json"), process_report)
    print(json.dumps({"concurrent_probe_pids": process_report["pids"]}), flush=True)
    concurrent = []
    for child in children:
        stdout, stderr = child.communicate(timeout=45)
        if child.returncode != 0:
            raise AssertionError({"pid": child.pid, "exit_code": child.returncode, "stderr": stderr})
        concurrent.append(json.loads(stdout))
    if concurrent[0] != concurrent[1] or S3StoredObject.objects.filter(object_key=concurrency_key).count() != 1:
        raise AssertionError("S3_REGISTERED_TEST_CONCURRENT_IDENTITY_MISMATCH")
    concurrent_reference = store.reference_for(concurrency_key)
    versions, markers, _ = store.driver._key_versions(concurrency_key)
    if markers or len(versions) != 1 or versions[0]["VersionId"] != concurrent_reference.version_id:
        raise AssertionError("S3_REGISTERED_TEST_CONCURRENT_NATIVE_VERSION_MISMATCH")
    disposed_key = "registry-probes/" + str(uuid4())
    store.put_immutable(disposed_key, first_bytes)
    disposed_reference = store.reference_for(disposed_key)
    deletion = store.driver.delete_version(disposed_reference, operation_id=uuid4())
    if not deletion.deleted_now:
        raise AssertionError("S3_REGISTERED_TEST_DISPOSITION_NOT_EXECUTED")
    S3StoredObject.objects.filter(object_key=disposed_key).update(status="disposed", disposed_at=deletion.verified_at)
    disposed_record = store.registration_for(disposed_key)
    validator.validate(disposed_record.model_dump(mode="json"))
    if disposed_record.status != "disposed" or store.exists(disposed_key):
        raise AssertionError("S3_REGISTERED_TEST_DISPOSITION_STATE_MISMATCH")
    for name, expected in [("reactivate", "S3_STORED_OBJECT_REACTIVATION_FORBIDDEN"),
            ("disposed-read", "ObjectNotFound"), ("disposed-write", "S3_STORED_OBJECT_DISPOSED")]:
        failures.append(observe_failure(selected, name, expected, probe_key=disposed_key))
    restricted = selected.control_directory.parent / ("restricted-" + str(uuid4()))
    restricted.mkdir(mode=0o700)
    restricted_path = restricted / store.fence_key(first.object_key)
    restricted_path.parent.mkdir(parents=True, mode=0o700)
    restricted_path.parent.chmod(0)
    failures.append(observe_failure(selected, "restricted-control", "S3_CONTROL_DIRECTORY_UNAVAILABLE",
        control_directory=restricted))
    restricted_path.parent.chmod(0o700)
    after = {reference.object_key: store.registration_for(reference.object_key).model_dump(mode="json") for reference in references}
    if before != after or inspect_evaluation_sources(manifest_path) != inventory:
        raise AssertionError("S3_REGISTERED_TEST_ORIGINAL_STATE_CHANGED")
    if S3StoredObject.objects.count() != len(references) + 3:
        raise AssertionError("S3_REGISTERED_TEST_FINAL_COUNT_MISMATCH")
    paths = [Path(__file__).resolve().parents[1] / name for name in (
        "files/models.py", "files/s3_registered.py", "files/migrations/0003_s3storedobject.py",
        "files/s3-object-reference.schema.json", "files/s3-stored-object.schema.json", "tests/verify_s3_registered_storage.py")]
    report = {"result": "PASS", "measured_at": datetime.now(timezone.utc).isoformat(), "database": identity[0],
        "server_version": identity[2], "files": len(references), "clinical_rows": 0,
        "registered_objects": S3StoredObject.objects.count(), "rejected_inputs": failures,
        "rollback_recovered_reference": published.model_dump(mode="json"),
        "concurrent_identity": concurrent, "actual_delete_result": deletion.model_dump(mode="json"),
        "concurrent_processes": process_report,
        "disposed_record": disposed_record.model_dump(mode="json"),
        "source_manifest_sha256": inventory["source_manifest_sha256"],
        "original_snapshots_sha256": hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
        "source_files": {str(path.relative_to(Path(__file__).resolve().parents[1])):
            hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
        "elapsed_seconds": time.perf_counter() - started, "application_s3_integration": "NOT_RUN",
        "new_model_calls": 0, "source_database_changed": False}
    save_new(selected.output, report)
    print(json.dumps({key: report[key] for key in ("result", "database", "files", "registered_objects",
        "elapsed_seconds", "clinical_rows", "application_s3_integration")}), flush=True)


if __name__ == "__main__":
    selected = arguments()
    if selected.mode == "negative":
        negative(selected)
    elif selected.mode == "write-probe":
        store, manifest_path, originals, _, first, _, _ = load(selected)
        store.put_immutable(selected.probe_key, source_bytes(manifest_path, originals[first.object_key]))
        record = store.registration_for(selected.probe_key)
        print(json.dumps({"id": str(record.id), "reference": record.reference.model_dump(mode="json")}))
    else:
        main(selected)
