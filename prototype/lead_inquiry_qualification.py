"""Side-effect-free mock classification of explicit inquiry fields (AC04).

No AI inference, message parsing, external calls, or verified facts invented.
"""
from collections.abc import Mapping


def classify_inquiry(inquiry, required_fields, *, mode="mock"):
    """Return field presence evidence, never the raw inquiry or secrets."""
    base = {"qualification_status": "BLOCKED", "field_assessments": {},
            "fabricated_verified_fields": [], "external_dispatch_count": 0}
    if mode != "mock":
        return {**base, "error": "mock_only"}
    if not isinstance(inquiry, Mapping) or not isinstance(required_fields, (list, tuple)):
        return {**base, "error": "invalid_input"}
    if not required_fields or len(required_fields) > 32:
        return {**base, "error": "invalid_required_fields"}
    if any(not isinstance(name, str) or not name.isidentifier() or len(name) > 64
           for name in required_fields) or len(set(required_fields)) != len(required_fields):
        return {**base, "error": "invalid_required_fields"}
    assessments = {}
    for name in required_fields:
        value = inquiry.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            assessments[name] = "UNKNOWN"
        elif isinstance(value, (str, int, float, bool)) and not isinstance(value, complex):
            assessments[name] = "VERIFIED_FROM_INPUT"
        else:
            assessments[name] = "UNKNOWN"
    return {"qualification_status": "NEEDS_REVIEW" if "UNKNOWN" in assessments.values() else "READY_FOR_REVIEW",
            "field_assessments": assessments, "fabricated_verified_fields": [], "external_dispatch_count": 0}
