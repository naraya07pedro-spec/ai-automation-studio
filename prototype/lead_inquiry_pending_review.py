"""Inspection + acknowledgement of unresolved mock-only reservations.

A review acknowledgement is *not* resolution, completion, authorization or
evidence of an external effect. The underlying PENDING state never changes.
No operator authentication exists in this prototype: the reviewer reference
is caller-supplied and must not be presented as verified identity.
"""
from contextlib import closing
from hashlib import sha256
import sqlite3

_ACK = "I_REVIEWED_UNRESOLVED_MOCK_EVENT"


def _event_ref(source, event_id):
    return sha256((source + "\0" + event_id).encode("utf-8")).hexdigest()


def _safe_reference(value):
    return (isinstance(value, str) and 1 <= len(value) <= 128
            and value.isascii() and all(c.isalnum() or c in "-_" for c in value))


class MockPendingReview:
    """Separate read and acknowledgement surface for a durable mock store."""

    def __init__(self, durable_store):
        if not callable(getattr(durable_store, "_connect", None)):
            raise TypeError("a durable mock event store is required")
        self._store = durable_store
        with self._connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS mock_pending_reviews ("
                "source TEXT NOT NULL, event_id TEXT NOT NULL,"
                "reviewer_ref TEXT NOT NULL, review_kind TEXT NOT NULL,"
                "PRIMARY KEY(source, event_id))"
            )

    def _connection(self):
        # The store uses an explicit closing context manager.
        return self._store._connect()

    @staticmethod
    def _result(status):
        return {"status": status, "external_dispatch_count": 0}

    def list_pending(self, *, limit=50):
        """Return opaque references; never expose source IDs, payload or notes."""
        if type(limit) is not int or not (1 <= limit <= 100):
            raise ValueError("limit must be an integer from 1 to 100")
        with self._connection() as db:
            rows = db.execute(
                "SELECT m.source,m.event_id,r.review_kind IS NOT NULL "
                "FROM mock_events AS m LEFT JOIN mock_pending_reviews AS r "
                "ON m.source=r.source AND m.event_id=r.event_id "
                "WHERE m.state='PENDING' ORDER BY m.source,m.event_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"event_ref": _event_ref(source, identity),
             "state": "PENDING", "acknowledged": bool(reviewed)}
            for source, identity, reviewed in rows
        ]

    def acknowledge(self, event_ref, reviewer_ref, acknowledgement):
        """Write once if exactly one pending event matches the opaque reference.

        This deliberately cannot turn PENDING into COMPLETED or dispatch work.
        """
        if (not isinstance(event_ref, str) or len(event_ref) != 64
                or any(c not in "0123456789abcdef" for c in event_ref)):
            return self._result("INVALID_EVENT_REF")
        if not _safe_reference(reviewer_ref):
            return self._result("INVALID_REVIEWER_REF")
        if acknowledgement != _ACK:
            return self._result("ACKNOWLEDGEMENT_REQUIRED")

        with self._connection() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                # Capped scan: fail closed rather than allowing unbounded work.
                rows = db.execute(
                    "SELECT source,event_id FROM mock_events "
                    "WHERE state='PENDING' ORDER BY source,event_id LIMIT 10001"
                ).fetchall()
                if len(rows) > 10000:
                    db.execute("COMMIT")
                    return self._result("REVIEW_CAPACITY_EXHAUSTED")
                matches = [(source, identity) for source, identity in rows
                           if _event_ref(source, identity) == event_ref]
                if len(matches) != 1:
                    db.execute("COMMIT")
                    return self._result("NOT_FOUND_OR_NOT_PENDING")
                source, identity = matches[0]
                existing = db.execute(
                    "SELECT 1 FROM mock_pending_reviews "
                    "WHERE source=? AND event_id=?", (source, identity)
                ).fetchone()
                if existing:
                    status = "ALREADY_ACKNOWLEDGED"
                else:
                    db.execute(
                        "INSERT INTO mock_pending_reviews "
                        "(source,event_id,reviewer_ref,review_kind) VALUES (?,?,?,?)",
                        (source, identity, reviewer_ref, "UNRESOLVED_ACKNOWLEDGED")
                    )
                    status = "ACKNOWLEDGED_STILL_PENDING"
                db.execute("COMMIT")
                return self._result(status)
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
