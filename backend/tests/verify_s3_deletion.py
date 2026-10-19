import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import UUID, uuid4

from verify_s3_registered_storage import load, observe_failure, save_new, source_bytes


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", required=True)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--reference-manifest", type=Path, required=True)
    parser.add_argument("--control-directory", type=Path, required=True)
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=["verify", "negative", "delete-worker", "read-worker"], required=True)
    parser.add_argument("--case", choices=["invalid-operation", "wrong-reference", "other-operation", "no-receipt",
        "fenced-read", "fenced-write", "public-proof"])
    parser.add_argument("--probe-key")
    parser.add_argument("--operation-id", type=UUID)
    return parser.parse_args()


def worker_command(selected, mode):
    return [sys.executable, str(Path(__file__).resolve()), "--settings", selected.settings,
        "--database-name", selected.database_name, "--credentials", str(selected.credentials),
        "--reference-manifest", str(selected.reference_manifest), "--source-manifest", str(selected.source_manifest),
        "--control-directory", str(selected.control_directory), "--provider-id", selected.provider_id,
        "--prefix", selected.prefix, "--owner-id", selected.owner_id, "--output", str(selected.output), "--mode", mode]


def negative(selected):
    store, manifest_path, originals, _, first, second, _ = load(selected)
    from files.s3_deletion import S3RegisteredDeletion
    deletion = S3RegisteredDeletion(store)
    reference = store.registration_for(selected.probe_key).reference
    if selected.case == "invalid-operation":
        deletion.prepare(reference, operation_id=UUID(int=0))
    elif selected.case == "wrong-reference":
        deletion.prepare(reference.model_copy(update={"version_id": second.version_id}), operation_id=selected.operation_id)
    elif selected.case == "other-operation":
        deletion.prepare(reference, operation_id=uuid4())
    elif selected.case in {"no-receipt", "public-proof"}:
        deletion.read_receipt(reference, operation_id=selected.operation_id)
    elif selected.case == "fenced-read":
        store.get_range(reference.object_key, 0, 1023)
    elif selected.case == "fenced-write":
        store.put_immutable(reference.object_key, source_bytes(manifest_path, originals[first.object_key]))
    raise AssertionError("S3_DELETION_NEGATIVE_INPUT_ACCEPTED")


def refused(selected, case, expected, reference, operation_id):
    command = worker_command(selected, "negative") + ["--case", case,
        "--probe-key", reference.object_key, "--operation-id", str(operation_id)]
    child = subprocess.run(command, capture_output=True, text=True, timeout=45)
    if (child.returncode != 1 or expected not in child.stderr
            or "S3_DELETION_NEGATIVE_INPUT_ACCEPTED" in child.stderr):
        raise AssertionError({"case": case, "exit_code": child.returncode, "stderr": child.stderr})
    print(json.dumps({"refused_case": case, "exit_code": child.returncode}), flush=True)
    return {"case": case, "exit_code": child.returncode, "expected_error": expected,
        "stderr_sha256": hashlib.sha256(child.stderr.encode()).hexdigest()}


