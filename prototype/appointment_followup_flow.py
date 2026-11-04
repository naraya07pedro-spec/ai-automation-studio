"""Deterministic appointment follow-up planning with synthetic dates only.

No background scheduler, clock-based trigger, real calendar, delivery channel,
external messages or customer records. The caller provides an explicit
reference instant to make decisions reproducible and independently testable.
"""
from datetime import datetime, timedelta
from hashlib import sha256
import json
import re

_MAX_EVENTS = 1000
_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})\Z")


def _instant(value):
    if type(value) is not str or len(value) > 32 or not _TIMESTAMP.fullmatch(value):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.utcoffset() is None or abs(dt.utcoffset()) > timedelta(hours=14):
            return None
        return dt
    except (ValueError, OverflowError):
        return None


class MockAppointmentFollowup:
    def __init__(self, *, max_events=_MAX_EVENTS):
        if type(max_events) is not int or not 1 <= max_events <= _MAX_EVENTS:
            raise ValueError("invalid_capacity")
        self._seen = {}
        self._max_events = max_events

    @staticmethod
    def _result(status, *, dedup="NOT_RUN", plan="NOT_PREPARED", window="NOT_ASSESSED"):
        return {
            "mode": "MOCK_ONLY", "workflow": "appointment-followup",
            "status": status, "deduplication": dedup,
            "window": window, "followup_plan": plan,
            "timeline": [
                {"stage": "INTAKE", "status": "SYNTHETIC_RECEIVED"},
                {"stage": "DATE_VALIDATE", "status": "BLOCKED" if status == "BLOCKED" else "PASSED"},
                {"stage": "DEDUP", "status": dedup},
                {"stage": "WINDOW_ASSESS", "status": window},
                {"stage": "DRAFT_PLAN", "status": plan},
                {"stage": "EXTERNAL_DISPATCH", "status": "DISABLED"},
            ],
            "external_dispatch_count": 0, "message_sent": False,
            "calendar_event_created": False,
        }

    def process(self, payload):
        if (type(payload) is not dict
                or set(payload) != {"event_id", "as_of", "appointment_at", "channel"}
                or type(payload.get("event_id")) is not str
                or not _ID.fullmatch(payload["event_id"])
                or type(payload.get("channel")) is not str
                or payload["channel"] not in ("mock_email", "mock_sms")):
            return self._result("BLOCKED")
        reference = _instant(payload["as_of"])
        appointment = _instant(payload["appointment_at"])
        if reference is None or appointment is None:
            return self._result("BLOCKED")
        delta = appointment - reference
        # Demonstrate an explicit, bounded 30-day scheduling horizon.
        # Past appointments are a review-only state, not a reason to send.
        if delta > timedelta(days=30) or delta < -timedelta(days=7):
            return self._result("BLOCKED")
        if delta < timedelta(0):
            window, plan = "PAST_APPOINTMENT", "MANUAL_REVIEW_NO_SEND"
        elif delta <= timedelta(hours=24):
            window, plan = "WITHIN_24_HOURS", "PREPARED_REMINDER_MOCK_ONLY"
        else:
            window, plan = "UPCOMING_30_DAYS", "DEFERRED_DRAFT_MOCK_ONLY"
        key = payload["event_id"]
        digest = sha256(json.dumps(
            (reference.isoformat(), appointment.isoformat(), payload["channel"]),
            separators=(",", ":")).encode("utf-8")).hexdigest()
        if key in self._seen:
            return self._result(
                "SKIPPED_DUPLICATE" if self._seen[key] == digest else "BLOCKED_CONFLICT",
                dedup="DUPLICATE" if self._seen[key] == digest else "CONFLICT")
        if len(self._seen) >= self._max_events:
            return self._result("BLOCKED_CAPACITY", dedup="CAPACITY_EXHAUSTED")
        self._seen[key] = digest
        return self._result("PLANNED_MOCK_ONLY", dedup="FIRST_SEEN",
                            window=window, plan=plan)
