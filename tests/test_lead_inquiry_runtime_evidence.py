"""Evidence registry and simulated SQLite dump drift regressions, mock-only."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from lead_inquiry_runtime_evidence import (
    registry_snapshot, lookup_runtime_evidence, classify_runtime_evidence)
from lead_inquiry_runtime_drift import check_runtime_drift
import lead_inquiry_disposable_recovery as producer
import lead_inquiry_synthetic_recovery as recovery


class RuntimeEvidenceRegistryTests(unittest.TestCase):
    def test_three_exact_observed_matrix_versions_registered(self):
        for python in ("3.11.17", "3.12.16", "3.13.17"):
            with self.subTest(python=python):
                result = lookup_runtime_evidence(python, "3.45.1")
                self.assertEqual("HISTORICAL_SYNTHETIC_CI_MATCH", result["status"])
                self.assertEqual(41792558426, result["evidence"]["run_id"])
                self.assertEqual(267, result["evidence"]["tests"])
                self.assertFalse(result["production_supported"])

    def test_patchlevel_changes_do_not_inherit_observed_evidence(self):
        for python in ("3.11.18", "3.12.17", "3.13.18"):
            with self.subTest(python=python):
                self.assertEqual("UNREGISTERED_RUNTIME",
                                 lookup_runtime_evidence(python, "3.45.1")["status"])

    def test_other_sqlite_version_remains_unregistered(self):
        self.assertEqual("UNREGISTERED_RUNTIME",
                         lookup_runtime_evidence("3.11.17", "3.46.0")["status"])

    def test_non_ubuntu_evidence_is_not_claimed(self):
        self.assertEqual("INVALID_OR_UNSUPPORTED_RUNTIME",
                         lookup_runtime_evidence("3.11.17", "3.45.1",
                                                 os_family="windows")["status"])

    def test_bogus_version_inputs_rejected(self):
        for version in (None, 312, True, "3.12", "3.12.1/secret", "03.12.1", ""):
            with self.subTest(value=version):
                self.assertEqual("INVALID_OR_UNSUPPORTED_RUNTIME",
                                 lookup_runtime_evidence(version, "3.45.1")["status"])

    def test_snapshot_is_detached_from_registry(self):
        a = registry_snapshot()
        a[0]["python"] = "9.99.99"
        b = registry_snapshot()
        self.assertEqual("3.11.17", b[0]["python"])

    def test_current_fixture_and_known_runtime_remain_not_production(self):
        drift = check_runtime_drift()
        # Tests can run on a runner other than the recorded CI combination.
        observed = {"status": drift["status"],
                    "runtime": {"python": "3.12.16", "sqlite": "3.45.1"},
                    "fixtures": drift["fixtures"],
                    "external_dispatch_count": 0}
        out = classify_runtime_evidence(observed)
        self.assertEqual("REGISTERED_AND_CURRENT_FIXTURE_MATCH", out["status"])
        self.assertFalse(out["production_supported"])

    def test_current_fixture_with_new_runtime_is_not_historically_registered(self):
        drift = check_runtime_drift()
        drift["runtime"]["python"] = "3.12.99"
        out = classify_runtime_evidence(drift)
        self.assertEqual("CURRENT_FIXTURE_MATCH_RUNTIME_UNREGISTERED", out["status"])
        self.assertIsNone(out["evidence"])

    def test_failed_current_drift_cannot_reuse_historic_green(self):
        drift = check_runtime_drift()
        drift["runtime"]["python"] = "3.12.16"
        drift["runtime"]["sqlite"] = "3.45.1"
        drift["status"] = "RUNTIME_DRIFT_DETECTED"
        out = classify_runtime_evidence(drift)
        self.assertEqual("CURRENT_FIXTURE_NOT_VERIFIED", out["status"])
        self.assertIsNone(out["evidence"])

    def test_one_failed_fixture_denies_registry_promotion(self):
        drift = check_runtime_drift()
        drift["fixtures"]["LEGACY_DECISION_V1"] = "DRIFT_OR_RECOVERY_FAILURE"
        self.assertEqual("CURRENT_FIXTURE_NOT_VERIFIED",
                         classify_runtime_evidence(drift)["status"])

    def test_missing_report_fields_deny_promotion(self):
        for value in (None, [], {}, {"status": "RUNTIME_FIXTURE_VERIFIED"}):
            with self.subTest(value=value):
                self.assertEqual("CURRENT_FIXTURE_NOT_VERIFIED",
                                 classify_runtime_evidence(value)["status"])

    def test_simulated_logical_dump_drift_fails_closed(self):
        original = recovery._fingerprint
        def changed_dump(db):
            return "0" * 64
        # Low-level comparison remains internally consistent; pinned
        # reference must still reject the changed dump interpretation.
        with patch.object(recovery, "_fingerprint", changed_dump):
            with patch.object(producer, "_fingerprint", changed_dump):
                out = check_runtime_drift()
        self.assertEqual("RUNTIME_DRIFT_DETECTED", out["status"])
        self.assertEqual("DRIFT_OR_RECOVERY_FAILURE",
                         out["fixtures"]["EVENT_STATE_V1"])
        self.assertEqual("DRIFT_OR_RECOVERY_FAILURE",
                         out["fixtures"]["LEGACY_DECISION_V1"])

    def test_drift_does_not_repin_static_reference(self):
        import lead_inquiry_manifest_verifier as verifier
        before = copy.deepcopy(verifier._EXPECTED)
        with patch.object(producer, "_fingerprint", return_value="f" * 64):
            self.assertEqual("RUNTIME_DRIFT_DETECTED", check_runtime_drift()["status"])
        self.assertEqual(before, verifier._EXPECTED)

    def test_outputs_contain_no_customer_or_environment_paths(self):
        out = classify_runtime_evidence(check_runtime_drift())
        rendered = json.dumps(out)
        for forbidden in (str(ROOT), "source.sqlite", "synthetic_old", "secret"):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(0, out["external_dispatch_count"])


if __name__ == "__main__":
    unittest.main()
