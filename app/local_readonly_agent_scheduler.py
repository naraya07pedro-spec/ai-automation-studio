"""Owner-armed recurring LOCAL OLLAMA inference, read-only and crash-aware.

Separate worker from deterministic rules and outbound webhook dispatch. Real
model inference only through existing bounded local_ollama_agent.run_agent:
calculator/UTC tools, no filesystem, MCP, email or network destinations.
No automatic retry after timeout/crash; no unsupervised external writes.

Prompts and outputs are private but PLAINTEXT local SQLite. Explicit opt-in.
"""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import threading

from local_ollama_agent import (run_agent, valid_model_name, MAX_PROMPT,
                                AgentError)
from local_recurring_scheduler import (
    ALLOWED_ZONES, CADENCES, CLOCK_PATTERN, SCHEDULE_ID,
    _aware, _iso, next_occurrence, utcnow)
from local_model_safety import ModelSafetyError
from openai_readonly_responses import valid_remote_model_name

MAX_TASKS = 12
MAX_RUNS = 30
MAX_ANSWER = 6000
MAX_RESULTS_PER_TASK = 30
ALLOWED_TOOLS = frozenset(("calculate", "utc_now"))


class AgentScheduleError(RuntimeError):
    def __init__(self, reason, status=422):
        super().__init__(reason)
        self.status = status


def validate_cadence(data):
    if type(data) is not dict:
        raise AgentScheduleError("invalid_ai_schedule_cadence")
    common = {"cadence", "timezone"}
    if data.get("cadence") not in CADENCES or data.get("timezone") not in ALLOWED_ZONES:
        raise AgentScheduleError("invalid_ai_schedule_cadence")
    kind = data["cadence"]
    extra = ({"every"} if kind in ("minutes", "hours") else
             {"at"} | ({"weekday"} if kind == "weekly" else
                        {"monthday"} if kind == "monthly" else set()))
    if set(data) != common | extra:
        raise AgentScheduleError("invalid_ai_schedule_cadence_fields")
    if kind in ("minutes", "hours"):
        upper = 1440 if kind == "minutes" else 168
        if type(data["every"]) is not int or not 1 <= data["every"] <= upper:
            raise AgentScheduleError("invalid_ai_schedule_interval")
    else:
        if type(data["at"]) is not str or not CLOCK_PATTERN.fullmatch(data["at"]):
            raise AgentScheduleError("invalid_ai_schedule_time")
        if kind == "weekly" and (type(data["weekday"]) is not int
                                  or not 0 <= data["weekday"] <= 6):
            raise AgentScheduleError("invalid_ai_schedule_weekday")
        if kind == "monthly" and (type(data["monthday"]) is not int
                                  or not 1 <= data["monthday"] <= 31):
            raise AgentScheduleError("invalid_ai_schedule_monthday")
    return dict(data)


def _validate_create(request, remote_enabled=False, local_enabled=True):
    fields = {"prompt", "model", "cadence", "max_runs", "expires_days"}
    if type(request) is not dict or not fields.issubset(request) or not set(request) <= fields | {"provider", "paid_api_consent"}:
        raise AgentScheduleError("invalid_ai_task_fields")
    prompt, model = request["prompt"], request["model"]
    if type(prompt) is not str or not 1 <= len(prompt.strip()) <= MAX_PROMPT:
        raise AgentScheduleError("invalid_ai_task_prompt")
    provider = request.get("provider", "local_ollama")
    if provider == "openai_responses":
        if not remote_enabled:
            raise AgentScheduleError("remote_model_not_enabled", 403)
        if request.get("paid_api_consent") is not True:
            raise AgentScheduleError("paid_api_consent_required", 403)
        if not valid_remote_model_name(model):
            raise AgentScheduleError("invalid_remote_model_name")
        if (type(request.get("max_runs")) is not int
                or request["max_runs"] > 3
                or type(request.get("expires_days")) is not int
                or request["expires_days"] > 7):
            raise AgentScheduleError("remote_task_request_cap_exceeded")
    elif provider != "local_ollama":
        raise AgentScheduleError("invalid_ai_task_provider")
    elif "paid_api_consent" in request:
        raise AgentScheduleError("paid_api_consent_invalid_for_local_task")
    elif not local_enabled:
        raise AgentScheduleError("local_model_not_enabled", 403)
    elif not valid_model_name(model):
        raise AgentScheduleError("invalid_ai_task_model")
    if (type(request["max_runs"]) is not int or
            not 1 <= request["max_runs"] <= MAX_RUNS or
            type(request["expires_days"]) is not int or
            not 1 <= request["expires_days"] <= 90):
        raise AgentScheduleError("invalid_ai_task_limits")
    return validate_cadence(request["cadence"])


