"""Durable single-owner agent profiles with a restrictive executable tool policy.

A profile changes planning context but NEVER grants tools. An actual plan still
needs original source permissions, owner approval and explicit step execution.
Changes or disabling a profile invalidate all earlier bound plan revisions.
"""
from datetime import datetime, timezone
import json
import re
import sqlite3
import threading

from local_private_sqlite_path import prepare_private_sqlite, PrivateSQLitePathError
from local_tool_registry import BATCH_READ_TOOLS

MAX_AGENTS = 24
AGENT_ID = re.compile(r"[a-z][a-z0-9_-]{2,39}\Z", re.ASCII)
CATEGORIES = frozenset((
    "research", "engineering", "business", "creative", "productivity", "personal"
))


class AgentProfileError(RuntimeError):
    def __init__(self, reason, status=422):
        super().__init__(reason)
        self.status = status


def _text(value, limit, field, *, optional=False):
    if (type(value) is not str or len(value) > limit
            or (not optional and not value.strip())
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise AgentProfileError("invalid_agent_" + field)
    return value.strip()


def _validate(fields):
    if type(fields) is not dict or set(fields) != {
        "agent_id", "name", "category", "description",
        "instructions", "allowed_tools"
    }:
        raise AgentProfileError("invalid_agent_fields")
    identifier = fields["agent_id"]
    if type(identifier) is not str or not AGENT_ID.fullmatch(identifier):
        raise AgentProfileError("invalid_agent_id")
    if fields["category"] not in CATEGORIES or type(fields["category"]) is not str:
        raise AgentProfileError("invalid_agent_category")
    tools = fields["allowed_tools"]
    if (type(tools) is not list or not 1 <= len(tools) <= len(BATCH_READ_TOOLS)
            or any(type(t) is not str or t not in BATCH_READ_TOOLS for t in tools)
            or len(set(tools)) != len(tools)):
        raise AgentProfileError("invalid_agent_tools")
    return {
        "agent_id": identifier,
        "name": _text(fields["name"], 64, "name"),
        "category": fields["category"],
        "description": _text(fields["description"], 280, "description", optional=True),
        "instructions": _text(fields["instructions"], 1200, "instructions", optional=True),
        "allowed_tools": sorted(tools),
    }


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LocalAgentProfiles:
    """Owner-managed SQLite registry; no model or background execution."""
    def __init__(self, database=":memory:"):
        try:
            location = prepare_private_sqlite(database)
        except PrivateSQLitePathError:
            raise AgentProfileError("agent_profile_path_rejected", 403) from None
        self.lock = threading.RLock()
        try:
            self.db = sqlite3.connect(location, check_same_thread=False)
            self.db.execute("PRAGMA busy_timeout=5000")
            if location != ":memory:":
                self.db.execute("PRAGMA journal_mode=WAL")
            with self.db:
                self.db.execute("""CREATE TABLE IF NOT EXISTS agent_profiles (
                    agent_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    description TEXT NOT NULL,
                    instructions TEXT NOT NULL,
                    allowed_tools_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    enabled INTEGER NOT NULL,
                    created_utc TEXT NOT NULL,
                    updated_utc TEXT NOT NULL)""")
        except sqlite3.Error:
            raise AgentProfileError("agent_profile_storage_unavailable", 503) from None

    def _one(self, identifier):
        if type(identifier) is not str or not AGENT_ID.fullmatch(identifier):
            raise AgentProfileError("invalid_agent_id")
        row = self.db.execute("""SELECT agent_id,name,category,description,
            instructions,allowed_tools_json,revision,enabled,created_utc,updated_utc
            FROM agent_profiles WHERE agent_id=?""", (identifier,)).fetchone()
        if row is None:
            raise AgentProfileError("agent_not_found", 404)
        try:
            tools = json.loads(row[5])
            obj = _validate({
                "agent_id": row[0], "name": row[1], "category": row[2],
                "description": row[3], "instructions": row[4], "allowed_tools": tools
            })
            if (obj["allowed_tools"] != tools
                    or type(row[6]) is not int or row[6] < 1
                    or row[7] not in (0, 1)):
                raise ValueError("invalid stored profile")
        except (ValueError, TypeError, AgentProfileError):
            raise AgentProfileError("agent_profile_storage_invalid", 503) from None
        obj.update(revision=row[6], enabled=bool(row[7]),
                   created_utc=row[8], updated_utc=row[9])
        return obj

    def get(self, identifier):
        try:
            with self.lock:
                return self._one(identifier)
        except sqlite3.Error:
            raise AgentProfileError("agent_profile_storage_unavailable", 503) from None

    def list(self):
        try:
            with self.lock:
                ids = [r[0] for r in self.db.execute(
                    "SELECT agent_id FROM agent_profiles ORDER BY created_utc,agent_id"
                ).fetchall()]
                return [self._one(i) for i in ids]
        except sqlite3.Error:
            raise AgentProfileError("agent_profile_storage_unavailable", 503) from None

    def create(self, fields):
        item = _validate(fields)
        now = _utc()
        try:
            with self.lock, self.db:
                if self.db.execute("SELECT COUNT(*) FROM agent_profiles").fetchone()[0] >= MAX_AGENTS:
                    raise AgentProfileError("agent_capacity_reached", 409)
                try:
                    self.db.execute("""INSERT INTO agent_profiles
                        (agent_id,name,category,description,instructions,
                         allowed_tools_json,revision,enabled,created_utc,updated_utc)
                         VALUES(?,?,?,?,?,?,1,1,?,?)""",
                        (item["agent_id"], item["name"], item["category"],
                         item["description"], item["instructions"],
                         json.dumps(item["allowed_tools"]), now, now))
                except sqlite3.IntegrityError:
                    raise AgentProfileError("agent_already_exists", 409) from None
                return self._one(item["agent_id"])
        except sqlite3.Error:
            raise AgentProfileError("agent_profile_storage_unavailable", 503) from None

    def update(self, fields):
        if type(fields) is not dict or set(fields) != {
            "agent_id", "name", "category", "description", "instructions",
            "allowed_tools", "expected_revision"
        }:
            raise AgentProfileError("invalid_agent_fields")
        rev = fields["expected_revision"]
        if type(rev) is not int or rev < 1:
            raise AgentProfileError("invalid_agent_revision")
        item = _validate({k: v for k, v in fields.items() if k != "expected_revision"})
        try:
            with self.lock, self.db:
                previous = self._one(item["agent_id"])
                if previous["revision"] != rev or not previous["enabled"]:
                    raise AgentProfileError("agent_profile_changed_or_disabled", 409)
                cursor = self.db.execute("""UPDATE agent_profiles
                    SET name=?,category=?,description=?,instructions=?,
                    allowed_tools_json=?,revision=revision+1,updated_utc=?
                    WHERE agent_id=? AND revision=? AND enabled=1""",
                    (item["name"],item["category"],item["description"],
                     item["instructions"],json.dumps(item["allowed_tools"]),_utc(),
                     item["agent_id"],rev))
                if cursor.rowcount != 1:
                    raise AgentProfileError("agent_profile_changed_or_disabled", 409)
                return self._one(item["agent_id"])
        except sqlite3.Error:
            raise AgentProfileError("agent_profile_storage_unavailable", 503) from None

    def disable(self, identifier, expected_revision):
        if type(expected_revision) is not int or expected_revision < 1:
            raise AgentProfileError("invalid_agent_revision")
        try:
            with self.lock, self.db:
                previous = self._one(identifier)
                if previous["revision"] != expected_revision or not previous["enabled"]:
                    raise AgentProfileError("agent_profile_changed_or_disabled", 409)
                self.db.execute("""UPDATE agent_profiles SET enabled=0,
                    revision=revision+1,updated_utc=? WHERE agent_id=?""",
                    (_utc(), identifier))
                return self._one(identifier)
        except sqlite3.Error:
            raise AgentProfileError("agent_profile_storage_unavailable", 503) from None

    def context(self, identifier):
        profile = self.get(identifier)
        if not profile["enabled"]:
            raise AgentProfileError("agent_profile_changed_or_disabled", 409)
        return {
            "binding": {"agent_id": profile["agent_id"], "revision": profile["revision"]},
            "planning": {
                "name": profile["name"],
                "instructions": profile["instructions"],
                "allowed_tools": profile["allowed_tools"],
            },
        }

    def check_binding(self, binding, steps):
        if (type(binding) is not dict or set(binding) != {"agent_id", "revision"}
                or type(binding["revision"]) is not int or binding["revision"] < 1):
            raise AgentProfileError("agent_profile_binding_invalid", 409)
        with self.lock:
            profile = self.get(binding["agent_id"])
            if not profile["enabled"] or profile["revision"] != binding["revision"]:
                raise AgentProfileError("agent_profile_changed_or_disabled", 409)
            if any(type(s) is not dict or s.get("tool") not in profile["allowed_tools"]
                   for s in steps):
                raise AgentProfileError("agent_profile_tool_denied", 403)
        return True

    def close(self):
        with self.lock:
            self.db.close()
