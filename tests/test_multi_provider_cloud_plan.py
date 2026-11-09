"""Three-provider paid proposal -> owner-reviewed deterministic local tools.

Synthetic responses ONLY: no real model charge, live account, or VPS access.
"""
import http.cookiejar
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
from unittest.mock import patch
import local_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from local_agent_task_plans import LocalAgentPlanStore
from local_cloud_plan_bridge import CloudPlanBridge, CloudPlanError, paid_planning_prompt
from local_model_safety import LocalModelSafetyState
from local_on_demand_remote_ai import LocalOnDemandRemoteAI, OnDemandRemoteAIError
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_server import create_handler
from local_workflow_core import LocalWorkflowCore

PASSWORD = "Owner-Cloud-Provider-Plan-Test-2026!"
GOAL = "Calculate 21 plus 21, then show UTC time"
STEPS = {"steps": [
    {"tool": "calculate", "args": {"a": "21", "b": "21", "operation": "add"}},
    {"tool": "utc_now", "args": {}},
]}
CLAUDE = "claude-haiku-5-5"
GEMINI = "gemini-2.5-flash"
PROVIDERS = (
    ("anthropic_messages", CLAUDE, "REMOTE_ANTHROPIC_READ_ONLY"),
    ("gemini_generate_content", GEMINI, "REMOTE_GEMINI_READ_ONLY"),
)


def reply(mode, content=None):
    return {"mode": mode, "answer": json.dumps(STEPS) if content is None else content,
            "tools_used": [], "external_dispatch_count": 0,
            "usage": {"input_tokens": 55, "output_tokens": 25}}


def request(rid, provider, model, *, consent=True):
    return {"request_id": rid, "provider": provider, "model": model,
            "goal": GOAL, "paid_api_consent": consent}


