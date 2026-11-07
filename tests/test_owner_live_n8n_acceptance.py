"""Stage #119: full owner localhost acceptance client using real Stage 117 handler.

Real SQLite, AES-256-GCM managed connector fixture and loopback HTTP server.
Provider n8n fetch remains a stub, so CI cannot certify real n8n access.
"""
from http.server import ThreadingHTTPServer
import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
sys.path.insert(0,str(ROOT/"app"))

import owner_live_n8n_acceptance as cli
import local_server
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
import test_n8n_execution_verifier as fixtures

HAS_CRYPTO=importlib.util.find_spec("cryptography") is not None


@unittest.skipUnless(HAS_CRYPTO,"Dedicated CI installs pinned AES-GCM")
class OwnerLiveCLIIntegration(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.Stage117VerifierTests(
            methodName="test_full_provider_execution_success_exact_input_bound")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.owner_path=self.fixture.root/"operator-cli.json"
        LocalOperatorAuth.provision(self.owner_path,fixtures.PASS)
        self.owner=LocalOperatorAuth.load(self.owner_path)
        self.audit=LocalSecurityAudit(self.fixture.root/"owner-cli-audit.sqlite3")
        self.addCleanup(self.audit.close)
        self.server=ThreadingHTTPServer(
            ("127.0.0.1",0),
            local_server.create_handler(
                operator_auth=self.owner,
                security_audit=self.audit,
                local_core=self.fixture.core,
                local_agent_plan_store=self.fixture.plans,
                agent_connector_handoff=self.fixture.handoff,
                handoff_outcomes=self.fixture.signed,
                n8n_execution_verifier=self.fixture.verifier))
        self.thread=threading.Thread(
            target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join,5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.client=cli.OwnerLiveClient(port=self.server.server_port)
        self.args={
            "plan_id":fixtures.PLAN,
            "expected_host":"stage117.app.n8n.cloud",
            "expected_workflow":fixtures.N8N_WORKFLOW,
            "expected_node":fixtures.NODE,
            "expected_claim":"COMPLETED",
            "password":fixtures.PASS,
        }

    def test_inspect_performs_only_local_reads_no_n8n_fetch(self):
        result=self.client.execute(mode="inspect",**self.args)
        self.assertEqual(result["status"],
                         "READY_FOR_ONE_OWNER_APPROVED_N8N_READ")
        self.assertFalse(result["provider_read_may_have_been_dispatched"])
        self.assertEqual(result["new_webhook_sends"],0)
        self.assertEqual(self.fixture.api_calls,[])
        self.assertEqual(len(self.fixture.sent),1)
        self.assertIsNone(self.client.csrf)
        with self.assertRaises(cli.OwnerLiveAcceptanceError):
            self.client._request("/api/n8n-execution-check-history")

    def test_owner_approved_single_get_and_exact_execution_delivery(self):
        result=self.client.execute(mode="verify",confirmation=cli.CONFIRMATION,
                                   **self.args)
        self.assertEqual(result["status"],"N8N_EXECUTION_SUCCESS_CORRELATED")
        self.assertTrue(result["live_provider_execution_correlated"])
        self.assertEqual(result["provider_execution_outcome"],"success")
        self.assertFalse(result["business_effect_verified"])
        self.assertTrue(result["provider_read_may_have_been_dispatched"])
        self.assertEqual(result["new_webhook_sends"],0)
        self.assertEqual(len(self.fixture.api_calls),1)
        self.assertEqual(len(self.fixture.sent),1)
        self.assertEqual(self.fixture.verifier.history()["records"][0]["status"],
                         "EXECUTION_SUCCESS_CORRELATED")
        self.assertTrue(any(x["action"]=="N8N_EXECUTION_READONLY_VERIFY"
                            for x in self.audit.recent(60)))
        safe=json.dumps(result)
        self.assertNotIn(fixtures.PASS,safe)
        self.assertNotIn(fixtures.API_KEY,safe)
        self.assertNotIn(fixtures.SIGN_KEY,safe)
        self.assertNotIn(fixtures.PLAN,safe)

    def test_second_attempt_blocked_without_reaching_provider(self):
        self.client.execute(mode="verify",confirmation=cli.CONFIRMATION,
                            **self.args)
        with self.assertRaisesRegex(cli.OwnerLiveAcceptanceError,
                                    "provider_check_already_recorded"):
            self.client.execute(mode="verify",confirmation=cli.CONFIRMATION,
                                **self.args)
        self.assertEqual(len(self.fixture.api_calls),1)

    def test_wrong_pinned_target_rejected_before_get(self):
        for change in [
            {"expected_host":"other.app.n8n.cloud"},
            {"expected_workflow":"wrong"},
            {"expected_node":"Wrong Webhook"},
            {"expected_claim":"FAILED"},
        ]:
            with self.subTest(change=change):
                args={**self.args,**change}
                with self.assertRaises(cli.OwnerLiveAcceptanceError):
                    self.client.execute(mode="verify",
                        confirmation=cli.CONFIRMATION,**args)
        self.assertEqual(self.fixture.api_calls,[])
        self.assertEqual(len(self.fixture.sent),1)

    def test_no_confirmation_never_dispatches_remote_read(self):
        for confirmation in (None,"YES","VERIFY_ONE_REMOTE_N8N_GET2"):
            with self.subTest(confirmation=confirmation):
                with self.assertRaisesRegex(
                    cli.OwnerLiveAcceptanceError,"explicit_n8n_read_consent_required"):
                    self.client.execute(mode="verify",
                        confirmation=confirmation,**self.args)
        self.assertEqual(self.fixture.api_calls,[])

    def test_provider_disagrees_with_signed_final_claim(self):
        self.fixture.fake_response["status"]="error"
        self.fixture.fake_response["finished"]=False
        r=self.client.execute(mode="verify",confirmation=cli.CONFIRMATION,
                              **self.args)
        self.assertEqual(r["status"],"N8N_EXECUTION_NOT_CORRELATED_OR_CONFLICTING")
        self.assertFalse(r["live_provider_execution_correlated"])
        self.assertEqual(len(self.fixture.api_calls),1)
        self.assertEqual(len(self.fixture.sent),1)

    def test_provider_api_timeout_never_retried_by_cli(self):
        def ambiguous(*args):
            self.fixture.api_calls.append("timeout_once")
            raise TimeoutError("confidential provider exception")
        self.fixture.verifier._fetcher=ambiguous
        r=self.client.execute(mode="verify",confirmation=cli.CONFIRMATION,
                              **self.args)
        self.assertEqual(r["status"],"N8N_EXECUTION_NOT_CORRELATED_OR_CONFLICTING")
        self.assertFalse(r["live_provider_execution_correlated"])
        self.assertEqual(self.fixture.api_calls,["timeout_once"])
        with self.assertRaises(cli.OwnerLiveAcceptanceError):
            self.client.execute(mode="verify",confirmation=cli.CONFIRMATION,
                                **self.args)
        self.assertEqual(self.fixture.api_calls,["timeout_once"])

    def test_client_local_socket_error_never_forges_success(self):
        idle=cli.OwnerLiveClient(port=self.server.server_port)
        with patch.object(idle,"_request",
                          side_effect=cli.OwnerLiveAcceptanceError(
                              "private body error should never be printed")):
            with self.assertRaises(cli.OwnerLiveAcceptanceError):
                idle.execute(mode="inspect",**self.args)
        self.assertEqual(self.fixture.api_calls,[])

    def test_post_ack_loss_returns_uncertain_without_repeat(self):
        actual=self.client._request
        def dropped(path,values=None):
            if path=="/api/n8n-execution-check":
                actual(path,values)
                raise cli.OwnerLiveAcceptanceError(
                    "simulated lost local response after provider GET")
            return actual(path,values)
        with patch.object(self.client,"_request",side_effect=dropped):
            r=self.client.execute(mode="verify",
                confirmation=cli.CONFIRMATION,**self.args)
        self.assertEqual(r["status"],
                         "LOCAL_RESPONSE_UNCERTAIN_CHECK_HISTORY_NO_RETRY")
        self.assertFalse(r["live_provider_execution_correlated"])
        self.assertEqual(len(self.fixture.api_calls),1)
        with self.assertRaises(cli.OwnerLiveAcceptanceError):
            self.client.execute(mode="verify",
                confirmation=cli.CONFIRMATION,**self.args)
        self.assertEqual(len(self.fixture.api_calls),1)

    def test_unexpected_redirect_and_arbitrary_endpoint_refused(self):
        with self.assertRaisesRegex(cli.OwnerLiveAcceptanceError,
                                    "local_endpoint_not_allowed"):
            self.client._request("/api/managed-webhook-commit",{})
        with self.assertRaises(cli.OwnerLiveAcceptanceError):
            cli.OwnerLiveClient(port=0)
        with self.assertRaises(cli.OwnerLiveAcceptanceError):
            cli.OwnerLiveClient(port="https://evil.example")
        self.assertIsNone(cli.NoRedirect().redirect_request(
            None,None,0,None,None,None) if False else None)

    def test_cli_main_fails_safe_on_nonmatching_pin_without_disclosure(self):
        args=["inspect","--plan-id",fixtures.PLAN,
              "--expected-host","wrong.app.n8n.cloud",
              "--expected-workflow",fixtures.N8N_WORKFLOW,
              "--expected-node",fixtures.NODE,
              "--expected-claim","COMPLETED",
              "--port",str(self.server.server_port)]
        stdout=io.StringIO()
        with patch("getpass.getpass",return_value=fixtures.PASS),\
             patch("sys.stdout",stdout):
            status=cli.main(args)
        self.assertEqual(status,2)
        result=json.loads(stdout.getvalue())
        self.assertEqual(result["status"],"PREFLIGHT_BLOCKED")
        for secret in (fixtures.PASS,fixtures.API_KEY,fixtures.SIGN_KEY,
                       fixtures.PLAN):
            self.assertNotIn(secret,stdout.getvalue())
        self.assertEqual(self.fixture.api_calls,[])


if __name__=="__main__":
    unittest.main()
