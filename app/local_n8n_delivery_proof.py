"""Owner-imported, HMAC-verified n8n final-step receipts for managed autopilot.

This NEVER calls an external API, does not authorize new sends and does not
change the dispatch status or resume uncertain policies. One claim must match
an existing durable local POST attempt (connector, schedule, delivery ID).
Authenticity proves only possession of the connector's HMAC secret, not
independently confirmed n8n or CRM business completion.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import re
import sqlite3

from local_connector_manager import ManagedConnectorError, _validate_id
from local_recurring_scheduler import SCHEDULE_ID
from local_scheduled_webhook import AutopilotError

PROOF_DOMAIN = "AAS-N8N-FINAL-RECEIPT-V1"
DELIVERY_ID = re.compile(r"^[0-9a-f]{32}$", re.ASCII)
EXECUTION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$", re.ASCII)
SIGNATURE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
OUTCOMES = frozenset({"COMPLETED", "FAILED"})
MAX_PROOFS_PER_READ = 100


class N8NProofError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def signing_message(connector_id, schedule_id, delivery_id, outcome, execution_id):
    """Exactly the ASCII string passed into n8n's HMAC SHA256 step."""
    return "\n".join((PROOF_DOMAIN, connector_id, schedule_id,
                      delivery_id, outcome, execution_id))


def _validate(request):
    expected = {"connector_id", "schedule_id", "delivery_id",
                "outcome", "execution_id", "proof_hmac_sha256"}
    if type(request) is not dict or set(request) != expected:
        raise N8NProofError("invalid_proof_fields")
    cid, sid, did, outcome, execution, proof = (request[x] for x in (
        "connector_id", "schedule_id", "delivery_id",
        "outcome", "execution_id", "proof_hmac_sha256"))
    if (type(cid) is not str or type(sid) is not str
            or type(did) is not str or type(outcome) is not str
            or type(execution) is not str or type(proof) is not str):
        raise N8NProofError("invalid_proof_fields")
    try:
        _validate_id(cid)
    except ManagedConnectorError:
        raise N8NProofError("invalid_connector_id") from None
    if (not SCHEDULE_ID.fullmatch(sid)
            or not DELIVERY_ID.fullmatch(did)
            or outcome not in OUTCOMES
            or not EXECUTION_ID.fullmatch(execution)
            or not SIGNATURE.fullmatch(proof)):
        raise N8NProofError("invalid_proof_fields")
    return cid, sid, did, outcome, execution, proof


