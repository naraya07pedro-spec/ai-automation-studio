"""Stage 118: strictly offline, read-only cross-ledger evidence integrity audit.

Operates only after the owner STOPS Studio. Recomputes original Stage 114
plan/workflow/payload hashes and matches Stage 116 signed claim and Stage 117
saved provider lookup record to the SAME delivery. No keys are read, decrypted,
printed or saved. No HTTP request is made. A locally consistent provider
record is *not* independently revalidated against live n8n.
"""
import argparse
from contextlib import closing
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys

from offline_owner_backup import _offline_guard, _source_root, _check_private

PLAN_ID = re.compile(r"^[a-f0-9]{24}$", re.ASCII)
CID = re.compile(r"^[a-z][a-z0-9_-]{2,39}$", re.ASCII)
DIGEST = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
DELIVERY = re.compile(r"^[a-f0-9]{32}$", re.ASCII)
HOST = re.compile(r"^[A-Za-z0-9.-]{4,253}$", re.ASCII)
MAX_DB = 32 * 1024 * 1024

class AcceptanceError(RuntimeError):
    pass

def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()

def _sender_sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True,
        ensure_ascii=True, separators=(",", ":")).encode("ascii")).hexdigest()

def _hex(value):
    return type(value) is str and DIGEST.fullmatch(value) is not None

def _require(condition, error):
    if not condition:
        raise AcceptanceError(error)

def _open_private_sqlite(root, relative):
    """Read-only immutable is safe only with clean shutdown and NO WAL sidecars."""
    path = root / relative
    _require(path.parent.is_dir() and not path.parent.is_symlink(),
             "ledger_parent_missing_or_symlink")
    _require(path.is_file() and not path.is_symlink(),
             "ledger_missing_or_symlink")
    _check_private(path)
    _require(path.stat().st_size <= MAX_DB, "ledger_size_limit_exceeded")
    for suffix in ("-wal","-shm","-journal"):
        _require(not Path(str(path)+suffix).exists(),
                 "ledger_requires_clean_shutdown")
    try:
        db = sqlite3.connect(path.as_uri()+"?mode=ro&immutable=1",
                             uri=True,timeout=2)
        db.execute("PRAGMA query_only=ON")
        _require(db.execute("PRAGMA quick_check").fetchone()==("ok",),
                 "ledger_sqlite_integrity_failed")
        return db
    except sqlite3.Error:
        raise AcceptanceError("ledger_sqlite_integrity_failed") from None

def _one(db, query, params, name):
    try:
        result=db.execute(query,params).fetchall()
    except sqlite3.Error:
        raise AcceptanceError(name+"_schema_or_query_invalid") from None
    _require(len(result)==1, name+"_missing_or_ambiguous")
    return result[0]

def _decode_list(value, field):
    try:
        decoded=json.loads(value)
    except (TypeError,ValueError):
        raise AcceptanceError(field+"_invalid_json") from None
    _require(type(decoded) is list and len(decoded)<=3, field+"_invalid_length")
    return decoded

