"""Owner-armed, bounded automatic HTTPS dispatch for REAL local schedule results.

The recurring worker is the only execution entrypoint. A policy is permanently
bound to one active schedule, exact configuration/source version, and the one
server-configured HTTPS destination. It needs an owner preview + fresh owner
password to arm; the model cannot arm it. Every external attempt is claimed in
durable SQLite before network activity and is NEVER automatically retried.

This is a private single-process worker, NOT a distributed exactly-once queue.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import threading

from local_recurring_scheduler import SCHEDULE_ID, _aware, _iso

PREVIEW_SECONDS = 300
MAX_AUTOPILOTS = 24
MAX_SENDS_PER_GRANT = 30
MAX_VALID_DAYS = 90
MAX_DISPATCH_PER_TICK = 3


class AutopilotError(Exception):
    def __init__(self, code, status=422):
        super().__init__(code)
        self.status = status


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


class ScheduledWebhookAutopilot:
    def __init__(self, scheduler, outbound, audit=None, database=":memory:",
                 clock=lambda: datetime.now(timezone.utc),
                 dispatch_action="WEBHOOK_AUTOPILOT_DISPATCH"):
        if outbound is None or not outbound.host:
            raise AutopilotError("webhook_not_configured", 403)
        self.scheduler = scheduler
        self.outbound = outbound
        self.audit = audit
        self.dispatch_action = dispatch_action
        self.clock = clock
        self.lock = threading.RLock()
        self.dispatch_lock = threading.Lock()
        self._previews = {}
        if str(database) != ":memory:":
            path = Path(database).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            database = str(path)
        self.db = sqlite3.connect(str(database), check_same_thread=False)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA synchronous=FULL")
        if str(database) != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS scheduled_webhook_policies (
                schedule_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL,
                schedule_hash TEXT NOT NULL, source_version INTEGER NOT NULL,
                schedule_epoch INTEGER NOT NULL, destination_hash TEXT NOT NULL,
                not_before_utc TEXT NOT NULL, expires_utc TEXT NOT NULL,
                max_sends INTEGER NOT NULL, attempted INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL, control_epoch INTEGER NOT NULL DEFAULT 0,
                last_slot_utc TEXT, created_utc TEXT NOT NULL)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS scheduled_webhook_attempts (
                schedule_id TEXT NOT NULL, slot_utc TEXT NOT NULL,
                delivery_id TEXT NOT NULL, status TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL, http_status INTEGER,
                claimed_utc TEXT NOT NULL, finished_utc TEXT,
                PRIMARY KEY(schedule_id,slot_utc))""")
            # Crash recovery is fail-closed for external side effects. The old
            # IN_FLIGHT claim may already have reached n8n; no blind retry.
            unsettled = self.db.execute("""SELECT DISTINCT schedule_id FROM
                scheduled_webhook_attempts WHERE status='IN_FLIGHT'""").fetchall()
            self.db.execute("""UPDATE scheduled_webhook_attempts
                SET status='OUTCOME_UNCERTAIN',finished_utc=?
                WHERE status='IN_FLIGHT'""", (_iso(_aware(self.clock())),))
            for (sid,) in unsettled:
                self.db.execute("""UPDATE scheduled_webhook_policies
                    SET status='PAUSED_UNCERTAIN',control_epoch=control_epoch+1
                    WHERE schedule_id=? AND status='ACTIVE'""", (sid,))

    def _now(self):
        return _aware(self.clock())

    def _destination_hash(self):
        return _digest(self.outbound.url.encode("utf-8"))

    @staticmethod
    def _schedule_hash(schedule):
        return _digest(_canonical(schedule["config"]))

    def _source(self, schedule):
        source = self.scheduler._workflow(schedule["workflow_id"])
        if source is None or source["status"] != "ACTIVE":
            raise AutopilotError("source_workflow_not_active", 409)
        return source

    def _checked_schedule(self, sid):
        if type(sid) is not str or not SCHEDULE_ID.fullmatch(sid):
            raise AutopilotError("invalid_schedule_id")
        try:
            schedule = self.scheduler.get(sid)
        except Exception as exc:
            raise AutopilotError("schedule_not_found_or_unavailable", 404) from None
        if schedule["status"] != "ACTIVE" or not schedule["next_due_utc"]:
            raise AutopilotError("schedule_not_active", 409)
        return schedule

    @staticmethod
    def _caps(max_sends, expires_days):
        if (type(max_sends) is not int or not 1 <= max_sends <= MAX_SENDS_PER_GRANT
                or type(expires_days) is not int or not 1 <= expires_days <= MAX_VALID_DAYS):
            raise AutopilotError("invalid_autopilot_limits")

    def preview(self, request):
        if (type(request) is not dict or
                set(request) != {"schedule_id", "max_sends", "expires_days"}):
            raise AutopilotError("invalid_autopilot_preview_fields")
        sid = request["schedule_id"]
        max_sends, expires_days = request["max_sends"], request["expires_days"]
        self._caps(max_sends, expires_days)
        schedule = self._checked_schedule(sid)
        source = self._source(schedule)
        if self.audit is not None and not self.audit.ready():
            raise AutopilotError("audit_unavailable", 503)
        now = self._now()
        with self.lock:
            if self.db.execute("""SELECT 1 FROM scheduled_webhook_policies
                WHERE schedule_id=?""", (sid,)).fetchone():
                raise AutopilotError("schedule_policy_already_exists_create_new_schedule", 409)
            if self.db.execute("""SELECT count(*) FROM scheduled_webhook_policies""").fetchone()[0] >= MAX_AUTOPILOTS:
                raise AutopilotError("autopilot_capacity_reached", 409)
            token = secrets.token_urlsafe(32)
            record = {
                "schedule_id": sid,
                "schedule_hash": self._schedule_hash(schedule),
                "schedule_epoch": schedule["control_epoch"],
                "workflow_id": schedule["workflow_id"],
                "source_version": source["version"],
                "target_hash": self._destination_hash(),
                "not_before_utc": schedule["next_due_utc"],
                "max_sends": max_sends,
                "expires_days": expires_days,
                "expires_preview_utc": _iso(now + timedelta(seconds=PREVIEW_SECONDS)),
            }
            self._previews[sid] = (_digest(token.encode("ascii")), record)
        return {
            "status": "PREVIEW_ONLY",
            "schedule_id": sid, "workflow_id": record["workflow_id"],
            "cadence": schedule["config"]["cadence"],
            "schedule_config": schedule["config"],
            "source_workflow_version": record["source_version"],
            "destination_hostname": self.outbound.host,
            "first_eligible_utc": record["not_before_utc"],
            "max_external_posts": max_sends,
            "valid_for_days": expires_days,
            "preview_expires_in_seconds": PREVIEW_SECONDS,
            "preview_token": token,
            "external_write_count": 0,
            "warning": "Arming permits automatic real HTTPS POSTs without further prompts, within exact limits. 2xx is only endpoint acceptance."
        }

    def arm(self, request):
        if (type(request) is not dict or
                set(request) != {"schedule_id", "max_sends", "expires_days", "preview_token"}):
            raise AutopilotError("invalid_autopilot_arm_fields")
        sid, max_sends, days = request["schedule_id"], request["max_sends"], request["expires_days"]
        token = request["preview_token"]
        self._caps(max_sends, days)
        if type(token) is not str or len(token) != 43 or not token.isascii():
            raise AutopilotError("invalid_autopilot_preview_token")
        now = self._now()
        with self.lock:
            pending = self._previews.get(sid)
            if pending is None or not secrets.compare_digest(
                    pending[0], _digest(token.encode("ascii"))):
                raise AutopilotError("autopilot_preview_missing_or_changed", 409)
            record = pending[1]
            if (record["max_sends"] != max_sends or record["expires_days"] != days
                    or record["expires_preview_utc"] <= _iso(now)
                    or record["target_hash"] != self._destination_hash()):
                raise AutopilotError("autopilot_preview_expired_or_changed", 409)
            schedule = self._checked_schedule(sid)
            source = self._source(schedule)
            if (record["schedule_hash"] != self._schedule_hash(schedule)
                    or record["schedule_epoch"] != schedule["control_epoch"]
                    or record["workflow_id"] != schedule["workflow_id"]
                    or record["source_version"] != source["version"]
                    or record["not_before_utc"] != schedule["next_due_utc"]):
                raise AutopilotError("autopilot_schedule_changed_repreview", 409)
            with self.db:
                if self.db.execute("""SELECT 1 FROM scheduled_webhook_policies
                    WHERE schedule_id=?""", (sid,)).fetchone():
                    raise AutopilotError("schedule_policy_already_exists_create_new_schedule", 409)
                if self.db.execute("""SELECT count(*) FROM scheduled_webhook_policies""").fetchone()[0] >= MAX_AUTOPILOTS:
                    raise AutopilotError("autopilot_capacity_reached", 409)
                self.db.execute("""INSERT INTO scheduled_webhook_policies
                    (schedule_id,workflow_id,schedule_hash,source_version,
                    schedule_epoch,destination_hash,not_before_utc,expires_utc,
                    max_sends,status,created_utc)
                    VALUES(?,?,?,?,?,?,?,?,?,'ACTIVE',?)""",
                    (sid,record["workflow_id"],record["schedule_hash"],
                     record["source_version"],record["schedule_epoch"],
                     record["target_hash"],record["not_before_utc"],
                     _iso(now + timedelta(days=days)),max_sends,_iso(now)))
            self._previews.pop(sid, None)
            return self.get(sid)

    def _row(self, row):
        (sid,wid,status,valid,limit,attempted,epoch,not_before,last,
         created,target) = row
        return {
            "schedule_id": sid, "workflow_id": wid, "status": status,
            "expires_utc": valid, "max_external_posts": limit,
            "attempted_posts": attempted, "control_epoch": epoch,
            "first_eligible_utc": not_before, "last_slot_utc": last,
            "created_utc": created,
            "destination_matches_configuration": target == self._destination_hash(),
            "external_dispatch_count": attempted
        }

    def _get_locked(self, sid):
        row = self.db.execute("""SELECT schedule_id,workflow_id,status,
            expires_utc,max_sends,attempted,control_epoch,not_before_utc,
            last_slot_utc,created_utc,destination_hash
            FROM scheduled_webhook_policies WHERE schedule_id=?""", (sid,)).fetchone()
        if not row:
            raise AutopilotError("autopilot_policy_not_found", 404)
        return self._row(row)

    def get(self, sid):
        if type(sid) is not str or not SCHEDULE_ID.fullmatch(sid):
            raise AutopilotError("invalid_schedule_id")
        with self.lock:
            return self._get_locked(sid)

    def list(self):
        with self.lock:
            rows = self.db.execute("""SELECT schedule_id,workflow_id,status,
                expires_utc,max_sends,attempted,control_epoch,not_before_utc,
                last_slot_utc,created_utc,destination_hash
                FROM scheduled_webhook_policies ORDER BY created_utc DESC LIMIT ?""",
                (MAX_AUTOPILOTS,)).fetchall()
            attempts = self.db.execute("""SELECT schedule_id,slot_utc,status,
                delivery_id,http_status,claimed_utc,finished_utc
                FROM scheduled_webhook_attempts ORDER BY claimed_utc DESC LIMIT 30""").fetchall()
        return {
            "policies": [self._row(r) for r in rows],
            "attempts": [
                {"schedule_id":sid, "scheduled_for_utc":slot, "status":status,
                 "delivery_id":did, "http_status":code, "claimed_utc":claimed,
                 "finished_utc":finished}
                for sid,slot,status,did,code,claimed,finished in attempts],
            "limits": {"max_policies":MAX_AUTOPILOTS,
                       "max_posts_per_policy":MAX_SENDS_PER_GRANT,
                       "max_policy_days":MAX_VALID_DAYS},
            "configured_destination_hostname": self.outbound.host,
            "external_dispatch_count": sum(r[5] for r in rows),
        }

    def stop(self, sid, expected_epoch):
        if type(sid) is not str or not SCHEDULE_ID.fullmatch(sid):
            raise AutopilotError("invalid_schedule_id")
        if type(expected_epoch) is not int or expected_epoch < 0:
            raise AutopilotError("invalid_control_epoch")
        with self.lock, self.db:
            row = self._get_locked(sid)
            if row["control_epoch"] != expected_epoch:
                raise AutopilotError("autopilot_epoch_conflict", 409)
            if row["status"] != "ACTIVE":
                raise AutopilotError("autopilot_not_active", 409)
            self.db.execute("""UPDATE scheduled_webhook_policies
                SET status='STOPPED',control_epoch=control_epoch+1
                WHERE schedule_id=? AND control_epoch=? AND status='ACTIVE'""",
                (sid,expected_epoch))
            return self._get_locked(sid)

    def _halt(self, sid, status):
        with self.lock, self.db:
            self.db.execute("""UPDATE scheduled_webhook_policies
                SET status=?,control_epoch=control_epoch+1
                WHERE schedule_id=? AND status='ACTIVE'""", (status,sid))

    @staticmethod
    def _payload(schedule_id, receipt, workflow_id):
        slot = receipt["scheduled_for_utc"]
        did = _digest(("aas.scheduled.v1:"+schedule_id+":"+slot).encode("ascii"))[:32]
        return {
            "schema": "aas.scheduled_workflow_receipt.v1",
            "delivery_id": did, "schedule_id": schedule_id,
            "scheduled_for_utc": slot, "workflow_id": workflow_id,
            "workflow_version": receipt["workflow_version"],
            "decision": receipt["decision"], "reason": receipt["reason"],
            "finished_at": receipt["finished_utc"]
        }

    def _claim(self, sid, receipt, workflow_id):
        payload = self._payload(sid,receipt,workflow_id)
        slot = receipt["scheduled_for_utc"]
        now = _iso(self._now())
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                current = self._get_locked(sid)
                if (current["status"] != "ACTIVE"
                        or current["attempted_posts"] >= current["max_external_posts"]
                        or current["expires_utc"] <= now
                        or slot < current["first_eligible_utc"]
                        or current["last_slot_utc"] is not None and
                        slot <= current["last_slot_utc"]):
                    self.db.rollback()
                    return None
                self.db.execute("""INSERT OR IGNORE INTO scheduled_webhook_attempts
                    (schedule_id,slot_utc,delivery_id,status,payload_sha256,claimed_utc)
                    VALUES(?,?,?,'IN_FLIGHT',?,?)""",
                    (sid,slot,payload["delivery_id"],_digest(_canonical(payload)),now))
                if self.db.execute("SELECT changes()").fetchone()[0] != 1:
                    self.db.rollback()
                    return None
                self.db.execute("""UPDATE scheduled_webhook_policies
                    SET attempted=attempted+1,last_slot_utc=?
                    WHERE schedule_id=? AND status='ACTIVE' AND attempted<max_sends""",
                    (slot,sid))
                if self.db.execute("SELECT changes()").fetchone()[0] != 1:
                    self.db.rollback()
                    return None
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return payload

    def _complete(self, sid, slot, http_status, accepted):
        outcome = "REMOTE_ACCEPTED" if accepted else "OUTCOME_UNCERTAIN"
        now = _iso(self._now())
        with self.lock, self.db:
            self.db.execute("""UPDATE scheduled_webhook_attempts
                SET status=?,http_status=?,finished_utc=?
                WHERE schedule_id=? AND slot_utc=? AND status='IN_FLIGHT'""",
                (outcome,http_status,now,sid,slot))
            self.db.execute("""UPDATE scheduled_webhook_policies
                SET status=CASE
                    WHEN ?=0 THEN 'PAUSED_UNCERTAIN'
                    WHEN attempted>=max_sends THEN 'EXHAUSTED'
                    ELSE status END,
                control_epoch=CASE WHEN ?=0 OR attempted>=max_sends
                    THEN control_epoch+1 ELSE control_epoch END
                WHERE schedule_id=? AND status='ACTIVE'""",
                (int(accepted),int(accepted),sid))

    def _run_policy(self, record):
        sid = record["schedule_id"]
        now = _iso(self._now())
        # Any unresolved delivery freezes the entire policy, not only that
        # slot. A temporary SQLite finalize failure must not permit a second
        # outbound request on the next tick.
        with self.lock:
            unsettled = self.db.execute("""SELECT 1 FROM scheduled_webhook_attempts
                WHERE schedule_id=? AND status IN ('IN_FLIGHT','OUTCOME_UNCERTAIN')
                LIMIT 1""", (sid,)).fetchone()
        if unsettled is not None:
            self._halt(sid,"PAUSED_UNCERTAIN")
            return 0
        if record["expires_utc"] <= now:
            self._halt(sid,"EXPIRED")
            return 0
        if record["attempted_posts"] >= record["max_external_posts"]:
            self._halt(sid,"EXHAUSTED")
            return 0
        if not record["destination_matches_configuration"]:
            self._halt(sid,"STOPPED_DESTINATION_CHANGED")
            return 0
        try:
            schedule = self._checked_schedule(sid)
            source = self._source(schedule)
        except AutopilotError:
            self._halt(sid,"STOPPED_SOURCE_CHANGED")
            return 0
        with self.lock:
            row = self.db.execute("""SELECT schedule_hash,source_version,
                schedule_epoch FROM scheduled_webhook_policies
                WHERE schedule_id=? AND status='ACTIVE'""",(sid,)).fetchone()
        if (row is None or row[0] != self._schedule_hash(schedule)
                or row[1] != source["version"]
                or row[2] != schedule["control_epoch"]):
            self._halt(sid,"STOPPED_SOURCE_CHANGED")
            return 0
        runs = self.scheduler.history(sid)["runs"]
        receipt = next((r for r in runs if r["status"]=="COMPLETED"
                        and r["decision"] is not None
                        and r["scheduled_for_utc"] >= record["first_eligible_utc"]
                        and (record["last_slot_utc"] is None or
                             r["scheduled_for_utc"] > record["last_slot_utc"])),None)
        if not receipt:
            return 0
        # The latest receipt only: no backlog flood after downtime.
        payload = self._claim(sid,receipt,record["workflow_id"])
        if payload is None:
            return 0
        slot = receipt["scheduled_for_utc"]
        try:
            if self.audit is not None:
                self.audit.record(self.dispatch_action,"ATTEMPT")
            http_status = self.outbound.sender(
                self.outbound.url,payload,payload["delivery_id"],self.outbound.secret)
            accepted = type(http_status) is int and 200 <= http_status < 300
        except Exception:
            http_status, accepted = None, False
        self._complete(sid,slot,http_status,accepted)
        try:
            if self.audit is not None:
                self.audit.record(self.dispatch_action,
                                  "SUCCESS" if accepted else "UNCERTAIN")
        except Exception:
            # The external outcome remains in the immutable ledger; a broken
            # audit system must stop further autonomous dispatch.
            self._halt(sid,"STOPPED_AUDIT_UNAVAILABLE")
        return 1

    def dispatch_due(self):
        if not self.dispatch_lock.acquire(blocking=False):
            return 0
        try:
            with self.lock:
                rows = self.db.execute("""SELECT schedule_id,workflow_id,status,
                    expires_utc,max_sends,attempted,control_epoch,not_before_utc,
                    last_slot_utc,created_utc,destination_hash
                    FROM scheduled_webhook_policies WHERE status='ACTIVE'
                    ORDER BY created_utc""").fetchall()
            count = 0
            for row in rows:
                if count >= MAX_DISPATCH_PER_TICK:
                    break
                try:
                    count += self._run_policy(self._row(row))
                except Exception:
                    # The scheduler itself must remain alive. A malformed
                    # receipt or storage fault quarantines only this policy. No blind retry:
                    # the persisted IN_FLIGHT gate or source checks will
                    # stop further writes if the database recovers.
                    try:
                        self._halt(row[0],"PAUSED_UNCERTAIN")
                    except sqlite3.Error:
                        pass
            return count
        finally:
            self.dispatch_lock.release()

    def close(self):
        with self.lock:
            self.db.close()
