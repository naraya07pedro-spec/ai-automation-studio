"""Owner-curated, project+Agent-scoped long-term memory for local AI plans.

Explicit selection only. No automatic ingestion, retrieval, provider writes or
model mutation. Expiry/revocation is checked at EACH owner-approved plan step.
Plaintext owner-private SQLite is NOT disk encryption or secure backup erasure.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from hmac import compare_digest
import re
import secrets
import sqlite3
import threading

from local_private_sqlite_path import prepare_private_sqlite, PrivateSQLitePathError
from local_agent_profiles import AGENT_ID

PROJECT_ID = re.compile(r"[a-z][a-z0-9_-]{2,39}\Z", re.ASCII)
NOTE_ID = re.compile(r"[a-z][a-z0-9_-]{2,31}\Z", re.ASCII)
HEX32 = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
HEX64 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
RETENTION_DAYS = frozenset((7, 30, 90, 365))
SOURCE_KINDS = frozenset(("OWNER_NOTE", "OWNER_IMPORTED_UNVERIFIED"))
MAX_NOTES = 200
MAX_PER_SCOPE = 24
MAX_SELECTED = 3
MAX_TEXT = 1000


class ScopedMemoryError(RuntimeError):
    def __init__(self, reason, status=422):
        super().__init__(reason)
        self.status = status


def _scope(project_id, agent_id):
    if (type(project_id) is not str or PROJECT_ID.fullmatch(project_id) is None
            or type(agent_id) is not str or AGENT_ID.fullmatch(agent_id) is None):
        raise ScopedMemoryError("invalid_scoped_memory_scope")
    return project_id, agent_id


def _note_id(note_id):
    if type(note_id) is not str or NOTE_ID.fullmatch(note_id) is None:
        raise ScopedMemoryError("invalid_scoped_memory_id")
    return note_id


def _now(clock):
    value = clock()
    if (not isinstance(value, datetime) or value.tzinfo is None
            or value.utcoffset() is None):
        raise ScopedMemoryError("scoped_memory_clock_unavailable", 503)
    return value.astimezone(timezone.utc)


def _text_valid(value):
    return (type(value) is str and 1 <= len(value) <= MAX_TEXT
            and bool(value.strip())
            and all((ord(ch) >= 32 or ch in "\t\n") and ord(ch) != 127
                    for ch in value))


def _validate_put(item):
    if type(item) is not dict or set(item) != {
        "project_id", "agent_id", "note_id", "text", "source_kind",
        "source_label", "retention_days", "expected_revision"
    }:
        raise ScopedMemoryError("invalid_scoped_memory_fields")
    _scope(item["project_id"], item["agent_id"])
    _note_id(item["note_id"])
    if not _text_valid(item["text"]):
        raise ScopedMemoryError("invalid_scoped_memory_text")
    if (type(item["source_kind"]) is not str
            or item["source_kind"] not in SOURCE_KINDS):
        raise ScopedMemoryError("invalid_scoped_memory_source")
    label = item["source_label"]
    if (type(label) is not str or len(label) > 96
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in label)):
        raise ScopedMemoryError("invalid_scoped_memory_source")
    if (type(item["retention_days"]) is not int
            or item["retention_days"] not in RETENTION_DAYS):
        raise ScopedMemoryError("invalid_scoped_memory_retention")
    if type(item["expected_revision"]) is not int or item["expected_revision"] < 0:
        raise ScopedMemoryError("invalid_scoped_memory_revision")
    return item


def _validate_refs(refs):
    if (type(refs) is not list or not 1 <= len(refs) <= MAX_SELECTED):
        raise ScopedMemoryError("invalid_scoped_memory_refs")
    seen = set()
    for ref in refs:
        if type(ref) is not dict or set(ref) != {
            "project_id", "agent_id", "note_id", "revision",
            "generation", "digest", "expires_utc"
        }:
            raise ScopedMemoryError("invalid_scoped_memory_refs")
        _scope(ref["project_id"], ref["agent_id"])
        _note_id(ref["note_id"])
        if (type(ref["revision"]) is not int or ref["revision"] < 1
                or type(ref["generation"]) is not str
                or HEX32.fullmatch(ref["generation"]) is None
                or type(ref["digest"]) is not str
                or HEX64.fullmatch(ref["digest"]) is None
                or type(ref["expires_utc"]) is not str):
            raise ScopedMemoryError("invalid_scoped_memory_refs")
        try:
            expiry = datetime.fromisoformat(ref["expires_utc"])
            if expiry.tzinfo is None or expiry.utcoffset() is None:
                raise ValueError
        except ValueError:
            raise ScopedMemoryError("invalid_scoped_memory_refs") from None
        key = ref["project_id"], ref["agent_id"], ref["note_id"]
        if key in seen:
            raise ScopedMemoryError("invalid_scoped_memory_refs")
        seen.add(key)
    if len({(r["project_id"], r["agent_id"]) for r in refs}) != 1:
        raise ScopedMemoryError("invalid_scoped_memory_refs")
    return refs


class LocalScopedAgentMemory:
    """Versioned, explicit project+Agent memory with deterministic expiry."""
    def __init__(self, database=":memory:", *, clock=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.RLock()
        try:
            location = prepare_private_sqlite(database)
        except PrivateSQLitePathError:
            raise ScopedMemoryError("scoped_memory_path_rejected", 403) from None
        try:
            self.db = sqlite3.connect(location, timeout=5, check_same_thread=False)
            self.db.execute("PRAGMA busy_timeout=5000")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA secure_delete=ON")
            with self.lock, self.db:
                self.db.execute("""CREATE TABLE IF NOT EXISTS scoped_agent_memory (
                    project_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    note_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision>=1),
                    generation TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_label TEXT NOT NULL,
                    expires_utc TEXT NOT NULL,
                    created_utc TEXT NOT NULL,
                    updated_utc TEXT NOT NULL,
                    PRIMARY KEY(project_id,agent_id,note_id))""")
        except sqlite3.Error:
            raise ScopedMemoryError("scoped_memory_storage_unavailable", 503) from None

    def _row(self, project_id, agent_id, note_id):
        return self.db.execute("""SELECT text,revision,generation,source_kind,
                  source_label,expires_utc,created_utc,updated_utc
                FROM scoped_agent_memory
                WHERE project_id=? AND agent_id=? AND note_id=?""",
                (project_id, agent_id, note_id)).fetchone()

    @staticmethod
    def _output(project_id, agent_id, note_id, row, now, *, include_text=True):
        text,revision,generation,source_kind,source_label,expiry,created,updated = row
        try:
            ex = datetime.fromisoformat(expiry)
            if (ex.tzinfo is None or ex.utcoffset() is None
                    or type(text) is not str or not _text_valid(text)
                    or type(revision) is not int or revision < 1
                    or type(generation) is not str or HEX32.fullmatch(generation) is None
                    or type(source_kind) is not str or source_kind not in SOURCE_KINDS
                    or type(source_label) is not str):
                raise ValueError
        except (ValueError, TypeError):
            raise ScopedMemoryError("scoped_memory_storage_invalid", 503) from None
        return {
            "project_id": project_id, "agent_id": agent_id, "note_id": note_id,
            "revision": revision, "generation": generation,
            "digest": sha256(text.encode("utf-8")).hexdigest(),
            "source_kind": source_kind, "source_label": source_label,
            "expires_utc": expiry, "expired": now >= ex,
            "created_utc": created, "updated_utc": updated,
            **({"text": text} if include_text else {}),
        }

    def list(self, project_id, agent_id):
        _scope(project_id, agent_id)
        try:
            with self.lock:
                now = _now(self.clock)
                rows = self.db.execute("""SELECT note_id FROM scoped_agent_memory
                   WHERE project_id=? AND agent_id=? ORDER BY note_id LIMIT ?""",
                   (project_id, agent_id, MAX_PER_SCOPE + 1)).fetchall()
                if len(rows) > MAX_PER_SCOPE:
                    raise ScopedMemoryError("scoped_memory_storage_invalid", 503)
                return [self._output(project_id, agent_id, note_id,
                                     self._row(project_id,agent_id,note_id),
                                     now) for (note_id,) in rows]
        except sqlite3.Error:
            raise ScopedMemoryError("scoped_memory_storage_unavailable", 503) from None

    def put(self, payload):
        value = _validate_put(payload)
        project_id, agent_id, note_id = (value[k] for k in
                                         ("project_id", "agent_id", "note_id"))
        try:
            with self.lock, self.db:
                now = _now(self.clock)
                expires = (now + timedelta(days=value["retention_days"])).isoformat()
                previous = self._row(project_id, agent_id, note_id)
                if previous is None:
                    if value["expected_revision"] != 0:
                        raise ScopedMemoryError("scoped_memory_revision_conflict", 409)
                    count = self.db.execute("SELECT COUNT(*) FROM scoped_agent_memory").fetchone()[0]
                    scope_count = self.db.execute("""SELECT COUNT(*) FROM scoped_agent_memory
                         WHERE project_id=? AND agent_id=?""",
                         (project_id, agent_id)).fetchone()[0]
                    if count >= MAX_NOTES or scope_count >= MAX_PER_SCOPE:
                        raise ScopedMemoryError("scoped_memory_capacity_reached", 409)
                    generation = secrets.token_hex(16)
                    stamp = now.isoformat()
                    self.db.execute("""INSERT INTO scoped_agent_memory (
                       project_id,agent_id,note_id,text,revision,generation,
                       source_kind,source_label,expires_utc,created_utc,updated_utc)
                       VALUES(?,?,?,?,1,?,?,?,?,?,?)""",
                       (project_id,agent_id,note_id,value["text"],generation,
                        value["source_kind"],value["source_label"],
                        expires,stamp,stamp))
                else:
                    old = self._output(project_id,agent_id,note_id,previous,now)
                    if old["expired"] or old["revision"] != value["expected_revision"]:
                        raise ScopedMemoryError("scoped_memory_revision_conflict", 409)
                    self.db.execute("""UPDATE scoped_agent_memory
                       SET text=?,revision=revision+1,source_kind=?,
                           source_label=?,expires_utc=?,updated_utc=?
                       WHERE project_id=? AND agent_id=? AND note_id=?""",
                       (value["text"],value["source_kind"],value["source_label"],
                        expires,now.isoformat(),project_id,agent_id,note_id))
                return self._output(project_id,agent_id,note_id,
                                    self._row(project_id,agent_id,note_id),now)
        except sqlite3.Error:
            raise ScopedMemoryError("scoped_memory_storage_unavailable", 503) from None

    def delete(self, project_id, agent_id, note_id, expected_revision):
        _scope(project_id, agent_id)
        _note_id(note_id)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ScopedMemoryError("invalid_scoped_memory_revision")
        try:
            with self.lock, self.db:
                result = self.db.execute("""DELETE FROM scoped_agent_memory
                  WHERE project_id=? AND agent_id=? AND note_id=? AND revision=?""",
                  (project_id,agent_id,note_id,expected_revision))
                if result.rowcount != 1:
                    raise ScopedMemoryError("scoped_memory_revision_conflict", 409)
            return {"status":"DELETED_FROM_LIVE_LEDGER",
                    "project_id":project_id,"agent_id":agent_id,"note_id":note_id,
                    "external_write_count":0,
                    "backup_copies_may_remain":True}
        except sqlite3.Error:
            raise ScopedMemoryError("scoped_memory_storage_unavailable", 503) from None

    def select(self, project_id, agent_id, ids):
        _scope(project_id, agent_id)
        if (type(ids) is not list or not 1 <= len(ids) <= MAX_SELECTED
                or any(type(x) is not str or NOTE_ID.fullmatch(x) is None for x in ids)
                or len(set(ids)) != len(ids)):
            raise ScopedMemoryError("invalid_scoped_memory_selection")
        try:
            with self.lock:
                now = _now(self.clock)
                selected = []
                for ident in ids:
                    row = self._row(project_id,agent_id,ident)
                    if row is None:
                        raise ScopedMemoryError("scoped_memory_not_found", 404)
                    obj = self._output(project_id,agent_id,ident,row,now)
                    if obj["expired"]:
                        raise ScopedMemoryError("scoped_memory_expired", 409)
                    selected.append(obj)
                refs = [{k: obj[k] for k in (
                    "project_id","agent_id","note_id","revision",
                    "generation","digest","expires_utc")} for obj in selected]
                context = [{"id":obj["note_id"],"text":obj["text"]} for obj in selected]
                return context, refs
        except sqlite3.Error:
            raise ScopedMemoryError("scoped_memory_storage_unavailable", 503) from None

    def require_revisions(self, refs, agent_binding):
        _validate_refs(refs)
        if (type(agent_binding) is not dict or
                type(agent_binding.get("agent_id")) is not str):
            raise ScopedMemoryError("scoped_memory_scope_denied", 403)
        if any(r["agent_id"] != agent_binding["agent_id"] for r in refs):
            raise ScopedMemoryError("scoped_memory_scope_denied", 403)
        try:
            with self.lock:
                now = _now(self.clock)
                for ref in refs:
                    row = self._row(ref["project_id"],ref["agent_id"],ref["note_id"])
                    if row is None:
                        raise ScopedMemoryError("scoped_memory_context_changed", 409)
                    obj = self._output(ref["project_id"],ref["agent_id"],
                                       ref["note_id"],row,now)
                    if (obj["expired"] or obj["revision"] != ref["revision"]
                            or not compare_digest(obj["generation"],ref["generation"])
                            or not compare_digest(obj["digest"],ref["digest"])
                            or obj["expires_utc"] != ref["expires_utc"]):
                        raise ScopedMemoryError("scoped_memory_context_changed", 409)
                return True
        except sqlite3.Error:
            raise ScopedMemoryError("scoped_memory_storage_unavailable", 503) from None

    def close(self):
        with self.lock:
            self.db.close()
