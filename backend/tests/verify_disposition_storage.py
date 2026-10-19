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

from jsonschema import Draft202012Validator
from pydantic import TypeAdapter
from referencing import Registry, Resource


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", required=True)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--metadata-snapshot", type=Path, required=True)
    parser.add_argument("--metadata-report", type=Path, required=True)
    parser.add_argument("--reference-manifest", type=Path, required=True)
    parser.add_argument("--control-directory", type=Path, required=True)
    parser.add_argument("--local-directory", type=Path, required=True)
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=["adopt", "verify", "negative"], required=True)
    parser.add_argument("--case", choices=["business-checksum-read", "business-checksum-write",
        "business-disposed-read", "business-disposed-write", "proof-missing", "proof-version",
        "proof-content", "local-s3-proof", "unknown-store"])
    return parser.parse_args()


def save_new(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def assert_metadata(selected):
    from django.core import serializers
    data = selected.metadata_snapshot.read_text()
    expected = list(serializers.deserialize("json", data))
    current = [item.object.__class__.objects.get(pk=item.object.pk) for item in expected]
    if serializers.serialize("json", current) != data:
        raise AssertionError("DISPOSITION_STORAGE_SOURCE_METADATA_CHANGED")


def load(selected):
    os.environ["DJANGO_SETTINGS_MODULE"] = selected.settings
    import django
    django.setup()
    from django.db import connection
    from archives.models import ArchiveFact, ArchiveFactVersion
    from assessment.models import Assessment
    from files.models import FileObject, FilePage
    from files.s3_registered import S3RegisteredStore
    from files.s3_storage import S3StorageConfiguration, S3VersionedStore
    from identity.models import PrincipalCredential
    from records.models import RecordVersion, RecordField
    from tasks.models import Task
    from verification.models import Verification
    from case_details.evaluation_inventory import SourceManifest

    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_user, current_setting('server_version')")
        identity = cursor.fetchone()
        if identity[0] != selected.database_name:
            raise ValueError("DISPOSITION_STORAGE_DATABASE_MISMATCH")
        cursor.execute("SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user")
        if cursor.fetchone() != (False, False, False):
            raise ValueError("DISPOSITION_STORAGE_LIMITED_ROLE_REQUIRED")
    for model in (PrincipalCredential, RecordVersion, RecordField, ArchiveFact, ArchiveFactVersion,
            Verification, Assessment, Task):
        if model.objects.exists():
            raise ValueError("DISPOSITION_STORAGE_SCOPE_DATABASE_REQUIRED")
    report = json.loads(selected.metadata_report.read_text())
    if (report["result"] != "PASS" or report["database"] != selected.database_name
            or not report["source_read_only"] or report["objects"] != 63 or report["pages"] != 290
            or hashlib.sha256(selected.metadata_snapshot.read_bytes()).hexdigest() != report["metadata_sha256"]):
        raise ValueError("DISPOSITION_STORAGE_METADATA_SCOPE_MISMATCH")
    source_data = selected.source_manifest.read_bytes()
    source = SourceManifest.model_validate_json(source_data)
    if (source.original_attachment_comparison != "PASS"
            or hashlib.sha256(source_data).hexdigest() != report["source_manifest_sha256"]):
        raise ValueError("DISPOSITION_STORAGE_SOURCE_COMPARISON_REQUIRED")
    sources = {item.id: item for item in source.sources}
    files = list(FileObject.objects.filter(id__in=list(sources)).order_by("size_bytes", "id"))
    if len(files) != 63 or FilePage.objects.filter(file_id__in=list(sources)).count() != 290:
        raise ValueError("DISPOSITION_STORAGE_ORIGINAL_COUNT_MISMATCH")
    for file in files:
        value = sources[str(file.id)]
        if (file.checksum != value.sha256 or file.size_bytes != value.size_bytes or file.status != "available"
                or file.object_version != value.sha256 or file.purpose != "original"):
            raise ValueError("DISPOSITION_STORAGE_ORIGINAL_MISMATCH")
    if selected.credentials.stat().st_mode & 0o077:
        raise ValueError("DISPOSITION_STORAGE_CREDENTIAL_PERMISSIONS_INVALID")
    credentials = json.loads(selected.credentials.read_text())
    configuration = S3StorageConfiguration(provider_id=selected.provider_id,
        endpoint_url=credentials["endpoint_url"], region=credentials["region"], bucket=credentials["bucket"],
        prefix=selected.prefix, owner_id=selected.owner_id, access_key=credentials["access_key"],
        secret_key=credentials["secret_key"], ca_bundle=Path(credentials["ca_file"]))
    store = S3RegisteredStore(S3VersionedStore(configuration), control_directory=selected.control_directory)
    assert_metadata(selected)
    return store, sources, files, identity


def source_path(selected, value):
    root = selected.source_manifest.resolve(strict=True).parent
    path = (root / value.path).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError("DISPOSITION_STORAGE_SOURCE_PATH_INVALID")
    return path


def source_bytes(selected, value):
    data = source_path(selected, value).read_bytes()
    if len(data) != value.size_bytes or hashlib.sha256(data).hexdigest() != value.sha256:
        raise ValueError("DISPOSITION_STORAGE_SOURCE_BYTES_MISMATCH")
    return data


def proof_validator():
    from verify_s3_registered_storage import schema_validator
    root = Path(__file__).resolve().parents[1] / "files"
    schemas = [json.loads((root / name).read_text()) for name in (
        "s3-object-reference.schema.json", "disposition-object-proof.schema.json")]
    for schema in schemas:
        Draft202012Validator.check_schema(schema)
    registry = Registry().with_resources((schema["$id"], Resource.from_contents(schema)) for schema in schemas)
    return Draft202012Validator(schemas[1], registry=registry, format_checker=schema_validator().format_checker)


def worker_command(selected, case):
    return [sys.executable, str(Path(__file__).resolve()), "--settings", selected.settings,
        "--database-name", selected.database_name, "--credentials", str(selected.credentials),
        "--source-manifest", str(selected.source_manifest), "--metadata-snapshot", str(selected.metadata_snapshot),
        "--metadata-report", str(selected.metadata_report), "--reference-manifest", str(selected.reference_manifest),
        "--control-directory", str(selected.control_directory), "--local-directory", str(selected.local_directory),
        "--provider-id", selected.provider_id, "--prefix", selected.prefix, "--owner-id", selected.owner_id,
        "--output", str(selected.output), "--mode", "negative", "--case", case]


def negative(selected):
    store, sources, files, _ = load(selected)
    from django.db import transaction
    from files.disposition_storage import disposition_object_proof, disposition_task_schema
    from files.models import FileObject
    from files.purpose_schemas import PurposeDispositionObjectProof, PurposeS3DispositionObjectProof
    from files.storage import LocalFileStore
    first = files[0]
    second = next(item for item in files if item.checksum != first.checksum)
    proof = disposition_object_proof(store, first)
    if selected.case.startswith("business-"):
        with transaction.atomic():
            if selected.case.startswith("business-checksum"):
                FileObject.objects.filter(id=first.id).update(checksum=second.checksum)
            else:
                FileObject.objects.filter(id=first.id).update(status="disposed")
            if selected.case.endswith("write"):
                store.put_immutable(first.object_key, source_bytes(selected, sources[str(first.id)]))
            else:
                store.get_range(first.object_key, 0, 1023)
    elif selected.case == "proof-missing":
        local = PurposeDispositionObjectProof.model_validate_json(proof.model_dump_json(exclude={"storage_reference"}))
        disposition_task_schema(store, local)
    elif selected.case == "proof-version":
        other = store.registration_for(second.object_key).reference
        wrong = proof.model_copy(update={"storage_reference": proof.storage_reference.model_copy(update={"version_id": other.version_id})})
        disposition_task_schema(store, wrong)
    elif selected.case == "proof-content":
        PurposeS3DispositionObjectProof.model_validate_json(json.dumps({**proof.model_dump(mode="json"), "checksum": second.checksum}))
    elif selected.case == "local-s3-proof":
        disposition_task_schema(LocalFileStore(selected.local_directory), proof)
    elif selected.case == "unknown-store":
        disposition_task_schema(object(), proof)
    raise AssertionError("DISPOSITION_STORAGE_INVALID_INPUT_ACCEPTED")


def main(selected):
    if selected.output.exists():
        raise FileExistsError(selected.output)
    store, sources, files, identity = load(selected)
    from files.disposition_storage import (DISPOSITION_EVENTS, delete_disposition_object, deletion_evidence_state,
        disposition_object_proof, disposition_task_schema, read_disposition_receipt)
    from files.models import FileObject, S3StoredObject
    from files.purpose_schemas import PurposeDispositionApprovalContext, PurposeDispositionTaskRefs, PurposeS3DispositionObjectProof
    from files.s3_deletion import S3RegisteredDeletion
    from files.s3_storage import S3ObjectReference
    from files.storage import LocalFileStore
    from tasks.registry import require_worker_event
    from case_details.evaluation_inventory import inspect_evaluation_sources

    started = time.perf_counter()
    before_inventory = inspect_evaluation_sources(selected.source_manifest)
    binding = store.driver.configuration.binding_sha256
    validator = proof_validator()
    if selected.mode == "adopt":
        if S3StoredObject.objects.exists():
            raise ValueError("DISPOSITION_STORAGE_EMPTY_REGISTRY_REQUIRED")
        native = store.driver.client.list_object_versions(Bucket=store.driver.configuration.bucket,
            Prefix=store.driver.configuration.prefix + "/", MaxKeys=1)
        if native.get("Versions") or native.get("DeleteMarkers") or native.get("IsTruncated"):
            raise ValueError("DISPOSITION_STORAGE_EMPTY_NATIVE_PREFIX_REQUIRED")
        objects = []
        for index, file in enumerate(files, 1):
            with source_path(selected, sources[str(file.id)]).open("rb") as stream:
                reference = store.driver.put_immutable(file.object_key, stream, checksum=file.checksum, size_bytes=file.size_bytes)
            store.adopt_reference(reference)
            proof = disposition_object_proof(store, file)
            validator.validate(proof.model_dump(mode="json"))
            objects.append({"file_id": str(file.id), "reference": reference.model_dump(mode="json"),
                "registration": store.registration_for(file.object_key).model_dump(mode="json"),
                "disposition_proof": proof.model_dump(mode="json")})
            if index == 1 or index % 10 == 0 or index == len(files):
                print(json.dumps({"adopted_business_objects": index, "total_objects": len(files)}), flush=True)
        assert_metadata(selected)
        if before_inventory != inspect_evaluation_sources(selected.source_manifest):
            raise AssertionError("DISPOSITION_STORAGE_ORIGINAL_SOURCE_CHANGED")
        report = {"result": "PASS", "mode": "adopt", "measured_at": datetime.now(timezone.utc).isoformat(),
            "database": identity[0], "server_version": identity[2], "binding_sha256": binding, "files": len(files),
            "total_bytes": sum(file.size_bytes for file in files), "objects": objects,
            "source_manifest_sha256": before_inventory["source_manifest_sha256"],
            "source_metadata_sha256": hashlib.sha256(selected.metadata_snapshot.read_bytes()).hexdigest(),
            "elapsed_seconds": time.perf_counter() - started, "new_model_calls": 0}
        save_new(selected.output, report)
        print(json.dumps({key: report[key] for key in ("result", "mode", "files", "total_bytes", "elapsed_seconds")}), flush=True)
        return
    saved = json.loads(selected.reference_manifest.read_text())
    if (saved["result"] != "PASS" or saved["mode"] != "adopt" or saved["binding_sha256"] != binding
            or saved["files"] != 63 or S3StoredObject.objects.count() != 63):
        raise ValueError("DISPOSITION_STORAGE_ADOPTED_BASELINE_REQUIRED")
    known = {item["file_id"]: item for item in saved["objects"]}
    approval_fields = TypeAdapter(PurposeDispositionApprovalContext.model_fields["file_versions"].annotation)
    task_field = TypeAdapter(PurposeDispositionTaskRefs.model_fields["file_identity"].annotation)
    proofs = []
    for file in files:
        reference = S3ObjectReference.model_validate(known[str(file.id)]["reference"])
        if store.registration_for(file.object_key).model_dump(mode="json") != known[str(file.id)]["registration"]:
            raise AssertionError("DISPOSITION_STORAGE_REGISTRATION_CHANGED")
        store.adopt_reference(reference)
        proof = disposition_object_proof(store, file)
        validator.validate(proof.model_dump(mode="json"))
        task_bytes = task_field.dump_json(proof)
        if (not isinstance(task_field.validate_json(task_bytes), PurposeS3DispositionObjectProof)
                or json.loads(task_bytes) != proof.model_dump(mode="json")
                or disposition_task_schema(store, proof) != "s3-delete-1"):
            raise AssertionError("DISPOSITION_STORAGE_TASK_REFERENCE_NOT_PRESERVED")
        proofs.append(proof)
        with source_path(selected, sources[str(file.id)]).open("rb") as stream:
            expected = stream.read(1024)
        if store.get_range(file.object_key, 0, 1023) != expected or not store.exists(file.object_key):
            raise AssertionError("DISPOSITION_STORAGE_BUSINESS_RANGE_MISMATCH")
    approval_bytes = approval_fields.dump_json(proofs)
    if json.loads(approval_bytes) != [item.model_dump(mode="json") for item in proofs]:
        raise AssertionError("DISPOSITION_STORAGE_APPROVAL_REFERENCE_NOT_PRESERVED")
    if approval_fields.validate_json(approval_bytes) != proofs:
        raise AssertionError("DISPOSITION_STORAGE_APPROVAL_SERIALIZATION_MISMATCH")
    failures = []
    for case, expected in [("business-checksum-read", "S3_BUSINESS_OBJECT_MISMATCH"),
            ("business-checksum-write", "S3_BUSINESS_OBJECT_MISMATCH"),
            ("business-disposed-read", "S3_BUSINESS_OBJECT_DISPOSED"),
            ("business-disposed-write", "S3_BUSINESS_OBJECT_DISPOSED"),
            ("proof-missing", "S3_DISPOSITION_REFERENCE_REQUIRED"),
            ("proof-version", "S3_DISPOSITION_REFERENCE_MISMATCH"),
            ("proof-content", "S3_DISPOSITION_OBJECT_MISMATCH"),
            ("local-s3-proof", "LOCAL_DISPOSITION_REFERENCE_MISMATCH"),
            ("unknown-store", "DISPOSITION_STORAGE_UNREGISTERED")]:
        child = subprocess.run(worker_command(selected, case), capture_output=True, text=True, timeout=45)
        if (child.returncode != 1 or expected not in child.stderr
                or "DISPOSITION_STORAGE_INVALID_INPUT_ACCEPTED" in child.stderr):
            raise AssertionError({"case": case, "exit_code": child.returncode, "stderr": child.stderr})
        failures.append({"case": case, "exit_code": child.returncode, "expected_error": expected,
            "stderr_sha256": hashlib.sha256(child.stderr.encode()).hexdigest()})
        print(json.dumps({"refused_case": case, "exit_code": child.returncode}), flush=True)
        assert_metadata(selected)
    first = files[0]
    data = source_bytes(selected, sources[str(first.id)])
    store.put_immutable(first.object_key, data)
    technical_key = "disposition-probes/" + str(uuid4())
    store.put_immutable(technical_key, data)
    technical = FileObject.objects.create(patient_id=first.patient_id, file_group_id=first.file_group_id,
        derived_from_id=first.id, object_key=technical_key, object_version=first.object_version,
        checksum=first.checksum, size_bytes=first.size_bytes, media_type=first.media_type, purpose="deidentified",
        processing_version="qa33-approved-source-byte-copy-1", status="available",
        retention_policy_id=first.retention_policy_id, created_at=datetime.now(timezone.utc))
    technical_proof = disposition_object_proof(store, technical)
    operation = uuid4()
    save_new(selected.output.with_name(selected.output.stem + "-technical-object.json"),
        {"file_id": str(technical.id), "operation_id": str(operation), "proof": technical_proof.model_dump(mode="json")})
    initial = deletion_evidence_state(store, proof=technical_proof, operation_id=operation)
    if initial != {"journal_present": False, "receipt_present": False}:
        raise AssertionError("DISPOSITION_STORAGE_TECHNICAL_EVIDENCE_PREEXISTED")
    S3RegisteredDeletion(store).prepare(technical_proof.storage_reference, operation_id=operation)
    prepared = deletion_evidence_state(store, proof=technical_proof, operation_id=operation)
    if prepared != {"journal_present": True, "receipt_present": False} or store.exists(technical_key):
        raise AssertionError("DISPOSITION_STORAGE_PREPARED_STATE_MISMATCH")
    answer = delete_disposition_object(store, proof=technical_proof, operation_id=operation)
    if not answer[2].result.deleted_now:
        raise AssertionError("DISPOSITION_STORAGE_TECHNICAL_DELETE_NOT_EXECUTED")
    technical.status = "disposed"
    technical.save(update_fields=["status"])
    repeated = read_disposition_receipt(store, proof=disposition_object_proof(store, technical), operation_id=operation)
    if repeated[1] != answer[1] or delete_disposition_object(store, proof=technical_proof, operation_id=operation)[1] != answer[1]:
        raise AssertionError("DISPOSITION_STORAGE_TECHNICAL_RECEIPT_CHANGED")
    completed = deletion_evidence_state(store, proof=technical_proof, operation_id=operation)
    if completed != {"journal_present": True, "receipt_present": True}:
        raise AssertionError("DISPOSITION_STORAGE_COMPLETED_STATE_MISMATCH")

    local = LocalFileStore(selected.local_directory)
    local_proof = disposition_object_proof(local, first)
    validator.validate(local_proof.model_dump(mode="json"))
    if "storage_reference" in task_field.dump_python(local_proof, mode="json"):
        raise AssertionError("DISPOSITION_STORAGE_LOCAL_SERIALIZATION_CHANGED")
    if disposition_task_schema(local, local_proof) != "local-delete-2":
        raise AssertionError("DISPOSITION_STORAGE_LOCAL_SCHEMA_CHANGED")
    local.put_immutable(first.object_key, data)
    local_operation = uuid4()
    local_initial = deletion_evidence_state(local, proof=local_proof, operation_id=local_operation)
    local_answer = delete_disposition_object(local, proof=local_proof, operation_id=local_operation)
    local_completed = deletion_evidence_state(local, proof=local_proof, operation_id=local_operation)
    if (local_initial != {"journal_present": False, "receipt_present": False}
            or local_completed != {"journal_present": True, "receipt_present": True}
            or read_disposition_receipt(local, proof=local_proof, operation_id=local_operation)[1] != local_answer[1]
            or local.exists(first.object_key) or not store.exists(first.object_key)):
        raise AssertionError("DISPOSITION_STORAGE_LOCAL_COPY_RECEIPT_MISMATCH")
    for event in DISPOSITION_EVENTS.values():
        require_worker_event(event)
    assert_metadata(selected)
    if before_inventory != inspect_evaluation_sources(selected.source_manifest):
        raise AssertionError("DISPOSITION_STORAGE_ORIGINAL_SOURCE_CHANGED")
    if FileObject.objects.count() != 64 or FileObject.objects.filter(status="disposed").count() != 1:
        raise AssertionError("DISPOSITION_STORAGE_FINAL_FILE_COUNT_MISMATCH")
    if S3StoredObject.objects.filter(status="available").count() != 63 or S3StoredObject.objects.filter(status="disposed").count() != 1:
        raise AssertionError("DISPOSITION_STORAGE_FINAL_REGISTRATION_MISMATCH")
    paths = [Path(__file__).resolve().parents[1] / name for name in ("files/purpose_schemas.py",
        "files/disposition_storage.py", "files/disposition-object-proof.schema.json", "retention/disposition.py",
        "retention/jobs.py", "tasks/registry.py", "tasks/dispatcher.py", "tasks/worker.py", "tests/verify_disposition_storage.py")]
    report = {"result": "PASS", "measured_at": datetime.now(timezone.utc).isoformat(), "database": identity[0],
        "server_version": identity[2], "files": len(files), "pages": 290, "total_bytes": sum(file.size_bytes for file in files),
        "rejected_inputs": failures, "binding_sha256": binding, "typed_approval_references": len(proofs),
        "typed_task_references": len(proofs), "original_metadata_sha256": hashlib.sha256(selected.metadata_snapshot.read_bytes()).hexdigest(),
        "approval_projection_sha256": hashlib.sha256(approval_bytes).hexdigest(),
        "source_manifest_sha256": before_inventory["source_manifest_sha256"], "s3_deletion_receipt": answer[2].model_dump(mode="json"),
        "s3_deletion_receipt_sha256": hashlib.sha256(answer[1]).hexdigest(),
        "local_deletion_receipt": local_answer[2].model_dump(mode="json"),
        "technical_file_id": str(technical.id), "evidence_states": [initial, prepared, completed],
        "source_files": {str(path.relative_to(Path(__file__).resolve().parents[1])): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
        "elapsed_seconds": time.perf_counter() - started, "runtime_adoption": "NOT_RUN",
        "full_disposition_workflow": "NOT_RUN", "new_model_calls": 0, "source_database_changed": False}
    save_new(selected.output, report)
    print(json.dumps({key: report[key] for key in ("result", "files", "pages", "typed_approval_references",
        "typed_task_references", "elapsed_seconds", "full_disposition_workflow")}), flush=True)


if __name__ == "__main__":
    selected = arguments()
    if selected.mode == "negative":
        negative(selected)
    else:
        main(selected)
