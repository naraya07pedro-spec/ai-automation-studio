"""Independent, strict verifier of *fixed synthetic* recovery manifests.

The expected fixture hashes and row counts are pinned reference values. This
module deliberately never imports the fixture producer, opens SQLite, or makes
network calls. A matching report is still NOT trusted production provenance.
"""
import re

_EXPECTED = {
    "EVENT_STATE_V1": {
        "counts": {"PENDING": 1, "COMPLETED": 1, "TOTAL": 2},
        "logical_sha256": "91165b3bbadd107ee90746aa8f6068559d27d30cd85b1dfb00622be3f2adcaa3",
    },
    "LEGACY_DECISION_V1": {
        "counts": {"NEEDS_REVIEW": 1, "READY_FOR_REVIEW": 1, "TOTAL": 2},
        "logical_sha256": "bb09ca11bb7eff5021012e106c54a820fdad06ae4797902f5d149a6081100036",
    },
}
_HEX = re.compile(r"[0-9a-f]{64}\Z")


def verify_fixed_fixture_manifest(report):
    """Return a privacy-safe verdict; never trust report's success label alone."""
    def outcome(valid, status):
        return {"valid": valid, "status": status, "external_dispatch_count": 0}
    if type(report) is not dict or set(report) != {
        "status", "schema", "manifest", "external_dispatch_count",
        "historical_migration_performed", "workspace_retained"
    }:
        return outcome(False, "INVALID_REPORT_SHAPE")
    schema = report["schema"]
    if type(schema) is not str or schema not in _EXPECTED:
        return outcome(False, "UNKNOWN_FIXTURE")
    if (report["status"] != "DISPOSABLE_REHEARSAL_PASSED"
            or report["external_dispatch_count"] != 0
            or report["historical_migration_performed"] is not False
            or report["workspace_retained"] is not False):
        return outcome(False, "UNTRUSTED_RESULT")
    manifest = report["manifest"]
    if type(manifest) is not dict or set(manifest) != {
        "manifest_version", "artifact_checks", "all_artifacts_verified",
        "source_unchanged", "workspace_deleted_on_exit"
    }:
        return outcome(False, "INVALID_MANIFEST")
    if (type(manifest["manifest_version"]) is not int
            or manifest["manifest_version"] != 1
            or manifest["all_artifacts_verified"] is not True
            or manifest["source_unchanged"] is not True
            or manifest["workspace_deleted_on_exit"] is not True):
        return outcome(False, "INVALID_MANIFEST")
    artifacts = manifest["artifact_checks"]
    if type(artifacts) is not dict or set(artifacts) != {"source", "backup", "restored"}:
        return outcome(False, "INVALID_ARTIFACT_SET")
    expected = _EXPECTED[schema]
    for artifact in artifacts.values():
        if type(artifact) is not dict or set(artifact) != {
            "schema", "counts", "logical_sha256", "integrity"
        }:
            return outcome(False, "INVALID_ARTIFACT")
        digest = artifact["logical_sha256"]
        if (artifact["schema"] != schema or artifact["integrity"] != "ok"
                or type(artifact["counts"]) is not dict
                or artifact["counts"] != expected["counts"]
                or any(type(v) is not int for v in artifact["counts"].values())
                or type(digest) is not str or not _HEX.fullmatch(digest)
                or digest != expected["logical_sha256"]):
            return outcome(False, "FIXTURE_REFERENCE_MISMATCH")
    return outcome(True, "FIXED_FIXTURE_MATCH")
