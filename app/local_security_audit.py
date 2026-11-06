"""Bounded, local-only security event ledger.

Records action/outcome/source fingerprint ONLY. No passwords, session tokens,
recovery proofs, provider issue contents, IPs, request bodies or raw repo names.
SQLite durability is enabled; same-OS-user tampering is not prevented.
"""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import threading

ACTIONS = frozenset({
    "OPERATOR_LOGIN", "OPERATOR_LOGOUT", "OWNER_ROTATE",
    "PUBLIC_SOURCE_GRANT", "PUBLIC_SOURCE_REVOKE",
    "RECOVERY_ENROLL", "RECOVERY_RESET", "LOCAL_ARTIFACT_EXPORT",
    "HN_STORY_GRANT", "HN_STORY_REVOKE",
    "LOCAL_CSV_PROFILE_EXPORT",
})
OUTCOMES = frozenset({"ATTEMPT", "SUCCESS", "DENIED", "FAILED", "UNCERTAIN"})
MAX_EVENTS = 5000
TRIM_COUNT = 500


class SecurityAuditError(RuntimeError):
    pass


class LocalSecurityAudit:
    def __init__(self, filename):
        self._lock = threading.RLock()
        self._blocked = False
        path = Path(filename).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.parent.is_symlink() or path.is_symlink():
            raise SecurityAuditError("unsafe_audit_path")
        if os.name == "posix":
            os.chmod(path.parent, 0o700)
        if path.exists():
            if not path.is_file():
                raise SecurityAuditError("unsafe_audit_path")
            if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise SecurityAuditError("insecure_audit_permissions")
        else:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        if os.name == "posix":
            os.chmod(path, 0o600)
        try:
            self._db = sqlite3.connect(str(path), timeout=3,
                                       check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=DELETE")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA busy_timeout=3000")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS security_events ("
                " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                " occurred_utc TEXT NOT NULL,"
                " action TEXT NOT NULL,"
                " outcome TEXT NOT NULL,"
                " source_fingerprint TEXT NOT NULL"
                ")")
            self._db.commit()
        except (OSError, sqlite3.Error) as exc:
            raise SecurityAuditError("audit_store_unavailable") from exc

    @staticmethod
    def _fingerprint(repo):
        from local_source_policy import LocalPublicSourcePolicy
        name = LocalPublicSourcePolicy.normalize(repo)
        if name is None:
            return ""
        return hashlib.sha256(name.encode("ascii")).hexdigest()[:16]

    def ready(self):
        with self._lock:
            return not self._blocked

    def record(self, action, outcome, repo=None):
        """Raise on failure. Once persistence fails, future writes fail closed."""
        if action not in ACTIONS or outcome not in OUTCOMES:
            raise ValueError("invalid_security_audit_event")
        subject = self._fingerprint(repo) if repo is not None else ""
        with self._lock:
            if self._blocked:
                raise SecurityAuditError("audit_restart_required")
            try:
                with self._db:
                    total = self._db.execute(
                        "SELECT COUNT(*) FROM security_events").fetchone()[0]
                    if total >= MAX_EVENTS:
                        self._db.execute(
                            "DELETE FROM security_events WHERE seq IN ("
                            "SELECT seq FROM security_events ORDER BY seq ASC LIMIT ?)",
                            (TRIM_COUNT,))
                    self._db.execute(
                        "INSERT INTO security_events "
                        "(occurred_utc,action,outcome,source_fingerprint) "
                        "VALUES (?,?,?,?)",
                        (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         action, outcome, subject))
            except sqlite3.Error as exc:
                self._blocked = True
                raise SecurityAuditError("audit_restart_required") from exc

    def recent(self, limit=40):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_audit_limit")
        with self._lock:
            if self._blocked:
                raise SecurityAuditError("audit_restart_required")
            try:
                rows = self._db.execute(
                    "SELECT seq,occurred_utc,action,outcome,source_fingerprint "
                    "FROM security_events ORDER BY seq DESC LIMIT ?",
                    (limit,)).fetchall()
                return [
                    {"seq": item[0], "occurred_utc": item[1],
                     "action": item[2], "outcome": item[3],
                     "source_fingerprint": item[4]}
                    for item in rows
                ]
            except sqlite3.Error as exc:
                self._blocked = True
                raise SecurityAuditError("audit_restart_required") from exc

    def close(self):
        with self._lock:
            self._db.close()
