"""Owner-controlled durable DAG queue for EXISTING approved read-only agent plans.

No background worker, model-generated permission, external write, paid model
request or automatic retry. Each explicit owner dispatch advances at most ONE
already-approved plan step. A durable claim precedes tool execution; a crash
is review-required instead of a replay instruction.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading

from local_agent_task_plans import PlanError, ID_PATTERN as PLAN_ID
from local_model_safety import ModelSafetyError
from local_agent_delegation import validate_delegation, DelegationError
from local_tool_registry import BATCH_READ_TOOLS

JOB_ID = re.compile(r"^[a-z][a-z0-9_-]{2,39}$", re.ASCII)
MAX_JOBS = 16
MAX_NODES = 8
MAX_EVENTS = 2000
MAX_LABEL = 80


class DurableQueueError(RuntimeError):
    def __init__(self, reason, status=409):
        super().__init__(reason)
        self.status = status


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _valid_id(value):
    if type(value) is not str or not JOB_ID.fullmatch(value):
        raise DurableQueueError("invalid_job_id", 422)
    return value


def _validate_create(data):
    if (type(data) is not dict or set(data) not in (
            {"job_id", "label", "priority", "nodes"},
            {"job_id", "label", "priority", "nodes", "delegation"})):
        raise DurableQueueError("invalid_job_fields", 422)
    jid = _valid_id(data["job_id"])
    label = data["label"]
    if (type(label) is not str or not 3 <= len(label.strip()) <= MAX_LABEL
            or any(ord(c) < 32 or ord(c) == 127 for c in label)):
        raise DurableQueueError("invalid_job_label", 422)
    priority = data["priority"]
    if type(priority) is not int or not 0 <= priority <= 3:
        raise DurableQueueError("invalid_job_priority", 422)
    raw = data["nodes"]
    if type(raw) is not list or not 2 <= len(raw) <= MAX_NODES:
        raise DurableQueueError("invalid_job_nodes", 422)
    nodes = []
    seen = set()
    for node in raw:
        if type(node) is not dict or set(node) != {"plan_id", "depends_on"}:
            raise DurableQueueError("invalid_job_node", 422)
        pid, deps = node["plan_id"], node["depends_on"]
        if type(pid) is not str or not PLAN_ID.fullmatch(pid) or pid in seen:
            raise DurableQueueError("invalid_job_node", 422)
        if (type(deps) is not list or len(deps) > MAX_NODES
                or any(type(d) is not str or not PLAN_ID.fullmatch(d) for d in deps)
                or len(set(deps)) != len(deps) or pid in deps):
            raise DurableQueueError("invalid_job_dependencies", 422)
        seen.add(pid)
        nodes.append({"plan_id": pid, "depends_on": deps})
    graph = {n["plan_id"]: n["depends_on"] for n in nodes}
    if any(d not in graph for deps in graph.values() for d in deps):
        raise DurableQueueError("job_dependency_not_in_job", 422)
    visiting, visited = set(), set()
    def visit(node):
        if node in visiting:
            raise DurableQueueError("job_dependency_cycle", 422)
        if node in visited:
            return
        visiting.add(node)
        for dep in graph[node]:
            visit(dep)
        visiting.remove(node)
        visited.add(node)
    for key in graph:
        visit(key)
    return jid, label.strip(), priority, nodes, data.get("delegation")


def _fingerprint(plan):
    """Digest immutable plan intent and source-selection metadata, not step results."""
    return _digest({k: plan[k] for k in (
        "plan_id", "model", "goal", "steps", "created_utc",
        "approval_epoch", "hn_approval_epoch", "memory_refs",
        "document_source", "document_set_sources", "csv_source")})


def _security_fingerprint(plan):
    """Frozen permission metadata, separate from old intent hashes."""
    return _digest({k: plan[k] for k in (
        "agent_binding", "scoped_memory_refs", "memory_refs",
        "document_source", "document_set_sources", "csv_source"
    )})


def _versioned_security_fingerprint(plan):
    """Bind newly enrolled jobs to full paid-plan origin, preserving old hashes."""
    return "v2:" + _digest({
        "legacy_security_sha256": _security_fingerprint(plan),
        "cloud_origin": plan["cloud_origin"],
    })


def _security_matches(plan, recorded):
    # Old durable jobs store an unprefixed SHA-256. Do not silently migrate
    # their approved intent or pretend old jobs had source-origin pinning.
    if recorded.startswith("v2:"):
        return recorded == _versioned_security_fingerprint(plan)
    return recorded == _security_fingerprint(plan)


class LocalDurablePlanQueue:
    """A single-machine owner-driven DAG with crash quarantine and fair priority."""

    def __init__(self, plans, model_safety, gate, gate_lock, database):
        if any(item is None for item in (plans, model_safety, gate, gate_lock)):
            raise DurableQueueError("job_dependencies_unavailable", 403)
        self.plans, self.model_safety = plans, model_safety
        self.gate, self.gate_lock = gate, gate_lock
        self.lock = threading.RLock()
        if str(database) == ":memory:":
            name = ":memory:"
        else:
            path = Path(database).expanduser()
            if path.is_symlink() or path.parent.is_symlink():
                raise DurableQueueError("job_unsafe_storage", 503)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.parent.is_symlink() or (path.exists() and not path.is_file()):
                raise DurableQueueError("job_unsafe_storage", 503)
            if os.name == "posix":
                os.chmod(path.parent, 0o700)
                if path.exists() and stat.S_IMODE(path.stat().st_mode) & 0o077:
                    raise DurableQueueError("job_insecure_storage", 503)
            name = str(path)
        try:
            self.db = sqlite3.connect(name, check_same_thread=False, timeout=5)
            self.db.execute("PRAGMA busy_timeout=5000")
            self.db.execute("PRAGMA synchronous=FULL")
            if name != ":memory:":
                self.db.execute("PRAGMA journal_mode=WAL")
                if os.name == "posix":
                    os.chmod(name, 0o600)
            with self.lock, self.db:
                self.db.execute("""CREATE TABLE IF NOT EXISTS durable_jobs(
                    job_id TEXT PRIMARY KEY, label TEXT NOT NULL,
                    priority INTEGER NOT NULL, status TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL,
                    last_claim_seq INTEGER NOT NULL DEFAULT 0,
                    delegation_json TEXT NOT NULL DEFAULT ''
                )""")
                self.db.execute("""CREATE TABLE IF NOT EXISTS durable_job_nodes(
                    job_id TEXT NOT NULL, plan_id TEXT NOT NULL UNIQUE,
                    deps_json TEXT NOT NULL, plan_sha256 TEXT NOT NULL,
                    node_status TEXT NOT NULL, next_index INTEGER NOT NULL,
                    last_receipt_sha256 TEXT,
                    security_sha256 TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(job_id,plan_id),
                    FOREIGN KEY(job_id) REFERENCES durable_jobs(job_id)
                )""")
                self.db.execute("""CREATE TABLE IF NOT EXISTS durable_job_events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL, plan_id TEXT,
                    event TEXT NOT NULL, evidence_sha256 TEXT,
                    at_utc TEXT NOT NULL
                )""")
                # Additive migration preserves existing v1 jobs/hashes.
                job_cols = {c[1] for c in self.db.execute(
                    "PRAGMA table_info(durable_jobs)").fetchall()}
                node_cols = {c[1] for c in self.db.execute(
                    "PRAGMA table_info(durable_job_nodes)").fetchall()}
                if "delegation_json" not in job_cols:
                    self.db.execute("""ALTER TABLE durable_jobs
                        ADD COLUMN delegation_json TEXT NOT NULL DEFAULT ''""")
                if "security_sha256" not in node_cols:
                    self.db.execute("""ALTER TABLE durable_job_nodes
                        ADD COLUMN security_sha256 TEXT NOT NULL DEFAULT ''""")
                # A completed tool step may have reached the other SQLite file
                # before process death. DO NOT infer it was safely dispatched.
                rows = self.db.execute("""SELECT DISTINCT job_id FROM durable_job_nodes
                    WHERE node_status='IN_FLIGHT'""").fetchall()
                for (jid,) in rows:
                    self.db.execute("""UPDATE durable_job_nodes SET
                        node_status='REVIEW_REQUIRED' WHERE job_id=? AND
                        node_status='IN_FLIGHT'""", (jid,))
                    self.db.execute("""UPDATE durable_jobs SET
                        status='REVIEW_REQUIRED',revision=revision+1,
                        updated_utc=? WHERE job_id=?""", (_now(), jid))
                    self._event(jid, None, "RESTART_REVIEW_REQUIRED", None)
        except sqlite3.Error:
            raise DurableQueueError("job_storage_unavailable", 503) from None

    def _check_delegation(self, delegation, nodes):
        try:
            return validate_delegation(delegation, nodes, self.plans)
        except DelegationError as exc:
            raise DurableQueueError(str(exc), exc.status) from None

    def _event(self, job_id, plan_id, event, evidence):
        if self.db.execute("SELECT COUNT(*) FROM durable_job_events").fetchone()[0] >= MAX_EVENTS:
            raise DurableQueueError("job_history_capacity_reached", 409)
        cur = self.db.execute("""INSERT INTO durable_job_events
            (job_id,plan_id,event,evidence_sha256,at_utc)
            VALUES(?,?,?,?,?)""", (job_id, plan_id, event, evidence, _now()))
        return cur.lastrowid

    def _plan(self, pid, *, index=0, original=None, security=None):
        try:
            p = self.plans.get(pid)
        except PlanError:
            raise DurableQueueError("job_plan_missing_or_unavailable", 409) from None
        if original is not None and _fingerprint(p) != original:
            raise DurableQueueError("job_plan_source_changed", 409)
        if security and not _security_matches(p, security):
            raise DurableQueueError("job_plan_permission_binding_changed", 409)
        try:
            self.plans._verify_agent_binding(p["agent_binding"],p["steps"])
            self.plans._verify_global_memory_isolation(
                p["agent_binding"],p["memory_refs"])
            self.plans._verify_scoped_memory_refs(
                p["scoped_memory_refs"],p["agent_binding"])
            self.plans._verify_memory_refs(p["memory_refs"])
        except PlanError:
            raise DurableQueueError("job_plan_permission_or_memory_changed",409) from None
        status = "APPROVED" if index == 0 else "NEXT_READY"
        if (p["status"] != status or p["next_index"] != index
                or p["batch_run_status"] in ("RUNNING", "STOPPING", "INTERRUPTED")
                or not p["steps"]
                or any(step["tool"] not in BATCH_READ_TOOLS for step in p["steps"])):
            raise DurableQueueError("job_plan_not_ready", 409)
        return p

    def _rows(self, job_id):
        return self.db.execute("""SELECT plan_id,deps_json,plan_sha256,
            node_status,next_index,last_receipt_sha256,security_sha256 FROM durable_job_nodes
            WHERE job_id=? ORDER BY rowid""", (job_id,)).fetchall()

    def _job(self, job_id):
        _valid_id(job_id)
        row = self.db.execute("""SELECT job_id,label,priority,status,
            revision,created_utc,updated_utc,last_claim_seq,delegation_json
            FROM durable_jobs WHERE job_id=?""", (job_id,)).fetchone()
        if row is None:
            raise DurableQueueError("job_not_found", 404)
        nodes = []
        completed = set()
        for pid, deps, digest, state, ix, receipt, security in self._rows(job_id):
            if state == "COMPLETED":
                completed.add(pid)
            nodes.append({"plan_id": pid, "depends_on": json.loads(deps),
                          "status": state, "next_index": ix,
                          "plan_sha256": digest,
                          "last_receipt_sha256": receipt,
                          "security_sha256": security})
        for node in nodes:
            node["dependencies_satisfied"] = all(
                d in completed for d in node["depends_on"])
        try:
            delegation = json.loads(row[8]) if row[8] else None
            if delegation is not None:
                from local_agent_delegation import _validate_fields
                delegation = _validate_fields(delegation)
        except (ValueError, TypeError, DelegationError):
            raise DurableQueueError("job_delegation_storage_invalid",503) from None
        handoff_sha256 = None
        if (delegation is not None and len(nodes) == 2
                and nodes[0]["status"] == "COMPLETED"):
            receipt = nodes[0]["last_receipt_sha256"]
            if type(receipt) is str and re.fullmatch(r"[0-9a-f]{64}",receipt):
                handoff_sha256 = _digest({
                    "project_id": delegation["project_id"],
                    "source_plan_id": nodes[0]["plan_id"],
                    "target_plan_id": nodes[1]["plan_id"],
                    "source_receipt_sha256": receipt,
                })
        return {"job_id": row[0], "label": row[1],
                "priority": row[2], "status": row[3],
                "delegation": delegation,
                "completion_handoff_sha256": handoff_sha256,
                "handoff_payload": "NONE",
                "revision": row[4], "created_utc": row[5],
                "updated_utc": row[6], "nodes": nodes,
                "next_requires_owner_action": True,
                "background_dispatch_enabled": False,
                "external_write_permission": False}

    def get(self, job_id):
        with self.lock:
            try:
                return self._job(job_id)
            except sqlite3.Error:
                raise DurableQueueError("job_storage_unavailable", 503) from None

    def list(self):
        with self.lock:
            try:
                ids = [r[0] for r in self.db.execute(
                    "SELECT job_id FROM durable_jobs ORDER BY priority DESC,"
                    " created_utc,job_id LIMIT ?", (MAX_JOBS,)).fetchall()]
                return {"jobs": [self._job(jid) for jid in ids],
                        "max_jobs": MAX_JOBS, "owner_tick_only": True}
            except sqlite3.Error:
                raise DurableQueueError("job_storage_unavailable", 503) from None

    def history(self, job_id):
        _valid_id(job_id)
        with self.lock:
            try:
                self._job(job_id)
                rows = self.db.execute("""SELECT seq,plan_id,event,
                    evidence_sha256,at_utc FROM durable_job_events
                    WHERE job_id=? ORDER BY seq DESC LIMIT 100""",
                    (job_id,)).fetchall()
                return {"job_id":job_id, "events":[{
                    "seq":r[0], "plan_id":r[1], "event":r[2],
                    "evidence_sha256":r[3], "at_utc":r[4]} for r in rows],
                    "external_write_count":0}
            except sqlite3.Error:
                raise DurableQueueError("job_storage_unavailable", 503) from None

    def create(self, request):
        jid, label, priority, nodes, delegation = _validate_create(request)
        with self.lock:
            checked = []
            for n in nodes:
                p = self._plan(n["plan_id"])
                checked.append((n, _fingerprint(p), _versioned_security_fingerprint(p)))
            if delegation is not None:
                self._check_delegation(delegation, nodes)
            try:
                with self.db:
                    if self.db.execute("SELECT count(*) FROM durable_jobs").fetchone()[0] >= MAX_JOBS:
                        raise DurableQueueError("job_capacity_reached", 409)
                    stamp = _now()
                    self.db.execute("""INSERT INTO durable_jobs
                        (job_id,label,priority,status,created_utc,updated_utc,delegation_json)
                        VALUES(?,?,?,'DRAFT',?,?,?)""",
                        (jid,label,priority,stamp,stamp,
                         json.dumps(delegation,sort_keys=True) if delegation else ""))
                    for n, sha, security in checked:
                        self.db.execute("""INSERT INTO durable_job_nodes
                            (job_id,plan_id,deps_json,plan_sha256,node_status,next_index,security_sha256)
                            VALUES(?,?,?,?,'WAITING',0,?)""",
                            (jid,n["plan_id"],json.dumps(n["depends_on"]),sha,security))
                    self._event(jid,None,"CREATED",None)
                return self._job(jid)
            except sqlite3.IntegrityError:
                raise DurableQueueError("job_or_plan_already_registered", 409) from None
            except sqlite3.Error:
                raise DurableQueueError("job_storage_unavailable", 503) from None

    def _mutate(self, jid, action):
        _valid_id(jid)
        transitions = {
            "arm": ("DRAFT", "ACTIVE"),
            "pause": ("ACTIVE", "PAUSED"),
            "resume": ("PAUSED", "ACTIVE"),
            "cancel_draft": ("DRAFT", "CANCELLED"),
            "cancel_active": ("ACTIVE", "CANCELLED"),
            "cancel_paused": ("PAUSED", "CANCELLED"),
        }
        with self.lock:
            try:
                with self.db:
                    old = self._job(jid)
                    target = "cancel_" + old["status"].lower() if action=="cancel" else action
                    if target not in transitions or old["status"] != transitions[target][0]:
                        raise DurableQueueError("job_state_conflict", 409)
                    if any(n["status"]=="IN_FLIGHT" for n in old["nodes"]):
                        raise DurableQueueError("job_step_in_flight", 409)
                    if action in ("arm","resume"):
                        if old["delegation"] is not None:
                            self._check_delegation(old["delegation"],old["nodes"])
                        for node in old["nodes"]:
                            if node["status"]=="COMPLETED":
                                continue
                            self._plan(node["plan_id"],index=node["next_index"],
                                       original=node["plan_sha256"],
                                       security=node["security_sha256"])
                    new = transitions[target][1]
                    self.db.execute("""UPDATE durable_jobs SET status=?,
                        revision=revision+1,updated_utc=? WHERE job_id=?""",
                        (new,_now(),jid))
                    self._event(jid,None,action.upper(),None)
                return self._job(jid)
            except sqlite3.Error:
                raise DurableQueueError("job_storage_unavailable", 503) from None

    def arm(self, job_id):
        return self._mutate(job_id, "arm")

    def pause(self, job_id):
        return self._mutate(job_id, "pause")

    def resume(self, job_id):
        return self._mutate(job_id, "resume")

    def cancel(self, job_id):
        return self._mutate(job_id, "cancel")

    def _choose(self, specific=None):
        if specific is not None:
            _valid_id(specific)
        jobs = self.db.execute("""SELECT job_id FROM durable_jobs
            WHERE status='ACTIVE' ORDER BY priority DESC,last_claim_seq ASC,
            created_utc ASC,job_id ASC""").fetchall()
        for (jid,) in jobs:
            if specific is not None and jid != specific:
                continue
            job = self._job(jid)
            for n in job["nodes"]:
                if n["status"] == "WAITING" and n["dependencies_satisfied"]:
                    return job,n
        raise DurableQueueError("job_no_ready_step", 409)

    def _review(self, jid, pid, cause):
        # No raw provider messages, plan results or secrets in durable history.
        with self.lock:
            try:
                with self.db:
                    self.db.execute("""UPDATE durable_job_nodes SET
                        node_status='REVIEW_REQUIRED' WHERE job_id=? AND
                        plan_id=? AND node_status='IN_FLIGHT'""", (jid,pid))
                    self.db.execute("""UPDATE durable_jobs SET
                        status='REVIEW_REQUIRED',revision=revision+1,
                        updated_utc=? WHERE job_id=?""", (_now(),jid))
                    self._event(jid,pid,cause,None)
            except sqlite3.Error:
                raise DurableQueueError("job_storage_uncertain_review_required", 503) from None

    def run_one(self, job_id=None):
        """ONE owner-dispatched existing step across a fair, prioritized queue."""
        with self.gate_lock:
            if not self.gate.acquire(blocking=False):
                raise DurableQueueError("local_agent_busy", 429)
        jid = pid = None
        claimed = False
        started = False
        try:
            with self.lock:
                try:
                    with self.db:
                        job,node = self._choose(job_id)
                        jid,pid = job["job_id"],node["plan_id"]
                        if job["delegation"] is not None:
                            self._check_delegation(job["delegation"],job["nodes"])
                        self._plan(pid,index=node["next_index"],
                                   original=node["plan_sha256"],
                                   security=node["security_sha256"])
                        ix = node["next_index"]
                        claimed_seq = self._event(jid,pid,"STEP_CLAIMED",None)
                        changed = self.db.execute("""UPDATE durable_job_nodes
                            SET node_status='IN_FLIGHT'
                            WHERE job_id=? AND plan_id=? AND node_status='WAITING'
                            AND next_index=?""", (jid,pid,ix)).rowcount
                        if changed != 1:
                            raise DurableQueueError("job_step_already_claimed")
                        self.db.execute("""UPDATE durable_jobs SET
                            last_claim_seq=?,revision=revision+1,updated_utc=?
                            WHERE job_id=? AND status='ACTIVE'""",
                            (claimed_seq,_now(),jid))
                    claimed = True
                except sqlite3.Error:
                    raise DurableQueueError("job_storage_unavailable", 503) from None
            # Claim is durable before touching any real tool/model callback.
            try:
                self.model_safety.begin()
                started = True
                result = self.plans.advance(pid)
            except (PlanError,ModelSafetyError) as exc:
                if str(exc) in ("local_model_timeout","local_model_transport_uncertain"):
                    try:
                        self.model_safety.timeout()
                    except ModelSafetyError:
                        pass
                raise DurableQueueError("job_step_uncertain_inspect_plan", 503) from None
            except Exception:
                raise DurableQueueError("job_step_uncertain_inspect_plan", 503) from None
            finally:
                if started:
                    try:
                        self.model_safety.finish()
                    except ModelSafetyError:
                        # Even if plan advanced, uncertain checkpoint blocks next dispatch.
                        raise DurableQueueError("job_checkpoint_uncertain", 503) from None
            if (result["next_index"] != ix+1 or
                    len(result["results"]) != ix+1 or
                    result["status"] not in ("COMPLETED","NEXT_READY")):
                raise DurableQueueError("job_result_checkpoint_invalid",503)
            receipt = _digest({"plan_id":pid,"index":ix,
                               "result":result["results"][ix]})
            with self.lock:
                try:
                    with self.db:
                        completed = result["status"] == "COMPLETED"
                        state = "COMPLETED" if completed else "WAITING"
                        changed = self.db.execute("""UPDATE durable_job_nodes SET
                            node_status=?,next_index=next_index+1,
                            last_receipt_sha256=?
                            WHERE job_id=? AND plan_id=? AND node_status='IN_FLIGHT'
                            AND next_index=?""",
                            (state,receipt,jid,pid,ix)).rowcount
                        if changed != 1:
                            raise DurableQueueError("job_claim_state_changed",503)
                        self._event(jid,pid,"STEP_RECORDED",receipt)
                        left = self.db.execute("""SELECT count(*) FROM durable_job_nodes
                            WHERE job_id=? AND node_status!='COMPLETED'""",
                            (jid,)).fetchone()[0]
                        if left == 0:
                            self.db.execute("""UPDATE durable_jobs
                                SET status='COMPLETED',revision=revision+1,
                                updated_utc=? WHERE job_id=?""",(_now(),jid))
                            self._event(jid,None,"JOB_COMPLETED",None)
                    return {"status":"STEP_RECORDED","job":self._job(jid),
                            "plan_id":pid,"step_index":ix,
                            "step_evidence_sha256":receipt,
                            "external_write_count":0,"model_call_cost_verified":False}
                except sqlite3.Error:
                    raise DurableQueueError("job_storage_uncertain_review_required",503) from None
        except DurableQueueError as exc:
            if claimed:
                try:
                    self._review(jid,pid,"STEP_UNCERTAIN")
                except DurableQueueError:
                    pass
            raise
        finally:
            self.gate.release()

    def reconcile_recorded(self, job_id, plan_id):
        """Owner review: only accept exactly one already-persisted tool checkpoint."""
        jid = _valid_id(job_id)
        if type(plan_id) is not str or not PLAN_ID.fullmatch(plan_id):
            raise DurableQueueError("invalid_plan_id",422)
        with self.lock:
            try:
                with self.db:
                    job=self._job(jid)
                    if job["status"] != "REVIEW_REQUIRED":
                        raise DurableQueueError("job_state_conflict")
                    node=next((n for n in job["nodes"] if n["plan_id"]==plan_id),None)
                    if node is None or node["status"]!="REVIEW_REQUIRED":
                        raise DurableQueueError("job_node_not_reviewable")
                    if self.model_safety.phase()!="IDLE":
                        raise DurableQueueError("job_model_safety_uncertain")
                    p=self.plans.get(plan_id)
                    if _fingerprint(p)!=node["plan_sha256"]:
                        raise DurableQueueError("job_plan_source_changed")
                    if (node["security_sha256"] and
                            not _security_matches(p, node["security_sha256"])):
                        raise DurableQueueError("job_plan_permission_binding_changed")
                    ix=node["next_index"]
                    if (p["next_index"]!=ix+1 or len(p["results"])!=ix+1
                            or p["status"] not in ("NEXT_READY","COMPLETED")):
                        raise DurableQueueError("job_recorded_step_not_proven")
                    evidence=_digest({"plan_id":plan_id,"index":ix,
                                       "result":p["results"][ix]})
                    state="COMPLETED" if p["status"]=="COMPLETED" else "WAITING"
                    self.db.execute("""UPDATE durable_job_nodes
                        SET node_status=?,next_index=?,last_receipt_sha256=?
                        WHERE job_id=? AND plan_id=? AND node_status='REVIEW_REQUIRED'""",
                        (state,ix+1,evidence,jid,plan_id))
                    self._event(jid,plan_id,"OWNER_RECONCILED_RECORDED_STEP",evidence)
                    count=self.db.execute("""SELECT count(*) FROM durable_job_nodes
                        WHERE job_id=? AND node_status!='COMPLETED'""",(jid,)).fetchone()[0]
                    # PAUSED, never automatic continuation after a crash.
                    new = "COMPLETED" if count==0 else "PAUSED"
                    self.db.execute("""UPDATE durable_jobs SET
                        status=?,revision=revision+1,updated_utc=?
                        WHERE job_id=?""",(new,_now(),jid))
                return self._job(jid)
            except sqlite3.Error:
                raise DurableQueueError("job_storage_unavailable",503) from None

    def close(self):
        with self.lock:
            self.db.close()
