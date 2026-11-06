"""Durable private recurring scheduler for REAL local deterministic workflows.

Minute/hour/day/week/month schedules, persistent SQLite state and results,
one owner-visible worker thread. No model calls, external providers, subprocess,
network requests, email, CRM, webhook, MCP, or user-selected executable code.

Explicit operational semantics:
- "At most one" catch-up occurrence per schedule when waking after downtime.
- Every claimed occurrence has an immutable rule snapshot in a durable ledger.
- IN_FLIGHT claims are safe to re-evaluate after restart because evaluation is
  pure/deterministic; never add external side effects to this worker.
- Successful/blocked lifetime counters survive rotation of old history rows.
"""
import calendar
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
from local_workflow_core import ID_PATTERN, SERVICES, TIMELINES, _evaluate_rule

MAX_SCHEDULES = 24
MAX_HISTORY_PER_SCHEDULE = 2000
MAX_CLAIMS_PER_TICK = 8
ALLOWED_ZONES = {"UTC": timezone.utc, "Asia/Jakarta": timezone(timedelta(hours=7))}
CLOCK_PATTERN = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$", re.ASCII)
SCHEDULE_ID = re.compile(r"^[a-f0-9]{24}$", re.ASCII)
CADENCES = ("minutes", "hours", "daily", "weekly", "monthly")


class ScheduleError(Exception):
    def __init__(self, reason, status=422):
        super().__init__(reason)
        self.status = status


def utcnow():
    return datetime.now(timezone.utc)


def _aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ScheduleError("invalid_scheduler_clock")
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _iso(value):
    return _aware(value).isoformat(timespec="seconds")