class ThreeProviderPlanBackend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.safety = LocalModelSafetyState(base / "safety.sqlite3")
        self.calls = []
        def runner(mode):
            def call(prompt, model):
                self.calls.append((mode, model, prompt))
                return reply(mode)
            return call
        self.remote = LocalOnDemandRemoteAI(
            base / "remote.sqlite3",
            runner=runner("REMOTE_OPENAI_READ_ONLY"),
            additional_runners={p: runner(m) for p, _, m in PROVIDERS})
        self.remote.attach_gate(self.safety, threading.BoundedSemaphore(1), threading.Lock())
        self.plans = LocalAgentPlanStore(base / "plans.sqlite3")
        self.bridge = CloudPlanBridge(self.remote, self.plans)

    def tearDown(self):
        self.remote.close()
        self.plans.close()
        self.safety.close()

    def test_each_provider_proposes_only_then_owner_executes_real_local_steps(self):
        for number, (provider, model, _) in enumerate(PROVIDERS, 1):
            with self.subTest(provider=provider):
                rid = format(number, "024x") + "0" * 8
                result = self.bridge.propose(request(rid, provider, model))
                self.assertEqual(result["provider"], provider)
                self.assertEqual(result["plan"]["model"], model)
                self.assertEqual(result["plan"]["status"], "PREPARED")
                self.assertEqual(result["provider_calls_this_action"], 1)
                self.assertEqual(result["plan"]["results"], [])
                self.assertEqual(self.plans.approve(rid[:24])["status"], "APPROVED")
                self.assertEqual(self.plans.advance(rid[:24])["results"][0]["tool"], "calculate")
                self.assertEqual(self.plans.advance(rid[:24])["status"], "COMPLETED")
                before = len(self.calls)
                recovery = self.bridge.recover({
                    "request_id": rid, "provider": provider, "model": model, "goal": GOAL})
                self.assertEqual(recovery["plan"]["status"], "COMPLETED")
                self.assertEqual(recovery["provider_calls_this_action"], 0)
                self.assertEqual(len(self.calls), before)

    def test_cross_provider_receipt_swap_is_denied_without_new_dispatch(self):
        rid = "a" * 32
        self.bridge.propose(request(rid, "anthropic_messages", CLAUDE))
        before = len(self.calls)
        with self.assertRaisesRegex(OnDemandRemoteAIError, "binding_mismatch"):
            self.bridge.recover({
                "request_id": rid, "provider": "openai_responses",
                "model": CLAUDE, "goal": GOAL})
        with self.assertRaisesRegex(OnDemandRemoteAIError, "binding_mismatch"):
            self.remote.bound_receipt(
                rid, CLAUDE, paid_planning_prompt(GOAL), provider="gemini_generate_content")
        with self.assertRaisesRegex(OnDemandRemoteAIError, "binding_mismatch"):
            self.bridge.recover({
                "request_id": rid, "provider": "anthropic_messages",
                "model": CLAUDE, "goal": "different goal"})
        self.assertEqual(len(self.calls), before)
        self.assertEqual(len(self.plans.list()), 1)

    def test_bad_provider_model_consent_and_fields_fail_before_paid_call(self):
        invalid = [
            request("b" * 32, "unknown", CLAUDE),
            request("b" * 32, "anthropic_messages", "bad/model"),
            request("b" * 32, "gemini_generate_content", "name?bad"),
            request("b" * 32, "gemini_generate_content", GEMINI, consent=False),
            {**request("b" * 32, "anthropic_messages", CLAUDE), "unsafe_tool": "shell"},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(CloudPlanError):
                self.bridge.propose(payload)
        with self.assertRaises(CloudPlanError):
            self.bridge.recover({
                "request_id": "b" * 32, "model": CLAUDE, "goal": GOAL,
                "provider": "invalid"})
        self.assertEqual(self.calls, [])
        self.assertEqual(self.plans.list(), [])

    def test_remote_model_allowlist_denies_other_provider_before_claim(self):
        with tempfile.TemporaryDirectory() as root:
            remote = LocalOnDemandRemoteAI(
                Path(root) / "allow.sqlite3",
                additional_runners={
                    p: (lambda prompt, model, mode=m: reply(mode))
                    for p, _, m in PROVIDERS},
                model_allowlist=["anthropic_messages:" + CLAUDE])
            remote.attach_gate(LocalModelSafetyState(), threading.BoundedSemaphore(1), threading.Lock())
            plans = LocalAgentPlanStore()
            try:
                bridge = CloudPlanBridge(remote, plans)
                with self.assertRaisesRegex(OnDemandRemoteAIError, "not_owner_allowlisted"):
                    bridge.propose(request("c" * 32, "gemini_generate_content", GEMINI))
                self.assertEqual(remote.recent()["receipts"], [])
                self.assertEqual(plans.list(), [])
            finally:
                remote.close()
                plans.close()

    def test_malicious_provider_result_never_executes_tools(self):
        self.remote.additional_runners["anthropic_messages"] = lambda *_: reply(
            "REMOTE_ANTHROPIC_READ_ONLY",
            json.dumps({"steps": [{"tool": "email_send", "args": {}}]}))
        with self.assertRaises(CloudPlanError):
            self.bridge.propose(request("d" * 32, "anthropic_messages", CLAUDE))
        self.assertEqual(self.plans.list(), [])
        self.assertEqual(self.remote.receipt("d" * 32)["status"], "COMPLETED")

    def test_legacy_openai_no_provider_field_still_recovers(self):
        rid = "e" * 32
        result = self.bridge.propose({
            "request_id": rid, "model": "gpt-4.1-mini",
            "goal": GOAL, "paid_api_consent": True})
        self.assertEqual(result["provider"], "openai_responses")
        recovered = self.bridge.recover({
            "request_id": rid, "model": "gpt-4.1-mini", "goal": GOAL})
        self.assertEqual(recovered["provider"], "openai_responses")


class ProviderOnlyHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        LocalOperatorAuth.provision(base / "operator.json", PASSWORD)
        self.owner = LocalOperatorAuth.load(base / "operator.json")
        self.safety = LocalModelSafetyState(base / "safety.sqlite3")
        self.audit = LocalSecurityAudit(base / "audit.sqlite3")
        self.core = LocalWorkflowCore(base / "core.sqlite3")
        self.plans = LocalAgentPlanStore(base / "plans.sqlite3")
        self.calls = []
        def anthropic(prompt, model):
            self.calls.append((prompt, model))
            return reply("REMOTE_ANTHROPIC_READ_ONLY")
        # NO OpenAI runner: this specifically regresses the prior disabled bridge.
        self.remote = LocalOnDemandRemoteAI(
            base / "remote.sqlite3",
            additional_runners={"anthropic_messages": anthropic})
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            create_handler(local_core=self.core, operator_auth=self.owner,
                           security_audit=self.audit, model_safety_state=self.safety,
                           on_demand_ai=self.remote, local_agent_plan_store=self.plans))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        self.opener = build_opener(ProxyHandler({}),
                                  HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = None

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.remote.close()
        self.plans.close()
        self.core.close()
        self.audit.close()
        self.safety.close()

    def post(self, route, payload, *, csrf=True):
        headers = {"Content-Type": "application/json"}
        if csrf and self.csrf:
            headers["X-AAS-CSRF"] = self.csrf
        with self.opener.open(
                Request(self.url + route, data=json.dumps(payload).encode(),
                        headers=headers, method="POST"), timeout=7) as response:
            return json.load(response)

    def get(self, route):
        with self.opener.open(self.url + route, timeout=7) as response:
            return json.load(response)

    def test_anthropic_only_backend_can_propose_review_execute_and_recover(self):
        with self.assertRaises(HTTPError) as unauth:
            self.get("/api/local-agent-capabilities")
        self.assertEqual(unauth.exception.code, 401)
        self.csrf = self.post("/api/operator-login",
                              {"password": PASSWORD})["csrf_token"]
        self.assertTrue(self.get("/api/local-agent-capabilities")["cloud_plan_review"])
        self.assertEqual(self.get("/api/local-agent-capabilities")
                         ["remote_on_demand_providers"], ["anthropic_messages"])
        rid = "f" * 32
        body = {**request(rid, "anthropic_messages", CLAUDE),
                "current_password": PASSWORD}
        with self.assertRaises(HTTPError) as invalid:
            self.post("/api/cloud-ai-plan-create", body, csrf=False)
        self.assertEqual(invalid.exception.code, 403)
        self.assertEqual(self.calls, [])
        result = self.post("/api/cloud-ai-plan-create", body)
        self.assertEqual(result["provider"], "anthropic_messages")
        self.assertEqual(result["plan"]["status"], "PREPARED")
        self.assertEqual(len(self.calls), 1)
        pid = result["plan"]["plan_id"]
        self.post("/api/local-agent-plan-approve", {"plan_id": pid})
        self.post("/api/local-agent-plan-next", {"plan_id": pid})
        self.post("/api/local-agent-plan-next", {"plan_id": pid})
        recovery = self.post("/api/cloud-ai-plan-recover", {
            "request_id": rid, "provider": "anthropic_messages", "model": CLAUDE,
            "goal": GOAL, "current_password": PASSWORD})
        self.assertEqual(recovery["provider_calls_this_action"], 0)
        self.assertEqual(recovery["plan"]["status"], "COMPLETED")
        self.assertEqual(len(self.calls), 1)

class NativeProviderOnlyBoot(unittest.TestCase):
    def test_native_cloud_only_server_allocates_plan_store_without_openai_or_ollama(self):
        class NoSocketServer:
            def __init__(self, address, handler):
                self.server_port = address[1]
            def serve_forever(self):
                return
            def server_close(self):
                return
        for provider in ("anthropic", "gemini"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                private = home / ".ai-automation-studio"
                private.mkdir(mode=0o700)
                LocalOperatorAuth.provision(private / "operator.json", PASSWORD)
                with patch.object(local_server.Path, "home", return_value=home), \
                     patch.object(local_server, "ThreadingHTTPServer", NoSocketServer), \
                     patch.object(local_server, "read_runtime_secret",
                                  return_value="fixture-private-key-long-enough-2026") as secret, \
                     patch.object(local_server, "installed_models",
                                  side_effect=AssertionError("Ollama used")), \
                     patch.object(local_server, "run_agent",
                                  side_effect=AssertionError("Ollama inference used")):
                    local_server.start_local_server(
                        enable_anthropic_on_demand=provider == "anthropic",
                        enable_gemini_on_demand=provider == "gemini")
                self.assertTrue((private / "agent-task-plans.sqlite3").exists(),
                                "Cloud-only provider must have durable reviewed-plan storage")
                self.assertTrue((private / "on-demand-remote-ai.sqlite3").exists())
                self.assertTrue((private / "ollama-safety.sqlite3").exists(),
                                "Cloud-only inference still needs a durable safety checkpoint")
                secret.assert_called_once_with(
                    "AAS_ANTHROPIC_API_KEY" if provider == "anthropic"
                    else "AAS_GEMINI_API_KEY")


if __name__ == "__main__":
    unittest.main()
