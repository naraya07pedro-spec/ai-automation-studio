"""Atomic, versioned *mock review* transitions, not event completion.

Separately stores operator-review state, leaving source event PENDING.
This module does not authenticate users, validate provider outcomes or dispatch.
External caller tokens and attestation would be needed before real effects.
"""
from contextlib import closing
import sqlite3

from lead_inquiry_pending_review import _event_ref


class MockReviewTransitions:
    """Compare-and-set for review case lifecycle only (not mock_events state)."""

    def __init__(self, durable_store):
        if not callable(getattr(durable_store, "_connect", None)):
            raise TypeError("durable mock event store required")
        self._store = durable_store
        with self._store._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS mock_review_transitions ("
                "source TEXT NOT NULL, event_id TEXT NOT NULL,"
                "state TEXT NOT NULL CHECK (state IN ('OPEN','ACKNOWLEDGED_HOLD')),"
                "version INTEGER NOT NULL CHECK (version >= 0),"
                "PRIMARY KEY (source,event_id))"
            )

    @staticmethod
    def _out(status, *, version=None):
        return {"status": status, "version": version, "external_dispatch_count": 0,
                "event_state_changed": False}

    @staticmethod
    def _valid_key(source, event_id):
        return all(isinstance(v, str) and 1 <= len(v) <= 256 and v == v.strip()
                   for v in (source, event_id)) and source.startswith("mock_")

    def inspect(self, source, event_id):
        """Inspect only mock review state; never return customer payload/identity."""
        if not self._valid_key(source, event_id):
            return self._out("INVALID_EVENT")
        with self._store._connect() as db:
            if db.execute("SELECT state FROM mock_events WHERE source=? AND event_id=?",
                          (source, event_id)).fetchone() != ("PENDING",):
                return self._out("NOT_PENDING")
            row = db.execute(
                "SELECT state,version FROM mock_review_transitions "
                "WHERE source=? AND event_id=?", (source, event_id)
            ).fetchone()
        return self._out(row[0] if row else "OPEN", version=row[1] if row else 0)

    def acknowledge_hold(self, source, event_id, *, event_ref, expected_version,
                         preflight_status, reviewer_ref):
        """Atomic mock-only OPEN -> ACKNOWLEDGED_HOLD, never completes event.

        PRECHECK_PASSED_NO_TRANSITION is only an input *simulation*;
        not real proof of authorization. State changes in the review table only.
        """
        if not self._valid_key(source, event_id):
            return self._out("INVALID_EVENT")
        if event_ref != _event_ref(source, event_id):
            return self._out("EVENT_MISMATCH")
        if type(expected_version) is not int or expected_version < 0:
            return self._out("INVALID_VERSION")
        if preflight_status != "PRECHECK_PASSED_NO_TRANSITION":
            return self._out("PREFLIGHT_REQUIRED")
        if (not isinstance(reviewer_ref, str) or not 1 <= len(reviewer_ref) <= 128
                or not reviewer_ref.isascii()
                or any(not (c.isalnum() or c in "_-") for c in reviewer_ref)):
            return self._out("INVALID_REVIEWER_REF")
        with self._store._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                event = db.execute(
                    "SELECT state FROM mock_events WHERE source=? AND event_id=?",
                    (source, event_id)
                ).fetchone()
                if event != ("PENDING",):
                    result = self._out("NOT_PENDING")
                else:
                    row = db.execute(
                        "SELECT state,version FROM mock_review_transitions "
                        "WHERE source=? AND event_id=?", (source, event_id)
                    ).fetchone()
                    state, version = row if row else ("OPEN", 0)
                    if version != expected_version:
                        result = self._out("STALE_VERSION", version=version)
                    elif state != "OPEN":
                        result = self._out("ALREADY_HELD", version=version)
                    elif row:
                        # Reserved for future transitions; currently unreachable
                        # after OPEN -> HOLD, but keep comparison defensive.
                        cur = db.execute(
                            "UPDATE mock_review_transitions SET state='ACKNOWLEDGED_HOLD',"
                            "version=version+1 WHERE source=? AND event_id=? "
                            "AND state='OPEN' AND version=?",
                            (source, event_id, expected_version)
                        )
                        result = self._out(
                            "ACKNOWLEDGED_HOLD" if cur.rowcount == 1 else "STALE_VERSION",
                            version=expected_version + 1 if cur.rowcount == 1 else version
                        )
                    else:
                        db.execute(
                            "INSERT INTO mock_review_transitions "
                            "(source,event_id,state,version) VALUES (?,?,?,1)",
                            (source, event_id, "ACKNOWLEDGED_HOLD")
                        )
                        result = self._out("ACKNOWLEDGED_HOLD", version=1)
                db.execute("COMMIT")
                return result
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
