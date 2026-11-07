"""No paid API network in CI: all transports are injected or refused."""
from datetime import datetime,timedelta,timezone
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
sys.path.insert(0,str(ROOT/"deploy"))
from openai_readonly_responses import (
    run_openai_readonly, create_openai_runner, validate_remote_api_key,
    valid_remote_model_name, RemoteInferenceError, ENDPOINT, MAX_OUTPUT_TOKENS)
from local_readonly_agent_scheduler import LocalReadOnlyAgentSchedules, AgentScheduleError
from local_model_safety import LocalModelSafetyState
from local_ollama_agent import AgentError
from local_service_credentials import CREDENTIAL_NAMES
import private_vps_service as vps
import provision_private_vps_credential as provisioning

KEY="sk-fixture-DO-NOT-USE-real-key-123456789"


def response(answer="Fixture response", input_tokens=17, output_tokens=8):
    return {
        "object":"response","status":"completed",
        "output":[{"type":"message","role":"assistant","status":"completed",
                   "content":[{"type":"output_text","text":answer}]}],
        "usage":{"input_tokens":input_tokens,"output_tokens":output_tokens},
    }


class FakeHTTP:
    def __init__(self,payload,status=200):
        self.payload=json.dumps(payload).encode()
        self.status=status
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def read(self,n):
        return self.payload[:n]


class FakeOpener:
    def __init__(self,payload=None,exc=None):
        self.payload=payload if payload is not None else response()
        self.exc=exc
        self.calls=[]
    def open(self,request,timeout):
        self.calls.append((request,timeout))
        if self.exc:
            raise self.exc
        return FakeHTTP(self.payload)


