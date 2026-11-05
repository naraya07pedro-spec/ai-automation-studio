"""Opt-in bounded AI task planning with explicit human approval and durable steps.

Only read-only calculator/UTC tools, no plugins or business writes. Plaintext
single-owner SQLite, intentionally separate from the conversation database.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading

from local_ollama_agent import (AgentError, calculate, execute_tool,
                                ollama_transport, valid_model_name)
from local_agent_github_reader import valid_repo, GitHubPlanReadError

MAX_STEPS = 3
MAX_PLANS = 12
MAX_GOAL = 1200
ID_PATTERN = re.compile(r"[0-9a-f]{24}\Z")
GITHUB_PLANNING_ADDENDUM = (
    ' Additional allowed read-only tool when explicitly enabled: '
    'github_public_issues with EXACT args {"repo":"owner/repository"}. '
    'It reads at most 15 open public GitHub issues from a repository '
    'the operator explicitly allowed in local source permissions. '
    'No arbitrary URLs, private repositories, issue edits or unrestricted browsing.'
)
PLANNING_SYSTEM = (
    "Build a short read-only tool plan for the user's request. "
    "Return ONLY JSON with a single key 'steps', an array of 1 to 3 objects. "
    "Each step must have EXACT keys 'tool' and 'args'. "
    "Allowed tool: calculate with string a,b and operation add/subtract/multiply/divide. "
    "Allowed tool: utc_now with empty args object. No other tool names or actions. "
    "If the request cannot be fulfilled with the tools listed here, return {\"steps\":[]}. "
    "Do not pretend to send messages, modify files or use unrestricted external APIs."
)


class PlanError(Exception):
    pass


def _validate_goal_model(goal, model):
    if (type(goal) is not str or not 1 <= len(goal.strip()) <= MAX_GOAL
            or not valid_model_name(model)):
        raise PlanError("invalid_plan_request")


def validate_steps(steps):
    if type(steps) is not list or not 1 <= len(steps) <= MAX_STEPS:
        raise PlanError("invalid_model_plan")
    checked = []
    for item in steps:
        if (type(item) is not dict or set(item) != {"tool", "args"}
                or type(item["tool"]) is not str or type(item["args"]) is not dict):
            raise PlanError("invalid_model_plan")
        tool, args = item["tool"], item["args"]
        if tool == "calculate":
            try:
                calculate(args)  # Arithmetic preflight only; no side effects.
            except AgentError:
                raise PlanError("invalid_model_plan") from None
        elif tool == "utc_now":
            if args:
                raise PlanError("invalid_model_plan")
        elif tool == "github_public_issues":
            if set(args) != {"repo"} or valid_repo(args.get("repo")) is None:
                raise PlanError("invalid_model_plan")
        else:
            raise PlanError("tool_not_allowed")
        checked.append({"tool": tool, "args": args})
    return checked


def propose_readonly_plan(goal, model, transport=None, allow_github=False):
    """Actual local model inference with fixed JSON output and zero tool execution."""
    _validate_goal_model(goal, model)
    request = {
        "model": model,
        "messages": [{"role": "system", "content":
                     PLANNING_SYSTEM + (GITHUB_PLANNING_ADDENDUM if allow_github else "")},
                     {"role": "user", "content": goal}],
        "stream": False,
        "format": "json",
        "options": {"num_predict": 450},
    }
    reply = (transport or ollama_transport)(request)
    if (type(reply) is not dict or type(reply.get("message")) is not dict
            or type(reply["message"].get("content")) is not str
            or len(reply["message"]["content"]) > 8000):
        raise PlanError("invalid_model_plan")
    try:
        data = json.loads(reply["message"]["content"])
    except (ValueError, UnicodeError):
        raise PlanError("invalid_model_plan") from None
    if type(data) is not dict or set(data) != {"steps"}:
        raise PlanError("invalid_model_plan")
    steps = validate_steps(data["steps"])
    if not allow_github and any(step["tool"] == "github_public_issues" for step in steps):
        raise PlanError("tool_not_allowed")
    return {"model": model, "goal": goal, "steps": steps}


def _require_id(value):
    if type(value) is not str or ID_PATTERN.fullmatch(value) is None:
        raise PlanError("invalid_plan_id")


class LocalAgentPlanStore:
    """Persistent state machine. No automatic dispatch on restart or approval."""
    def __init__(self, database=":memory:", github_reader=None):
        location = str(database)
        if location != ":memory:":
            path = Path(database).expanduser()
            if path.is_symlink():
                raise PlanError("plan_path_rejected")
            path.parent.mkdir(parents=True, exist_ok=True)
            location = str(path)
        self.lock = threading.RLock()
        self.github_reader = github_reader
        try:
            self.db = sqlite3.connect(location, check_same_thread=False)
            self.db.execute("PRAGMA busy_timeout=5000")
            if location != ":memory:":
                self.db.execute("PRAGMA journal_mode=WAL")
            with self.lock, self.db:
                self.db.execute("""CREATE TABLE IF NOT EXISTS agent_task_plans (
                    plan_id TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    results_json TEXT NOT NULL,
                    next_index INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_utc TEXT NOT NULL,
                    approval_epoch INTEGER)""")
                columns = {col[1] for col in self.db.execute(
                    "PRAGMA table_info(agent_task_plans)").fetchall()}
                if "approval_epoch" not in columns:
                    self.db.execute("""ALTER TABLE agent_task_plans
                        ADD COLUMN approval_epoch INTEGER""")
                # A process death leaves an explicitly visible interruption,
                # rather than silently executing a plan again.
                self.db.execute("""UPDATE agent_task_plans SET status='INTERRUPTED'
                    WHERE status='IN_FLIGHT'""")
        except sqlite3.Error:
            raise PlanError("plan_storage_unavailable") from None

    def close(self):
        with self.lock:
            self.db.close()

    def _fetch(self, plan_id):
        _require_id(plan_id)
        row = self.db.execute("""SELECT model,goal,steps_json,results_json,
                next_index,status,created_utc,approval_epoch FROM agent_task_plans
                WHERE plan_id=?""", (plan_id,)).fetchone()
        if row is None:
            raise PlanError("plan_not_found")
        try:
            steps = validate_steps(json.loads(row[2]))
            results = json.loads(row[3])
        except (TypeError, ValueError, PlanError):
            raise PlanError("plan_storage_invalid") from None
        if (type(results) is not list or len(results) > MAX_STEPS
                or type(row[4]) is not int or not 0 <= row[4] <= len(steps)
                or len(results) != row[4]):
            raise PlanError("plan_storage_invalid")
        return {"plan_id": plan_id, "model": row[0], "goal": row[1],
                "steps": steps, "results": results,
                "next_index": row[4], "status": row[5],
                "created_utc": row[6],
                "approval_epoch": row[7]}

    def get(self, plan_id):
        try:
            with self.lock:
                return self._fetch(plan_id)
        except sqlite3.Error:
            raise PlanError("plan_storage_unavailable") from None

    def list(self):
        try:
            with self.lock:
                rows = self.db.execute("""SELECT plan_id,status,goal,created_utc
                    FROM agent_task_plans ORDER BY created_utc DESC,plan_id DESC
                    LIMIT ?""", (MAX_PLANS,)).fetchall()
            return [{"plan_id": pid, "status": status,
                     "goal": goal, "created_utc": created}
                    for pid, status, goal, created in rows]
        except sqlite3.Error:
            raise PlanError("plan_storage_unavailable") from None

    def create(self, proposed):
        if type(proposed) is not dict or set(proposed) != {"model", "goal", "steps"}:
            raise PlanError("invalid_model_plan")
        _validate_goal_model(proposed["goal"], proposed["model"])
        steps = validate_steps(proposed["steps"])
        if any(step["tool"] == "github_public_issues" for step in steps):
            if self.github_reader is None:
                raise PlanError("tool_not_allowed")
            for step in steps:
                if step["tool"] == "github_public_issues":
                    try:
                        self.github_reader.check(step["args"]["repo"])
                    except GitHubPlanReadError:
                        raise PlanError("public_source_not_allowed") from None
        pid = secrets.token_hex(12)
        created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            with self.lock, self.db:
                count = self.db.execute("SELECT COUNT(*) FROM agent_task_plans").fetchone()[0]
                if count >= MAX_PLANS:
                    raise PlanError("plan_capacity_reached")
                self.db.execute("""INSERT INTO agent_task_plans
                    (plan_id,model,goal,steps_json,results_json,next_index,status,created_utc)
                    VALUES(?,?,?,?,?,0,'PREPARED',?)""",
                    (pid, proposed["model"], proposed["goal"],
                     json.dumps(steps, separators=(",", ":")), "[]", created))
                return self._fetch(pid)
        except sqlite3.Error:
            raise PlanError("plan_storage_unavailable") from None

    def approve(self, plan_id):
        try:
            with self.lock, self.db:
                old = self._fetch(plan_id)
                if old["status"] != "PREPARED":
                    raise PlanError("plan_state_conflict")
                epoch = None
                if any(step["tool"] == "github_public_issues" for step in old["steps"]):
                    if self.github_reader is None:
                        raise PlanError("tool_not_allowed")
                    epoch = self.github_reader.version()
                    for step in old["steps"]:
                        if step["tool"] == "github_public_issues":
                            try:
                                self.github_reader.check(step["args"]["repo"], epoch)
                            except GitHubPlanReadError:
                                raise PlanError("public_source_not_allowed") from None
                self.db.execute("""UPDATE agent_task_plans
                    SET status='APPROVED',approval_epoch=? WHERE plan_id=?""",
                                (epoch, plan_id))
                return self._fetch(plan_id)
        except sqlite3.Error:
            raise PlanError("plan_storage_unavailable") from None

    def resume_interrupted(self, plan_id):
        try:
            with self.lock, self.db:
                old = self._fetch(plan_id)
                if old["status"] != "INTERRUPTED":
                    raise PlanError("plan_state_conflict")
                new_status = "APPROVED" if old["next_index"] == 0 else "NEXT_READY"
                self.db.execute("UPDATE agent_task_plans SET status=? WHERE plan_id=?",
                                (new_status, plan_id))
                return self._fetch(plan_id)
        except sqlite3.Error:
            raise PlanError("plan_storage_unavailable") from None

    def advance(self, plan_id):
        """One explicitly triggered, preapproved read-only action only."""
        try:
            with self.lock, self.db:
                old = self._fetch(plan_id)
                if old["status"] not in ("APPROVED", "NEXT_READY"):
                    raise PlanError("plan_state_conflict")
                index = old["next_index"]
                if index >= len(old["steps"]):
                    raise PlanError("plan_state_conflict")
                step = old["steps"][index]
                if step["tool"] == "github_public_issues":
                    if self.github_reader is None:
                        raise PlanError("tool_not_allowed")
                    try:
                        self.github_reader.check(step["args"]["repo"],
                                                 old["approval_epoch"])
                    except GitHubPlanReadError:
                        raise PlanError("public_source_permission_changed") from None
                self.db.execute("""UPDATE agent_task_plans
                    SET status='IN_FLIGHT' WHERE plan_id=?""", (plan_id,))
            # The durable IN_FLIGHT marker is committed before tool invocation.
            try:
                if step["tool"] == "github_public_issues":
                    output = self.github_reader.read(
                        step["args"]["repo"], old["approval_epoch"])
                else:
                    output = execute_tool(step["tool"], step["args"])
            except Exception:
                with self.lock, self.db:
                    self.db.execute("""UPDATE agent_task_plans
                        SET status='BLOCKED' WHERE plan_id=? AND status='IN_FLIGHT'""",
                        (plan_id,))
                raise PlanError("plan_step_failed") from None
            with self.lock, self.db:
                results = old["results"] + [{"tool": step["tool"], "output": output}]
                new_index = index + 1
                state = "COMPLETED" if new_index == len(old["steps"]) else "NEXT_READY"
                self.db.execute("""UPDATE agent_task_plans
                    SET results_json=?,next_index=?,status=?
                    WHERE plan_id=? AND status='IN_FLIGHT'""",
                    (json.dumps(results), new_index, state, plan_id))
                return self._fetch(plan_id)
        except sqlite3.Error:
            raise PlanError("plan_storage_unavailable") from None

    def cancel(self, plan_id):
        try:
            with self.lock, self.db:
                old = self._fetch(plan_id)
                if old["status"] not in ("PREPARED", "APPROVED", "NEXT_READY",
                                         "INTERRUPTED", "BLOCKED"):
                    raise PlanError("plan_state_conflict")
                self.db.execute("""UPDATE agent_task_plans SET status='CANCELLED'
                    WHERE plan_id=?""", (plan_id,))
                return self._fetch(plan_id)
        except sqlite3.Error:
            raise PlanError("plan_storage_unavailable") from None

    def delete(self, plan_id):
        try:
            with self.lock, self.db:
                old = self._fetch(plan_id)
                if old["status"] not in ("COMPLETED", "CANCELLED", "BLOCKED"):
                    raise PlanError("plan_state_conflict")
                self.db.execute("DELETE FROM agent_task_plans WHERE plan_id=?",
                                (plan_id,))
                return {"status": "DELETED", "plan_id": plan_id}
        except sqlite3.Error:
            raise PlanError("plan_storage_unavailable") from None
