"""Slice 74: opt-in local report file output. Synthetic model and local disk only."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import os
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_agent_plan_reports import _sources, validate_report_result
from local_agent_task_plans import LocalAgentPlanStore
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_plan_artifacts import (
    LocalPlanArtifactExporter, PlanArtifactError, render_saved_report)
from local_server import create_handler


def make_proposal():
    return {"model": "fixture:model", "goal": "Summarize saved calculation",
            "steps": [{"tool": "calculate",
                       "args": {"a": "7", "b": "8", "operation": "multiply"}}]}


class ArtifactExportTests(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.home = Path(home.name)
        self.calls = []
        def report(plan):
            self.calls.append(plan["plan_id"])
            return validate_report_result({
                "summary": "Tool output is 56. ![tracking](https://example.org/x)",
                "findings": [{"step_index": 1, "quote": "56",
                              "interpretation": "Output contains 56."}]},
                _sources(plan))
        self.store = LocalAgentPlanStore(self.home / "plans.sqlite3",
                                         report_generator=report)
        self.addCleanup(self.store.close)
        self.exporter = LocalPlanArtifactExporter(self.home / "exports")
        self.pid = self.store.create(make_proposal())["plan_id"]

    def completed(self):
        self.store.approve(self.pid)
        self.store.advance(self.pid)
        return self.store.synthesize_report(self.pid)

    def test_cannot_export_without_completed_plan_or_saved_report(self):
        with self.assertRaisesRegex(PlanArtifactError, "artifact_requires_completed_plan"):
            self.exporter.export(self.store.get(self.pid))
        self.assertFalse(self.exporter.root.exists())
        self.store.approve(self.pid)
        self.store.advance(self.pid)
        with self.assertRaisesRegex(PlanArtifactError, "artifact_requires_saved_report"):
            self.exporter.export(self.store.get(self.pid))
        self.assertEqual([], self.calls)
        self.assertFalse(self.exporter.root.exists())

    def test_explicit_write_is_single_file_and_idempotent_after_restart(self):
        done = self.completed()
        self.assertFalse(self.exporter.root.exists())
        first = self.exporter.export(done)
        self.assertEqual("CREATED", first["status"])
        self.assertEqual(1, first["local_file_write_count"])
        self.assertEqual(0, first["external_write_count"])
        self.assertEqual("local-agent-report-" + self.pid + ".md", first["filename"])
        f = self.exporter.root / first["filename"]
        raw = f.read_bytes()
        self.assertEqual(first["bytes"], len(raw))
        self.assertEqual(raw, render_saved_report(done))
        self.assertIn(b"## Findings", raw)
        self.assertNotIn(b"![tracking](", raw)
        self.assertIn(b"exact substring membership", raw)
        second = self.exporter.export(self.store.get(self.pid))
        self.assertEqual("EXISTS_IDENTICAL", second["status"])
        self.assertEqual(0, second["local_file_write_count"])
        self.assertEqual(1, len(list(self.exporter.root.glob("*.md"))))
        self.assertEqual(1, len(self.calls))  # Never rerun Ollama.
        reopened = LocalAgentPlanStore(self.home / "plans.sqlite3")
        try:
            self.assertEqual("EXISTS_IDENTICAL",
                             self.exporter.export(reopened.get(self.pid))["status"])
        finally:
            reopened.close()

    def test_cannot_overwrite_existing_changed_output_or_symlink(self):
        done = self.completed()
        first = self.exporter.export(done)
        target = self.exporter.root / first["filename"]
        target.write_text("OWNER CHANGED THIS FILE", encoding="utf-8")
        with self.assertRaisesRegex(PlanArtifactError, "artifact_target_conflict"):
            self.exporter.export(done)
        self.assertEqual("OWNER CHANGED THIS FILE", target.read_text())
        target.unlink()
        secret = self.home / "unrelated.txt"
        # A symlink must be rejected even when the linked file happens to
        # contain the exact expected artifact bytes.
        secret.write_bytes(render_saved_report(done))
        try:
            target.symlink_to(secret)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable on this runner")
        with self.assertRaisesRegex(PlanArtifactError, "artifact_target_conflict"):
            self.exporter.export(done)
        self.assertEqual(render_saved_report(done), secret.read_bytes())

    def test_workspace_symlink_and_report_tampering_fail_closed(self):
        done = self.completed()
        broken = {**done, "report": {**done["report"], "findings": [
            {**done["report"]["findings"][0], "quote": "INVENTED"}]}}
        with self.assertRaisesRegex(PlanArtifactError, "artifact_report_invalid"):
            self.exporter.export(broken)
        self.assertFalse(self.exporter.root.exists())
        elsewhere = self.home / "elsewhere"
        elsewhere.mkdir()
        try:
            self.exporter.root.symlink_to(elsewhere, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable on this runner")
        with self.assertRaisesRegex(PlanArtifactError, "artifact_workspace_unsafe"):
            self.exporter.export(done)
        self.assertEqual([], list(elsewhere.iterdir()))

    def test_export_fails_if_original_report_schema_is_modified(self):
        done = self.completed()
        tampered = {**done, "report": {**done["report"], "new_action": "send_email"}}
        with self.assertRaisesRegex(PlanArtifactError, "artifact_report_invalid"):
            self.exporter.export(tampered)
        self.assertFalse(self.exporter.root.exists())


class ArtifactExportHTTPTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.store = LocalAgentPlanStore(self.home / "plans.sqlite3",
            report_generator=lambda plan: validate_report_result(
                {"summary": "Synthetic report", "findings": [
                    {"step_index": 1, "quote": "56",
                     "interpretation": "Output is 56"}]}, _sources(plan)))
        self.addCleanup(self.store.close)
        self.exporter = LocalPlanArtifactExporter(self.home / "exports")
        self.audit = LocalSecurityAudit(self.home / "security-audit.sqlite3")
        self.addCleanup(self.audit.close)
        self.auth = LocalOperatorAuth.ephemeral_for_tests(
            "only_for_fixture_owner_export_password")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(
            operator_auth=self.auth, security_audit=self.audit,
            local_agent_runner=lambda *_: None,
            local_agent_planner=lambda *_: make_proposal(),
            local_agent_plan_store=self.store,
            plan_artifact_exporter=self.exporter))
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 4)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        _,session = self.auth.login("only_for_fixture_owner_export_password")
        self.cookie = "aas_session=" + session["cookie_token"]
        self.csrf = session["csrf_token"]
        self.pid = self.store.create(make_proposal())["plan_id"]

    def post(self, data, *, logged_in=True, csrf=True, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1",
                                                self.server.server_port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if logged_in:
            headers["Cookie"] = self.cookie
        if csrf:
            headers["X-AAS-CSRF"] = self.csrf
        if origin:
            headers["Origin"] = origin
        connection.request("POST", "/api/local-agent-plan-export",
                           json.dumps(data).encode(), headers)
        response = connection.getresponse()
        output = response.status, json.loads(response.read())
        connection.close()
        return output

    def test_requires_owner_csrf_and_completed_saved_report(self):
        self.assertEqual(401, self.post({"plan_id": self.pid},
                                        logged_in=False)[0])
        self.assertEqual(403, self.post({"plan_id": self.pid}, csrf=False)[0])
        self.assertEqual(403, self.post({"plan_id": self.pid},
                                        origin="https://evil.example")[0])
        self.assertEqual(409, self.post({"plan_id": self.pid})[0])
        self.assertEqual(422, self.post({"plan_id": self.pid,
                                         "path": "../outside.md"})[0])
        self.assertFalse(self.exporter.root.exists())
        self.store.approve(self.pid)
        self.store.advance(self.pid)
        self.assertEqual(409, self.post({"plan_id": self.pid})[0])
        self.store.synthesize_report(self.pid)
        status, receipt = self.post({"plan_id": self.pid})
        self.assertEqual(200, status)
        self.assertEqual("CREATED", receipt["status"])
        self.assertEqual(200, self.post({"plan_id": self.pid})[0])
        self.assertEqual(1, len(list(self.exporter.root.glob("*.md"))))
        events = self.audit.recent(30)
        self.assertTrue(any(event["action"] == "LOCAL_ARTIFACT_EXPORT"
                            and event["outcome"] == "ATTEMPT"
                            for event in events))
        self.assertTrue(any(event["action"] == "LOCAL_ARTIFACT_EXPORT"
                            and event["outcome"] == "SUCCESS"
                            for event in events))
        self.assertTrue(all(event["source_fingerprint"] == ""
                            for event in events))

    def test_audit_unavailable_blocks_new_export_before_file_write(self):
        self.store.approve(self.pid)
        self.store.advance(self.pid)
        self.store.synthesize_report(self.pid)
        self.audit._blocked = True  # CI-only injected security-ledger failure.
        status, body = self.post({"plan_id": self.pid})
        self.assertEqual(503, status)
        self.assertEqual("security_audit_restart_required", body["error"])
        self.assertFalse(self.exporter.root.exists())


if __name__ == "__main__":
    unittest.main()
