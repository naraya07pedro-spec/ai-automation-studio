"""Crash-aware, durable one-in-flight model safety checkpoint.

Stores only one enumerated state; never model names, prompts or documents.
Failure to read/write safety state must block model dispatch, not reset it.
"""
import os
from pathlib import Path
import sqlite3
import stat
import threading

STATES = frozenset(("IDLE", "IN_FLIGHT", "UNCERTAIN"))


class ModelSafetyError(RuntimeError):
    pass


class LocalModelSafetyState:
    """SQLite checkpoint committed BEFORE a model may be called.

    Reopening IN_FLIGHT converts it to UNCERTAIN, since a crashed process
    cannot prove the model stopped. A timeout also latches UNCERTAIN.
    Only explicit owner acknowledgement clears the uncertain state.
    """

    def __init__(self, database=":memory:"):
        self.lock = threading.RLock()
        self._failed = False
        location = str(database)
        if location != ":memory:":
            path = Path(database).expanduser()
            if path.is_symlink() or path.parent.is_symlink():
                raise ModelSafetyError("unsafe_model_safety_path")
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if path.parent.is_symlink():
                raise ModelSafetyError("unsafe_model_safety_path")
            if os.name == "posix":
                os.chmod(path.parent, 0o700)
            if path.exists():
                if not path.is_file():
                    raise ModelSafetyError("unsafe_model_safety_path")
                if (os.name == "posix"
                        and stat.S_IMODE(path.stat().st_mode) & 0o077):
                    raise ModelSafetyError("insecure_model_safety_permissions")
            else:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            location = str(path)
        try:
            self.db = sqlite3.connect(location, timeout=3,
                                      check_same_thread=False)
            self.db.execute("PRAGMA busy_timeout=3000")
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA synchronous=FULL")
            with self.lock, self.db:
                self.db.execute("""CREATE TABLE IF NOT EXISTS model_safety (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    phase TEXT NOT NULL CHECK(phase IN
                        ('IDLE','IN_FLIGHT','UNCERTAIN')))""")
                self.db.execute("INSERT OR IGNORE INTO model_safety(id,phase)"
                                " VALUES(1,'IDLE')")
                phase = self._read_unlocked()
                if phase == "IN_FLIGHT":
                    self.db.execute("UPDATE model_safety SET phase='UNCERTAIN'"
                                    " WHERE id=1")
        except (sqlite3.Error, OSError, ModelSafetyError):
            self._failed = True
            raise ModelSafetyError("model_safety_storage_unavailable") from None

    def _read_unlocked(self):
        row = self.db.execute(
            "SELECT phase FROM model_safety WHERE id=1").fetchone()
        if row is None or row[0] not in STATES:
            raise ModelSafetyError("model_safety_storage_unavailable")
        return row[0]

    def _transition(self, action):
        with self.lock:
            if self._failed:
                raise ModelSafetyError("model_safety_storage_unavailable")
            try:
                # Cross-process writers must serialize state transitions.
                self.db.execute("BEGIN IMMEDIATE")
                phase = self._read_unlocked()
                if action == "begin":
                    if phase != "IDLE":
                        self.db.rollback()
                        raise ModelSafetyError("model_safety_quarantined")
                    next_phase = "IN_FLIGHT"
                elif action == "finish":
                    next_phase = "IDLE" if phase == "IN_FLIGHT" else phase
                elif action == "timeout":
                    next_phase = "UNCERTAIN"
                elif action == "ack":
                    if phase == "IN_FLIGHT":
                        self.db.rollback()
                        raise ModelSafetyError("model_safety_active")
                    next_phase = "IDLE"
                else:
                    self.db.rollback()
                    raise ValueError("invalid_safety_action")
                self.db.execute("UPDATE model_safety SET phase=? WHERE id=1",
                                (next_phase,))
                self.db.commit()
                return next_phase
            except ModelSafetyError as exc:
                if exc.args[0] != "model_safety_quarantined" and exc.args[0] != "model_safety_active":
                    self._failed = True
                raise
            except sqlite3.Error:
                self.db.rollback()
                self._failed = True
                raise ModelSafetyError("model_safety_storage_unavailable") from None

    def phase(self):
        with self.lock:
            if self._failed:
                raise ModelSafetyError("model_safety_storage_unavailable")
            try:
                return self._read_unlocked()
            except (sqlite3.Error, ModelSafetyError):
                self._failed = True
                raise ModelSafetyError("model_safety_storage_unavailable") from None

    def begin(self):
        return self._transition("begin")

    def finish(self):
        return self._transition("finish")

    def timeout(self):
        return self._transition("timeout")

    def acknowledge(self):
        return self._transition("ack")

    def close(self):
        with self.lock:
            self.db.close()
