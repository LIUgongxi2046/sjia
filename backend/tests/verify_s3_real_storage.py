import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import time
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from case_details.evaluation_inventory import SourceManifest, inspect_evaluation_sources
from files.s3_storage import (S3DeleteResult, S3ObjectReference, S3StorageConfiguration,
    S3VersionedStore, canonical_key)


def arguments():
    parser = argparse.ArgumentParser(description="使用授权来源及独立S3环境核验准确对象版本")
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--reference-manifest", type=Path, required=True)
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=["different-content", "wrong-source-checksum", "wrong-binding",
        "wrong-reference-checksum", "wrong-etag", "invalid-range", "invalid-key", "invalid-version",
        "missing-owner", "changed-policy", "invalid-delete-result", "multiple-native-versions", "missing-delete-operation"])
    parser.add_argument("--delete-result", type=Path)
    parser.add_argument("--probe-reference", type=Path)
    return parser.parse_args()


def load(arguments):
    credentials_path = arguments.credentials.resolve(strict=True)
    if credentials_path.stat().st_mode & 0o077:
        raise ValueError("S3_TEST_CREDENTIAL_PERMISSIONS_INVALID")
    credentials = json.loads(credentials_path.read_text())
    configuration = S3StorageConfiguration(provider_id=arguments.provider_id,
        endpoint_url=credentials["endpoint_url"], region=credentials["region"], bucket=credentials["bucket"],
        prefix=arguments.prefix, owner_id=arguments.owner_id, access_key=credentials["access_key"],
        secret_key=credentials["secret_key"], ca_bundle=Path(credentials["ca_file"]))
    store = S3VersionedStore(configuration)
    manifest_path = arguments.source_manifest.resolve(strict=True)
    manifest = SourceManifest.model_validate_json(manifest_path.read_bytes())
    if manifest.original_attachment_comparison != "PASS":
        raise ValueError("S3_TEST_SOURCE_COMPARISON_REQUIRED")
    saved = json.loads(arguments.reference_manifest.read_text())
    if (saved["source_manifest_sha256"] != hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            or saved["binding_sha256"] != configuration.binding_sha256):
        raise ValueError("S3_TEST_REFERENCE_MANIFEST_MISMATCH")
    references = [S3ObjectReference.model_validate(item) for item in saved["objects"]]
    source_by_key = {source.id + "/original": source for source in manifest.sources}
    if set(source_by_key) != {reference.object_key for reference in references} or len(references) != len(source_by_key):
        raise ValueError("S3_TEST_SOURCE_SCOPE_MISMATCH")
    ordered = sorted(references, key=lambda reference: reference.size_bytes)
    first, second = ordered[0], next(item for item in ordered if item.checksum != ordered[0].checksum)
    return store, manifest_path, manifest, references, source_by_key, first, second


def source_path(manifest_path, source):
    path = (manifest_path.parent / source.path).resolve(strict=True)
    if not path.is_file() or not path.is_relative_to(manifest_path.parent):
        raise ValueError("S3_TEST_SOURCE_PATH_INVALID")
    return path


