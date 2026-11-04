"""Recommended *mock-only* entrypoint for review grant issuance and consumption.

The policy and clock are injected once at construction, not provided to methods.
This facade is NOT a security boundary if callers can import underlying stores
or construct an always-allow authority. No identity provider is implemented.
"""
import sqlite3

from lead_inquiry_pending_review import MockPendingReview, _event_ref
from lead_inquiry_review_transitions import MockReviewTransitions
from lead_inquiry_single_use_grants import MockSingleUseReviewGrants


class MockReviewOrchestrator:
    """Narrow facade; deliberately exposes no raw store or mutable policy."""

    def __init__(self, store, *, authority=None, clock=None):
        if not callable(getattr(store, "_connect", None)):
            raise TypeError("durable mock store required")
        self._pending = MockPendingReview(store)
        self._review = MockReviewTransitions(store)
        self._grants = MockSingleUseReviewGrants(
            store, authority=authority, clock=clock)

    @staticmethod
    def _blocked(status):
        return {"status": status, "event_state_changed": False,
                "external_dispatch_count": 0}

    def list_pending(self, *, limit=50):
        """List redacted references; never expose event IDs or customer fields."""
        return self._pending.list_pending(limit=limit)

    def inspect_review(self, source, event_id):
        return self._review.inspect(source, event_id)

    def request_hold_grant(self, source, event_id, *, evidence_digest,
                           operator_ref):
        """Caller supplies evidence *reference*, not verifier/clock/lifetime."""
        proposal = {
            "event_ref": _event_ref(source, event_id)
                         if isinstance(source, str) and isinstance(event_id, str)
                         else "",
            "intent": "ACKNOWLEDGE_UNRESOLVED_MOCK_EVENT",
            "evidence_kind": "MOCK_NO_EXTERNAL_EFFECT",
            "evidence_digest": evidence_digest,
            "operator_ref": operator_ref,
        }
        try:
            return self._grants.issue(source, event_id, proposal=proposal)
        except (sqlite3.Error, OSError):
            return self._blocked("STORAGE_UNAVAILABLE")

    def hold_with_grant(self, source, event_id, *, token):
        """Atomically consume grant to hold review; original event stays PENDING."""
        try:
            return self._grants.consume_hold(source, event_id, token=token)
        except (sqlite3.Error, OSError):
            return self._blocked("STORAGE_UNAVAILABLE")
