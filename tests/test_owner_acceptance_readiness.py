"""Stage 125 focused privacy/read-only and fail-closed navigation regression."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import owner_acceptance_readiness as readiness

PID = "ab" * 12
CAPS = {key: True for key in readiness.REQUIRED_CAPABILITIES}
ROWS = {
    "signed_handoff": {"plan_id": PID, "signature_valid": True,
                       "provider_asserted_outcome": "COMPLETED",
                       "private_password": "DO-NOT-EXPOSE"},
    "n8n_execution": {"plan_id": PID, "status": "EXECUTION_SUCCESS_CORRELATED",
                      "provider_execution_status": "success",
                      "provider_finished": True, "inbound_delivery_correlated": True},
    "signed_gmail": {"plan_id": PID, "signature_valid_at_import": True,
                     "provider_n8n_execution_correlated_at_import": True},
    "n8n_gmail_output": {"plan_id": PID, "status": "PROVIDER_SEND_NODE_ID_CORRELATED"},
    "gmail_sent": {"plan_id": PID, "status": "SENT_ARTIFACT_MATCHED",
                   "gmail_sent_label_seen": True},
}


def histories():
    return {key: {"records": [dict(value)]} for key, value in ROWS.items()}


class FakeClient:
    def __init__(self, caps=None, pages=None, failure=None):
        self.caps = CAPS if caps is None else caps
        self.pages = histories() if pages is None else pages
        self.failure = failure
        self.paths = []
        self.logged_out = False

    def _login(self, password):
        if not password:
            raise readiness.OwnerLiveAcceptanceError("empty_password")

    def _request(self, path, values=None):
        self.paths.append((path, values))
        if path == self.failure:
            raise readiness.OwnerLiveAcceptanceError("fake_auth_error")
        if path == "/api/local-agent-capabilities":
            return self.caps
        return dict(self.pages)[
            {v: k for k, v in readiness.HISTORY_ENDPOINTS}[path]]

    def _logout(self):
        self.logged_out = True


class ReadinessTests(unittest.TestCase):
    def test_complete_saved_history_is_not_live_provider_verification(self):
        report = readiness.evaluate(CAPS, histories(), PID)
        self.assertEqual(report["status"], "SAVED_CHAIN_PRESENT_RUN_OFFLINE_AUDIT")
        self.assertEqual(report["next_action"],
                         "STOP_STUDIO_AND_RUN_STRICT_NINE_LAYER_OFFLINE_AUDIT")
        self.assertEqual(len(report["stages"]), 5)
        self.assertTrue(all(v == "SAVED_SUCCESS_REPORTED"
                            for v in report["stages"].values()))
        for key in ("saved_record_integrity_audited", "provider_contacted_by_this_command",
                    "automatic_retry", "production_ready", "real_recipient_delivery_verified"):
            self.assertIs(report[key], False)
        self.assertEqual(report["external_write_count"], 0)
        self.assertNotIn(PID, json.dumps(report))
        self.assertNotIn("DO-NOT-EXPOSE", json.dumps(report))

    def test_each_missing_stage_points_only_to_inspection(self):
        for name in readiness.STAGES:
            with self.subTest(name=name):
                pages = histories()
                pages[name] = {"records": []}
                report = readiness.evaluate(CAPS, pages, PID)
                self.assertEqual(report["status"], "NEXT_LOCAL_ACCEPTANCE_STEP")
                self.assertEqual(report["next_action"], readiness.NEXT_ACTIONS[name])
                self.assertIn("NOT_VISIBLE", report["stages"][name])
                self.assertEqual(report["external_write_count"], 0)

    def test_provider_failure_stops_no_retry(self):
        for name, field, bad in (
            ("signed_handoff", "provider_asserted_outcome", "FAILED"),
            ("n8n_execution", "status", "LOOKUP_UNCERTAIN"),
            ("signed_gmail", "signature_valid_at_import", False),
            ("n8n_gmail_output", "status", "PROVIDER_SEND_NODE_NOT_CORRELATED"),
            ("gmail_sent", "gmail_sent_label_seen", False),
        ):
            with self.subTest(name=name):
                pages = histories()
                pages[name]["records"][0][field] = bad
                result = readiness.evaluate(CAPS, pages, PID)
                self.assertEqual(result["status"], "CONFLICT_STOP_NO_RETRY")
                self.assertEqual(result["next_action"], "REVIEW_LOCAL_HISTORY_MANUALLY")

    def test_duplicate_history_conflict(self):
        pages = histories()
        pages["n8n_execution"]["records"].append(dict(ROWS["n8n_execution"]))
        self.assertEqual(readiness.evaluate(CAPS, pages, PID)["status"],
                         "CONFLICT_STOP_NO_RETRY")

    def test_capabilities_fail_closed(self):
        for bad in (None, {}, {**CAPS, "official_n8n_gmail_output_required": False}):
            report = readiness.evaluate(bad, histories(), PID)
            self.assertEqual(report["status"], "CONFIGURATION_BLOCKED")

    def test_invalid_or_truncated_history_fails_closed(self):
        for bad in (None, {}, {"records": None}, {"records": {"a": 1}}):
            pages = histories()
            pages["gmail_sent"] = bad
            self.assertEqual(readiness.evaluate(CAPS, pages, PID)["status"],
                             "HISTORY_UNAVAILABLE")

    def test_no_provider_post_or_remote_hosts_and_no_identifier_in_output(self):
        fake = FakeClient()
        report = readiness.inspect_saved_chain(PID, "secret", client=fake)
        self.assertTrue(fake.logged_out)
        self.assertEqual(report["status"], "SAVED_CHAIN_PRESENT_RUN_OFFLINE_AUDIT")
        self.assertEqual([p for p, _ in fake.paths],
                         ["/api/local-agent-capabilities"] +
                         [endpoint for _, endpoint in readiness.HISTORY_ENDPOINTS])
        self.assertTrue(all(values is None for _, values in fake.paths))

    def test_logout_always_on_local_error(self):
        fake = FakeClient(failure="/api/n8n-gmail-output-witness-history")
        with self.assertRaises(readiness.OwnerLiveAcceptanceError):
            readiness.inspect_saved_chain(PID, "secret", client=fake)
        self.assertTrue(fake.logged_out)

    def test_reject_invalid_plan_before_network(self):
        fake = FakeClient()
        for bad in ("INVALID", "a" * 25, "A" * 24, ""):
            with self.assertRaises(readiness.OwnerLiveAcceptanceError):
                readiness.inspect_saved_chain(bad, "secret", client=fake)
        self.assertEqual(fake.paths, [])

    def test_main_exits_nonzero_on_incomplete_chain_without_leaks(self):
        report = readiness._report("NEXT_LOCAL_ACCEPTANCE_STEP", "INSPECT_OWNER_LIVE_N8N_ACCEPTANCE")
        with patch.object(readiness, "inspect_saved_chain", return_value=report), \
             patch.object(readiness.getpass, "getpass", return_value="secret"), \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            code = readiness.main(["--plan-id", PID])
        self.assertEqual(code, 2)
        parsed = json.loads(output.getvalue())
        self.assertNotIn("secret", output.getvalue())
        self.assertEqual(parsed["external_write_count"], 0)


if __name__ == "__main__":
    unittest.main()
