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
from local_connector_vault import decode_master_key, VaultError
from openai_readonly_responses import validate_remote_api_key, RemoteInferenceError

UNIT_NAME = "ai-automation-studio.service"
PRIVATE_CREDENTIAL_ROOT = Path("/etc/ai-automation-studio")
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


def render_unit(*, username, checkout, home, python, mode,
                encrypted_webhooks=False, gmail_metadata=False,
                outbound_webhook=False, signed_webhook=False,
                managed_autopilot=False, scheduled_local_ai=False,
                scheduled_openai=False):
    """Render a fixed-name loopback service with no user-controlled directives."""
    if not _ALLOWED_USER.fullmatch(username) or username == "root":
        raise PrivateVPSDeploymentError("dedicated_nonroot_user_required")
    checkout, home, python = (_path(checkout), _path(home), _path(python))
    flags = feature_flags(mode)
    if "--enable-agent-history" in flags:
        raise PrivateVPSDeploymentError("plaintext_history_not_enabled_in_vps")
    if signed_webhook and not outbound_webhook:
        raise PrivateVPSDeploymentError("signed_webhook_requires_outbound_webhook")
    if managed_autopilot and not encrypted_webhooks:
        raise PrivateVPSDeploymentError("managed_autopilot_requires_encrypted_webhooks")
    if scheduled_local_ai and mode not in ("local-ai", "full"):
        raise PrivateVPSDeploymentError("scheduled_local_ai_requires_local_ai_mode")
    if mode == "cloud-only" and not scheduled_openai:
        raise PrivateVPSDeploymentError("cloud_only_requires_scheduled_openai")
    if scheduled_openai and not scheduled_local_ai and mode != "cloud-only":
        raise PrivateVPSDeploymentError("scheduled_openai_requires_scheduled_ai")
    extra = ()
    credentials = ""
    if scheduled_local_ai:
        extra += ("--enable-scheduled-local-ai",)
    if scheduled_openai:
        extra += ("--enable-scheduled-openai",)
    if encrypted_webhooks:
        extra += ("--enable-encrypted-managed-webhooks",)
    if managed_autopilot:
        extra += ("--enable-managed-autopilot",)
    if gmail_metadata:
        extra += ("--enable-google-gmail-metadata",)
    if outbound_webhook:
        extra += ("--enable-outbound-webhook",)
    if scheduled_openai:
        credentials += "LoadCredential=aas-openai-api-key:/etc/ai-automation-studio/aas-openai-api-key\n"
    if encrypted_webhooks or gmail_metadata:
        credentials += "LoadCredential=aas-vault-key:/etc/ai-automation-studio/aas-vault-key\n"
    if outbound_webhook:
        credentials += "LoadCredential=aas-webhook-url:/etc/ai-automation-studio/aas-webhook-url\n"
    if signed_webhook:
        credentials += "LoadCredential=aas-webhook-signing-secret:/etc/ai-automation-studio/aas-webhook-signing-secret\n"
    arguments = " ".join((*flags, *extra))
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
        + credentials
        + f"ExecStart={python} {checkout}/app/local_server.py"
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


