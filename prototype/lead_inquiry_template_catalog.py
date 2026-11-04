"""Honest mock template catalog and constrained guided setup validation.

Three executable synthetic local templates. Remaining entries are non-executable
previews and can never activate connectors, AI calls, or external writes.
"""
from lead_inquiry_validation import validate_configuration

_CATALOG = (
    {"id": "lead-inquiry", "title": "Lead Inquiry Qualification",
     "category": "Sales Operations", "status": "EXECUTABLE_MOCK",
     "description": "Validate, deduplicate, qualify and locally review a synthetic lead.",
     "mode": "MOCK_ONLY"},
    {"id": "crm-handoff", "title": "CRM Handoff",
     "category": "Sales Operations", "status": "PREVIEW_NOT_EXECUTABLE",
     "description": "Future capability: map approved leads to a real CRM.",
     "mode": "NOT_IMPLEMENTED"},
    {"id": "support-triage", "title": "Support Request Triage",
     "category": "Customer Support", "status": "EXECUTABLE_MOCK",
     "description": "Route synthetic support requests to mock queues with duplicate protection.",
     "mode": "MOCK_ONLY"},
    {"id": "appointment-followup", "title": "Appointment Follow-up",
     "category": "Scheduling", "status": "EXECUTABLE_MOCK",
     "description": "Simulate bounded reminder planning with explicit synthetic dates and no sends.",
     "mode": "MOCK_ONLY"},
    {"id": "knowledge-routing", "title": "Knowledge & Document Routing",
     "category": "Knowledge Operations", "status": "PREVIEW_NOT_EXECUTABLE",
     "description": "Future capability: route documents to approved destinations.",
     "mode": "NOT_IMPLEMENTED"},
)
_ALLOWED = {
    "source": ("mock_inbox", "mock_web_form"),
    "destination": ("mock_crm", "mock_sheet"),
    "follow_up_policy": ("approval_required", "draft_only"),
    "timezone": ("Asia/Jakarta", "UTC"),
    "ai_mode": ("mock",),
}
_SETUP_FIELDS = frozenset((*_ALLOWED, "monthly_budget_idr"))
_MAPPING = {"inquiry_id": "source_event_id", "service_requested": "service_requested"}
_DEFAULT = {"source": "mock_inbox", "destination": "mock_crm",
            "follow_up_policy": "approval_required", "ai_mode": "mock",
            "timezone": "Asia/Jakarta", "monthly_budget_idr": 0}


def list_templates():
    """Detached public metadata, three executable and two honest previews."""
    return {"catalog_version": 1, "templates": [dict(item) for item in _CATALOG],
            "executable_count": 3, "preview_count": 2,
            "external_dispatch_count": 0}


def setup_choices():
    return {"template_id": "lead-inquiry",
            "fields": {k: list(v) for k, v in _ALLOWED.items()},
            "monthly_budget_idr": {"min": 0, "max": 1000000},
            "defaults": dict(_DEFAULT), "mode": "MOCK_ONLY",
            "external_dispatch_count": 0}


def validate_guided_setup(request):
    """Validate exact allowlisted setup, never reflect untrusted input."""
    def denied(reason):
        return {"status": "BLOCKED", "errors": [reason],
                "configuration": None, "external_dispatch_count": 0}
    if type(request) is not dict or set(request) != {"template_id", "setup"}:
        return denied("invalid_setup_shape")
    if request["template_id"] != "lead-inquiry":
        return denied("template_not_executable")
    setup = request["setup"]
    if type(setup) is not dict or set(setup) != _SETUP_FIELDS:
        return denied("invalid_setup_fields")
    for key, allowed in _ALLOWED.items():
        value = setup[key]
        if type(value) is not str or value not in allowed:
            return denied(key + "_unsupported")
    amount = setup["monthly_budget_idr"]
    if type(amount) is not int or not 0 <= amount <= 1000000:
        return denied("budget_out_of_range")
    config = {**setup, "mapping": dict(_MAPPING)}
    verdict = validate_configuration(config, mode="mock")
    if verdict["activation_status"] != "DRAFT_VALID":
        return {"status": "BLOCKED", "errors": verdict["validation_errors"],
                "configuration": None, "external_dispatch_count": 0}
    return {"status": "DRAFT_VALID", "errors": [],
            "configuration": config, "external_dispatch_count": 0}


def default_setup():
    return {"template_id": "lead-inquiry", "setup": dict(_DEFAULT)}
