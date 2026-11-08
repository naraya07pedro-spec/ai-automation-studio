"""Stage 124: offline saved Gmail evidence chain audit, stages 120-122.

No remote provider lookup, vault key/HMAC secret decryption, or database writes.
Called within Stage 118's existing owner-stop guard and read-only SQLite
connection AFTER its complete six-ledger provenance audit passes.

This establishes *local recorded-evidence consistency*, not independent proof
of actual n8n/Gmail provider state, email delivery, or commercial outcome.
"""
from datetime import datetime, timezone
import hashlib
import hmac


DOMAIN = "AAS-GMAIL-ARTIFACT-BINDING-V1"


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc(value):
    """Stage 120-122 timestamps must be valid UTC instants, not fake strings."""
    if type(value) is not str:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        return None
    return parsed.astimezone(timezone.utc)


def check_saved_gmail_chain(db, pid, cid, wid, eid, did, exid, signed_msg_sha,
                           *, owner_pinned_gmail_node, one, require, hex_valid):
    """Reject omission, identity drift, proof-hash drift and replayed history.

    Important: the Stage 121 HMAC itself was verified at import and is not
    persisted. We recompute the canonical *message digest*, not the HMAC.
    """
    bound = one(db, """SELECT connector_id,delivery_id,execution_id,
        signed_message_sha256,gmail_id_sha256,recipient_sha256,
        subject_sha256,artifact_message_sha256,recorded_utc
        FROM signed_gmail_artifact_bindings WHERE plan_id=?""",
        (pid,), "signed_gmail_binding")
    bcid,bdid,bex,bmsg,gmail_sha,to_sha,subject_sha,claim_sha,at = bound
    require((bcid,bdid,bex,bmsg)==(cid,did,exid,signed_msg_sha),
            "signed_gmail_chain_identity_mismatch")
    require(all(hex_valid(x) for x in (
                gmail_sha,to_sha,subject_sha,claim_sha)),
            "signed_gmail_chain_hash_invalid")
    # This is the exact Stage 121 canonical claim message, including domain
    # and individually hashed Gmail message/normalized recipient/subject.
    canonical = "\n".join((DOMAIN,pid,cid,wid,eid,did,exid,
                             gmail_sha,to_sha,subject_sha))
    require(hmac.compare_digest(_sha(canonical),claim_sha),
            "signed_gmail_claim_digest_mismatch")
    recorded_at = _utc(at)
    require(recorded_at is not None, "signed_gmail_recorded_time_invalid")

    output = one(db, """SELECT execution_id,delivery_id,
        pinned_send_node_sha256,signed_gmail_id_sha256,status,
        checked_at_utc
        FROM n8n_gmail_output_witnesses WHERE plan_id=?""",
        (pid,), "n8n_gmail_send_output")
    oex,odid,node_sha,out_gmail_sha,out_state,checked = output
    require((oex,odid)==(exid,did),
            "n8n_gmail_output_execution_or_delivery_mismatch")
    require(hex_valid(node_sha) and hmac.compare_digest(
        node_sha,_sha(owner_pinned_gmail_node)),
        "n8n_gmail_send_node_not_owner_pinned")
    require(out_gmail_sha==gmail_sha and hex_valid(out_gmail_sha),
            "n8n_gmail_output_identity_mismatch")
    require(out_state=="PROVIDER_SEND_NODE_ID_CORRELATED",
            "n8n_gmail_output_not_correlated")
    checked_at = _utc(checked)
    require(checked_at is not None and recorded_at<=checked_at,
            "n8n_gmail_output_order_invalid")

    sent = one(db, """SELECT gmail_message_sha256,expected_to_sha256,
        expected_subject_sha256,n8n_execution_sha256,status,
        gmail_sent_label_seen,gmail_artifact_sha256,claimed_at_utc
        FROM gmail_sent_evidence WHERE plan_id=?""",
        (pid,), "gmail_sent_evidence")
    mid,to,subject,link,status,label_seen,artifact_sha,claimed = sent
    require((mid,to,subject)==(gmail_sha,to_sha,subject_sha),
            "gmail_sent_identity_mismatch")
    require(hex_valid(link) and hmac.compare_digest(
        link,_sha(exid+":"+did)),
        "gmail_sent_execution_delivery_mismatch")
    require(status=="SENT_ARTIFACT_MATCHED" and type(label_seen) is int
            and label_seen==1 and hex_valid(artifact_sha),
            "gmail_sent_not_independently_matched")
    claimed_at = _utc(claimed)
    require(claimed_at is not None and checked_at<=claimed_at,
            "gmail_sent_order_invalid")

    return ["stage121_signed_gmail_claim_digest_and_binding",
            "stage122_recorded_n8n_send_output_identity",
            "stage120_saved_gmail_sent_metadata_identity"]