def negative_case(arguments):
    store, manifest_path, _, _, sources, first, second = load(arguments)
    if arguments.case == "different-content":
        with source_path(manifest_path, sources[second.object_key]).open("rb") as stream:
            store.put_immutable(first.object_key, stream, checksum=second.checksum, size_bytes=second.size_bytes)
    elif arguments.case == "wrong-source-checksum":
        with source_path(manifest_path, sources[first.object_key]).open("rb") as stream:
            store.put_immutable(first.object_key, stream, checksum=second.checksum, size_bytes=first.size_bytes)
    elif arguments.case == "wrong-binding":
        S3VersionedStore(replace(store.configuration, prefix="capability")).get_range(first, 0, 1023)
    elif arguments.case == "wrong-reference-checksum":
        changed = S3ObjectReference.model_validate({**first.model_dump(), "checksum": second.checksum})
        store.get_range(changed, 0, 1023)
    elif arguments.case == "wrong-etag":
        changed = S3ObjectReference.model_validate({**first.model_dump(), "etag": second.etag})
        store.get_range(changed, 0, 1023)
    elif arguments.case == "invalid-range":
        store.get_range(first, first.size_bytes, first.size_bytes)
    elif arguments.case == "invalid-key":
        canonical_key(".")
    elif arguments.case == "invalid-version":
        S3ObjectReference.model_validate({**first.model_dump(), "version_id": "null"})
    elif arguments.case == "missing-owner":
        replace(store.configuration, owner_id="")
    elif arguments.case == "changed-policy":
        store.validate_bucket()
    elif arguments.case == "invalid-delete-result":
        actual = json.loads(arguments.delete_result.read_text())
        S3DeleteResult.model_validate_json(json.dumps({**actual, "deleted_now": False}))
    elif arguments.case == "multiple-native-versions":
        probe = S3ObjectReference.model_validate_json(arguments.probe_reference.read_bytes())
        with source_path(manifest_path, sources[second.object_key]).open("rb") as stream:
            store.put_immutable(probe.object_key, stream, checksum=second.checksum, size_bytes=second.size_bytes)
    elif arguments.case == "missing-delete-operation":
        probe = S3ObjectReference.model_validate_json(arguments.probe_reference.read_bytes())
        store.delete_version(probe, operation_id=None)
    raise AssertionError("S3_NEGATIVE_INPUT_WAS_ACCEPTED")


