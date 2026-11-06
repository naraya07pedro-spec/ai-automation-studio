"""Private, always-on Linux systemd deployment for the existing LOOPBACK server.

This is NOT a public-HTTP cloud conversion. Access requires an SSH tunnel.
No model is installed; no external write tool or unattended workflow is added.
"""
import argparse
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from launch_workbench import feature_flags

UNIT_NAME = "ai-automation-studio.service"
UNIT_FILE = Path("/etc/systemd/system") / UNIT_NAME
_ALLOWED_PATH = re.compile(r"/[A-Za-z0-9_./-]+", re.ASCII)
_ALLOWED_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}", re.ASCII)


class PrivateVPSDeploymentError(ValueError):
    pass


def _path(value):
    path = str(value)
    if not _ALLOWED_PATH.fullmatch(path) or ".." in Path(path).parts or "//" in path:
        raise PrivateVPSDeploymentError("unsafe_or_nonabsolute_deploy_path")
    return path


def render_unit(*, username, checkout, home, python, mode):
    """Render a fixed-name loopback service with no user-controlled directives."""
    if not _ALLOWED_USER.fullmatch(username) or username == "root":
        raise PrivateVPSDeploymentError("dedicated_nonroot_user_required")
    checkout, home, python = (_path(checkout), _path(home), _path(python))
    flags = feature_flags(mode)
    if "--enable-agent-history" in flags:
        raise PrivateVPSDeploymentError("plaintext_history_not_enabled_in_vps")
    arguments = " ".join(flags)
    return (
        "[Unit]\n"
        "Description=AI Automation Studio private SSH-tunnel workbench\n"
        "After=network.target\n"
        "StartLimitIntervalSec=60\n"
        "StartLimitBurst=5\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={username}\n"
        f"WorkingDirectory={checkout}\n"
        f"Environment=HOME={home}\n"
        "Environment=PYTHONDONTWRITEBYTECODE=1\n"
        f"ExecStart={python} {checkout}/app/local_server.py"
        + (f" {arguments}\n" if arguments else "\n")
        + "Restart=on-failure\n"
        "RestartSec=5s\n"
        "UMask=0077\n"
        "NoNewPrivileges=true\n"
        "PrivateTmp=true\n"
        "PrivateDevices=true\n"
        "ProtectSystem=strict\n"
        "ProtectKernelTunables=true\n"
        "ProtectKernelModules=true\n"
        "ProtectControlGroups=true\n"
        "RestrictSUIDSGID=true\n"
        "LockPersonality=true\n"
        "CapabilityBoundingSet=\n"
        "AmbientCapabilities=\n"
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n"
        f"ReadWritePaths={home}/.ai-automation-studio\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def validate_host(*, username, checkout):
    """Refuse deployment without a previously provisioned private owner."""
    if os.name != "posix" or not Path("/run/systemd/system").is_dir():
        raise PrivateVPSDeploymentError("linux_systemd_host_required")
    if sys.version_info < (3, 11):
        raise PrivateVPSDeploymentError("python_3_11_or_newer_required")
    if not _ALLOWED_USER.fullmatch(username) or username == "root":
        raise PrivateVPSDeploymentError("dedicated_nonroot_user_required")
    account = pwd.getpwnam(username)
    if account.pw_uid == 0:
        raise PrivateVPSDeploymentError("root_account_forbidden")
    home = Path(_path(account.pw_dir))
    tree = Path(_path(checkout))
    if tree.resolve(strict=True) != tree:
        raise PrivateVPSDeploymentError("checkout_must_not_be_symlink")
    if not tree.is_dir() or tree.stat().st_uid != account.pw_uid:
        raise PrivateVPSDeploymentError("checkout_must_be_owned_by_service_user")
    server = tree / "app" / "local_server.py"
    if not server.is_file() or server.is_symlink():
        raise PrivateVPSDeploymentError("server_file_missing_or_symlink")
    owner = home / ".ai-automation-studio" / "operator.json"
    if owner.is_symlink() or not owner.is_file():
        raise PrivateVPSDeploymentError("first_provision_private_operator_for_service_user")
    if not stat.S_ISREG(owner.stat().st_mode) or owner.stat().st_uid != account.pw_uid:
        raise PrivateVPSDeploymentError("operator_config_owner_invalid")
    if stat.S_IMODE(owner.stat().st_mode) & 0o077:
        raise PrivateVPSDeploymentError("operator_config_permissions_too_broad")
    return str(home)


def install_unit(text):
    """Explicit privileged action. Never overwrite an existing unit."""
    if os.geteuid() != 0:
        raise PrivateVPSDeploymentError("sudo_required_to_install_system_service")
    if UNIT_FILE.exists() or UNIT_FILE.is_symlink():
        raise PrivateVPSDeploymentError("existing_service_unit_not_overwritten")
    fd = os.open(str(UNIT_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        UNIT_FILE.unlink(missing_ok=True)
        raise
    # The unit is left in place if systemctl fails, for explicit inspection.
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", UNIT_NAME], check=True)
    subprocess.run(["systemctl", "is-active", "--quiet", UNIT_NAME], check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install SSH-only always-on VPS service.")
    parser.add_argument("--user", required=True,
                        help="Existing non-root Linux user who owns the checkout and operator.json")
    parser.add_argument("--checkout", required=True,
                        help="Absolute directory of the existing Git checkout")
    parser.add_argument("--mode", choices=("offline", "local-ai", "full"), default="offline")
    parser.add_argument("--install", action="store_true",
                        help="Explicit opt-in: write systemd unit, enable and start the service as root")
    args = parser.parse_args(argv)
    try:
        home = validate_host(username=args.user, checkout=args.checkout)
        unit = render_unit(username=args.user, checkout=args.checkout,
                           home=home, python=sys.executable, mode=args.mode)
        if args.install:
            install_unit(unit)
            print("SYSTEMD_ACTIVE: " + UNIT_NAME + "; bind=127.0.0.1:8765")
            print("Remote access must use an SSH tunnel; no public web URL is created.")
        else:
            print(unit)
            print("PREVIEW_ONLY: no files, services, passwords or firewall settings changed.")
    except (PrivateVPSDeploymentError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        print("DEPLOYMENT_BLOCKED: " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
