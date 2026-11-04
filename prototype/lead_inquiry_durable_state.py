"""Durable, mock-only event state prototype backed by a local SQLite file.

This is a bounded standard-library experiment, NOT selection of the final
database, agent runtime, or platform architecture. Never dispatches actions.

PENDING records are *not* replayed automatically after a crash. On reopening,
the caller must reconcile/inspect them before deciding what happens next.
"""
import hashlib
from contextlib import contextmanager
import json
import sqlite3
from pathlib import Path


class DurableMockEventStore:
    def __init__(self, file_path, *, max_events=1000):
        if type(max_events) is not int or max_events < 1:
            raise ValueError("max_events must be a positive integer")
        path = Path(file_path)
        if str(file_path) == ":memory:" or path.exists() and not path.is_file():
            raise ValueError("a regular persistent file path is required")
        self.path = str(path)
        self.max_events = max_events
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS mock_events ("
                "source TEXT NOT NULL, event_id TEXT NOT NULL, digest TEXT NOT NULL,"
                "state TEXT NOT NULL CHECK(state IN ('PENDING','COMPLETED')),"
                "PRIMARY KEY(source, event_id))"
            )

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _out(status):
        return {"status": status, "external_dispatch_count": 0}

    @staticmethod
    def _ids(source, event_id):
        return all(isinstance(s, str) and bool(s) and s == s.strip()
                   and len(s) <= 256 for s in (source, event_id))

    def reserve(self, source, event_id, payload):
        """Atomically reserve only a NEW mock event, without external effects."""
        if not self._ids(source, event_id):
            return self._out("INVALID_ID")
        if not source.startswith("mock_"):
            return self._out("MOCK_ONLY")
        if not isinstance(payload, dict):
            return self._out("INVALID_PAYLOAD")
        try:
            serialized = json.dumps(payload, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":"), allow_nan=False)
            if len(serialized) > 262144:
                return self._out("PAYLOAD_TOO_LARGE")
            digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        except (TypeError, ValueError, OverflowError):
            return self._out("INVALID_PAYLOAD")

        with self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                old = conn.execute(
                    "SELECT digest, state FROM mock_events WHERE source=? AND event_id=?",
                    (source, event_id),
                ).fetchone()
                if old:
                    status = ("CONFLICT" if old[0] != digest else
                              "NEEDS_RECONCILIATION" if old[1] == "PENDING" else
                              "DUPLICATE")
                elif conn.execute("SELECT COUNT(*) FROM mock_events").fetchone()[0] >= self.max_events:
                    status = "CAPACITY_EXHAUSTED"
                else:
                    conn.execute(
                        "INSERT INTO mock_events (source,event_id,digest,state) VALUES (?,?,?,'PENDING')",
                        (source, event_id, digest),
                    )
                    status = "RESERVED_PENDING"
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return self._out(status)

    def mark_mock_completed(self, source, event_id):
        """Deprecated completion API: irrevocably disabled in the mock prototype.

        A review acknowledgement or a valid grant cannot establish that an
        external effect occurred. Never mutate event state through this path.
        """
        return self._out("DIRECT_COMPLETION_DISABLED")

    def state(self, source, event_id):
        if not self._ids(source, event_id):
            return "INVALID_ID"
        with self._connect() as conn:
            record = conn.execute(
                "SELECT state FROM mock_events WHERE source=? AND event_id=?",
                (source, event_id),
            ).fetchone()
        return record[0] if record else "NOT_FOUND"