def observe_failure(arguments, name, expected, *, delete_result=None, probe_reference=None):
    command = [sys.executable, str(Path(__file__).resolve()), "--credentials", str(arguments.credentials),
        "--source-manifest", str(arguments.source_manifest), "--reference-manifest", str(arguments.reference_manifest),
        "--provider-id", arguments.provider_id, "--prefix", arguments.prefix, "--owner-id", arguments.owner_id,
        "--output", str(arguments.output), "--case", name]
    if delete_result is not None:
        command.extend(["--delete-result", str(delete_result)])
    if probe_reference is not None:
        command.extend(["--probe-reference", str(probe_reference)])
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode != 1 or expected not in result.stderr or "S3_NEGATIVE_INPUT_WAS_ACCEPTED" in result.stderr:
        raise AssertionError({"case": name, "exit_code": result.returncode, "stderr": result.stderr})
    print(json.dumps({"refused_case": name, "exit_code": result.returncode}), flush=True)
    return {"case": name, "exit_code": result.returncode, "expected_error": expected,
        "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest()}


def save_new(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main(arguments):
    if arguments.output.exists():
        raise FileExistsError(arguments.output)
    store, manifest_path, manifest, references, sources, first, second = load(arguments)
    inventory_before = inspect_evaluation_sources(manifest_path)
    timestamps = {source.id: source_path(manifest_path, source).stat().st_mtime_ns for source in manifest.sources}
    before = {reference.object_key: store.verify_reference(reference)["VersionId"] for reference in references}
    bucket_proof = store.validate_bucket()
    positive = []
    started = time.perf_counter()
    for index, reference in enumerate(references, 1):
        source = sources[reference.object_key]
        path = source_path(manifest_path, source)
        if source.sha256 != reference.checksum or source.size_bytes != reference.size_bytes:
            raise ValueError("S3_TEST_SOURCE_REFERENCE_MISMATCH")
        with open(os.devnull, "wb") as destination:
            if store.read_to(reference, destination) != reference.size_bytes:
                raise AssertionError("S3_TEST_COMPLETE_READ_MISMATCH")
        ranges = [(0, 1023), (reference.size_bytes // 2, reference.size_bytes // 2 + 1023),
            (reference.size_bytes - 1024, reference.size_bytes - 1)]
        with path.open("rb") as stream:
            for start, end in ranges:
                stream.seek(start)
                if store.get_range(reference, start, end) != stream.read(end - start + 1):
                    raise AssertionError("S3_TEST_RANGE_READ_MISMATCH")
        with path.open("rb") as stream:
            repeated = store.put_immutable(reference.object_key, stream,
                checksum=reference.checksum, size_bytes=reference.size_bytes)
        if repeated != reference:
            raise AssertionError("S3_TEST_IMMUTABLE_RETRY_VERSION_CHANGED")
        positive.append({"source_id": source.id, "bytes": reference.size_bytes, "ranges": len(ranges),
            "idempotent_write_same_version": True})
        if index == 1 or index % 10 == 0 or index == len(references):
            print(json.dumps({"verified_objects": index, "total_objects": len(references)}), flush=True)
    negative = []
    for name, expected in [("different-content", "ObjectConflict"),
            ("wrong-source-checksum", "S3_SOURCE_BYTES_MISMATCH"), ("wrong-binding", "S3_BINDING_MISMATCH"),
            ("wrong-reference-checksum", "S3_RANGE_RESPONSE_MISMATCH"), ("wrong-etag", "PreconditionFailed"),
            ("invalid-range", "S3_RANGE_INVALID"), ("invalid-key", "S3_OBJECT_KEY_INVALID"),
            ("invalid-version", "S3_VERSION_REQUIRED"), ("missing-owner", "S3_CONFIGURATION_REQUIRED")]:
        negative.append(observe_failure(arguments, name, expected))
    probe_key = "version-probes/" + str(uuid4())
    with source_path(manifest_path, sources[first.object_key]).open("rb") as stream:
        old = store.put_immutable(probe_key, stream, checksum=first.checksum, size_bytes=first.size_bytes)
    with source_path(manifest_path, sources[second.object_key]).open("rb") as stream:
        response = store.client.put_object(Bucket=store.configuration.bucket, Key=store.native_key(probe_key),
            Body=stream, ContentLength=second.size_bytes, ServerSideEncryption="AES256",
            Metadata={"sjia-sha256": second.checksum})
    current_head = store.client.head_object(Bucket=store.configuration.bucket, Key=store.native_key(probe_key),
        VersionId=response["VersionId"])
    current = S3ObjectReference(binding_sha256=store.configuration.binding_sha256, object_key=probe_key,
        version_id=current_head["VersionId"], etag=current_head["ETag"], checksum=second.checksum,
        size_bytes=second.size_bytes)
    if current.version_id == old.version_id:
        raise AssertionError("S3_TEST_NATIVE_VERSION_NOT_CHANGED")
    probe_path = arguments.output.with_name(arguments.output.stem + "-probe-reference.json")
    save_new(probe_path, old.model_dump(mode="json"))
    negative.append(observe_failure(arguments, "multiple-native-versions", "S3_IMMUTABLE_KEY_VERSION_CONFLICT",
        probe_reference=probe_path))
    negative.append(observe_failure(arguments, "missing-delete-operation", "S3_DELETE_OPERATION_ID_INVALID",
        probe_reference=probe_path))
    if store.verify_reference(old)["VersionId"] != old.version_id:
        raise AssertionError("S3_TEST_MISSING_OPERATION_DELETED_OBJECT")
    with source_path(manifest_path, sources[first.object_key]).open("rb") as stream:
        if store.get_range(old, 0, 1023) != stream.read(1024):
            raise AssertionError("S3_TEST_OLD_VERSION_NOT_PINNED")
    operation_id = uuid4()
    deletion = store.delete_version(old, operation_id=operation_id)
    if not deletion.deleted_now or store.verify_reference(current)["VersionId"] != current.version_id:
        raise AssertionError("S3_TEST_VERSION_DELETE_SCOPE_MISMATCH")
    recovered = store.delete_version(old, operation_id=operation_id)
    if recovered.deleted_now or recovered.provider_delete_request_id is not None:
        raise AssertionError("S3_TEST_ABSENCE_RECEIPT_MISMATCH")
    receipt_path = arguments.output.with_name(arguments.output.stem + "-delete-result.json")
    save_new(receipt_path, deletion.model_dump(mode="json"))
    negative.append(observe_failure(arguments, "invalid-delete-result", "S3_DELETE_RESPONSE_MISMATCH",
        delete_result=receipt_path))
    cleanup = store.delete_version(current, operation_id=uuid4())
    if not cleanup.deleted_now:
        raise AssertionError("S3_TEST_PROBE_CLEANUP_MISMATCH")
    deny_policy = {"Version": "2012-10-17", "Statement": [{"Sid": "QA30DenyProbeWrite", "Effect": "Deny",
        "Principal": "*", "Action": ["s3:PutObject"],
        "Resource": ["arn:aws:s3:::" + store.configuration.bucket + "/configuration-probe/*"]}]}
    policy_written = store.client.put_bucket_policy(Bucket=store.configuration.bucket, Policy=json.dumps(deny_policy))
    negative.append(observe_failure(arguments, "changed-policy", "S3_BUCKET_POLICY_MISMATCH"))
    actual_policy = json.loads(store.client.get_bucket_policy(Bucket=store.configuration.bucket)["Policy"])
    policy_checksum = hashlib.sha256(json.dumps(actual_policy, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    policy_store = S3VersionedStore(replace(store.configuration, bucket_policy_sha256=policy_checksum))
    qualified_policy_proof = policy_store.validate_bucket()
    if (policy_store.configuration.binding_sha256 != store.configuration.binding_sha256
            or policy_store.configuration.access_configuration_sha256 == store.configuration.access_configuration_sha256):
        raise AssertionError("S3_TEST_ACCESS_CONFIGURATION_IDENTITY_MISMATCH")
    with source_path(manifest_path, sources[first.object_key]).open("rb") as stream:
        if policy_store.get_range(first, 0, 1023) != stream.read(1024):
            raise AssertionError("S3_TEST_REFERENCE_CHANGED_WITH_POLICY")
    policy_removed = store.client.delete_bucket_policy(Bucket=store.configuration.bucket)
    restored_bucket_proof = store.validate_bucket()
    context = ssl.create_default_context(cafile=store.configuration.ca_bundle)
    with httpx.Client(verify=context, trust_env=False, timeout=30) as client:
        anonymous = client.get(store.configuration.endpoint_url + "/" + store.configuration.bucket + "/"
            + store.native_key(first.object_key))
        if anonymous.status_code != 403:
            raise AssertionError("S3_TEST_ANONYMOUS_OBJECT_READ_ACCEPTED")
    after = {reference.object_key: store.verify_reference(reference)["VersionId"] for reference in references}
    inventory_after = inspect_evaluation_sources(manifest_path)
    if before != after or inventory_before != inventory_after or any(
            source_path(manifest_path, source).stat().st_mtime_ns != timestamps[source.id] for source in manifest.sources):
        raise AssertionError("S3_TEST_ORIGINAL_STATE_CHANGED")
    module = Path(__file__).resolve().parents[1] / "files/s3_storage.py"
    report = {"schema_version": "s3-real-storage-verification-1", "result": "PASS",
        "measured_at": datetime.now(timezone.utc).isoformat(), "files": len(references),
        "total_bytes": inventory_before["total_bytes"], "source_manifest_sha256": inventory_before["source_manifest_sha256"],
        "reference_manifest_sha256": hashlib.sha256(arguments.reference_manifest.read_bytes()).hexdigest(),
        "module_sha256": hashlib.sha256(module.read_bytes()).hexdigest(),
        "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "positive": positive, "negative": negative, "bucket_proof": bucket_proof,
        "restored_bucket_proof": restored_bucket_proof,
        "qualified_policy_proof": qualified_policy_proof,
        "policy_request_ids": [item["ResponseMetadata"]["RequestId"] for item in (policy_written, policy_removed)],
        "actual_version_delete": deletion.model_dump(mode="json"),
        "actual_verified_absence": recovered.model_dump(mode="json"),
        "actual_probe_cleanup": cleanup.model_dump(mode="json"),
        "anonymous_status": anonymous.status_code, "elapsed_seconds": time.perf_counter() - started,
        "application_storage_changed": False, "new_model_calls": 0,
        "full_application_acceptance": "NOT_RUN", "clinical_acceptance": "NOT_RUN"}
    save_new(arguments.output, report)
    print(json.dumps({key: report[key] for key in ("result", "files", "total_bytes", "module_sha256",
        "verifier_sha256", "elapsed_seconds", "full_application_acceptance", "clinical_acceptance")}), flush=True)


if __name__ == "__main__":
    selected = arguments()
    if selected.case is not None:
        negative_case(selected)
    else:
        main(selected)
