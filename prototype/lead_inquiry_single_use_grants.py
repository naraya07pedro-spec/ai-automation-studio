"""Single-use *mock* review grants consumed atomically with review hold.

This is a scoped security experiment, NOT a real authorization service.
The injected verifier is trusted only in tests and must never be caller-controlled
in a deployed API. The underlying mock_events record always stays PENDING.
"""
import hashlib
import secrets

from lead_inquiry_pending_review import _event_ref
from lead_inquiry_reconciliation_preflight import evaluate_reconciliation
from lead_inquiry_review_transitions import MockReviewTransitions


class MockSingleUseReviewGrants:
    def __init__(self, store, *, authority=None, clock=None):
        """Authority and clock are installed by a trusted composition root.

        Default-deny: without both, no grant can be issued or consumed.
        Caller-supplied authority/clock at the API boundary remains forbidden.
        """
        self._store = store
        self._authority = authority
        self._clock = clock
        self._reviews = MockReviewTransitions(store)
        with store._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS mock_review_grants ("
                "token_hash TEXT PRIMARY KEY, source TEXT NOT NULL,"
                "event_id TEXT NOT NULL, expected_version INTEGER NOT NULL,"
                "expires_at INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0 "
                "CHECK(used IN (0,1)))"
            )

    @staticmethod
    def _out(status, version=None):
        return {"status": status, "review_version": version,
                "event_state_changed": False, "external_dispatch_count": 0}

    def _trusted_now(self):
        if not callable(self._clock):
            return None
        try:
            current = self._clock()
        except Exception:
            return None
        return current if type(current) is int and current >= 0 else None

    def issue(self, source, event_id, *, proposal):
        """Issue a short-lived mock grant using only constructor-owned policy.

        Proposal may contain event_ref, intent, evidence_kind, evidence_digest
        and operator_ref. It cannot specify a verifier, clock or lifetime.
        """
        if not callable(self._authority) or not callable(self._clock):
            return self._out("ISSUER_NOT_CONFIGURED")
        if not isinstance(proposal, dict) or set(proposal) != {
            "event_ref", "intent", "evidence_kind", "evidence_digest",
            "operator_ref",
        }:
            return self._out("INVALID_PROPOSAL")
        now = self._trusted_now()
        if now is None:
            return self._out("TRUSTED_CLOCK_UNAVAILABLE")
        scoped = dict(proposal)
        scoped["issued_at"] = now
        scoped["expires_at"] = now + 60
        verdict = evaluate_reconciliation(
            self._store, source=source, event_id=event_id,
            proposal=scoped, verifier=self._authority, now=now,
        )
        if verdict["preflight_status"] != "PRECHECK_PASSED_NO_TRANSITION":
            return self._out(verdict["preflight_status"])
        inspected = self._reviews.inspect(source, event_id)
        if inspected["status"] != "OPEN" or inspected["version"] != 0:
            return self._out("REVIEW_NOT_OPEN")
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._store._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                pending = db.execute(
                    "SELECT state FROM mock_events WHERE source=? AND event_id=?",
                    (source, event_id),
                ).fetchone()
                review = db.execute(
                    "SELECT state,version FROM mock_review_transitions "
                    "WHERE source=? AND event_id=?", (source, event_id),
                ).fetchone()
                if pending != ("PENDING",) or review is not None:
                    db.execute("COMMIT")
                    return self._out("STALE_STATE")
                db.execute(
                    "INSERT INTO mock_review_grants "
                    "(token_hash,source,event_id,expected_version,expires_at) "
                    "VALUES (?,?,?,?,?)",
                    (digest, source, event_id, 0, scoped["expires_at"]),
                )
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        result = self._out("MOCK_GRANT_ISSUED")
        result["token"] = token
        return result

    def consume_hold(self, source, event_id, *, token):
        """Single transaction: compare event, version, grant and burn token.

        Only OPEN -> ACKNOWLEDGED_HOLD is possible; never marks the source
        event completed. No fallbacks, no external calls.
        """
        if not self._reviews._valid_key(source, event_id):
            return self._out("INVALID_EVENT")
        if not isinstance(token, str) or len(token) > 256 or not token:
            return self._out("INVALID_TOKEN")
        now = self._trusted_now()
        if now is None:
            return self._out("TRUSTED_CLOCK_UNAVAILABLE")
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._store._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                grant = db.execute(
                    "SELECT source,event_id,expected_version,expires_at,used "
                    "FROM mock_review_grants WHERE token_hash=?", (digest,),
                ).fetchone()
                if grant is None:
                    status = "INVALID_GRANT"
                elif grant[0:2] != (source, event_id):
                    status = "EVENT_MISMATCH"
                elif grant[4] != 0:
                    status = "GRANT_ALREADY_USED"
                elif now >= grant[3]:
                    status = "GRANT_EXPIRED"
                else:
                    event = db.execute(
                        "SELECT state FROM mock_events WHERE source=? AND event_id=?",
                        (source, event_id),
                    ).fetchone()
                    review = db.execute(
                        "SELECT state,version FROM mock_review_transitions "
                        "WHERE source=? AND event_id=?", (source, event_id),
                    ).fetchone()
                    state, version = review if review else ("OPEN", 0)
                    if event != ("PENDING",):
                        status = "NOT_PENDING"
                    elif state != "OPEN" or version != grant[2]:
                        status = "STALE_VERSION"
                    else:
                        changed = db.execute(
                            "UPDATE mock_review_grants SET used=1 "
                            "WHERE token_hash=? AND used=0", (digest,),
                        ).rowcount
                        if changed != 1:
                            status = "GRANT_ALREADY_USED"
                        else:
                            db.execute(
                                "INSERT INTO mock_review_transitions "
                                "(source,event_id,state,version) VALUES (?,?,?,1)",
                                (source, event_id, "ACKNOWLEDGED_HOLD"),
                            )
                            status = "ACKNOWLEDGED_HOLD"
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return self._out(status, 1 if status == "ACKNOWLEDGED_HOLD" else None)
