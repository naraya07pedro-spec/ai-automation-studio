"""Immutable, reviewed snapshots of observed *synthetic* runtime CI evidence.

These are human-reviewed records derived from successful GitHub Actions logs,
not credentials, provider attestations or automatically discovered capabilities.
Runtime versions must match exactly. Unknown versions remain unregistered.
"""
from collections.abc import Mapping
import re

_RECORDS = (
    {"python": "3.11.17", "sqlite": "3.45.1", "os_family": "ubuntu",
     "run_id": 41792558426, "job_name": "Python 3.11 mock verification",
     "tests": 267, "fixture_status": "PINNED_REFERENCE_MATCH"},
    {"python": "3.12.16", "sqlite": "3.45.1", "os_family": "ubuntu",
     "run_id": 41792558426, "job_name": "Python 3.12 mock verification",
     "tests": 267, "fixture_status": "PINNED_REFERENCE_MATCH"},
    {"python": "3.13.17", "sqlite": "3.45.1", "os_family": "ubuntu",
     "run_id": 41792558426, "job_name": "Python 3.13 mock verification",
     "tests": 267, "fixture_status": "PINNED_REFERENCE_MATCH"},
)
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")


def registry_snapshot():
    """A detached, aggregate, redacted copy, never writable global data."""
    return [dict(record) for record in _RECORDS]


def lookup_runtime_evidence(python, sqlite, *, os_family="ubuntu"):
    """Observed CI evidence ONLY, not suitability for production."""
    output = {"status": "UNREGISTERED_RUNTIME", "evidence": None,
              "production_supported": False, "external_dispatch_count": 0}
    if (type(python) is not str or not _VERSION.fullmatch(python)
            or type(sqlite) is not str or not _VERSION.fullmatch(sqlite)
            or type(os_family) is not str or os_family != "ubuntu"):
        output["status"] = "INVALID_OR_UNSUPPORTED_RUNTIME"
        return output
    for record in _RECORDS:
        if (record["python"], record["sqlite"], record["os_family"]) == (
                python, sqlite, os_family):
            output["status"] = "HISTORICAL_SYNTHETIC_CI_MATCH"
            output["evidence"] = dict(record)
            return output
    return output


def classify_runtime_evidence(current_drift, *, os_family="ubuntu"):
    """Never promote historic evidence when the current drift gate has failed."""
    if (type(current_drift) is not dict or
            current_drift.get("status") != "RUNTIME_FIXTURE_VERIFIED"
            or type(current_drift.get("runtime")) is not dict
            or type(current_drift.get("fixtures")) is not dict
            or set(current_drift["fixtures"]) != {
                "EVENT_STATE_V1", "LEGACY_DECISION_V1"}
            or any(value != "PINNED_REFERENCE_MATCH"
                   for value in current_drift["fixtures"].values())
            or current_drift.get("external_dispatch_count") != 0):
        return {"status": "CURRENT_FIXTURE_NOT_VERIFIED", "evidence": None,
                "production_supported": False, "external_dispatch_count": 0}
    runtime = current_drift["runtime"]
    result = lookup_runtime_evidence(runtime.get("python"), runtime.get("sqlite"),
                                     os_family=os_family)
    return {"status": ("REGISTERED_AND_CURRENT_FIXTURE_MATCH"
                       if result["status"] == "HISTORICAL_SYNTHETIC_CI_MATCH"
                       else "CURRENT_FIXTURE_MATCH_RUNTIME_UNREGISTERED"),
            "evidence": result["evidence"], "production_supported": False,
            "external_dispatch_count": 0}
