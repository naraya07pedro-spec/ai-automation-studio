"""First usable LOCAL rule-workflow core, separate from synthetic mock demos.

Owns immutable v1 rule definitions, explicit activation, transactional SQLite
event identity, bounded local execution history and restart recovery. No AI,
connectors, identity, sending, personal-data fields or network access.

This is *not* a secure multi-user or production automation engine.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading


ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,39}$", re.ASCII)
EVENT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,60}$", re.ASCII)
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ]{2,49}$", re.ASCII)
SERVICES = frozenset(("automation", "integration", "consulting"))
TIMELINES = frozenset(("urgent", "soon", "flexible"))
POLICIES = frozenset(("review_required", "draft_only"))
MAX_WORKFLOWS = 50
MAX_RUNS_PER_WORKFLOW = 500
HISTORY_PAGE = 25
MAX_REVISIONS = 30


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _error(code, status=422):
    return status, {"status": "BLOCKED", "error": code,
                    "external_dispatch_count": 0}


def _evaluate_rule(service_filter, min_budget, policy, service, budget, timeline):
    """One deterministic decision function shared by manual and CSV intake."""
    if service_filter != "any" and service_filter != service:
        return "NOT_QUALIFIED_LOCAL", "SERVICE_MISMATCH"
    if budget < min_budget:
        return "NOT_QUALIFIED_LOCAL", "BUDGET_BELOW_THRESHOLD"
    if timeline == "flexible":
        return "NEEDS_REVIEW_LOCAL", "TIMELINE_UNSPECIFIED"
    if policy == "review_required":
        return "NEEDS_REVIEW_LOCAL", "POLICY_REQUIRES_REVIEW"
    return "QUALIFIED_DRAFT_ONLY", "MATCHED_LOCAL_RULES"


def _event_fingerprint(request):
    """Stable fingerprint matches old v1 manual-event semantics."""
    return hashlib.sha256(json.dumps(request, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")).hexdigest()


class LocalWorkflowCore:
    """Operator-controlled, single-machine SQLite rules and evidence ledger."""

    def __init__(self, database=":memory:"):
        if str(database) != ":memory:":
            path = Path(database).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            database = str(path)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(database), check_same_thread=False)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA foreign_keys=ON")
        if str(database) != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS local_workflows (
                workflow_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                service_filter TEXT NOT NULL, min_budget_tier INTEGER NOT NULL,
                followup_policy TEXT NOT NULL, version INTEGER NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL,
                activated_at TEXT)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS local_runs (
                workflow_id TEXT NOT NULL, event_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, workflow_version INTEGER NOT NULL,
                service_code TEXT NOT NULL, budget_tier INTEGER NOT NULL,
                timeline TEXT NOT NULL, decision TEXT NOT NULL,
                reason TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(workflow_id,event_id),
                FOREIGN KEY(workflow_id) REFERENCES local_workflows(workflow_id))""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS local_workflow_revisions (
                workflow_id TEXT NOT NULL, version INTEGER NOT NULL,
                service_filter TEXT NOT NULL, min_budget_tier INTEGER NOT NULL,
                followup_policy TEXT NOT NULL, state TEXT NOT NULL,
                created_at TEXT NOT NULL, activated_at TEXT,
                PRIMARY KEY(workflow_id,version),
                FOREIGN KEY(workflow_id) REFERENCES local_workflows(workflow_id))""")
            # Compatible, non-destructive migration from pre-v2 SQLite files.
            # Existing active rules and previous run evidence stay unchanged.
            columns = {row[1] for row in self.db.execute(
                "PRAGMA table_info(local_workflows)").fetchall()}
            if "control_epoch" not in columns:
                self.db.execute("""ALTER TABLE local_workflows
                    ADD COLUMN control_epoch INTEGER NOT NULL DEFAULT 0""")
            self.db.execute("""INSERT OR IGNORE INTO local_workflow_revisions (
                workflow_id,version,service_filter,min_budget_tier,
                followup_policy,state,created_at,activated_at)
                SELECT workflow_id,version,service_filter,min_budget_tier,
                followup_policy,CASE WHEN status='DRAFT' THEN 'DRAFT'
                ELSE 'ACTIVE' END,created_at,activated_at FROM local_workflows""")

    def close(self):
        with self._lock:
            self.db.close()

    def create(self, request):
        if type(request) is not dict or set(request) != {
                "workflow_id", "display_name", "service_filter",
                "min_budget_tier", "followup_policy"}:
            return _error("invalid_workflow_fields")
        wid = request["workflow_id"]
        name = request["display_name"]
        service = request["service_filter"]
        budget = request["min_budget_tier"]
        policy = request["followup_policy"]
        if type(wid) is not str or not ID_PATTERN.fullmatch(wid):
            return _error("invalid_workflow_id")
        if type(name) is not str or not NAME_PATTERN.fullmatch(name):
            return _error("invalid_display_name")
        if type(service) is not str or service not in SERVICES | {"any"}:
            return _error("invalid_service_filter")
        if type(budget) is not int or budget not in (1, 2, 3):
            return _error("invalid_budget_tier")
        if type(policy) is not str or policy not in POLICIES:
            return _error("invalid_followup_policy")
        with self._lock, self.db:
            existing = self.db.execute(
                "SELECT 1 FROM local_workflows WHERE workflow_id=?", (wid,)).fetchone()
            if existing:
                return _error("workflow_id_exists", 409)
            if self.db.execute("SELECT count(*) FROM local_workflows").fetchone()[0] >= MAX_WORKFLOWS:
                return _error("workflow_capacity_reached", 409)
            self.db.execute("""INSERT INTO local_workflows
                (workflow_id,display_name,service_filter,min_budget_tier,
                 followup_policy,version,status,created_at)
                VALUES(?,?,?,?,?,1,'DRAFT',?)""",
                (wid, name, service, budget, policy, _utc()))
            self.db.execute("""INSERT INTO local_workflow_revisions (
                workflow_id,version,service_filter,min_budget_tier,
                followup_policy,state,created_at)
                SELECT workflow_id,version,service_filter,min_budget_tier,
                followup_policy,'DRAFT',created_at FROM local_workflows
                WHERE workflow_id=?""", (wid,))
        return 201, {"status": "CREATED_DRAFT", "workflow_id": wid,
                     "version": 1, "external_dispatch_count": 0}

    def activate(self, request):
        if type(request) is not dict or set(request) != {"workflow_id", "expected_version"}:
            return _error("invalid_activation_fields")
        wid, version = request["workflow_id"], request["expected_version"]
        if type(wid) is not str or not ID_PATTERN.fullmatch(wid) or type(version) is not int:
            return _error("invalid_activation_fields")
        with self._lock, self.db:
            row = self.db.execute(
                "SELECT version,status FROM local_workflows WHERE workflow_id=?",
                (wid,)).fetchone()
            if row is None:
                return _error("workflow_not_found", 404)
            if row[0] != version:
                return _error("workflow_version_conflict", 409)
            if row[1] != "DRAFT":
                return _error("workflow_already_activated", 409)
            when = _utc()
            self.db.execute("""UPDATE local_workflows SET status='ACTIVE',
                activated_at=?,control_epoch=control_epoch+1
                WHERE workflow_id=? AND status='DRAFT'""", (when, wid))
            self.db.execute("""UPDATE local_workflow_revisions
                SET state='ACTIVE',activated_at=?
                WHERE workflow_id=? AND version=? AND state='DRAFT'""",
                (when, wid, version))
        return 200, {"status": "ACTIVE_LOCAL", "workflow_id": wid,
                     "version": version, "external_dispatch_count": 0}


    @staticmethod
    def _valid_rule_params(request):
        return (type(request["service_filter"]) is str
                and request["service_filter"] in SERVICES | {"any"}
                and type(request["min_budget_tier"]) is int
                and request["min_budget_tier"] in (1, 2, 3)
                and type(request["followup_policy"]) is str
                and request["followup_policy"] in POLICIES)

    def stage_revision(self, request):
        """Prepare a bounded immutable draft; existing active rules do not move."""
        fields = {"workflow_id", "expected_version", "service_filter",
                  "min_budget_tier", "followup_policy"}
        if type(request) is not dict or set(request) != fields:
            return _error("invalid_revision_fields")
        wid, expected = request["workflow_id"], request["expected_version"]
        if (type(wid) is not str or not ID_PATTERN.fullmatch(wid)
                or type(expected) is not int or expected < 1
                or not self._valid_rule_params(request)):
            return _error("invalid_revision_fields")
        with self._lock, self.db:
            row = self.db.execute("""SELECT version,status FROM local_workflows
                WHERE workflow_id=?""", (wid,)).fetchone()
            if row is None:
                return _error("workflow_not_found", 404)
            if row[0] != expected:
                return _error("workflow_version_conflict", 409)
            if row[1] not in ("ACTIVE", "PAUSED"):
                return _error("workflow_not_activated", 409)
            revisions = self.db.execute("""SELECT version,state FROM
                local_workflow_revisions WHERE workflow_id=?
                ORDER BY version""", (wid,)).fetchall()
            if len(revisions) >= MAX_REVISIONS:
                return _error("workflow_revision_capacity_reached", 409)
            if any(state == "DRAFT" for _, state in revisions):
                return _error("pending_revision_exists", 409)
            new_version = max(version for version, _ in revisions) + 1
            self.db.execute("""INSERT INTO local_workflow_revisions (
                workflow_id,version,service_filter,min_budget_tier,
                followup_policy,state,created_at) VALUES (?,?,?,?,?,'DRAFT',?)""",
                (wid,new_version,request["service_filter"],
                 request["min_budget_tier"],request["followup_policy"],_utc()))
        return 201, {"status": "REVISION_DRAFTED", "workflow_id": wid,
                     "active_version": expected, "draft_version": new_version,
                     "external_dispatch_count": 0}

    def publish_revision(self, request):
        """Atomically swap rules; preserve prior immutable evidence and pause."""
        if type(request) is not dict or set(request) != {
                "workflow_id", "expected_version", "draft_version"}:
            return _error("invalid_publish_fields")
        wid = request["workflow_id"]
        expected, target = request["expected_version"], request["draft_version"]
        if (type(wid) is not str or not ID_PATTERN.fullmatch(wid)
                or type(expected) is not int or expected < 1
                or type(target) is not int or target <= expected):
            return _error("invalid_publish_fields")
        with self._lock, self.db:
            row = self.db.execute("""SELECT version,status FROM local_workflows
                WHERE workflow_id=?""", (wid,)).fetchone()
            if row is None:
                return _error("workflow_not_found", 404)
            if row[0] != expected:
                return _error("workflow_version_conflict", 409)
            if row[1] not in ("ACTIVE", "PAUSED"):
                return _error("workflow_not_activated", 409)
            draft = self.db.execute("""SELECT service_filter,min_budget_tier,
                followup_policy,state FROM local_workflow_revisions
                WHERE workflow_id=? AND version=?""", (wid,target)).fetchone()
            if draft is None or draft[3] != "DRAFT" or target != expected + 1:
                return _error("draft_version_not_found", 409)
            when = _utc()
            self.db.execute("""UPDATE local_workflow_revisions SET state='ARCHIVED'
                WHERE workflow_id=? AND version=? AND state='ACTIVE'""",
                (wid,expected))
            self.db.execute("""UPDATE local_workflow_revisions
                SET state='ACTIVE',activated_at=?
                WHERE workflow_id=? AND version=? AND state='DRAFT'""",
                (when,wid,target))
            self.db.execute("""UPDATE local_workflows SET
                service_filter=?,min_budget_tier=?,followup_policy=?,
                version=?,control_epoch=control_epoch+1
                WHERE workflow_id=? AND version=?""",
                (draft[0],draft[1],draft[2],target,wid,expected))
        return 200, {"status": "REVISION_PUBLISHED_LOCAL",
                     "workflow_id": wid, "version": target,
                     "execution_state": row[1],
                     "external_dispatch_count": 0}

    def set_paused(self, request, pause):
        """Pause blocks new evaluations; no cancellation of completed writes."""
        if type(request) is not dict or set(request) != {
                "workflow_id", "expected_version", "expected_epoch"}:
            return _error("invalid_control_fields")
        wid = request["workflow_id"]
        version, epoch = request["expected_version"], request["expected_epoch"]
        if (type(wid) is not str or not ID_PATTERN.fullmatch(wid)
                or type(version) is not int or version < 1
                or type(epoch) is not int or epoch < 0):
            return _error("invalid_control_fields")
        with self._lock, self.db:
            row = self.db.execute("""SELECT version,status,control_epoch
                FROM local_workflows WHERE workflow_id=?""", (wid,)).fetchone()
            if row is None:
                return _error("workflow_not_found", 404)
            if row[0] != version or row[2] != epoch:
                return _error("workflow_control_conflict", 409)
            source, dest = ("ACTIVE","PAUSED") if pause else ("PAUSED","ACTIVE")
            if row[1] != source:
                return _error("invalid_workflow_transition", 409)
            self.db.execute("""UPDATE local_workflows SET status=?,
                control_epoch=control_epoch+1
                WHERE workflow_id=? AND version=? AND control_epoch=?""",
                (dest,wid,version,epoch))
        return 200, {"status": "PAUSED_LOCAL" if pause else "RESUMED_LOCAL",
                     "workflow_id": wid, "version": version,
                     "control_epoch": epoch + 1, "external_dispatch_count": 0}

    def revisions(self, wid):
        if type(wid) is not str or not ID_PATTERN.fullmatch(wid):
            return _error("invalid_workflow_id")
        with self._lock:
            exists = self.db.execute("""SELECT 1 FROM local_workflows
                WHERE workflow_id=?""", (wid,)).fetchone()
            if exists is None:
                return _error("workflow_not_found", 404)
            rows = self.db.execute("""SELECT version,service_filter,
                min_budget_tier,followup_policy,state,created_at,activated_at
                FROM local_workflow_revisions WHERE workflow_id=?
                ORDER BY version""", (wid,)).fetchall()
        return 200, {"status": "LOCAL_REVISIONS", "workflow_id": wid,
                     "revisions": [
                        {"version": v, "service_filter": s,
                         "min_budget_tier": b, "followup_policy": p,
                         "state": st, "created_at": created,
                         "activated_at": activated}
                        for v,s,b,p,st,created,activated in rows],
                     "external_dispatch_count": 0}

    def run(self, request):
        fields = {"workflow_id", "event_id", "service_code",
                  "budget_tier", "timeline"}
        if type(request) is not dict or set(request) != fields:
            return _error("invalid_run_fields")
        wid, eid = request["workflow_id"], request["event_id"]
        service, budget, timeline = (request["service_code"],
                                     request["budget_tier"], request["timeline"])
        if type(wid) is not str or not ID_PATTERN.fullmatch(wid):
            return _error("invalid_workflow_id")
        if type(eid) is not str or not EVENT_PATTERN.fullmatch(eid):
            return _error("invalid_event_id")
        if type(service) is not str or service not in SERVICES:
            return _error("invalid_service_code")
        if type(budget) is not int or budget not in (1, 2, 3):
            return _error("invalid_budget_tier")
        if type(timeline) is not str or timeline not in TIMELINES:
            return _error("invalid_timeline")
        fingerprint = _event_fingerprint(request)
        with self._lock, self.db:
            workflow = self.db.execute("""SELECT service_filter,min_budget_tier,
                followup_policy,version,status FROM local_workflows WHERE workflow_id=?""",
                (wid,)).fetchone()
            if workflow is None:
                return _error("workflow_not_found", 404)
            if workflow[4] != "ACTIVE":
                return _error("workflow_not_active", 409)
            prior = self.db.execute("""SELECT fingerprint,workflow_version,
                decision,reason,created_at FROM local_runs
                WHERE workflow_id=? AND event_id=?""", (wid, eid)).fetchone()
            if prior is not None:
                if prior[0] != fingerprint:
                    return _error("event_id_payload_conflict", 409)
                return 200, {"status": "REPLAYED_LOCAL", "workflow_id": wid,
                             "event_id": eid, "workflow_version": prior[1],
                             "decision": prior[2], "reason": prior[3],
                             "created_at": prior[4], "external_dispatch_count": 0}
            if self.db.execute("SELECT count(*) FROM local_runs WHERE workflow_id=?",
                               (wid,)).fetchone()[0] >= MAX_RUNS_PER_WORKFLOW:
                return _error("run_capacity_reached", 409)
            service_filter, min_budget, policy, version, _ = workflow
            decision, reason = _evaluate_rule(
                service_filter, min_budget, policy, service, budget, timeline)
            created_at = _utc()
            self.db.execute("""INSERT INTO local_runs
                (workflow_id,event_id,fingerprint,workflow_version,
                 service_code,budget_tier,timeline,decision,reason,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (wid, eid, fingerprint, version, service, budget,
                 timeline, decision, reason, created_at))
        return 200, {"status": "RECORDED_LOCAL", "workflow_id": wid,
                     "event_id": eid, "workflow_version": version,
                     "decision": decision, "reason": reason,
                     "created_at": created_at, "external_dispatch_count": 0}

    def get_run(self, wid, eid):
        """Fetch exactly one already-recorded deterministic local receipt."""
        if type(wid) is not str or not ID_PATTERN.fullmatch(wid):
            return _error("invalid_workflow_id")
        if type(eid) is not str or not EVENT_PATTERN.fullmatch(eid):
            return _error("invalid_event_id")
        with self._lock:
            row = self.db.execute("""SELECT workflow_version,decision,reason,created_at
                FROM local_runs WHERE workflow_id=? AND event_id=?""",
                (wid, eid)).fetchone()
        if row is None:
            return _error("local_run_not_found", 404)
        return 200, {"workflow_id": wid, "event_id": eid,
                     "workflow_version": row[0], "decision": row[1],
                     "reason": row[2], "created_at": row[3],
                     "external_dispatch_count": 0}

    def list_workflows(self):
        with self._lock:
            rows = self.db.execute("""SELECT w.workflow_id,w.display_name,
                w.service_filter,w.min_budget_tier,w.followup_policy,
                w.version,w.status,w.control_epoch,count(r.event_id),
                (SELECT MAX(rv.version) FROM local_workflow_revisions rv
                 WHERE rv.workflow_id=w.workflow_id AND rv.state='DRAFT')
                FROM local_workflows w LEFT JOIN local_runs r
                ON w.workflow_id=r.workflow_id GROUP BY w.workflow_id
                ORDER BY w.created_at,w.workflow_id LIMIT ?""",
                (MAX_WORKFLOWS,)).fetchall()
        return {"mode": "LOCAL_RULE_CORE", "workflows": [
            {"workflow_id": a, "display_name": b, "service_filter": c,
             "min_budget_tier": d, "followup_policy": e, "version": f,
             "status": g, "control_epoch": h, "run_count": i,
             "pending_version": j}
            for a, b, c, d, e, f, g, h, i, j in rows],
            "external_dispatch_count": 0}

    def history(self, wid):
        if type(wid) is not str or not ID_PATTERN.fullmatch(wid):
            return _error("invalid_workflow_id")
        with self._lock:
            exists = self.db.execute(
                "SELECT 1 FROM local_workflows WHERE workflow_id=?", (wid,)).fetchone()
            if not exists:
                return _error("workflow_not_found", 404)
            total = self.db.execute("SELECT count(*) FROM local_runs WHERE workflow_id=?",
                                    (wid,)).fetchone()[0]
            rows = self.db.execute("""SELECT event_id,workflow_version,
                service_code,budget_tier,timeline,decision,reason,created_at
                FROM local_runs WHERE workflow_id=? ORDER BY rowid DESC LIMIT ?""",
                (wid, HISTORY_PAGE)).fetchall()
        return 200, {"status": "LOCAL_HISTORY", "workflow_id": wid,
                     "total": total, "page_size": HISTORY_PAGE,
                     "runs": [{"event_id": a, "workflow_version": b,
                               "service_code": c, "budget_tier": d, "timeline": e,
                               "decision": f, "reason": g, "created_at": h}
                              for a, b, c, d, e, f, g, h in rows],
                     "external_dispatch_count": 0}
