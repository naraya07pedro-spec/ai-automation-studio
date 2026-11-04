"""Single-operator localhost access gate. Not multi-user or public authentication.

Owner password is provisioned at the command line, never through an open
HTTP registration endpoint. scrypt verifier is stored with restrictive
file permissions; sessions & CSRF tokens live only in process memory.

Threat boundary: prevents unauthenticated browser/API access over loopback.
Does NOT defend against a compromised OS account, privileged local process,
malware, or plaintext loopback traffic interception. No PII/customer use.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import threading
import time

FILE_VERSION = 1
MIN_PASSWORD = 12
MAX_PASSWORD = 128
SESSION_TTL_SECONDS = 3600
MAX_SESSIONS = 8
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60
_SCRYPT_N = 2 ** 14


class OperatorConfigurationError(RuntimeError):
    pass


class LocalOperatorAuth:
    def __init__(self, salt, password_hash, clock=None):
        if type(salt) is not bytes or len(salt) != 16:
            raise OperatorConfigurationError("invalid_owner_config")
        if type(password_hash) is not bytes or len(password_hash) != 32:
            raise OperatorConfigurationError("invalid_owner_config")
        self._salt = salt
        self._hash = password_hash
        self._clock = clock if clock is not None else time.monotonic
        self._lock = threading.RLock()
        self._sessions = {}
        self._failures = 0
        self._locked_until = 0

    @staticmethod
    def _validate_password(password):
        return (type(password) is str and MIN_PASSWORD <= len(password) <= MAX_PASSWORD
                and len(password.encode("utf-8")) <= 512
                and not any(ord(char) < 32 or ord(char) == 127 for char in password))

    @staticmethod
    def _derive(password, salt):
        return hashlib.scrypt(password.encode("utf-8"), salt=salt,
                              n=_SCRYPT_N, r=8, p=1, dklen=32)

    @classmethod
    def ephemeral_for_tests(cls, password, clock=None):
        """Only explicit test fixture construction; never a server fallback."""
        if not cls._validate_password(password):
            raise ValueError("password_policy_rejected")
        salt = secrets.token_bytes(16)
        return cls(salt, cls._derive(password, salt), clock=clock)

    @classmethod
    def provision(cls, filename, password):
        """One-time offline owner provisioning, O_EXCL and 0600 on POSIX."""
        if not cls._validate_password(password):
            raise ValueError("password_policy_rejected")
        path = Path(filename).expanduser()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(path.parent, 0o700)
        salt = secrets.token_bytes(16)
        record = json.dumps({
            "version": FILE_VERSION,
            "salt": salt.hex(),
            "scrypt_hash": cls._derive(password, salt).hex(),
        }, separators=(",", ":")).encode("ascii")
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(record)
                stream.flush()
                os.fsync(stream.fileno())
            if os.name == "posix":
                os.chmod(path, 0o600)
        except BaseException:
            try:
                path.unlink()
            except OSError:
                pass
            raise
        return cls(salt, bytes.fromhex(json.loads(record)["scrypt_hash"]))

    @classmethod
    def load(cls, filename, clock=None):
        path = Path(filename).expanduser()
        if not path.is_file() or path.is_symlink():
            raise OperatorConfigurationError("owner_not_configured")
        if os.name == "posix":
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                raise OperatorConfigurationError("insecure_owner_file_permissions")
        try:
            raw = path.read_bytes()
            if len(raw) > 512:
                raise ValueError("oversized")
            record = json.loads(raw)
            if (type(record) is not dict or set(record) != {
                "version", "salt", "scrypt_hash"} or record["version"] != FILE_VERSION
                    or type(record["salt"]) is not str
                    or type(record["scrypt_hash"]) is not str):
                raise ValueError("schema")
            salt = bytes.fromhex(record["salt"])
            digest = bytes.fromhex(record["scrypt_hash"])
            return cls(salt, digest, clock=clock)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise OperatorConfigurationError("invalid_owner_config") from exc

    @staticmethod
    def _session_key(token):
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def login(self, password):
        if type(password) is not str or len(password) > MAX_PASSWORD:
            return 401, None
        with self._lock:
            now = self._clock()
            if now < self._locked_until:
                return 429, None
            matched = hmac.compare_digest(self._derive(password, self._salt), self._hash)
            if not matched:
                self._failures += 1
                if self._failures >= MAX_FAILED_ATTEMPTS:
                    self._locked_until = now + LOCKOUT_SECONDS
                    self._failures = 0
                return 401, None
            self._failures = 0
            self._locked_until = 0
            token = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(32)
            self._prune(now)
            while len(self._sessions) >= MAX_SESSIONS:
                del self._sessions[next(iter(self._sessions))]
            self._sessions[self._session_key(token)] = {
                "csrf": csrf, "expires": now + SESSION_TTL_SECONDS}
            return 200, {"cookie_token": token,
                         "csrf_token": csrf, "expires_in": SESSION_TTL_SECONDS}

    def _prune(self, now):
        for key, entry in list(self._sessions.items()):
            if entry["expires"] <= now:
                del self._sessions[key]

    def _lookup(self, token):
        if type(token) is not str or len(token) != 43:
            return None
        try:
            key = self._session_key(token)
        except UnicodeEncodeError:
            return None
        with self._lock:
            self._prune(self._clock())
            return self._sessions.get(key)

    def inspect(self, token):
        with self._lock:
            entry = self._lookup(token)
            if entry is None:
                return None
            return {"csrf_token": entry["csrf"],
                    "expires_in": max(0, int(entry["expires"] - self._clock()))}

    def authorize(self, token, csrf):
        with self._lock:
            entry = self._lookup(token)
            return (entry is not None and type(csrf) is str
                    and hmac.compare_digest(entry["csrf"], csrf))

    def logout(self, token):
        if type(token) is not str or len(token) != 43:
            return False
        try:
            key = self._session_key(token)
        except UnicodeEncodeError:
            return False
        with self._lock:
            return self._sessions.pop(key, None) is not None
