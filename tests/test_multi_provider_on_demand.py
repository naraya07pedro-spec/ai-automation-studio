"""Stage 127 three-provider backend acceptance without actual billable requests."""
from contextlib import closing
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_model_safety import LocalModelSafetyState
from local_on_demand_remote_ai import LocalOnDemandRemoteAI, OnDemandRemoteAIError
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_server import create_handler
from multi_provider_readonly import (
    run_anthropic_readonly, run_gemini_readonly,
    ANTHROPIC_ENDPOINT, GEMINI_HOST,
)
from openai_readonly_responses import RemoteInferenceError

KEY = "test-secret-provider-key-2026-very-private"
PASS = "Owner-stage127-only-password-long"
P = "Describe the current tool policy in one sentence."


def anthropic_response():
    return {"type": "message", "role": "assistant", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "Hello."}],
            "usage": {"input_tokens": 14, "output_tokens": 5}}


def gemini_response():
    return {"candidates": [{"finishReason": "STOP",
             "content": {"role": "model", "parts": [{"text": "Halo."}]}}],
            "usageMetadata": {"promptTokenCount": 14,
                              "candidatesTokenCount": 6}}


class FakeResponse:
    status = 200
    def __init__(self, obj):
        self.raw = json.dumps(obj).encode()
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False
    def read(self, n=-1):
        return self.raw[:n]


class FakeOpener:
    def __init__(self, body):
        self.body = body
        self.calls = []
    def open(self, request, timeout):
        self.calls.append((request, timeout))
        return FakeResponse(self.body)


class OfficialProviderAdapters(unittest.TestCase):
    def test_anthropic_exact_host_payload_headers_and_no_tools(self):
        stub = FakeOpener(anthropic_response())
        result = run_anthropic_readonly(P, "claude-haiku-5-5", api_key=KEY,
                                       opener=stub)
        self.assertEqual(result["mode"], "REMOTE_ANTHROPIC_READ_ONLY")
        self.assertEqual(result["answer"], "Hello.")
        self.assertEqual(result["usage"],
                         {"input_tokens": 14, "output_tokens": 5})
        self.assertEqual(len(stub.calls), 1)
        request, timeout = stub.calls[0]
        self.assertEqual(timeout, 35)
        self.assertEqual(request.full_url, ANTHROPIC_ENDPOINT)
        self.assertEqual(request.get_method(), "POST")
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers["x-api-key"], KEY)
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        body = json.loads(request.data)
        self.assertEqual(body["model"], "claude-haiku-5-5")
        self.assertEqual(body["messages"][0]["content"], P)
        self.assertFalse(body["stream"])
        self.assertEqual(body["max_tokens"], 512)
        self.assertNotIn("tools", body)
        self.assertNotIn("tool_choice", body)
        self.assertNotIn(KEY, repr(result))

    def test_gemini_exact_host_payload_no_tools_and_safe_model(self):
        stub = FakeOpener(gemini_response())
        result = run_gemini_readonly(P, "gemini-2.5-flash",
                                    api_key=KEY, opener=stub)
        self.assertEqual(result["mode"], "REMOTE_GEMINI_READ_ONLY")
        self.assertEqual(result["answer"], "Halo.")
        self.assertEqual(result["usage"],
                         {"input_tokens": 14, "output_tokens": 6})
        self.assertEqual(len(stub.calls), 1)
        req, _ = stub.calls[0]
        self.assertEqual(req.full_url,
                         GEMINI_HOST + "/v1beta/models/gemini-2.5-flash:generateContent")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual({k.lower(): v for k,v in req.header_items()}
                         ["x-goog-api-key"], KEY)
        body = json.loads(req.data)
        self.assertEqual(body["generationConfig"]["candidateCount"], 1)
        self.assertEqual(body["generationConfig"]["maxOutputTokens"], 512)
        self.assertEqual(body["contents"][0]["parts"][0]["text"], P)
        self.assertNotIn("tools", body)
        for model in ("../../untrusted", "name/path", "name?key=foo",
                      "models/gemini-2.5-flash", "", "x#fragment"):
            with self.subTest(model=model), self.assertRaises(RemoteInferenceError):
                run_gemini_readonly(P, model, api_key=KEY, opener=stub)
        self.assertEqual(len(stub.calls), 1)

    def test_rejections_and_ambiguous_response_are_not_retried(self):
        malformed = [
            {**anthropic_response(), "stop_reason": "max_tokens"},
            {**anthropic_response(), "content": [{"type": "tool_use", "name": "web"}]},
            {**anthropic_response(), "usage": {"input_tokens": True, "output_tokens": 5}},
            {**anthropic_response(), "usage": {"input_tokens": 5, "output_tokens": 900}},
        ]
        for value in malformed:
            with self.subTest(value=value):
                stub = FakeOpener(value)
                with self.assertRaisesRegex(RemoteInferenceError, "uncertain"):
                    run_anthropic_readonly(P, "claude-haiku-5-5",
                                           api_key=KEY, opener=stub)
                self.assertEqual(len(stub.calls), 1)
        gemini_bad = [
            {**gemini_response(), "candidates": []},
            {**gemini_response(), "candidates": [{"finishReason": "MAX_TOKENS"}]},
            {**gemini_response(), "candidates": [{"finishReason": "STOP",
                "content": {"role": "model", "parts": [{"functionCall": {}}]}}]},
            {**gemini_response(), "usageMetadata": {"promptTokenCount": 10,
                                                    "candidatesTokenCount": 999}},
        ]
        for value in gemini_bad:
            with self.subTest(value=value):
                stub = FakeOpener(value)
                with self.assertRaisesRegex(RemoteInferenceError, "uncertain"):
                    run_gemini_readonly(P, "gemini-2.5-flash",
                                        api_key=KEY, opener=stub)
                self.assertEqual(len(stub.calls), 1)

        class RejectOpener:
            def __init__(self, code):
                self.code, self.calls = code, 0
            def open(self, req, timeout):
                self.calls += 1
                raise HTTPError(req.full_url, self.code, "secret failure", {}, None)
        denied = RejectOpener(429)
        with self.assertRaisesRegex(RemoteInferenceError, "provider_rejected"):
            run_anthropic_readonly(P, "claude-haiku-5-5", api_key=KEY, opener=denied)
        self.assertEqual(denied.calls, 1)
        uncertain = RejectOpener(500)
        with self.assertRaisesRegex(RemoteInferenceError, "uncertain"):
            run_gemini_readonly(P, "gemini-2.5-flash", api_key=KEY, opener=uncertain)
        self.assertEqual(uncertain.calls, 1)


