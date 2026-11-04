"""Offline SQLite backup/restore rehearsal for marked synthetic mock fixtures only.

Never operate on an unmarked database. Never replace a destination. This is
not a production backup service, secure snapshot store or recovery guarantee.
"""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3

from lead_inquiry_legacy_inventory import inventory_mock_sqlite

_MARKER = "AI_AUTOMATION_STUDIO_SYNTHETIC_V1"


def _report(status, schema=None, count=None, matching=None):
    return {"status": status, "schema": schema, "total_rows": count,
            "restored_matches_backup": matching,
            "external_dispatch_count": 0, "historical_migration_performed": False}


def _readable_file(path):
    p = Path(path)
    return p.is_file() and not p.is_symlink()


def _connect_ro(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro",
                         uri=True, timeout=3)
    db.execute("PRAGMA query_only=ON")
    return db


def _verified_synthetic(db):
    try:
        result = db.execute(
            "SELECT marker FROM mock_synthetic_fixture_marker"
        ).fetchall()
        return result == [(_MARKER,)]
    except sqlite3.Error:
        return False


def _fingerprint(db):
    """Logical comparison of two offline copies, never return raw SQL/values."""
    digest = hashlib.sha256()
    for statement in db.iterdump():
        digest.update(statement.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _check(db):
    return db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


def _new_destination(path):
    """Reserve output exclusively and do not overwrite an existing object."""
    p = Path(path)
    if not p.parent.is_dir() or p.is_symlink():
        raise ValueError("destination parent missing or path unsupported")
    descriptor = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    return p


def rehearse_synthetic_backup_restore(source, backup, restored):
    """Create two new synthetic-only DB files using SQLite backup API.

    The source remains untouched. On failure, partial outputs are left
    visibly present rather than silently deleting forensic evidence.
    """
    try:
        src, dst, out = Path(source), Path(backup), Path(restored)
        if len({str(p.absolute()) for p in (src, dst, out)}) != 3:
            return _report("PATH_COLLISION")
        if not _readable_file(src):
            return _report("SOURCE_NOT_FOUND_OR_UNSUPPORTED")
        inspection = inventory_mock_sqlite(src)
        if inspection["status"] != "INVENTORY_ONLY":
            return _report("UNSUPPORTED_SOURCE_SCHEMA")
        with closing(_connect_ro(src)) as origin:
            if not _verified_synthetic(origin):
                return _report("SYNTHETIC_MARKER_REQUIRED")
            if not _check(origin):
                return _report("SOURCE_INTEGRITY_FAILED")
            _new_destination(dst)
            with closing(sqlite3.connect(dst, timeout=3)) as copy:
                origin.backup(copy)
        with closing(_connect_ro(dst)) as copy:
            if not _verified_synthetic(copy) or not _check(copy):
                return _report("BACKUP_VERIFY_FAILED")
            backup_hash = _fingerprint(copy)
            _new_destination(out)
            with closing(sqlite3.connect(out, timeout=3)) as destination:
                copy.backup(destination)
        with closing(_connect_ro(out)) as restoration:
            if not _verified_synthetic(restoration) or not _check(restoration):
                return _report("RESTORE_VERIFY_FAILED")
            same = _fingerprint(restoration) == backup_hash
        backup_inventory = inventory_mock_sqlite(dst)
        restored_inventory = inventory_mock_sqlite(out)
        if (not same or backup_inventory != restored_inventory
                or backup_inventory["status"] != "INVENTORY_ONLY"
                or backup_inventory["schema"] != inspection["schema"]):
            return _report("RESTORE_MISMATCH")
        return _report("SYNTHETIC_REHEARSAL_PASSED", backup_inventory["schema"],
                       backup_inventory["counts"]["TOTAL"], True)
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return _report("REHEARSAL_FAILED")
