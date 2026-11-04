"""Read-only migration inventory for two incompatible mock SQLite schemas.

NEVER converts, overwrites, drops, or changes a historical COMPLETED record.
A decision-only legacy row is not equivalent to a durable event state. The
report exposes counts and schema status only, not event IDs or customer data.
"""
from pathlib import Path
import sqlite3

_EVENT_COLUMNS = frozenset(("source", "event_id", "digest", "state"))
_DECISION_COLUMNS = frozenset(("source", "event_id", "payload_hash", "decision"))


def inventory_mock_sqlite(path):
    """Inspect an existing file in SQLite mode=ro; fail closed on unknown schema."""
    def result(status, schema=None, counts=None):
        return {"status": status, "schema": schema, "counts": counts,
                "migration_performed": False, "external_dispatch_count": 0}

    try:
        file = Path(path)
        if not file.is_file() or file.is_symlink():
            return result("FILE_NOT_FOUND_OR_UNSUPPORTED")
        # Opening via mode=ro prevents accidental creation and SQL writes.
        uri = file.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            table = db.execute(
                "SELECT type FROM sqlite_master WHERE name='mock_events'"
            ).fetchone()
            if table is None:
                return result("NO_EVENT_TABLE")
            if table != ("table",):
                return result("UNRECOGNIZED_SCHEMA")
            columns = frozenset(row[1] for row in db.execute(
                "PRAGMA table_info('mock_events')"
            ).fetchall())
            if columns == _EVENT_COLUMNS:
                schema = "EVENT_STATE_V1"
                rows = db.execute(
                    "SELECT state,COUNT(*) FROM mock_events GROUP BY state"
                ).fetchall()
                allowed = {"PENDING", "COMPLETED"}
            elif columns == _DECISION_COLUMNS:
                schema = "LEGACY_DECISION_V1"
                rows = db.execute(
                    "SELECT decision,COUNT(*) FROM mock_events GROUP BY decision"
                ).fetchall()
                allowed = {"NEEDS_REVIEW", "READY_FOR_REVIEW"}
            else:
                return result("UNRECOGNIZED_SCHEMA")
            if any(key not in allowed or type(count) is not int or count < 0
                   for key, count in rows):
                return result("UNRECOGNIZED_VALUES", schema)
            counts = {key: 0 for key in sorted(allowed)}
            counts.update(rows)
            counts["TOTAL"] = sum(counts.values())
            return result("INVENTORY_ONLY", schema, counts)
    except (sqlite3.Error, OSError, TypeError, ValueError):
        # Do not include SQLite error strings, paths, identities, or payloads.
        return result("INSPECTION_FAILED")
