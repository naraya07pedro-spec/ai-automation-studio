"""Runtime compatibility gate for fixed-fixture mock SQLite rehearsals.

A bounded Python/SQLite version screen is not proof of portability. The
independent pinned-fixture digest gate remains the actual compatibility check.
No user data, paths, environment variables or credentials are reported.
"""
import sqlite3
import sys

# CI baseline, not a promise of support on every platform/runtime build.
_TESTED_PYTHON = (3, 11)
_MIN_SQLITE = (3, 35, 0)


def runtime_contract(*, python_version=None, sqlite_version=None):
    """Return only sanitized runtime-version information and policy status.

    Optional versions are reserved for deterministic negative tests; the
    production caller must use the no-argument form for local interpreter data.
    """
    py = sys.version_info[:3] if python_version is None else python_version
    sql = sqlite3.sqlite_version_info if sqlite_version is None else sqlite_version
    if (not isinstance(py, tuple) or len(py) != 3
            or not isinstance(sql, tuple) or len(sql) != 3
            or any(type(part) is not int or part < 0 for part in (*py, *sql))):
        return {"status": "INVALID_RUNTIME_VERSION", "python": None,
                "sqlite": None, "external_dispatch_count": 0}
    python = ".".join(str(v) for v in py)
    sqlite = ".".join(str(v) for v in sql)
    if py[:2] != _TESTED_PYTHON:
        status = "UNTESTED_PYTHON"
    elif sql < _MIN_SQLITE:
        status = "SQLITE_BELOW_TEST_BASELINE"
    else:
        status = "BASELINE_ELIGIBLE_NOT_PROVEN"
    return {"status": status, "python": python, "sqlite": sqlite,
            "external_dispatch_count": 0}
