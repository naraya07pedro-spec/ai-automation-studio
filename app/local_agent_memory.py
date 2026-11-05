"""Owner-curated local memory cards for an explicitly selected Ollama chat turn.

This is not automatic memory extraction or broad filesystem retrieval.
Only an authenticated owner's typed notes are stored, in plaintext SQLite.
No model can create or modify cards. Each chat use requires chosen card IDs.
"""
from pathlib import Path
import os
import re
import sqlite3
import stat
import threading

MAX_MEMORY_CARDS = 24
MAX_MEMORY_TEXT = 600
MAX_SELECTED_MEMORY = 3
ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}\Z")


class MemoryCardError(Exception):
    pass


def valid_memory_id(value):
    return type(value) is str and ID_RE.fullmatch(value) is not None


def validate_memory_text(value):
    return (type(value) is str
            and 1 <= len(value) <= MAX_MEMORY_TEXT
            and bool(value.strip())
            and all(ord(char) >= 32 or char in "\n\t" for char in value)
            and "\x7f" not in value)


def valid_selection(ids):
    return (type(ids) is list and 1 <= len(ids) <= MAX_SELECTED_MEMORY
            and all(valid_memory_id(v) for v in ids)
            and len(set(ids)) == len(ids))


class LocalAgentMemory:
    def __init__(self, database=":memory:"):
        path_name = str(database)
        if path_name != ":memory:":
            path = Path(database).expanduser()
            if path.is_symlink() or path.parent.is_symlink():
                raise MemoryCardError("unsafe_memory_path")
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.parent.is_symlink():
                raise MemoryCardError("unsafe_memory_path")
            if os.name == "posix":
                os.chmod(path.parent, 0o700)
            if path.exists():
                if not path.is_file():
                    raise MemoryCardError("unsafe_memory_path")
                if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
                    raise MemoryCardError("insecure_memory_permissions")
            else:
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
            path_name = str(path)
        self.lock = threading.RLock()
        try:
            self.db = sqlite3.connect(path_name, check_same_thread=False, timeout=3)
            self.db.execute("PRAGMA busy_timeout=3000")
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA synchronous=FULL")
            with self.db:
                self.db.execute("""CREATE TABLE IF NOT EXISTS memory_cards(
                    card_id TEXT PRIMARY KEY,
                    body TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision>=1),
                    CHECK(length(card_id) BETWEEN 1 AND 32),
                    CHECK(length(body) BETWEEN 1 AND 600))""")
        except (sqlite3.Error, OSError):
            raise MemoryCardError("memory_storage_unavailable") from None

    def list_cards(self):
        try:
            with self.lock:
                rows = self.db.execute(
                    "SELECT card_id,body,revision FROM memory_cards "
                    "ORDER BY card_id ASC LIMIT ?", (MAX_MEMORY_CARDS + 1,)
                ).fetchall()
            if len(rows) > MAX_MEMORY_CARDS:
                raise MemoryCardError("memory_storage_unavailable")
            return [{"id": card_id, "text": body, "revision": revision}
                    for card_id, body, revision in rows]
        except sqlite3.Error:
            raise MemoryCardError("memory_storage_unavailable") from None

    def put(self, card_id, text, expected_revision):
        if not valid_memory_id(card_id) or not validate_memory_text(text):
            raise MemoryCardError("invalid_memory_card")
        if type(expected_revision) is not int or not 0 <= expected_revision <= 1000000:
            raise MemoryCardError("invalid_memory_revision")
        try:
            with self.lock, self.db:
                previous = self.db.execute(
                    "SELECT revision FROM memory_cards WHERE card_id=?",
                    (card_id,)).fetchone()
                if previous is None:
                    if expected_revision != 0:
                        raise MemoryCardError("memory_revision_conflict")
                    count = self.db.execute(
                        "SELECT COUNT(*) FROM memory_cards").fetchone()[0]
                    if count >= MAX_MEMORY_CARDS:
                        raise MemoryCardError("memory_capacity_reached")
                    self.db.execute(
                        "INSERT INTO memory_cards(card_id,body,revision) VALUES(?,?,1)",
                        (card_id, text))
                    version = 1
                else:
                    if expected_revision != previous[0]:
                        raise MemoryCardError("memory_revision_conflict")
                    version = previous[0] + 1
                    self.db.execute(
                        "UPDATE memory_cards SET body=?,revision=? WHERE card_id=?",
                        (text, version, card_id))
            return {"id": card_id, "text": text, "revision": version,
                    "source": "OWNER_CURATED_UNVERIFIED"}
        except sqlite3.Error:
            raise MemoryCardError("memory_storage_unavailable") from None

    def delete(self, card_id, expected_revision):
        if not valid_memory_id(card_id):
            raise MemoryCardError("invalid_memory_id")
        if type(expected_revision) is not int or expected_revision < 1:
            raise MemoryCardError("invalid_memory_revision")
        try:
            with self.lock, self.db:
                result = self.db.execute(
                    "DELETE FROM memory_cards WHERE card_id=? AND revision=?",
                    (card_id, expected_revision))
                if result.rowcount != 1:
                    raise MemoryCardError("memory_revision_conflict")
            return {"status": "DELETED_LOCAL", "id": card_id,
                    "external_write_count": 0}
        except sqlite3.Error:
            raise MemoryCardError("memory_storage_unavailable") from None

    def selected(self, ids):
        if not valid_selection(ids):
            raise MemoryCardError("invalid_memory_selection")
        try:
            with self.lock:
                cards = []
                for card_id in ids:
                    row = self.db.execute(
                        "SELECT body FROM memory_cards WHERE card_id=?",
                        (card_id,)).fetchone()
                    if row is None:
                        raise MemoryCardError("memory_card_not_found")
                    cards.append({"id": card_id, "text": row[0]})
            return cards
        except sqlite3.Error:
            raise MemoryCardError("memory_storage_unavailable") from None

    def close(self):
        with self.lock:
            self.db.close()
