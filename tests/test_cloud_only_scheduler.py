"""Cloud-only real private workbench contracts; zero paid API calls in CI."""
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
import http.cookiejar
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "deploy"))
import launch_workbench as launcher
import local_server
from local_model_safety import LocalModelSafetyState
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_workflow_core import LocalWorkflowCore
from local_readonly_agent_scheduler import LocalReadOnlyAgentSchedules, AgentScheduleError
from openai_readonly_responses import RemoteInferenceError
import private_vps_service

KEY = "sk-THIS-IS-A-NETWORKLESS-FIXTURE-123456789"
PASSWORD = "DisposableCloudOnlyOwner!2026"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 7, 2, 0, tzinfo=timezone.utc)
    def __call__(self):
        return self.now
    def advance(self, **delta):
        self.now += timedelta(**delta)


def remote_payload():
    return {
        "prompt": "Describe this fixed topic in one short sentence.",
        "model": "test-model-id",
        "provider": "openai_responses",
        "paid_api_consent": True,
        "cadence": {"cadence": "minutes", "every": 1,
                    "timezone": "Asia/Jakarta"},
        "max_runs": 1, "expires_days": 1,
    }


class CloudOnlyModes(unittest.TestCase):
    def test_cloud_profile_has_no_ollama_tool_or_public_source_flags(self):
        self.assertEqual(launcher.feature_flags("cloud-only"), ())
        self.assertEqual(launcher.feature_flags("offline"), ())
        with self.assertRaisesRegex(ValueError, "history_requires_local_ai"):
            launcher.feature_flags("cloud-only", history=True)
        self.assertIn("--enable-local-agent",
                      launcher.feature_flags("local-ai"))
        self.assertIn("--enable-local-agent",
                      launcher.feature_flags("full"))

    def test_windows_owner_launch_explicit_cloud_only_has_no_local_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner = Path(tmp) / "operator.json"
            owner.write_text("FIxTURE only")
            with patch.object(launcher, "_operator_path", return_value=owner), \
                 patch.object(launcher, "_port_free", return_value=True), \
                 patch.object(launcher, "_serve", return_value=None) as serve:
                rc = launcher.main(["--mode", "cloud-only",
                                    "--enable-scheduled-openai",
                                    "--no-browser"])
            self.assertEqual(rc, 0)
            flags = serve.call_args.args[0]
            self.assertEqual(flags, ("--enable-scheduled-openai",))
            self.assertNotIn("--enable-local-agent", flags)
            self.assertNotIn("--enable-scheduled-local-ai", flags)
            self.assertNotIn("--enable-agent-plans", flags)

    def test_cloud_profile_refuses_implicit_paid_access_or_ollama_flag(self):
        with patch.object(launcher, "_serve") as serve, \
             patch.object(launcher, "_port_free", return_value=True):
            self.assertEqual(launcher.main(["--mode","cloud-only",
                                             "--no-browser"]), 2)
            self.assertEqual(launcher.main(["--mode","cloud-only",
                                             "--enable-scheduled-local-ai",
                                             "--enable-scheduled-openai",
                                             "--no-browser"]), 2)
            serve.assert_not_called()

    def test_local_profile_previous_scheduling_contract_still_requires_local_flag(self):
        with patch.object(launcher, "_serve") as serve, \
             patch.object(launcher, "_port_free", return_value=True):
            self.assertEqual(launcher.main(["--mode","local-ai",
                                             "--enable-scheduled-openai",
                                             "--no-browser"]), 2)
            serve.assert_not_called()

    def test_cloud_only_systemd_unit_loads_paid_key_without_local_ollama(self):
        values = dict(username="studio", checkout="/home/studio/studio",
                      home="/home/studio", python="/usr/bin/python3",
                      mode="cloud-only")
        unit = private_vps_service.render_unit(**values, scheduled_openai=True)
        self.assertIn("LoadCredential=aas-openai-api-key:", unit)
        self.assertIn("--enable-scheduled-openai", unit)
        self.assertNotIn("--enable-local-agent", unit)
        self.assertNotIn("--enable-scheduled-local-ai", unit)
        self.assertIn("ExecStart=/usr/bin/python3 /home/studio/studio/app/local_server.py", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertNotIn(KEY,unit)
        self.assertNotIn("EnvironmentFile=",unit)

    def test_cloud_only_unit_requires_opt_in_and_rejects_ollama_scheduler(self):
        opts = dict(username="studio", checkout="/home/studio/studio",
                    home="/home/studio",python="/usr/bin/python3",
                    mode="cloud-only")
        with self.assertRaisesRegex(ValueError, "cloud_only_requires_scheduled_openai"):
            private_vps_service.render_unit(**opts)
        with self.assertRaisesRegex(ValueError, "scheduled_local_ai_requires"):
            private_vps_service.render_unit(**opts,scheduled_local_ai=True,
                                           scheduled_openai=True)
        opts["mode"]="offline"
        with self.assertRaisesRegex(ValueError,"scheduled_openai_requires_scheduled_ai"):
            private_vps_service.render_unit(**opts,scheduled_openai=True)

    def test_native_server_cli_allows_only_new_remote_switch(self):
        with patch.object(local_server, "start_local_server") as start:
            local_server.main(["--enable-scheduled-openai"])
            start.assert_called_once()
            self.assertTrue(start.call_args.kwargs["enable_scheduled_openai"])
            self.assertFalse(start.call_args.kwargs["enable_local_agent"])
            self.assertFalse(start.call_args.kwargs["enable_scheduled_local_ai"])

    def test_full_server_startup_initializes_durable_cloud_gate_without_ollama(self):
        class NoNetworkServer:
            def __init__(self, address, handler):
                self.server_port = address[1]
            def serve_forever(self):
                return
            def server_close(self):
                return
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            base = home/".ai-automation-studio"
            base.mkdir(mode=0o700)
            LocalOperatorAuth.provision(base/"operator.json", PASSWORD)
            with patch.object(local_server.Path, "home", return_value=home), \
                 patch.object(local_server, "ThreadingHTTPServer", NoNetworkServer), \
                 patch.object(local_server, "read_runtime_secret",
                              return_value=KEY) as secret, \
                 patch.object(local_server, "installed_models",
                              side_effect=AssertionError("Ollama catalog used")), \
                 patch.object(local_server, "probe_model",
                              side_effect=AssertionError("Ollama probe used")), \
                 patch.object(local_server, "run_agent",
                              side_effect=AssertionError("Ollama invoked")):
                local_server.start_local_server(enable_scheduled_openai=True)
            self.assertTrue((base/"ollama-safety.sqlite3").exists(),
                            "Cloud-only MUST have the durable crash/uncertain checkpoint")
            self.assertTrue((base/"readonly-ai-schedules.sqlite3").exists())
            secret.assert_called_once_with("AAS_OPENAI_API_KEY")

    def test_server_without_paid_key_fails_before_http_bind(self):
        class MustNotBind:
            def __init__(self,*a,**k):
                raise AssertionError("HTTP bound without a provider key")
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)
            base=home/".ai-automation-studio"
            base.mkdir(mode=0o700)
            LocalOperatorAuth.provision(base/"operator.json",PASSWORD)
            with patch.object(local_server.Path, "home", return_value=home), \
                 patch.object(local_server, "read_runtime_secret", return_value=""), \
                 patch.object(local_server, "ThreadingHTTPServer", MustNotBind):
                with self.assertRaises(RemoteInferenceError):
                    local_server.start_local_server(enable_scheduled_openai=True)


class CloudOnlyHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name)
        self.clock=Clock()
        LocalOperatorAuth.provision(self.base/"operator.json",PASSWORD)
        self.operator=LocalOperatorAuth.load(self.base/"operator.json")
        self.core=LocalWorkflowCore(self.base/"core.sqlite3")
        self.audit=LocalSecurityAudit(self.base/"audit.sqlite3")
        self.safety=LocalModelSafetyState(self.base/"model-safety.sqlite3")
        self.calls=[]
        def remote(prompt,model):
            self.calls.append((prompt,model))
            return {"answer":"Networkless remote model fixture response",
                    "mode":"REMOTE_OPENAI_READ_ONLY","tools_used":[],
                    "external_dispatch_count":0,
                    "usage":{"input_tokens":13,"output_tokens":6}}
        self.remote=remote
        self.scheduled=LocalReadOnlyAgentSchedules(
            self.base/"task.sqlite3", clock=self.clock,
            remote_runner=self.remote, local_enabled=False)
        self.server=ThreadingHTTPServer(("127.0.0.1",0),
            local_server.create_handler(local_core=self.core,
                operator_auth=self.operator, security_audit=self.audit,
                model_safety_state=self.safety,scheduled_ai=self.scheduled))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url="http://127.0.0.1:"+str(self.server.server_port)
        self.opener=build_opener(ProxyHandler({}),
                    HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf=None

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.scheduled.close()
        self.core.close()
        self.safety.close()
        self.audit.close()
        self.tmp.cleanup()

    def get(self,route):
        with self.opener.open(self.url+route,timeout=5) as result:
            return json.load(result)

    def post(self,route,payload,csrf=True):
        header={"Content-Type":"application/json"}
        if csrf and self.csrf:
            header["X-AAS-CSRF"]=self.csrf
        req=Request(self.url+route,json.dumps(payload).encode(),
                    headers=header,method="POST")
        with self.opener.open(req,timeout=5) as result:
            return json.load(result)

    def login(self):
        self.csrf=self.post("/api/operator-login",{"password":PASSWORD})["csrf_token"]

    def test_live_private_http_cloud_mode_disables_ollama_but_enables_tasks(self):
        with self.assertRaises(HTTPError) as denied:
            self.get("/api/read-only-ai-schedules")
        self.assertEqual(denied.exception.code,401)
        self.login()
        capabilities=self.get("/api/local-agent-capabilities")
        self.assertFalse(capabilities["enabled"])
        self.assertTrue(capabilities["remote_scheduled_ai"])
        with self.assertRaises(HTTPError) as disabled:
            self.get("/api/local-agent-models")
        self.assertEqual(disabled.exception.code,403)
        items=self.get("/api/read-only-ai-schedules")
        self.assertTrue(items["enabled"])
        self.assertFalse(items["local_ollama_enabled"])
        self.assertTrue(items["remote_openai_enabled"])
        self.assertFalse(items["local_model_only"])

    def test_owner_password_csrf_and_absence_of_paid_request_on_schedule_create(self):
        self.login()
        body={**remote_payload(),"current_password":PASSWORD}
        with self.assertRaises(HTTPError) as csrf:
            self.post("/api/read-only-ai-schedule-create",body,csrf=False)
        self.assertEqual(csrf.exception.code,403)
        wrong=dict(body,current_password="wrong")
        with self.assertRaises(HTTPError) as rejected:
            self.post("/api/read-only-ai-schedule-create",wrong)
        self.assertEqual(rejected.exception.code,401)
        for invalid_consent in (None, False, "true", 1):
            no_consent=dict(body)
            if invalid_consent is None:
                no_consent.pop("paid_api_consent")
            else:
                no_consent["paid_api_consent"]=invalid_consent
            with self.subTest(invalid_consent=invalid_consent):
                with self.assertRaises(HTTPError) as blocked:
                    self.post("/api/read-only-ai-schedule-create",no_consent)
                self.assertEqual(blocked.exception.code,403)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.get("/api/read-only-ai-schedules")["tasks"],[])
        task=self.post("/api/read-only-ai-schedule-create",body)
        self.assertEqual(task["provider"],"openai_responses")
        self.assertEqual(self.calls,[],"API calls can only happen on due slot")
        self.clock.advance(minutes=1)
        self.assertEqual(self.scheduled.run_due(),1)
        self.assertEqual(len(self.calls),1)
        history=self.get("/api/read-only-ai-history?task_id="+task["task_id"])
        self.assertEqual(history["runs"][0]["answer"],
                         "Networkless remote model fixture response")
        self.assertEqual(history["runs"][0]["usage_tokens"],
                         {"input_tokens":13,"output_tokens":6})
        self.assertEqual(history["runs"][0]["external_dispatch_count"],0)

    def test_implicit_local_task_and_local_resume_are_denied(self):
        self.login()
        body={**remote_payload(),"current_password":PASSWORD}
        body.pop("provider")
        body.pop("paid_api_consent")
        body["model"]="llama3.2:3b"
        with self.assertRaises(HTTPError) as rejected:
            self.post("/api/read-only-ai-schedule-create",body)
        self.assertEqual(rejected.exception.code,403)
        self.assertEqual(self.get("/api/read-only-ai-schedules")["tasks"],[])
        self.assertEqual(self.calls,[])

    def test_existing_ollama_task_pauses_when_switched_to_cloud_only(self):
        # Existing owner schedules must never silently change provider or run.
        self.scheduled.close()
        previous=LocalReadOnlyAgentSchedules(
            self.base/"task.sqlite3",clock=self.clock)
        old=previous.create({
            "prompt":"Local-only legacy prompt","model":"llama3.2:3b",
            "cadence":{"cadence":"minutes","every":1,
                       "timezone":"Asia/Jakarta"},
            "max_runs":2,"expires_days":1})
        previous.close()
        self.scheduled=LocalReadOnlyAgentSchedules(
            self.base/"task.sqlite3",clock=self.clock,
            remote_runner=self.remote,local_enabled=False)
        self.scheduled.attach_gate(
            self.safety,threading.BoundedSemaphore(1),threading.Lock())
        local=self.scheduled.list()["tasks"][0]
        self.assertEqual(local["task_id"],old["task_id"])
        self.assertEqual(local["provider"],"local_ollama")
        self.assertEqual(local["status"],"PAUSED_ERROR")
        self.assertEqual(local["control_epoch"],1)
        with self.assertRaisesRegex(AgentScheduleError,
                                     "local_model_not_enabled"):
            self.scheduled.change(
                {"task_id":old["task_id"],"expected_epoch":1},pause=False)
        self.clock.advance(minutes=1)
        self.assertEqual(self.scheduled.run_due(),0)
        self.assertEqual(self.calls,[])

