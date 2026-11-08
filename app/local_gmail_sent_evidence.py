"""Stage 120: owner-approved, one-shot independent Gmail SENT artifact witness.

Requires an already correlated Stage 117 n8n execution and Stage 116 signed
handoff. The Gmail message ID is supplied independently by the owner and is
NOT cryptographically tied to the n8n execution. A SENT artifact proves only
an item in this Google account, NOT delivery or that n8n caused the send.
No provider write, body read, automatic retry or model permission.
"""
from datetime import datetime, timezone
from email.utils import getaddresses
from hashlib import sha256
import re
import sqlite3

from local_agent_task_plans import ID_PATTERN as PLAN_ID

MESSAGE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$", re.ASCII)
EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.-]{1,190}$", re.ASCII)
MAX_TOTAL = 100
MAX_PER_DAY = 5


class GmailSentEvidenceError(RuntimeError):
    def __init__(self, reason, status=409):
        super().__init__(reason)
        self.status = status


def _hash(text):
    return sha256(text.encode("utf-8")).hexdigest()


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate(data):
    if type(data) is not dict or set(data) != {
            "plan_id", "gmail_message_id", "expected_to", "expected_subject"}:
        raise GmailSentEvidenceError("invalid_gmail_evidence_fields", 422)
    p, mid, to, subject = (data[k] for k in (
        "plan_id", "gmail_message_id", "expected_to", "expected_subject"))
    if any(type(x) is not str for x in (p, mid, to, subject)):
        raise GmailSentEvidenceError("invalid_gmail_evidence_fields", 422)
    if (not PLAN_ID.fullmatch(p) or not MESSAGE_ID.fullmatch(mid)
            or not EMAIL.fullmatch(to) or ".." in to or to.startswith(".")
            or not 1 <= len(subject) <= 200
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in subject)):
        raise GmailSentEvidenceError("invalid_gmail_evidence_fields", 422)
    return p, mid, to.casefold(), subject


def _single_to(value):
    if type(value) is not str or len(value) > 1024:
        return None
    addresses = getaddresses([value])
    if len(addresses) != 1:
        return None
    addr = addresses[0][1]
    if not EMAIL.fullmatch(addr) or ".." in addr:
        return None
    return addr.casefold()


