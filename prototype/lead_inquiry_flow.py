"""One end-to-end mock-only inquiry path. No external IO or automatic approvals.

The three existing prototypes are composed without changing their semantics.
An optional transactional mock store preserves decisions across new instances.
No external dispatch, approval execution, or durable multi-stage runtime exists.
"""
from collections.abc import Mapping
import sqlite3

from lead_inquiry_validation import validate_configuration
from lead_inquiry_dedup import InMemoryDeduplicator
from lead_inquiry_qualification import classify_inquiry
from durable_mock_state import DurableMockEventStore


class MockInquiryFlow:
    """Isolated mock workflow with optional durable event decisions."""

    def __init__(self, *, max_events=1000, durable_store=None):
        if durable_store is not None and not isinstance(durable_store, DurableMockEventStore):
            raise TypeError("durable_store must be DurableMockEventStore")
        self._dedup = InMemoryDeduplicator(max_events=max_events) if durable_store is None else None
        self._durable_store = durable_store

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

        if self._durable_store is None:
            dedup = self._dedup.register(source, event_id, inquiry)
            first_seen = dedup["status"] == "FIRST_SEEN"
        else:
            try:
                dedup = self._durable_store.record(
                    source, event_id, inquiry, qualification["qualification_status"]
                )
            except (sqlite3.Error, OSError):
                # Never fall back to volatile state after a storage failure.
                result["workflow_status"] = "STORAGE_UNAVAILABLE"
                return result
            first_seen = dedup["status"] == "RECORDED"
        result["deduplication"] = dedup
        if not first_seen:
            result["workflow_status"] = {
                "DUPLICATE": "SKIPPED_DUPLICATE",
                "CONFLICT": "BLOCKED_CONFLICT",
            }.get(dedup["status"], "BLOCKED")
            return result

        result["qualification"] = qualification
        result["workflow_status"] = (
            "NEEDS_REVIEW"
            if qualification["qualification_status"] == "NEEDS_REVIEW"
            else "READY_FOR_REVIEW"
        )
        return result
