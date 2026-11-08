"""Explicit owner-reviewed transfer of a completed, typed Agent result into
a NEW owner-scoped note for a later separately approved target plan.

No target-plan mutation, model invocation, automatic delivery or external write.
A durable CLAIMED checkpoint precedes the cross-SQLite note creation. A crash
at that boundary requires owner inspection, never blind retry.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from hmac import compare_digest
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading

from local_agent_delegation import validate_delegation, DelegationError
from local_durable_plan_queue import _digest, _fingerprint, _security_fingerprint, DurableQueueError
from local_scoped_agent_memory import NOTE_ID, RETENTION_DAYS, ScopedMemoryError
from local_private_sqlite_path import prepare_private_sqlite, PrivateSQLitePathError

HEX32 = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
HEX64 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
MAX_PREVIEWS = 160
PREVIEW_MINUTES = 10


class TypedHandoffError(RuntimeError):
    def __init__(self, reason, status=409):
        super().__init__(reason)
        self.status = status


def _utc():
    return datetime.now(timezone.utc)


def _typed(result, step):
    """Only code-owned deterministic tools, NEVER arbitrary AI free text."""
    if (type(result) is not dict or set(result) != {"tool", "output"}
            or result["tool"] != step.get("tool")
            or type(result["output"]) is not str):
        raise TypedHandoffError("handoff_result_untrusted", 409)
    value = result["output"]
    if not 1 <= len(value) <= 80:
        raise TypedHandoffError("handoff_result_untrusted", 409)
    if result["tool"] == "calculate":
        if not re.fullmatch(r"-?(?:0|[1-9][0-9]{0,31})(?:\.[0-9]{1,24})?", value):
            raise TypedHandoffError("handoff_result_untrusted", 409)
        try:
            if not Decimal(value).is_finite():
                raise ValueError
        except (ValueError, InvalidOperation):
            raise TypedHandoffError("handoff_result_untrusted", 409) from None
        kind = "DECIMAL_NUMBER"
    elif result["tool"] == "utc_now":
        try:
            parsed = datetime.fromisoformat(value)
            if (parsed.tzinfo is None or parsed.utcoffset() != timedelta(0)
                    or not re.fullmatch(
                        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", value)):
                raise ValueError
        except ValueError:
            raise TypedHandoffError("handoff_result_untrusted", 409) from None
        kind = "UTC_TIMESTAMP"
    else:
        raise TypedHandoffError("handoff_tool_not_transferable", 403)
    return {"kind": kind, "value": value}


def _validate_preview(data):
    if (type(data) is not dict or set(data) != {
            "job_id", "source_step_index", "target_note_id", "retention_days"}):
        raise TypedHandoffError("invalid_typed_handoff_fields", 422)
    if (type(data["job_id"]) is not str
            or re.fullmatch(r"[a-z][a-z0-9_-]{2,39}",data["job_id"]) is None
            or type(data["source_step_index"]) is not int
            or not 0 <= data["source_step_index"] <= 2
            or type(data["target_note_id"]) is not str
            or NOTE_ID.fullmatch(data["target_note_id"]) is None
            or type(data["retention_days"]) is not int
            or data["retention_days"] not in RETENTION_DAYS):
        raise TypedHandoffError("invalid_typed_handoff_fields", 422)
    return data


class LocalTypedAgentHandoff:
    """Crash-aware owner-gated typed result -> scoped note transfer.

    Only a NEW target note is created. An existing approved target plan never
    silently gains it; the owner must build/approve a new plan selecting it.
    """
    def __init__(self, queue, database, *, clock=None):
        if (queue is None or queue.plans.agent_profiles is None
                or queue.plans.scoped_memory_store is None):
            raise TypedHandoffError("typed_handoff_dependencies_disabled", 403)
        self.queue = queue
        self.plans = queue.plans
        self.vault = self.plans.scoped_memory_store
        self.clock = clock or _utc
        self.lock = threading.RLock()
        try:
            location = prepare_private_sqlite(database)
        except PrivateSQLitePathError:
            raise TypedHandoffError("typed_handoff_path_rejected", 403) from None
        self.db = sqlite3.connect(location, check_same_thread=False, timeout=5)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS agent_typed_handoffs (
                preview_id TEXT PRIMARY KEY,
                request_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                created_utc TEXT NOT NULL,
                target_note_revision INTEGER,
                committed_utc TEXT
            )""")

    def _now(self):
        now = self.clock()
        if not isinstance(now,datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise TypedHandoffError("typed_handoff_clock_unavailable", 503)
        return now.astimezone(timezone.utc)

    def _snapshot(self, request):
        with self.queue.lock:
            try:
                job = self.queue.get(request["job_id"])
                meta, nodes = job["delegation"], job["nodes"]
                if (meta is None or len(nodes) != 2
                        or nodes[0]["status"] != "COMPLETED"
                        or nodes[1]["status"] != "WAITING"
                        or job["status"] not in ("ACTIVE", "PAUSED")):
                    raise TypedHandoffError("handoff_source_not_completed", 409)
                validate_delegation(meta, nodes, self.plans)
                source = self.plans.get(nodes[0]["plan_id"])
                target = self.plans.get(nodes[1]["plan_id"])
                if (source["status"] != "COMPLETED"
                        or target["status"] != "APPROVED"
                        or target["results"] or target["next_index"] != 0
                        or _fingerprint(source) != nodes[0]["plan_sha256"]
                        or _security_fingerprint(source) != nodes[0]["security_sha256"]
                        or _fingerprint(target) != nodes[1]["plan_sha256"]
                        or _security_fingerprint(target) != nodes[1]["security_sha256"]):
                    raise TypedHandoffError("handoff_source_or_target_changed", 409)
                ix = request["source_step_index"]
                if ix >= len(source["results"]) or ix >= len(source["steps"]):
                    raise TypedHandoffError("handoff_source_step_not_found", 404)
                if (ix != len(source["steps"])-1
                        or nodes[0]["last_receipt_sha256"] != _digest({
                            "plan_id":source["plan_id"],"index":ix,
                            "result":source["results"][ix]})):
                    raise TypedHandoffError("handoff_source_evidence_mismatch", 409)
                typed = _typed(source["results"][ix],source["steps"][ix])
                existing = self.vault.list(meta["project_id"],meta["target_agent_id"])
                if any(x["note_id"] == request["target_note_id"] for x in existing):
                    raise TypedHandoffError("handoff_target_note_exists", 409)
                # Keep the typed value isolated to the owner's preview and
                # the newly created target note; the ledger stores no plaintext.
                body = f"Owner-reviewed typed result: {typed['kind']} = {typed['value']}"
                provenance = {
                    "job_id": job["job_id"],
                    "source_plan_id": source["plan_id"],
                    "target_plan_id": target["plan_id"],
                    "project_id": meta["project_id"],
                    "source_agent_id": meta["source_agent_id"],
                    "target_agent_id": meta["target_agent_id"],
                    "source_step_index":ix,
                    "source_step_receipt_sha256":nodes[0]["last_receipt_sha256"],
                    "source_plan_security_sha256":nodes[0]["security_sha256"],
                    "target_plan_security_sha256":nodes[1]["security_sha256"],
                    "target_note_id":request["target_note_id"],
                    "retention_days":request["retention_days"],
                    "typed":typed,
                }
                return typed,body,provenance,_digest(provenance)
            except DelegationError:
                raise TypedHandoffError("handoff_delegation_permission_changed", 409) from None
            except (DurableQueueError, ScopedMemoryError):
                raise TypedHandoffError("handoff_source_or_scope_unavailable", 409) from None

    def preview(self, request):
        value = _validate_preview(request)
        with self.lock:
            typed, body, provenance, digest = self._snapshot(value)
            now = self._now()
            token = secrets.token_hex(16)
            try:
                with self.db:
                    # Expired unclaimed previews may be reclaimed; uncertainty
                    # and committed receipts are never silently garbage-collected.
                    self.db.execute("""DELETE FROM agent_typed_handoffs
                        WHERE status='PREVIEW' AND created_utc < ?""",
                        ((now-timedelta(minutes=PREVIEW_MINUTES)).isoformat(),))
                    count = self.db.execute(
                        "SELECT count(*) FROM agent_typed_handoffs").fetchone()[0]
                    if count >= MAX_PREVIEWS:
                        raise TypedHandoffError("handoff_ledger_capacity", 409)
                    self.db.execute("""INSERT INTO agent_typed_handoffs (
                        preview_id,request_json,payload_sha256,status,created_utc)
                        VALUES(?,?,?,'PREVIEW',?)""",(
                        token,json.dumps(value,sort_keys=True),digest,now.isoformat()))
            except sqlite3.Error:
                raise TypedHandoffError("handoff_storage_unavailable",503) from None
            return {
                "preview_id":token, "payload_sha256":digest,
                "typed_result":typed, "target_note_id":value["target_note_id"],
                "target_agent_id":provenance["target_agent_id"],
                "project_id":provenance["project_id"],
                "expires_utc":(now+timedelta(minutes=PREVIEW_MINUTES)).isoformat(),
                "source_receipt_sha256":provenance["source_step_receipt_sha256"],
                "will_create_new_note": True,
                "requires_new_target_plan_approval":True,
                "external_write_count":0,
            }

    def receipt(self, preview_id):
        if type(preview_id) is not str or HEX32.fullmatch(preview_id) is None:
            raise TypedHandoffError("invalid_handoff_preview_id",422)
        with self.lock:
            try:
                row = self.db.execute("""SELECT request_json,payload_sha256,
                    status,created_utc,target_note_revision,committed_utc
                    FROM agent_typed_handoffs WHERE preview_id=?""",
                    (preview_id,)).fetchone()
            except sqlite3.Error:
                raise TypedHandoffError("handoff_storage_unavailable",503) from None
            if row is None:
                raise TypedHandoffError("handoff_preview_not_found",404)
            req = json.loads(row[0])
            return {"preview_id":preview_id, "payload_sha256":row[1],
                    "status":row[2], "created_utc":row[3],
                    "target_note_id":req["target_note_id"],
                    "target_note_revision":row[4],
                    "committed_utc":row[5],
                    "no_automatic_retry":True,
                    "external_write_count":0}

    def commit(self, request):
        if (type(request) is not dict
                or set(request) != {"preview_id","payload_sha256"}
                or type(request["preview_id"]) is not str
                or HEX32.fullmatch(request["preview_id"]) is None
                or type(request["payload_sha256"]) is not str
                or HEX64.fullmatch(request["payload_sha256"]) is None):
            raise TypedHandoffError("invalid_handoff_commit_fields",422)
        with self.lock:
            current = self.receipt(request["preview_id"])
            if current["status"] != "PREVIEW":
                raise TypedHandoffError("handoff_already_claimed_no_retry",409)
            if not compare_digest(current["payload_sha256"],
                                  request["payload_sha256"]):
                raise TypedHandoffError("handoff_preview_digest_mismatch",409)
            try:
                row = self.db.execute("""SELECT request_json,created_utc
                    FROM agent_typed_handoffs WHERE preview_id=?""",
                    (request["preview_id"],)).fetchone()
                if (self._now()-datetime.fromisoformat(row[1])
                        >= timedelta(minutes=PREVIEW_MINUTES)):
                    raise TypedHandoffError("handoff_preview_expired",409)
                preview_request = json.loads(row[0])
                typed, body, provenance, digest = self._snapshot(preview_request)
                if not compare_digest(digest,current["payload_sha256"]):
                    raise TypedHandoffError("handoff_source_changed",409)
                # Two SQLite stores cannot be made durable-atomic here.
                # CLAIMED before creating note. Crash means owner inspection.
                with self.db:
                    changed = self.db.execute("""UPDATE agent_typed_handoffs
                        SET status='CLAIMED_REVIEW_REQUIRED'
                        WHERE preview_id=? AND status='PREVIEW'""",
                        (request["preview_id"],)).rowcount
                    if changed != 1:
                        raise TypedHandoffError("handoff_already_claimed_no_retry",409)
            except sqlite3.Error:
                raise TypedHandoffError("handoff_storage_unavailable",503) from None
            try:
                note = self.vault.put({
                    "project_id":provenance["project_id"],
                    "agent_id":provenance["target_agent_id"],
                    "note_id":provenance["target_note_id"],
                    "text":body,
                    "source_kind":"OWNER_IMPORTED_UNVERIFIED",
                    "source_label":"Typed handoff "+provenance["job_id"],
                    "retention_days":provenance["retention_days"],
                    "expected_revision":0,
                })
                with self.db:
                    self.db.execute("""UPDATE agent_typed_handoffs
                        SET status='COMMITTED',target_note_revision=?,
                            committed_utc=?
                        WHERE preview_id=? AND status='CLAIMED_REVIEW_REQUIRED'""",
                        (note["revision"],self._now().isoformat(),request["preview_id"]))
                return {
                    **self.receipt(request["preview_id"]),
                    "project_id":provenance["project_id"],
                    "target_agent_id":provenance["target_agent_id"],
                    "target_note_digest":note["digest"],
                    "source_receipt_sha256":provenance["source_step_receipt_sha256"],
                    "requires_new_target_plan_approval":True,
                }
            except (sqlite3.Error, ScopedMemoryError, OSError):
                # The note may or may not have reached disk. Never retry.
                raise TypedHandoffError("handoff_commit_uncertain_inspect_both_ledgers",503) from None

    def close(self):
        with self.lock:
            self.db.close()
