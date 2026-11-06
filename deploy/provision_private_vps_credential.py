"""One-time root-only provisioning for fixed systemd LoadCredential sources.

Never accept secrets as command-line arguments or print them. Refuse overwrite
to prevent silently rotating a live service's key / outbound destination.
"""
import argparse
import getpass
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_connector_vault import decode_master_key, VaultError

CREDENTIALS_DIR = Path("/etc/ai-automation-studio")
NAMES = {
    "vault-key": "aas-vault-key",
    "webhook-url": "aas-webhook-url",
    "webhook-signing-secret": "aas-webhook-signing-secret",
}


class ProvisionError(RuntimeError):
    pass


def _validate(name, value):
    if name not in NAMES:
        raise ProvisionError("unsupported_credential_name")
    if type(value) is not str or not 1 <= len(value.encode("utf-8")) <= 8191:
        raise ProvisionError("invalid_credential_size")
    if any(x in value for x in ("\x00", "\r", "\n")):
        raise ProvisionError("invalid_credential_line")
    if name == "vault-key":
        try:
            decode_master_key(value)
        except VaultError:
            raise ProvisionError("invalid_vault_master_key") from None
    if name == "webhook-url":
        if not value.startswith("https://") or not 12 <= len(value) <= 420:
            raise ProvisionError("invalid_https_webhook_url")
    if name == "webhook-signing-secret":
        if len(value) > 128 or any(ord(x) < 33 or ord(x) > 126 for x in value):
            raise ProvisionError("invalid_webhook_signing_secret")


def provision(name, value, *, directory=CREDENTIALS_DIR):
    """For real use, only root can write and source directory must be root-only."""
    if os.geteuid() != 0:
        raise ProvisionError("root_required")
    _validate(name, value)
    folder = Path(directory)
    if folder.is_symlink():
        raise ProvisionError("credential_directory_symlink_forbidden")
    folder.mkdir(mode=0o700, parents=False, exist_ok=True)
    if (folder.stat().st_uid != 0 or
            stat.S_IMODE(folder.stat().st_mode) != 0o700):
        raise ProvisionError("credential_directory_must_be_root_0700")
    path = folder / NAMES[name]
    if path.is_symlink() or path.exists():
        raise ProvisionError("credential_exists_no_automatic_overwrite")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(value.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, 0o600)
        fdir = os.open(str(folder), os.O_RDONLY)
        try:
            os.fsync(fdir)
        finally:
            os.close(fdir)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path.name


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Privately provision one root-only systemd credential without shell history.")
    parser.add_argument("--name", choices=tuple(NAMES), required=True)
    args = parser.parse_args(argv)
    try:
        secret = getpass.getpass(
            "Enter " + args.name + " privately (not echoed): ")
        result = provision(args.name, secret)
        print("PRIVATE_CREDENTIAL_STORED: " + result +
              " (root-owned 0600, no secret printed)")
        return 0
    except (ProvisionError, OSError, UnicodeError) as exc:
        print("CREDENTIAL_PROVISION_BLOCKED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
