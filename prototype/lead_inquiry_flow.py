"""One end-to-end mock-only inquiry path. No external IO or automatic approvals.

The three existing prototypes are composed without changing their semantics.
This is process-local and does not implement durable workflow execution.
"""
from collections.abc import Mapping
import sqlite3

from lead_inquiry_validation import validate_configuration
from lead_inquiry_dedup import InMemoryDeduplicator
from lead_inquiry_qualification import classify_inquiry


class MockInquiryFlow:
    """Isolated mock workflow with a process-local, bounded event register."""

    def __init__(self, *, max_events=1000, durable_store=None):
        """An explicitly supplied store opts into persistent mock behavior.

        Default remains in-memory to preserve previous prototype tests. The
        durable store must expose reserve() and mark_mock_completed(); no
        automatic fallback to memory is allowed on storage failures.
        """
        if durable_store is not None and not (
            callable(getattr(durable_store, "reserve", None))
            and callable(getattr(durable_store, "state", None))
        ):
            raise TypeError("durable_store must implement reserve and state")
        self._durable_store = durable_store
        self._dedup = None if durable_store is not None else InMemoryDeduplicator(max_events=max_events)

    def process(self, configuration, event, required_fields, *, mode="mock"):
        """Validate first; never classify/record invalid or non-mock requests.

        Output is deliberately redacted and never contains inbound customer
        content, credentials, or free-text messages.
        """
        validation = validate_configuration(configuration, mode=mode)
        result = {
            "workflow_status": "BLOCKED",
            "validation": validation,
            "deduplication": None,
            "qualification": None,
            "external_dispatch_count": 0,
        }
        if validation["activation_status"] != "DRAFT_VALID":
            return result
        if not isinstance(event, Mapping):
            return result
        source = configuration["source"]
        event_id = event.get("source_event_id")
        inquiry = event.get("inquiry")
        if not isinstance(inquiry, dict):
            return result
        # Qualify before reserving event ID: invalid schemas must not consume
        # deduplication capacity or make a corrected event appear conflicting.
        qualification = classify_inquiry(inquiry, required_fields, mode=mode)
        if qualification["qualification_status"] == "BLOCKED":
            result["qualification"] = qualification
            return result

        durable = self._durable_store is not None
        try:
            dedup = (
                self._durable_store.reserve(source, event_id, inquiry)
                if durable else self._dedup.register(source, event_id, inquiry)
            )
        except (sqlite3.Error, OSError):
            # Do not leak database paths, SQL messages, or customer content.
            result["deduplication"] = {"status": "STORAGE_UNAVAILABLE", "external_dispatch_count": 0}
            return result

        result["deduplication"] = dedup
        accepted = "RESERVED_PENDING" if durable else "FIRST_SEEN"
        if dedup["status"] != accepted:
            result["workflow_status"] = {
                "DUPLICATE": "SKIPPED_DUPLICATE",
                "CONFLICT": "BLOCKED_CONFLICT",
                "NEEDS_RECONCILIATION": "BLOCKED_RECONCILIATION",
            }.get(dedup["status"], "BLOCKED")
            return result

        # A durable reservation remains PENDING. Do not infer a successful
        # provider action from local qualification or advance completion.
        # Replay must be reconciled explicitly; no automatic retry or send.
        result["qualification"] = qualification
        result["workflow_status"] = (
            "NEEDS_REVIEW"
            if qualification["qualification_status"] == "NEEDS_REVIEW"
            else "READY_FOR_REVIEW"
        )
        return result
