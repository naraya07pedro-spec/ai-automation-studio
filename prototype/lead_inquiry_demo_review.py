"""Disposable mock draft review state, never authorization for external effects.

This is a local UI exercise: acknowledgement/rejection never grants a send,
CRM write, event completion or verified operator identity.
"""
import secrets

_STATUSES = {"ACKNOWLEDGE": "ACKNOWLEDGED_LOCAL_ONLY",
             "REJECT": "REJECTED_LOCAL_ONLY"}
_MAX_PENDING = 100


def execution_timeline(flow_result):
    """Deterministic, redacted stage evidence derived from existing flow."""
    validation = flow_result.get("validation") or {}
    dedup = flow_result.get("deduplication") or {}
    qual = flow_result.get("qualification") or {}
    state = flow_result.get("workflow_status", "BLOCKED")
    validation_ok = validation.get("activation_status") == "DRAFT_VALID"
    dedup_ok = dedup.get("status") == "FIRST_SEEN"
    qualified = qual.get("qualification_status") == "READY_FOR_REVIEW"
    return [
        {"stage": "INTAKE", "status": "SYNTHETIC_RECEIVED"},
        {"stage": "CONFIG_VALIDATE", "status": "PASSED" if validation_ok else "BLOCKED"},
        {"stage": "DEDUP", "status": dedup.get("status", "NOT_RUN")},
        {"stage": "QUALIFICATION", "status": qual.get("qualification_status", "NOT_RUN")},
        {"stage": "DRAFT", "status": "PREPARED_MOCK_ONLY" if qualified and dedup_ok else "NOT_CREATED"},
        {"stage": "REVIEW", "status": "AWAITING_LOCAL_REVIEW" if state == "READY_FOR_REVIEW" and dedup_ok else "NOT_AVAILABLE"},
        {"stage": "EXTERNAL_DISPATCH", "status": "DISABLED"},
    ]


class LocalMockReview:
    def __init__(self):
        self._pending = {}

    def offer(self, flow_result, *, policy="approval_required"):
        """Only a fresh, fully present synthetic lead receives a canned draft."""
        if (policy not in ("approval_required", "draft_only")
                or flow_result.get("workflow_status") != "READY_FOR_REVIEW"
                or (flow_result.get("deduplication") or {}).get("status") != "FIRST_SEEN"
                or (policy == "approval_required" and len(self._pending) >= _MAX_PENDING)):
            return {"status": "NOT_AVAILABLE", "draft": None, "review_token": None}
        # Fixed text cannot accidentally reflect inbound customer content.
        draft = {
            "subject": "Thanks for your inquiry (mock draft)",
            "body": "Thank you for reaching out. A team member would review your request before any follow-up.",
            "recipient": "NOT_SET",
            "delivery": "NEVER_SENT",
            "requires_verified_identity": True,
        }
        if policy == "draft_only":
            return {"status": "DRAFT_ONLY_NO_REVIEW", "draft": draft, "review_token": None}
        token = secrets.token_urlsafe(24)
        self._pending[token] = "AWAITING_LOCAL_REVIEW"
        return {"status": "AWAITING_LOCAL_REVIEW", "draft": draft, "review_token": token}

    def decide(self, token, decision):
        """Single-use action, in-memory and redacted; never returns token."""
        if (type(token) is not str or not (16 <= len(token) <= 128)
                or type(decision) is not str or decision not in _STATUSES):
            return {"status": "INVALID_REVIEW_REQUEST", "external_dispatch_count": 0,
                    "event_state_changed": False}
        if token not in self._pending:
            return {"status": "TOKEN_NOT_FOUND_OR_CONSUMED", "external_dispatch_count": 0,
                    "event_state_changed": False}
        del self._pending[token]
        return {"status": _STATUSES[decision], "external_dispatch_count": 0,
                "event_state_changed": False}
