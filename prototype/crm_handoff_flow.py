"""Fourth executable synthetic workflow: a *non-writing* mock CRM record handoff.

The caller-supplied review flag is a demo-only condition and is NOT a verified
human approval, authentication, or authorization to perform a real CRM write.
No external connectors or CRM SDKs are imported.
"""
from hashlib import sha256
import json
import re

_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_ENUMS = {
    "lead_stage": ("new", "qualified", "needs_review"),
    "service_code": ("automation", "integration", "consulting"),
    "owner_queue": ("sales", "solutions", "operations"),
    "mapping_profile": ("standard", "compact"),
}
_MAPPINGS = {
    "standard": ("crm_lifecycle_stage", "crm_service_category", "crm_owner_queue"),
    "compact": ("stage", "service", "queue"),
}


class MockCRMHandoff:
    def __init__(self, *, max_events=1000):
        if type(max_events) is not int or not 1 <= max_events <= 1000:
            raise ValueError("invalid_capacity")
        self._seen = {}
        self._max_events = max_events

    @staticmethod
    def _result(status, *, dedup="NOT_RUN", profile="NOT_SELECTED",
                mapped=None, review="NOT_VERIFIED"):
        return {
            "mode": "MOCK_ONLY", "workflow": "crm-handoff",
            "status": status, "deduplication": dedup,
            "mapping_profile": profile, "review_gate": review,
            "record_preview": mapped, "crm_record_created": False,
            "external_dispatch_count": 0,
            "timeline": [
                {"stage": "INTAKE", "status": "SYNTHETIC_RECEIVED"},
                {"stage": "FIELD_VALIDATE", "status": "BLOCKED" if status == "BLOCKED" else "PASSED"},
                {"stage": "LOCAL_REVIEW_GATE", "status": review},
                {"stage": "DEDUP", "status": dedup},
                {"stage": "FIELD_MAP", "status": "PREVIEW_PREPARED" if mapped else "NOT_PREPARED"},
                {"stage": "CRM_WRITE", "status": "DISABLED"},
            ],
        }

    def process(self, payload):
        if (type(payload) is not dict
                or set(payload) != {"event_id", "lead_stage", "service_code",
                                   "owner_queue", "mapping_profile", "review_acknowledged"}
                or type(payload.get("event_id")) is not str
                or not _ID.fullmatch(payload["event_id"])
                or any(type(payload.get(key)) is not str
                       or payload[key] not in allowed
                       for key, allowed in _ENUMS.items())
                or type(payload.get("review_acknowledged")) is not bool):
            return self._result("BLOCKED")
        if not payload["review_acknowledged"]:
            # A missing mock acknowledgement is not allowed to consume the
            # event's dedup key or prepare any CRM record.
            return self._result("REVIEW_REQUIRED_NO_RECORD",
                                review="MOCK_REVIEW_NOT_ACKNOWLEDGED")
        key = payload["event_id"]
        digest = sha256(json.dumps(tuple(payload[k] for k in (
            "lead_stage", "service_code", "owner_queue", "mapping_profile")),
            separators=(",", ":")).encode("utf-8")).hexdigest()
        if key in self._seen:
            if self._seen[key] == digest:
                return self._result("SKIPPED_DUPLICATE", dedup="DUPLICATE",
                                    review="CALLER_CLAIMED_MOCK_ACK")
            return self._result("BLOCKED_CONFLICT", dedup="CONFLICT",
                                review="CALLER_CLAIMED_MOCK_ACK")
        if len(self._seen) >= self._max_events:
            return self._result("BLOCKED_CAPACITY", dedup="CAPACITY_EXHAUSTED",
                                review="CALLER_CLAIMED_MOCK_ACK")
        self._seen[key] = digest
        keys = _MAPPINGS[payload["mapping_profile"]]
        # All preview values are from strict synthetic enums; no identities.
        values = (payload["lead_stage"], payload["service_code"], payload["owner_queue"])
        record = dict(zip(keys, values))
        return self._result("SIMULATED_HANDOFF_PREPARED", dedup="FIRST_SEEN",
                            profile=payload["mapping_profile"], mapped=record,
                            review="CALLER_CLAIMED_MOCK_ACK")