def payload(rid, provider, model):
    return {"request_id":rid, "provider":provider, "model":model,
            "prompt":P, "paid_api_consent":True}


class PersistentProviderLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.safety = LocalModelSafetyState(self.home / "safety.sqlite3")
        self.calls = []
        def make_runner(provider, mode):
            def runner(prompt, model):
                self.calls.append((provider, prompt, model))
                return {"mode": mode, "answer": provider + "-answer",
                        "usage": {"input_tokens": 9, "output_tokens": 3},
                        "tools_used": [], "external_dispatch_count": 0}
            return runner
        self.openai = make_runner("openai_responses", "REMOTE_OPENAI_READ_ONLY")
        self.anthropic = make_runner("anthropic_messages", "REMOTE_ANTHROPIC_READ_ONLY")
        self.gemini = make_runner("gemini_generate_content", "REMOTE_GEMINI_READ_ONLY")
        self.gate = threading.BoundedSemaphore(1)
        self.lock = threading.Lock()
        self.file = self.home / "remote.sqlite3"
        self.store = LocalOnDemandRemoteAI(
            self.file, runner=self.openai, additional_runners={
                "anthropic_messages": self.anthropic,
                "gemini_generate_content": self.gemini})
        self.store.attach_gate(self.safety, self.gate, self.lock)

    def tearDown(self):
        self.store.close()
        self.safety.close()

    def test_three_models_shared_cap_and_immutable_provider_receipts(self):
        for i, (provider, model) in enumerate((
            ("openai_responses", "gpt-5"), ("anthropic_messages", "claude-haiku-5-5"),
            ("gemini_generate_content", "gemini-2.5-flash"))):
            result = self.store.run(payload(f"{i+1:032x}", provider, model))
            self.assertEqual(result["status"], "COMPLETED")
            self.assertEqual(result["provider"], provider)
            self.assertEqual(result["model"], model)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len(self.store.recent()["receipts"]), 3)
        self.assertNotIn("answer", self.store.recent()["receipts"][0])
        self.assertEqual(self.store.enabled_providers(),
                         ["openai_responses", "anthropic_messages", "gemini_generate_content"])
        with self.assertRaisesRegex(OnDemandRemoteAIError, "already_used"):
            self.store.run(payload(f"{1:032x}", "gemini_generate_content", "gemini-2.5-flash"))
        with self.assertRaisesRegex(OnDemandRemoteAIError, "binding_mismatch"):
            self.store.bound_receipt(f"{2:032x}", "claude-haiku-5-5", P)
        self.assertEqual(len(self.calls), 3)
        # Original OpenAI paid-plan receipt binding remains strict.
        self.assertEqual(self.store.bound_receipt(f"{1:032x}", "gpt-5", P)
                         ["provider"], "openai_responses")

    def test_disabled_provider_rejected_without_claim_and_no_fallback(self):
        self.store.close()
        self.store = LocalOnDemandRemoteAI(
            self.file, additional_runners={"gemini_generate_content": self.gemini})
        self.store.attach_gate(self.safety, self.gate, self.lock)
        request = payload("a"*32, "anthropic_messages", "claude-haiku-5-5")
        with self.assertRaisesRegex(OnDemandRemoteAIError, "provider_not_enabled"):
            self.store.run(request)
        with self.assertRaisesRegex(OnDemandRemoteAIError, "provider_not_enabled"):
            self.store.run({k: v for k, v in request.items() if k != "provider"})
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.recent()["receipts"], [])
        self.assertEqual(self.safety.phase(), "IDLE")
        ok = self.store.run(payload("b"*32, "gemini_generate_content", "gemini-2.5-flash"))
        self.assertEqual(ok["provider"], "gemini_generate_content")
        self.assertEqual(self.calls[0][0], "gemini_generate_content")

    def test_provider_specific_invalid_input_does_not_use_daily_budget(self):
        for provider, model in (("gemini_generate_content", "a/b"),
                                ("anthropic_messages", "a:b"),
                                ("auto", "gpt-5")):
            with self.subTest(provider=provider), self.assertRaises(OnDemandRemoteAIError):
                self.store.run(payload("f"*32, provider, model))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.recent()["receipts"], [])

    def test_migrated_legacy_openai_rows_keep_provider(self):
        self.store.close()
        with closing(sqlite3.connect(self.file)) as db:
            db.execute("ALTER TABLE remote_asks RENAME TO remote_asks_v2")
            db.execute("""CREATE TABLE remote_asks (
                request_id TEXT PRIMARY KEY,prompt_sha256 TEXT NOT NULL,
                model TEXT NOT NULL,status TEXT NOT NULL,answer TEXT,usage_json TEXT,
                created_utc TEXT NOT NULL,finished_utc TEXT)""")
            db.execute("""INSERT INTO remote_asks(request_id,prompt_sha256,model,status,
                         created_utc) VALUES (?,?,?,?,?)""",
                       ("c"*32,"0"*64,"gpt-5","OUTCOME_UNCERTAIN",
                        "2026-11-08T00:00:00+00:00"))
            db.execute("DROP TABLE remote_asks_v2")
            db.commit()
        self.store = LocalOnDemandRemoteAI(self.file, runner=self.openai)
        self.store.attach_gate(self.safety, self.gate, self.lock)
        self.assertEqual(self.store.receipt("c"*32)["provider"], "openai_responses")
        self.assertEqual(self.store.receipt("c"*32)["status"], "OUTCOME_UNCERTAIN")

    def test_uncertain_provider_latches_shared_model_checkpoint(self):
        def ambiguous(*args):
            self.calls.append(("anthropic_messages", "attempt", "test"))
            raise RemoteInferenceError("remote_response_uncertain")
        self.store.additional_runners["anthropic_messages"] = ambiguous
        with self.assertRaisesRegex(OnDemandRemoteAIError, "check_usage"):
            self.store.run(payload("a"*32, "anthropic_messages", "claude-haiku-5-5"))
        self.assertEqual(self.store.receipt("a"*32)["status"], "OUTCOME_UNCERTAIN")
        self.assertEqual(self.safety.phase(), "UNCERTAIN")
        with self.assertRaisesRegex(OnDemandRemoteAIError, "quarantined"):
            self.store.run(payload("b"*32, "gemini_generate_content", "gemini-2.5-flash"))
        self.assertEqual(len(self.calls), 1)

    def test_global_daily_cap_applies_cross_provider(self):
        for i in range(5):
            provider, model = (
                ("gemini_generate_content", "gemini-2.5-flash") if i % 2 == 0
                else ("anthropic_messages", "claude-haiku-5-5"))
            self.store.run(payload(f"{i:032x}", provider, model))
        with self.assertRaisesRegex(OnDemandRemoteAIError, "daily_cap_reached"):
            self.store.run(payload("f"*32, "openai_responses", "gpt-5"))
        self.assertEqual(len(self.calls), 5)


    def test_owner_allowlist_prevents_any_unreviewed_model_dispatch(self):
        self.store.close()
        self.store = LocalOnDemandRemoteAI(
            self.file, runner=self.openai,
            additional_runners={"gemini_generate_content":self.gemini},
            model_allowlist=["gemini_generate_content:gemini-2.5-flash"],
            daily_attempt_limit=1)
        self.store.attach_gate(self.safety,self.gate,self.lock)
        self.assertEqual(self.store.enabled_providers(),["gemini_generate_content"])
        self.assertEqual(self.store.approved_models(),
                         [{"provider":"gemini_generate_content",
                           "model":"gemini-2.5-flash"}])
        self.assertTrue(self.store.recent()["remote_model_allowlist_enforced"])
        self.assertEqual(self.store.recent()["maximum_attempts_per_utc_day"],1)
        for request in [
            payload("a"*32,"openai_responses","gpt-5"),
            payload("b"*32,"gemini_generate_content","gemini-2.5-pro"),
        ]:
            with self.assertRaisesRegex(OnDemandRemoteAIError,"not_owner_allowlisted"):
                self.store.run(request)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.store.recent()["receipts"],[])
        self.assertEqual(self.safety.phase(),"IDLE")
        first=self.store.run(payload("c"*32,"gemini_generate_content","gemini-2.5-flash"))
        self.assertEqual(first["status"],"COMPLETED")
        with self.assertRaisesRegex(OnDemandRemoteAIError,"daily_cap_reached"):
            self.store.run(payload("d"*32,"gemini_generate_content","gemini-2.5-flash"))
        self.assertEqual(len(self.calls),1)
        self.store.close()
        # Omission of CLI policy flags on restart MUST NOT restore unlimited models.
        self.store=LocalOnDemandRemoteAI(self.file,additional_runners={
            "gemini_generate_content":self.gemini})
        self.store.attach_gate(self.safety,self.gate,self.lock)
        self.assertEqual(self.store.daily_attempt_limit,1)
        self.assertEqual(self.store.approved_models(),[{
            "provider":"gemini_generate_content","model":"gemini-2.5-flash"}])
        with self.assertRaisesRegex(OnDemandRemoteAIError,"daily_cap_reached"):
            self.store.run(payload("e"*32,"gemini_generate_content","gemini-2.5-flash"))
        self.assertEqual(len(self.calls),1)

    def test_restart_will_not_silently_change_saved_models_or_daily_cap(self):
        self.store.close()
        self.store=LocalOnDemandRemoteAI(self.file,runner=self.openai,
            model_allowlist=["openai_responses:gpt-5"],daily_attempt_limit=2)
        self.store.attach_gate(self.safety,self.gate,self.lock)
        self.store.close()
        for extra in [
            {"runner":self.openai,"model_allowlist":["openai_responses:gpt-4"]},
            {"runner":self.openai,"model_allowlist":["openai_responses:gpt-5"],
             "daily_attempt_limit":3},
        ]:
            with self.subTest(extra=extra),self.assertRaisesRegex(
                   OnDemandRemoteAIError,"offline_owner_review"):
                LocalOnDemandRemoteAI(self.file,**extra)
        # Match/no-op flags, or no flags, honor identical persisted policy.
        self.store=LocalOnDemandRemoteAI(self.file,runner=self.openai)
        self.store.attach_gate(self.safety,self.gate,self.lock)
        self.assertEqual(self.store.daily_attempt_limit,2)
        self.assertEqual(self.store.approved_models(),[{
            "provider":"openai_responses","model":"gpt-5"}])

    def test_invalid_model_policy_configuration_fails_closed(self):
        for allowlist in [
            [],["auto:gpt-5"],["gemini_generate_content:a/b"],
            ["anthropic_messages:a:b"],["openai_responses:gpt-5"]*2,
            ["openai_responses:gpt-5","openai_responses:gpt-5"],
            [True],
        ]:
            with self.subTest(allowlist=allowlist),self.assertRaisesRegex(
                    OnDemandRemoteAIError,"invalid_remote_model_allowlist"):
                LocalOnDemandRemoteAI(":memory:",runner=self.openai,
                                     model_allowlist=allowlist)
        for cap in [0,-1,6,True,1.25,"2"]:
            with self.subTest(cap=cap),self.assertRaisesRegex(
                    OnDemandRemoteAIError,"invalid_remote_daily_attempt_limit"):
                LocalOnDemandRemoteAI(":memory:",runner=self.openai,
                                     daily_attempt_limit=cap)
        with self.assertRaisesRegex(OnDemandRemoteAIError,"no_enabled_provider"):
            LocalOnDemandRemoteAI(":memory:",runner=self.openai,
                 model_allowlist=["gemini_generate_content:gemini-2.5-flash"])


class OwnerAuthenticatedProviderHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name)
        self.auth = LocalOperatorAuth.ephemeral_for_tests(PASS)
        self.safety = LocalModelSafetyState(path/"safety.sqlite3")
        self.audit = LocalSecurityAudit(path/"audit.sqlite3")
        self.calls = []
        def gemini(prompt, model):
            self.calls.append((prompt, model))
            return {"mode": "REMOTE_GEMINI_READ_ONLY", "answer": "fixture",
                    "usage": {"input_tokens": 2, "output_tokens": 2},
                    "tools_used": [], "external_dispatch_count": 0}
        self.store = LocalOnDemandRemoteAI(path/"remote.sqlite3",
             additional_runners={"gemini_generate_content": gemini})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
            create_handler(operator_auth=self.auth, security_audit=self.audit,
                           model_safety_state=self.safety, on_demand_ai=self.store))
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.audit.close)
        self.addCleanup(self.safety.close)
        self.addCleanup(self.store.close)
        self.addCleanup(self.worker.join, 4)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        code, sess = self.auth.login(PASS)
        self.assertEqual(code, 200)
        self.cookie = "aas_session=" + sess["cookie_token"]
        self.csrf = sess["csrf_token"]

    def request(self, method, endpoint, obj=None, *, auth=True, csrf=True):
        con = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Cookie"] = self.cookie
        if csrf:
            headers["X-AAS-CSRF"] = self.csrf
        con.request(method, endpoint, body=json.dumps(obj).encode() if obj is not None else None,
                    headers=headers)
        response = con.getresponse()
        result = response.status, json.loads(response.read())
        con.close()
        return result

    def test_owner_csrf_password_consent_and_provider_capabilities(self):
        request = {**payload("a"*32, "gemini_generate_content", "gemini-2.5-flash"),
                   "current_password": PASS}
        self.assertEqual(self.request("GET", "/api/read-only-ai-ask-history",
                                      auth=False)[0], 401)
        self.assertEqual(self.request("POST", "/api/read-only-ai-ask", request,
                                      csrf=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/read-only-ai-ask",
                                     {**request, "current_password": "wrong"})[0], 401)
        self.assertEqual(self.request("POST", "/api/read-only-ai-ask",
                                     {**request, "paid_api_consent": "true"})[0], 403)
        self.assertEqual(self.calls, [])
        info = self.request("GET", "/api/local-agent-capabilities")[1]
        self.assertEqual(info["remote_on_demand_providers"], ["gemini_generate_content"])
        self.assertFalse(info["remote_model_allowlist_enforced"])
        self.assertIsNone(info["remote_approved_models"])
        self.assertEqual(info["remote_daily_attempt_limit"],5)
        self.assertFalse(info["cloud_plan_review"])
        status, receipt = self.request("POST", "/api/read-only-ai-ask", request)
        self.assertEqual(status, 200)
        self.assertEqual(receipt["status"], "COMPLETED")
        self.assertEqual(receipt["provider"], "gemini_generate_content")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.request("POST", "/api/read-only-ai-ask", request)[0], 409)
        self.assertEqual(len(self.calls), 1)
        history = self.request("GET", "/api/read-only-ai-ask-history")[1]
        self.assertNotIn("answer", history["receipts"][0])
        self.assertEqual(history["receipts"][0]["provider"], "gemini_generate_content")


if __name__ == "__main__":
    unittest.main()
