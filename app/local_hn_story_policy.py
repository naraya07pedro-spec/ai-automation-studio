"""Owner-granted exact Hacker News public story ID policy.

No credentials, wildcards, arbitrary paths or external writes.
Durable epoch advances on every grant/revoke; approval does not survive it.
"""
import json
import os
from pathlib import Path
import stat
import threading

from local_source_policy import secure_replace_json, SourcePolicyError, PostReplaceDurabilityError

MAX_STORIES = 12
MAX_STORY_ID = 999_999_999


def valid_story_id(value):
    return type(value) is int and 1 <= value <= MAX_STORY_ID


class HNStoryPolicy:
    def __init__(self, path=None):
        self._lock = threading.RLock()
        self._path = Path(path).expanduser() if path is not None else None
        self._allowed = set()
        self._epoch = 0
        self._blocked = False
        if self._path is not None and self._path.exists():
            if (self._path.is_symlink() or not self._path.is_file()
                    or (os.name == "posix" and
                        stat.S_IMODE(self._path.stat().st_mode) & 0o077)):
                raise SourcePolicyError("unsafe_hn_policy_path")
            try:
                data = json.loads(self._path.read_bytes())
                if (type(data) is not dict or set(data) != {"version","allowed","epoch"}
                        or data["version"] != 1 or type(data["epoch"]) is not int
                        or data["epoch"] < 0 or type(data["allowed"]) is not list
                        or len(data["allowed"]) > MAX_STORIES
                        or any(not valid_story_id(v) for v in data["allowed"])
                        or len(set(data["allowed"])) != len(data["allowed"])):
                    raise ValueError("schema")
                self._allowed = set(data["allowed"])
                self._epoch = data["epoch"]
            except (ValueError, UnicodeError, TypeError, OSError) as exc:
                raise SourcePolicyError("invalid_hn_policy") from exc

    def snapshot(self):
        with self._lock:
            return {"allowed_story_ids": [] if self._blocked else sorted(self._allowed),
                    "epoch": self._epoch, "max_allowed": MAX_STORIES,
                    "provider": "HACKER_NEWS_PUBLIC_V0",
                    "storage_recovery_required": self._blocked}

    def version(self):
        with self._lock:
            return self._epoch

    def is_allowed(self, story_id, epoch=None):
        with self._lock:
            return (not self._blocked and valid_story_id(story_id)
                    and story_id in self._allowed
                    and (epoch is None or epoch == self._epoch))

    def _persist(self, items):
        if self._blocked:
            raise SourcePolicyError("hn_policy_requires_restart")
        if self._path is not None:
            try:
                secure_replace_json(self._path, {
                    "version": 1, "allowed": sorted(items), "epoch": self._epoch+1})
            except PostReplaceDurabilityError:
                self._blocked = True
                raise
        self._allowed = items
        self._epoch += 1

    def grant(self, story_id):
        if not valid_story_id(story_id):
            return 422, {"error":"invalid_hn_story_id"}
        with self._lock:
            if self._blocked:
                return 503, {"error":"hn_policy_restart_required"}
            if story_id in self._allowed:
                return 200, {"status":"HN_STORY_ALREADY_ALLOWED", **self.snapshot()}
            if len(self._allowed) >= MAX_STORIES:
                return 409, {"error":"hn_story_allowlist_full"}
            try:
                self._persist(self._allowed | {story_id})
            except (SourcePolicyError, OSError):
                return 503, {"error": (
                    "hn_policy_restart_required" if self._blocked
                    else "hn_policy_storage_failed")}
            return 200, {"status":"HN_STORY_ALLOWED", **self.snapshot()}

    def revoke(self, story_id):
        if not valid_story_id(story_id):
            return 422, {"error":"invalid_hn_story_id"}
        with self._lock:
            if self._blocked:
                return 503, {"error":"hn_policy_restart_required"}
            if story_id not in self._allowed:
                return 404, {"error":"hn_story_not_allowed"}
            try:
                self._persist(self._allowed - {story_id})
            except (SourcePolicyError, OSError):
                return 503, {"error": (
                    "hn_policy_restart_required" if self._blocked
                    else "hn_policy_storage_failed")}
            return 200, {"status":"HN_STORY_REVOKED", **self.snapshot()}
