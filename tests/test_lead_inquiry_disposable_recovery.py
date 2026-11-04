"""Disposable recovery fixture, manifest and cleanup contract tests."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
import lead_inquiry_disposable_recovery as subject


class DisposableRecoveryTests(unittest.TestCase):
    def test_event_state_fixture_round_trip(self):
        out = subject.run_disposable_recovery()
        self.assertEqual("DISPOSABLE_REHEARSAL_PASSED", out["status"])
        self.assertEqual("EVENT_STATE_V1", out["schema"])
        self.assertEqual({"PENDING": 1, "COMPLETED": 1, "TOTAL": 2},
                         out["manifest"]["artifact_checks"]["source"]["counts"])

    def test_legacy_decision_fixture_round_trip(self):
        out = subject.run_disposable_recovery(schema="LEGACY_DECISION_V1")
        self.assertEqual("DISPOSABLE_REHEARSAL_PASSED", out["status"])
        self.assertEqual({"NEEDS_REVIEW": 1, "READY_FOR_REVIEW": 1, "TOTAL": 2},
                         out["manifest"]["artifact_checks"]["source"]["counts"])

    def test_logical_fingerprints_match_all_three(self):
        checks = subject.run_disposable_recovery()["manifest"]["artifact_checks"]
        self.assertEqual(1, len({v["logical_sha256"] for v in checks.values()}))
        self.assertTrue(all(v["integrity"] == "ok" for v in checks.values()))
        self.assertEqual(64, len(checks["restored"]["logical_sha256"]))

    def test_manifest_deterministic_across_new_workspaces(self):
        first = subject.run_disposable_recovery()
        second = subject.run_disposable_recovery()
        self.assertEqual(first, second)

    def test_no_arbitrary_path_verifier_or_clock_parameter(self):
        for kwargs in ({"source": "/tmp/data"}, {"workspace": "/tmp/work"},
                       {"verifier": lambda *_: True}, {"clock": lambda: 1}):
            with self.subTest(kwargs=list(kwargs)):
                with self.assertRaises(TypeError):
                    subject.run_disposable_recovery(**kwargs)

    def test_unsupported_schema_does_not_allocate_workspace(self):
        with patch.object(subject.tempfile, "TemporaryDirectory",
                          side_effect=AssertionError("must not allocate")):
            out = subject.run_disposable_recovery(schema="OTHER")
        self.assertEqual("UNSUPPORTED_SCHEMA", out["status"])
        self.assertIsNone(out["manifest"])

    def test_non_string_schema_rejected(self):
        for value in (None, 1, True, [], {}):
            with self.subTest(value=value):
                self.assertEqual("UNSUPPORTED_SCHEMA",
                                 subject.run_disposable_recovery(schema=value)["status"])

    def test_workspaces_deleted_after_success(self):
        created = []
        real = tempfile.TemporaryDirectory
        class TrackedDirectory(real):
            def __enter__(self):
                name = super().__enter__()
                created.append(name)
                return name
        with patch.object(subject.tempfile, "TemporaryDirectory", TrackedDirectory):
            result = subject.run_disposable_recovery()
        self.assertEqual("DISPOSABLE_REHEARSAL_PASSED", result["status"])
        self.assertEqual(1, len(created))
        self.assertFalse(Path(created[0]).exists())

    def test_workspaces_deleted_after_recovery_failure(self):
        created = []
        real = tempfile.TemporaryDirectory
        class TrackedDirectory(real):
            def __enter__(self):
                path = super().__enter__()
                created.append(path)
                return path
        with patch.object(subject.tempfile, "TemporaryDirectory", TrackedDirectory):
            with patch.object(subject, "rehearse_synthetic_backup_restore",
                              return_value={"status": "REHEARSAL_FAILED"}):
                result = subject.run_disposable_recovery()
        self.assertEqual("RECOVERY_FAILED", result["status"])
        self.assertIsNone(result["manifest"])
        self.assertFalse(Path(created[0]).exists())

    def test_restore_mismatch_has_no_success_manifest(self):
        original = subject._verify_artifact
        def corrupt_report(path, schema):
            info = original(path, schema)
            if info and Path(path).name == "restored.sqlite":
                info["logical_sha256"] = "0" * 64
            return info
        with patch.object(subject, "_verify_artifact", side_effect=corrupt_report):
            result = subject.run_disposable_recovery()
        self.assertEqual("ARTIFACT_MISMATCH", result["status"])
        self.assertIsNone(result["manifest"])

    def test_injected_io_failure_is_redacted(self):
        with patch.object(subject, "_fixture", side_effect=OSError("secret/file/location")):
            result = subject.run_disposable_recovery()
        self.assertEqual("WORKSPACE_FAILED", result["status"])
        self.assertNotIn("secret", json.dumps(result))
        self.assertIsNone(result["manifest"])

    def test_no_identity_or_path_in_manifest(self):
        rendered = json.dumps(subject.run_disposable_recovery())
        for term in ("synthetic_pending", "synthetic_old", "mock_fixture",
                     "/tmp/", "source.sqlite", "restored.sqlite"):
            self.assertNotIn(term, rendered)

    def test_no_external_dispatch_or_historical_migration(self):
        result = subject.run_disposable_recovery()
        self.assertEqual(0, result["external_dispatch_count"])
        self.assertFalse(result["historical_migration_performed"])
        self.assertFalse(result["workspace_retained"])

    def test_recovery_success_claim_requires_real_rehearsal_status(self):
        for status in ("REHEARSAL_FAILED", "BACKUP_VERIFY_FAILED", "DESTINATION_EXISTS"):
            with self.subTest(status=status):
                with patch.object(subject, "rehearse_synthetic_backup_restore",
                                  return_value={"status": status}):
                    result = subject.run_disposable_recovery()
                self.assertEqual("RECOVERY_FAILED", result["status"])
                self.assertIsNone(result["manifest"])

    def test_programming_assertions_not_silenced(self):
        with patch.object(subject, "_fixture", side_effect=AssertionError("programmer error")):
            with self.assertRaises(AssertionError):
                subject.run_disposable_recovery()


if __name__ == "__main__":
    unittest.main()
