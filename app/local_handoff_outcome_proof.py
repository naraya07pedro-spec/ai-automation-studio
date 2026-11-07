"""Owner-imported HMAC receipts for Stage 114 manual agent -> connector handoffs.

A signed final-step receipt is an authenticated shared-secret assertion, NOT an
independent proof of n8n execution or of business effects. This is a read/write
to the owner's local evidence ledger ONLY, never a webhook retry, policy arm,
model call, remote API query or provider-side success claim.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import re
import sqlite3

from local_agent_task_plans import ID_PATTERN as PLAN_ID
from local_connector_manager import ManagedConnectorError, _validate_id
from local_outbound_webhook import OutboundError
from local_workflow_core import ID_PATTERN as WORKFLOW_ID, EVENT_PATTERN

PROOF_DOMAIN = "AAS-MANUAL-HANDOFF-N8N-FINAL-RECEIPT-V1"
DELIVERY_ID = re.compile(r"^[0-9a-f]{32}$", re.ASCII)
EXECUTION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$", re.ASCII)
SIGNATURE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
OUTCOMES = frozenset(("COMPLETED", "FAILED"))
MAX_HISTORY = 50


class HandoffOutcomeProofError(RuntimeError):
    def __init__(self, reason, status=409):
        super().__init__(reason)
        self.status = status


def signing_message(connector_id, workflow_id, event_id, delivery_id,
                    outcome, execution_id):
    """Exact ASCII newline-separated n8n final-node HMAC message."""
    return "\n".join((PROOF_DOMAIN, connector_id, workflow_id,
                      event_id, delivery_id, outcome, execution_id))


def _validate(data):
    expected = {"plan_id", "connector_id", "workflow_id", "event_id",
                "delivery_id", "outcome", "execution_id", "proof_hmac_sha256"}
    if type(data) is not dict or set(data) != expected:
        raise HandoffOutcomeProofError("invalid_handoff_outcome_fields", 422)
    if any(type(value) is not str for value in data.values()):
        raise HandoffOutcomeProofError("invalid_handoff_outcome_fields", 422)
    pid, cid, wid, eid, did, outcome, execution, signature = (
        data[k] for k in ("plan_id", "connector_id", "workflow_id",
                          "event_id", "delivery_id", "outcome",
                          "execution_id", "proof_hmac_sha256"))
    if (not PLAN_ID.fullmatch(pid) or not WORKFLOW_ID.fullmatch(wid)
            or not EVENT_PATTERN.fullmatch(eid) or
            not DELIVERY_ID.fullmatch(did) or outcome not in OUTCOMES
            or not EXECUTION_ID.fullmatch(execution)
            or not SIGNATURE.fullmatch(signature)):
        raise HandoffOutcomeProofError("invalid_handoff_outcome_fields", 422)
    try:
        _validate_id(cid)
    except ManagedConnectorError:
        raise HandoffOutcomeProofError("invalid_connector_id", 422) from None
    return pid, cid, wid, eid, did, outcome, execution, signature


class SignedManualHandoffOutcome:
    """Reuse Stage 114 handoff DB; digest-only evidence bound to send ledger."""

    def __init__(self, handoff, manager):
        if (handoff is None or manager is None or handoff.manager is not manager
                or getattr(manager, "_vault", None) is None):
            raise HandoffOutcomeProofError(
                "encrypted_agent_handoff_and_connector_required", 403)
        self.handoff = handoff
        self.manager = manager
        try:
            with handoff.lock, handoff.db:
                handoff.db.execute("""CREATE TABLE IF NOT EXISTS
                    signed_manual_handoff_outcomes(
                      plan_id TEXT PRIMARY KEY,
                      connector_id TEXT NOT NULL,
                      delivery_id TEXT NOT NULL,
                      workflow_id TEXT NOT NULL,
                      event_id TEXT NOT NULL,
                      claimed_outcome TEXT NOT NULL
                          CHECK(claimed_outcome IN ('COMPLETED','FAILED')),
                      execution_id TEXT NOT NULL,
                      signed_message_sha256 TEXT NOT NULL,
                      recorded_utc TEXT NOT NULL,
                      UNIQUE(connector_id,delivery_id),
                      FOREIGN KEY(plan_id) REFERENCES agent_connector_handoffs(plan_id)
                    )""")
        except sqlite3.Error:
            raise HandoffOutcomeProofError("handoff_outcome_storage_unavailable", 503) from None

    @staticmethod
    def _public(row, dispatch):
        return {
            "plan_id":row[0], "connector_id":row[1],
            "delivery_id":row[2], "workflow_id":row[3], "event_id":row[4],
            "provider_asserted_outcome":row[5], "n8n_execution_id":row[6],
            "message_sha256":row[7], "recorded_utc":row[8],
            "local_dispatch_status_unchanged":dispatch,
            "signature_valid":True,
            "evidence":"HMAC_MATCHED_SHARED_SECRET_OWNER_IMPORTED",
            "independently_verified_remote":False,
            "business_effect_verified":False,
            "no_automatic_retry":True,
            "external_write_count":0,
        }

    def import_receipt(self, request):
        pid, cid, wid, eid, did, outcome, execution, signature = _validate(request)
        handoff, manager = self.handoff, self.manager
        # Same order as Stage 114 commit: handoff -> manager -> sender client.
        # Do not grab manager lock and then try to fetch handoff state.
        with handoff.lock, manager._lock:
            try:
                row = handoff._row(pid)
                if (row is None or row[1:4] != (cid, wid, eid)
                        or row[8] not in ("REMOTE_ACCEPTED", "OUTCOME_UNCERTAIN",
                                          "REVIEW_REQUIRED", "IN_FLIGHT")
                        or (row[9] is not None and row[9] != did)):
                    raise HandoffOutcomeProofError("handoff_dispatch_not_claimed", 409)
                connector = manager._entry(cid, require_active=True)
                secret = connector["secret"]
                if (type(secret) is not str or
                        not 32 <= len(secret.encode("utf-8")) <= 1024):
                    raise HandoffOutcomeProofError(
                        "strong_connector_hmac_secret_required", 403)
                msg = signing_message(cid, wid, eid, did, outcome, execution)
                expected = hmac.new(secret.encode("utf-8"),
                                    msg.encode("ascii"), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expected, signature):
                    raise HandoffOutcomeProofError(
                        "handoff_outcome_signature_invalid", 403)
                client = manager._client(connector)
                with client._lock:
                    send = client.db.execute("""SELECT status,delivery_id,
                        payload_sha256,target_sha256 FROM webhook_deliveries
                        WHERE workflow_id=? AND event_id=?""", (wid, eid)).fetchone()
                if send is None or send[1] != did:
                    raise HandoffOutcomeProofError(
                        "handoff_delivery_attempt_not_found", 404)
                dispatch, _, send_payload, target_sha = send
                if dispatch not in ("REMOTE_ACCEPTED", "OUTCOME_UNCERTAIN"):
                    raise HandoffOutcomeProofError(
                        "handoff_delivery_attempt_not_finalized", 409)
                # The exact Stage 114 preview payload and destination must
                # agree with the independent connector send ledger.
                if (not hmac.compare_digest(row[6], send_payload) or
                        not hmac.compare_digest(
                            target_sha,
                            hashlib.sha256(connector["url"].encode("utf-8")).hexdigest())):
                    raise HandoffOutcomeProofError(
                        "handoff_delivery_source_mismatch", 409)
                fingerprint = hashlib.sha256(msg.encode("ascii")).hexdigest()
                with handoff.db:
                    old = handoff.db.execute("""SELECT plan_id,connector_id,
                        delivery_id,workflow_id,event_id,claimed_outcome,
                        execution_id,signed_message_sha256,recorded_utc
                        FROM signed_manual_handoff_outcomes
                        WHERE plan_id=?""", (pid,)).fetchone()
                    identity = (pid,cid,did,wid,eid,outcome,execution,fingerprint)
                    if old is not None:
                        if old[:8] != identity:
                            raise HandoffOutcomeProofError(
                                "conflicting_handoff_outcome_proof", 409)
                        result = "ALREADY_RECORDED"
                        recorded = old
                    else:
                        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
                        handoff.db.execute("""INSERT INTO
                            signed_manual_handoff_outcomes(
                            plan_id,connector_id,delivery_id,workflow_id,event_id,
                            claimed_outcome,execution_id,signed_message_sha256,
                            recorded_utc) VALUES(?,?,?,?,?,?,?,?,?)""",
                            identity + (timestamp,))
                        recorded = identity + (timestamp,)
                        result = "RECORDED"
                return {"result":result, **self._public(recorded, dispatch)}
            except ManagedConnectorError as exc:
                raise HandoffOutcomeProofError(str(exc), exc.status) from None
            except sqlite3.IntegrityError:
                raise HandoffOutcomeProofError(
                    "delivery_already_attested_to_another_plan", 409) from None
            except sqlite3.Error:
                raise HandoffOutcomeProofError(
                    "handoff_outcome_storage_unavailable", 503) from None
            except OutboundError as exc:
                raise HandoffOutcomeProofError(str(exc), exc.status) from None

    def list(self):
        # Pure local GET, including records saved before connector revocation.
        with self.handoff.lock:
            try:
                rows = self.handoff.db.execute("""SELECT p.plan_id,p.connector_id,
                    p.delivery_id,p.workflow_id,p.event_id,p.claimed_outcome,
                    p.execution_id,p.signed_message_sha256,p.recorded_utc,
                    h.status
                    FROM signed_manual_handoff_outcomes AS p
                    JOIN agent_connector_handoffs AS h ON p.plan_id=h.plan_id
                    ORDER BY p.recorded_utc DESC,p.plan_id DESC LIMIT ?""",
                    (MAX_HISTORY,)).fetchall()
            except sqlite3.Error:
                raise HandoffOutcomeProofError(
                    "handoff_outcome_storage_unavailable", 503) from None
        return {"records":[self._public(row[:9],row[9]) for row in rows],
                "evidence_scope":"OWNER_IMPORTED_SHARED_SECRET_SIGNED_FINAL_STEP",
                "independently_verified_remote":False,
                "business_effect_verified":False,
                "provider_semantics_not_independently_verified":True,
                "external_write_count":0}
