"""Slice 70: owner-selected multi-source comparison in reviewed durable task plans.

All Ollama behavior is deterministic synthetic callback. No real device test.
"""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_agent_task_plans import (
    LocalAgentPlanStore, PlanError, propose_readonly_plan,
    validated_document_set_snapshot, document_set_source_metadata)
from local_multi_document_analysis import analyze_multiple_documents
from local_operator_auth import LocalOperatorAuth
from local_server import create_handler

PASSWORD = "slice_70_test_only_owner_password"
QUESTION = "When does the project review meeting start?"
DOCS = [
    {"filename": "fixture-runbook.txt",
     "content": "Operations runbook: client onboarding completed at 16:00 WIB.\n"},
    {"filename": "fixture-meeting.md",
     "content": "Project briefing: review meeting starts at 18:30 WIB on Monday.\n"},
]
STEP = {"tool": "local_multi_document_question", "args": {"question": QUESTION}}


def proposal(goal="Compare my selected work documents"):
    return {"goal": goal, "model": "fixture:model", "steps": [STEP]}


def analyze_fixture(documents, question, model):
    return analyze_multiple_documents(
        documents, question, model,
        transport=lambda payload: {"message": {"content": json.dumps({
            "answer": "The review meeting starts at 18:30 WIB.",
            "insufficient_evidence": False,
            "citations": [{
                "chunk_id": "F2C1",
                "quote": "meeting starts at 18:30 WIB"
            }]
        })}})


class DurableMultiDocPlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "plans.sqlite3"
        self.calls = []
        def analyzer(documents, question, model):
            self.calls.append((documents, question, model))
            return analyze_fixture(documents, question, model)
        self.store = LocalAgentPlanStore(self.path,
                                          multi_document_analyzer=analyzer)
        self.addCleanup(self.store.close)

    def test_plan_proposal_only_receives_metadata_not_document_contents(self):
        selected = validated_document_set_snapshot(DOCS, "fixture:model")
        metadata = [
            {k: entry[k] for k in ("filename", "source_bytes", "sha256")}
            for entry in document_set_source_metadata(selected)
        ]
        captured = []
        def transport(payload):
            captured.append(payload)
            return {"message": {"content": json.dumps({"steps": [STEP]})}}
        prepared = propose_readonly_plan("Compare selected files", "fixture:model",
                                         transport=transport,
                                         document_set_context=metadata)
        self.assertEqual([STEP], prepared["steps"])
        self.assertEqual(1, len(captured))
        body = json.dumps(captured[0])
        for source in DOCS:
            self.assertIn(source["filename"], body)
            self.assertNotIn(source["content"], body)
        self.assertNotIn("tools", captured[0])
        self.assertIn("METADATA ONLY", body)
        created = self.store.create(prepared, document_set_snapshot=DOCS)
        self.assertEqual("PREPARED", created["status"])
        self.assertEqual(["fixture-runbook.txt", "fixture-meeting.md"],
                         [v["filename"] for v in created["document_set_sources"]])
        for source in DOCS:
            self.assertNotIn(source["content"], json.dumps(created))
        self.assertEqual([], self.calls)
        self.assertEqual([], created["results"])

    def test_reviewed_step_uses_exact_snapshots_and_persists_after_restart(self):
        created = self.store.create(proposal(), document_set_snapshot=DOCS)
        with self.assertRaisesRegex(PlanError, "plan_state_conflict"):
            self.store.advance(created["plan_id"])
        self.store.approve(created["plan_id"])
        self.assertEqual([], self.calls)
        reopened = LocalAgentPlanStore(self.path,
            multi_document_analyzer=analyze_fixture)
        try:
            reloaded = reopened.get(created["plan_id"])
            self.assertEqual("APPROVED", reloaded["status"])
            self.assertEqual(2, len(reloaded["document_set_sources"]))
            done = reopened.advance(created["plan_id"])
            self.assertEqual("COMPLETED", done["status"])
            result = json.loads(done["results"][0]["output"])
            self.assertEqual("MULTI_DOCUMENT_ANALYZED_LOCAL", result["status"])
            self.assertEqual(["fixture-runbook.txt", "fixture-meeting.md"],
                             result["selected_files"])
            self.assertEqual("fixture-meeting.md", result["citations"][0]["filename"])
            self.assertEqual("meeting starts at 18:30 WIB",
                             result["citations"][0]["quote"])
            self.assertTrue(result["citations"][0]["verified_exact_match"])
            self.assertFalse(result["interpretation_verified"])
            self.assertEqual(0, result["external_write_count"])
            self.assertEqual(1, result["model_calls"])
            self.assertEqual(0, len(self.calls))  # reopened adapter, not old adapter
        finally:
            reopened.close()

    def test_no_selected_sources_no_tool_and_no_unused_snapshot(self):
        with self.assertRaisesRegex(PlanError, "tool_not_allowed"):
            self.store.create(proposal())
        with self.assertRaisesRegex(PlanError, "tool_not_allowed"):
            self.store.create({"goal": "math", "model": "fixture:model",
                "steps": [{"tool": "calculate", "args": {
                    "a": "7", "b": "8", "operation": "multiply"}}]},
                document_set_snapshot=DOCS)
        with self.assertRaisesRegex(PlanError, "invalid_plan_document_selection"):
            self.store.create(proposal(), document_snapshot=DOCS[0],
                              document_set_snapshot=DOCS)
        with self.assertRaisesRegex(PlanError, "tool_not_allowed"):
            propose_readonly_plan(
                "Compare files", "fixture:model",
                transport=lambda _: {"message": {"content": json.dumps({
                    "steps": [STEP]})}})
        self.assertEqual([], self.store.list())

    def test_input_validation_and_duplicate_names_are_fail_closed(self):
        bad_sets = [
            [],
            [DOCS[0]],
            DOCS + [DOCS[0], DOCS[1]],
            [{"filename":"fixture-A.txt","content":"a"},
             {"filename":"FIXTURE-a.txt","content":"b"}],
            [DOCS[0], {"filename":"../private.md","content":"x"}],
            [DOCS[0], {"filename":"virus.exe","content":"x"}],
            [DOCS[0], {"filename":"second.txt","content":"x" * 2501}],
            [DOCS[0], {"filename":"second.txt","content":"bad\x00text"}],
            [DOCS[0], {"filename":"second.txt","content":"\ud800"}],
            [DOCS[0], {"filename":"second.txt","content":42}],
            [DOCS[0], {"filename":"second.txt","content":"some", "path":"C:\\secret"}],
            [DOCS[0], {"filename":"second.txt","content":""}],
            [DOCS[0], "arbitrary"],
            None,
            True,
        ]
        for docs in bad_sets:
            with self.subTest(data=repr(docs)[:65]), self.assertRaises(PlanError):
                self.store.create(proposal(), document_set_snapshot=docs)
        self.assertEqual([], self.store.list())
        # Protocol allows exactly 2–3 owner-selected documents only.
        metadata = document_set_source_metadata(
            validated_document_set_snapshot(DOCS, "fixture:model"))
        for bad in ([], metadata[:1], metadata + [metadata[0]],
                    [{**metadata[0], "content": "secret"}, metadata[1]]):
            with self.subTest(meta=repr(bad)[:65]), self.assertRaises(PlanError):
                propose_readonly_plan("Compare files", "fixture:model",
                    document_set_context=bad,
                    transport=lambda _: {"message":{"content":'{"steps":[]}'}})

    def test_snapshot_corruption_and_no_adapter_fail_without_tool_dispatch(self):
        old = self.store.create(proposal(), document_set_snapshot=DOCS)
        self.store.approve(old["plan_id"])
        disabled = LocalAgentPlanStore(self.path)
        try:
            with self.assertRaisesRegex(PlanError, "plan_document_set_unavailable"):
                disabled.advance(old["plan_id"])
            self.assertEqual("APPROVED", disabled.get(old["plan_id"])["status"])
        finally:
            disabled.close()
        with self.store.lock, self.store.db:
            row = self.store.db.execute(
                "SELECT document_set_json FROM agent_task_plans WHERE plan_id=?",
                (old["plan_id"],)).fetchone()
            modified = json.loads(row[0])
            modified[1]["content"] += " malicious appended note"
            self.store.db.execute(
                "UPDATE agent_task_plans SET document_set_json=? WHERE plan_id=?",
                (json.dumps(modified), old["plan_id"]))
        with self.assertRaisesRegex(PlanError, "plan_storage_invalid"):
            self.store.get(old["plan_id"])
        self.assertEqual([], self.calls)

    def test_legacy_source_free_plan_migrates_without_new_permission(self):
        legacy = self.path.parent / "legacy.sqlite3"
        with sqlite3.connect(legacy) as db:
            db.execute("""CREATE TABLE agent_task_plans (
                plan_id TEXT PRIMARY KEY, model TEXT NOT NULL, goal TEXT NOT NULL,
                steps_json TEXT NOT NULL, results_json TEXT NOT NULL,
                next_index INTEGER NOT NULL, status TEXT NOT NULL,
                created_utc TEXT NOT NULL, approval_epoch INTEGER,
                memory_refs_json TEXT NOT NULL DEFAULT '[]',
                report_json TEXT NOT NULL DEFAULT '',
                document_json TEXT NOT NULL DEFAULT '')""")
            db.execute("""INSERT INTO agent_task_plans VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("0" * 24, "fixture:model", "Legacy arithmetic",
                 json.dumps([{"tool": "calculate", "args": {
                     "a": "2", "b": "3", "operation": "multiply"}}]),
                 "[]", 0, "PREPARED", "2026-11-05T00:00:00+00:00",
                 None, "[]", "", ""))
        migrated = LocalAgentPlanStore(legacy, multi_document_analyzer=analyze_fixture)
        try:
            old = migrated.get("0" * 24)
            self.assertEqual([], old["document_set_sources"])
            self.assertEqual("PREPARED", old["status"])
            migrated.approve("0" * 24)
            self.assertEqual("COMPLETED", migrated.advance("0" * 24)["status"])
            self.assertEqual("6", migrated.get("0" * 24)["results"][0]["output"])
        finally:
            migrated.close()


class ReviewedMultiDocHTTPTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.calls = []
        self.proposals = []
        def analyzer(documents, question, model):
            self.calls.append((documents, question, model))
            return analyze_fixture(documents, question, model)
        def planner(goal, model, memory_context=None,
                    document_context=None, document_set_context=None):
            self.proposals.append((goal, document_set_context))
            return proposal(goal)
        self.store = LocalAgentPlanStore(Path(temp.name) / "plans.sqlite3",
                                          multi_document_analyzer=analyzer)
        self.addCleanup(self.store.close)
        self.auth = LocalOperatorAuth.ephemeral_for_tests(PASSWORD)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
            create_handler(operator_auth=self.auth,
                           local_agent_runner=lambda p,m: {"mode":"LOCAL_MODEL",
                                                           "answer":"fixture"},
                           local_agent_planner=planner,
                           local_agent_plan_store=self.store,
                           local_multi_document_analyzer=analyzer))
        worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 4)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        _, session = self.auth.login(PASSWORD)
        self.cookie = "aas_session=" + session["cookie_token"]
        self.csrf = session["csrf_token"]

    def request(self, method, path, body=None, auth=True, csrf=True, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1",
                                          self.server.server_port, timeout=8)
        headers = {"Content-Type": "application/json"}
        if auth: headers["Cookie"] = self.cookie
        if csrf: headers["X-AAS-CSRF"] = self.csrf
        if origin: headers["Origin"] = origin
        conn.request(method, path, json.dumps(body).encode() if body is not None
                     else None, headers)
        response = conn.getresponse()
        output = (response.status, json.loads(response.read()))
        conn.close()
        return output

    def test_authenticated_scoped_multi_source_plan_and_manual_click(self):
        endpoint = "/api/local-agent-plan-create"
        payload = {"goal": "Compare the selected project files",
                   "model": "fixture:model", "documents": DOCS}
        self.assertEqual(401, self.request("POST", endpoint, payload,
                                            auth=False)[0])
        self.assertEqual(403, self.request("POST", endpoint, payload,
                                            csrf=False)[0])
        self.assertEqual(403, self.request("POST", endpoint, payload,
                           origin="http://attacker.invalid")[0])
        self.assertEqual([], self.proposals)
        code, created = self.request("POST", endpoint, payload)
        self.assertEqual(200, code, created)
        self.assertEqual("PREPARED", created["status"])
        self.assertEqual(2, len(created["document_set_sources"]))
        self.assertEqual("fixture-meeting.md",
                         created["document_set_sources"][1]["filename"])
        for source in DOCS:
            self.assertNotIn(source["content"], json.dumps(created))
        self.assertEqual(1, len(self.proposals))
        self.assertNotIn(DOCS[0]["content"], json.dumps(self.proposals))
        self.assertEqual([], self.calls)
        pid = created["plan_id"]
        self.assertEqual(409, self.request("POST", "/api/local-agent-plan-next",
                                           {"plan_id": pid})[0])
        self.assertEqual(200, self.request("POST", "/api/local-agent-plan-approve",
                                           {"plan_id": pid})[0])
        self.assertEqual([], self.calls)
        code, done = self.request("POST", "/api/local-agent-plan-next",
                                   {"plan_id": pid})
        self.assertEqual(200, code, done)
        self.assertEqual("COMPLETED", done["status"])
        self.assertEqual(1, len(self.calls))
        result = json.loads(done["results"][0]["output"])
        self.assertEqual("MULTI_DOCUMENT_ANALYZED_LOCAL", result["status"])
        self.assertEqual("fixture-meeting.md", result["citations"][0]["filename"])
        code, reread = self.request("GET",
                                  "/api/local-agent-plan?plan_id=" + pid)
        self.assertEqual(200, code)
        self.assertEqual(2, len(reread["document_set_sources"]))
        self.assertNotIn(DOCS[1]["content"], json.dumps(reread))

    def test_rejects_conflicting_and_bad_sources_before_model(self):
        base = {"goal": "Compare files", "model": "fixture:model"}
        endpoint = "/api/local-agent-plan-create"
        for payload in (
            {**base, "documents": DOCS, "document": DOCS[0]},
            {**base, "documents": [DOCS[0]]},
            {**base, "documents": DOCS + DOCS[:2]},
            {**base, "documents": [DOCS[0], {"filename":"bad.exe","content":"x"}]},
            {**base, "documents": [DOCS[0], {
                "filename":"secret.md","content":"bad\x00text"}]},
        ):
            with self.subTest(payload=repr(payload)[:70]):
                code, _ = self.request("POST", endpoint, payload)
                self.assertEqual(422, code)
        self.assertEqual([], self.proposals)
        self.assertEqual([], self.store.list())

    def test_plan_multi_document_capability_is_separately_required(self):
        disabled_store = LocalAgentPlanStore(":memory:")
        self.addCleanup(disabled_store.close)
        # A selected source cannot authorize a tool when server-side adapter
        # has not explicitly been enabled.
        with self.assertRaisesRegex(PlanError, "tool_not_allowed"):
            disabled_store.create(proposal(), document_set_snapshot=DOCS)


if __name__ == "__main__":
    unittest.main()
