"""Stage #109 evidence boundary: no paid requests or live claims from CI."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))
import verify_private_cloud_ai_runtime as cloud


def make_fixture(path: Path, *, provider="openai_responses", max_runs=1,
                 attempted=1, usage=None, tools=None, answer="Safe fixture response",
                 status="COMPLETED"):
    path.mkdir(mode=0o700)
    db = sqlite3.connect(path / "readonly-ai-schedules.sqlite3")
    try:
        db.execute("""CREATE TABLE ai_schedules(
            task_id TEXT PRIMARY KEY, max_runs INTEGER, attempted_runs INTEGER,
            provider TEXT)""")
        db.execute("""CREATE TABLE ai_schedule_runs(
            task_id TEXT, answer TEXT, tools_json TEXT, usage_json TEXT,
            finished_utc TEXT, status TEXT)""")
        db.execute("INSERT INTO ai_schedules VALUES (?,?,?,?)",
                   ("private-sensitive-task-id", max_runs, attempted, provider))
        db.execute("INSERT INTO ai_schedule_runs VALUES (?,?,?,?,?,?)",
                   ("private-sensitive-task-id", answer,
                    json.dumps([] if tools is None else tools),
                    json.dumps({"input_tokens":19, "output_tokens":7}
                               if usage is None else usage),
                    "2026-11-07T06:00:00+00:00", status))
        db.commit()
    finally:
        db.close()


class ReadonlyCloudAcceptanceTests(unittest.TestCase):
    def test_receipt_is_redacted_and_single_attempt_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "owner"
            make_fixture(base)
            before = (base / "readonly-ai-schedules.sqlite3").read_bytes()
            receipt = cloud._read_receipt(base)
            self.assertEqual(receipt["provider_label_in_local_db"],
                             "openai_responses")
            self.assertEqual(receipt["usage_tokens_reported_by_local_adapter"],
                             {"input_tokens":19, "output_tokens":7})
            self.assertEqual(receipt["max_runs"], 1)
            self.assertEqual(receipt["attempted_runs"], 1)
            self.assertEqual(len(receipt["task_fingerprint"]), 16)
            self.assertNotIn("private-sensitive-task-id", json.dumps(receipt))
            self.assertNotIn("Safe fixture response", json.dumps(receipt))
            self.assertEqual(before,
                             (base / "readonly-ai-schedules.sqlite3").read_bytes())

    def test_rejects_noncloud_or_multiple_attempts(self):
        for change in [
            {"provider": "local_ollama"},
            {"max_runs": 2},
            {"attempted": 2},
            {"status": "IN_FLIGHT"},
            {"answer": ""},
            {"usage": {"input_tokens": 0, "output_tokens": 7}},
            {"usage": {"input_tokens": 17, "output_tokens": 0}},
            {"tools": ["calculate"]},
        ]:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp) / "owner"
                make_fixture(base, **change)
                self.assertIsNone(cloud._read_receipt(base))

    def test_rejects_symlink_and_missing_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "owner"
            make_fixture(base)
            linked = Path(tmp) / "alias"
            linked.symlink_to(base, target_is_directory=True)
            self.assertIsNone(cloud._read_receipt(linked))
            (base / "readonly-ai-schedules.sqlite3").unlink()
            self.assertIsNone(cloud._read_receipt(base))

    def test_systemctl_failed_lookup_fails_closed(self):
        with patch.object(cloud, "_systemctl", return_value=None):
            report = cloud._check_cloud_process()
        self.assertFalse(any(report.values()))

    def test_false_snapshot_never_claims_success(self):
        with patch.object(cloud, "private_runtime_probe",
                          return_value={"status": "NOT_VERIFIED"}), \
             patch.object(cloud, "_check_cloud_process", return_value={
                 "nonroot_service_identity": True,
                 "live_cloud_only_server_process": True,
                 "root_owned_credential_declared_in_unit": True,
             }), \
             patch.object(cloud, "_read_receipt",
                          return_value={"task_fingerprint": "fake"}), \
             patch.object(cloud.sys, "platform", "linux"), \
             patch.object(cloud.os, "geteuid", return_value=1000):
            result = cloud.assess()
        self.assertEqual(result["status"], "NOT_VERIFIED")
        self.assertIsNone(result["redacted_receipt"])

    def test_successful_mock_remains_review_required_not_provider_verified(self):
        with patch.object(cloud, "private_runtime_probe", return_value={
                 "status": "LIVE_PRIVATE_VPS_SNAPSHOT_PASSED",
             }), patch.object(cloud, "_check_cloud_process", return_value={
                 "nonroot_service_identity": True,
                 "live_cloud_only_server_process": True,
                 "root_owned_credential_declared_in_unit": True,
             }), patch.object(cloud, "_read_receipt", return_value={
                 "task_fingerprint": "mock-id",
             }), patch.object(cloud.sys, "platform", "linux"), \
             patch.object(cloud.os, "geteuid", return_value=1000):
            result = cloud.assess()
        self.assertEqual(result["status"],
                         "CLOUD_RUNTIME_RECEIPT_OBSERVED_REVIEW_REQUIRED")
        self.assertFalse(result["provider_account_and_billing_verified"])
        self.assertFalse(result["tencent_instance_identity_verified"])
        self.assertFalse(result["continuous_24h_availability_verified"])

    def test_cli_exit_code_does_not_hide_missing_proof(self):
        output = io.StringIO()
        with patch.object(cloud, "assess", return_value={
                 "status": "NOT_VERIFIED", "checks": {},
             }), contextlib.redirect_stdout(output):
            self.assertEqual(cloud.main(), 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "NOT_VERIFIED")


if __name__ == "__main__":
    unittest.main()
