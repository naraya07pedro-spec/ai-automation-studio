"""Mock-only reconciliation preflight; never transitions event state.

A trusted authorization verifier is deliberately NOT implemented here. The
caller must supply one; returning True is only a simulated trust boundary.
This is a policy contract, not real authentication, authorization, or proof of
a provider effect. Only an explicit dry-run verdict is produced.
"""
import sqlite3

from lead_inquiry_pending_review import _event_ref


_ALLOWED_INTENT = "ACKNOWLEDGE_UNRESOLVED_MOCK_EVENT"
_ALLOWED_EVIDENCE = "MOCK_NO_EXTERNAL_EFFECT"


def evaluate_reconciliation(store, *, source, event_id, proposal, verifier, now):
    """Fail-closed preflight on an existing PENDING mock event.

    Returns a redacted decision without writing to the store or accepting a
    completion claim. No event data, identity or evidence body is returned.
    """
    def decision(status):
        return {"preflight_status": status, "state_changed": False,
                "external_dispatch_count": 0}

    if (not isinstance(source, str) or not source.startswith("mock_")
            or not isinstance(event_id, str) or not event_id
            or len(source) > 256 or len(event_id) > 256
            or source != source.strip() or event_id != event_id.strip()):
        return decision("INVALID_EVENT")

    if not isinstance(proposal, dict):
        return decision("INVALID_PROPOSAL")
    if not callable(verifier) or type(now) is not int or now < 0:
        return decision("INVALID_TRUST_CONTEXT")

    required = {"event_ref", "intent", "evidence_kind", "evidence_digest",
                "issued_at", "expires_at", "operator_ref"}
    if set(proposal) != required:
        return decision("INVALID_PROPOSAL")
    if proposal["event_ref"] != _event_ref(source, event_id):
        return decision("EVENT_MISMATCH")
    if proposal["intent"] != _ALLOWED_INTENT or proposal["evidence_kind"] != _ALLOWED_EVIDENCE:
        return decision("UNSUPPORTED_INTENT_OR_EVIDENCE")

    digest = proposal["evidence_digest"]
    if (not isinstance(digest, str) or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)):
        return decision("INVALID_EVIDENCE_DIGEST")

    issued, expires = proposal["issued_at"], proposal["expires_at"]
    if (type(issued) is not int or type(expires) is not int or
            issued < 0 or issued > now or expires <= now or expires <= issued or
            expires - issued > 300):
        return decision("EXPIRED_OR_INVALID_WINDOW")

    operator = proposal["operator_ref"]
    if (not isinstance(operator, str) or not 1 <= len(operator) <= 128 or
            not operator.isascii() or
            any(not (c.isalnum() or c in "_-") for c in operator)):
        return decision("INVALID_OPERATOR_REFERENCE")

    try:
        if store.state(source, event_id) != "PENDING":
            return decision("NOT_PENDING")
    except (sqlite3.Error, OSError):
        return decision("STORAGE_UNAVAILABLE")

    try:
        authorized = verifier(operator, "mock.reconciliation.inspect", proposal["event_ref"])
    except Exception:
        return decision("AUTHORIZATION_UNAVAILABLE")
    if authorized is not True:
        return decision("NOT_AUTHORIZED")

    # Recheck immediately before return. This remains a TOCTOU-prone dry-run;
    # the final transition needs atomic comparison and independent auth.
    try:
        if store.state(source, event_id) != "PENDING":
            return decision("STALE_STATE")
    except (sqlite3.Error, OSError):
        return decision("STORAGE_UNAVAILABLE")
    return decision("PRECHECK_PASSED_NO_TRANSITION")
