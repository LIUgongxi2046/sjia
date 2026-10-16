"""Pure temporary-file tests: no application imports, services, or databases."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


TOOL = Path(__file__).resolve().parents[1] / "verify_source.py"


class DeliveryIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sjia-file-integrity-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "package"
        (self.root / "bin").mkdir(parents=True)
        self.contents = {"README.txt": b"version=1\n", "bin/check.sh": b"#!/bin/sh\nexit 0\n"}
        self.modes = {"README.txt": 0o644, "bin/check.sh": 0o755}
        for name, content in self.contents.items():
            path = self.root / name
            path.write_bytes(content)
            path.chmod(self.modes[name])
        # Expected data is frozen before any candidate mutations in a test.
        self.expected = {"manifest_version": "sjia-delivery-files-v1", "files": {
            name: {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(), "mode": self.modes[name]}
            for name, content in self.contents.items()}}
        self.manifest = self.base / "expected.json"
        self.write_expectation()

    def write_expectation(self, text=None):
        self.manifest.write_text(text if text is not None else json.dumps(self.expected), encoding="utf-8")
        self.pin = hashlib.sha256(self.manifest.read_bytes()).hexdigest()

    def run_tool(self, expected_code, code=None, optimize=False, **overrides):
        arguments = {"root": self.root, "manifest": self.manifest, "manifest-sha256": self.pin}
        arguments.update(overrides)
        command = [sys.executable] + (["-O"] if optimize else []) + [str(TOOL)]
        for key, value in arguments.items():
            command.extend(["--" + key, str(value)])
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "PASS" if expected_code == 0 else "FAIL")
        if code:
            self.assertIn(code, {error["code"] for error in report["errors"]})
        return report

    def test_correct_complete_package(self):
        report = self.run_tool(0)
        self.assertEqual(report["verified_files"], 2)
        self.assertEqual(report["actual_files"], 2)
        self.assertEqual(report["scope"], "FILE_INTEGRITY_ONLY")

    def test_same_size_single_byte_change(self):
        (self.root / "README.txt").write_bytes(b"version=2\n")
        report = self.run_tool(1, "file_mismatch")
        self.assertEqual(set(report["errors"][0]["differences"]), {"sha256"})

    def test_file_length_change(self):
        (self.root / "README.txt").write_bytes(b"version=1\nextra")
        report = self.run_tool(1, "file_mismatch")
        self.assertEqual(set(report["errors"][0]["differences"]), {"bytes", "sha256"})

    def test_missing_file(self):
        (self.root / "README.txt").unlink()
        self.run_tool(1, "missing_file")

    def test_unexpected_source_file(self):
        (self.root / "injected.py").write_text("print('extra')\n")
        self.run_tool(1, "unexpected_file")

    def test_unexpected_hidden_file(self):
        (self.root / ".hidden").write_text("extra\n")
        self.run_tool(1, "unexpected_file")

    def test_permission_mode_change(self):
        (self.root / "bin/check.sh").chmod(0o644)
        report = self.run_tool(1, "file_mismatch")
        self.assertEqual(set(report["errors"][0]["differences"]), {"mode"})

    def test_bytes_compared_separately_from_digest(self):
        self.expected["files"]["README.txt"]["bytes"] += 1
        self.write_expectation()
        report = self.run_tool(1, "file_mismatch")
        self.assertEqual(set(report["errors"][0]["differences"]), {"bytes"})

    def test_manifest_omission_does_not_hide_extra_file(self):
        del self.expected["files"]["README.txt"]
        self.write_expectation()
        self.run_tool(1, "unexpected_file")

    def test_replaced_manifest_rejected_by_original_pin(self):
        self.manifest.write_text('{"manifest_version":"fake","files":{}}')
        self.run_tool(2, "invalid_input")

    def test_invalid_pin(self):
        self.run_tool(2, "invalid_input", **{"manifest-sha256": "not-a-sha256"})

    def test_unsafe_manifest_paths(self):
        for path in ("../outside.txt", "/outside.txt", "a/../../outside.txt", "a//b", "a/./b", "a/", "C:/outside", "a\\b", "", "a\x00b", "a\x7fb", "a\x80b", "a\x9fb"):
            with self.subTest(path=path):
                self.expected["files"] = {path: {"bytes": 0, "sha256": "0" * 64, "mode": 0o644}}
                self.write_expectation()
                self.run_tool(2, "invalid_input")

    def test_unicode_names_without_control_characters_pass(self):
        name = "说明 café.txt"
        (self.root / "README.txt").rename(self.root / name)
        self.expected["files"][name] = self.expected["files"].pop("README.txt")
        self.write_expectation()
        self.run_tool(0)

    def test_manifest_cannot_be_inside_candidate(self):
        internal = self.root / "expected.json"
        internal.write_bytes(self.manifest.read_bytes())
        self.run_tool(2, "invalid_input", manifest=internal)

    def test_file_symlink_rejected_even_if_matching_target(self):
        outside = self.base / "outside.txt"
        outside.write_bytes(self.contents["README.txt"])
        (self.root / "README.txt").unlink()
        (self.root / "README.txt").symlink_to(outside)
        self.run_tool(1, "symbolic_link")

    def test_directory_symlink_not_traversed(self):
        outside = self.base / "outside"
        (self.root / "bin").rename(outside)
        (self.root / "bin").symlink_to(outside, target_is_directory=True)
        report = self.run_tool(1, "symbolic_link")
        self.assertEqual(report["actual_files"], 1)

    def test_broken_symlink_rejected(self):
        (self.root / "broken").symlink_to(self.base / "not-present")
        self.run_tool(1, "symbolic_link")

    def test_root_symlink_rejected(self):
        alias = self.base / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.run_tool(2, "invalid_input", root=alias)

    def test_manifest_symlink_rejected(self):
        alias = self.base / "expected-alias.json"
        alias.symlink_to(self.manifest)
        self.run_tool(2, "invalid_input", manifest=alias)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO test")
    def test_special_file_rejected_without_reading(self):
        os.mkfifo(self.root / "pipe")
        self.run_tool(1, "special_file")

    def test_duplicate_json_keys_rejected(self):
        self.write_expectation('{"manifest_version":"sjia-delivery-files-v1","files":{},"files":{}}')
        self.run_tool(2, "invalid_input")

    def test_bad_schema_and_types_rejected(self):
        cases = [[], {}, {"manifest_version": "historical", "files": {}},
                 {"manifest_version": "sjia-delivery-files-v1", "files": {}},
                 {**self.expected, "ignore": ["*.py"]}]
        for manifest in cases:
            with self.subTest(manifest=manifest):
                self.write_expectation(json.dumps(manifest))
                self.run_tool(2, "invalid_input")

    def test_invalid_record_fields(self):
        original = dict(self.expected["files"]["README.txt"])
        cases = [{**original, "bytes": True}, {**original, "bytes": -1},
                 {**original, "mode": True}, {**original, "mode": 0o10000},
                 {**original, "sha256": "x" * 64}, {**original, "extra": 1},
                 {"bytes": 10, "sha256": original["sha256"]}]
        for record in cases:
            with self.subTest(record=record):
                self.expected["files"]["README.txt"] = record
                self.write_expectation()
                self.run_tool(2, "invalid_input")

    def test_json_parse_error_is_failure(self):
        self.write_expectation("{")
        self.run_tool(2, "invalid_input")

    def test_missing_root_is_failure(self):
        self.run_tool(2, "invalid_input", root=self.base / "absent")

    def test_missing_manifest_is_failure(self):
        self.run_tool(2, "invalid_input", manifest=self.base / "absent.json")

    def test_python_optimization_cannot_disable_checks(self):
        (self.root / "README.txt").write_bytes(b"version=2\n")
        self.run_tool(1, "file_mismatch", optimize=True)

    def test_portable_root_and_working_directory(self):
        moved = self.base / "new name with spaces"
        self.root.rename(moved)
        self.run_tool(0, root=moved)


if __name__ == "__main__":
    unittest.main()
