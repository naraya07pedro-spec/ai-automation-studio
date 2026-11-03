"""In-memory mock-only event deduplication, NOT durable idempotency.

This bounded proof of behavior never sends external actions and stores only a
payload hash. A restart loses all deduplication state. Do not use this as a
production exactly-once delivery mechanism.
"""
from hashlib import sha256
from json import dumps
from threading import Lock


class InMemoryDeduplicator:
    """Fail-closed, capacity-bounded deduplication scoped to this instance."""

    def __init__(self, max_events=1000):
        if type(max_events) is not int or max_events < 1:
            raise ValueError("max_events must be a positive integer")
        self._limit = max_events
        self._seen = {}
        self._lock = Lock()

    def register(self, source, event_id, payload):
        """Classify new, repeated, or conflicting events without dispatching."""
        if any(not isinstance(v, str) or not v or v != v.strip() or len(v) > 256
               for v in (source, event_id)):
            return self._result("INVALID_ID")
        if not source.startswith("mock_"):
            return self._result("MOCK_ONLY")
        if not isinstance(payload, dict):
            return self._result("INVALID_PAYLOAD")
        try:
            serialized = dumps(payload, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True, allow_nan=False)
        except (TypeError, ValueError):
            return self._result("INVALID_PAYLOAD")
        if len(serialized) > 262144:
            return self._result("PAYLOAD_TOO_LARGE")
        digest = sha256(serialized.encode("utf-8")).hexdigest()
        key = (source, event_id)
        with self._lock:
            previous = self._seen.get(key)
            if previous is not None:
                return self._result("DUPLICATE" if previous == digest else "CONFLICT")
            if len(self._seen) >= self._limit:
                return self._result("CAPACITY_EXHAUSTED")
            self._seen[key] = digest
            return self._result("FIRST_SEEN")

    @staticmethod
    def _result(status):
        return {"status": status, "external_dispatch_count": 0}
