"""One end-to-end mock-only inquiry path. No external IO or automatic approvals.

The three existing prototypes are composed without changing their semantics.
This is process-local and does not implement durable workflow execution.
"""
from collections.abc import Mapping

from lead_inquiry_validation import validate_configuration
from lead_inquiry_dedup import InMemoryDeduplicator
from lead_inquiry_qualification import classify_inquiry


class MockInquiryFlow:
    """Isolated mock workflow with a process-local, bounded event register."""

    def __init__(self, *, max_events=1000):
        self._dedup = InMemoryDeduplicator(max_events=max_events)

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

        dedup = self._dedup.register(source, event_id, inquiry)
        result["deduplication"] = dedup
        if dedup["status"] != "FIRST_SEEN":
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