class ProviderSafetyTests(unittest.TestCase):
    def test_only_fixed_https_responses_endpoint_and_tool_free_request(self):
        mock=FakeOpener()
        result=run_openai_readonly("Short instruction","gpt-6-luna",
                                   api_key=KEY,opener=mock)
        self.assertEqual(result["mode"],"REMOTE_OPENAI_READ_ONLY")
        self.assertEqual(result["answer"],"Fixture response")
        self.assertEqual(result["usage"],{"input_tokens":17,"output_tokens":8})
        self.assertEqual(result["tools_used"],[])
        self.assertEqual(result["external_dispatch_count"],0)
        self.assertEqual(len(mock.calls),1)
        req,timeout=mock.calls[0]
        self.assertEqual(req.full_url,ENDPOINT)
        self.assertEqual(req.get_method(),"POST")
        self.assertLessEqual(timeout,35)
        self.assertEqual(req.get_header("Authorization"),"Bearer "+KEY)
        body=json.loads(req.data)
        self.assertEqual(body["tools"],[])
        self.assertEqual(body["tool_choice"],"none")
        self.assertFalse(body["store"])
        self.assertFalse(body["background"])
        self.assertEqual(body["truncation"],"disabled")
        self.assertEqual(body["max_output_tokens"],MAX_OUTPUT_TOKENS)
        self.assertEqual(body["input"],[{"role":"user","content":"Short instruction"}])
        self.assertNotIn(KEY,json.dumps(result))

    def test_no_custom_url_or_arbitrary_provider_model_identifier(self):
        for model in ("", "http://evil.example", "../../hack",
                      "gpt 6", "model\nX", "foo/bar"):
            self.assertFalse(valid_remote_model_name(model))
            with self.assertRaisesRegex(RemoteInferenceError,"remote_invalid_model"):
                run_openai_readonly("text",model,api_key=KEY,opener=FakeOpener())
        for key in ("","token", "secret\nanother", "a"*513):
            with self.assertRaisesRegex(RemoteInferenceError,"remote_api_key_invalid"):
                validate_remote_api_key(key)

    def test_reject_tool_call_and_incomplete_provider_receipts(self):
        candidates=[
            {"object":"response","status":"incomplete","output":[],"usage":{}},
            {**response(),"output":[{"type":"function_call","name":"send_email"}]},
            {**response(),"output":[{"type":"message","role":"assistant",
                                     "content":[{"type":"tool_use","name":"bash"}]}]},
            {**response(),"output":[]},
            {**response(),"usage":{"input_tokens":17,"output_tokens":MAX_OUTPUT_TOKENS+1}},
            {**response(),"usage":None},
            response(answer=""),
            response(answer="x"*7000),
        ]
        for item in candidates:
            with self.subTest(result=item.get("status",item.get("output"))):
                with self.assertRaisesRegex(RemoteInferenceError,
                                             "remote_response_uncertain"):
                    run_openai_readonly("simple","gpt-6-luna",api_key=KEY,
                                        opener=FakeOpener(item))

    def test_http_auth_quota_and_rate_rejections_are_definite(self):
        for code in (400,401,402,403,404,422,429):
            with self.subTest(code=code):
                exc=HTTPError(ENDPOINT,code,"private message",None,io.BytesIO(b"TOP-SECRET"))
                with self.assertRaisesRegex(RemoteInferenceError,
                                             "remote_provider_rejected") as caught:
                    run_openai_readonly("simple","gpt-6-luna",api_key=KEY,
                                        opener=FakeOpener(exc=exc))
                self.assertNotIn("SECRET",str(caught.exception))

    def test_ambiguous_network_and_server_errors_never_auto_retry(self):
        for exc in (
            HTTPError(ENDPOINT,500,"provider note",None,None),
            URLError("connection reset"),
            TimeoutError("read timeout"),
        ):
            fake=FakeOpener(exc=exc)
            with self.assertRaisesRegex(RemoteInferenceError,
                                         "remote_response_uncertain"):
                run_openai_readonly("simple","gpt-6-luna",api_key=KEY,opener=fake)
            self.assertEqual(len(fake.calls),1)

    def test_no_injected_proxy_or_untrusted_redirect(self):
        from openai_readonly_responses import safe_remote_opener, NoRedirect
        from urllib.request import ProxyHandler
        opener=safe_remote_opener()
        proxies=[h for h in opener.handlers if isinstance(h,ProxyHandler)]
        # urllib omits an explicitly empty ProxyHandler from handlers;
        # no proxy handler means no environment-inherited proxy route.
        self.assertEqual(proxies,[])
        redirects=[h for h in opener.handlers if isinstance(h,NoRedirect)]
        self.assertEqual(len(redirects),1)
        self.assertIsNone(redirects[0].redirect_request(None,None,302,"",None,"https://evil.example"))

    def test_fixed_credentials_allowlist_and_vps_unit_requires_opt_in(self):
        self.assertEqual(CREDENTIAL_NAMES["AAS_OPENAI_API_KEY"],"aas-openai-api-key")
        self.assertEqual(provisioning.NAMES["openai-api-key"],"aas-openai-api-key")
        provisioning._validate("openai-api-key",KEY)
        with self.assertRaises(provisioning.ProvisionError):
            provisioning._validate("openai-api-key","fake")
        base={"username":"ubuntu","checkout":"/home/ubuntu/ai-automation-studio",
              "home":"/home/ubuntu","python":"/usr/bin/python3","mode":"local-ai"}
        unit=vps.render_unit(**base,scheduled_local_ai=True,
                             scheduled_openai=True)
        self.assertIn("LoadCredential=aas-openai-api-key:",unit)
        self.assertIn("--enable-scheduled-openai",unit)
        self.assertNotIn(KEY,unit)
        self.assertNotIn("AAS_OPENAI_API_KEY=",unit)
        self.assertNotIn("EnvironmentFile=",unit)
        self.assertNotIn("LoadCredential=aas-openai-api-key:",vps.render_unit(**base))
        with self.assertRaisesRegex(ValueError,"requires_scheduled_ai"):
            vps.render_unit(**base,scheduled_openai=True)

    def test_provider_key_is_bound_to_runner_not_returned_as_output(self):
        with patch("openai_readonly_responses.run_openai_readonly",
                   return_value={"answer":"ok"}) as call:
            run=create_openai_runner(KEY)
            self.assertEqual(run("hello","gpt-6-luna"),{"answer":"ok"})
            self.assertEqual(call.call_args.kwargs["api_key"],KEY)
            self.assertNotIn(KEY,repr(run("hello","gpt-6-luna")))


class Clock:
    def __init__(self):
        self.now=datetime(2026,11,7,9,0,tzinfo=timezone.utc)
    def __call__(self):
        return self.now
    def advance(self,**v):
        self.now+=timedelta(**v)


class ScheduledRemoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"bounded-ai.sqlite3"
        self.clock=Clock()
        self.safety=LocalModelSafetyState()
        self.gate=threading.BoundedSemaphore(1)
        self.lock=threading.Lock()
        self.requests=[]
        def remote(prompt,model):
            self.requests.append((prompt,model))
            return {"answer":"Paid response mock", "mode":"REMOTE_OPENAI_READ_ONLY",
                    "tools_used":[],"external_dispatch_count":0,
                    "usage":{"input_tokens":33,"output_tokens":8}}
        self.remote=remote
        self.scheduled=LocalReadOnlyAgentSchedules(
            self.path,remote_runner=self.remote,clock=self.clock,
            model_safety=self.safety,agent_gate=self.gate,agent_state_lock=self.lock)

    def tearDown(self):
        self.scheduled.close()
        self.safety.close()
        self.tmp.cleanup()

    def create(self,**updates):
        data={"prompt":"Draft one brief idea without accessing external tools.",
              "model":"gpt-6-luna","provider":"openai_responses",
              "paid_api_consent":True,
              "cadence":{"cadence":"minutes","timezone":"Asia/Jakarta","every":1},
              "max_runs":2,"expires_days":3}
        data.update(updates)
        return self.scheduled.create(data)

    def test_owner_authorizes_finite_remote_schedule_and_usage_is_persisted(self):
        task=self.create()
        self.assertEqual(task["provider"],"openai_responses")
        self.assertEqual(task["remote_api_request_limit"],2)
        self.assertEqual(self.scheduled.run_due(),0)
        self.clock.advance(minutes=1)
        self.assertEqual(self.scheduled.run_due(),1)
        self.assertEqual(len(self.requests),1)
        hist=self.scheduled.history(task["task_id"])["runs"]
        self.assertEqual(hist[0]["status"],"COMPLETED")
        self.assertEqual(hist[0]["usage_tokens"],{"input_tokens":33,"output_tokens":8})
        self.assertEqual(hist[0]["tools_used"],[])
        self.assertEqual(hist[0]["external_dispatch_count"],0)
        self.assertEqual(self.safety.phase(),"IDLE")
        self.assertEqual(self.scheduled.run_due(),0)
        self.clock.advance(minutes=1)
        self.scheduled.run_due()
        self.clock.advance(minutes=1)
        self.assertEqual(self.scheduled.run_due(),0)
        self.assertEqual(len(self.requests),2)
        self.assertEqual(self.scheduled.list()["tasks"][0]["status"],"EXHAUSTED")

    def test_remote_default_disabled_and_invalid_provider_rejected(self):
        self.scheduled.close()
        self.scheduled=LocalReadOnlyAgentSchedules(
            self.path,clock=self.clock,model_safety=self.safety,
            agent_gate=self.gate,agent_state_lock=self.lock)
        with self.assertRaisesRegex(AgentScheduleError,"remote_model_not_enabled"):
            self.create()
        with self.assertRaisesRegex(AgentScheduleError,"invalid_ai_task_provider"):
            self.create(provider="evil")
        self.assertEqual(self.scheduled.list()["tasks"],[])

    def test_true_billing_consent_required_even_for_valid_remote_model(self):
        for invalid in (None, False, "true", 1, 0):
            with self.subTest(invalid=invalid):
                request={
                    "prompt":"Short text only", "model":"gpt-6-luna",
                    "provider":"openai_responses",
                    "cadence":{"cadence":"minutes","every":1,
                               "timezone":"Asia/Jakarta"},
                    "max_runs":1, "expires_days":1,
                }
                if invalid is not None:
                    request["paid_api_consent"]=invalid
                with self.assertRaisesRegex(AgentScheduleError,
                                            "paid_api_consent_required"):
                    self.scheduled.create(request)
        self.assertEqual(self.scheduled.list()["tasks"],[])
        self.assertEqual(self.requests,[])

    def test_paid_task_cap_and_expiry_bounded(self):
        for args in ({"max_runs":4},{"max_runs":30},{"expires_days":8},
                     {"expires_days":90},{"model":"../../test"}):
            with self.subTest(args=args):
                with self.assertRaises(AgentScheduleError):
                    self.create(**args)
        self.assertFalse(self.requests)

    def test_ambiguous_remote_request_pauses_without_retry_and_quarantines(self):
        self.scheduled.remote_runner=lambda p,m: (_ for _ in ()).throw(
            RemoteInferenceError("remote_response_uncertain"))
        task=self.create()
        self.clock.advance(minutes=1)
        self.assertEqual(self.scheduled.run_due(),1)
        self.assertEqual(self.scheduled.list()["tasks"][0]["status"],"PAUSED_UNCERTAIN")
        self.assertEqual(self.scheduled.history(task["task_id"])["runs"][0]["status"],
                         "PAUSED_UNCERTAIN")
        self.assertEqual(self.safety.phase(),"UNCERTAIN")
        self.clock.advance(minutes=1)
        self.assertEqual(self.scheduled.run_due(),0)

    def test_definite_rejection_pauses_without_network_loop(self):
        self.scheduled.remote_runner=lambda p,m: (_ for _ in ()).throw(
            RemoteInferenceError("remote_provider_rejected"))
        self.create()
        self.clock.advance(minutes=1)
        self.assertEqual(self.scheduled.run_due(),1)
        self.assertEqual(self.scheduled.list()["tasks"][0]["status"],"PAUSED_ERROR")
        self.assertEqual(self.safety.phase(),"IDLE")
        self.assertEqual(self.scheduled.run_due(),0)

    def test_restart_without_remote_feature_pauses_active_paid_schedule(self):
        task=self.create()
        self.scheduled.close()
        self.scheduled=LocalReadOnlyAgentSchedules(
            self.path,clock=self.clock,model_safety=self.safety,
            agent_gate=self.gate,agent_state_lock=self.lock)
        state=self.scheduled.list()["tasks"][0]
        self.assertEqual(state["status"],"PAUSED_ERROR")
        self.assertEqual(state["provider"],"openai_responses")
        self.clock.advance(minutes=1)
        self.assertEqual(self.scheduled.run_due(),0)
        with self.assertRaisesRegex(AgentScheduleError,"remote_model_not_enabled"):
            self.scheduled.change({
                "task_id":task["task_id"],"expected_epoch":1},pause=False)

    def test_local_legacy_inference_remains_unchanged(self):
        local=self.scheduled.create({
            "prompt":"Local Ollama task","model":"fixture:3b",
            "cadence":{"cadence":"minutes","timezone":"Asia/Jakarta","every":1},
            "max_runs":1,"expires_days":1})
        self.assertEqual(local["provider"],"local_ollama")
        self.assertEqual(local["remote_api_request_limit"],0)

    def test_restart_reopens_remote_token_usage(self):
        task=self.create()
        self.clock.advance(minutes=1)
        self.scheduled.run_due()
        self.scheduled.close()
        self.scheduled=LocalReadOnlyAgentSchedules(
            self.path,clock=self.clock,remote_runner=self.remote,
            model_safety=self.safety,agent_gate=self.gate,agent_state_lock=self.lock)
        saved=self.scheduled.history(task["task_id"])["runs"][0]
        self.assertEqual(saved["usage_tokens"],{"input_tokens":33,"output_tokens":8})
        self.assertEqual(saved["answer"],"Paid response mock")

    def test_legacy_sqlite_table_migrates_without_new_paid_provider(self):
        self.scheduled.close()
        self.path.unlink()
        db=sqlite3.connect(self.path)
        with db:
            db.execute("""CREATE TABLE ai_schedules(task_id TEXT PRIMARY KEY,prompt TEXT,
                model TEXT,config_json TEXT,status TEXT,next_due_utc TEXT,
                expires_utc TEXT,max_runs INTEGER,attempted_runs INTEGER,
                control_epoch INTEGER,created_utc TEXT,last_run_utc TEXT)""")
            db.execute("""CREATE TABLE ai_schedule_runs(task_id TEXT,slot_utc TEXT,
                status TEXT,answer TEXT,tools_json TEXT,error TEXT,
                claimed_utc TEXT,finished_utc TEXT,PRIMARY KEY(task_id,slot_utc))""")
            db.execute("""INSERT INTO ai_schedules VALUES(
                'aaaaaaaaaaaaaaaaaaaaaaaa','old prompt','fixture:1',
                '{"cadence":"minutes","timezone":"UTC","every":1}',
                'PAUSED','2026-11-07T10:00:00+00:00',
                '2026-11-08T10:00:00+00:00',3,0,0,
                '2026-11-07T09:00:00+00:00',NULL)""")
        db.close()
        self.path.chmod(0o600)
        self.scheduled=LocalReadOnlyAgentSchedules(
            self.path,clock=self.clock,model_safety=self.safety,
            agent_gate=self.gate,agent_state_lock=self.lock)
        state=self.scheduled.list()["tasks"][0]
        self.assertEqual(state["provider"],"local_ollama")
        self.assertIsNone(self.scheduled.history(state["task_id"])["runs"] or None)

