"""Single-owner, opt-in bounded local agent conversation ledger.

This is plaintext local SQLite, NOT a secret vault. No cloud sync or PII support.
Explicit enablement and explicit per-turn save are both required.
"""
import sqlite3
import threading
from pathlib import Path

MAX_PAIRS = 6
MAX_ANSWER = 3000


class ConversationError(Exception):
    pass


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
            self.db.execute("""CREATE TABLE IF NOT EXISTS agent_conversation (
                turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT NOT NULL,
                prompt TEXT NOT NULL,
                answer TEXT NOT NULL,
                CHECK(length(prompt)<=3000),
                CHECK(length(answer)<=3000))""")

    def close(self):
        with self.lock:
            self.db.close()

    def snapshot(self):
        try:
            with self.lock:
                rows = self.db.execute(
                    "SELECT model,prompt,answer FROM agent_conversation "
                    "ORDER BY turn_id DESC LIMIT ?", (MAX_PAIRS,)).fetchall()
            return [{"model": model, "prompt": prompt, "answer": answer}
                    for model, prompt, answer in reversed(rows)]
        except sqlite3.Error:
            raise ConversationError("conversation_storage_unavailable") from None

    def append(self, model, prompt, answer):
        if (type(model) is not str or not model or len(model) > 80
                or type(prompt) is not str or not 1 <= len(prompt) <= 3000
                or type(answer) is not str or not 1 <= len(answer) <= MAX_ANSWER):
            raise ConversationError("conversation_record_rejected")
        try:
            with self.lock, self.db:
                self.db.execute("INSERT INTO agent_conversation(model,prompt,answer) "
                                "VALUES(?,?,?)", (model,prompt,answer))
                self.db.execute("""DELETE FROM agent_conversation WHERE turn_id NOT IN
                    (SELECT turn_id FROM agent_conversation ORDER BY turn_id DESC LIMIT ?)""",
                                (MAX_PAIRS,))
        except sqlite3.Error:
            raise ConversationError("conversation_storage_unavailable") from None

    def clear(self):
        try:
            with self.lock, self.db:
                self.db.execute("DELETE FROM agent_conversation")
        except sqlite3.Error:
            raise ConversationError("conversation_storage_unavailable") from None
