"""Slice 77: deliberate two-phase owner action, synthetic CSV and local files."""
from http.server import ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_csv_profile import profile_selected_csv
from local_agent_task_plans import LocalAgentPlanStore, PlanError, validate_steps
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit, SecurityAuditError
from local_tool_registry import (
    ACTION_TOOL, BATCH_READ_TOOLS, registered_tools, require_owner_action,
    ToolPermissionError)
from local_tool_action_gate import (
    LocalCSVProfileActionGate, ToolActionError, AUDIT_ACTION)
from local_server import create_handler

CSV = "revenue,stage\n10,SYNTHETIC_PERSON_1\n20,SYNTHETIC_PERSON_2\n,SYNTHETIC_PERSON_3\n"
SOURCE = {"filename": "synthetic-ledger.csv", "content": CSV}
PLAN = {"model":"fixture:model", "goal":"Profile a selected local CSV for a report",
        "steps":[{"tool":"local_csv_profile",
                  "args":{"columns":["revenue","stage"]}},
                 {"tool":"calculate",
                  "args":{"a":{"from_step":1,"field":"column_sum","column":"revenue"},
                          "b":"2","operation":"multiply"}}]}


class ActionGateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "plans.sqlite3"
        self.store = LocalAgentPlanStore(self.path, csv_profiler=profile_selected_csv)
        self.addCleanup(self.store.close)
        self.audit = LocalSecurityAudit(self.root / "audit.sqlite3")
        self.addCleanup(self.audit.close)
        self.gate = LocalCSVProfileActionGate(
            self.store, self.root / "exports", self.audit)
        self.pid = self.store.create(PLAN, csv_snapshot=SOURCE)["plan_id"]

    def finished(self):
        self.store.approve(self.pid)
        self.store.advance(self.pid)
        self.store.advance(self.pid)
        return self.store.get(self.pid)

    def test_registry_is_fixed_and_does_not_allow_action_in_model_or_batch(self):
        names = registered_tools(planning=True, csv=True, action=True)
        match = [t for t in names if t["name"] == ACTION_TOOL]
        self.assertEqual(1, len(match))
        self.assertEqual("LOCAL_CREATE_ONLY", match[0]["effect"])
        self.assertTrue(match[0]["owner_confirmation_required"])
        self.assertFalse(match[0]["model_plannable"])
        self.assertFalse(match[0]["batch_allowed"])
        self.assertNotIn(ACTION_TOOL, BATCH_READ_TOOLS)
        self.assertEqual(8, len(BATCH_READ_TOOLS))
        disabled = [t for t in registered_tools(planning=True, csv=True,
                                                action=False)
                    if t["name"] == ACTION_TOOL][0]
        self.assertFalse(disabled["enabled"])
        with self.assertRaisesRegex(PlanError, "tool_not_allowed"):
            validate_steps([{"tool": ACTION_TOOL, "args": {}}])
        with self.assertRaisesRegex(ToolPermissionError,
                                    "local_tool_permission_denied"):
            require_owner_action("calculate", enabled=True)
        with self.assertRaisesRegex(ToolPermissionError,
                                    "local_tool_permission_denied"):
            require_owner_action(ACTION_TOOL, enabled=False)
        with self.assertRaisesRegex(PlanError, "tool_not_allowed"):
            self.store.create({"model":"fixture:model","goal":"write",
                               "steps":[{"tool":ACTION_TOOL,"args":{}}]})

    def test_preview_cannot_write_and_needs_completed_csv_profile(self):
        with self.assertRaisesRegex(ToolActionError,
                                    "action_requires_completed_plan"):
            self.gate.preview(ACTION_TOOL, self.pid)
        self.assertFalse(self.gate.writer.root.exists())
        self.finished()
        result = self.gate.preview(ACTION_TOOL, self.pid)
        self.assertTrue(result["preview_token"])
        self.assertEqual(64, len(result["preview_sha256"]))
        self.assertFalse(self.gate.writer.root.exists())
        self.assertEqual("ONE_LOCAL_CREATE_ONLY_PLAINTEXT_FILE",
                         result["risk"])
        self.assertEqual("30", result["preview"]["profiles"][0]
                         ["profile"]["column_stats"][0]
                         ["numeric_summary"]["sum"])
        self.assertNotIn("SYNTHETIC_PERSON_", json.dumps(result))
        self.assertEqual(0, result["side_effects"]["external_write_count"])

    def test_confirm_creates_only_fixed_json_and_retries_identically(self):
        self.finished()
        preview = self.gate.preview(ACTION_TOOL, self.pid)
        result = self.gate.commit(ACTION_TOOL, self.pid, preview["preview_token"],
                                  preview["preview_sha256"])
        self.assertEqual("CREATED", result["status"])
        self.assertEqual(1, result["local_file_write_count"])
        self.assertEqual(0, result["external_write_count"])
        saved = self.gate.writer.root / result["filename"]
        self.assertEqual(preview["preview_sha256"], result["sha256"])
        data = json.loads(saved.read_text(encoding="utf-8"))
        self.assertEqual("60", self.store.get(self.pid)["results"][1]["output"])
        self.assertEqual(30, data["profiles"][0]["profile"]["column_stats"][0]
                         ["numeric_summary"]["sum"] == "30" and 30)
        self.assertNotIn("SYNTHETIC_PERSON_", saved.read_text())
        self.assertEqual(1, len(list(self.gate.writer.root.glob("*.json"))))
        with self.assertRaisesRegex(ToolActionError,
                                    "action_preview_expired_or_changed"):
            self.gate.commit(ACTION_TOOL, self.pid, preview["preview_token"],
                             preview["preview_sha256"])
        second = self.gate.preview(ACTION_TOOL, self.pid)
        repeat = self.gate.commit(ACTION_TOOL, self.pid,
                                  second["preview_token"], second["preview_sha256"])
        self.assertEqual("EXISTS_IDENTICAL", repeat["status"])
        self.assertEqual(0, repeat["local_file_write_count"])
        events = self.audit.recent(10)
        self.assertEqual(4, len([e for e in events
                                 if e["action"] == AUDIT_ACTION]))
        self.assertTrue(all(e["source_fingerprint"] == "" for e in events))

    def test_altered_file_and_symlink_never_overwritten(self):
        self.finished()
        p = self.gate.preview(ACTION_TOOL, self.pid)
        self.gate.commit(ACTION_TOOL, self.pid, p["preview_token"],
                         p["preview_sha256"])
        target = self.gate.writer.root / p["filename"]
        target.write_text("OWNER PRIVATE MODIFICATION", encoding="utf-8")
        other = self.gate.preview(ACTION_TOOL, self.pid)
        with self.assertRaisesRegex(ToolActionError, "action_target_conflict"):
            self.gate.commit(ACTION_TOOL, self.pid, other["preview_token"],
                             other["preview_sha256"])
        self.assertEqual("OWNER PRIVATE MODIFICATION", target.read_text())
        target.unlink()
        outside = self.root / "outside.json"
        outside.write_text("PRESERVE", encoding="utf-8")
        try:
            target.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Windows runner does not support symlinks")
        third = self.gate.preview(ACTION_TOOL, self.pid)
        with self.assertRaisesRegex(ToolActionError, "action_target_conflict"):
            self.gate.commit(ACTION_TOOL, self.pid, third["preview_token"],
                             third["preview_sha256"])
        self.assertEqual("PRESERVE", outside.read_text())

    def test_preview_token_cannot_bind_another_plan_or_digest(self):
        self.finished()
        p = self.gate.preview(ACTION_TOOL, self.pid)
        with self.assertRaisesRegex(ToolActionError,
                                    "action_preview_expired_or_changed"):
            self.gate.commit(ACTION_TOOL, self.pid, p["preview_token"],
                             "0" * 64)
        self.assertFalse(self.gate.writer.root.exists())
        p = self.gate.preview(ACTION_TOOL, self.pid)
        with self.assertRaisesRegex(ToolActionError,
                                    "action_preview_expired_or_changed"):
            self.gate.commit(ACTION_TOOL, "0" * 24, p["preview_token"],
                             p["preview_sha256"])
        self.assertFalse(self.gate.writer.root.exists())
        with self.assertRaisesRegex(ToolActionError, "invalid_action_commit"):
            self.gate.commit(ACTION_TOOL, self.pid, "fake", "0" * 64)

    def test_tampered_profile_blocks_preview_without_written_file(self):
        self.finished()
        with self.store.lock, self.store.db:
            original = self.store.get(self.pid)["results"]
            bad = json.loads(original[0]["output"])
            bad["column_stats"][0]["numeric_summary"]["sum"] = "9999"
            original[0]["output"] = json.dumps(bad)
            self.store.db.execute("UPDATE agent_task_plans SET results_json=? "
                                  "WHERE plan_id=?", (json.dumps(original), self.pid))
        # The old handoff verifier already detects the corruption.
        with self.assertRaises(PlanError):
            self.gate.preview(ACTION_TOOL, self.pid)
        self.assertFalse(self.gate.writer.root.exists())

    def test_after_preview_source_tamper_disallows_write(self):
        self.finished()
        p = self.gate.preview(ACTION_TOOL, self.pid)
        with self.store.lock, self.store.db:
            x = self.store.db.execute("SELECT csv_json FROM agent_task_plans "
                                      "WHERE plan_id=?", (self.pid,)).fetchone()[0]
            self.store.db.execute("UPDATE agent_task_plans SET csv_json=? "
                                  "WHERE plan_id=?", (
                                      x.replace("10,SYNTHETIC", "70,SYNTHETIC"),
                                      self.pid))
        with self.assertRaises(PlanError):
            self.gate.commit(ACTION_TOOL, self.pid, p["preview_token"],
                             p["preview_sha256"])
        self.assertFalse(self.gate.writer.root.exists())

    def test_disabled_permission_and_audit_unavailable_fail_before_write(self):
        self.finished()
        self.gate.enabled = False
        with self.assertRaisesRegex(ToolActionError, "local_tool_permission_denied"):
            self.gate.preview(ACTION_TOOL, self.pid)
        self.gate.enabled = True
        self.store.csv_profiler = None
        with self.assertRaisesRegex(ToolActionError, "local_csv_permission_revoked"):
            self.gate.preview(ACTION_TOOL, self.pid)
        self.store.csv_profiler = profile_selected_csv
        p = self.gate.preview(ACTION_TOOL, self.pid)
        self.audit._blocked = True  # synthetic crash of SQLite security ledger
        with self.assertRaisesRegex(ToolActionError, "action_audit_unavailable"):
            self.gate.commit(ACTION_TOOL, self.pid, p["preview_token"],
                             p["preview_sha256"])
        self.assertFalse(self.gate.writer.root.exists())

    def test_restart_invalidates_unspent_preview_without_auto_execution(self):
        self.finished()
        p = self.gate.preview(ACTION_TOOL, self.pid)
        fresh = LocalCSVProfileActionGate(self.store,
                    self.root / "exports", self.audit)
        with self.assertRaisesRegex(ToolActionError,
                                    "action_preview_expired_or_changed"):
            fresh.commit(ACTION_TOOL, self.pid, p["preview_token"],
                         p["preview_sha256"])
        self.assertFalse(fresh.writer.root.exists())
        after = fresh.preview(ACTION_TOOL, self.pid)
        receipt = fresh.commit(ACTION_TOOL, self.pid,
                               after["preview_token"], after["preview_sha256"])
        self.assertEqual("CREATED", receipt["status"])


class ActionHTTPTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.store = LocalAgentPlanStore(self.home / "plans.sqlite3",
                                         csv_profiler=profile_selected_csv)
        self.addCleanup(self.store.close)
        self.audit = LocalSecurityAudit(self.home / "audit.sqlite3")
        self.addCleanup(self.audit.close)
        self.gate = LocalCSVProfileActionGate(self.store,
                        self.home / "exports", self.audit)
        self.auth = LocalOperatorAuth.ephemeral_for_tests(
            "only_for_fixture_csv_action_owner_password")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(
            operator_auth=self.auth, security_audit=self.audit,
            local_agent_runner=lambda *_: None,
            local_agent_planner=lambda *_a, **_k: PLAN,
            local_agent_plan_store=self.store,
            plan_action_gate=self.gate))
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 4)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        _, session = self.auth.login("only_for_fixture_csv_action_owner_password")
        self.cookie = "aas_session=" + session["cookie_token"]
        self.csrf = session["csrf_token"]
        self.pid = self.store.create(PLAN, csv_snapshot=SOURCE)["plan_id"]
        self.store.approve(self.pid)
        self.store.advance(self.pid)
        self.store.advance(self.pid)

    def request(self, url, data, *, auth=True, csrf=True, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1",
                                          self.server.server_port, timeout=10)
        headers = {"Content-Type":"application/json"}
        if auth:
            headers["Cookie"] = self.cookie
        if csrf:
            headers["X-AAS-CSRF"] = self.csrf
        if origin is not None:
            headers["Origin"] = origin
        conn.request("POST", url, json.dumps(data).encode("utf-8"), headers)
        response = conn.getresponse()
        result = (response.status, json.loads(response.read()))
        conn.close()
        return result

    def test_two_phase_api_auth_csrf_origin_schema_and_exact_write(self):
        preview_path = "/api/local-agent-action-preview"
        commit_path = "/api/local-agent-action-commit"
        body = {"action": ACTION_TOOL, "plan_id":self.pid}
        self.assertEqual(401, self.request(preview_path, body, auth=False)[0])
        self.assertEqual(403, self.request(preview_path, body, csrf=False)[0])
        self.assertEqual(403, self.request(preview_path, body,
                         origin="https://evil.example")[0])
        self.assertEqual(422, self.request(preview_path, {**body,
                                    "path":"../outside.json"})[0])
        self.assertEqual(422, self.request(preview_path, {
                                    **body,"action":"arbitrary_file_write"})[0])
        self.assertFalse(self.gate.writer.root.exists())
        code, preview = self.request(preview_path, body)
        self.assertEqual(200, code)
        self.assertFalse(self.gate.writer.root.exists())
        self.assertEqual(422, self.request(commit_path, {
                                **body, "preview_token":preview["preview_token"],
                                "preview_sha256":preview["preview_sha256"],
                                "content":"OVERRIDE"})[0])
        self.assertEqual(403, self.request(commit_path, {
                                **body, "preview_token":preview["preview_token"],
                                "preview_sha256":preview["preview_sha256"]},
                                csrf=False)[0])
        code, receipt = self.request(commit_path, {
                               **body, "preview_token":preview["preview_token"],
                               "preview_sha256":preview["preview_sha256"]})
        self.assertEqual(200, code)
        self.assertEqual("CREATED", receipt["status"])
        self.assertEqual(preview["preview_sha256"], receipt["sha256"])
        self.assertEqual(409, self.request(commit_path, {
                               **body, "preview_token":preview["preview_token"],
                               "preview_sha256":preview["preview_sha256"]})[0])
        self.assertTrue((self.gate.writer.root / receipt["filename"]).is_file())
        # Metadata is a fixed registry descriptor, not an executable plugin.
        conn = http.client.HTTPConnection("127.0.0.1",
                                          self.server.server_port, timeout=10)
        conn.request("GET", "/api/local-agent-capabilities",
                     headers={"Cookie": self.cookie})
        res = conn.getresponse()
        catalog = json.loads(res.read())
        conn.close()
        self.assertEqual(200, res.status)
        self.assertTrue(catalog["plan_action_export"])
        action = next(t for t in catalog["tool_registry"]
                      if t["name"] == ACTION_TOOL)
        self.assertEqual("LOCAL_CREATE_ONLY", action["effect"])
        self.assertFalse(action["batch_allowed"])

    def test_preview_audit_failure_prevents_any_mutation(self):
        code, p = self.request("/api/local-agent-action-preview",
                        {"action":ACTION_TOOL,"plan_id":self.pid})
        self.assertEqual(200, code)
        self.audit._blocked = True
        code, data = self.request("/api/local-agent-action-commit", {
                      "action":ACTION_TOOL, "plan_id":self.pid,
                      "preview_token":p["preview_token"],
                      "preview_sha256":p["preview_sha256"]})
        self.assertEqual(503, code)
        self.assertEqual("security_audit_restart_required", data["error"])
        self.assertFalse(self.gate.writer.root.exists())


if __name__ == "__main__":
    unittest.main()
