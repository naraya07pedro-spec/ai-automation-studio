"""Optional encrypted owner connector registry using PyCA AES-256-GCM.

Requires an independent 32-byte base64url master key in AAS_VAULT_MASTER_KEY.
Never generate/store the master key beside ciphertext, print it or silently
downgrade to plaintext. Authenticated encryption detects tampering/wrong key.
Not an OAuth credential provider, HSM/KMS or defense against compromised OS.
"""
import base64
import binascii
import json
import os
from pathlib import Path
import secrets
import stat

MAX_VAULT_FILE_BYTES = 16384
MAX_PLAINTEXT_BYTES = 10000
AAD = b"AI-AUTOMATION-STUDIO:MANAGED-WEBHOOK-VAULT:V1"


class VaultError(RuntimeError):
    pass


def decode_master_key(value):
    if type(value) is not str or len(value) != 44 or not value.isascii():
        raise VaultError("vault_master_key_must_be_base64url_32_bytes")
    try:
        key = base64.b64decode(value, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        raise VaultError("vault_master_key_must_be_base64url_32_bytes") from None
    if len(key) != 32 or base64.urlsafe_b64encode(key).decode("ascii") != value:
        raise VaultError("vault_master_key_must_be_base64url_32_bytes")
    return key


def generate_master_key():
    """For CLI/offline usage only; NEVER called automatically on startup."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


class EncryptedConnectorVault:
    def __init__(self, filename, master_key, *, aad=AAD):
        # If the dependency or key is absent, fail explicitly before touching disk.
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            from cryptography.exceptions import InvalidTag
        except ImportError:
            raise VaultError("vault_dependency_missing_install_requirements_vault") from None
        if type(aad) is not bytes or not aad or len(aad) > 256:
            raise VaultError("invalid_vault_domain")
        self._aad = aad
        self._aead = AESGCM(decode_master_key(master_key))
        self._invalid_tag = InvalidTag
        self.path = Path(filename).expanduser()
        self._verify_path()
        if not self.path.exists():
            self.write({"version":1,"entries":[]})

    def _verify_path(self):
        p = self.path
        if p.is_symlink() or (p.exists() and not p.is_file()):
            raise VaultError("vault_unsafe_path")
        if p.parent.is_symlink():
            raise VaultError("vault_unsafe_directory")
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            os.chmod(p.parent, 0o700)
            if p.exists() and stat.S_IMODE(p.stat().st_mode) & 0o077:
                raise VaultError("vault_insecure_file_permissions")

    def _sealed(self, data):
        raw = json.dumps(data, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=True).encode("ascii")
        if len(raw) > MAX_PLAINTEXT_BYTES:
            raise VaultError("vault_payload_too_large")
        nonce = os.urandom(12)
        ciphertext = self._aead.encrypt(nonce, raw, self._aad)
        return {"version":2,"algorithm":"AES-256-GCM",
                "nonce":base64.b64encode(nonce).decode("ascii"),
                "ciphertext":base64.b64encode(ciphertext).decode("ascii")}

    def read(self):
        self._verify_path()
        try:
            if self.path.stat().st_size > MAX_VAULT_FILE_BYTES:
                raise ValueError("file too large")
            envelope = json.loads(self.path.read_text(encoding="ascii"))
            if (type(envelope) is not dict or
                    set(envelope) != {"version","algorithm","nonce","ciphertext"} or
                    envelope["version"] != 2 or
                    envelope["algorithm"] != "AES-256-GCM"):
                raise ValueError("not encrypted")
            nonce = base64.b64decode(envelope["nonce"], validate=True)
            encrypted = base64.b64decode(envelope["ciphertext"], validate=True)
            if len(nonce) != 12 or not 16 <= len(encrypted) <= MAX_VAULT_FILE_BYTES:
                raise ValueError("bad envelope")
            raw = self._aead.decrypt(nonce, encrypted, self._aad)
            if len(raw) > MAX_PLAINTEXT_BYTES:
                raise ValueError("oversized decrypted body")
            return json.loads(raw)
        except (OSError, ValueError, TypeError, UnicodeError, KeyError,
                binascii.Error, self._invalid_tag):
            # No ciphertext / plaintext / filesystem details in API error.
            raise VaultError("vault_unlock_failed_wrong_key_or_tampered") from None

    def write(self, data):
        self._verify_path()
        sealed = self._sealed(data)
        raw = json.dumps(sealed, sort_keys=True, separators=(",", ":")).encode("ascii")
        if len(raw) > MAX_VAULT_FILE_BYTES:
            raise VaultError("vault_payload_too_large")
        p = self.path
        tmp = p.parent / ("."+p.name+"."+secrets.token_hex(12)+".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if os.name == "posix":
                os.chmod(tmp, 0o600)
            if p.is_symlink():
                raise VaultError("vault_unsafe_path")
            os.replace(tmp,p)
            if os.name == "posix":
                folder = os.open(str(p.parent), os.O_RDONLY)
                try:
                    os.fsync(folder)
                finally:
                    os.close(folder)
        finally:
            if tmp.exists():
                tmp.unlink()