class GmailSentEvidence:
    """Uses the EXISTING private handoff SQLite ledger, no independent DB."""

    def __init__(self, n8n_verifier, gmail, *, signed_artifact_binding=None):
        if n8n_verifier is None or gmail is None:
            raise GmailSentEvidenceError("n8n_and_gmail_required", 403)
        self.n8n = n8n_verifier
        self.gmail = gmail
        self.signed_artifact_binding = signed_artifact_binding
        self.handoff = n8n_verifier.handoff
        self.lock = self.handoff.lock
        self.db = self.handoff.db
        with self.lock, self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS gmail_sent_evidence(
                plan_id TEXT PRIMARY KEY,
                gmail_message_sha256 TEXT NOT NULL,
                expected_to_sha256 TEXT NOT NULL,
                expected_subject_sha256 TEXT NOT NULL,
                n8n_execution_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                gmail_sent_label_seen INTEGER NOT NULL DEFAULT 0,
                gmail_artifact_sha256 TEXT,
                claimed_at_utc TEXT NOT NULL,
                FOREIGN KEY(plan_id) REFERENCES agent_connector_handoffs(plan_id)
            )""")
            self.db.execute("""UPDATE gmail_sent_evidence
                SET status='LOOKUP_UNCERTAIN' WHERE status='IN_FLIGHT'""")

    def _authorized_chain(self, plan):
        row = self.db.execute("""SELECT p.delivery_id,p.execution_id,
                 p.claimed_outcome,p.connector_id,p.signed_message_sha256,
                 n.delivery_id,n.execution_id,n.connector_id,
                 n.signed_message_sha256,n.status,n.provider_finished,
                 n.inbound_delivery_correlated
            FROM signed_manual_handoff_outcomes p
            JOIN n8n_provider_execution_checks n ON n.plan_id=p.plan_id
            WHERE p.plan_id=?""", (plan,)).fetchone()
        if (row is None or row[2] != "COMPLETED" or row[0] != row[5]
                or row[1] != row[6] or row[3] != row[7]
                or row[4] != row[8]
                or row[9] != "EXECUTION_SUCCESS_CORRELATED"
                or row[10] != 1 or row[11] != 1):
            raise GmailSentEvidenceError("verified_n8n_success_chain_required")
        return _hash(row[1] + ":" + row[0])

    @staticmethod
    def _public(row):
        return {
            "plan_id": row[0], "status": row[5],
            "gmail_sent_label_seen": bool(row[6]),
            "gmail_message_sha256": row[1],
            "gmail_artifact_sha256": row[7],
            "n8n_execution_sha256": row[4],
            "checked_at_utc": row[8],
            "gmail_sent_artifact_verified": row[5] == "SENT_ARTIFACT_MATCHED",
            "causal_link_to_n8n_verified": False,
            "recipient_delivery_verified": False,
            "business_effect_verified": False,
            "model_calls": 0, "external_write_count": 0,
        }

    def verify_one(self, data):
        plan, message_id, to, subject = _validate(data)
        with self.lock:
            try:
                witness = self._authorized_chain(plan)
                if self.signed_artifact_binding is not None:
                    # Stage 121 strict opt-in: unsigned owner-chosen Gmail IDs
                    # must never reach a Google token refresh or provider GET.
                    from local_signed_gmail_artifact import SignedGmailArtifactError
                    try:
                        self.signed_artifact_binding.require_match(
                            plan, message_id, to, subject)
                    except SignedGmailArtifactError as exc:
                        raise GmailSentEvidenceError(str(exc), exc.status) from None
                with self.db:
                    if self.db.execute(
                            "SELECT 1 FROM gmail_sent_evidence WHERE plan_id=?",
                            (plan,)).fetchone():
                        raise GmailSentEvidenceError("gmail_evidence_already_checked")
                    if self.db.execute(
                            "SELECT COUNT(*) FROM gmail_sent_evidence").fetchone()[0] >= MAX_TOTAL:
                        raise GmailSentEvidenceError("gmail_evidence_lifetime_limit")
                    today = datetime.now(timezone.utc).date().isoformat()
                    if self.db.execute("""SELECT COUNT(*) FROM gmail_sent_evidence
                            WHERE claimed_at_utc>=?""", (today,)).fetchone()[0] >= MAX_PER_DAY:
                        raise GmailSentEvidenceError("gmail_evidence_daily_limit")
                    self.db.execute("""INSERT INTO gmail_sent_evidence(
                        plan_id,gmail_message_sha256,expected_to_sha256,
                        expected_subject_sha256,n8n_execution_sha256,status,
                        claimed_at_utc) VALUES(?,?,?,?,?,'IN_FLIGHT',?)""",
                        (plan,_hash(message_id),_hash(to),_hash(subject),witness,_utc()))
            except sqlite3.Error:
                raise GmailSentEvidenceError("gmail_evidence_storage_unavailable", 503) from None
        # Exactly one scoped Gmail GET, after a token refresh. Never hold the
        # SQLite mutex across a remote request and never retry uncertain reads.
        result = None
        try:
            result = self.gmail.sent_message_metadata(message_id)
        except Exception:
            pass
        sent = (type(result) is dict and result.get("id") == message_id
                and result.get("sent") is True)
        matched = (sent and _single_to(result.get("to")) == to
                   and result.get("subject") == subject)
        status = ("SENT_ARTIFACT_MATCHED" if matched else
                  "NOT_CORRELATED" if type(result) is dict else
                  "LOOKUP_UNCERTAIN")
        fingerprint = (_hash(message_id + "\n" + to + "\n" + subject)
                       if matched else None)
        with self.lock:
            try:
                with self.db:
                    self.db.execute("""UPDATE gmail_sent_evidence SET
                        status=?,gmail_sent_label_seen=?,gmail_artifact_sha256=?
                        WHERE plan_id=? AND status='IN_FLIGHT'""",
                        (status, int(bool(sent)), fingerprint, plan))
                    row = self.db.execute("""SELECT plan_id,gmail_message_sha256,
                        expected_to_sha256,expected_subject_sha256,
                        n8n_execution_sha256,status,gmail_sent_label_seen,
                        gmail_artifact_sha256,claimed_at_utc
                        FROM gmail_sent_evidence WHERE plan_id=?""", (plan,)).fetchone()
            except sqlite3.Error:
                raise GmailSentEvidenceError("gmail_evidence_result_uncertain", 503) from None
        if row is None or row[5] != status:
            raise GmailSentEvidenceError("gmail_evidence_result_uncertain", 503)
        return self._public(row)

    def history(self):
        with self.lock:
            try:
                rows = self.db.execute("""SELECT plan_id,gmail_message_sha256,
                    expected_to_sha256,expected_subject_sha256,
                    n8n_execution_sha256,status,gmail_sent_label_seen,
                    gmail_artifact_sha256,claimed_at_utc
                    FROM gmail_sent_evidence
                    ORDER BY claimed_at_utc DESC,plan_id DESC LIMIT 30""").fetchall()
            except sqlite3.Error:
                raise GmailSentEvidenceError("gmail_evidence_storage_unavailable", 503) from None
        return {"records": [self._public(row) for row in rows],
                "proof_scope": "GMAIL_SENT_METADATA_ARTIFACT_ONLY",
                "causal_link_to_n8n_verified": False,
                "recipient_delivery_verified": False,
                "business_effect_verified": False}
