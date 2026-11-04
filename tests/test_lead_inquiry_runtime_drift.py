"""Fail-closed runtime drift gate tests; synthetic fixture data only."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from lead_inquiry_runtime_drift import check_runtime_drift
from lead_inquiry_disposable_recovery import run_disposable_recovery
from lead_inquiry_manifest_verifier import verify_fixed_fixture_manifest


class RuntimeDriftGateTests(unittest.TestCase):
    def fake_runtime(self):
        return {"status": "UNTESTED_PYTHON", "python": "3.13.0",
                "sqlite": "3.46.0", "external_dispatch_count": 0}

    def test_actual_runtime_both_pinned_fixtures(self):
        out = check_runtime_drift()
        self.assertEqual("RUNTIME_FIXTURE_VERIFIED", out["status"])
        self.assertEqual({
            "EVENT_STATE_V1": "PINNED_REFERENCE_MATCH",
            "LEGACY_DECISION_V1": "PINNED_REFERENCE_MATCH"}, out["fixtures"])

    def test_screen_unverified_but_actual_hashes_valid(self):
        out = check_runtime_drift(runtime_probe=self.fake_runtime)
        self.assertEqual("RUNTIME_FIXTURE_VERIFIED", out["status"])
        self.assertEqual("UNTESTED_PYTHON", out["runtime"]["screen"])

    def test_all_three_artifacts_consistently_forged_is_rejected(self):
        def altered(*, schema):
            out = run_disposable_recovery(schema=schema)
            for item in out["manifest"]["artifact_checks"].values():
                item["logical_sha256"] = "0" * 64
            return out
        out = check_runtime_drift(runner=altered, runtime_probe=self.fake_runtime)
        # Independent verifier must reject the forged reference.
        self.assertEqual("RUNTIME_DRIFT_DETECTED", out["status"])
        self.assertEqual("DRIFT_OR_RECOVERY_FAILURE",
                         out["fixtures"]["EVENT_STATE_V1"])

    def test_one_schema_failure_does_not_mask_other_schema(self):
        def fail_legacy(*, schema):
            if schema == "LEGACY_DECISION_V1":
                return {"status": "FIXTURE_REFERENCE_MISMATCH", "schema": schema}
            return run_disposable_recovery(schema=schema)
        out = check_runtime_drift(runner=fail_legacy, runtime_probe=self.fake_runtime)
        self.assertEqual("RUNTIME_DRIFT_DETECTED", out["status"])
        self.assertEqual("PINNED_REFERENCE_MATCH", out["fixtures"]["EVENT_STATE_V1"])
        self.assertEqual("DRIFT_OR_RECOVERY_FAILURE", out["fixtures"]["LEGACY_DECISION_V1"])

    def test_false_success_label_is_not_accepted(self):
        def bogus(*, schema):
            return {"status": "DISPOSABLE_REHEARSAL_PASSED", "schema": schema}
        out = check_runtime_drift(runner=bogus, runtime_probe=self.fake_runtime)
        self.assertEqual("RUNTIME_DRIFT_DETECTED", out["status"])

    def test_invalid_runtime_result_fails_closed(self):
        for bad in (None, {}, {"status": "UNTESTED_PYTHON"},
                    {"status": "BASELINE_ELIGIBLE_NOT_PROVEN", "python": 311,
                     "sqlite": "3.45.1", "external_dispatch_count": 0}):
            with self.subTest(bad=bad):
                out = check_runtime_drift(runtime_probe=lambda: bad)
                self.assertEqual("INVALID_RUNTIME", out["status"])
                self.assertIsNone(out["fixtures"])

    def test_verifier_error_is_a_failure(self):
        def broken(_):
            raise RuntimeError("secret runtime path")
        out = check_runtime_drift(verifier=broken, runtime_probe=self.fake_runtime)
        self.assertEqual("RUNTIME_DRIFT_DETECTED", out["status"])
        self.assertNotIn("secret", json.dumps(out))

    def test_false_boolean_verdict_rejected(self):
        out = check_runtime_drift(
            verifier=lambda _: {"valid": 1, "status": "FIXED_FIXTURE_MATCH",
                                "external_dispatch_count": 0},
            runtime_probe=self.fake_runtime)
        self.assertEqual("RUNTIME_DRIFT_DETECTED", out["status"])

    def test_bad_verifier_status_rejected(self):
        out = check_runtime_drift(
            verifier=lambda _: {"valid": True, "status": "MAYBE",
                                "external_dispatch_count": 0},
            runtime_probe=self.fake_runtime)
        self.assertEqual("RUNTIME_DRIFT_DETECTED", out["status"])

    def test_gate_output_never_includes_paths_or_customer_fields(self):
        out = check_runtime_drift()
        rendered = json.dumps(out)
        for term in (str(ROOT), "synthetic_pending", "synthetic_old", "source.sqlite"):
            self.assertNotIn(term, rendered)

    def test_no_external_dispatch(self):
        out = check_runtime_drift()
        self.assertEqual(0, out["external_dispatch_count"])

    def test_both_schemas_checked_even_after_first_fails(self):
        called = []
        def runner(*, schema):
            called.append(schema)
            return {"status": "FAILED"}
        check_runtime_drift(runner=runner, runtime_probe=self.fake_runtime)
        self.assertEqual(["EVENT_STATE_V1", "LEGACY_DECISION_V1"], called)


if __name__ == "__main__":
    unittest.main()
