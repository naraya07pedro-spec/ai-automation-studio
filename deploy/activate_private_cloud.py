"""First-time Tencent/Linux private cloud-only activation, opt-in and fail-closed.

Run ON the actual systemd VPS as its nonroot service owner, from a reviewed
checkout. --inspect (default) is read-only. --apply asks for exact interactive
approval before provisioning private secrets or installing the systemd unit.

Never connects to Tencent's API, opens public HTTP, runs git pull, issues an
OpenAI request, changes an existing unit or upgrades user data.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
UNIT = "ai-automation-studio.service"
UNIT_FILE = Path("/etc/systemd/system") / UNIT
CREDENTIAL = Path("/etc/ai-automation-studio/aas-openai-api-key")
REQUIRED_ANCESTOR = "a788af547f6231d2a021e62414f2ba9ed838f73b"
CONFIRMATION = "ACTIVATE_PRIVATE_CLOUD"


def _quiet(args: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              check=False, timeout=6)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _is_port_free() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 8765))
        return True
    except OSError:
        return False


def _owner_status(home: Path, uid: int) -> tuple[bool, bool]:
    root = home / ".ai-automation-studio"
    owner = root / "operator.json"
    if root.is_symlink() or owner.is_symlink():
        return False, False
    if not owner.exists():
        return (not root.exists()
                or (root.is_dir() and root.stat().st_uid == uid
                    and stat.S_IMODE(root.stat().st_mode) & 0o077 == 0)), True
    try:
        valid = (root.is_dir() and root.stat().st_uid == uid
                 and stat.S_IMODE(root.stat().st_mode) & 0o077 == 0
                 and owner.is_file() and owner.stat().st_uid == uid
                 and stat.S_IMODE(owner.stat().st_mode) & 0o077 == 0)
    except OSError:
        valid = False
    return valid, False


def _trusted_python_for_sudo() -> bool:
    """Require a root-owned, not group/world-writable interpreter for sudo."""
    try:
        configured = Path(sys.executable)
        resolved = configured.resolve(strict=True)
        return (
            configured.is_absolute()
            and resolved.is_file()
            and resolved.stat().st_uid == 0
            and resolved.stat().st_mode & 0o022 == 0
            and configured.parent.stat().st_uid == 0
            and configured.parent.stat().st_mode & 0o022 == 0
        )
    except (OSError, ValueError):
        return False


def inspect() -> dict:
    """No reads of provider key bytes, credential directory or owner password."""
    uid = os.geteuid() if hasattr(os, "geteuid") else -1
    checks = {
        "linux_systemd": sys.platform == "linux"
                         and Path("/run/systemd/system").is_dir(),
        "nonroot_service_user": uid > 0,
        "python_3_11_plus": sys.version_info >= (3, 11),
        "system_python_safe_for_sudo": _trusted_python_for_sudo(),
        "checkout_owned_and_not_symlink": False,
        "reviewed_main_checkout_clean": False,
        "includes_stage_109": False,
        "correct_user_home": False,
        "owner_storage_safe_or_uninitialized": False,
        "no_existing_service": False,
        "private_port_8765_unused": _is_port_free(),
        "sudo_installed": shutil.which("sudo") is not None,
    }
    needs_owner = False
    user = None
    if checks["nonroot_service_user"]:
        try:
            account = pwd.getpwuid(uid)
            user = account.pw_name
            home = Path(account.pw_dir)
            checks["correct_user_home"] = (
                home.is_absolute() and not home.is_symlink()
                and home == Path.home()
            )
            if checks["correct_user_home"]:
                good, needs_owner = _owner_status(home, uid)
                checks["owner_storage_safe_or_uninitialized"] = good
        except (KeyError, OSError, ValueError):
            pass
    try:
        checks["checkout_owned_and_not_symlink"] = (
            ROOT.is_dir() and ROOT.stat().st_uid == uid
            and not ROOT.is_symlink() and (ROOT / ".git").exists()
            and (ROOT / "app" / "local_server.py").is_file()
        )
    except OSError:
        pass
    if checks["checkout_owned_and_not_symlink"]:
        status = _quiet(["git", "-C", str(ROOT), "status", "--porcelain"])
        branch = _quiet(["git", "-C", str(ROOT), "branch", "--show-current"])
        ancestor = _quiet(["git", "-C", str(ROOT), "merge-base", "--is-ancestor",
                           REQUIRED_ANCESTOR, "HEAD"])
        checks["reviewed_main_checkout_clean"] = (
            status is not None and status.returncode == 0
            and not status.stdout.strip()
            and branch is not None and branch.returncode == 0
            and branch.stdout.strip() == "main"
        )
        checks["includes_stage_109"] = (
            ancestor is not None and ancestor.returncode == 0
        )
    if checks["linux_systemd"]:
        unit = _quiet(["systemctl", "show", UNIT,
                       "--property=LoadState", "--value"])
        checks["no_existing_service"] = (
            unit is not None and unit.returncode == 0
            and unit.stdout.strip() == "not-found"
            and not UNIT_FILE.exists() and not UNIT_FILE.is_symlink()
        )
    return {
        "status": ("READY_TO_APPLY_WITH_OWNER_CONSENT"
                   if all(checks.values()) else "BLOCKED"),
        "mode": "INSPECT_ONLY",
        "checks": checks,
        "owner_initialization_needed": needs_owner,
        "user": user,  # internal only, never printed by the CLI
        "limitations": (
            "No Tencent instance identity, API key validity, API charge, "
            "provider response, reboot or 24-hour uptime verified."
        ),
    }


def _safe_public_report(report: dict) -> dict:
    return {key: val for key, val in report.items() if key != "user"}


def _run_interactive(args: list[str]) -> bool:
    """Child inherits TTY; secrets are accepted only by existing hidden prompts."""
    try:
        return subprocess.run(args, check=False).returncode == 0
    except OSError:
        return False


def apply(*, confirm: str | None = None) -> dict:
    """No changes unless all checks pass AND explicit owner confirmation is given."""
    prior = inspect()
    if prior["status"] != "READY_TO_APPLY_WITH_OWNER_CONSENT":
        return {"status": "BLOCKED", "checks": prior["checks"]}
    if confirm != CONFIRMATION:
        return {"status": "OWNER_CONFIRMATION_REQUIRED", "mutations": 0}
    user = prior["user"]
    if not user:
        return {"status": "BLOCKED", "reason": "missing_service_user"}
    py = sys.executable
    if prior["owner_initialization_needed"]:
        if not _run_interactive([py, str(ROOT / "app/local_server.py"),
                                 "--init-owner"]):
            return {"status": "BLOCKED", "reason": "owner_setup_failed"}
    home = Path(pwd.getpwnam(user).pw_dir)
    good, still_needed = _owner_status(home, os.geteuid())
    if not good or still_needed:
        return {"status": "BLOCKED", "reason": "owner_not_provisioned_safely"}

    # Recheck critical host boundaries before privileged actions.
    if not _is_port_free() or UNIT_FILE.exists() or UNIT_FILE.is_symlink():
        return {"status": "BLOCKED", "reason": "service_or_port_changed"}
    if not _run_interactive(["sudo", "-v"]):
        return {"status": "BLOCKED", "reason": "sudo_authentication_failed"}
    existing = _quiet(["sudo", "-n", "test", "-f", str(CREDENTIAL)])
    if existing is None:
        return {"status": "BLOCKED", "reason": "credential_presence_unknown"}
    if existing.returncode == 1:
        if not _run_interactive(
            ["sudo", py, str(ROOT / "deploy/provision_private_vps_credential.py"),
             "--name", "openai-api-key"]
        ):
            return {"status": "BLOCKED", "reason": "credential_provision_failed"}
    elif existing.returncode != 0:
        return {"status": "BLOCKED", "reason": "credential_presence_unknown"}

    install = [
        "sudo", py, str(ROOT / "deploy/private_vps_service.py"),
        "--user", user, "--checkout", str(ROOT),
        "--mode", "cloud-only", "--enable-scheduled-openai",
    ]
    if not _run_interactive(install):
        return {"status": "BLOCKED", "reason": "systemd_preview_failed"}
    if not _is_port_free() or UNIT_FILE.exists() or UNIT_FILE.is_symlink():
        return {"status": "BLOCKED", "reason": "service_or_port_changed"}
    if not _run_interactive(install + ["--install"]):
        return {"status": "DEPLOYMENT_INCOMPLETE_REVIEW_SYSTEMD"}
    # Inspect the *running* service rather than trusting an installer print.
    sys.path.insert(0, str(ROOT / "deploy"))
    from verify_private_vps_runtime import probe
    from verify_private_cloud_ai_runtime import _check_cloud_process
    # systemctl is-active can precede the HTTP listener on a small VPS.
    # Bounded observation only; never restarts or mutates a running service.
    base, effective, passed = {}, {}, False
    for attempt in range(12):
        base = probe()
        effective = _check_cloud_process()
        passed = (base.get("status") == "LIVE_PRIVATE_VPS_SNAPSHOT_PASSED"
                  and all(effective.values()))
        if passed:
            break
        if attempt < 11:
            time.sleep(0.5)
    return {
        "status": ("PRIVATE_CLOUD_SERVER_BOOT_VERIFIED_NO_MODEL_REQUEST"
                   if passed else "DEPLOYMENT_INCOMPLETE_REVIEW_SYSTEMD"),
        "private_http_verified": (
            base.get("status") == "LIVE_PRIVATE_VPS_SNAPSHOT_PASSED"),
        "live_cloud_process_checks": effective,
        "paid_model_request_executed_by_this_tool": False,
        "vps_identity_verified": False,
        "openai_provider_account_verified": False,
        "continuous_24h_uptime_verified": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="First-time private cloud-only Linux activation; inspect by default."
    )
    parser.add_argument("--apply", action="store_true",
                        help="Opt in to owner init, hidden key provision, and new unit install.")
    args = parser.parse_args(argv)
    if not args.apply:
        result = inspect()
        print(json.dumps(_safe_public_report(result), sort_keys=True))
        return 0 if result["status"] == "READY_TO_APPLY_WITH_OWNER_CONSENT" else 2
    print("This installs a NEW, localhost-only systemd cloud-only service. "
          "It can provision a hidden paid API key but NEVER invokes the model; "
          "a separately owner-approved UI task is required for any charges.")
    print("Do not run unreviewed repository code with sudo. Existing systemd "
          "units are never overwritten.")
    try:
        typed = input("Type " + CONFIRMATION + " to continue: ").strip()
    except (EOFError, KeyboardInterrupt):
        return 2
    report = apply(confirm=typed)
    print(json.dumps(_safe_public_report(report), sort_keys=True))
    return 0 if report["status"] == "PRIVATE_CLOUD_SERVER_BOOT_VERIFIED_NO_MODEL_REQUEST" else 2


if __name__ == "__main__":
    raise SystemExit(main())
