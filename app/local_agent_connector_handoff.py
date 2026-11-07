"""Owner-approved linkage from a COMPLETED local agent plan to a saved workflow receipt.

This does NOT authorize a model to dispatch a tool. It binds provenance in a
separate durable ledger, then delegates the only possible HTTPS POST to the
existing managed connector's preview/one-shot delivery ledger. The payload is
the selected deterministic workflow receipt, NOT the agent's private results.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
from local_private_sqlite_path import prepare_private_sqlite, PrivateSQLitePathError
import re
import sqlite3
import threading

from local_agent_task_plans import ID_PATTERN as PLAN_ID, PlanError
from local_connector_manager import ManagedConnectorError, _validate_id
from local_outbound_webhook import OutboundError
from local_workflow_core import ID_PATTERN as WORKFLOW_ID, EVENT_PATTERN


_HEX64 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_MAX_RECORDS = 200


class AgentConnectorHandoffError(Exception):
    def __init__(self, reason, status=409):
        super().__init__(reason)
        self.status = status


def _sha(obj):
    return hashlib.sha256(json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ids(data, fields):
    if type(data) is not dict or set(data) != fields:
        raise AgentConnectorHandoffError("invalid_handoff_fields", 422)
    pid, cid, wid, eid = (data.get(key) for key in
                          ("plan_id", "connector_id", "workflow_id", "event_id"))
    if (type(pid) is not str or not PLAN_ID.fullmatch(pid) or
            type(wid) is not str or not WORKFLOW_ID.fullmatch(wid) or
            type(eid) is not str or not EVENT_PATTERN.fullmatch(eid)):
        raise AgentConnectorHandoffError("invalid_handoff_identity", 422)
    try:
        _validate_id(cid)
    except ManagedConnectorError:
        raise AgentConnectorHandoffError("invalid_handoff_identity", 422) from None
    return pid, cid, wid, eid


class AgentConnectorHandoff:
    """Durable local approval boundary; a preview alone never sends anything."""

    def __init__(self, plans, core, manager, database=":memory:"):
        if (plans is None or core is None or manager is None or
                getattr(manager, "_vault", None) is None):
            raise AgentConnectorHandoffError(
                "encrypted_managed_connector_and_plan_required", 403)
        self.plans, self.core, self.manager = plans, core, manager
        self.lock = threading.RLock()
        try:
            path = prepare_private_sqlite(database)
        except PrivateSQLitePathError:
            raise AgentConnectorHandoffError("handoff_unsafe_storage", 503) from None
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA synchronous=FULL")
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS agent_connector_handoffs (
                plan_id TEXT PRIMARY KEY,
                connector_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                plan_sha256 TEXT NOT NULL,
                workflow_sha256 TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                preview_token_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                delivery_id TEXT,
                http_status INTEGER,
                updated_utc TEXT NOT NULL,
                UNIQUE(connector_id, workflow_id, event_id)
            )""")

    def _source(self, pid, wid, eid):
        try:
            plan = self.plans.get(pid)
        except PlanError:
            raise AgentConnectorHandoffError("handoff_plan_unavailable", 409) from None
        if (plan["status"] != "COMPLETED" or
                plan["next_index"] != len(plan["steps"]) or
                len(plan["steps"]) != len(plan["results"])):
            raise AgentConnectorHandoffError("handoff_plan_not_completed", 409)
        # No plan result is sent; only its digest is included in local evidence.
        evidence = {k: plan[k] for k in
                    ("plan_id", "model", "goal", "steps", "results")}
        code, run = self.core.get_run(wid, eid)
        if code != 200:
            raise AgentConnectorHandoffError("handoff_workflow_receipt_missing", 404)
        if run["workflow_id"] != wid or run["event_id"] != eid:
            raise AgentConnectorHandoffError("handoff_workflow_receipt_invalid", 409)
        return _sha(evidence), _sha(run)

    @staticmethod
    def _public(row):
        return {
            "plan_id": row[0], "connector_id": row[1],
            "workflow_id": row[2], "event_id": row[3],
            "plan_sha256": row[4], "workflow_sha256": row[5],
            "payload_sha256": row[6], "status": row[8],
            "delivery_id": row[9], "http_status": row[10],
            "updated_utc": row[11], "downstream_success_verified": False,
            "plan_results_sent": False, "no_automatic_retry": True
        }

    def _row(self, pid):
        return self.db.execute("""SELECT plan_id,connector_id,workflow_id,event_id,
            plan_sha256,workflow_sha256,payload_sha256,preview_token_sha256,
            status,delivery_id,http_status,updated_utc
            FROM agent_connector_handoffs WHERE plan_id=?""", (pid,)).fetchone()

    def preview(self, request):
        pid, cid, wid, eid = _ids(request, {
            "plan_id", "connector_id", "workflow_id", "event_id"})
        with self.lock:
            plan_hash, run_hash = self._source(pid, wid, eid)
            old = self._row(pid)
            if old is not None and (
                    old[8] != "PREVIEWED" or old[1:4] != (cid, wid, eid)):
                raise AgentConnectorHandoffError("handoff_already_bound_or_claimed")
            other = self.db.execute("""SELECT plan_id FROM agent_connector_handoffs
                WHERE connector_id=? AND workflow_id=? AND event_id=?""",
                (cid, wid, eid)).fetchone()
            if other is not None and other[0] != pid:
                raise AgentConnectorHandoffError("handoff_event_already_bound")
            # Existing managed preview persists an unspent single-use token.
            # It applies allowlisted HTTPS, exact connector and immutable
            # local-workflow receipt validation, independent from model text.
            try:
                p = self.manager.preview({
                    "connector_id": cid, "workflow_id": wid, "event_id": eid})
            except (ManagedConnectorError, OutboundError) as exc:
                raise AgentConnectorHandoffError(str(exc), exc.status) from None
            if (p.get("status") != "PREVIEW_ONLY" or
                    p.get("external_write_count") != 0 or
                    type(p.get("preview_token")) is not str or
                    type(p.get("payload_sha256")) is not str):
                raise AgentConnectorHandoffError("handoff_connector_preview_invalid", 503)
            token_hash = hashlib.sha256(
                p["preview_token"].encode("ascii")).hexdigest()
            try:
                with self.db:
                    self.db.execute("""INSERT INTO agent_connector_handoffs
                        (plan_id,connector_id,workflow_id,event_id,plan_sha256,
                         workflow_sha256,payload_sha256,preview_token_sha256,
                         status,updated_utc)
                         VALUES(?,?,?,?,?,?,?,?,'PREVIEWED',?)
                         ON CONFLICT(plan_id) DO UPDATE SET
                         plan_sha256=excluded.plan_sha256,
                         workflow_sha256=excluded.workflow_sha256,
                         payload_sha256=excluded.payload_sha256,
                         preview_token_sha256=excluded.preview_token_sha256,
                         updated_utc=excluded.updated_utc
                         WHERE agent_connector_handoffs.status='PREVIEWED'
                         AND agent_connector_handoffs.connector_id=excluded.connector_id
                         AND agent_connector_handoffs.workflow_id=excluded.workflow_id
                         AND agent_connector_handoffs.event_id=excluded.event_id""",
                        (pid, cid, wid, eid, plan_hash, run_hash,
                         p["payload_sha256"], token_hash, _now()))
            except sqlite3.Error:
                raise AgentConnectorHandoffError("handoff_storage_unavailable", 503) from None
            return {**p, "plan_id": pid, "plan_sha256": plan_hash,
                    "workflow_sha256": run_hash,
                    "plan_results_sent": False,
                    "model_granted_connector_access": False,
                    "owner_password_required_for_commit": True}

    def commit(self, request):
        pid, cid, wid, eid = _ids(request, {
            "plan_id", "connector_id", "workflow_id", "event_id",
            "plan_sha256", "workflow_sha256", "payload_sha256", "preview_token"})
        token = request["preview_token"]
        if (type(token) is not str or len(token) != 43 or
                any(type(request[k]) is not str or not _HEX64.fullmatch(request[k])
                    for k in ("plan_sha256", "workflow_sha256", "payload_sha256"))):
            raise AgentConnectorHandoffError("invalid_handoff_commit", 422)
        with self.lock:
            row = self._row(pid)
            if row is None or row[8] != "PREVIEWED" or row[1:4] != (cid, wid, eid):
                raise AgentConnectorHandoffError("handoff_unavailable_or_claimed")
            plan_hash, run_hash = self._source(pid, wid, eid)
            if (not hmac.compare_digest(plan_hash, row[4]) or
                    not hmac.compare_digest(run_hash, row[5]) or
                    not hmac.compare_digest(plan_hash, request["plan_sha256"]) or
                    not hmac.compare_digest(run_hash, request["workflow_sha256"]) or
                    not hmac.compare_digest(row[6], request["payload_sha256"]) or
                    not hmac.compare_digest(
                        row[7], hashlib.sha256(token.encode("ascii")).hexdigest())):
                raise AgentConnectorHandoffError("handoff_source_or_preview_changed")
            # Claim first. On timeout/crash this local record, as well as the
            # underlying connector ledger, forbids an automatic resend.
            try:
                with self.db:
                    changed = self.db.execute("""UPDATE agent_connector_handoffs
                        SET status='IN_FLIGHT',updated_utc=?
                        WHERE plan_id=? AND status='PREVIEWED'
                        AND preview_token_sha256=?""",
                        (_now(), pid, row[7])).rowcount
                    if changed != 1:
                        raise AgentConnectorHandoffError("handoff_already_claimed")
            except sqlite3.Error:
                raise AgentConnectorHandoffError("handoff_storage_unavailable", 503) from None
            try:
                receipt = self.manager.commit({
                    "connector_id": cid, "workflow_id": wid, "event_id": eid,
                    "payload_sha256": request["payload_sha256"],
                    "preview_token": token})
            except (ManagedConnectorError, OutboundError, OSError, sqlite3.Error):
                # May have crossed a network boundary: do not roll back claim.
                try:
                    with self.db:
                        self.db.execute("""UPDATE agent_connector_handoffs
                            SET status='REVIEW_REQUIRED',updated_utc=?
                            WHERE plan_id=? AND status='IN_FLIGHT'""", (_now(), pid))
                except sqlite3.Error:
                    pass
                raise AgentConnectorHandoffError(
                    "handoff_outcome_uncertain_inspect_ledgers", 503) from None
            outcome = receipt.get("status")
            if outcome not in ("REMOTE_ACCEPTED", "OUTCOME_UNCERTAIN"):
                outcome = "REVIEW_REQUIRED"
            try:
                with self.db:
                    self.db.execute("""UPDATE agent_connector_handoffs
                        SET status=?,delivery_id=?,http_status=?,updated_utc=?
                        WHERE plan_id=? AND status='IN_FLIGHT'""",
                        (outcome, receipt.get("delivery_id"),
                         receipt.get("http_status"), _now(), pid))
            except sqlite3.Error:
                raise AgentConnectorHandoffError(
                    "handoff_outcome_uncertain_inspect_ledgers", 503) from None
            return {**receipt, "plan_id": pid, "plan_sha256": plan_hash,
                    "workflow_sha256": run_hash,
                    "plan_results_sent": False,
                    "downstream_success_verified": False,
                    "no_automatic_retry": True}

    def history(self):
        with self.lock:
            try:
                rows = self.db.execute("""SELECT plan_id,connector_id,workflow_id,
                    event_id,plan_sha256,workflow_sha256,payload_sha256,
                    preview_token_sha256,status,delivery_id,http_status,updated_utc
                    FROM agent_connector_handoffs ORDER BY updated_utc DESC
                    LIMIT 25""").fetchall()
            except sqlite3.Error:
                raise AgentConnectorHandoffError("handoff_storage_unavailable", 503) from None
        return {"handoffs": [self._public(row) for row in rows],
                "external_write_count_this_read": 0,
                "downstream_success_verified": False,
                "note": "Inspect managed connector ledger and downstream system separately"}

    def close(self):
        with self.lock:
            self.db.close()
