"""Read-only two-phase ON-HOST Linux reboot persistence acceptance.

No reboot, service install, restart, password, backup or outbound network
actions. Real CLI runs only as the configured NONROOT systemd service user.
It confirms separate boot IDs, the existing private HTTP safety snapshot,
service enabled+active, owner-config identity, and existing schedule IDs.

The baseline is local owner-only JSON and never contains user data, task IDs,
prompt text, webhook credentials, tokens, public IPs or any task result.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import sqlite3
import stat
import subprocess
import sys

import verify_private_vps_runtime as runtime

UNIT = "ai-automation-studio.service"
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
BOOT_PATTERN = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.ASCII)
BASELINE_NAME = "private-vps-reboot-baseline-v1.json"
SCHEMA = 1
MAX_IDS = 10000
STATE_STORES = (
    ("recurring-schedules.sqlite3", "recurring_schedules",
     "schedule_id", "recurring_schedule_runs"),
    ("readonly-ai-schedules.sqlite3", "ai_schedules",
     "task_id", "ai_schedule_runs"),
)


class RebootAcceptanceError(ValueError):
    pass


def _restricted_file(path, uid, *, size_limit=None):
    path = Path(path)
    if path.is_symlink():
        raise RebootAcceptanceError("unsafe_state_file")
    try:
        meta = path.stat()
    except OSError:
        raise RebootAcceptanceError("state_file_missing_or_unreadable") from None
    if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != uid
            or stat.S_IMODE(meta.st_mode) & 0o077):
        raise RebootAcceptanceError("state_file_owner_or_permissions_unsafe")
    if size_limit is not None and not 1 <= meta.st_size <= size_limit:
        raise RebootAcceptanceError("state_file_invalid_size")
    return path


def _restricted_home(home, uid):
    home = Path(home)
    state = home / ".ai-automation-studio"
    if state.is_symlink() or not state.is_dir():
        raise RebootAcceptanceError("private_state_directory_missing_or_unsafe")
    mode = state.stat()
    if mode.st_uid != uid or stat.S_IMODE(mode.st_mode) & 0o077:
        raise RebootAcceptanceError("private_state_directory_permissions_unsafe")
    return state


def _read_state_store(state, uid, specification):
    filename, table, identifier, runs_table = specification
    path = state / filename
    if not path.exists():
        if path.is_symlink():
            raise RebootAcceptanceError("unsafe_state_file")
        return {"present": False, "task_count": 0,
                "ids_sha256": None, "run_count": 0}
    _restricted_file(path, uid)
    try:
        # Read-only SQLite URI. Do not copy the DB, create new tables,
        # checkpoint WAL, or touch the scheduler while it is running.
        database = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", timeout=3,
                                   uri=True)
        try:
            ids = database.execute(
                "SELECT " + identifier + " FROM " + table +
                " ORDER BY " + identifier + " LIMIT ?", (MAX_IDS + 1,)).fetchall()
            if len(ids) > MAX_IDS or any(type(row[0]) is not str for row in ids):
                raise RebootAcceptanceError("invalid_or_unbounded_schedule_state")
            n_runs = database.execute(
                "SELECT count(*) FROM " + runs_table).fetchone()[0]
            if type(n_runs) is not int or n_runs < 0:
                raise RebootAcceptanceError("invalid_schedule_run_count")
        finally:
            database.close()
    except sqlite3.Error:
        raise RebootAcceptanceError("schedule_state_unreadable") from None
    digest = hashlib.sha256()
    for (identifier_value,) in ids:
        # Length prefix prevents concatenation ambiguity.
        data = identifier_value.encode("utf-8")
        digest.update(len(data).to_bytes(4, "big"))
        digest.update(data)
    return {"present": True, "task_count": len(ids),
            "ids_sha256": digest.hexdigest(), "run_count": n_runs}


def read_state_snapshot(home, uid):
    state = _restricted_home(home, uid)
    owner = _restricted_file(state / "operator.json", uid, size_limit=65536)
    owner_digest = hashlib.sha256(owner.read_bytes()).hexdigest()
    stores = {part[0]: _read_state_store(state, uid, part) for part in STATE_STORES}
    return {
        "owner_config_sha256": owner_digest,
        "stores": stores,
    }


def _boot_id():
    try:
        raw = BOOT_ID.read_text(encoding="ascii").strip().lower()
    except (OSError, UnicodeError):
        raise RebootAcceptanceError("boot_id_unavailable") from None
    if not BOOT_PATTERN.fullmatch(raw):
        raise RebootAcceptanceError("boot_id_invalid")
    return raw


def _systemctl(args):
    try:
        result = subprocess.run(["systemctl", *args, UNIT], check=False,
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        raise RebootAcceptanceError("systemd_query_unavailable") from None
    if result.returncode != 0:
        raise RebootAcceptanceError("systemd_service_not_enabled_or_configured")
    return result.stdout.strip()


def _linux_systemd():
    return sys.platform == "linux" and Path("/run/systemd/system").is_dir()


def live_snapshot():
    if not _linux_systemd():
        raise RebootAcceptanceError("linux_systemd_host_required")
    uid = os.geteuid()
    if uid == 0:
        raise RebootAcceptanceError("run_as_dedicated_nonroot_service_user")
    account = pwd.getpwuid(uid)
    service_user = _systemctl(["show", "--property=User", "--value"])
    if service_user != account.pw_name:
        raise RebootAcceptanceError("must_run_as_matching_systemd_service_user")
    # Require persistently enabled, not enabled-runtime or merely "static".
    # A currently active process by itself is not evidence of boot startup.
    if _systemctl(["is-enabled"]) != "enabled":
        raise RebootAcceptanceError("systemd_unit_not_persistently_enabled")
    current = runtime.probe()
    if current.get("status") != "LIVE_PRIVATE_VPS_SNAPSHOT_PASSED":
        raise RebootAcceptanceError("private_vps_runtime_snapshot_not_passed")
    state = read_state_snapshot(account.pw_dir, uid)
    return {
        "schema": SCHEMA,
        "boot_id": _boot_id(),
        "state": state,
        "captured_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checks": {
            "systemd_service_enabled": True,
            "systemd_service_active": True,
            "loopback_only_http_authenticated": True,
            "same_nonroot_service_identity": True,
        },
    }


def baseline_path(home=None):
    if home is None:
        home = pwd.getpwuid(os.geteuid()).pw_dir
    state = _restricted_home(home, os.geteuid())
    directory = state / "live-acceptance"
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise RebootAcceptanceError("unsafe_acceptance_directory")
    directory.mkdir(exist_ok=True, mode=0o700)
    meta = directory.stat()
    if meta.st_uid != os.geteuid() or stat.S_IMODE(meta.st_mode) & 0o077:
        raise RebootAcceptanceError("acceptance_directory_permissions_unsafe")
    return directory / BASELINE_NAME


def store_baseline(path, snapshot):
    """Never overwrite. A fresh maintenance cycle requires deliberate deletion."""
    path = Path(path)
    if path.is_symlink() or path.exists():
        raise RebootAcceptanceError("existing_baseline_must_be_reviewed_first")
    if snapshot.get("schema") != SCHEMA or not BOOT_PATTERN.fullmatch(
            str(snapshot.get("boot_id", ""))):
        raise RebootAcceptanceError("invalid_baseline_snapshot")
    fd = None
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            json.dump(snapshot, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise RebootAcceptanceError("baseline_creation_failed") from None
    finally:
        if fd is not None:
            os.close(fd)
    return True


def read_baseline(path):
    path = _restricted_file(path, os.geteuid(), size_limit=16384)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise RebootAcceptanceError("baseline_corrupt_or_unreadable") from None
    if (type(data) is not dict or data.get("schema") != SCHEMA
            or not BOOT_PATTERN.fullmatch(str(data.get("boot_id", "")))
            or type(data.get("state")) is not dict
            or set(data["state"].get("stores", {})) != {x[0] for x in STATE_STORES}):
        raise RebootAcceptanceError("baseline_invalid_schema")
    return data


def compare_reboot(before, after):
    if before.get("schema") != SCHEMA or after.get("schema") != SCHEMA:
        raise RebootAcceptanceError("snapshot_schema_mismatch")
    if before["boot_id"] == after["boot_id"]:
        raise RebootAcceptanceError("reboot_not_observed")
    old, new = before["state"], after["state"]
    if old["owner_config_sha256"] != new["owner_config_sha256"]:
        raise RebootAcceptanceError("owner_config_changed_across_reboot")
    total = 0
    for file, *_ in STATE_STORES:
        b, a = old["stores"][file], new["stores"][file]
        if (b["present"] != a["present"] or
                b["task_count"] != a["task_count"] or
                b["ids_sha256"] != a["ids_sha256"] or
                a["run_count"] < b["run_count"]):
            raise RebootAcceptanceError("durable_schedule_state_changed_or_lost")
        total += b["task_count"]
    return {
        "status": ("LIVE_PRIVATE_VPS_REBOOT_AND_SCHEDULE_PERSISTENCE_PASSED"
                   if total else "LIVE_PRIVATE_VPS_REBOOT_SERVICE_ONLY_PASSED"),
        "checks": {
            "different_linux_boot_id": True,
            "same_private_owner_configuration": True,
            "systemd_enabled_and_active_after_reboot": True,
            "loopback_and_auth_still_protected": True,
            "schedule_id_sets_preserved": True if total else None,
            "no_decrease_in_recorded_runs": True,
        },
        "task_count_preserved": total,
        "schedule_persistence_proven": bool(total),
        "independent_tencent_identity_verified": False,
        "reboot_was_triggered_by_this_program": False,
        "automatic_start_without_manual_intervention_proven": False,
        "actual_due_slot_executed_since_reboot_proven": False,
        "24_hour_uptime_verified": False,
        "limitations": "This host passed checks across distinct Linux boot IDs. Does not independently prove Tencent ownership, unattended automatic start, new due-slot execution, model readiness, n8n downstream completion or continuous 24h operation.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only two-phase private Linux VPS reboot/persistence acceptance; never reboots or restarts services")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--capture-before-reboot", action="store_true")
    action.add_argument("--verify-after-reboot", action="store_true")
    args = parser.parse_args(argv)
    try:
        current = live_snapshot()
        path = baseline_path()
        if args.capture_before_reboot:
            store_baseline(path, current)
            result = {
                "status": "PRIVATE_VPS_REBOOT_BASELINE_SAVED",
                "baseline_saved_private": True,
                "tasks_present": sum(
                    item["task_count"] for item in current["state"]["stores"].values()),
                "live_snapshot_passed": True,
                "reboot_triggered": False,
                "post_reboot_verified": False,
            }
            code = 0
        else:
            before = read_baseline(path)
            result = compare_reboot(before, current)
            code = 0 if result["schedule_persistence_proven"] else 3
    except (RebootAcceptanceError, OSError, ValueError, sqlite3.Error,
            KeyError, TypeError) as exc:
        result = {
            "status": "NOT_VERIFIED",
            "reason": str(exc) if isinstance(exc, RebootAcceptanceError)
                      else "live_reboot_acceptance_unavailable",
            "service_reboot_proven": False,
            "schedule_persistence_proven": False,
        }
        code = 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
