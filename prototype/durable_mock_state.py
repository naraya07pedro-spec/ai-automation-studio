"""SQLite-backed *mock-only* inquiry state, for restart/replay experiments.

Prototype storage choice, NOT the final product persistence architecture.
No network access, external action dispatch, credentials, or full input retention.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


class DurableMockEventStore:
    """Atomically record mock decisions by (source, event_id).

    Replaying the same event with the same payload returns its persisted status.
    Different payload under the same identity is a conflict. Any database error
    propagates: callers must fail closed instead of silently using memory.
    """

    def __init__(self, database_path: str | Path):
        path = Path(database_path)
        if str(path) == ":memory:" or not path.name or not path.parent.exists():
            raise ValueError("an existing directory and an on-disk database path are required")
        self.path = str(path)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS mock_events (
                    source TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK (decision IN
                        ('NEEDS_REVIEW', 'READY_FOR_REVIEW')),
                    PRIMARY KEY (source, event_id)
                )
            """)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.execute("PRAGMA busy_timeout = 5000")
        return db

    @staticmethod
    def _result(status, decision=None):
        return {"status": status, "decision": decision, "external_dispatch_count": 0}

    def record(self, source, event_id, payload, decision):
        """Record a mock decision, or return duplicate/conflict without writes."""
        if any(
            not isinstance(value, str) or not value or value != value.strip()
            or len(value) > 256 for value in (source, event_id)
        ):
            return self._result("INVALID_ID")
        if not source.startswith("mock_"):
            return self._result("MOCK_ONLY")
        if decision not in ("NEEDS_REVIEW", "READY_FOR_REVIEW"):
            return self._result("INVALID_DECISION")
        if not isinstance(payload, dict):
            return self._result("INVALID_PAYLOAD")
        try:
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=True, allow_nan=False)
        except (ValueError, TypeError):
            return self._result("INVALID_PAYLOAD")
        if len(canonical) > 262144:
            return self._result("PAYLOAD_TOO_LARGE")
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        # BEGIN IMMEDIATE serializes competing writers across connections and
        # processes. The unique key is the independent last-line defense.
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute(
                    "SELECT payload_hash, decision FROM mock_events "
                    "WHERE source = ? AND event_id = ?", (source, event_id)
                ).fetchone()
                if existing:
                    db.execute("COMMIT")
                    return self._result(
                        "DUPLICATE" if existing[0] == digest else "CONFLICT",
                        existing[1]
                    )
                db.execute(
                    "INSERT INTO mock_events (source, event_id, payload_hash, decision) "
                    "VALUES (?, ?, ?, ?)", (source, event_id, digest, decision)
                )
                db.execute("COMMIT")
                return self._result("RECORDED", decision)
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
