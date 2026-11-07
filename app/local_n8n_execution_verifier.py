"""Stage 117: owner-approved, read-only n8n public API execution verification.

A signed final-step assertion (Stage 116) and two local send ledgers MUST
already exist. Only GET /api/v1/executions/{signed_id}?includeData=true is sent.
The owner pre-pins an n8n API origin, workflow ID and *incoming webhook node*
in process configuration. Inspecting the exact provider-returned webhook input
binds the execution to our original delivery without scanning arbitrary nodes.

No webhook write/retry, API key in logs, raw n8n execution data persistence,
model permission, background execution or business-effect success claim.
"""
from datetime import datetime, timezone
import hashlib
import http.client
import json
import re
import sqlite3
from urllib.parse import urlsplit

from local_agent_task_plans import ID_PATTERN as PLAN_ID
from local_outbound_webhook import (
    OutboundError, validate_https_target, _PinnedTLS, _resolve_public_ipv4)

EXECUTION = re.compile(r"^[1-9][0-9]{0,15}$", re.ASCII)
WORKFLOW = re.compile(r"^[A-Za-z0-9_-]{1,128}$", re.ASCII)
NODE = re.compile(r"^[^\x00-\x1f\x7f]{1,100}$", re.ASCII)
MAX_BODY_BYTES = 1024 * 1024
MAX_CHECKS = 100
MAX_DAILY_CHECKS = 8