def audit(root, plan_id, *, expected_host=None, expected_workflow=None,
          expected_node=None):
    """No live writes. Return non-sensitive verdict, never raw evidence contents."""
    _require(type(plan_id) is str and PLAN_ID.fullmatch(plan_id),
             "invalid_plan_id")
    _require(type(root) in (str,Path), "invalid_owner_root")
    source=Path(root).expanduser()
    _require(not source.is_symlink(), "owner_root_symlink")
    source=source.resolve(strict=True)
    _source_root(source)
    _check_private(source/"operator.json")
    if expected_host is not None:
        _require(type(expected_host) is str and HOST.fullmatch(expected_host)
                 and "." in expected_host, "invalid_pinned_host")
    if expected_workflow is not None:
        _require(type(expected_workflow) is str and
                 re.fullmatch(r"[A-Za-z0-9_-]{1,128}",expected_workflow),
                 "invalid_pinned_workflow")
    if expected_node is not None:
        _require(type(expected_node) is str and
                 1 <= len(expected_node) <= 100 and
                 all(32<=ord(c)<127 for c in expected_node),
                 "invalid_pinned_node")
    with _offline_guard():
        with closing(_open_private_sqlite(source,"agent-task-plans.sqlite3")) as plans, \
             closing(_open_private_sqlite(source,"local-workflows.sqlite3")) as workflows, \
             closing(_open_private_sqlite(source,"agent-connector-handoffs.sqlite3")) as handoffs:
            plan=_one(plans, """SELECT model,goal,steps_json,results_json,
                next_index,status FROM agent_task_plans WHERE plan_id=?""",
                (plan_id,),"plan")
            steps=_decode_list(plan[2],"plan_steps")
            results=_decode_list(plan[3],"plan_results")
            _require(plan[5]=="COMPLETED" and type(plan[4]) is int
                     and 1<=len(steps)<=3 and len(results)==len(steps)
                     and plan[4]==len(steps), "plan_not_completed")
            evidence={"plan_id":plan_id,"model":plan[0],"goal":plan[1],
                      "steps":steps,"results":results}
            handoff=_one(handoffs, """SELECT connector_id,workflow_id,event_id,
                plan_sha256,workflow_sha256,payload_sha256,status,delivery_id,
                http_status FROM agent_connector_handoffs WHERE plan_id=?""",
                (plan_id,),"handoff")
            cid,wid,eid,plan_sha,workflow_sha,payload_sha,hs,did,http_status=handoff
            _require(type(cid) is str and CID.fullmatch(cid),
                     "connector_id_invalid")
            _require(type(did) is str and DELIVERY.fullmatch(did),
                     "delivery_id_invalid")
            _require(all(_hex(h) for h in (plan_sha,workflow_sha,payload_sha)),
                     "handoff_digest_invalid")
            _require(hmac.compare_digest(_sha(evidence),plan_sha),
                     "plan_provenance_hash_mismatch")
            _require(hs in ("REMOTE_ACCEPTED","OUTCOME_UNCERTAIN",
                            "REVIEW_REQUIRED","IN_FLIGHT"),
                     "handoff_not_claimed")
            _require(type(wid) is str and type(eid) is str,
                     "workflow_identity_invalid")
            runrow=_one(workflows, """SELECT workflow_version,decision,reason,
                created_at FROM local_runs WHERE workflow_id=? AND event_id=?""",
                (wid,eid),"workflow_run")
            run={"workflow_id":wid,"event_id":eid,
                 "workflow_version":runrow[0],"decision":runrow[1],
                 "reason":runrow[2],"created_at":runrow[3],
                 "external_dispatch_count":0}
            _require(hmac.compare_digest(_sha(run),workflow_sha),
                     "workflow_receipt_hash_mismatch")
            expected_delivery=_sender_sha({"workflow_id":wid,"event_id":eid})[:32]
            _require(hmac.compare_digest(did,expected_delivery),
                     "delivery_id_not_derived_from_original_workflow")
            payload={"schema":"aas.local_workflow_receipt.v1",
                     "delivery_id":did,"workflow_id":wid,"event_id":eid,
                     "workflow_version":runrow[0],"decision":runrow[1],
                     "reason":runrow[2],"created_at":runrow[3]}
            _require(hmac.compare_digest(_sender_sha(payload),payload_sha),
                     "sent_payload_hash_mismatch")
            with closing(_open_private_sqlite(
                    source,"managed-webhook-ledgers/"+cid+".sqlite3")) as senddb:
                sender=_one(senddb, """SELECT status,payload_sha256,delivery_id,
                    target_sha256,http_status FROM webhook_deliveries
                    WHERE workflow_id=? AND event_id=?""",
                    (wid,eid),"sender")
            _require(sender[0] in ("REMOTE_ACCEPTED","OUTCOME_UNCERTAIN"),
                     "sender_attempt_not_finalized")
            _require(sender[2]==did and _hex(sender[1]) and _hex(sender[3])
                     and hmac.compare_digest(sender[1],payload_sha),
                     "sender_ledger_binding_mismatch")
            if hs=="REMOTE_ACCEPTED":
                _require(sender[0]=="REMOTE_ACCEPTED" and
                         type(http_status) is int and 200<=http_status<300
                         and sender[4]==http_status,
                         "sender_acknowledgment_mismatch")
            signed=_one(handoffs, """SELECT connector_id,delivery_id,
                workflow_id,event_id,claimed_outcome,execution_id,
                signed_message_sha256,dispatch_status_at_import
                FROM signed_manual_handoff_outcomes WHERE plan_id=?""",
                (plan_id,),"signed_receipt")
            _require(signed[0:4]==(cid,did,wid,eid),
                     "signed_receipt_identity_mismatch")
            claim,exid,hash_msg,signed_sender_status=signed[4:]
            _require(claim in ("COMPLETED","FAILED") and type(exid) is str
                     and re.fullmatch(r"[1-9][0-9]{0,15}",exid),
                     "signed_outcome_or_execution_invalid")
            _require(signed_sender_status==sender[0],
                     "signed_sender_status_mismatch")
            msg="\n".join(("AAS-MANUAL-HANDOFF-N8N-FINAL-RECEIPT-V1",
                           cid,wid,eid,did,claim,exid))
            _require(_hex(hash_msg) and
                     hmac.compare_digest(hashlib.sha256(
                         msg.encode("ascii")).hexdigest(),hash_msg),
                     "signed_message_binding_mismatch")
            provider=_one(handoffs, """SELECT connector_id,execution_id,
                delivery_id,provider_workflow_id,pinned_node_sha256,
                signed_message_sha256,status,provider_execution_status,
                provider_finished,inbound_delivery_correlated,
                provider_metadata_sha256,provider_hostname
                FROM n8n_provider_execution_checks WHERE plan_id=?""",
                (plan_id,),"provider_check")
            pcid,pexid,pdid,pwid,node_hash,provider_msg,status,pstate,finished,correlated,meta_sha,host=provider
            _require((pcid,pexid,pdid)==(cid,exid,did),
                     "provider_check_identity_mismatch")
            _require(_hex(node_hash) and _hex(meta_sha)
                     and _hex(provider_msg) and provider_msg==hash_msg,
                     "provider_check_digest_mismatch")
            _require(type(host) is str and HOST.fullmatch(host)
                     and "." in host, "provider_host_invalid")
            if expected_host is not None:
                _require(hmac.compare_digest(host.lower(),
                                             expected_host.lower()),
                         "provider_host_not_owner_pinned")
            if expected_workflow is not None:
                _require(pwid==expected_workflow,
                         "provider_workflow_not_owner_pinned")
            if expected_node is not None:
                _require(hmac.compare_digest(node_hash,
                    hashlib.sha256(expected_node.encode()).hexdigest()),
                    "provider_node_not_owner_pinned")
            _require(status in ("EXECUTION_SUCCESS_CORRELATED",
                                "EXECUTION_FAILED_CORRELATED"),
                     "provider_execution_not_successfully_correlated")
            _require(correlated==1 and finished in (0,1),
                     "provider_inbound_delivery_not_correlated")
            if status=="EXECUTION_SUCCESS_CORRELATED":
                _require(claim=="COMPLETED" and pstate=="success" and finished==1,
                         "provider_success_inconsistent")
            else:
                _require(claim=="FAILED" and pstate=="error",
                         "provider_failure_inconsistent")
            # No independent remote fetch here: it audits the locally recorded
            # Stage 117 response, not the trustworthiness of the provider.
            return {
                "status":"LOCAL_EVIDENCE_CHAIN_CONSISTENT",
                "checked_layers":["completed_plan","local_workflow_run",
                                  "stage114_handoff","actual_connector_sender_ledger",
                                  "stage116_signed_claim_record",
                                  "stage117_saved_provider_execution_record"],
                "recorded_provider_execution":status,
                "owner_host_pin_checked":expected_host is not None,
                "owner_workflow_pin_checked":expected_workflow is not None,
                "owner_node_pin_checked":expected_node is not None,
                "live_provider_queried_now":False,
                "hmac_secret_reverified_offline":False,
                "business_effect_verified":False,
                "remote_reads":0,"external_writes":0,
                "source_owner_data_exported":False,
            }

