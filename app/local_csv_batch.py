"""Bounded, explicit CSV intake into the real local SQLite rule ledger.

An uploaded file is only parsed in memory. Its raw bytes, free text and
filename are never stored or reflected. A preview token binds the exact
validated CSV to the current active workflow version and run ledger count.

No authentication, provider adapter, message dispatch or AI inference.
"""
import csv
import hashlib
import io
import json

from local_workflow_core import (
    EVENT_PATTERN, MAX_RUNS_PER_WORKFLOW, SERVICES, TIMELINES,
    _error, _evaluate_rule, _event_fingerprint, _utc,
)

CSV_HEADER = ("event_id", "service_code", "budget_tier", "timeline")
MAX_CSV_BYTES = 4096
MAX_BATCH_ROWS = 25


class LocalCSVBatchIntake:
    def __init__(self, core):
        self.core = core

    @staticmethod
    def _parse(csv_text):
        if type(csv_text) is not str:
            return None
        if not csv_text or not csv_text.isascii() or "\x00" in csv_text:
            return None
        if len(csv_text.encode("utf-8")) > MAX_CSV_BYTES:
            return None
        try:
            rows = list(csv.reader(io.StringIO(csv_text, newline=""), strict=True))
        except (csv.Error, ValueError):
            return None
        if not 2 <= len(rows) <= MAX_BATCH_ROWS + 1:
            return None
        if tuple(rows[0]) != CSV_HEADER:
            return None
        events = []
        seen = set()
        for values in rows[1:]:
            if len(values) != 4:
                return None
            event_id, service, budget, timeline = values
            if not EVENT_PATTERN.fullmatch(event_id):
                return None
            if service not in SERVICES or budget not in ("1", "2", "3"):
                return None
            if timeline not in TIMELINES or event_id in seen:
                return None
            seen.add(event_id)
            events.append((event_id, service, int(budget), timeline))
        return events

    @staticmethod
    def _digest(workflow_id, version, control_epoch, ledger_count, csv_text):
        # Consistency token, NOT authorization: there is no authenticated
        # identity on this localhost-only prototype.
        snapshot = {
            "workflow_id": workflow_id,
            "version": version,
            "control_epoch": control_epoch,
            "ledger_count": ledger_count,
            "csv_sha256": hashlib.sha256(csv_text.encode("utf-8")).hexdigest(),
        }
        return hashlib.sha256(json.dumps(
            snapshot, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()

    def _assess_locked(self, wid, events):
        core = self.core
        row = core.db.execute("""SELECT service_filter,min_budget_tier,
            followup_policy,version,status,control_epoch FROM local_workflows
            WHERE workflow_id=?""", (wid,)).fetchone()
        if row is None:
            return _error("workflow_not_found", 404)
        service_filter, min_budget, policy, version, status, epoch = row
        if status != "ACTIVE":
            return _error("workflow_not_active", 409)
        count = core.db.execute(
            "SELECT count(*) FROM local_runs WHERE workflow_id=?", (wid,)
        ).fetchone()[0]
        planned = []
        new_count = 0
        for event_id, service, budget, timeline in events:
            request = {"workflow_id": wid, "event_id": event_id,
                       "service_code": service, "budget_tier": budget,
                       "timeline": timeline}
            fingerprint = _event_fingerprint(request)
            prior = core.db.execute("""SELECT fingerprint,decision,reason
                FROM local_runs WHERE workflow_id=? AND event_id=?""",
                (wid, event_id)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    return _error("event_id_payload_conflict", 409)
                decision, reason = prior[1], prior[2]
                disposition = "REPLAYED_LOCAL"
            else:
                new_count += 1
                decision, reason = _evaluate_rule(
                    service_filter, min_budget, policy, service, budget, timeline)
                disposition = "NEW_LOCAL"
            planned.append({
                "event_id": event_id, "service_code": service,
                "budget_tier": budget, "timeline": timeline,
                "fingerprint": fingerprint, "decision": decision,
                "reason": reason, "disposition": disposition,
            })
        if count + new_count > MAX_RUNS_PER_WORKFLOW:
            return _error("run_capacity_reached", 409)
        return 200, {"version": version, "epoch": epoch, "count": count,
                     "planned": planned, "new_count": new_count,
                     "replay_count": len(planned) - new_count}

    def preview(self, request):
        if type(request) is not dict or set(request) != {"workflow_id", "csv_text"}:
            return _error("invalid_csv_request")
        wid, csv_text = request["workflow_id"], request["csv_text"]
        from local_workflow_core import ID_PATTERN
        if type(wid) is not str or not ID_PATTERN.fullmatch(wid):
            return _error("invalid_workflow_id")
        events = self._parse(csv_text)
        if events is None:
            return _error("invalid_csv_content")
        with self.core._lock:
            code, report = self._assess_locked(wid, events)
            if code != 200:
                return code, report
            digest = self._digest(wid, report["version"], report["epoch"], report["count"], csv_text)
            return 200, {
                "status": "PREVIEW_READY", "workflow_id": wid,
                "workflow_version": report["version"],
                "preview_token": digest,
                "new_count": report["new_count"],
                "replay_count": report["replay_count"],
                "rows": [{key: item[key] for key in
                          ("event_id", "decision", "reason", "disposition")}
                         for item in report["planned"]],
                "external_dispatch_count": 0,
            }

    def commit(self, request):
        fields = {"workflow_id", "csv_text", "expected_version", "preview_token"}
        if type(request) is not dict or set(request) != fields:
            return _error("invalid_commit_fields")
        wid, csv_text = request["workflow_id"], request["csv_text"]
        version, token = request["expected_version"], request["preview_token"]
        from local_workflow_core import ID_PATTERN
        if type(wid) is not str or not ID_PATTERN.fullmatch(wid):
            return _error("invalid_workflow_id")
        if type(version) is not int or version < 1:
            return _error("invalid_expected_version")
        if (type(token) is not str or len(token) != 64 or
                any(c not in "0123456789abcdef" for c in token)):
            return _error("invalid_preview_token")
        events = self._parse(csv_text)
        if events is None:
            return _error("invalid_csv_content")
        core = self.core
        with core._lock, core.db:
            code, report = self._assess_locked(wid, events)
            if code != 200:
                return code, report
            if version != report["version"]:
                return _error("workflow_version_conflict", 409)
            if token != self._digest(wid, version, report["epoch"], report["count"], csv_text):
                return _error("stale_batch_preview", 409)
            # All rows were validated and checked before the first write.
            # One transaction commits all new rows; a failure rolls back all.
            for item in report["planned"]:
                if item["disposition"] != "NEW_LOCAL":
                    continue
                core.db.execute("""INSERT INTO local_runs
                    (workflow_id,event_id,fingerprint,workflow_version,
                     service_code,budget_tier,timeline,decision,reason,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""", (
                        wid, item["event_id"], item["fingerprint"], version,
                        item["service_code"], item["budget_tier"],
                        item["timeline"], item["decision"], item["reason"], _utc(),
                    ))
            return 200, {
                "status": "BATCH_COMMITTED_LOCAL",
                "workflow_id": wid, "workflow_version": version,
                "new_count": report["new_count"],
                "replay_count": report["replay_count"],
                "saved_total": report["count"] + report["new_count"],
                "external_dispatch_count": 0,
            }
