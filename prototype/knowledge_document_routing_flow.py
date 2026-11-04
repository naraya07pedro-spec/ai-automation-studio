"""Fifth executable template: deterministic synthetic knowledge/document routing.

No file upload, document bytes, embeddings, document fetching, AI classifiers,
external connector, persistence or dispatch. Input values are strictly enums.
"""
from hashlib import sha256
import json
import re

_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_KIND = ("policy", "invoice", "technical_guide", "contract")
_CLASSIFICATION = ("public", "internal", "restricted")
_DESTINATION = ("mock_knowledge_base", "mock_finance_queue", "mock_legal_queue")
_ROUTES = {
    "policy": "mock_knowledge_base",
    "invoice": "mock_finance_queue",
    "technical_guide": "mock_knowledge_base",
    "contract": "mock_legal_queue",
}


class MockKnowledgeRouting:
    """Bounded deterministic router with no raw documents or provider actions."""

    def __init__(self, *, max_events=1000):
        if type(max_events) is not int or not 1 <= max_events <= 1000:
            raise ValueError("invalid_capacity")
        self._max_events = max_events
        self._seen = {}

    @staticmethod
    def _result(status, *, dedup="NOT_RUN", destination="NOT_ASSIGNED",
                policy="NOT_ASSESSED", preview=None):
        return {
            "mode": "MOCK_ONLY", "workflow": "knowledge-routing",
            "status": status, "deduplication": dedup,
            "policy_decision": policy, "destination": destination,
            "routing_preview": preview, "document_uploaded": False,
            "document_transferred": False, "external_dispatch_count": 0,
            "timeline": [
                {"stage": "SYNTHETIC_INTAKE", "status": "RECEIVED"},
                {"stage": "FIELD_VALIDATE", "status": "BLOCKED" if status == "BLOCKED" else "PASSED"},
                {"stage": "CLASSIFICATION_POLICY", "status": policy},
                {"stage": "DEDUP", "status": dedup},
                {"stage": "ROUTE_PREVIEW", "status": "PREPARED_MOCK_ONLY" if preview is not None else "NOT_PREPARED"},
                {"stage": "EXTERNAL_DISPATCH", "status": "DISABLED"},
            ],
        }

    def process(self, payload):
        if (type(payload) is not dict
                or set(payload) != {"event_id", "document_kind", "classification",
                                   "destination", "review_acknowledged"}
                or type(payload.get("event_id")) is not str
                or not _ID.fullmatch(payload["event_id"])
                or type(payload.get("document_kind")) is not str
                or payload["document_kind"] not in _KIND
                or type(payload.get("classification")) is not str
                or payload["classification"] not in _CLASSIFICATION
                or type(payload.get("destination")) is not str
                or payload["destination"] not in _DESTINATION
                or type(payload.get("review_acknowledged")) is not bool):
            return self._result("BLOCKED")
        # Restricted labels do not create any routing preview regardless of
        # caller-supplied review claim. Real document policy is not provided.
        if payload["classification"] == "restricted":
            return self._result("RESTRICTED_NO_ROUTE",
                                policy="RESTRICTED_MANUAL_HANDLING_ONLY")
        expected = _ROUTES[payload["document_kind"]]
        if payload["destination"] != expected:
            return self._result("DESTINATION_NOT_ALLOWED",
                                policy="DESTINATION_POLICY_MISMATCH")
        if payload["classification"] == "internal" and not payload["review_acknowledged"]:
            return self._result("REVIEW_REQUIRED_NO_ROUTE",
                                policy="MOCK_REVIEW_NOT_ACKNOWLEDGED")
        key = payload["event_id"]
        fingerprint = sha256(json.dumps(
            [payload["document_kind"], payload["classification"],
             payload["destination"], payload["review_acknowledged"]],
            separators=(",", ":")).encode("utf-8")).hexdigest()
        if key in self._seen:
            return self._result(
                "SKIPPED_DUPLICATE" if self._seen[key] == fingerprint
                else "BLOCKED_CONFLICT",
                dedup="DUPLICATE" if self._seen[key] == fingerprint else "CONFLICT",
                policy="POLICY_CHECK_PASSED_MOCK")
        if len(self._seen) >= self._max_events:
            return self._result("BLOCKED_CAPACITY",
                                dedup="CAPACITY_EXHAUSTED",
                                policy="POLICY_CHECK_PASSED_MOCK")
        self._seen[key] = fingerprint
        preview = {"document_kind": payload["document_kind"],
                   "classification": payload["classification"],
                   "mock_destination": expected}
        return self._result("ROUTING_PREVIEW_PREPARED",
                            dedup="FIRST_SEEN", destination=expected,
                            policy="POLICY_CHECK_PASSED_MOCK", preview=preview)
