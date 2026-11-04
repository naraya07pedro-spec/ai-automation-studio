"""Single-owner, fixed public GitHub source allowlist with atomic local storage.

This is an explicit additional operator permission boundary, not a provider
OAuth grant, private repository access or multi-user RBAC. No outbound writes.
"""
import json
import os
from pathlib import Path
import secrets
import stat
import threading

from github_public_issue_intake import PublicGitHubReadOnlyIntake

MAX_ALLOWED_REPOS = 16


class SourcePolicyError(RuntimeError):
    pass


class PostReplaceDurabilityError(SourcePolicyError):
    """Target replaced but directory durability was not confirmed."""
    pass


def secure_replace_json(path, data):
    """Atomically replace owner-owned JSON, preserving restrictive POSIX mode.

    Caller serializes writes. Failures BEFORE os.replace preserve the old
    file. Errors AFTER replace are uncertain: do not trust old memory state.
    """
    p = Path(path).expanduser()
    if p.is_symlink() or (p.exists() and not p.is_file()):
        raise SourcePolicyError("unsafe_policy_path")
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if p.parent.is_symlink():
        raise SourcePolicyError("unsafe_policy_directory")
    if os.name == "posix":
        os.chmod(p.parent, 0o700)
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(raw) > 4096:
        raise SourcePolicyError("policy_too_large")
    temp = p.parent / ("." + p.name + "." + secrets.token_hex(12) + ".tmp")
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        if os.name == "posix":
            os.chmod(temp, 0o600)
        if p.is_symlink():
            raise SourcePolicyError("unsafe_policy_path")
        os.replace(temp, p)
        if os.name == "posix":
            # Rename has already made the new file visible. If syncing its
            # containing directory fails, the old in-memory state is unsafe.
            try:
                dirfd = os.open(str(p.parent), os.O_RDONLY)
                try:
                    os.fsync(dirfd)
                finally:
                    os.close(dirfd)
            except OSError as exc:
                raise PostReplaceDurabilityError(
                    "post_replace_directory_sync_failed") from exc
    finally:
        if temp.exists():
            temp.unlink()


class LocalPublicSourcePolicy:
    """Default deny; exact canonical public repo grants only, max 16.

    An epoch change revokes already-constituted preview snapshots even if an
    owner revokes and later regrants that same repo.
    """

    def __init__(self, path=None):
        self._path = Path(path).expanduser() if path is not None else None
        self._lock = threading.RLock()
        self._allowed = set()
        self._epoch = 0
        self._blocked = False
        if self._path is not None and self._path.exists():
            if self._path.is_symlink() or not self._path.is_file():
                raise SourcePolicyError("unsafe_policy_path")
            if os.name == "posix" and stat.S_IMODE(self._path.stat().st_mode) & 0o077:
                raise SourcePolicyError("insecure_source_policy_permissions")
            try:
                data = json.loads(self._path.read_bytes())
                if (type(data) is not dict or set(data) != {"version", "allowed", "epoch"}
                        or type(data["version"]) is not int or data["version"] != 1
                        or type(data["allowed"]) is not list
                        or type(data["epoch"]) is not int or data["epoch"] < 0
                        or len(data["allowed"]) > MAX_ALLOWED_REPOS):
                    raise ValueError("shape")
                normalized = [self.normalize(item) for item in data["allowed"]]
                if any(item is None for item in normalized) or len(set(normalized)) != len(normalized):
                    raise ValueError("repos")
                self._allowed = set(normalized)
                self._epoch = data["epoch"]
            except (OSError, ValueError, UnicodeError, TypeError, KeyError) as exc:
                raise SourcePolicyError("invalid_source_policy") from exc

    @staticmethod
    def normalize(repo):
        value = PublicGitHubReadOnlyIntake._check_repo(repo)
        return value.lower() if value is not None else None

    def snapshot(self):
        with self._lock:
            return {"allowed": [] if self._blocked else sorted(self._allowed),
                    "epoch": self._epoch,
                    "max_allowed": MAX_ALLOWED_REPOS,
                    "storage_recovery_required": self._blocked}

    def is_allowed(self, repo, epoch=None):
        normalized = self.normalize(repo)
        with self._lock:
            return (not self._blocked and normalized is not None
                    and normalized in self._allowed
                    and (epoch is None or epoch == self._epoch))

    def version(self):
        with self._lock:
            return self._epoch

    def _persist(self, updated):
        if self._blocked:
            raise SourcePolicyError("source_policy_requires_restart")
        if self._path is not None:
            try:
                secure_replace_json(self._path, {
                    "version": 1, "allowed": sorted(updated),
                    "epoch": self._epoch + 1})
            except PostReplaceDurabilityError:
                # File may already show updated grants/revocations.
                self._blocked = True
                raise
        self._allowed = updated
        self._epoch += 1

    def grant(self, repo):
        name = self.normalize(repo)
        if name is None:
            return 422, {"error": "invalid_public_repo"}
        with self._lock:
            if self._blocked:
                return 503, {"error": "source_policy_restart_required"}
            if name in self._allowed:
                return 200, {"status": "SOURCE_ALREADY_ALLOWED", **self.snapshot()}
            if len(self._allowed) >= MAX_ALLOWED_REPOS:
                return 409, {"error": "source_allowlist_full"}
            updated = set(self._allowed)
            updated.add(name)
            try:
                self._persist(updated)
            except (OSError, SourcePolicyError):
                return 503, {"error": (
                    "source_policy_restart_required" if self._blocked
                    else "source_policy_storage_failed")}
            return 200, {"status": "PUBLIC_SOURCE_ALLOWED", **self.snapshot()}

    def revoke(self, repo):
        name = self.normalize(repo)
        if name is None:
            return 422, {"error": "invalid_public_repo"}
        with self._lock:
            if self._blocked:
                return 503, {"error": "source_policy_restart_required"}
            if name not in self._allowed:
                return 404, {"error": "source_not_allowed"}
            updated = set(self._allowed)
            updated.remove(name)
            try:
                self._persist(updated)
            except (OSError, SourcePolicyError):
                return 503, {"error": (
                    "source_policy_restart_required" if self._blocked
                    else "source_policy_storage_failed")}
            return 200, {"status": "PUBLIC_SOURCE_REVOKED", **self.snapshot()}
