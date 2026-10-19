import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from jsonschema import Draft202012Validator


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--variants", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--reference-manifest", type=Path, required=True)
    parser.add_argument("--metadata-snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case")
    return parser.parse_args()


def save_new(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def setup(selected):
    os.environ["DJANGO_SETTINGS_MODULE"] = selected.settings
    import django
    django.setup()


def source_bytes(selected):
    from case_details.evaluation_inventory import SourceManifest
    source = SourceManifest.model_validate_json(selected.source_manifest.read_bytes())
    if source.original_attachment_comparison != "PASS":
        raise ValueError("STORAGE_SELECTION_SOURCE_APPROVAL_REQUIRED")
    first = min(source.sources, key=lambda item: item.size_bytes)
    root = selected.source_manifest.resolve(strict=True).parent
    path = (root / first.path).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError("STORAGE_SELECTION_SOURCE_PATH_INVALID")
    data = path.read_bytes()
    if len(data) != first.size_bytes or hashlib.sha256(data).hexdigest() != first.sha256:
        raise ValueError("STORAGE_SELECTION_SOURCE_BYTES_MISMATCH")
    return data


def assert_metadata(selected):
    from django.core import serializers
    data = selected.metadata_snapshot.read_text()
    saved = list(serializers.deserialize("json", data))
    current = [item.object.__class__.objects.get(pk=item.object.pk) for item in saved]
    if serializers.serialize("json", current) != data:
        raise AssertionError("STORAGE_SELECTION_SOURCE_METADATA_CHANGED")


def network_denied(event, values):
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise RuntimeError("STORAGE_SELECTION_NETWORK_ACCESS_DENIED")


def child_case(selected):
    if selected.case == "offline-import":
        sys.addaudithook(network_denied)
        setup(selected)
        from django.core.management import call_command
        from django.utils.functional import empty
        from files.service import object_store
        from files.storage_factory import storage_selection
        call_command("check", verbosity=0)
        importlib.import_module("config.urls")
        if object_store._wrapped is not empty or storage_selection._wrapped is not empty:
            raise AssertionError("STORAGE_SELECTION_IMPORT_EAGERLY_RESOLVED")
        print(json.dumps({"result": "PASS", "case": selected.case, "network_calls": 0}))
        return
    setup(selected)
    from files.service import object_store
    from files.storage_factory import S3PrivateConfiguration, inspect_storage_configuration, storage_selection
    if selected.case == "local-positive":
        from files.deletion import delete_local_object, read_deletion_receipt
        from files.storage import LocalFileStore
        if not isinstance(object_store, LocalFileStore):
            raise AssertionError("STORAGE_SELECTION_LOCAL_TYPE_MISMATCH")
        data = source_bytes(selected)
        key = "selection-probes/" + str(uuid4())
        object_store.put_immutable(key, data)
        object_store.put_immutable(key, data)
        if object_store.get(key) != data or inspect_storage_configuration(None) is not None:
            raise AssertionError("STORAGE_SELECTION_LOCAL_BYTES_MISMATCH")
        operation = uuid4()
        values = {"operation_id": operation, "object_key": key,
            "checksum": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
        delete_local_object(object_store, **values)
        receipt = read_deletion_receipt(object_store, **values)
        if receipt is None or object_store.exists(key):
            raise AssertionError("STORAGE_SELECTION_LOCAL_DELETE_NOT_VERIFIED")
        print(json.dumps({"result": "PASS", "case": selected.case}))
        return
    if selected.case == "runtime-missing":
        inspect_storage_configuration(None)
    elif selected.case == "runtime-mismatch":
        expected = storage_selection.declaration()
        expected["kind"] = "local"
        inspect_storage_configuration(expected)
    elif selected.case == "configuration-changed":
        storage_selection.declaration()
        with Path(os.environ["SJIA_S3_STORAGE_CONFIGURATION_FILE"]).open("ab") as stream:
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        storage_selection.declaration()
    elif selected.case == "restore-root":
        from retention.content_recovery import configured_restore_root
        configured_restore_root()
    else:
        storage_selection.declaration()
    raise AssertionError("STORAGE_SELECTION_INVALID_INPUT_ACCEPTED")


def main(selected):
    if selected.output.exists():
        raise FileExistsError(selected.output)
    setup(selected)
    from django.db import connection
    from files.disposition_storage import disposition_object_proof, disposition_task_schema
    from files.models import FileObject, S3StoredObject
    from files.service import object_store, read_object_bytes
    from files.s3_deletion import S3RegisteredDeletion
    from files.s3_registered import S3RegisteredStore
    from files.s3_storage import S3ObjectReference
    from files.storage_factory import S3PrivateConfiguration, inspect_storage_configuration, storage_selection
    from retention.content_recovery import configured_restore_root

    started = time.perf_counter()
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_user, current_setting('server_version')")
        database, role, version = cursor.fetchone()
        cursor.execute("SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user")
        if database != "sjia_qa33_business_storage" or role != database or cursor.fetchone() != (False, False, False):
            raise ValueError("STORAGE_SELECTION_ISOLATED_DATABASE_REQUIRED")
    if not version.startswith("17.11") or not isinstance(object_store, S3RegisteredStore):
        raise AssertionError("STORAGE_SELECTION_TARGET_MISMATCH")
    assert_metadata(selected)
    expected = storage_selection.declaration()
    if inspect_storage_configuration(expected) != expected or not storage_selection.writable():
        raise AssertionError("STORAGE_SELECTION_RUNTIME_MISMATCH")
    if configured_restore_root() is not None:
        raise AssertionError("STORAGE_SELECTION_UNEXPECTED_RESTORE_TARGET")
    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root / "backend-design/runtime.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator({**schema["properties"]["object_storage"], "$defs": schema["$defs"]}).validate(expected)
    configuration_schema = json.loads((root / "backend/files/s3-private-storage.schema.json").read_text())
    Draft202012Validator.check_schema(configuration_schema)
    raw = selected.configuration.read_bytes()
    Draft202012Validator(configuration_schema).validate(json.loads(raw))
    parsed = S3PrivateConfiguration.model_validate_json(raw)
    if any(secret.get_secret_value() in repr(parsed) or secret.get_secret_value() in parsed.model_dump_json()
            for secret in (parsed.access_key, parsed.secret_key)):
        raise AssertionError("STORAGE_SELECTION_SECRET_EXPOSED")
    adopted = json.loads(selected.reference_manifest.read_text())
    if adopted["result"] != "PASS" or adopted["files"] != 63 or adopted["binding_sha256"] != expected["binding_sha256"]:
        raise ValueError("STORAGE_SELECTION_REFERENCE_SCOPE_INVALID")
    source = json.loads(selected.source_manifest.read_text())
    sources = {item["id"]: item for item in source["sources"]}
    for item in adopted["objects"]:
        file = FileObject.objects.get(pk=item["file_id"])
        reference = S3ObjectReference.model_validate(item["reference"])
        if object_store.registration_for(file.object_key).model_dump(mode="json") != item["registration"]:
            raise AssertionError("STORAGE_SELECTION_REGISTRATION_CHANGED")
        proof = disposition_object_proof(object_store, file)
        if proof.storage_reference != reference or disposition_task_schema(object_store, proof) != "s3-delete-1":
            raise AssertionError("STORAGE_SELECTION_PROOF_MISMATCH")
        path = (selected.source_manifest.parent / sources[str(file.id)]["path"]).resolve(strict=True)
        with path.open("rb") as stream:
            if object_store.get_range(file.object_key, 0, 1023) != stream.read(1024):
                raise AssertionError("STORAGE_SELECTION_RANGE_MISMATCH")
    data = source_bytes(selected)
    first = FileObject.objects.filter(id__in=list(sources)).order_by("size_bytes", "id").first()
    if read_object_bytes(first.id) != data:
        raise AssertionError("STORAGE_SELECTION_BUSINESS_READ_MISMATCH")
    key = "selection-probes/" + str(uuid4())
    object_store.put_immutable(key, data)
    reference = object_store.reference_for(key)
    object_store.put_immutable(key, data)
    if object_store.reference_for(key) != reference or object_store.get(key) != data:
        raise AssertionError("STORAGE_SELECTION_GLOBAL_WRITE_MISMATCH")
    operation = uuid4()
    deletion = S3RegisteredDeletion(object_store)
    receipt = deletion.delete(reference, operation_id=operation)
    if object_store.exists(key) or deletion.read_receipt(reference, operation_id=operation) != receipt:
        raise AssertionError("STORAGE_SELECTION_GLOBAL_DELETE_NOT_VERIFIED")
    save_new(selected.output.with_name(selected.output.stem + "-technical-object.json"),
        {"reference": reference.model_dump(mode="json"), "operation_id": str(operation),
            "receipt_key": receipt[0], "receipt_sha256": hashlib.sha256(receipt[1]).hexdigest(),
            "receipt": receipt[2].model_dump(mode="json")})
    results = []
    base_command = [sys.executable, str(Path(__file__).resolve())]
    for argument, value in vars(selected).items():
        if argument != "case" and value is not None:
            base_command.extend(["--" + argument.replace("_", "-"), str(value)])
    cases = [("offline-import", "", 0), ("local-positive", "", 0),
        ("missing-config", "S3_STORAGE_CONFIGURATION_REQUIRED", 1),
        ("unknown-backend", "STORAGE_BACKEND_UNREGISTERED", 1),
        ("local-conflict", "STORAGE_CONFIGURATION_SELECTION_CONFLICT", 1),
        ("relative-config", "STORAGE_CONFIGURATION_PATH_INVALID", 1),
        ("permissive", "STORAGE_CONFIGURATION_PERMISSIONS_INVALID", 1),
        ("symlink", "STORAGE_CONFIGURATION_PATH_INVALID", 1),
        ("fifo", "STORAGE_CONFIGURATION_PERMISSIONS_INVALID", 1),
        ("oversize", "STORAGE_CONFIGURATION_PERMISSIONS_INVALID", 1),
        ("invalid-schema", "extra_forbidden", 1),
        ("relative-control", "S3_CONFIGURATION_ABSOLUTE_PATH_REQUIRED", 1),
        ("runtime-missing", "RUNTIME_S3_STORAGE_DECLARATION_REQUIRED", 1),
        ("runtime-mismatch", "RUNTIME_STORAGE_CONFIGURATION_MISMATCH", 1),
        ("configuration-changed", "STORAGE_CONFIGURATION_RESTART_REQUIRED", 1),
        ("restore-root", "RESTORE_S3_STORAGE_TARGET_REQUIRED", 1)]
    for case, reason, exit_code in cases:
        environment = {**os.environ, "SJIA_FILE_OBJECT_BACKEND": "s3",
            "SJIA_S3_STORAGE_CONFIGURATION_FILE": str(selected.configuration), "SJIA_RESTORE_ROOT": ""}
        if case in {"offline-import", "missing-config"}:
            environment["SJIA_S3_STORAGE_CONFIGURATION_FILE"] = ""
        if case == "unknown-backend":
            environment["SJIA_FILE_OBJECT_BACKEND"] = "unregistered"
        if case in {"local-positive", "local-conflict"}:
            environment["SJIA_FILE_OBJECT_BACKEND"] = "local"
            if case == "local-positive":
                environment["SJIA_S3_STORAGE_CONFIGURATION_FILE"] = ""
        if case == "relative-config":
            environment["SJIA_S3_STORAGE_CONFIGURATION_FILE"] = "relative-configuration.json"
        if case in {"permissive", "symlink", "fifo", "oversize", "invalid-schema", "relative-control"}:
            environment["SJIA_S3_STORAGE_CONFIGURATION_FILE"] = str(selected.variants / (case + ".json"))
        if case == "configuration-changed":
            path = selected.variants / ("pinned-" + str(uuid4()) + ".json")
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
                stream.write(raw)
            environment["SJIA_S3_STORAGE_CONFIGURATION_FILE"] = str(path)
        if case == "restore-root":
            environment["SJIA_RESTORE_ROOT"] = str(selected.variants)
        child = subprocess.run([*base_command, "--case", case], env=environment,
            capture_output=True, text=True, timeout=45)
        if child.returncode != exit_code or reason not in child.stderr or "STORAGE_SELECTION_INVALID_INPUT_ACCEPTED" in child.stderr:
            raise AssertionError({"case": case, "exit_code": child.returncode,
                "stderr": child.stderr, "stdout": child.stdout})
        results.append({"case": case, "exit_code": child.returncode, "reason_code": reason or "PASS",
            "stderr_sha256": hashlib.sha256(child.stderr.encode()).hexdigest()})
        print(json.dumps({"case": case, "result": "PASS"}), flush=True)
    assert_metadata(selected)
    if FileObject.objects.count() != 64 or S3StoredObject.objects.filter(status="available").count() != 63:
        raise AssertionError("STORAGE_SELECTION_FINAL_STATE_MISMATCH")
    report = {"schema_version": "storage-selection-verification-1", "result": "PASS",
        "measured_at": datetime.now(timezone.utc).isoformat(), "database": database,
        "server_version": version, "runtime_storage": expected, "files": 63,
        "metadata_rows": 382, "range_reads": 63, "business_full_reads": 1,
        "global_immutable_write_retry": "PASS", "actual_technical_version_deleted": True,
        "cases": results, "elapsed_seconds": time.perf_counter() - started,
        "new_model_calls": 0, "full_runtime_admission": "NOT_RUN", "full_application_acceptance": "NOT_RUN",
        "source_sha256": {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [root / "backend/files/storage_factory.py", root / "backend/files/service.py",
                root / "backend/config/runtime_admission.py", root / "backend/retention/content_recovery.py",
                root / "backend/core/errors.py", root / "backend/files/s3_storage.py",
                root / "backend/config/settings/base.py", root / "backend-design/runtime.schema.json",
                root / "backend/files/s3-private-storage.schema.json", Path(__file__).resolve()]}}
    save_new(selected.output, report)
    print(json.dumps({key: report[key] for key in ("result", "files", "range_reads", "metadata_rows",
        "elapsed_seconds", "full_runtime_admission", "full_application_acceptance")}), flush=True)


if __name__ == "__main__":
    selected = arguments()
    if selected.case:
        child_case(selected)
    else:
        main(selected)