class ProviderExecutionError(RuntimeError):
    def __init__(self, reason, status=409):
        super().__init__(reason)
        self.status = status


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical_hash(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")).hexdigest()


def validate_n8n_config(origin, workflow_id, webhook_node_name, api_key):
    if (type(origin) is not str or type(workflow_id) is not str
            or type(webhook_node_name) is not str or type(api_key) is not str
            or not WORKFLOW.fullmatch(workflow_id)
            or not NODE.fullmatch(webhook_node_name)
            or not 20 <= len(api_key) <= 8192 or
            any(ord(c) < 33 or ord(c) > 126 for c in api_key)):
        raise ProviderExecutionError("n8n_api_configuration_invalid", 422)
    try:
        parts = urlsplit(origin)
        if (parts.scheme != "https" or parts.path not in ("", "/")
                or parts.query or parts.fragment or parts.username
                or parts.password or parts.port not in (None, 443)):
            raise ValueError("origin_not_https_root")
        host, _ = validate_https_target(
            origin.rstrip("/") + "/api/v1/executions/1?includeData=true")
    except (OutboundError, ValueError, TypeError):
        raise ProviderExecutionError("n8n_api_origin_not_allowed", 422) from None
    # n8n API key intentionally remains process-only, not in SQLite or UI.
    return host


def fetch_n8n_execution(host, execution_id, api_key):
    """One DNS-pinned TLS GET. Never follow redirects or echo provider bodies."""
    path = "/api/v1/executions/" + execution_id + "?includeData=true"
    try:
        ip = _resolve_public_ipv4(host)
        conn = _PinnedTLS(host, ip)
        try:
            conn.request("GET", path, headers={
                "X-N8N-API-KEY": api_key,
                "Accept": "application/json",
                "User-Agent": "AI-Automation-Studio/117",
            })
            response = conn.getresponse()
            if response.status != 200:
                # Never consume untrusted body; status is enough for failure.
                if response.status in (401, 403):
                    return {"_api_error":"AUTH_REQUIRED"}
                if response.status == 404:
                    return {"_api_error":"NOT_FOUND"}
                return {"_api_error":"REMOTE_HTTP_UNEXPECTED"}
            raw = response.read(MAX_BODY_BYTES + 1)
            if len(raw) > MAX_BODY_BYTES:
                return {"_api_error":"RESPONSE_TOO_LARGE"}
            parsed = json.loads(raw.decode("utf-8", errors="strict"))
            if type(parsed) is not dict:
                return {"_api_error":"RESPONSE_INVALID"}
            return parsed
        finally:
            conn.close()
    except (OSError, TimeoutError, ValueError, UnicodeError, json.JSONDecodeError,
            http.client.HTTPException, OutboundError):
        return {"_api_error":"NETWORK_OR_RESPONSE_UNCERTAIN"}


def _inbound_binding(record, node_name, workflow_id, event_id, delivery_id):
    """Exact pinned webhook-node output only; no fuzzy recursive search."""
    data = record.get("data")
    if type(data) is not dict:
        return False
    result = data.get("resultData")
    if type(result) is not dict or type(result.get("runData")) is not dict:
        return False
    runs = result["runData"].get(node_name)
    if type(runs) is not list or len(runs) != 1:
        return False
    run = runs[0]
    if type(run) is not dict or type(run.get("data")) is not dict:
        return False
    outputs = run["data"].get("main")
    if (type(outputs) is not list or len(outputs) != 1
            or type(outputs[0]) is not list or len(outputs[0]) != 1):
        return False
    item = outputs[0][0]
    if type(item) is not dict or type(item.get("json")) is not dict:
        return False
    payload = item["json"].get("body", item["json"])
    if type(payload) is not dict:
        return False
    return (payload.get("schema") == "aas.local_workflow_receipt.v1"
            and type(payload.get("delivery_id")) is str
            and payload["delivery_id"] == delivery_id
            and type(payload.get("workflow_id")) is str
            and payload["workflow_id"] == workflow_id
            and type(payload.get("event_id")) is str
            and payload["event_id"] == event_id)


class N8NExecutionVerifier:
    """One-shot, locally durable, owner-explicit independent API lookup."""

    def __init__(self, signed_proofs, *, api_origin, workflow_id,
                 webhook_node_name, api_key, fetcher=None):
        if signed_proofs is None:
            raise ProviderExecutionError("signed_handoff_evidence_required", 403)
        self.signed = signed_proofs
        self.handoff = signed_proofs.handoff
        self.workflow_id = workflow_id
        self.node_name = webhook_node_name
        self.host = validate_n8n_config(
            api_origin, workflow_id, webhook_node_name, api_key)
        self._key = api_key
        self._fetcher = fetcher or fetch_n8n_execution
        self.lock = self.handoff.lock
        with self.handoff.lock, self.handoff.db:
            self.handoff.db.execute("""CREATE TABLE IF NOT EXISTS
                n8n_provider_execution_checks(
                  plan_id TEXT PRIMARY KEY,
                  connector_id TEXT NOT NULL,
                  execution_id TEXT NOT NULL,
                  delivery_id TEXT NOT NULL,
                  provider_workflow_id TEXT NOT NULL,
                  pinned_node_sha256 TEXT NOT NULL,
                  signed_message_sha256 TEXT NOT NULL,
                  status TEXT NOT NULL,
                  provider_execution_status TEXT,
                  provider_finished INTEGER,
                  inbound_delivery_correlated INTEGER NOT NULL DEFAULT 0,
                  provider_metadata_sha256 TEXT,
                  checked_at_utc TEXT NOT NULL
                )""")
            # Interrupted network read is NOT proof of any provider result.
            self.handoff.db.execute("""UPDATE n8n_provider_execution_checks
                SET status='LOOKUP_UNCERTAIN' WHERE status='IN_FLIGHT'""")

    @staticmethod
    def _public(row):
        return {
            "plan_id":row[0], "connector_id":row[1],
            "execution_id":row[2], "delivery_id":row[3],
            "configured_n8n_workflow_id":row[4],
            "configured_webhook_node_sha256":row[5],
            "status":row[7], "provider_execution_status":row[8],
            "provider_finished":bool(row[9]) if row[9] is not None else None,
            "inbound_delivery_correlated":bool(row[10]),
            "provider_metadata_sha256":row[11],
            "checked_at_utc":row[12],
            "provider_api_execution_verified":row[7] in (
                "EXECUTION_SUCCESS_CORRELATED", "EXECUTION_FAILED_CORRELATED",
                "CLAIM_MISMATCH", "DELIVERY_NOT_CORRELATED"),
            "business_effect_verified":False,
            "external_write_count":0,
            "remote_read_count_this_response":0,
            "automatic_retry":False,
        }

    def history(self):
        with self.handoff.lock:
            try:
                rows = self.handoff.db.execute("""SELECT plan_id,connector_id,
                    execution_id,delivery_id,provider_workflow_id,
                    pinned_node_sha256,signed_message_sha256,status,
                    provider_execution_status,provider_finished,
                    inbound_delivery_correlated,provider_metadata_sha256,
                    checked_at_utc FROM n8n_provider_execution_checks
                    ORDER BY checked_at_utc DESC,plan_id LIMIT 30""").fetchall()
            except sqlite3.Error:
                raise ProviderExecutionError("n8n_check_storage_unavailable", 503) from None
        return {"records":[self._public(x) for x in rows],
                "scope":"OFFICIAL_N8N_EXECUTION_READ_ONLY_GET",
                "business_effect_verified":False,
                "external_write_count":0,
                "remote_reads":0}

    def verify_one(self, request):
        if (type(request) is not dict or set(request) != {"plan_id"}
                or type(request["plan_id"]) is not str
                or not PLAN_ID.fullmatch(request["plan_id"])):
            raise ProviderExecutionError("invalid_execution_verify_request", 422)
        pid = request["plan_id"]
        with self.lock:
            with self.handoff.lock:
                try:
                    signed = self.handoff.db.execute("""SELECT s.connector_id,
                        s.delivery_id,s.workflow_id,s.event_id,
                        s.execution_id,s.claimed_outcome,
                        s.signed_message_sha256,h.status
                        FROM signed_manual_handoff_outcomes s
                        JOIN agent_connector_handoffs h ON s.plan_id=h.plan_id
                        WHERE s.plan_id=?""", (pid,)).fetchone()
                    if signed is None:
                        raise ProviderExecutionError(
                            "signed_handoff_receipt_required", 409)
                    cid, did, wid, eid, exid, claim, message_hash, handoff_status = signed
                    if not EXECUTION.fullmatch(exid):
                        raise ProviderExecutionError(
                            "n8n_execution_id_not_numeric", 422)
                    if handoff_status not in (
                            "REMOTE_ACCEPTED", "OUTCOME_UNCERTAIN",
                            "REVIEW_REQUIRED", "IN_FLIGHT"):
                        raise ProviderExecutionError(
                            "handoff_delivery_not_claimed", 409)
                    if self.handoff.db.execute(
                            "SELECT 1 FROM n8n_provider_execution_checks WHERE plan_id=?",
                            (pid,)).fetchone():
                        raise ProviderExecutionError(
                            "n8n_execution_already_checked_read_history", 409)
                    now = _now()
                    if self.handoff.db.execute(
                            "SELECT count(*) FROM n8n_provider_execution_checks"
                            ).fetchone()[0] >= MAX_CHECKS:
                        raise ProviderExecutionError("n8n_check_capacity_reached", 409)
                    if self.handoff.db.execute("""SELECT count(*) FROM
                        n8n_provider_execution_checks WHERE substr(checked_at_utc,1,10)=?
                        """, (now[:10],)).fetchone()[0] >= MAX_DAILY_CHECKS:
                        raise ProviderExecutionError("n8n_check_daily_limit", 429)
                    # Persist a one-shot claim BEFORE any external GET.
                    # Response metadata only; secret and provider data never stored.
                    with self.handoff.db:
                        self.handoff.db.execute("""INSERT INTO n8n_provider_execution_checks
                            (plan_id,connector_id,execution_id,delivery_id,
                             provider_workflow_id,pinned_node_sha256,
                             signed_message_sha256,status,checked_at_utc)
                            VALUES(?,?,?,?,?,?,?,'IN_FLIGHT',?)""",
                            (pid,cid,exid,did,self.workflow_id,
                             hashlib.sha256(self.node_name.encode()).hexdigest(),
                             message_hash,now))
                except sqlite3.IntegrityError:
                    raise ProviderExecutionError(
                        "n8n_execution_already_checked_read_history", 409) from None
                except sqlite3.Error:
                    raise ProviderExecutionError(
                        "n8n_check_storage_unavailable", 503) from None
            # Never include API key, provider payload or exceptions in UI/logs.
            try:
                response = self._fetcher(self.host, exid, self._key)
            except Exception:
                response = {"_api_error":"NETWORK_OR_RESPONSE_UNCERTAIN"}
            status, execution_state, finished, correlated = (
                "LOOKUP_UNCERTAIN", None, None, False)
            digest = None
            if type(response) is dict and "_api_error" in response:
                safe_errors = frozenset((
                    "AUTH_REQUIRED", "NOT_FOUND", "REMOTE_HTTP_UNEXPECTED",
                    "RESPONSE_TOO_LARGE", "RESPONSE_INVALID",
                    "NETWORK_OR_RESPONSE_UNCERTAIN"))
                error = response["_api_error"]
                status = ("LOOKUP_" + error if type(error) is str
                          and error in safe_errors else "LOOKUP_UNCERTAIN")
            elif type(response) is dict:
                raw_id = response.get("id")
                raw_workflow = response.get("workflowId")
                execution_state = response.get("status")
                finished = response.get("finished")
                if (str(raw_id) != exid or str(raw_workflow) != self.workflow_id
                        or execution_state not in (
                            "success", "error", "running", "waiting", "unknown", "canceled", "crashed")
                        or type(finished) is not bool):
                    status = "PROVIDER_IDENTITY_MISMATCH"
                    execution_state,finished = None,None
                else:
                    correlated = _inbound_binding(response,self.node_name,
                                                  wid,eid,did)
                    # An execution 'success' must be explicitly finished; for
                    # 'error', stoppedAt is required because finished may be false.
                    if not correlated:
                        status = "DELIVERY_NOT_CORRELATED"
                    elif execution_state == "success" and finished is True:
                        status = ("EXECUTION_SUCCESS_CORRELATED"
                                  if claim == "COMPLETED" else "CLAIM_MISMATCH")
                    elif execution_state == "error" and type(
                            response.get("stoppedAt")) is str and response["stoppedAt"]:
                        status = ("EXECUTION_FAILED_CORRELATED"
                                  if claim == "FAILED" else "CLAIM_MISMATCH")
                    else:
                        status = "PROVIDER_EXECUTION_NOT_FINAL"
                    digest = _canonical_hash({
                        "id":exid,"workflowId":self.workflow_id,
                        "status":execution_state,"finished":finished,
                        "stoppedAt":response.get("stoppedAt") if
                            type(response.get("stoppedAt")) is str else None,
                        "inbound_correlated":correlated,
                        "signed_message_sha256":message_hash})
            try:
                with self.handoff.lock, self.handoff.db:
                    changed=self.handoff.db.execute("""UPDATE n8n_provider_execution_checks
                        SET status=?,provider_execution_status=?,provider_finished=?,
                            inbound_delivery_correlated=?,provider_metadata_sha256=?,
                            checked_at_utc=?
                        WHERE plan_id=? AND status='IN_FLIGHT'""",
                        (status,execution_state if type(execution_state) is str else None,
                         int(finished) if type(finished) is bool else None,
                         int(correlated),digest,_now(),pid)).rowcount
                    if changed != 1:
                        raise ProviderExecutionError(
                            "n8n_execution_record_uncertain", 503)
                    row=self.handoff.db.execute("""SELECT plan_id,connector_id,
                        execution_id,delivery_id,provider_workflow_id,
                        pinned_node_sha256,signed_message_sha256,status,
                        provider_execution_status,provider_finished,
                        inbound_delivery_correlated,provider_metadata_sha256,
                        checked_at_utc FROM n8n_provider_execution_checks
                        WHERE plan_id=?""", (pid,)).fetchone()
            except sqlite3.Error:
                raise ProviderExecutionError(
                    "n8n_execution_record_uncertain", 503) from None
            return {**self._public(row),"remote_reads":1,
                    "provider_api_response_inspected":status not in (
                        "LOOKUP_UNCERTAIN",),
                    "provider_execution_verified_with_delivery":status in (
                        "EXECUTION_SUCCESS_CORRELATED",
                        "EXECUTION_FAILED_CORRELATED", "CLAIM_MISMATCH"),
                    "independent_business_effect_verified":False}
