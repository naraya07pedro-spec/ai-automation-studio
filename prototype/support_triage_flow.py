"""Deterministic, bounded, process-local support triage mock workflow.

This is a second independent executable template, not a model or customer
service integration. No free-text stored, logged, or returned to callers.
"""
from hashlib import sha256
import json

_CATEGORIES = ("access", "billing", "technical", "general")
_SEVERITIES = ("low", "normal", "high")
_MAX_EVENTS = 1000


class MockSupportTriage:
    def __init__(self, *, max_events=_MAX_EVENTS):
        if type(max_events) is not int or not 1 <= max_events <= _MAX_EVENTS:
            raise ValueError("invalid_capacity")
        self._max_events = max_events
        self._seen = {}

    @staticmethod
    def _reply(status, *, dedup="NOT_RUN", queue="NOT_ASSIGNED",
               urgency="NOT_ASSESSED"):
        stages = [
            {"stage": "INTAKE", "status": "SYNTHETIC_RECEIVED"},
            {"stage": "FIELD_VALIDATE", "status": "PASSED" if status != "BLOCKED" else "BLOCKED"},
            {"stage": "DEDUP", "status": dedup},
            {"stage": "TRIAGE", "status": status},
            {"stage": "MOCK_QUEUE", "status": queue},
            {"stage": "EXTERNAL_DISPATCH", "status": "DISABLED"},
        ]
        return {"mode": "MOCK_ONLY", "workflow": "support-triage",
                "status": status, "deduplication": dedup,
                "queue": queue, "urgency": urgency, "timeline": stages,
                "external_dispatch_count": 0, "ticket_created": False}

    def process(self, payload):
        if (type(payload) is not dict
                or set(payload) != {"event_id", "category", "severity", "customer_impact"}
                or type(payload.get("event_id")) is not str
                or not 1 <= len(payload["event_id"]) <= 80
                or not payload["event_id"].isascii()
                or not all(c.isalnum() or c in "-_" for c in payload["event_id"])
                or type(payload.get("category")) is not str
                or payload["category"] not in _CATEGORIES
                or type(payload.get("severity")) is not str
                or payload["severity"] not in _SEVERITIES
                or type(payload.get("customer_impact")) is not bool):
            return self._reply("BLOCKED")
        key = payload["event_id"]
        digest = sha256(json.dumps(
            (payload["category"], payload["severity"], payload["customer_impact"]),
            separators=(",", ":")).encode()).hexdigest()
        if key in self._seen:
            if self._seen[key] == digest:
                return self._reply("SKIPPED_DUPLICATE", dedup="DUPLICATE")
            return self._reply("BLOCKED_CONFLICT", dedup="CONFLICT")
        if len(self._seen) >= self._max_events:
            return self._reply("BLOCKED_CAPACITY", dedup="CAPACITY_EXHAUSTED")
        self._seen[key] = digest
        queue = {
            "access": "MOCK_ACCOUNT_SUPPORT", "billing": "MOCK_BILLING",
            "technical": "MOCK_TECHNICAL", "general": "MOCK_GENERAL",
        }[payload["category"]]
        urgency = "PRIORITY_REVIEW" if (
            payload["severity"] == "high" or
            payload["customer_impact"] and payload["severity"] == "normal"
        ) else "STANDARD_REVIEW"
        return self._reply("ROUTED_MOCK_ONLY", dedup="FIRST_SEEN",
                           queue=queue, urgency=urgency)
