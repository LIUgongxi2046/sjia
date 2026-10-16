#!/usr/bin/env python3
"""Compare a complete delivery tree against a separately pinned expectation.

Derived from delivered-candidate/reproduce/verify_source.py (2026-10-12).
Standard library only; reads files, never imports or executes package contents.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys


MANIFEST_VERSION = "sjia-delivery-files-v1"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class InputError(ValueError):
    """Invalid root, expectation, or pinned manifest identity."""


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def valid_path(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        return False
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in name):
        return False
    path = PurePosixPath(name)
    return (
        not path.is_absolute()
        and all(part not in ("", ".", "..") for part in name.split("/"))
        and path.as_posix() == name
    )


def load_manifest(path, pinned_sha256):
    if not SHA256.fullmatch(pinned_sha256):
        raise InputError("--manifest-sha256 must be 64 lowercase hexadecimal characters")
    if path.is_symlink() or not path.is_file():
        raise InputError("Manifest must be a regular file, not a symbolic link")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != pinned_sha256:
        raise InputError("Manifest SHA256 does not match the independent pin")
    manifest = json.loads(content.decode("utf-8"), object_pairs_hook=unique_object)
    if not isinstance(manifest, dict) or set(manifest) != {"manifest_version", "files"}:
        raise InputError("Manifest must contain exactly manifest_version and files")
    if manifest["manifest_version"] != MANIFEST_VERSION:
        raise InputError(f"Unsupported manifest_version; expected {MANIFEST_VERSION}")
    records = manifest["files"]
    if not isinstance(records, dict) or not records:
        raise InputError("Manifest files must be a nonempty object")
    for name, record in records.items():
        if not valid_path(name):
            raise InputError(f"Unsafe or noncanonical manifest path: {name!r}")
        if not isinstance(record, dict) or set(record) != {"bytes", "sha256", "mode"}:
            raise InputError(f"Expected exactly bytes, sha256, mode for {name}")
        if type(record["bytes"]) is not int or record["bytes"] < 0:
            raise InputError(f"Invalid byte count for {name}")
        if type(record["mode"]) is not int or not 0 <= record["mode"] <= 0o7777:
            raise InputError(f"Invalid POSIX permission mode for {name}")
        if not isinstance(record["sha256"], str) or not SHA256.fullmatch(record["sha256"]):
            raise InputError(f"Invalid SHA256 for {name}")
    return records


def scan_files(root):
    """Reject links and special files without traversing them; include hidden files."""
    files, errors = {}, []

    def visit(directory):
        try:
            with os.scandir(directory) as entries:
                children = sorted(entries, key=lambda item: item.name)
        except OSError as exc:
            errors.append({"code": "unreadable_directory", "path": directory.relative_to(root).as_posix(), "detail": str(exc)})
            return
        for child in children:
            path = Path(child.path)
            name = path.relative_to(root).as_posix()
            try:
                mode = child.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode):
                    errors.append({"code": "symbolic_link", "path": name})
                elif stat.S_ISDIR(mode):
                    visit(path)
                elif stat.S_ISREG(mode):
                    files[name] = path
                else:
                    errors.append({"code": "special_file", "path": name})
            except OSError as exc:
                errors.append({"code": "unreadable_entry", "path": name, "detail": str(exc)})

    visit(root)
    return files, errors


def inspect_file(path):
    """Read an ordinary file and flag changes observed during the read."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise InputError("File is no longer a regular file")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise InputError("File changed before reading")
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    after = path.lstat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mode, s.st_mtime_ns, s.st_ctime_ns)
    if identity(before) != identity(after):
        raise InputError("File changed while reading")
    return {"bytes": size, "sha256": digest.hexdigest(), "mode": stat.S_IMODE(before.st_mode)}


def verify(root, records):
    actual, errors = scan_files(root)
    for name in sorted(records.keys() - actual.keys()):
        errors.append({"code": "missing_file", "path": name})
    for name in sorted(actual.keys() - records.keys()):
        errors.append({"code": "unexpected_file", "path": name})
    verified = 0
    for name in sorted(records.keys() & actual.keys()):
        try:
            observed = inspect_file(actual[name])
        except (OSError, InputError) as exc:
            errors.append({"code": "unreadable_or_changed_file", "path": name, "detail": str(exc)})
            continue
        differences = {key: {"expected": records[name][key], "actual": observed[key]}
                       for key in ("bytes", "sha256", "mode") if records[name][key] != observed[key]}
        if differences:
            errors.append({"code": "file_mismatch", "path": name, "differences": differences})
        else:
            verified += 1
    return {"status": "FAIL" if errors else "PASS", "expected_files": len(records),
            "actual_files": len(actual), "verified_files": verified, "errors": errors}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="Complete, quiescent delivery tree; no ignored files")
    parser.add_argument("--manifest", required=True, type=Path, help="Independent expected manifest outside --root")
    parser.add_argument("--manifest-sha256", required=True, help="SHA256 obtained independently of the tree being checked")
    args = parser.parse_args(argv)
    try:
        if args.root.is_symlink() or not args.root.is_dir():
            raise InputError("Root must be a directory, not a symbolic link")
        root = args.root.resolve(strict=True)
        manifest = args.manifest.resolve(strict=True)
        if manifest.is_relative_to(root):
            raise InputError("Expected manifest must be outside --root")
        records = load_manifest(args.manifest, args.manifest_sha256)
        report = verify(root, records)
        report.update({"scope": "FILE_INTEGRITY_ONLY", "manifest_version": MANIFEST_VERSION,
                       "manifest_sha256": args.manifest_sha256, "root": str(root)})
        exit_code = 0 if report["status"] == "PASS" else 1
    except (OSError, ValueError, RecursionError) as exc:
        report = {"status": "FAIL", "scope": "FILE_INTEGRITY_ONLY", "errors": [{"code": "invalid_input", "detail": str(exc)}]}
        exit_code = 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
