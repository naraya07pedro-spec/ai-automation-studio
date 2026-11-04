"""Grant issuer trust-boundary checks, mock-only; no real IAM claims."""
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
from lead_inquiry_review_transitions import MockReviewTransitions
from lead_inquiry_single_use_grants import MockSingleUseReviewGrants


class MockGrantIssuerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.sqlite"
        self.store = DurableMockEventStore(self.path)
        self.store.reserve("mock_inbox", "event-one", {"service": "website"})
        self.store.reserve("mock_inbox", "event-two", {"service": "marketing"})
        self.now = 1200
        self.allowed = True
        self.grants = self.new_grants()

    def new_grants(self):
        return MockSingleUseReviewGrants(
            DurableMockEventStore(self.path),
            authority=lambda *_: self.allowed, clock=lambda: self.now)

    @staticmethod
    def proposal(event="event-one"):
        return {
            "event_ref": _event_ref("mock_inbox", event),
            "intent": "ACKNOWLEDGE_UNRESOLVED_MOCK_EVENT",
            "evidence_kind": "MOCK_NO_EXTERNAL_EFFECT",
            "evidence_digest": hashlib.sha256(b"synthetic evidence").hexdigest(),
            "operator_ref": "test_operator",
        }

    def issue(self, event="event-one", proposal=None):
        return self.grants.issue("mock_inbox", event,
                                 proposal=self.proposal(event) if proposal is None else proposal)

    def consume(self, token, event="event-one", grants=None):
        return (grants or self.grants).consume_hold("mock_inbox", event, token=token)

    def test_valid_issue_and_consume_leaves_event_pending(self):
        grant = self.issue()
        self.assertEqual("MOCK_GRANT_ISSUED", grant["status"])
        result = self.consume(grant["token"])
        self.assertEqual("ACKNOWLEDGED_HOLD", result["status"])
        self.assertEqual(1, result["review_version"])
        self.assertFalse(result["event_state_changed"])
        self.assertEqual(0, result["external_dispatch_count"])
        self.assertEqual("PENDING", self.store.state("mock_inbox", "event-one"))

    def test_token_replay_after_restart_is_denied(self):
        token = self.issue()["token"]
        self.consume(token)
        self.assertEqual("GRANT_ALREADY_USED", self.consume(token, grants=self.new_grants())["status"])

    def test_expiry_uses_owned_clock(self):
        token = self.issue()["token"]
        self.now += 60
        self.assertEqual("GRANT_EXPIRED", self.consume(token)["status"])
        self.assertEqual("OPEN", MockReviewTransitions(self.store).inspect("mock_inbox", "event-one")["status"])

    def test_verifier_denial_blocks_issue(self):
        self.allowed = False
        self.assertEqual("NOT_AUTHORIZED", self.issue()["status"])

    def test_unconfigured_authority_is_default_deny(self):
        unconfigured = MockSingleUseReviewGrants(self.store)
        self.assertEqual("ISSUER_NOT_CONFIGURED",
                         unconfigured.issue("mock_inbox", "event-one", proposal=self.proposal())["status"])

    def test_no_clock_is_default_deny_for_consume(self):
        token = self.issue()["token"]
        missing_clock = MockSingleUseReviewGrants(self.store, authority=lambda *_: True)
        self.assertEqual("TRUSTED_CLOCK_UNAVAILABLE",
                         missing_clock.consume_hold("mock_inbox", "event-one", token=token)["status"])

    def test_request_cannot_supply_verifier_or_time(self):
        with self.assertRaises(TypeError):
            self.grants.issue("mock_inbox", "event-one", proposal=self.proposal(),
                              verifier=lambda *_: True, now=0)
        token = self.issue()["token"]
        with self.assertRaises(TypeError):
            self.grants.consume_hold("mock_inbox", "event-one", token=token, now=0)

    def test_proposal_cannot_supply_lifetime(self):
        proposal = self.proposal()
        proposal.update({"issued_at": 1, "expires_at": 999999999999})
        outcome = self.issue(proposal=proposal)
        self.assertEqual("INVALID_PROPOSAL", outcome["status"])
        self.assertNotIn("token", outcome)

    def test_cross_event_token_is_rejected(self):
        token = self.issue()["token"]
        self.assertEqual("EVENT_MISMATCH", self.consume(token, "event-two")["status"])
        self.assertEqual("ACKNOWLEDGED_HOLD", self.consume(token)["status"])

    def test_random_token_is_rejected(self):
        self.assertEqual("INVALID_GRANT", self.consume(secrets.token_urlsafe(32))["status"])

    def test_two_independent_grants_only_one_can_change_review(self):
        a = self.issue()["token"]
        b = self.issue()["token"]
        self.assertNotEqual(a, b)
        self.assertEqual("ACKNOWLEDGED_HOLD", self.consume(a)["status"])
        self.assertEqual("STALE_VERSION", self.consume(b)["status"])

    def test_concurrent_consumers_one_winner(self):
        token = self.issue()["token"]
        def action(_):
            return self.consume(token, grants=self.new_grants())["status"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            statuses = list(pool.map(action, range(30)))
        self.assertEqual(1, statuses.count("ACKNOWLEDGED_HOLD"))
        self.assertEqual(29, statuses.count("GRANT_ALREADY_USED"))

    def test_completed_source_event_denies_token(self):
        token = self.issue()["token"]
        self.store.mark_mock_completed("mock_inbox", "event-one")
        self.assertEqual("NOT_PENDING", self.consume(token)["status"])

    def test_legacy_preflight_bypass_is_rejected(self):
        token = self.issue()["token"]
        legacy = MockReviewTransitions(self.store).acknowledge_hold(
            "mock_inbox", "event-one", event_ref=_event_ref("mock_inbox", "event-one"),
            expected_version=0, preflight_status="PRECHECK_PASSED_NO_TRANSITION",
            reviewer_ref="spoofed")
        self.assertEqual("LEGACY_PATH_DISABLED", legacy["status"])
        self.assertEqual("ACKNOWLEDGED_HOLD", self.consume(token)["status"])

    def test_only_token_digest_persisted(self):
        token = self.issue()["token"]
        with self.store._connect() as db:
            digest = db.execute("SELECT token_hash FROM mock_review_grants").fetchone()[0]
        self.assertEqual(hashlib.sha256(token.encode("ascii")).hexdigest(), digest)
        self.assertNotIn(token.encode("ascii"), self.path.read_bytes())

    def test_consume_response_is_redacted(self):
        token = self.issue()["token"]
        result = json.dumps(self.consume(token))
        for secret in (token, "event-one", "test_operator"):
            self.assertNotIn(secret, result)

    def test_invalid_token_rejected(self):
        self.assertEqual("INVALID_TOKEN", self.consume("")["status"])

    def test_clock_exception_or_type_fails_closed(self):
        store = self.store
        def failed():
            raise RuntimeError("clock unavailable secret")
        for clock in (failed, lambda: True, lambda: -1):
            grants = MockSingleUseReviewGrants(store, authority=lambda *_: True, clock=clock)
            self.assertEqual("TRUSTED_CLOCK_UNAVAILABLE",
                             grants.issue("mock_inbox", "event-one", proposal=self.proposal())["status"])

    def test_clock_error_during_consumption_fails_closed(self):
        token = self.issue()["token"]
        clock_bad = MockSingleUseReviewGrants(self.store, clock=lambda: "forged")
        self.assertEqual("TRUSTED_CLOCK_UNAVAILABLE",
                         self.consume(token, grants=clock_bad)["status"])
        self.assertEqual("ACKNOWLEDGED_HOLD", self.consume(token)["status"])

    def test_unconfigured_issuer_cannot_emit_random_token(self):
        issuer = MockSingleUseReviewGrants(self.store)
        response = issuer.issue("mock_inbox", "event-one", proposal=self.proposal())
        self.assertNotIn("token", response)

    def test_authority_exception_blocks_issue(self):
        def fail(*_):
            raise RuntimeError("sensitive auth error")
        issuer = MockSingleUseReviewGrants(self.store, authority=fail, clock=lambda: 1200)
        result = issuer.issue("mock_inbox", "event-one", proposal=self.proposal())
        self.assertEqual("AUTHORIZATION_UNAVAILABLE", result["status"])
        self.assertNotIn("sensitive", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