class SignedN8NDeliveryProof:
    def __init__(self, managed_autopilot):
        if managed_autopilot is None or managed_autopilot.manager._vault is None:
            raise N8NProofError("encrypted_managed_autopilot_required", 403)
        self.autopilot = managed_autopilot

    @staticmethod
    def _schema(db):
        db.execute("""CREATE TABLE IF NOT EXISTS n8n_signed_delivery_proofs (
            delivery_id TEXT PRIMARY KEY,
            schedule_id TEXT NOT NULL,
            slot_utc TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK(outcome IN ('COMPLETED','FAILED')),
            execution_id TEXT NOT NULL,
            message_sha256 TEXT NOT NULL,
            verified_at_utc TEXT NOT NULL
        )""")

    @staticmethod
    def _public(cid, row):
        did, sid, slot, outcome, execution_id, verified_at = row
        return {
            "connector_id": cid,
            "schedule_id": sid,
            "delivery_id": did,
            "scheduled_for_utc": slot,
            "provider_asserted_outcome": outcome,
            "n8n_execution_id": execution_id,
            "verified_at_utc": verified_at,
            "signature_valid": True,
            "evidence": "HMAC_MATCHED_SHARED_SECRET_OWNER_IMPORTED",
            "independently_verified_remote": False,
            "external_write_count": 0,
        }

    def reconcile(self, request):
        cid, sid, did, outcome, execution_id, signature = _validate(request)
        manager = self.autopilot.manager
        # Lock order consistent with revoke/send: manager first, then ledger.
        # Revoked connector secrets are erased; do not silently bypass this.
        with manager._lock:
            try:
                row = manager._entry(cid, require_active=True)
            except ManagedConnectorError as exc:
                raise N8NProofError(str(exc), exc.status) from None
            secret = row["secret"]
            if type(secret) is not str or len(secret.encode("utf-8")) < 32:
                raise N8NProofError("strong_connector_hmac_secret_required", 403)
            message = signing_message(cid, sid, did, outcome, execution_id)
            expected = hmac.new(secret.encode("utf-8"), message.encode("ascii"),
                                hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise N8NProofError("n8n_proof_signature_invalid", 403)
            # Validate the actual authorized sender ledger BEFORE recording any proof.
            try:
                policy = self.autopilot._get(cid, require_active=True, create=False)
            except (ManagedConnectorError, AutopilotError) as exc:
                raise N8NProofError("local_delivery_ledger_not_found", 404) from None
            with policy.lock:
                attempt = policy.db.execute("""SELECT slot_utc,status
                    FROM scheduled_webhook_attempts
                    WHERE schedule_id=? AND delivery_id=?""", (sid, did)).fetchone()
                if attempt is None:
                    raise N8NProofError("local_delivery_attempt_not_found", 404)
                slot, dispatch_status = attempt
                if dispatch_status not in ("REMOTE_ACCEPTED", "OUTCOME_UNCERTAIN"):
                    raise N8NProofError("delivery_attempt_not_finalized", 409)
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                fingerprint = hashlib.sha256(message.encode("ascii")).hexdigest()
                with policy.db:
                    self._schema(policy.db)
                    prior = policy.db.execute("""SELECT schedule_id,slot_utc,
                        outcome,execution_id,message_sha256
                        FROM n8n_signed_delivery_proofs
                        WHERE delivery_id=?""", (did,)).fetchone()
                    current = (sid,slot,outcome,execution_id,fingerprint)
                    if prior is not None:
                        if prior != current:
                            raise N8NProofError("conflicting_delivery_proof", 409)
                        code = "ALREADY_VERIFIED"
                    else:
                        policy.db.execute("""INSERT INTO n8n_signed_delivery_proofs
                            (delivery_id,schedule_id,slot_utc,outcome,
                             execution_id,message_sha256,verified_at_utc)
                            VALUES(?,?,?,?,?,?,?)""",
                            (did,sid,slot,outcome,execution_id,fingerprint,now))
                        code = "RECORDED"
                    saved = policy.db.execute("""SELECT delivery_id,schedule_id,
                        slot_utc,outcome,execution_id,verified_at_utc
                        FROM n8n_signed_delivery_proofs WHERE delivery_id=?""",
                        (did,)).fetchone()
            return {"result":code,"local_dispatch_status_unchanged":dispatch_status,
                    **self._public(cid,saved)}

    def list(self):
        records = []
        # Known owner-registered IDs only; do not read arbitrary client paths.
        for entry in self.autopilot.manager.list()["connectors"]:
            cid = entry["connector_id"]
            if not self.autopilot._path(cid).is_file():
                continue
            try:
                policy = self.autopilot._get(cid, require_active=False, create=False)
                with policy.lock:
                    exists = policy.db.execute("""SELECT 1 FROM sqlite_master
                        WHERE type='table' AND name='n8n_signed_delivery_proofs'""").fetchone()
                    if not exists:
                        continue
                    rows = policy.db.execute("""SELECT delivery_id,schedule_id,
                        slot_utc,outcome,execution_id,verified_at_utc
                        FROM n8n_signed_delivery_proofs
                        ORDER BY verified_at_utc DESC,delivery_id DESC LIMIT ?""",
                        (MAX_PROOFS_PER_READ,)).fetchall()
                    records.extend(self._public(cid,row) for row in rows)
            except (AutopilotError, ManagedConnectorError, sqlite3.Error, OSError):
                # Fail closed: a damaged ledger must not imply verified success.
                raise N8NProofError("n8n_proof_storage_unavailable",503) from None
        records.sort(key=lambda r:(r["verified_at_utc"],r["delivery_id"]),reverse=True)
        return {"enabled":True,"records":records[:MAX_PROOFS_PER_READ],
                "external_write_count":0,
                "evidence_scope":"OWNER_IMPORTED_SIGNED_N8N_FINAL_STEP",
                "provider_semantics_not_independently_verified":True}
