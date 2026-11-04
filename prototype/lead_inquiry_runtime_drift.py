"""Fail-closed runtime drift gate for *fixed synthetic* recovery fixtures.

The Python/SQLite baseline is informational. This gate checks actual logical
fixture fingerprints against independently pinned references on THIS runtime.
Only sanitized result data; no customer data or path contents.
"""
from lead_inquiry_disposable_recovery import run_disposable_recovery
from lead_inquiry_manifest_verifier import verify_fixed_fixture_manifest
from lead_inquiry_runtime_contract import runtime_contract

_SCHEMAS = ("EVENT_STATE_V1", "LEGACY_DECISION_V1")


def check_runtime_drift(*, runner=None, verifier=None, runtime_probe=None):
    """Strictly validate both fixtures, never silently update references.

    Injectable functions are for unit tests of the gate only. Do not make
    untrusted callers control these dependencies in an exposed application.
    """
    run = run_disposable_recovery if runner is None else runner
    verify = verify_fixed_fixture_manifest if verifier is None else verifier
    probe = runtime_contract if runtime_probe is None else runtime_probe

    baseline = probe()
    if (type(baseline) is not dict
            or baseline.get("status") not in (
                "BASELINE_ELIGIBLE_NOT_PROVEN", "UNTESTED_PYTHON",
                "SQLITE_BELOW_TEST_BASELINE")
            or type(baseline.get("python")) is not str
            or type(baseline.get("sqlite")) is not str
            or baseline.get("external_dispatch_count") != 0):
        return {"status": "INVALID_RUNTIME", "runtime": None,
                "fixtures": None, "external_dispatch_count": 0}

    statuses = {}
    for schema in _SCHEMAS:
        try:
            record = run(schema=schema)
            result = verify(record)
            if (type(record) is not dict
                    or record.get("status") != "DISPOSABLE_REHEARSAL_PASSED"
                    or record.get("schema") != schema
                    or type(result) is not dict
                    or result.get("valid") is not True
                    or result.get("status") != "FIXED_FIXTURE_MATCH"
                    or result.get("external_dispatch_count") != 0):
                statuses[schema] = "DRIFT_OR_RECOVERY_FAILURE"
            else:
                statuses[schema] = "PINNED_REFERENCE_MATCH"
        except (OSError, ValueError, TypeError, RuntimeError):
            statuses[schema] = "DRIFT_OR_RECOVERY_FAILURE"

    ok = all(s == "PINNED_REFERENCE_MATCH" for s in statuses.values())
    return {"status": "RUNTIME_FIXTURE_VERIFIED" if ok else "RUNTIME_DRIFT_DETECTED",
            "runtime": {"python": baseline["python"], "sqlite": baseline["sqlite"],
                        "screen": baseline["status"]},
            "fixtures": statuses, "external_dispatch_count": 0}
