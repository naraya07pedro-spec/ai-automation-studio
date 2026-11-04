"""Pinned-reference checks and concurrent disposable recovery regression tests."""
import concurrent.futures
import copy
import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
import lead_inquiry_disposable_recovery as producer
from lead_inquiry_manifest_verifier import verify_fixed_fixture_manifest as verify


class IndependentRecoveryManifestTests(unittest.TestCase):
    def setUp(self):
        self.event = producer.run_disposable_recovery()
        self.legacy = producer.run_disposable_recovery(schema="LEGACY_DECISION_V1")

    def test_both_reference_manifests_match(self):
        for output in (self.event, self.legacy):
            with self.subTest(schema=output["schema"]):
                self.assertEqual("DISPOSABLE_REHEARSAL_PASSED", output["status"])
                self.assertEqual({"valid": True, "status": "FIXED_FIXTURE_MATCH",
                                  "external_dispatch_count": 0}, verify(output))

    def test_pinned_event_fixture_digest(self):
        self.assertEqual(
            "91165b3bbadd107ee90746aa8f6068559d27d30cd85b1dfb00622be3f2adcaa3",
            self.event["manifest"]["artifact_checks"]["source"]["logical_sha256"])

    def test_pinned_legacy_fixture_digest(self):
        self.assertEqual(
            "bb09ca11bb7eff5021012e106c54a820fdad06ae4797902f5d149a6081100036",
            self.legacy["manifest"]["artifact_checks"]["source"]["logical_sha256"])

    def test_equal_but_forged_all_three_hashes_are_denied(self):
        forged = copy.deepcopy(self.event)
        for entry in forged["manifest"]["artifact_checks"].values():
            entry["logical_sha256"] = "1" * 64
        self.assertEqual("FIXTURE_REFERENCE_MISMATCH", verify(forged)["status"])

    def test_all_three_changed_counts_are_denied(self):
        forged = copy.deepcopy(self.event)
        for entry in forged["manifest"]["artifact_checks"].values():
            entry["counts"] = {"PENDING": 0, "COMPLETED": 2, "TOTAL": 2}
        self.assertFalse(verify(forged)["valid"])

    def test_cross_schema_mislabel_is_denied(self):
        forged = copy.deepcopy(self.event)
        forged["schema"] = "LEGACY_DECISION_V1"
        self.assertFalse(verify(forged)["valid"])

    def test_success_label_alone_is_insufficient(self):
        for patch in ({"manifest": None}, {"historical_migration_performed": True},
                      {"workspace_retained": True}, {"external_dispatch_count": 1}):
            with self.subTest(patch=patch):
                forged = copy.deepcopy(self.event)
                forged.update(patch)
                self.assertFalse(verify(forged)["valid"])

    def test_boolean_instead_of_integer_is_denied(self):
        forged = copy.deepcopy(self.event)
        forged["manifest"]["artifact_checks"]["source"]["counts"]["PENDING"] = True
        self.assertFalse(verify(forged)["valid"])
        forged = copy.deepcopy(self.event)
        forged["manifest"]["manifest_version"] = True
        self.assertFalse(verify(forged)["valid"])

    def test_missing_or_extra_artifact_key_denied(self):
        forged = copy.deepcopy(self.event)
        del forged["manifest"]["artifact_checks"]["backup"]
        self.assertFalse(verify(forged)["valid"])
        forged = copy.deepcopy(self.event)
        forged["manifest"]["artifact_checks"]["unexpected"] = {}
        self.assertFalse(verify(forged)["valid"])

    def test_downgraded_integrity_is_denied(self):
        forged = copy.deepcopy(self.event)
        forged["manifest"]["artifact_checks"]["restored"]["integrity"] = "failed"
        self.assertFalse(verify(forged)["valid"])

    def test_manifest_verification_does_not_echo_secret(self):
        forged = copy.deepcopy(self.event)
        forged["manifest"]["SECRET_CUSTOMER_KEY"] = "secret"
        out = verify(forged)
        self.assertFalse(out["valid"])
        self.assertNotIn("SECRET_CUSTOMER_KEY", json.dumps(out))

    def test_producer_rejects_consistent_but_mutated_synthetic_fixture(self):
        original = producer._fixture
        def altered_fixture(path, schema):
            original(path, schema)
            with sqlite3.connect(path) as db:
                if schema == "EVENT_STATE_V1":
                    db.execute("UPDATE mock_events SET state='COMPLETED' "
                               "WHERE event_id='synthetic_pending'")
        with patch.object(producer, "_fixture", altered_fixture):
            result = producer.run_disposable_recovery()
        self.assertEqual("FIXTURE_REFERENCE_MISMATCH", result["status"])
        self.assertIsNone(result["manifest"])

    def test_concurrent_recoveries_both_schemas_isolated(self):
        cases = (["EVENT_STATE_V1", "LEGACY_DECISION_V1"] * 10)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            outputs = list(pool.map(lambda schema: producer.run_disposable_recovery(schema=schema), cases))
        self.assertEqual(20, len(outputs))
        for schema, result in zip(cases, outputs):
            self.assertEqual(schema, result["schema"])
            self.assertTrue(verify(result)["valid"])
            self.assertFalse(result["workspace_retained"])

    def test_failure_of_one_concurrent_task_does_not_invalidate_others(self):
        # Invalid request must fail without allocating workspace, while
        # independently running valid fixture recoveries still pass.
        cases = ["EVENT_STATE_V1", "BOGUS", "LEGACY_DECISION_V1"] * 5
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            outputs = list(pool.map(lambda schema: producer.run_disposable_recovery(schema=schema), cases))
        for schema, result in zip(cases, outputs):
            if schema == "BOGUS":
                self.assertEqual("UNSUPPORTED_SCHEMA", result["status"])
                self.assertIsNone(result["manifest"])
            else:
                self.assertTrue(verify(result)["valid"])

    def test_untrusted_malformed_report_never_crashes_verifier(self):
        for malformed in (None, 4, [], "success", {}, {"status": "success"}):
            with self.subTest(value=malformed):
                self.assertFalse(verify(malformed)["valid"])


if __name__ == "__main__":
    unittest.main()