def main(selected):
    if selected.output.exists():
        raise FileExistsError(selected.output)
    store, manifest_path, originals, references, first, _, identity = load(selected)
    from django.db import connection, transaction
    from files.models import S3StoredObject
    from files.s3_deletion import S3DeletionJournal, S3DeletionReceipt, S3RegisteredDeletion
    from case_details.evaluation_inventory import inspect_evaluation_sources

    started = time.perf_counter()
    before_inventory = inspect_evaluation_sources(manifest_path)
    before = {item.object_key: store.registration_for(item.object_key).model_dump(mode="json") for item in references}
    baseline = S3StoredObject.objects.count()
    data = source_bytes(manifest_path, originals[first.object_key])
    deletion = S3RegisteredDeletion(store)
    probes = []
    failures = []
    receipts = []

    def create_probe():
        key = "deletion-probes/" + str(uuid4())
        store.put_immutable(key, data)
        reference = store.reference_for(key)
        probes.append(reference)
        save_new(selected.output.with_name(selected.output.stem + "-probe-" + str(len(probes)) + ".json"),
            reference.model_dump(mode="json"))
        return reference

    standard = create_probe()
    operation = uuid4()
    for case, expected in [("invalid-operation", "S3_DELETE_OPERATION_ID_INVALID"),
            ("wrong-reference", "S3_DELETION_REGISTRATION_MISMATCH"), ("no-receipt", "FileNotFoundError")]:
        failures.append(refused(selected, case, expected, standard, operation))
        store.driver.verify_reference(standard)
        if store._fenced(standard.object_key):
            raise AssertionError("S3_DELETION_INVALID_PREPARATION_PUBLISHED")
    with transaction.atomic():
        journal = deletion.prepare(standard, operation_id=operation)
        if S3DeletionJournal.model_validate_json(journal.model_dump_json()) != journal:
            raise AssertionError("S3_DELETION_JOURNAL_SERIALIZATION_MISMATCH")
        transaction.set_rollback(True)
    if store.registration_for(standard.object_key).status != "available" or store.exists(standard.object_key):
        raise AssertionError("S3_DELETION_PREPARATION_ROLLBACK_FENCE_MISMATCH")
    store.driver.verify_reference(standard)
    for case, expected in [("fenced-read", "ObjectNotFound"), ("fenced-write", "S3_OBJECT_DISPOSITION_PENDING"),
            ("other-operation", "S3_DELETION_JOURNAL_MISMATCH"), ("no-receipt", "FileNotFoundError")]:
        failures.append(refused(selected, case, expected, standard, operation))
    answer = deletion.delete(standard, operation_id=operation)
    if not answer[2].result.deleted_now or S3DeletionReceipt.model_validate_json(answer[1]) != answer[2]:
        raise AssertionError("S3_DELETION_STANDARD_RECEIPT_MISMATCH")
    if deletion.delete(standard, operation_id=operation)[1] != answer[1]:
        raise AssertionError("S3_DELETION_IDEMPOTENT_RECEIPT_MISMATCH")
    receipt_path = store.control_store.root / answer[0]
    receipt_path.chmod(0o644)
    failures.append(refused(selected, "public-proof", "S3_DELETION_PROOF_UNAVAILABLE", standard, operation))
    receipt_path.chmod(0o600)
    if deletion.read_receipt(standard, operation_id=operation)[1] != answer[1]:
        raise AssertionError("S3_DELETION_RECEIPT_PERMISSION_RECOVERY_MISMATCH")
    receipts.append(answer[2].model_dump(mode="json"))

    recovered = create_probe()
    operation = uuid4()
    deletion.prepare(recovered, operation_id=operation)
    actual_external = store.driver.delete_version(recovered, operation_id=operation)
    if not actual_external.deleted_now:
        raise AssertionError("S3_DELETION_EXTERNAL_EFFECT_NOT_EXECUTED")
    if deletion._path(deletion._keys(recovered)[1], recovered).exists():
        raise AssertionError("S3_DELETION_RECEIPT_PREMATURELY_PRESENT")
    recovered_answer = deletion.delete(recovered, operation_id=operation)
    result = recovered_answer[2].result
    if result.deleted_now or result.provider_delete_request_id is not None or result.provider_delete_status is not None:
        raise AssertionError("S3_DELETION_RECOVERED_ABSENCE_RESPONSE_MISMATCH")
    receipts.append(recovered_answer[2].model_dump(mode="json"))

    rolled_back = create_probe()
    operation = uuid4()
    with transaction.atomic():
        previous = deletion.delete(rolled_back, operation_id=operation)
        if store.registration_for(rolled_back.object_key).status != "disposed":
            raise AssertionError("S3_DELETION_TRANSACTION_DISPOSITION_MISSING")
        transaction.set_rollback(True)
    if store.registration_for(rolled_back.object_key).status != "available" or store.exists(rolled_back.object_key):
        raise AssertionError("S3_DELETION_TRANSACTION_FENCE_MISSING")
    failures.append(refused(selected, "fenced-read", "ObjectNotFound", rolled_back, operation))
    resumed = deletion.delete(rolled_back, operation_id=operation)
    if resumed[1] != previous[1] or resumed[2].result != previous[2].result:
        raise AssertionError("S3_DELETION_TRANSACTION_RECEIPT_CHANGED")
    receipts.append(resumed[2].model_dump(mode="json"))

    concurrent = create_probe()
    operation = uuid4()
    commands = [worker_command(selected, "delete-worker") + ["--probe-key", concurrent.object_key,
        "--operation-id", str(operation)] for _ in range(2)]
    children = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for command in commands]
    process_report = {"object_key": concurrent.object_key, "pids": [child.pid for child in children],
        "operation_id": str(operation), "started_at": datetime.now(timezone.utc).isoformat()}
    save_new(selected.output.with_name(selected.output.stem + "-processes.json"), process_report)
    concurrent_answers = []
    for child in children:
        stdout, stderr = child.communicate(timeout=45)
        if child.returncode != 0:
            raise AssertionError({"pid": child.pid, "exit_code": child.returncode, "stderr": stderr})
        concurrent_answers.append(json.loads(stdout))
    if concurrent_answers[0] != concurrent_answers[1]:
        raise AssertionError("S3_DELETION_CONCURRENT_RECEIPT_MISMATCH")
    receipts.append(deletion.read_receipt(concurrent, operation_id=operation)[2].model_dump(mode="json"))

    serial = create_probe()
    operation = uuid4()
    marker = selected.output.with_name(selected.output.stem + "-read-worker.json")
    with transaction.atomic():
        store._lock(serial.object_key)
        child = subprocess.Popen(worker_command(selected, "read-worker") + ["--probe-key", serial.object_key],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 15
        observed = False
        while time.monotonic() < deadline and not observed:
            if child.poll() is not None:
                raise AssertionError("S3_DELETION_READ_WORKER_TERMINATED_EARLY")
            if marker.exists():
                worker = json.loads(marker.read_text())
                with connection.cursor() as cursor:
                    cursor.execute("SELECT count(*) FROM pg_locks WHERE pid=%s AND locktype='advisory' AND NOT granted",
                        [worker["database_pid"]])
                    observed = cursor.fetchone() == (1,)
            time.sleep(0.01)
        if not observed:
            raise AssertionError("S3_DELETION_READ_LOCK_NOT_OBSERVED")
        deletion.prepare(serial, operation_id=operation)
    stdout, stderr = child.communicate(timeout=45)
    if child.returncode != 1 or "ObjectNotFound" not in stderr:
        raise AssertionError({"pid": child.pid, "exit_code": child.returncode, "stderr": stderr, "stdout": stdout})
    serial_evidence = {**worker, "process_pid": child.pid, "actual_advisory_wait_observed": observed,
        "exit_code": child.returncode, "expected_error": "ObjectNotFound",
        "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest()}
    receipts.append(deletion.delete(serial, operation_id=operation)[2].model_dump(mode="json"))

    for case, expected in [("metadata-update", "S3_STORED_OBJECT_IDENTITY_IMMUTABLE"),
            ("metadata-delete", "S3_STORED_OBJECT_DELETE_FORBIDDEN"), ("duplicate-key", "s3_stored_object_binding_key_uniq"),
            ("invalid-key", "s3_stored_object_key_check"), ("invalid-version", "s3_stored_object_version_check"),
            ("invalid-state", "s3_stored_object_state_check"), ("invalid-size", "s3_stored_object_size_bytes_check"),
            ("wrong-content", "ObjectConflict")]:
        failures.append(observe_failure(selected, case, expected))
    for case, expected in [("reactivate", "S3_STORED_OBJECT_REACTIVATION_FORBIDDEN"),
            ("disposed-read", "ObjectNotFound"), ("disposed-write", "S3_OBJECT_DISPOSITION_PENDING")]:
        failures.append(observe_failure(selected, case, expected, probe_key=standard.object_key))
    restricted = selected.control_directory.parent / ("restricted-" + str(uuid4()))
    restricted.mkdir(mode=0o700)
    restricted_path = restricted / store.fence_key(first.object_key)
    restricted_path.parent.mkdir(parents=True, mode=0o700)
    restricted_path.parent.chmod(0)
    failures.append(observe_failure(selected, "restricted-control", "S3_CONTROL_DIRECTORY_UNAVAILABLE", control_directory=restricted))
    restricted_path.parent.chmod(0o700)

    for index, reference in enumerate(references, 1):
        store.adopt_reference(reference)
        if store.reference_for(reference.object_key) != reference or not store.exists(reference.object_key):
            raise AssertionError("S3_DELETION_ORIGINAL_REFERENCE_CHANGED")
        source = originals[reference.object_key]
        with (manifest_path.parent / source.path).resolve(strict=True).open("rb") as stream:
            expected = stream.read(1024)
        if store.get_range(reference.object_key, 0, 1023) != expected:
            raise AssertionError("S3_DELETION_ORIGINAL_RANGE_CHANGED")
        if index == 1 or index % 10 == 0 or index == len(references):
            print(json.dumps({"verified_originals": index, "total_originals": len(references)}), flush=True)
    if store.get(first.object_key) != data:
        raise AssertionError("S3_DELETION_ORIGINAL_FULL_READ_CHANGED")
    for reference in probes:
        if store.registration_for(reference.object_key).status != "disposed" or store.exists(reference.object_key):
            raise AssertionError("S3_DELETION_PROBE_STATE_MISMATCH")
        versions, markers, _ = store.driver._key_versions(reference.object_key)
        if versions or markers:
            raise AssertionError("S3_DELETION_PROBE_VERSION_REMAINS")
    after = {item.object_key: store.registration_for(item.object_key).model_dump(mode="json") for item in references}
    if before != after or before_inventory != inspect_evaluation_sources(manifest_path):
        raise AssertionError("S3_DELETION_ORIGINAL_STATE_CHANGED")
    if S3StoredObject.objects.count() != baseline + len(probes):
        raise AssertionError("S3_DELETION_FINAL_REGISTRATION_COUNT_MISMATCH")
    paths = [Path(__file__).resolve().parents[1] / name for name in ("files/models.py",
        "files/s3_registered.py", "files/s3_storage.py", "files/s3_deletion.py", "tests/verify_s3_deletion.py")]
    report = {"result": "PASS", "measured_at": datetime.now(timezone.utc).isoformat(), "database": identity[0],
        "server_version": identity[2], "files": len(references), "clinical_rows": 0,
        "registered_objects": S3StoredObject.objects.count(), "disposed_probes": len(probes),
        "receipts": receipts, "rejected_inputs": failures, "actual_external_delete": actual_external.model_dump(mode="json"),
        "concurrent_answers": concurrent_answers, "concurrent_processes": process_report, "read_serialization": serial_evidence,
        "source_manifest_sha256": before_inventory["source_manifest_sha256"],
        "original_snapshots_sha256": hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
        "source_files": {str(path.relative_to(Path(__file__).resolve().parents[1])):
            hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
        "elapsed_seconds": time.perf_counter() - started, "application_s3_integration": "NOT_RUN",
        "new_model_calls": 0, "source_database_changed": False}
    save_new(selected.output, report)
    print(json.dumps({key: report[key] for key in ("result", "files", "disposed_probes", "registered_objects",
        "clinical_rows", "elapsed_seconds", "application_s3_integration")}), flush=True)


if __name__ == "__main__":
    selected = arguments()
    if selected.mode == "negative":
        negative(selected)
    elif selected.mode in {"delete-worker", "read-worker"}:
        store, _, _, _, _, _, _ = load(selected)
        from django.db import connection
        from files.s3_deletion import S3RegisteredDeletion
        if selected.mode == "delete-worker":
            reference = store.registration_for(selected.probe_key).reference
            answer = S3RegisteredDeletion(store).delete(reference, operation_id=selected.operation_id)
            print(json.dumps({"receipt_key": answer[0], "receipt_sha256": hashlib.sha256(answer[1]).hexdigest(),
                "receipt": answer[2].model_dump(mode="json")}))
        else:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                database_pid = cursor.fetchone()[0]
            save_new(selected.output.with_name(selected.output.stem + "-read-worker.json"),
                {"pid": os.getpid(), "database_pid": database_pid})
            store.get_range(selected.probe_key, 0, 1023)
            raise AssertionError("S3_DELETION_FENCED_READ_ACCEPTED")
    else:
        main(selected)
