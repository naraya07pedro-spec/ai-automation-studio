"""Single-use mock grant contract tests. No live authorization/dispatch."""
import concurrent.futures
import hashlib
import json
import secrets
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from lead_inquiry_durable_state import DurableMockEventStore
from lead_inquiry_pending_review import _event_ref
from lead_inquiry_single_use_grants import MockSingleUseReviewGrants
from lead_inquiry_review_transitions import MockReviewTransitions


class SingleUseGrantsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "mock.sqlite"
        self.store = DurableMockEventStore(self.path)
        self.store.reserve("mock_inbox", "event-one", {"service": "website"})
        self.store.reserve("mock_inbox", "event-two", {"service": "marketing"})
        self.grants = MockSingleUseReviewGrants(self.store)
        self.now = 1200

    def proposal(self, event="event-one", *, expires=1260):
        return {"event_ref": _event_ref("mock_inbox", event),
                "intent": "ACKNOWLEDGE_UNRESOLVED_MOCK_EVENT",
                "evidence_kind": "MOCK_NO_EXTERNAL_EFFECT",
                "evidence_digest": hashlib.sha256(b"synthetic-no-effect").hexdigest(),
                "issued_at": 1180, "expires_at": expires,
                "operator_ref": "test_operator"}

    def issue(self, event="event-one", verifier=lambda *_: True, *, expires=1260):
        return self.grants.issue("mock_inbox", event,
                                 proposal=self.proposal(event, expires=expires),
                                 verifier=verifier, now=self.now)

    def test_issue_and_consume_changes_review_only(self):
        grant = self.issue()
        self.assertEqual("MOCK_GRANT_ISSUED", grant["status"])
        result = self.grants.consume_hold("mock_inbox", "event-one",
                                          token=grant["token"], now=self.now)
        self.assertEqual("ACKNOWLEDGED_HOLD", result["status"])
        self.assertEqual(1, result["review_version"])
        self.assertEqual("PENDING", self.store.state("mock_inbox", "event-one"))
        self.assertFalse(result["event_state_changed"])
        self.assertEqual(0, result["external_dispatch_count"])

    def test_replayed_token_denied_after_restart(self):
        token = self.issue()["token"]
        self.grants.consume_hold("mock_inbox", "event-one", token=token, now=self.now)
        reloaded = MockSingleUseReviewGrants(DurableMockEventStore(self.path))
        self.assertEqual("GRANT_ALREADY_USED",
                         reloaded.consume_hold("mock_inbox", "event-one", token=token, now=self.now)["status"])

    def test_expired_token_cannot_change_state(self):
        token = self.issue(expires=1201)["token"]
        self.assertEqual("GRANT_EXPIRED",
                         self.grants.consume_hold("mock_inbox", "event-one", token=token, now=1201)["status"])
        self.assertEqual("OPEN", MockReviewTransitions(self.store).inspect("mock_inbox", "event-one")["status"])

    def test_denied_authorization_creates_no_grant(self):
        result = self.issue(verifier=lambda *_: False)
        self.assertEqual("NOT_AUTHORIZED", result["status"])
        self.assertNotIn("token", result)

    def test_missing_authorization_verifier_creates_no_grant(self):
        result = self.issue(verifier=None)
        self.assertEqual("INVALID_TRUST_CONTEXT", result["status"])

    def test_token_bound_to_specific_event(self):
        token = self.issue()["token"]
        wrong = self.grants.consume_hold("mock_inbox", "event-two", token=token, now=self.now)
        self.assertEqual("EVENT_MISMATCH", wrong["status"])
        correct = self.grants.consume_hold("mock_inbox", "event-one", token=token, now=self.now)
        self.assertEqual("ACKNOWLEDGED_HOLD", correct["status"])

    def test_random_fabricated_token_rejected(self):
        bad = self.grants.consume_hold("mock_inbox", "event-one",
                                      token=secrets.token_urlsafe(32), now=self.now)
        self.assertEqual("INVALID_GRANT", bad["status"])

    def test_old_version_after_other_review_transition_denied(self):
        token = self.issue()["token"]
        other = MockReviewTransitions(self.store).acknowledge_hold(
            "mock_inbox", "event-one", event_ref=_event_ref("mock_inbox", "event-one"),
            expected_version=0, preflight_status="PRECHECK_PASSED_NO_TRANSITION",
            reviewer_ref="simulated")
        self.assertEqual("ACKNOWLEDGED_HOLD", other["status"])
        self.assertEqual("STALE_VERSION",
                         self.grants.consume_hold("mock_inbox", "event-one", token=token, now=self.now)["status"])

    def test_completed_underlying_event_blocks_grant(self):
        token = self.issue()["token"]
        self.store.mark_mock_completed("mock_inbox", "event-one")
        self.assertEqual("NOT_PENDING",
                         self.grants.consume_hold("mock_inbox", "event-one", token=token, now=self.now)["status"])

    def test_concurrent_same_token_has_one_winner(self):
        token = self.issue()["token"]
        def run(_):
            instance = MockSingleUseReviewGrants(DurableMockEventStore(self.path))
            return instance.consume_hold("mock_inbox", "event-one", token=token, now=self.now)["status"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(run, range(30)))
        self.assertEqual(1, results.count("ACKNOWLEDGED_HOLD"))
        self.assertEqual(29, results.count("GRANT_ALREADY_USED"))
        self.assertEqual("PENDING", self.store.state("mock_inbox", "event-one"))

    def test_two_independent_grants_only_one_can_win(self):
        a, b = self.issue()["token"], self.issue()["token"]
        self.assertNotEqual(a, b)
        self.assertEqual("ACKNOWLEDGED_HOLD", self.grants.consume_hold(
            "mock_inbox", "event-one", token=a, now=self.now)["status"])
        self.assertEqual("STALE_VERSION", self.grants.consume_hold(
            "mock_inbox", "event-one", token=b, now=self.now)["status"])

    def test_only_token_hash_written_to_db(self):
        token = self.issue()["token"]
        with self.store._connect() as db:
            record = db.execute("SELECT token_hash FROM mock_review_grants").fetchone()[0]
        self.assertNotEqual(record, token)
        self.assertEqual(record, hashlib.sha256(token.encode("ascii")).hexdigest())
        self.assertNotIn(token.encode(), self.path.read_bytes())

    def test_no_sensitive_fields_in_consume_result(self):
        token = self.issue()["token"]
        output = self.grants.consume_hold("mock_inbox", "event-one", token=token, now=self.now)
        text = json.dumps(output)
        self.assertNotIn("event-one", text)
        self.assertNotIn(token, text)
        self.assertNotIn("test_operator", text)

    def test_invalid_time_or_token_rejected(self):
        for token, now, expected in [("", 1200, "INVALID_TOKEN"), ("abc", True, "INVALID_TIME")]:
            self.assertEqual(expected, self.grants.consume_hold(
                "mock_inbox", "event-one", token=token, now=now)["status"])

    def test_expired_proposal_cannot_issue(self):
        out = self.issue(expires=1200)
        self.assertEqual("EXPIRED_OR_INVALID_WINDOW", out["status"])
        self.assertNotIn("token", out)


if __name__ == "__main__":
    unittest.main()
