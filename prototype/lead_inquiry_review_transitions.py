"""Atomic, versioned *mock review* transitions, not event completion.

Separately stores operator-review state, leaving source event PENDING.
This module does not authenticate users, validate provider outcomes or dispatch.
External caller tokens and attestation would be needed before real effects.
"""

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
        """Deprecated unsafe API: always deny without reading or writing state.

        Preserved only as a compatibility shim so old callers receive a
        deterministic fail-closed answer. The mock grant coordinator is the
        sole review-state writer in the supported prototype path.
        """
        return self._out("LEGACY_PATH_DISABLED")
