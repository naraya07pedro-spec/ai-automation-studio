"""Read only narrowly allowlisted systemd LoadCredential files on private VPS.

Normal Windows / local usage keeps its existing environment variable interface.
Never use EnvironmentFile or put production secrets directly into a systemd unit.
No arbitrary filename or credential access is exposed to an HTTP/agent tool.
"""
import os
from pathlib import Path
import stat

CREDENTIAL_NAMES = {
    "AAS_VAULT_MASTER_KEY": "aas-vault-key",
    "AAS_WEBHOOK_URL": "aas-webhook-url",
    "AAS_WEBHOOK_SIGNING_SECRET": "aas-webhook-signing-secret",
}
MAX_SECRET_BYTES = 8192


class ServiceCredentialError(RuntimeError):
    pass


def read_runtime_secret(env_name):
    """Read a fixed env value OR a systemd private credential, never both.

    The systemd manager copies root-protected sources to per-unit runtime
    credentials. Only already-allowlisted names are accepted. No files are
    opened when CREDENTIALS_DIRECTORY is unset (regular Windows behavior).
    """
    if env_name not in CREDENTIAL_NAMES:
        raise ServiceCredentialError("unknown_runtime_secret")
    raw_env = os.environ.get(env_name)
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if directory is None:
        return raw_env or ""
    base = Path(directory)
    if not base.is_absolute() or ".." in base.parts:
        raise ServiceCredentialError("invalid_systemd_credential_directory")
    source = base / CREDENTIAL_NAMES[env_name]
    if raw_env is not None and source.exists():
        raise ServiceCredentialError("ambiguous_env_and_systemd_secret")
    if not source.exists():
        return raw_env or ""
    if source.is_symlink():
        raise ServiceCredentialError("systemd_credential_symlink_refused")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(source), flags)
    except OSError:
        raise ServiceCredentialError("systemd_credential_unreadable") from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode)
                or info.st_size > MAX_SECRET_BYTES
                or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_uid not in (os.getuid(), 0)):
            raise ServiceCredentialError("systemd_credential_unsafe_permissions")
        raw = os.read(fd, MAX_SECRET_BYTES + 1)
        if len(raw) > MAX_SECRET_BYTES:
            raise ServiceCredentialError("systemd_credential_too_large")
        # A terminal printf or here-string often leaves a single final newline.
        value = raw.decode("utf-8").removesuffix("\n")
        if "\n" in value or "\r" in value or "\x00" in value:
            raise ServiceCredentialError("systemd_credential_invalid_text")
        return value
    except UnicodeError:
        raise ServiceCredentialError("systemd_credential_invalid_text") from None
    finally:
        os.close(fd)
