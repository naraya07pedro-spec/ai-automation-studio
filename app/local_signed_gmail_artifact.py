"""Stage 121: domain-separated, signed n8n-to-Gmail artifact binding.

The connector's HMAC authenticates a claim by the holder of its secret, not
proof of Gmail send causation. Stage 117 n8n verification and Stage 120 Gmail
metadata are independently checked. Never elevate signed claims to a
provider-delivery or independent business-effect assertion.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import re
import sqlite3

from local_agent_task_plans import ID_PATTERN as PLAN_ID
from local_connector_manager import ManagedConnectorError
from local_gmail_sent_evidence import MESSAGE_ID, EMAIL

DOMAIN = "AAS-GMAIL-ARTIFACT-BINDING-V1"
HEX = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
MAX_HISTORY = 30


class SignedGmailArtifactError(RuntimeError):
    def __init__(self, reason, status=409):
        super().__init__(reason)
        self.status = status


def signing_message(plan_id, connector_id, workflow_id, event_id,
                    delivery_id, execution_id, gmail_id_sha256,
                    recipient_sha256, subject_sha256):
    """All ten lines exact ASCII; signer must not reorder or add separators."""
    return "\n".join((
        DOMAIN, plan_id, connector_id, workflow_id, event_id, delivery_id,
        execution_id, gmail_id_sha256, recipient_sha256, subject_sha256))


def _validate(data):
    expected = {"plan_id", "connector_id", "workflow_id", "event_id",
                "delivery_id", "execution_id", "gmail_id_sha256",
                "recipient_sha256", "subject_sha256", "proof_hmac_sha256"}
    if (type(data) is not dict or set(data) != expected
            or any(type(value) is not str for value in data.values())):
        raise SignedGmailArtifactError("invalid_gmail_binding_fields", 422)
    if (not PLAN_ID.fullmatch(data["plan_id"])
            or any(HEX.fullmatch(data[key]) is None for key in
                   ("gmail_id_sha256", "recipient_sha256", "subject_sha256",
                    "proof_hmac_sha256"))
            or any(not data[k] or len(data[k]) > 128 or
                   not all(ch.isascii() and (ch.isalnum() or ch in "_-")
                           for ch in data[k])
                   for k in ("connector_id","workflow_id","event_id","execution_id"))
            or not re.fullmatch(r"[0-9a-f]{32}", data["delivery_id"], re.ASCII)):
        raise SignedGmailArtifactError("invalid_gmail_binding_fields", 422)
    return data


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class SignedGmailArtifactBinding:
    def __init__(self, n8n_verifier, manager):
        if (n8n_verifier is None or manager is None
                or n8n_verifier.signed.manager is not manager
                or getattr(manager, "_vault", None) is None):
            raise SignedGmailArtifactError("encrypted_n8n_binding_required", 403)
        self.n8n = n8n_verifier
        self.manager = manager
        self.handoff = n8n_verifier.handoff
        self.lock = self.handoff.lock
        self.db = self.handoff.db
        with self.lock, self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS signed_gmail_artifact_bindings(
                plan_id TEXT PRIMARY KEY,
                connector_id TEXT NOT NULL,
                delivery_id TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                signed_message_sha256 TEXT NOT NULL,
                gmail_id_sha256 TEXT NOT NULL,
                recipient_sha256 TEXT NOT NULL,
                subject_sha256 TEXT NOT NULL,
                artifact_message_sha256 TEXT NOT NULL,
                recorded_utc TEXT NOT NULL,
                FOREIGN KEY(plan_id) REFERENCES agent_connector_handoffs(plan_id)
            )""")

    def _correlation(self, pid):
        """All joins and identities must be exact and provider success saved."""
        row = self.db.execute("""SELECT p.plan_id,p.connector_id,p.workflow_id,
            p.event_id,p.delivery_id,p.execution_id,p.signed_message_sha256,
            p.claimed_outcome,n.connector_id,n.delivery_id,n.execution_id,
            n.signed_message_sha256,n.status,n.provider_finished,
            n.inbound_delivery_correlated
            FROM signed_manual_handoff_outcomes p
            JOIN n8n_provider_execution_checks n ON n.plan_id=p.plan_id
            WHERE p.plan_id=?""", (pid,)).fetchone()
        if (row is None or row[7] != "COMPLETED" or row[1] != row[8]
                or row[4] != row[9] or row[5] != row[10]
                or row[6] != row[11]
                or row[12] != "EXECUTION_SUCCESS_CORRELATED"
                or row[13] != 1 or row[14] != 1):
            raise SignedGmailArtifactError("verified_n8n_chain_required")
        return row

    @staticmethod
    def _public(row):
        return {
            "plan_id": row[0], "connector_id": row[1],
            "delivery_sha256": _digest(row[2]),
            "execution_sha256": _digest(row[3]),
            "signed_message_sha256": row[4],
            "gmail_id_sha256": row[5],
            "recipient_sha256": row[6],
            "subject_sha256": row[7],
            "artifact_message_sha256": row[8],
            "recorded_utc": row[9],
            "signature_valid_at_import": True,
            "provider_n8n_execution_correlated_at_import": True,
            "gmail_artifact_independently_verified": False,
            "n8n_causation_verified": False,
            "recipient_delivery_verified": False,
            "business_effect_verified": False,
            "external_write_count": 0,
        }

    def import_claim(self, data):
        data = _validate(data)
        with self.lock, self.manager._lock:
            try:
                row = self._correlation(data["plan_id"])
                for name, value in (
                    ("connector_id", row[1]), ("workflow_id", row[2]),
                    ("event_id", row[3]), ("delivery_id", row[4]),
                    ("execution_id", row[5])):
                    if data[name] != value:
                        raise SignedGmailArtifactError("gmail_binding_chain_mismatch")
                connector = self.manager._entry(row[1], require_active=True)
                secret = connector["secret"]
                if (type(secret) is not str
                        or not 32 <= len(secret.encode("utf-8")) <= 1024):
                    raise SignedGmailArtifactError("strong_connector_hmac_secret_required", 403)
                message = signing_message(*(
                    data[k] for k in (
                        "plan_id","connector_id","workflow_id","event_id",
                        "delivery_id","execution_id","gmail_id_sha256",
                        "recipient_sha256","subject_sha256")))
                expected = hmac.new(secret.encode("utf-8"),
                                    message.encode("ascii"),hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expected,data["proof_hmac_sha256"]):
                    raise SignedGmailArtifactError("gmail_binding_signature_invalid",403)
                fingerprint = _digest(message)
                at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                identity = (
                    data["plan_id"],data["connector_id"],data["delivery_id"],
                    data["execution_id"],row[6],data["gmail_id_sha256"],
                    data["recipient_sha256"],data["subject_sha256"],fingerprint)
                with self.db:
                    old = self.db.execute("""SELECT plan_id,connector_id,
                        delivery_id,execution_id,signed_message_sha256,
                        gmail_id_sha256,recipient_sha256,subject_sha256,
                        artifact_message_sha256,recorded_utc
                        FROM signed_gmail_artifact_bindings WHERE plan_id=?""",
                        (data["plan_id"],)).fetchone()
                    if old is not None:
                        if old[:9] != identity:
                            raise SignedGmailArtifactError("gmail_binding_conflict")
                        return {"result":"ALREADY_RECORDED", **self._public(old)}
                    # Never let a later signer retroactively certify a
                    # previously checked, unsigned Stage 120 Gmail result.
                    if self.db.execute("""SELECT 1 FROM sqlite_master
                            WHERE type='table' AND name='gmail_sent_evidence'""").fetchone():
                        if self.db.execute("""SELECT 1 FROM gmail_sent_evidence
                                WHERE plan_id=?""", (data["plan_id"],)).fetchone():
                            raise SignedGmailArtifactError(
                                "prior_gmail_check_cannot_be_retroactively_bound")
                    self.db.execute("""INSERT INTO signed_gmail_artifact_bindings
                        (plan_id,connector_id,delivery_id,execution_id,
                         signed_message_sha256,gmail_id_sha256,
                         recipient_sha256,subject_sha256,
                         artifact_message_sha256,recorded_utc)
                        VALUES(?,?,?,?,?,?,?,?,?,?)""", identity+(at,))
                    return {"result":"RECORDED", **self._public(identity+(at,))}
            except ManagedConnectorError as exc:
                raise SignedGmailArtifactError(str(exc),exc.status) from None
            except sqlite3.IntegrityError:
                raise SignedGmailArtifactError("gmail_binding_conflict") from None
            except sqlite3.Error:
                raise SignedGmailArtifactError("gmail_binding_storage_unavailable",503) from None

    def require_match(self, plan_id, message_id, to_address, subject):
        """Fail closed before Stage 120 refresh token or Gmail API GET."""
        if (type(message_id) is not str or not MESSAGE_ID.fullmatch(message_id)
                or type(to_address) is not str or not EMAIL.fullmatch(to_address)
                or type(subject) is not str):
            raise SignedGmailArtifactError("invalid_gmail_binding_query",422)
        with self.lock:
            try:
                row = self._correlation(plan_id)
                saved = self.db.execute("""SELECT connector_id,delivery_id,
                    execution_id,signed_message_sha256,gmail_id_sha256,
                    recipient_sha256,subject_sha256
                    FROM signed_gmail_artifact_bindings WHERE plan_id=?""",
                    (plan_id,)).fetchone()
                if saved is None:
                    raise SignedGmailArtifactError("signed_gmail_binding_required")
                expected = (
                    row[1],row[4],row[5],row[6],
                    _digest(message_id),_digest(to_address.casefold()),_digest(subject))
                if not all(hmac.compare_digest(str(a), str(b))
                           for a,b in zip(saved,expected)):
                    raise SignedGmailArtifactError("signed_gmail_binding_mismatch")
                return True
            except sqlite3.Error:
                raise SignedGmailArtifactError("gmail_binding_storage_unavailable",503) from None

    def history(self):
        with self.lock:
            try:
                records = self.db.execute("""SELECT plan_id,connector_id,
                    delivery_id,execution_id,signed_message_sha256,
                    gmail_id_sha256,recipient_sha256,subject_sha256,
                    artifact_message_sha256,recorded_utc
                    FROM signed_gmail_artifact_bindings
                    ORDER BY recorded_utc DESC, plan_id DESC LIMIT ?""",
                    (MAX_HISTORY,)).fetchall()
            except sqlite3.Error:
                raise SignedGmailArtifactError("gmail_binding_storage_unavailable",503) from None
        return {"records":[self._public(row) for row in records],
                "evidence":"SHARED_SECRET_SIGNED_ASSERTION_NOT_PROVIDER_CAUSAL_PROOF",
                "n8n_causation_verified":False,
                "recipient_delivery_verified":False,
                "business_effect_verified":False}
