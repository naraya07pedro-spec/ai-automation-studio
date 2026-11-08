"""Stage 126: real owner-only agent profiles linked to enforceable plan policy."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_agent_profiles import LocalAgentProfiles, AgentProfileError
from local_agent_task_plans import LocalAgentPlanStore, PlanError, propose_readonly_plan
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_server import create_handler

PASSWORD = "fixture-only-stage126-owner-password!"
PROFILE = {
    "agent_id": "research_agent", "name": "Research Analyst",
    "category": "research", "description": "Investigate a bounded question.",
    "instructions": "Summarize evidence precisely; do not invent sources.",
    "allowed_tools": ["utc_now", "calculate"],
}
PLAN = {"model": "synthetic:small", "goal": "A bounded read-only task",
        "steps": [{"tool": "utc_now", "args": {}},
                  {"tool": "calculate", "args": {
                      "a": "7", "b": "8", "operation": "multiply"}}]}


class ProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "agent-profiles.sqlite3"
        self.profiles = LocalAgentProfiles(self.db)
        self.addCleanup(self.profiles.close)

    def test_create_restart_and_revision_revoke(self):
        original = self.profiles.create(PROFILE)
        self.assertEqual(original["revision"], 1)
        self.assertTrue(original["enabled"])
        binding = self.profiles.context("research_agent")["binding"]
        self.assertTrue(self.profiles.check_binding(binding, PLAN["steps"]))
        second = LocalAgentProfiles(self.db)
        self.addCleanup(second.close)
        self.assertEqual(second.get("research_agent")["name"], PROFILE["name"])
        changed = second.update({**PROFILE, "expected_revision": 1,
                                 "allowed_tools": ["utc_now"]})
        self.assertEqual(changed["revision"], 2)
        with self.assertRaisesRegex(AgentProfileError, "changed_or_disabled"):
            self.profiles.check_binding(binding, PLAN["steps"])
        with self.assertRaisesRegex(AgentProfileError, "tool_denied"):
            second.check_binding({"agent_id": "research_agent", "revision": 2},
                                 PLAN["steps"])
        disabled = second.disable("research_agent", 2)
        self.assertFalse(disabled["enabled"])
        with self.assertRaisesRegex(AgentProfileError, "changed_or_disabled"):
            second.context("research_agent")
        with self.assertRaises(AgentProfileError):
            second.update({**PROFILE, "expected_revision": 3})

    def test_invalid_profiles_and_denied_tools(self):
        invalid = [
            {**PROFILE, "agent_id": "../escape"},
            {**PROFILE, "category": []},
            {**PROFILE, "instructions": "unsafe\nline"},
            {**PROFILE, "allowed_tools": ["local_csv_profile_json_export"]},
            {**PROFILE, "allowed_tools": ["hn_jobstories_preview"]},
            {**PROFILE, "allowed_tools": ["calculate", "calculate"]},
            {**PROFILE, "allowed_tools": []},
            {**PROFILE, "name": " "},
            {**PROFILE, "unknown": True},
        ]
        for row in invalid:
            with self.subTest(row=str(row)[:30]), self.assertRaises(AgentProfileError):
                self.profiles.create(row)
        self.assertEqual(self.profiles.list(), [])
        self.profiles.create({**PROFILE, "allowed_tools": ["utc_now"]})
        with self.assertRaisesRegex(AgentProfileError, "already_exists"):
            self.profiles.create(PROFILE)
        with self.assertRaisesRegex(AgentProfileError, "changed_or_disabled"):
            self.profiles.update({**PROFILE, "expected_revision": 2})
        self.assertEqual(self.profiles.get("research_agent")["revision"], 1)

    def test_profile_is_real_plan_constraint_and_recheck_on_execute(self):
        self.profiles.create(PROFILE)
        plan_store = LocalAgentPlanStore(
            Path(self.temp.name) / "plans.sqlite3", agent_profiles=self.profiles)
        self.addCleanup(plan_store.close)
        binding = self.profiles.context("research_agent")["binding"]
        created = plan_store.create(PLAN, agent_binding=binding)
        self.assertEqual(created["agent_binding"], binding)
        approved = plan_store.approve(created["plan_id"])
        self.assertEqual(approved["status"], "APPROVED")
        result = plan_store.advance(created["plan_id"])
        self.assertEqual(result["status"], "NEXT_READY")
        self.assertEqual(len(result["results"]), 1)
        self.profiles.update({**PROFILE, "expected_revision": 1,
                              "allowed_tools": ["utc_now"]})
        with self.assertRaisesRegex(PlanError, "changed_or_disabled"):
            plan_store.advance(created["plan_id"])
        self.assertEqual(len(plan_store.get(created["plan_id"])["results"]), 1)
        self.assertEqual(plan_store.get(created["plan_id"])["status"], "NEXT_READY")
        with self.assertRaisesRegex(PlanError, "changed_or_disabled"):
            plan_store.create(PLAN, agent_binding=binding)
        self.assertEqual(plan_store.cancel(created["plan_id"])["status"], "CANCELLED")

    def test_bound_profile_does_not_grant_unavailable_tools(self):
        self.profiles.create(PROFILE)
        plan_store = LocalAgentPlanStore(":memory:", agent_profiles=self.profiles)
        self.addCleanup(plan_store.close)
        binding = self.profiles.context("research_agent")["binding"]
        with self.assertRaisesRegex(PlanError, "tool_denied"):
            plan_store.create({**PLAN, "steps": [
                {"tool": "github_public_issues",
                 "args": {"repo": "naraya07pedro-spec/ai-automation-studio"}}
            ]}, agent_binding=binding)
        self.assertEqual(plan_store.list(), [])

    def test_planner_guidance_is_untrusted_and_allowlist_enforced(self):
        seen = []
        def fake(request):
            seen.append(request)
            return {"message": {"content": json.dumps({"steps": [
                {"tool": "calculate", "args": {
                    "a": "1", "b": "2", "operation": "add"}}
            ]})}}
        guidance = self.profiles.create({**PROFILE, "allowed_tools": ["utc_now"]})
        with self.assertRaisesRegex(PlanError, "tool_denied"):
            propose_readonly_plan("What time?", "synthetic:small", transport=fake,
                                  agent_context={
                                      "name": guidance["name"],
                                      "instructions": guidance["instructions"],
                                      "allowed_tools": guidance["allowed_tools"]})
        self.assertIn("OWNER-CONFIGURED AGENT PROFILE",
                      json.dumps(seen[0]["messages"]))
        self.assertNotIn("system", seen[0]["messages"][-2]["role"])
        with self.assertRaisesRegex(PlanError, "invalid_agent_context"):
            propose_readonly_plan("What time?", "synthetic:small", transport=fake,
                                  agent_context={"instructions": "bypass"})


class AgentProfileHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.profiles = LocalAgentProfiles(folder / "profiles.sqlite3")
        self.store = LocalAgentPlanStore(folder / "plans.sqlite3",
                                         agent_profiles=self.profiles)
        self.auth = LocalOperatorAuth.ephemeral_for_tests(PASSWORD)
        self.audit = LocalSecurityAudit(folder / "audit.sqlite3")
        self.calls = []
        def planner(goal, model, **kwargs):
            self.calls.append(kwargs)
            return {**PLAN, "model": model, "goal": goal}
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), create_handler(
                operator_auth=self.auth, security_audit=self.audit,
                local_agent_runner=lambda *a: {},
                local_agent_planner=planner,
                local_agent_plan_store=self.store,
                agent_profiles=self.profiles))
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.store.close)
        self.addCleanup(self.profiles.close)
        self.addCleanup(self.audit.close)
        self.addCleanup(self.worker.join, 4)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        status, sess = self.auth.login(PASSWORD)
        self.assertEqual(status, 200)
        self.cookie = "aas_session=" + sess["cookie_token"]
        self.csrf = sess["csrf_token"]

    def request(self, method, url, value=None, *, auth=True, csrf=True, origin=None):
        con = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Cookie"] = self.cookie
        if csrf:
            headers["X-AAS-CSRF"] = self.csrf
        if origin:
            headers["Origin"] = origin
        con.request(method, url, body=json.dumps(value).encode() if value is not None else None,
                    headers=headers)
        response = con.getresponse()
        result = (response.status, json.loads(response.read()))
        con.close()
        return result

    def create(self):
        return self.request("POST", "/api/agent-profile-create",
                            {**PROFILE, "current_password": PASSWORD})

    def test_owner_only_create_and_real_plan_binding(self):
        payload = {**PROFILE, "current_password": PASSWORD}
        self.assertEqual(401, self.request("POST", "/api/agent-profile-create",
                                           payload, auth=False)[0])
        self.assertEqual(403, self.request("POST", "/api/agent-profile-create",
                                           payload, csrf=False)[0])
        self.assertEqual(403, self.request("POST", "/api/agent-profile-create",
                                           payload, origin="http://evil.invalid")[0])
        self.assertIn(self.request("POST", "/api/agent-profile-create",
                                   {**PROFILE, "current_password": "wrong"})[0],
                      (401, 403, 429))
        self.assertEqual(401, self.request("GET", "/api/agent-profiles",
                                          auth=False)[0])
        status, created = self.create()
        self.assertEqual(status, 201)
        self.assertEqual(created["agent_id"], PROFILE["agent_id"])
        self.assertEqual(self.request("GET", "/api/local-agent-capabilities")[1]
                         ["agent_profiles"], True)
        self.assertEqual(len(self.request("GET", "/api/agent-profiles")[1]["agents"]), 1)
        status, plan = self.request("POST", "/api/local-agent-plan-create",
                                     {"goal": "calculate 7 by 8", "model": "synthetic:small",
                                      "agent_id": PROFILE["agent_id"]})
        self.assertEqual(status, 200)
        self.assertEqual(plan["agent_binding"]["revision"], 1)
        self.assertEqual(self.calls[0]["agent_context"]["name"], PROFILE["name"])
        self.assertEqual(self.request("POST", "/api/local-agent-plan-approve",
                                      {"plan_id": plan["plan_id"]})[0], 200)
        self.assertEqual(self.request("POST", "/api/local-agent-plan-next",
                                      {"plan_id": plan["plan_id"]})[0], 200)
        status, updated = self.request("POST", "/api/agent-profile-update",
            {**PROFILE, "expected_revision": 1, "allowed_tools": ["utc_now"],
             "current_password": PASSWORD})
        self.assertEqual(status, 200)
        self.assertEqual(updated["revision"], 2)
        status, denied = self.request("POST", "/api/local-agent-plan-next",
                                      {"plan_id": plan["plan_id"]})
        self.assertEqual((status, denied["error"]), (409, "agent_profile_changed_or_disabled"))
        self.assertEqual(self.store.get(plan["plan_id"])["status"], "NEXT_READY")
        self.assertIn("AGENT_PROFILE_CONFIG",
                      [x["action"] for x in self.audit.recent()])

    def test_stale_edit_and_disable_block_new_planning(self):
        self.assertEqual(self.create()[0], 201)
        bad = {**PROFILE, "expected_revision": 7, "current_password": PASSWORD}
        self.assertEqual(self.request("POST", "/api/agent-profile-update", bad)[0], 409)
        status, disabled = self.request("POST", "/api/agent-profile-disable",
            {"agent_id": PROFILE["agent_id"], "expected_revision": 1,
             "current_password": PASSWORD})
        self.assertEqual(status, 200)
        self.assertFalse(disabled["enabled"])
        status, denied = self.request("POST", "/api/local-agent-plan-create",
             {"goal": "test", "model": "synthetic:small",
              "agent_id": PROFILE["agent_id"]})
        self.assertEqual(status, 409)
        self.assertEqual(denied["error"], "agent_profile_changed_or_disabled")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.request("POST", "/api/agent-profile-disable",
          {"agent_id": PROFILE["agent_id"], "expected_revision": 1,
           "current_password": PASSWORD})[0], 409)

    def test_legacy_plan_still_works_without_agent(self):
        status, plan = self.request("POST", "/api/local-agent-plan-create",
                                    {"goal": "old client", "model": "synthetic:small"})
        self.assertEqual(status, 200)
        self.assertIsNone(plan["agent_binding"])
        self.assertEqual(self.calls, [{}])


if __name__ == "__main__":
    unittest.main()