def validate_secret_sources(*, encrypted_webhooks=False, gmail_metadata=False,
                            outbound_webhook=False, signed_webhook=False,
                            directory=PRIVATE_CREDENTIAL_ROOT, expected_uid=0,
                            scheduled_openai=False):
    """Validate exact root-only sources before an explicit privileged install."""
    if signed_webhook and not outbound_webhook:
        raise PrivateVPSDeploymentError("signed_webhook_requires_outbound_webhook")
    expected = []
    if scheduled_openai:
        expected.append("aas-openai-api-key")
    if encrypted_webhooks or gmail_metadata:
        expected.append("aas-vault-key")
    if outbound_webhook:
        expected.append("aas-webhook-url")
    if signed_webhook:
        expected.append("aas-webhook-signing-secret")
    if not expected:
        return []
    folder = Path(directory)
    if (folder.is_symlink() or not folder.is_dir()
            or folder.stat().st_uid != expected_uid
            or stat.S_IMODE(folder.stat().st_mode) & 0o077):
        raise PrivateVPSDeploymentError("credential_directory_must_be_root_only")
    for name in expected:
        path = folder / name
        if (path.is_symlink() or not path.is_file()
                or path.stat().st_uid != expected_uid
                or stat.S_IMODE(path.stat().st_mode) & 0o077
                or not 1 <= path.stat().st_size <= 8192):
            raise PrivateVPSDeploymentError("required_root_0600_credential_missing_or_unsafe")
        if name == "aas-openai-api-key":
            try:
                validate_remote_api_key(path.read_text(encoding="ascii").removesuffix("\n"))
            except (RemoteInferenceError, UnicodeError):
                raise PrivateVPSDeploymentError("invalid_openai_api_key_credential") from None
        if name == "aas-vault-key":
            try:
                decode_master_key(path.read_text(encoding="ascii").removesuffix("\n"))
            except (VaultError, UnicodeError):
                raise PrivateVPSDeploymentError("invalid_vault_master_key_credential") from None
        if name == "aas-webhook-url":
            try:
                url = path.read_text(encoding="utf-8").removesuffix("\n")
            except UnicodeError:
                raise PrivateVPSDeploymentError("invalid_webhook_url_credential") from None
            if (not url.startswith("https://") or "\n" in url or "\r" in url
                    or len(url) > 420):
                raise PrivateVPSDeploymentError("invalid_webhook_url_credential")
    return expected


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
    parser.add_argument("--mode", choices=("offline", "local-ai", "full", "cloud-only"), default="offline")
    parser.add_argument("--enable-scheduled-openai", action="store_true",
                        help="Opt into billed owner-approved remote text inference; requires local scheduler + separate root-only API credential.")
    parser.add_argument("--enable-scheduled-local-ai", action="store_true",
                        help="Opt in to bounded read-only Ollama schedules in local-ai or full mode; not required by cloud-only.")
    parser.add_argument("--enable-managed-autopilot", action="store_true",
                        help="Allow bounded owner-armed scheduling for encrypted managed webhook targets.")
    parser.add_argument("--enable-encrypted-managed-webhooks", action="store_true",
                        help="Opt in to encrypted managed n8n webhook connections.")
    parser.add_argument("--enable-google-gmail-metadata", action="store_true",
                        help="Opt in to existing Gmail metadata-only OAuth connector.")
    parser.add_argument("--enable-webhook", action="store_true",
                        help="Opt in to existing outbound n8n webhook.")
    parser.add_argument("--enable-webhook-signing", action="store_true",
                        help="Opt in to root-owned HMAC signature credential.")
    parser.add_argument("--install", action="store_true",
                        help="Explicit opt-in: write systemd unit, enable and start the service as root")
    args = parser.parse_args(argv)
    try:
        home = validate_host(username=args.user, checkout=args.checkout)
        unit = render_unit(username=args.user, checkout=args.checkout,
                           home=home, python=sys.executable, mode=args.mode,
                           encrypted_webhooks=args.enable_encrypted_managed_webhooks,
                           gmail_metadata=args.enable_google_gmail_metadata,
                           outbound_webhook=args.enable_webhook,
                           signed_webhook=args.enable_webhook_signing,
                           managed_autopilot=args.enable_managed_autopilot,
                           scheduled_local_ai=args.enable_scheduled_local_ai,
                           scheduled_openai=args.enable_scheduled_openai)
        if args.install:
            validate_secret_sources(
                encrypted_webhooks=args.enable_encrypted_managed_webhooks,
                gmail_metadata=args.enable_google_gmail_metadata,
                outbound_webhook=args.enable_webhook,
                signed_webhook=args.enable_webhook_signing,
                scheduled_openai=args.enable_scheduled_openai)
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