def _parse(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def next_occurrence(config, after):
    """Return the first FUTURE due instant, with month-end clamp, in UTC."""
    after = _aware(after)
    kind = config["cadence"]
    if kind == "minutes":
        return after + timedelta(minutes=config["every"])
    if kind == "hours":
        return after + timedelta(hours=config["every"])
    zone = ALLOWED_ZONES[config["timezone"]]
    local = after.astimezone(zone)
    hh, mm = map(int, config["at"].split(":"))
    if kind == "daily":
        candidate = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(days=1)
    elif kind == "weekly":
        candidate = (local + timedelta(days=(config["weekday"] - local.weekday()) % 7))
        candidate = candidate.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(days=7)
    else:
        if kind != "monthly":
            raise ScheduleError("invalid_schedule_cadence")
        year, month = local.year, local.month
        while True:
            day = min(config["monthday"], calendar.monthrange(year, month)[1])
            candidate = local.replace(year=year, month=month, day=day,
                                      hour=hh, minute=mm, second=0, microsecond=0)
            if candidate > local:
                break
            year += month // 12
            month = month % 12 + 1
    return candidate.astimezone(timezone.utc)


def validate_config(data):
    required = {"workflow_id", "cadence", "timezone", "service_code",
                "budget_tier", "timeline"}
    extra = {"every", "at", "weekday", "monthday"}
    if type(data) is not dict or not required.issubset(data) or not set(data) <= required | extra:
        raise ScheduleError("invalid_schedule_fields")
    if type(data["workflow_id"]) is not str or not ID_PATTERN.fullmatch(data["workflow_id"]):
        raise ScheduleError("invalid_workflow_id")
    if type(data["cadence"]) is not str or data["cadence"] not in CADENCES:
        raise ScheduleError("invalid_schedule_cadence")
    if type(data["timezone"]) is not str or data["timezone"] not in ALLOWED_ZONES:
        raise ScheduleError("invalid_schedule_timezone")
    if type(data["service_code"]) is not str or data["service_code"] not in SERVICES:
        raise ScheduleError("invalid_service_code")
    if type(data["budget_tier"]) is not int or data["budget_tier"] not in (1, 2, 3):
        raise ScheduleError("invalid_budget_tier")
    if type(data["timeline"]) is not str or data["timeline"] not in TIMELINES:
        raise ScheduleError("invalid_timeline")
    kind = data["cadence"]
    expected = ({"every"} if kind in ("minutes", "hours") else {"at"}
                | ({"weekday"} if kind == "weekly" else {"monthday"} if kind == "monthly" else set()))
    if set(data) != required | expected:
        raise ScheduleError("invalid_schedule_cadence_fields")
    result = dict(data)
    if kind in ("minutes", "hours"):
        maximum = 1440 if kind == "minutes" else 168
        if type(data["every"]) is not int or not 1 <= data["every"] <= maximum:
            raise ScheduleError("invalid_schedule_interval")
    else:
        if type(data["at"]) is not str or not CLOCK_PATTERN.fullmatch(data["at"]):
            raise ScheduleError("invalid_schedule_time")
        if kind == "weekly" and (type(data["weekday"]) is not int
                                 or not 0 <= data["weekday"] <= 6):
            raise ScheduleError("invalid_schedule_weekday")
        if kind == "monthly" and (type(data["monthday"]) is not int
                                  or not 1 <= data["monthday"] <= 31):
            raise ScheduleError("invalid_schedule_monthday")
    return result


class LocalRecurringScheduler:
    def __init__(self, core, database=":memory:", clock=utcnow):
        self.core = core
        self.clock = clock
        self.lock = threading.RLock()
        self.stop_signal = threading.Event()
        self.thread = None
        if str(database) != ":memory:":
            path = Path(database).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            database = str(path)
        self.db = sqlite3.connect(str(database), check_same_thread=False)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        if str(database) != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS recurring_schedules (
                schedule_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL,
                config_json TEXT NOT NULL, status TEXT NOT NULL,
                next_due_utc TEXT NOT NULL, created_utc TEXT NOT NULL,
                last_run_utc TEXT, completed_runs INTEGER NOT NULL DEFAULT 0,
                blocked_runs INTEGER NOT NULL DEFAULT 0,
                control_epoch INTEGER NOT NULL DEFAULT 0)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS recurring_schedule_runs (
                schedule_id TEXT NOT NULL, slot_utc TEXT NOT NULL,
                status TEXT NOT NULL, snapshot_json TEXT NOT NULL,
                decision TEXT, reason TEXT, workflow_version INTEGER,
                finished_utc TEXT, PRIMARY KEY(schedule_id,slot_utc),
                FOREIGN KEY(schedule_id) REFERENCES recurring_schedules(schedule_id))""")
            self.db.execute("""CREATE INDEX IF NOT EXISTS recurring_due_idx
                ON recurring_schedules(status,next_due_utc)""")

    def _workflow(self, wid):
        rows = self.core.list_workflows()["workflows"]
        return next((w for w in rows if w["workflow_id"] == wid), None)

    def create(self, request):
        config = validate_config(request)
        workflow = self._workflow(config["workflow_id"])
        if workflow is None:
            raise ScheduleError("workflow_not_found", 404)
        if workflow["status"] != "ACTIVE":
            raise ScheduleError("workflow_not_active", 409)
        now = _aware(self.clock())
        due = next_occurrence(config, now)
        sid = secrets.token_hex(12)
        with self.lock, self.db:
            count = self.db.execute("SELECT count(*) FROM recurring_schedules").fetchone()[0]
            if count >= MAX_SCHEDULES:
                raise ScheduleError("schedule_capacity_reached", 409)
            self.db.execute("""INSERT INTO recurring_schedules
                (schedule_id, workflow_id, config_json, status, next_due_utc, created_utc)
                VALUES(?,?,?,'ACTIVE',?,?)""",
                (sid, config["workflow_id"], json.dumps(config, sort_keys=True),
                 _iso(due), _iso(now)))
        return self.get(sid)

    def _row(self, row):
        sid,wid,config,status,due,created,last,completed,blocked,epoch = row
        return {"schedule_id": sid, "workflow_id": wid,
                "config": json.loads(config), "status": status,
                "next_due_utc": due if status == "ACTIVE" else None,
                "created_utc": created, "last_run_utc": last,
                "completed_runs": completed, "blocked_runs": blocked,
                "control_epoch": epoch, "external_dispatch_count": 0}

    def _get_locked(self, sid):
        row = self.db.execute("""SELECT schedule_id,workflow_id,config_json,
            status,next_due_utc,created_utc,last_run_utc,
            completed_runs,blocked_runs,control_epoch
            FROM recurring_schedules WHERE schedule_id=?""", (sid,)).fetchone()
        if row is None:
            raise ScheduleError("schedule_not_found", 404)
        return self._row(row)

    def get(self, sid):
        if type(sid) is not str or not SCHEDULE_ID.fullmatch(sid):
            raise ScheduleError("invalid_schedule_id")
        with self.lock:
            return self._get_locked(sid)

    def list(self):
        with self.lock:
            rows = self.db.execute("""SELECT schedule_id,workflow_id,config_json,
                status,next_due_utc,created_utc,last_run_utc,
                completed_runs,blocked_runs,control_epoch
                FROM recurring_schedules ORDER BY created_utc,schedule_id""").fetchall()
        return {"schedules": [self._row(row) for row in rows],
                "max_schedules": MAX_SCHEDULES,
                "engine": "PERSISTENT_LOCAL_DETERMINISTIC",
                "worker_running": self.thread is not None and self.thread.is_alive(),
                "external_dispatch_count": 0}

    def change(self, sid, expected_epoch, *, pause):
        if type(sid) is not str or not SCHEDULE_ID.fullmatch(sid):
            raise ScheduleError("invalid_schedule_id")
        if type(expected_epoch) is not int or expected_epoch < 0:
            raise ScheduleError("invalid_control_epoch")
        workflow = None
        with self.lock:
            prior = self._get_locked(sid)
            if prior["control_epoch"] != expected_epoch:
                raise ScheduleError("schedule_epoch_conflict", 409)
            if prior["status"] != ("ACTIVE" if pause else "PAUSED"):
                raise ScheduleError("schedule_state_conflict", 409)
            if not pause:
                workflow = self._workflow(prior["workflow_id"])
                if workflow is None or workflow["status"] != "ACTIVE":
                    raise ScheduleError("workflow_not_active", 409)
            now = _aware(self.clock())
            due = (_iso(next_occurrence(prior["config"], now))
                   if not pause else (prior["next_due_utc"] or _iso(now)))
            with self.db:
                self.db.execute("""UPDATE recurring_schedules
                    SET status=?,next_due_utc=?,control_epoch=control_epoch+1
                    WHERE schedule_id=? AND control_epoch=?""",
                    ("PAUSED" if pause else "ACTIVE", due, sid, expected_epoch))
            return self._get_locked(sid)

    def history(self, sid):
        self.get(sid)
        with self.lock:
            rows = self.db.execute("""SELECT slot_utc,status,decision,
                reason,workflow_version,finished_utc FROM recurring_schedule_runs
                WHERE schedule_id=? ORDER BY slot_utc DESC LIMIT 25""", (sid,)).fetchall()
        return {"schedule_id": sid, "runs": [
            {"scheduled_for_utc": slot, "status": status, "decision": decision,
             "reason": reason, "workflow_version": version,
             "finished_utc": finished}
            for slot,status,decision,reason,version,finished in rows],
            "history_retention_per_schedule": MAX_HISTORY_PER_SCHEDULE,
            "external_dispatch_count": 0}

    def _finish(self, sid, slot, snapshot):
        now = _iso(self.clock())
        try:
            if snapshot.get("status") != "ACTIVE":
                decision, reason, version = None, "WORKFLOW_NOT_ACTIVE", snapshot.get("version")
                outcome = "BLOCKED"
            else:
                decision, reason = _evaluate_rule(
                    snapshot["service_filter"], snapshot["min_budget_tier"],
                    snapshot["followup_policy"], snapshot["service_code"],
                    snapshot["budget_tier"], snapshot["timeline"])
                version = snapshot["version"]
                outcome = "COMPLETED"
        except (KeyError, TypeError, ValueError):
            outcome, decision, reason, version = "BLOCKED", None, "EVALUATION_INVALID", None
        with self.lock, self.db:
            self.db.execute("""UPDATE recurring_schedule_runs
                SET status=?,decision=?,reason=?,workflow_version=?,finished_utc=?
                WHERE schedule_id=? AND slot_utc=? AND status='IN_FLIGHT'""",
                (outcome,decision,reason,version,now,sid,slot))
            changed = self.db.execute("SELECT changes()").fetchone()[0]
            if changed:
                self.db.execute("""UPDATE recurring_schedules
                    SET last_run_utc=?,
                    completed_runs=completed_runs+?,
                    blocked_runs=blocked_runs+?,
                    status=CASE WHEN ?='BLOCKED' THEN 'PAUSED' ELSE status END
                    WHERE schedule_id=?""",
                    (now, int(outcome == "COMPLETED"), int(outcome == "BLOCKED"),
                     outcome,sid))
                self.db.execute("""DELETE FROM recurring_schedule_runs WHERE rowid IN (
                    SELECT rowid FROM recurring_schedule_runs WHERE schedule_id=?
                    ORDER BY slot_utc DESC LIMIT -1 OFFSET ?)""",
                    (sid, MAX_HISTORY_PER_SCHEDULE))

    def run_due(self):
        """Called by owner process worker; safe to call manually in isolated tests."""
        now = _aware(self.clock())
        claims = []
        with self.lock, self.db:
            # Recovery first. Only pure deterministic actions can be replayed.
            pending = self.db.execute("""SELECT schedule_id,slot_utc,snapshot_json
                FROM recurring_schedule_runs WHERE status='IN_FLIGHT'
                ORDER BY slot_utc LIMIT ?""", (MAX_CLAIMS_PER_TICK,)).fetchall()
            for sid, slot, snapshot in pending:
                claims.append((sid, slot, json.loads(snapshot)))
            capacity = MAX_CLAIMS_PER_TICK - len(claims)
            if capacity:
                due_rows = self.db.execute("""SELECT schedule_id,workflow_id,
                    config_json,next_due_utc FROM recurring_schedules
                    WHERE status='ACTIVE' AND next_due_utc<=?
                    ORDER BY next_due_utc,schedule_id LIMIT ?""",
                    (_iso(now), capacity)).fetchall()
                for sid,wid,raw,slot in due_rows:
                    config = json.loads(raw)
                    workflow = self._workflow(wid)
                    snapshot = ({"status": workflow["status"],
                                 "service_filter": workflow["service_filter"],
                                 "min_budget_tier": workflow["min_budget_tier"],
                                 "followup_policy": workflow["followup_policy"],
                                 "version": workflow["version"]}
                                if workflow is not None else {"status": "MISSING"})
                    snapshot.update(service_code=config["service_code"],
                                    budget_tier=config["budget_tier"],
                                    timeline=config["timeline"])
                    self.db.execute("""INSERT OR IGNORE INTO recurring_schedule_runs
                        (schedule_id,slot_utc,status,snapshot_json)
                        VALUES(?,?,'IN_FLIGHT',?)""",
                        (sid, slot, json.dumps(snapshot, sort_keys=True)))
                    changed = self.db.execute("SELECT changes()").fetchone()[0]
                    if changed:
                        next_due = _iso(next_occurrence(config, now))
                        self.db.execute("""UPDATE recurring_schedules
                            SET next_due_utc=? WHERE schedule_id=?""", (next_due,sid))
                        claims.append((sid,slot,snapshot))
        for sid,slot,snapshot in claims:
            self._finish(sid,slot,snapshot)
        return len(claims)

    def _loop(self):
        while not self.stop_signal.is_set():
            try:
                self.run_due()
            except (sqlite3.Error, ScheduleError, OSError, ValueError):
                # Do not expose schedule content in logs. Tick errors are
                # not treated as completed; next iteration may retry.
                print("Local recurring worker: storage or schedule tick unavailable.")
            self.stop_signal.wait(5)

    def start(self):
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return
            self.stop_signal.clear()
            self.thread = threading.Thread(
                target=self._loop, name="local-recurring-scheduler", daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_signal.set()
        worker = self.thread
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=12)
            if worker.is_alive():
                raise RuntimeError("scheduler_worker_did_not_stop")

    def close(self):
        self.stop()
        with self.lock:
            self.db.close()
