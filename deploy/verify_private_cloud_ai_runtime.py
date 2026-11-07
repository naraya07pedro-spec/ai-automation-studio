"""Read-only on-VPS acceptance for the *running* private cloud-only AI worker.

Does not call OpenAI, restart services, create tasks, read task prompts into
output, or claim that a persisted receipt independently proves provider billing.
Run AS THE NON-ROOT SERVICE USER on the real Linux VPS, after creating exactly
one short paid-consent task from the authenticated Mission Control UI.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from verify_private_vps_runtime import probe as private_runtime_probe

UNIT = "ai-automation-studio.service"
CREDENTIAL_DIRECTIVE = (
    "LoadCredential=aas-openai-api-key:"
    "/etc/ai-automation-studio/aas-openai-api-key"
)
EXPECTED_FLAGS = frozenset(("--enable-scheduled-openai",))
FORBIDDEN_FLAGS = frozenset((
    "--enable-local-agent", "--enable-scheduled-local-ai",
    "--enable-managed-autopilot", "--enable-outbound-webhook",
))


def _systemctl(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["systemctl", *args], capture_output=True, text=True,
            timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _check_cloud_process() -> dict[str, bool]:
    """Inspect effective live MainPID, not just a potentially stale unit file."""
    checks = {
        "nonroot_service_identity": False,
        "live_cloud_only_server_process": False,
        "root_owned_credential_declared_in_unit": False,
    }
    if sys.platform != "linux" or os.geteuid() == 0:
        return checks
    unit = _systemctl("cat", UNIT)
    pid_text = _systemctl("show", UNIT, "--property=MainPID", "--value")
    user_text = _systemctl("show", UNIT, "--property=User", "--value")
    if unit is None or pid_text is None or user_text is None:
        return checks
    user_text = user_text.strip()
    try:
        import pwd
        service_uid = pwd.getpwnam(user_text).pw_uid
        pid = int(pid_text.strip())
        proc = Path("/proc") / str(pid)
        proc_uid = proc.stat().st_uid
        argv = [x.decode("utf-8") for x in
                (proc / "cmdline").read_bytes().split(b"\0") if x]
    except (OSError, ValueError, UnicodeError, KeyError, OverflowError):
        return checks
    checks["nonroot_service_identity"] = (
        service_uid != 0 and service_uid == os.geteuid()
        and proc_uid == service_uid and pid > 1
    )
    args = set(argv)
    checks["live_cloud_only_server_process"] = (
        checks["nonroot_service_identity"]
        and any(part.endswith("/app/local_server.py") for part in argv)
        and EXPECTED_FLAGS.issubset(args)
        and not FORBIDDEN_FLAGS.intersection(args)
    )
    # The unit is inspected but the key file itself is NEVER opened or printed.
    # The running service with this LoadCredential directive depends on systemd
    # provisioning; this is not proof of provider account eligibility or billing.
    normalized_lines = {line.strip() for line in unit.splitlines()}
    checks["root_owned_credential_declared_in_unit"] = (
        CREDENTIAL_DIRECTIVE in normalized_lines
        and not any(line.startswith("Environment=AAS_OPENAI_API_KEY=")
                    for line in normalized_lines)
    )
    return checks


def _read_receipt(base: Path) -> dict | None:
    """One committed, bounded cloud result from the owner's *live* SQLite DB.

    It is read-only and redacted. A forged/stubbed DB could still contain the
    same fields, so no provider/billing assertion can derive from this alone.
    """
    if base.is_symlink() or not base.is_dir():
        return None
    try:
        owner = base.stat()
        if (os.name == "posix" and
                (owner.st_uid != os.geteuid() or owner.st_mode & 0o077)):
            return None
        db_path = base / "readonly-ai-schedules.sqlite3"
        if db_path.is_symlink() or not db_path.is_file():
            return None
        if os.name == "posix" and db_path.stat().st_uid != os.geteuid():
            return None
        # No schema migrations or normal writable connection.
        with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
            rows = db.execute(
                """SELECT s.task_id,s.max_runs,s.attempted_runs,
                          r.answer,r.tools_json,r.usage_json,r.finished_utc
                   FROM ai_schedule_runs r
                   JOIN ai_schedules s ON s.task_id=r.task_id
                   WHERE s.provider='openai_responses'
                     AND r.status='COMPLETED'
                   ORDER BY r.finished_utc DESC LIMIT 50"""
            ).fetchall()
    except (OSError, sqlite3.Error):
        return None

    for task_id, max_runs, attempts, answer, raw_tools, raw_usage, finished in rows:
        try:
            usage = json.loads(raw_usage)
            tools = json.loads(raw_tools)
        except (TypeError, ValueError):
            continue
        if (type(task_id) is not str or type(max_runs) is not int
                or max_runs != 1 or type(attempts) is not int or attempts != 1
                or type(answer) is not str or not 1 <= len(answer.strip()) <= 6000
                or tools != [] or type(usage) is not dict
                or set(usage) != {"input_tokens", "output_tokens"}
                or any(type(usage[k]) is not int or not 0 < usage[k] <= 200000
                       for k in ("input_tokens", "output_tokens"))
                or type(finished) is not str or not finished):
            continue
        return {
            "task_fingerprint": hashlib.sha256(task_id.encode()).hexdigest()[:16],
            "provider_label_in_local_db": "openai_responses",
            "finished_utc": finished,
            "usage_tokens_reported_by_local_adapter": usage,
            "max_runs": 1,
            "attempted_runs": 1,
            "external_dispatch_from_model_tools": 0,
        }
    return None


def assess() -> dict:
    checks: dict[str, bool] = {}
    # This verifier itself may not run on CI or a Windows host as live evidence.
    if sys.platform == "linux" and os.geteuid() != 0:
        snapshot = private_runtime_probe()
        checks["private_vps_runtime_snapshot"] = (
            snapshot.get("status") == "LIVE_PRIVATE_VPS_SNAPSHOT_PASSED"
        )
        checks.update(_check_cloud_process())
        receipt = _read_receipt(Path.home() / ".ai-automation-studio")
    else:
        checks["private_vps_runtime_snapshot"] = False
        checks.update({
            "nonroot_service_identity": False,
            "live_cloud_only_server_process": False,
            "root_owned_credential_declared_in_unit": False,
        })
        receipt = None
    checks["bounded_remote_receipt_in_owner_sqlite"] = receipt is not None
    passed = all(checks.values())
    return {
        "status": ("CLOUD_RUNTIME_RECEIPT_OBSERVED_REVIEW_REQUIRED"
                   if passed else "NOT_VERIFIED"),
        "checks": checks,
        "redacted_receipt": receipt if passed else None,
        "provider_account_and_billing_verified": False,
        "tencent_instance_identity_verified": False,
        "continuous_24h_availability_verified": False,
        "limitations": (
            "Read-only, point-in-time systemd/private-HTTP/SQLite observation. "
            "Local receipts can be simulated or forged. Independently reconcile "
            "the actual Responses request and token usage in the provider console; "
            "verify Tencent instance identity, reboot and availability separately. "
            "This verifier performs zero paid API calls and zero task mutations."
        ),
    }


def main() -> int:
    report = assess()
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "CLOUD_RUNTIME_RECEIPT_OBSERVED_REVIEW_REQUIRED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
