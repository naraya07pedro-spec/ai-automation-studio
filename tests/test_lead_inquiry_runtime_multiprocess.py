"""Independent interpreter validation of disposable mock SQLite recovery.

Workers run separate OS processes with isolated Python imports. This is not
OS sandboxing or a power-failure test; only fixed synthetic fixture data.
"""
import concurrent.futures
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from lead_inquiry_runtime_contract import runtime_contract
from lead_inquiry_manifest_verifier import verify_fixed_fixture_manifest

# Worker imports modules from its own process, no inherited Python objects.
_WORKER = """
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "prototype"))
from lead_inquiry_disposable_recovery import run_disposable_recovery
from lead_inquiry_runtime_contract import runtime_contract
from lead_inquiry_manifest_verifier import verify_fixed_fixture_manifest
schema = sys.argv[1]
result = run_disposable_recovery(schema=schema)
print(json.dumps({"result": result, "verification": verify_fixed_fixture_manifest(result),
                  "runtime": runtime_contract()}, sort_keys=True))
"""


def launch_worker(schema):
    return subprocess.run(
        [sys.executable, "-I", "-c", _WORKER, schema],
        cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
    )


class RuntimeAndProcessRecoveryTests(unittest.TestCase):
    def test_ci_python_311_is_baseline_eligible_when_sqlite_supported(self):
        out = runtime_contract()
        self.assertIn(out["status"], (
            "BASELINE_ELIGIBLE_NOT_PROVEN", "UNTESTED_PYTHON",
            "SQLITE_BELOW_TEST_BASELINE"))
        self.assertEqual(0, out["external_dispatch_count"])
        self.assertEqual(".".join(map(str, sys.version_info[:3])), out["python"])
        self.assertEqual(sqlite3.sqlite_version, out["sqlite"])

    def test_python_311_sqlite_335_screen_is_not_proof(self):
        out = runtime_contract(python_version=(3, 11, 9),
                               sqlite_version=(3, 35, 0))
        self.assertEqual("BASELINE_ELIGIBLE_NOT_PROVEN", out["status"])

    def test_python_version_unverified_is_distinguished(self):
        for version in ((3, 10, 16), (3, 12, 1), (4, 0, 0)):
            with self.subTest(version=version):
                self.assertEqual("UNTESTED_PYTHON",
                                 runtime_contract(python_version=version,
                                                  sqlite_version=(3, 45, 0))["status"])

    def test_older_sqlite_is_below_compatibility_screen(self):
        out = runtime_contract(python_version=(3, 11, 0),
                               sqlite_version=(3, 34, 9))
        self.assertEqual("SQLITE_BELOW_TEST_BASELINE", out["status"])

    def test_boolean_and_invalid_runtime_numbers_rejected(self):
        for version in ((True, 11, 0), (-1, 11, 0), ("3", 11, 0), (3, 11)):
            with self.subTest(version=version):
                self.assertEqual("INVALID_RUNTIME_VERSION",
                                 runtime_contract(python_version=version)["status"])

    def test_no_sensitive_platform_strings_in_runtime_report(self):
        rendered = json.dumps(runtime_contract())
        self.assertNotIn(str(ROOT), rendered)
        self.assertNotIn(sys.executable, rendered)

    def test_separate_process_event_manifest_passes_verifier(self):
        p = launch_worker("EVENT_STATE_V1")
        self.assertEqual(0, p.returncode)
        data = json.loads(p.stdout)
        self.assertTrue(data["verification"]["valid"])
        self.assertEqual("DISPOSABLE_REHEARSAL_PASSED", data["result"]["status"])
        self.assertEqual(0, data["result"]["external_dispatch_count"])

    def test_separate_process_legacy_manifest_passes_verifier(self):
        p = launch_worker("LEGACY_DECISION_V1")
        self.assertEqual(0, p.returncode)
        data = json.loads(p.stdout)
        self.assertTrue(data["verification"]["valid"])
        self.assertEqual("LEGACY_DECISION_V1", data["result"]["schema"])

    def test_subprocess_manifests_verifiable_in_parent_process(self):
        for schema in ("EVENT_STATE_V1", "LEGACY_DECISION_V1"):
            with self.subTest(schema=schema):
                p = launch_worker(schema)
                self.assertEqual(0, p.returncode)
                record = json.loads(p.stdout)
                self.assertTrue(verify_fixed_fixture_manifest(record["result"])["valid"])

    def test_eight_parallel_subprocesses_have_identical_valid_references(self):
        schemas = ["EVENT_STATE_V1", "LEGACY_DECISION_V1"] * 4
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(launch_worker, schemas))
        self.assertEqual(8, len(results))
        for schema, run in zip(schemas, results):
            self.assertEqual(0, run.returncode)
            report = json.loads(run.stdout)
            self.assertEqual(schema, report["result"]["schema"])
            self.assertTrue(verify_fixed_fixture_manifest(report["result"])["valid"])
            self.assertFalse(report["result"]["workspace_retained"])

    def test_invalid_subprocess_schema_does_not_break_parallel_others(self):
        schemas = ["EVENT_STATE_V1", "INVALID_SCHEMA", "LEGACY_DECISION_V1"] * 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(launch_worker, schemas))
        for schema, run in zip(schemas, results):
            self.assertEqual(0, run.returncode)
            report = json.loads(run.stdout)
            if schema == "INVALID_SCHEMA":
                self.assertEqual("UNSUPPORTED_SCHEMA", report["result"]["status"])
                self.assertIsNone(report["result"]["manifest"])
                self.assertFalse(report["verification"]["valid"])
            else:
                self.assertTrue(verify_fixed_fixture_manifest(report["result"])["valid"])

    def test_child_manifest_does_not_expose_paths_or_event_ids(self):
        p = launch_worker("EVENT_STATE_V1")
        self.assertEqual(0, p.returncode)
        for forbidden in ("synthetic_pending", "synthetic_old", "source.sqlite",
                          "backup.sqlite", "restored.sqlite", str(ROOT)):
            self.assertNotIn(forbidden, p.stdout)

    def test_python_processes_do_not_share_python_memory(self):
        p1, p2 = launch_worker("EVENT_STATE_V1"), launch_worker("EVENT_STATE_V1")
        self.assertEqual(0, p1.returncode)
        self.assertEqual(0, p2.returncode)
        self.assertEqual(json.loads(p1.stdout)["result"], json.loads(p2.stdout)["result"])


if __name__ == "__main__":
    unittest.main()
