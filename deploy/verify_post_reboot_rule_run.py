"""Observe one REAL systemd-worker deterministic due slot after actual Linux reboot.

Read-only, on-host, nonroot. Requires PR105 private pre-reboot baseline and
separate real Linux boot, plus the CURRENT systemd invocation ID and a local
rule result stamped by that invocation. No creation of schedules, no model,
no provider calls, no restart, no reboot and no operator secrets. No remote
attestation: a privileged actor or one editing SQLite can forge records.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

import verify_private_vps_reboot as reboot

MAX_WAIT_SECONDS = 180
WORKER_ID = re.compile(r"^[0-9a-f]{32}$", re.ASCII)
STATUS_OBSERVED = "LOCAL_RULE_POST_REBOOT_SERVICE_INVOCATION_OBSERVED"
STATUS_PENDING = "POST_REBOOT_LOCAL_RULE_EXECUTION_NOT_YET_OBSERVED"


class PostBootRuleError(ValueError):
    pass


def _date(value):
    if type(value) is not str:
        raise PostBootRuleError("invalid_runtime_timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise PostBootRuleError("invalid_runtime_timestamp") from None
    if parsed.tzinfo is None:
        raise PostBootRuleError("naive_runtime_timestamp")
    return parsed.astimezone(timezone.utc)


def linux_boot_time():
    """Kernel's actual boot epoch, not user's entered wall-clock estimate."""
    try:
        stat = Path("/proc/stat").read_text(encoding="ascii")
    except (OSError, UnicodeError):
        raise PostBootRuleError("linux_boot_time_unavailable") from None
    matched = re.findall(r"^btime ([0-9]{9,11})$", stat, re.MULTILINE)
    if len(matched) != 1:
        raise PostBootRuleError("linux_boot_time_unavailable")
    return datetime.fromtimestamp(int(matched[0]), tz=timezone.utc)


def current_invocation():
    """Fixed service name and fixed systemctl property; no user arguments."""
    raw = reboot._systemctl(["show", "--property=InvocationID", "--value"])
    if not WORKER_ID.fullmatch(raw.lower()):
        raise PostBootRuleError("systemd_invocation_identity_missing")
    return raw.lower()


def _state_path():
    user = reboot.pwd.getpwuid(os.geteuid())
    return reboot._restricted_home(user.pw_dir, os.geteuid())


def _read_observations(state, invocation, boot_id, boot_time, captured):
    """Only read schema-defined receipt columns, never snapshots or prompts."""
    path = state / "recurring-schedules.sqlite3"
    reboot._restricted_file(path, os.geteuid())
    if boot_time <= captured:
        raise PostBootRuleError("boot_did_not_occur_after_baseline")
    if boot_time >= datetime.now(timezone.utc):
        raise PostBootRuleError("invalid_kernel_boot_timestamp")
    try:
        db = sqlite3.connect(path.resolve().as_uri()+"?mode=ro",
                             uri=True,timeout=3)
        try:
            db.execute("PRAGMA query_only=ON")
            rows = db.execute("""SELECT r.slot_utc, r.finished_utc,
                    s.created_utc, r.status
                    FROM recurring_schedule_runs AS r
                    JOIN recurring_schedules AS s
                      ON s.schedule_id = r.schedule_id
                    WHERE r.completed_by_invocation=?
                      AND r.completed_boot_id=?
                      AND r.status='COMPLETED'
                    ORDER BY r.finished_utc DESC LIMIT 2000""",
                    (invocation,boot_id)).fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        raise PostBootRuleError("worker_receipt_schema_missing_or_unreadable") from None
    count = 0
    latest = None
    now = datetime.now(timezone.utc)
    for slot_raw,finish_raw,created_raw,status in rows:
        slot = _date(slot_raw)
        finished = _date(finish_raw)
        created = _date(created_raw)
        if (status == "COMPLETED" and created <= captured
                and slot > boot_time and finished >= slot
                and finished <= now and finished > boot_time):
            count += 1
            if latest is None or finished > latest:
                latest = finished
    return {"matching_completed_receipts": count,
            "latest_completed_utc": latest.isoformat(timespec="seconds")
                                     if latest is not None else None}


def observe(wait_seconds=0):
    if type(wait_seconds) is not int or not 0 <= wait_seconds <= MAX_WAIT_SECONDS:
        raise PostBootRuleError("invalid_wait_limit")
    # The two-phase original check must pass first, including matching user,
    # persisted task set, protected loopback and verified new Linux boot ID.
    after = reboot.live_snapshot()
    baseline = reboot.read_baseline(reboot.baseline_path())
    compared = reboot.compare_reboot(baseline,after)
    if not compared["schedule_persistence_proven"]:
        raise PostBootRuleError("no_preexisting_schedule_to_validate")
    state = _state_path()
    boot = linux_boot_time()
    captured = _date(baseline["captured_utc"])
    if boot <= captured:
        raise PostBootRuleError("kernel_boot_not_newer_than_original_baseline")
    invocation = current_invocation()
    if not (after["state"]["stores"]["recurring-schedules.sqlite3"]["task_count"] > 0):
        raise PostBootRuleError("no_local_deterministic_schedule_preexisting")
    started = time.monotonic()
    last = None
    while True:
        last = _read_observations(state,invocation,after["boot_id"],boot,captured)
        if last["matching_completed_receipts"] or time.monotonic()-started >= wait_seconds:
            break
        time.sleep(min(5.0,wait_seconds-(time.monotonic()-started)))
    # Recheck service identity & privacy AFTER reading the new real ledger
    # so a service restart during wait cannot be mistaken for old invocation.
    final = reboot.live_snapshot()
    if final["boot_id"] != after["boot_id"]:
        raise PostBootRuleError("boot_changed_during_verification")
    if current_invocation() != invocation:
        raise PostBootRuleError("service_invocation_changed_during_verification")
    if final["state"]["stores"]["recurring-schedules.sqlite3"]["ids_sha256"] != (
            after["state"]["stores"]["recurring-schedules.sqlite3"]["ids_sha256"]):
        raise PostBootRuleError("schedule_set_changed_during_verification")
    found = bool(last["matching_completed_receipts"])
    return {
        "status": STATUS_OBSERVED if found else STATUS_PENDING,
        "observed_completed_local_rule_slots": last["matching_completed_receipts"],
        "latest_local_rule_finished_utc": last["latest_completed_utc"],
        "worker_invocation_matched_active_systemd": found,
        "worker_linux_boot_matched_current_boot": found,
        "existing_schedule_identities_preserved": True,
        "read_only_observation": True,
        "new_external_actions": 0,
        "model_or_n8n_called_by_this_verifier": False,
        "service_autostart_without_intervention_verified": False,
        "independent_tencent_identity_verified": False,
        "continued_24h_operation_verified": False,
        "result_provenance": "SAME_HOST_OBSERVATIONAL_SQLITE_SYSTEMD",
        "limitations": "A completed deterministic rule slot after Linux reboot matched the current systemd service invocation. This is not cryptographic remote attestation, proof of cloud identity, unattended service autostart, 24/7 uptime, AI inference, n8n downstream success, or proof against a privileged DB forger.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=(
        "READ-ONLY proof of a completed rule scheduler slot under the active"
        " systemd invocation after a distinct real Linux boot"))
    parser.add_argument("--wait-seconds",type=int,default=0,
                        help="Optional 0-180 seconds to observe next safe local rule tick")
    args = parser.parse_args(argv)
    try:
        result = observe(args.wait_seconds)
        exitcode = 0 if result["status"] == STATUS_OBSERVED else 3
    except (PostBootRuleError, reboot.RebootAcceptanceError,
            OSError, KeyError, TypeError, sqlite3.Error) as exc:
        result = {
            "status": "NOT_VERIFIED",
            "reason": str(exc) if isinstance(
                exc,(PostBootRuleError,reboot.RebootAcceptanceError))
                else "post_reboot_observation_unavailable",
            "actual_local_rule_execution_proven": False,
        }
        exitcode = 2
    print(json.dumps(result,sort_keys=True))
    return exitcode


if __name__ == "__main__":
    raise SystemExit(main())
