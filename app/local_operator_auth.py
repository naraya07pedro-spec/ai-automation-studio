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
RECOVERY_VERSION = 2
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
    def __init__(self, salt, password_hash, clock=None, owner_path=None,
                 recovery_salt=None, recovery_hash=None):
        if type(salt) is not bytes or len(salt) != 16:
            raise OperatorConfigurationError("invalid_owner_config")
        if type(password_hash) is not bytes or len(password_hash) != 32:
            raise OperatorConfigurationError("invalid_owner_config")
        self._salt = salt
        self._hash = password_hash
        self._owner_path = Path(owner_path).expanduser() if owner_path is not None else None
        if ((recovery_salt is None) != (recovery_hash is None)):
            raise OperatorConfigurationError("invalid_recovery_config")
        if recovery_salt is not None and (
                type(recovery_salt) is not bytes or len(recovery_salt) != 16
                or type(recovery_hash) is not bytes or len(recovery_hash) != 32):
            raise OperatorConfigurationError("invalid_recovery_config")
        self._recovery_salt = recovery_salt
        self._recovery_hash = recovery_hash
        self._clock = clock if clock is not None else time.monotonic
        self._lock = threading.RLock()
        self._sessions = {}
        self._failures = 0
        self._locked_until = 0
        self._requires_restart = False

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
            if type(record) is not dict or type(record.get("version")) is not int:
                raise ValueError("schema")
            version = record["version"]
            if version == FILE_VERSION and set(record) == {
                    "version", "salt", "scrypt_hash"}:
                recovery_salt, recovery_hash = None, None
            elif version == RECOVERY_VERSION and set(record) == {
                    "version", "salt", "scrypt_hash", "recovery_salt", "recovery_hash"}:
                if (type(record["recovery_salt"]) is not str
                        or type(record["recovery_hash"]) is not str):
                    raise ValueError("schema")
                recovery_salt = bytes.fromhex(record["recovery_salt"])
                recovery_hash = bytes.fromhex(record["recovery_hash"])
            else:
                raise ValueError("schema")
            if (type(record["salt"]) is not str
                    or type(record["scrypt_hash"]) is not str):
                raise ValueError("schema")
            salt = bytes.fromhex(record["salt"])
            digest = bytes.fromhex(record["scrypt_hash"])
            return cls(salt, digest, clock=clock, owner_path=path,
                       recovery_salt=recovery_salt, recovery_hash=recovery_hash)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise OperatorConfigurationError("invalid_owner_config") from exc

    @staticmethod
    def _session_key(token):
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def _confirm_locked(self, password):
        """Shared failure counter for login, source changes and rotation."""
        if self._requires_restart:
            return 503
        if type(password) is not str or len(password) > MAX_PASSWORD:
            return 401
        now = self._clock()
        if now < self._locked_until:
            return 429
        matched = hmac.compare_digest(self._derive(password, self._salt), self._hash)
        if not matched:
            self._failures += 1
            if self._failures >= MAX_FAILED_ATTEMPTS:
                self._locked_until = now + LOCKOUT_SECONDS
                self._failures = 0
            return 401
        self._failures = 0
        self._locked_until = 0
        return 200

    def confirm_current_password(self, password):
        with self._lock:
            return self._confirm_locked(password)

    def rotate_password(self, current_password, new_password):
        """Rotate verified owner password atomically and revoke every session.

        Caller already passed session+CSRF but must also reenter old password.
        A storage failure preserves old verifier and existing sessions.
        """
        if not self._validate_password(new_password):
            return 422, {"error": "invalid_new_operator_password"}
        if self._owner_path is None:
            return 503, {"error": "owner_file_unavailable"}
        with self._lock:
            confirmed = self._confirm_locked(current_password)
            if confirmed != 200:
                return confirmed, {"error": (
                    "operator_restart_required" if confirmed == 503
                    else "operator_login_locked" if confirmed == 429
                    else "invalid_operator_credentials")}
            if hmac.compare_digest(current_password, new_password):
                return 422, {"error": "operator_password_unchanged"}
            salt = secrets.token_bytes(16)
            digest = self._derive(new_password, salt)
            # Existing sessions must remain active if disk persistence fails.
            from local_source_policy import (
                secure_replace_json, PostReplaceDurabilityError)
            try:
                secure_replace_json(self._owner_path, {
                    "version": FILE_VERSION,
                    "salt": salt.hex(),
                    "scrypt_hash": digest.hex()})
            except PostReplaceDurabilityError:
                self._requires_restart = True
                self._sessions.clear()
                return 503, {"error": "operator_restart_required"}
            except (OSError, RuntimeError):
                return 503, {"error": "owner_rotation_storage_failed"}
            self._salt, self._hash = salt, digest
            # A password rotation also invalidates any previously printed
            # offline recovery code, avoiding indefinite older credentials.
            self._recovery_salt, self._recovery_hash = None, None
            self._sessions.clear()
            self._failures, self._locked_until = 0, 0
            return 200, {"status": "OWNER_PASSWORD_ROTATED_SESSIONS_REVOKED"}

    @staticmethod
    def _recovery_digest(code, salt):
        return hashlib.sha256(salt + code.encode("ascii")).digest()

    def setup_recovery(self, current_password):
        """Offline-only: issue 256-bit one-time recovery code; persist hash.

        Re-enrolling consumes the prior recovery code, never reveals it.
        CLI checks the production TCP socket is unbound before this call.
        """
        if self._owner_path is None:
            return 503, {"error": "owner_file_unavailable"}
        with self._lock:
            accepted = self._confirm_locked(current_password)
            if accepted != 200:
                return accepted, {"error": (
                    "operator_restart_required" if accepted == 503
                    else "operator_login_locked" if accepted == 429
                    else "invalid_operator_credentials")}
            code = secrets.token_urlsafe(32)
            rsalt = secrets.token_bytes(16)
            digest = self._recovery_digest(code, rsalt)
            from local_source_policy import (
                secure_replace_json, PostReplaceDurabilityError)
            try:
                secure_replace_json(self._owner_path, {
                    "version": RECOVERY_VERSION,
                    "salt": self._salt.hex(),
                    "scrypt_hash": self._hash.hex(),
                    "recovery_salt": rsalt.hex(),
                    "recovery_hash": digest.hex()})
            except PostReplaceDurabilityError:
                self._requires_restart = True
                self._sessions.clear()
                return 503, {"error": "operator_restart_required"}
            except (OSError, RuntimeError):
                return 503, {"error": "recovery_setup_storage_failed"}
            self._recovery_salt, self._recovery_hash = rsalt, digest
            # Existing HTTP login sessions cannot survive source verifier
            # replacement; the server must be stopped during CLI setup.
            self._sessions.clear()
            return 200, {"recovery_code": code,
                         "status": "OFFLINE_RECOVERY_CODE_ENABLED"}

    def recover_with_code(self, code, new_password):
        """Offline only: atomically consume recovery proof + reset password.

        Rewrites the owner config as v1 with no recovery code, so a code
        can never be replayed after a successful owner recovery.
        """
        if self._owner_path is None:
            return 503, {"error": "owner_file_unavailable"}
        if not self._validate_password(new_password):
            return 422, {"error": "invalid_new_operator_password"}
        if type(code) is not str or len(code) != 43 or not code.isascii():
            return 403, {"error": "invalid_recovery_proof"}
        with self._lock:
            if self._requires_restart:
                return 503, {"error": "operator_restart_required"}
            if self._recovery_salt is None or self._recovery_hash is None:
                return 403, {"error": "recovery_not_enabled"}
            trial = self._recovery_digest(code, self._recovery_salt)
            if not hmac.compare_digest(trial, self._recovery_hash):
                return 403, {"error": "invalid_recovery_proof"}
            new_salt = secrets.token_bytes(16)
            new_hash = self._derive(new_password, new_salt)
            from local_source_policy import (
                secure_replace_json, PostReplaceDurabilityError)
            try:
                secure_replace_json(self._owner_path, {
                    "version": FILE_VERSION,
                    "salt": new_salt.hex(),
                    "scrypt_hash": new_hash.hex()})
            except PostReplaceDurabilityError:
                self._requires_restart = True
                self._sessions.clear()
                return 503, {"error": "operator_restart_required"}
            except (OSError, RuntimeError):
                return 503, {"error": "recovery_storage_failed"}
            self._salt, self._hash = new_salt, new_hash
            self._recovery_salt, self._recovery_hash = None, None
            self._sessions.clear()
            self._failures, self._locked_until = 0, 0
            return 200, {"status": "OWNER_RECOVERED_CODE_CONSUMED"}

    def login(self, password):
        with self._lock:
            accepted = self._confirm_locked(password)
            if accepted != 200:
                return accepted, None
            now = self._clock()
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
            if self._requires_restart:
                return None
            entry = self._lookup(token)
            if entry is None:
                return None
            return {"csrf_token": entry["csrf"],
                    "expires_in": max(0, int(entry["expires"] - self._clock()))}

    def authorize(self, token, csrf):
        with self._lock:
            if self._requires_restart:
                return False
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
