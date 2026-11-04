"""Disposable, internally built SQLite recovery demonstration, mock data only.

No request-supplied paths, identities or credentials. Files created in a
temporary directory are removed on exit, including failure. Fingerprints of
synthetic data are NOT proof of production backup custody.
"""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import tempfile

from lead_inquiry_legacy_inventory import inventory_mock_sqlite
from lead_inquiry_manifest_verifier import verify_fixed_fixture_manifest
from lead_inquiry_synthetic_recovery import (
    _check, _fingerprint, _verified_synthetic, rehearse_synthetic_backup_restore,
)

_MARKER = "AI_AUTOMATION_STUDIO_SYNTHETIC_V1"
_SCHEMAS = ("EVENT_STATE_V1", "LEGACY_DECISION_V1")


def _result(status, schema=None, manifest=None):
    return {"status": status, "schema": schema, "manifest": manifest,
            "external_dispatch_count": 0, "historical_migration_performed": False,
            "workspace_retained": False}


def _fixture(path, schema):
    with closing(sqlite3.connect(path)) as db:
        if schema == "EVENT_STATE_V1":
            db.execute("CREATE TABLE mock_events (source TEXT, event_id TEXT,"
                       "digest TEXT, state TEXT)")
            db.executemany("INSERT INTO mock_events VALUES (?,?,?,?)", (
                ("mock_fixture", "synthetic_pending", "synthetic_a", "PENDING"),
                ("mock_fixture", "synthetic_old", "synthetic_b", "COMPLETED"),
            ))
        else:
            db.execute("CREATE TABLE mock_events (source TEXT, event_id TEXT,"
                       "payload_hash TEXT, decision TEXT)")
            db.executemany("INSERT INTO mock_events VALUES (?,?,?,?)", (
                ("mock_fixture", "synthetic_review", "synthetic_a", "NEEDS_REVIEW"),
                ("mock_fixture", "synthetic_ready", "synthetic_b", "READY_FOR_REVIEW"),
            ))
        db.execute("CREATE TABLE mock_synthetic_fixture_marker (marker TEXT)")
        db.execute("INSERT INTO mock_synthetic_fixture_marker VALUES (?)", (_MARKER,))
        db.commit()


def _verify_artifact(path, schema):
    inventory = inventory_mock_sqlite(path)
    if inventory["status"] != "INVENTORY_ONLY" or inventory["schema"] != schema:
        return None
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro",
                                 uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        if not _verified_synthetic(db) or not _check(db):
            return None
        return {"schema": schema, "counts": inventory["counts"],
                "logical_sha256": _fingerprint(db), "integrity": "ok"}


def run_disposable_recovery(*, schema="EVENT_STATE_V1"):
    """Run only on internally generated test data in a fresh temp directory.

    The deterministic manifest is emitted only after verification of source,
    backup and restored copies. No path or record identity is returned.
    """
    if type(schema) is not str or schema not in _SCHEMAS:
        return _result("UNSUPPORTED_SCHEMA")
    try:
        with tempfile.TemporaryDirectory(prefix="mock-recovery-") as directory:
            root = Path(directory)
            source, backup, restored = (root / n for n in (
                "source.sqlite", "backup.sqlite", "restored.sqlite"))
            _fixture(source, schema)
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            original = _verify_artifact(source, schema)
            if original is None:
                return _result("SOURCE_VERIFY_FAILED", schema)
            recovery = rehearse_synthetic_backup_restore(source, backup, restored)
            if recovery["status"] != "SYNTHETIC_REHEARSAL_PASSED":
                return _result("RECOVERY_FAILED", schema)
            artifacts = {"source": original,
                         "backup": _verify_artifact(backup, schema),
                         "restored": _verify_artifact(restored, schema)}
            if (any(v is None for v in artifacts.values())
                    or not artifacts["source"] == artifacts["backup"] == artifacts["restored"]
                    or before != hashlib.sha256(source.read_bytes()).hexdigest()):
                return _result("ARTIFACT_MISMATCH", schema)
            candidate = _result("DISPOSABLE_REHEARSAL_PASSED", schema, {
                "manifest_version": 1, "artifact_checks": artifacts,
                "all_artifacts_verified": True, "source_unchanged": True,
                "workspace_deleted_on_exit": True})
            if verify_fixed_fixture_manifest(candidate)["valid"] is not True:
                return _result("FIXTURE_REFERENCE_MISMATCH", schema)
            return candidate
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return _result("WORKSPACE_FAILED", schema)