def main(argv=None):
    parser=argparse.ArgumentParser(
        description="OFFLINE read-only consistency audit of Stage 114-117 local ledgers. No network.")
    parser.add_argument("--plan-id",required=True)
    parser.add_argument("--owner-root",
                        default=str(Path.home()/".ai-automation-studio"))
    parser.add_argument("--expected-n8n-host")
    parser.add_argument("--expected-n8n-workflow")
    parser.add_argument("--expected-n8n-node")
    args=parser.parse_args(argv)
    try:
        result=audit(args.owner_root,args.plan_id,
                     expected_host=args.expected_n8n_host,
                     expected_workflow=args.expected_n8n_workflow,
                     expected_node=args.expected_n8n_node)
    except (AcceptanceError,OSError,ValueError,sqlite3.Error) as exc:
        reason=str(exc) if isinstance(exc,AcceptanceError) else "local_state_unreadable"
        print(json.dumps({"status":"LOCAL_EVIDENCE_CHAIN_NOT_PROVEN",
                          "reason":reason,"live_provider_queried_now":False,
                          "business_effect_verified":False,
                          "remote_reads":0,"external_writes":0},sort_keys=True))
        return 2
    print(json.dumps(result,sort_keys=True))
    return 0

if __name__=="__main__":
    sys.exit(main())
