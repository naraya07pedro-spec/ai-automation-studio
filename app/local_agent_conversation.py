"""Bounded, opt-in, per-conversation local agent history.

Plaintext SQLite. One local owner, no multi-user isolation or PII assurance.
Existing Slice 49 history migrates into conversation_id='default' without loss.
"""
from pathlib import Path
import re
import sqlite3
import threading

MAX_PAIRS = 6
MAX_ANSWER = 3000
MAX_CONVERSATIONS = 8
DEFAULT_CONVERSATION = "default"
CONVERSATION_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}\Z")


class ConversationError(Exception):
    pass


def valid_conversation_id(value):
    return type(value) is str and CONVERSATION_ID.fullmatch(value) is not None


def _require_id(value):
    if not valid_conversation_id(value):
        raise ConversationError("invalid_conversation_id")


class LocalAgentConversation:
    def __init__(self, database=":memory:"):
        db = str(database)
        if db != ":memory:":
            path = Path(database).expanduser()
            if path.is_symlink():
                raise ConversationError("conversation_path_rejected")
            path.parent.mkdir(parents=True, exist_ok=True)
            db = str(path)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(db, check_same_thread=False)
        self.db.execute("PRAGMA busy_timeout=5000")
        if db != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        with self.db:
            # Keep the original SQLite table and add a backward-compatible
            # default column. No copy, drop or rewrite of saved chat records.
            self.db.execute("""CREATE TABLE IF NOT EXISTS agent_conversation (
                turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT NOT NULL,
                prompt TEXT NOT NULL,
                answer TEXT NOT NULL,
                conversation_id TEXT NOT NULL DEFAULT 'default',
                CHECK(length(prompt)<=3000),
                CHECK(length(answer)<=3000))""")
            columns = {row[1] for row in self.db.execute(
                "PRAGMA table_info(agent_conversation)").fetchall()}
            if "conversation_id" not in columns:
                self.db.execute("""ALTER TABLE agent_conversation
                    ADD COLUMN conversation_id TEXT NOT NULL DEFAULT 'default'""")
            self.db.execute("""CREATE INDEX IF NOT EXISTS
                idx_agent_conversation_lookup
                ON agent_conversation(conversation_id,turn_id)""")

    def close(self):
        with self.lock:
            self.db.close()

    def list_conversations(self):
        try:
            with self.lock:
                rows = self.db.execute("""SELECT conversation_id
                    FROM agent_conversation GROUP BY conversation_id
                    ORDER BY MAX(turn_id) DESC,conversation_id ASC
                    LIMIT ?""", (MAX_CONVERSATIONS,)).fetchall()
            return [row[0] for row in rows]
        except sqlite3.Error:
            raise ConversationError("conversation_storage_unavailable") from None

    def snapshot(self, conversation_id=DEFAULT_CONVERSATION):
        _require_id(conversation_id)
        try:
            with self.lock:
                rows = self.db.execute("""SELECT model,prompt,answer
                    FROM agent_conversation WHERE conversation_id=?
                    ORDER BY turn_id DESC LIMIT ?""",
                    (conversation_id,MAX_PAIRS)).fetchall()
            return [{"model": model, "prompt": prompt, "answer": answer}
                    for model, prompt, answer in reversed(rows)]
        except sqlite3.Error:
            raise ConversationError("conversation_storage_unavailable") from None

    def append(self, model, prompt, answer, conversation_id=DEFAULT_CONVERSATION):
        _require_id(conversation_id)
        if (type(model) is not str or not model or len(model) > 80
                or type(prompt) is not str or not 1 <= len(prompt) <= 3000
                or type(answer) is not str or not 1 <= len(answer) <= MAX_ANSWER):
            raise ConversationError("conversation_record_rejected")
        try:
            with self.lock, self.db:
                existing = self.db.execute("""SELECT 1 FROM agent_conversation
                    WHERE conversation_id=? LIMIT 1""", (conversation_id,)).fetchone()
                if existing is None:
                    count = self.db.execute("""SELECT COUNT(DISTINCT conversation_id)
                        FROM agent_conversation""").fetchone()[0]
                    if count >= MAX_CONVERSATIONS:
                        raise ConversationError("conversation_capacity_reached")
                self.db.execute("""INSERT INTO agent_conversation
                    (model,prompt,answer,conversation_id) VALUES(?,?,?,?)""",
                    (model,prompt,answer,conversation_id))
                self.db.execute("""DELETE FROM agent_conversation
                    WHERE conversation_id=? AND turn_id NOT IN (
                      SELECT turn_id FROM agent_conversation
                      WHERE conversation_id=? ORDER BY turn_id DESC LIMIT ?)""",
                    (conversation_id,conversation_id,MAX_PAIRS))
        except sqlite3.Error:
            raise ConversationError("conversation_storage_unavailable") from None

    def clear(self, conversation_id=None):
        # Legacy clear() means clear all; UI always sends a selected ID.
        if conversation_id is not None:
            _require_id(conversation_id)
        try:
            with self.lock, self.db:
                if conversation_id is None:
                    self.db.execute("DELETE FROM agent_conversation")
                else:
                    self.db.execute("""DELETE FROM agent_conversation
                        WHERE conversation_id=?""", (conversation_id,))
        except sqlite3.Error:
            raise ConversationError("conversation_storage_unavailable") from None
