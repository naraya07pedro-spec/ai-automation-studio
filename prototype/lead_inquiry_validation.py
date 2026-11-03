"""Dependency-free, mock-only lead inquiry configuration validator.

This is a narrow executable spike for acceptance cases AC01 and AC02,
not an application architecture choice or a live connector implementation.
"""

from collections.abc import Mapping


_REQUIRED_MAPPINGS = ("inquiry_id", "service_requested")
_REQUIRED_CONFIG = ("source", "destination", "follow_up_policy", "ai_mode", "timezone")


def validate_configuration(configuration: object, *, mode: str = "mock") -> dict:
    """Return a redacted, deterministic activation decision without side effects.

    Unrecognized config fields are neither returned nor logged. All processing
    remains in memory. Live operation is explicitly unsupported in this spike.
    """
    if mode != "mock":
        return {"activation_status": "BLOCKED", "validation_errors": ["mock_only"], "external_dispatch_count": 0}

    if not isinstance(configuration, Mapping):
        return {"activation_status": "BLOCKED", "validation_errors": ["configuration_invalid"], "external_dispatch_count": 0}

    errors = []
    for field in _REQUIRED_CONFIG:
        value = configuration.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field}_missing")

    mapping = configuration.get("mapping")
    if mapping is None or mapping == {}:
        errors.append("mapping_missing")
    elif not isinstance(mapping, Mapping) or any(
        not isinstance(mapping.get(name), str) or not mapping[name].strip()
        for name in _REQUIRED_MAPPINGS
    ):
        errors.append("mapping_invalid")

    if configuration.get("follow_up_policy") not in ("approval_required", "draft_only"):
        if "follow_up_policy_missing" not in errors:
            errors.append("follow_up_policy_unsupported")
    if configuration.get("ai_mode") != "mock":
        if "ai_mode_missing" not in errors:
            errors.append("ai_mode_unsupported")
    for name in ("source", "destination"):
        value = configuration.get(name)
        if isinstance(value, str) and value.strip() and not value.startswith("mock_"):
            errors.append(f"{name}_unsupported")

    budget = configuration.get("monthly_budget_idr")
    if budget is not None and (type(budget) is not int or budget < 0):
        errors.append("budget_invalid")

    return {
        "activation_status": "BLOCKED" if errors else "DRAFT_VALID",
        "validation_errors": errors,
        "external_dispatch_count": 0,
    }
