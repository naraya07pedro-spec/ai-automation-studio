"""Preflight policy tests with an explicitly simulated authorization verifier."""
import copy
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from lead_inquiry_durable_state import DurableMockEventStore
from lead_inquiry_pending_review import _event_ref
from lead_inquiry_reconciliation_preflight import evaluate_reconciliation


class ReconciliationPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DurableMockEventStore(Path(self.temp.name) / "mock.sqlite")
        self.store.reserve("mock_inbox", "event-1", {"service": "website"})
        self.now = 1200
        self.proposal = {
            "event_ref": _event_ref("mock_inbox", "event-1"),
            "intent": "ACKNOWLEDGE_UNRESOLVED_MOCK_EVENT",
            "evidence_kind": "MOCK_NO_EXTERNAL_EFFECT",
            "evidence_digest": hashlib.sha256(b"synthetic proof").hexdigest(),
            "issued_at": 1150, "expires_at": 1250, "operator_ref": "mock_operator_1",
        }

    def check(self, proposal=None, verifier=None, *, source="mock_inbox", event_id="event-1", now=None):
        return evaluate_reconciliation(
            self.store, source=source, event_id=event_id,
            proposal=self.proposal if proposal is None else proposal,
            verifier=(lambda *_: True) if verifier is None else verifier,
            now=self.now if now is None else now,
        )

    def test_valid_preflight_does_not_transition(self):
        out = self.check()
        self.assertEqual("PRECHECK_PASSED_NO_TRANSITION", out["preflight_status"])
        self.assertIs(out["state_changed"], False)
        self.assertEqual(0, out["external_dispatch_count"])
        self.assertEqual("PENDING", self.store.state("mock_inbox", "event-1"))

    def test_denied_authorization_blocks(self):
        self.assertEqual("NOT_AUTHORIZED", self.check(verifier=lambda *_: False)["preflight_status"])

    def test_truthy_non_boolean_verifier_is_denied(self):
        self.assertEqual("NOT_AUTHORIZED", self.check(verifier=lambda *_: "yes")["preflight_status"])

    def test_verifier_raises_fail_closed(self):
        def fails(*_):
            raise RuntimeError("unavailable")
        self.assertEqual("AUTHORIZATION_UNAVAILABLE", self.check(verifier=fails)["preflight_status"])

    def test_missing_verifier_blocked(self):
        out = evaluate_reconciliation(self.store, source="mock_inbox", event_id="event-1",
                                      proposal=self.proposal, verifier=None, now=self.now)
        self.assertEqual("INVALID_TRUST_CONTEXT", out["preflight_status"])

    def test_expired_and_future_windows(self):
        p = copy.deepcopy(self.proposal)
        p["expires_at"] = 1199
        self.assertEqual("EXPIRED_OR_INVALID_WINDOW", self.check(p)["preflight_status"])
        p["expires_at"] = 1250
        p["issued_at"] = 1201
        self.assertEqual("EXPIRED_OR_INVALID_WINDOW", self.check(p)["preflight_status"])

    def test_window_over_5_minutes_denied(self):
        p = copy.deepcopy(self.proposal)
        p["issued_at"] = 800
        p["expires_at"] = 1250
        self.assertEqual("EXPIRED_OR_INVALID_WINDOW", self.check(p)["preflight_status"])

    def test_wrong_event_identity_denied(self):
        self.assertEqual("EVENT_MISMATCH", self.check(event_id="different")["preflight_status"])

    def test_unsupported_completion_intent_denied(self):
        p = copy.deepcopy(self.proposal)
        p["intent"] = "MARK_COMPLETED"
        self.assertEqual("UNSUPPORTED_INTENT_OR_EVIDENCE", self.check(p)["preflight_status"])

    def test_non_mock_source_denied(self):
        self.assertEqual("INVALID_EVENT", self.check(source="gmail")["preflight_status"])

    def test_invalid_evidence_digest_denied(self):
        p = copy.deepcopy(self.proposal)
        p["evidence_digest"] = "test"
        self.assertEqual("INVALID_EVIDENCE_DIGEST", self.check(p)["preflight_status"])

    def test_secret_not_in_output(self):
        p = copy.deepcopy(self.proposal)
        p["operator_ref"] = "secret@example.com"
        out = self.check(p)
        self.assertEqual("INVALID_OPERATOR_REFERENCE", out["preflight_status"])
        self.assertNotIn("secret@example.com", json.dumps(out))

    def test_already_completed_is_not_pending(self):
        self.store.mark_mock_completed("mock_inbox", "event-1")
        self.assertEqual("NOT_PENDING", self.check()["preflight_status"])

    def test_storage_failure_blocks_without_leak(self):
        class Broken:
            def state(self, *_):
                raise sqlite3.OperationalError("sensitive-file-location")
        self.store = Broken()
        out = self.check()
        self.assertEqual("STORAGE_UNAVAILABLE", out["preflight_status"])
        self.assertNotIn("sensitive-file-location", json.dumps(out))

    def test_state_change_during_verification_is_detected(self):
        def verifier(*_):
            self.store.mark_mock_completed("mock_inbox", "event-1")
            return True
        self.assertEqual("STALE_STATE", self.check(verifier=verifier)["preflight_status"])

    def test_extra_unreviewed_proposal_fields_denied(self):
        p = copy.deepcopy(self.proposal)
        p["recipient"] = "somewhere"
        self.assertEqual("INVALID_PROPOSAL", self.check(p)["preflight_status"])

    def test_invalid_time_type_denied(self):
        self.assertEqual("INVALID_TRUST_CONTEXT", self.check(now=True)["preflight_status"])


if __name__ == "__main__":
    unittest.main()
