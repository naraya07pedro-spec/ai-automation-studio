"""Offline owner-review replacement of the paid one-shot model policy.

Never invokes an AI model, external provider, API key, or network.
The server must be OFF. No automatic retry or deletion of billed receipts.
"""
import argparse
from datetime import datetime, timezone
import getpass
import json
from pathlib import Path
import socket
import sqlite3

from local_on_demand_remote_ai import validate_model_allowlist, MAX_DAILY_ATTEMPTS
from local_private_sqlite_path import prepare_private_sqlite
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit


class RemotePolicyChangeBlocked(RuntimeError):
    pass


def replace_saved_policy(database, *, models, daily_limit=None, acknowledged=False):
    if acknowledged is not True:
        raise RemotePolicyChangeBlocked("explicit_owner_review_required")
    # models=None is a deliberate offline owner authorization to REMOVE the
    # model allowlist. Runtime startup NEVER authorizes that change.
    requested=validate_model_allowlist(models)
    if (daily_limit is not None and
            (type(daily_limit) is not int or
             not 1 <= daily_limit <= MAX_DAILY_ATTEMPTS)):
        raise RemotePolicyChangeBlocked("invalid_daily_cap")
    path=Path(database).expanduser()
    if path.is_symlink() or not path.is_file():
        raise RemotePolicyChangeBlocked("existing_remote_receipt_database_required")
    safe_path=prepare_private_sqlite(path)
    db=sqlite3.connect(safe_path,timeout=5,isolation_level=None)
    try:
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("BEGIN IMMEDIATE")
        try:
            # Never loosen policy during unresolved charge/provider outcomes.
            uncertain=db.execute("""SELECT COUNT(*) FROM remote_asks
                WHERE status IN ('IN_FLIGHT','OUTCOME_UNCERTAIN')""").fetchone()[0]
            if uncertain:
                raise RemotePolicyChangeBlocked(
                    "ambiguous_paid_receipts_require_external_billing_review")
            row=db.execute("""SELECT daily_attempt_limit,revision
                FROM remote_one_shot_policy WHERE id=1""").fetchone()
            current_cap=row[0] if row else MAX_DAILY_ATTEMPTS
            cap=daily_limit if daily_limit is not None else current_cap
            value=(json.dumps(sorted(p+":"+m for p,m in requested))
                   if requested is not None else "null")
            utc=datetime.now(timezone.utc).isoformat(timespec="seconds")
            if row is None:
                db.execute("""INSERT INTO remote_one_shot_policy
                    (id,approved_models_json,daily_attempt_limit,revision,updated_utc)
                    VALUES (1,?,?,1,?)""",(value,cap,utc))
                rev=1
            else:
                rev=row[1]+1
                changed=db.execute("""UPDATE remote_one_shot_policy
                    SET approved_models_json=?,daily_attempt_limit=?,
                        revision=revision+1,updated_utc=?
                    WHERE id=1 AND revision=?""",
                    (value,cap,utc,row[1])).rowcount
                if changed!=1:
                    raise RemotePolicyChangeBlocked("remote_policy_revision_conflict")
            db.commit()
        except BaseException:
            db.rollback()
            raise
    except sqlite3.Error:
        raise RemotePolicyChangeBlocked(
            "remote_policy_change_storage_uncertain_inspect_receipts") from None
    finally:
        db.close()
    return {"status":"LOCAL_REMOTE_POLICY_REPLACED_OFFLINE",
            "revision":rev,"approved_models":(
                sorted(p+":"+m for p,m in requested) if requested is not None else None),
            "maximum_attempts_per_utc_day":cap,
            "provider_billing_verified":False,
            "history_deleted":False}


def main(argv=None):
    parser=argparse.ArgumentParser(
        description="Owner-only offline replacement of one-shot paid AI policy.")
    parser.add_argument("--replace",action="store_true",required=True)
    options=parser.add_mutually_exclusive_group(required=True)
    options.add_argument("--allow-remote-model",action="append",default=None,
                         metavar="PROVIDER:MODEL")
    options.add_argument("--allow-any-models",action="store_true")
    parser.add_argument("--daily-attempts",type=int,default=None,
                        help="1..5; omitted preserves the stored cap.")
    args=parser.parse_args(argv)
    guard=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try:
        guard.bind(("127.0.0.1",8765))
        guard.listen(1)
    except OSError:
        guard.close()
        raise SystemExit("Stop the local Studio before modifying paid policy.")
    try:
        home=Path.home()/".ai-automation-studio"
        owner=LocalOperatorAuth.load(home/"operator.json")
        password=getpass.getpass("Current owner password (never echoed): ")
        if owner.confirm_current_password(password)!=200:
            raise SystemExit("Current owner password rejected.")
        models=None if args.allow_any_models else args.allow_remote_model
        verified=validate_model_allowlist(models)
        print("New approved models:",(sorted(p+":"+m for p,m in verified)
                                       if verified is not None else "UNRESTRICTED"))
        print("New daily cap:",args.daily_attempts if args.daily_attempts else
              "PRESERVE EXISTING")
        print("This is NOT a USD spend guarantee. Billing remains provider-controlled.")
        print("No provider calls, retries or receipts will be deleted.")
        if input("Type exactly REPLACE PAID MODEL POLICY to proceed: ") != (
                "REPLACE PAID MODEL POLICY"):
            raise SystemExit("Confirmation declined; no policy change.")
        audit=LocalSecurityAudit(home/"security-audit.sqlite3")
        try:
            audit.record("REMOTE_MODEL_POLICY_CONFIG","ATTEMPT")
            try:
                result=replace_saved_policy(
                    home/"on-demand-remote-ai.sqlite3",
                    models=models,daily_limit=args.daily_attempts,
                    acknowledged=True)
            except Exception:
                audit.record("REMOTE_MODEL_POLICY_CONFIG","UNCERTAIN")
                raise
            audit.record("REMOTE_MODEL_POLICY_CONFIG","SUCCESS")
            print("Saved owner policy revision:",result["revision"])
            print("Restart Studio normally. Exact persisted policy is enforced.")
        finally:
            audit.close()
    finally:
        guard.close()


if __name__=="__main__":
    main()