class LocalReadOnlyAgentSchedules:
    def __init__(self, database, runner=run_agent, clock=utcnow,
                 model_safety=None, agent_gate=None, agent_state_lock=None,
                 remote_runner=None, local_enabled=True):
        self.clock = clock
        self.runner = runner
        self.remote_runner = remote_runner
        if type(local_enabled) is not bool:
            raise AgentScheduleError("invalid_local_model_setting", 503)
        self.local_enabled = local_enabled
        self.model_safety = model_safety
        self.agent_gate = agent_gate
        self.agent_state_lock = agent_state_lock or threading.RLock()
        self.lock = threading.RLock()
        self.dispatch_lock = threading.Lock()
        self.stop_signal = threading.Event()
        self.thread = None
        location = str(database)
        if location != ":memory:":
            path = Path(database).expanduser()
            if path.is_symlink() or path.parent.is_symlink():
                raise AgentScheduleError("unsafe_ai_schedule_database", 503)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.parent.is_symlink():
                raise AgentScheduleError("unsafe_ai_schedule_database", 503)
            if os.name == "posix":
                os.chmod(path.parent, 0o700)
            if path.exists():
                if (not path.is_file() or
                        (os.name == "posix" and
                         stat.S_IMODE(path.stat().st_mode) & 0o077)):
                    raise AgentScheduleError("unsafe_ai_schedule_database", 503)
            else:
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
            location = str(path)
        self.db = sqlite3.connect(location, check_same_thread=False)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA synchronous=FULL")
        if location != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS ai_schedules(
                task_id TEXT PRIMARY KEY, prompt TEXT NOT NULL,
                model TEXT NOT NULL, config_json TEXT NOT NULL,
                status TEXT NOT NULL, next_due_utc TEXT NOT NULL,
                expires_utc TEXT NOT NULL, max_runs INTEGER NOT NULL,
                attempted_runs INTEGER NOT NULL DEFAULT 0,
                control_epoch INTEGER NOT NULL DEFAULT 0,
                created_utc TEXT NOT NULL, last_run_utc TEXT,
                provider TEXT NOT NULL DEFAULT 'local_ollama')""")
            schedule_columns = {row[1] for row in self.db.execute(
                "PRAGMA table_info(ai_schedules)").fetchall()}
            if "provider" not in schedule_columns:
                self.db.execute("ALTER TABLE ai_schedules ADD COLUMN provider "
                                "TEXT NOT NULL DEFAULT 'local_ollama'")
            self.db.execute("""CREATE TABLE IF NOT EXISTS ai_schedule_runs(
                task_id TEXT NOT NULL, slot_utc TEXT NOT NULL,
                status TEXT NOT NULL, answer TEXT, tools_json TEXT,
                error TEXT, claimed_utc TEXT NOT NULL, finished_utc TEXT,
                usage_json TEXT, PRIMARY KEY(task_id,slot_utc))""")
            history_columns = {row[1] for row in self.db.execute(
                "PRAGMA table_info(ai_schedule_runs)").fetchall()}
            if "usage_json" not in history_columns:
                self.db.execute("ALTER TABLE ai_schedule_runs "
                                "ADD COLUMN usage_json TEXT")
            self.db.execute("CREATE INDEX IF NOT EXISTS ai_due ON ai_schedules(status,next_due_utc)")
            # Ollama POST may have reached the model at process death.
            unsettled = self.db.execute("""SELECT DISTINCT task_id FROM ai_schedule_runs
                WHERE status='IN_FLIGHT'""").fetchall()
            self.db.execute("""UPDATE ai_schedule_runs SET status='OUTCOME_UNCERTAIN',
                error='model_process_interrupted',finished_utc=?
                WHERE status='IN_FLIGHT'""", (_iso(self.clock()),))
            for (tid,) in unsettled:
                self.db.execute("""UPDATE ai_schedules SET status='PAUSED_UNCERTAIN',
                    control_epoch=control_epoch+1 WHERE task_id=?""", (tid,))
            # Disabling the paid provider immediately pauses any older
            # remote tasks before their next potential billable due slot.
            if remote_runner is None:
                self.db.execute("""UPDATE ai_schedules
                    SET status='PAUSED_ERROR',control_epoch=control_epoch+1
                    WHERE provider='openai_responses' AND status='ACTIVE'""")
            # Switching to cloud-only must not dispatch or silently preserve
            # active Ollama tasks, and no legacy task is converted to paid.
            if not self.local_enabled:
                self.db.execute("""UPDATE ai_schedules
                    SET status='PAUSED_ERROR',control_epoch=control_epoch+1
                    WHERE provider='local_ollama' AND status='ACTIVE'""")

    def attach_gate(self, model_safety, agent_gate, agent_state_lock):
        """Called when creating the HTTP handler, before background start."""
        if model_safety is None or agent_gate is None or agent_state_lock is None:
            raise AgentScheduleError("model_safety_gate_required", 503)
        if self.agent_gate is not None and self.agent_gate is not agent_gate:
            raise AgentScheduleError("model_gate_already_attached", 503)
        self.model_safety = model_safety
        self.agent_gate = agent_gate
        self.agent_state_lock = agent_state_lock

    @staticmethod
    def _public(row):
        tid, prompt, model, raw, status, due, expires, limit, attempted, epoch, created, last, provider = row
        return {"task_id":tid, "prompt":prompt, "model":model,
                "provider":provider,
                "cadence":json.loads(raw),"status":status,
                "next_due_utc":due if status == "ACTIVE" else None,
                "expires_utc":expires,"max_runs":limit,
                "attempted_runs":attempted,"control_epoch":epoch,
                "created_utc":created,"last_run_utc":last,
                "external_dispatch_count":0,
                "model_tools":("NONE_REMOTE_TEXT_ONLY" if provider=="openai_responses"
                               else "LOCAL_READ_ONLY_CALCULATOR_UTC"),
                "remote_api_request_limit":limit if provider=="openai_responses" else 0}

    def _get(self, tid):
        if type(tid) is not str or not SCHEDULE_ID.fullmatch(tid):
            raise AgentScheduleError("invalid_ai_task_id")
        result = self.db.execute("""SELECT task_id,prompt,model,config_json,status,
            next_due_utc,expires_utc,max_runs,attempted_runs,control_epoch,
            created_utc,last_run_utc,provider FROM ai_schedules WHERE task_id=?""", (tid,)).fetchone()
        if result is None:
            raise AgentScheduleError("ai_task_not_found", 404)
        return self._public(result)

    def create(self, request):
        cadence = _validate_create(
            request, remote_enabled=self.remote_runner is not None,
            local_enabled=self.local_enabled)
        now = _aware(self.clock())
        expires = _iso(now + timedelta(days=request["expires_days"]))
        tid = secrets.token_hex(12)
        with self.lock, self.db:
            count = self.db.execute("SELECT count(*) FROM ai_schedules").fetchone()[0]
            if count >= MAX_TASKS:
                raise AgentScheduleError("ai_task_capacity_reached", 409)
            self.db.execute("""INSERT INTO ai_schedules(
                task_id,prompt,model,config_json,status,next_due_utc,expires_utc,
                max_runs,created_utc,provider)
                VALUES(?,?,?,?,'ACTIVE',?,?,?,?,?)""",
                (tid,request["prompt"].strip(),request["model"],
                 json.dumps(cadence,sort_keys=True),
                 _iso(next_occurrence(cadence,now)),expires,
                 request["max_runs"],_iso(now),
                 request.get("provider","local_ollama")))
            return self._get(tid)

    def list(self):
        with self.lock:
            rows=self.db.execute("""SELECT task_id,prompt,model,config_json,status,
                next_due_utc,expires_utc,max_runs,attempted_runs,control_epoch,
                created_utc,last_run_utc,provider FROM ai_schedules
                ORDER BY created_utc,task_id""").fetchall()
            return {"enabled":True,"tasks":[self._public(x) for x in rows],
                    "max_tasks":MAX_TASKS,
                    "worker_running":self.thread is not None and self.thread.is_alive(),
                    "local_model_only":self.remote_runner is None and self.local_enabled,
                    "local_ollama_enabled":self.local_enabled,
                    "remote_openai_enabled":self.remote_runner is not None,
                    "remote_request_cap_per_task":3,
                    "remote_tasks_billable_and_send_prompts_to_openai":self.remote_runner is not None,
                    "external_dispatch_count":0,
                    "plaintext_local_results":True}

    def history(self, tid):
        with self.lock:
            self._get(tid)
            rows=self.db.execute("""SELECT slot_utc,status,answer,tools_json,error,
                claimed_utc,finished_utc,usage_json FROM ai_schedule_runs WHERE task_id=?
                ORDER BY slot_utc DESC LIMIT ?""",
                (tid,MAX_RESULTS_PER_TASK)).fetchall()
        return {"task_id":tid,"runs":[{
            "scheduled_for_utc":slot,"status":status,"answer":answer,
            "tools_used":json.loads(tools) if tools is not None else [],
            "error":error,"claimed_utc":claimed,"finished_utc":finished,
            "usage_tokens":json.loads(usage_json) if usage_json else None,
            "external_dispatch_count":0}
            for slot,status,answer,tools,error,claimed,finished,usage_json in rows]}

    def change(self, request, *, pause):
        if (type(request) is not dict or
                set(request) != {"task_id", "expected_epoch"} or
                type(request["expected_epoch"]) is not int or
                request["expected_epoch"] < 0):
            raise AgentScheduleError("invalid_ai_task_change_fields")
        with self.lock, self.db:
            prior=self._get(request["task_id"])
            if prior["control_epoch"] != request["expected_epoch"]:
                raise AgentScheduleError("ai_task_epoch_conflict", 409)
            if not pause and prior["provider"]=="openai_responses" and self.remote_runner is None:
                raise AgentScheduleError("remote_model_not_enabled",403)
            if not pause and prior["provider"]=="local_ollama" and not self.local_enabled:
                raise AgentScheduleError("local_model_not_enabled",403)
            if pause:
                if prior["status"] != "ACTIVE":
                    raise AgentScheduleError("ai_task_not_active", 409)
                new_status,new_due="PAUSED",prior["next_due_utc"]
            else:
                if prior["status"] not in ("PAUSED", "PAUSED_ERROR", "PAUSED_UNCERTAIN"):
                    raise AgentScheduleError("ai_task_not_resumable", 409)
                if prior["attempted_runs"] >= prior["max_runs"]:
                    raise AgentScheduleError("ai_task_limit_exhausted", 409)
                if _aware(self.clock()) >= _aware(
                        datetime.fromisoformat(prior["expires_utc"])):
                    raise AgentScheduleError("ai_task_expired", 409)
                # A timeout/crash requires operator acknowledgement through
                # the existing model quarantine UI BEFORE this can resume.
                if self.model_safety is not None:
                    try:
                        if self.model_safety.phase() != "IDLE":
                            raise AgentScheduleError("model_checkpoint_not_cleared", 423)
                    except ModelSafetyError:
                        raise AgentScheduleError("model_safety_unavailable", 503) from None
                new_status="ACTIVE"
                new_due=_iso(next_occurrence(prior["cadence"],self.clock()))
            self.db.execute("""UPDATE ai_schedules SET status=?,next_due_utc=?,
                control_epoch=control_epoch+1 WHERE task_id=? AND control_epoch=?""",
                (new_status,new_due,request["task_id"],request["expected_epoch"]))
            return self._get(request["task_id"])

    def run_due(self):
        """One due request per call, strictly sequential; no backlog bursts."""
        if self.model_safety is None or self.agent_gate is None:
            raise AgentScheduleError("model_safety_gate_required", 503)
        if not self.dispatch_lock.acquire(blocking=False):
            return 0
        acquired=False
        try:
            # Idle polling must NOT churn the durable model safety checkpoint.
            # Only enter the shared inference gate if a due slot actually
            # exists. The second transaction below still claims atomically.
            with self.lock:
                pending=self.db.execute("""SELECT 1 FROM ai_schedules
                    WHERE status='ACTIVE' AND next_due_utc<=? LIMIT 1""",
                    (_iso(self.clock()),)).fetchone()
            if pending is None:
                return 0
            with self.agent_state_lock:
                acquired=self.agent_gate.acquire(blocking=False)
                if not acquired:
                    return 0
                try:
                    self.model_safety.begin()
                except ModelSafetyError:
                    return 0
            claimed=None
            with self.lock, self.db:
                now=_iso(self.clock())
                row=self.db.execute("""SELECT task_id,prompt,model,config_json,
                    next_due_utc,expires_utc,max_runs,attempted_runs,provider
                    FROM ai_schedules WHERE status='ACTIVE' AND next_due_utc<=?
                    ORDER BY next_due_utc,task_id LIMIT 1""",(now,)).fetchone()
                if row is not None:
                    tid,prompt,model,raw,slot,expiry,limit,attempted,provider=row
                    if now >= expiry or attempted >= limit:
                        self.db.execute("""UPDATE ai_schedules SET status=?,
                            control_epoch=control_epoch+1 WHERE task_id=?""",
                            ("EXPIRED" if now >= expiry else "EXHAUSTED",tid))
                    else:
                        next_due=_iso(next_occurrence(json.loads(raw),self.clock()))
                        self.db.execute("""INSERT INTO ai_schedule_runs(
                            task_id,slot_utc,status,claimed_utc) VALUES(?,?,'IN_FLIGHT',?)""",
                            (tid,slot,now))
                        newstatus="EXHAUSTED" if attempted+1 >= limit else "ACTIVE"
                        self.db.execute("""UPDATE ai_schedules SET attempted_runs=attempted_runs+1,
                            status=?,next_due_utc=?,last_run_utc=?
                            WHERE task_id=?""",(newstatus,next_due,now,tid))
                        claimed=(tid,slot,prompt,model,provider)
            if claimed is None:
                return 0
            tid,slot,prompt,model,provider=claimed
            status,answer,tools,error,usage="PAUSED_ERROR",None,None,None,None
            try:
                if provider=="openai_responses":
                    if self.remote_runner is None:
                        raise AgentError("remote_provider_disabled")
                    result=self.remote_runner(prompt,model)
                else:
                    if not self.local_enabled:
                        raise AgentError("local_provider_disabled")
                    result=self.runner(prompt,model)
                expected_mode=("REMOTE_OPENAI_READ_ONLY"
                               if provider=="openai_responses" else "LOCAL_MODEL")
                if (type(result) is not dict or result.get("mode")!=expected_mode
                        or result.get("external_dispatch_count") != 0
                        or type(result.get("answer")) is not str
                        or not 1 <= len(result["answer"].strip()) <= MAX_ANSWER
                        or type(result.get("tools_used")) is not list
                        or any(x not in ALLOWED_TOOLS for x in result["tools_used"])
                        or len(result["tools_used"]) > 8):
                    raise AgentScheduleError("invalid_readonly_ai_result", 503)
                if provider=="openai_responses":
                    usage=result.get("usage")
                    if (type(usage) is not dict
                            or set(usage)!={"input_tokens","output_tokens"}
                            or any(type(v) is not int or not 0<=v<=200000
                                   for v in usage.values())):
                        raise AgentScheduleError("invalid_remote_usage",503)
                status,answer,tools="COMPLETED",result["answer"],result["tools_used"]
            except AgentError as exc:
                error=str(exc) if str(exc) in (
                    "local_model_unavailable", "local_model_timeout",
                    "local_model_transport_uncertain","agent_step_limit",
                    "empty_model_answer","invalid_model_response",
                    "model_response_too_large","remote_provider_rejected",
                    "remote_response_uncertain","remote_provider_disabled",
                    "local_provider_disabled") else "local_ai_execution_failed"
                if str(exc) in ("local_model_timeout","local_model_transport_uncertain",
                                "remote_response_uncertain"):
                    status="PAUSED_UNCERTAIN"
                    try:
                        self.model_safety.timeout()
                    except ModelSafetyError:
                        pass
            except Exception:
                error="local_ai_execution_failed"
            with self.lock, self.db:
                self.db.execute("""UPDATE ai_schedule_runs SET status=?,answer=?,
                    tools_json=?,error=?,finished_utc=?,usage_json=?
                    WHERE task_id=? AND slot_utc=?
                    AND status='IN_FLIGHT'""",
                    (status,answer,json.dumps(tools) if tools is not None else None,
                     error,_iso(self.clock()),
                     json.dumps(usage,sort_keys=True) if usage is not None else None,
                     tid,slot))
                if status != "COMPLETED":
                    self.db.execute("""UPDATE ai_schedules SET status=?,
                        control_epoch=control_epoch+1
                        WHERE task_id=? AND status IN ('ACTIVE','EXHAUSTED')""",
                        (status,tid))
            return 1
        finally:
            if acquired:
                try:
                    self.model_safety.finish()
                except ModelSafetyError:
                    # Shared fail-closed checkpoint has already latched failure.
                    pass
                self.agent_gate.release()
            self.dispatch_lock.release()

    def _loop(self):
        while not self.stop_signal.is_set():
            try:
                self.run_due()
            except (AgentScheduleError, sqlite3.Error, OSError, ValueError):
                # Do not print prompt, answer, model name or secrets to stdout.
                print("Local AI scheduler: inference or storage unavailable.")
            self.stop_signal.wait(5)

    def start(self):
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return
            if self.model_safety is None or self.agent_gate is None:
                raise AgentScheduleError("model_safety_gate_required", 503)
            self.stop_signal.clear()
            self.thread=threading.Thread(
                target=self._loop,name="local-readonly-ai-scheduler",daemon=True)
            self.thread.start()

    def close(self):
        self.stop_signal.set()
        worker=self.thread
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=110)
            if worker.is_alive():
                raise AgentScheduleError("ai_scheduler_worker_still_active",503)
        with self.lock:
            self.db.close()
