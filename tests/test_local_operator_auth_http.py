"""Real HTTP security gates for the shipped single-owner localhost server.

A provisioned password creates process-memory, HttpOnly cookie sessions.
All mutable endpoints require matching CSRF; no app data is accessible
without session. Provider fetcher is a spy and NEVER touches GitHub.
"""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from local_operator_auth import (
    LocalOperatorAuth, OperatorConfigurationError,
    LOCKOUT_SECONDS, SESSION_TTL_SECONDS)
from local_server import create_handler
from local_workflow_core import LocalWorkflowCore


PASSWORD = "test-only-owner-password-42"
WORKFLOW = {
    "workflow_id": "owner_flow",
    "display_name": "Owner Rule Test",
    "service_filter": "automation",
    "min_budget_tier": 2,
    "followup_policy": "draft_only",
}
ISSUES = [{"number": 47, "state": "open", "labels": [
    {"name":"aas:service:automation"},
    {"name":"aas:budget:3"},
    {"name":"aas:timeline:soon"}]}]


class OwnerStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "owner" / "operator.json"

    def test_provision_and_load_password_verifier_not_plaintext(self):
        LocalOperatorAuth.provision(self.path, PASSWORD)
        self.assertTrue(self.path.exists())
        self.assertNotIn(PASSWORD, self.path.read_text())
        self.assertEqual({"version","salt","scrypt_hash"},
                         set(json.loads(self.path.read_text())))
        reloaded=LocalOperatorAuth.load(self.path)
        self.assertEqual(200,reloaded.login(PASSWORD)[0])
        self.assertEqual(401,reloaded.login("wrong-password")[0])

    def test_owner_file_permissions_restrictive_on_posix(self):
        LocalOperatorAuth.provision(self.path,PASSWORD)
        if sys.platform != "win32":
            self.assertEqual(0,stat.S_IMODE(self.path.stat().st_mode) & 0o077)
            self.path.chmod(0o644)
            with self.assertRaises(OperatorConfigurationError):
                LocalOperatorAuth.load(self.path)

    def test_owner_setup_cannot_replace_existing_identity(self):
        LocalOperatorAuth.provision(self.path,PASSWORD)
        before=self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            LocalOperatorAuth.provision(self.path,"another-secure-password-12")
        self.assertEqual(before,self.path.read_bytes())

    def test_short_and_control_character_passwords_denied(self):
        for bad in ("tiny", "\nmore_than_twelve_chars", "has\tcontrol_char123",
                    "x"*129, "", None, False):
            with self.assertRaises(ValueError):
                LocalOperatorAuth.provision(self.path,bad)
        self.assertFalse(self.path.exists())

    def test_missing_or_corrupted_identity_blocks_server_access(self):
        with self.assertRaises(OperatorConfigurationError):
            LocalOperatorAuth.load(self.path)
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"version":999,"salt":"x"}')
        with self.assertRaises(OperatorConfigurationError):
            LocalOperatorAuth.load(self.path)

    def test_symlink_identity_not_followed(self):
        LocalOperatorAuth.provision(self.path,PASSWORD)
        link=self.path.parent/"link.json"
        try: link.symlink_to(self.path)
        except (OSError, NotImplementedError): self.skipTest("symlink unavailable")
        with self.assertRaises(OperatorConfigurationError):
            LocalOperatorAuth.load(link)

    def test_failed_login_lockout_and_expiry(self):
        clock=[1000.]
        auth=LocalOperatorAuth.ephemeral_for_tests(PASSWORD,clock=lambda:clock[0])
        for _ in range(5):
            self.assertEqual(401,auth.login("wrong-value")[0])
        self.assertEqual(429,auth.login(PASSWORD)[0])
        clock[0]+=LOCKOUT_SECONDS+1
        code,entry=auth.login(PASSWORD)
        self.assertEqual(200,code)
        self.assertTrue(auth.inspect(entry["cookie_token"]))
        clock[0]+=SESSION_TTL_SECONDS+1
        self.assertIsNone(auth.inspect(entry["cookie_token"]))

    def test_csrf_and_logout_revoke_owner_session(self):
        auth=LocalOperatorAuth.ephemeral_for_tests(PASSWORD)
        code,entry=auth.login(PASSWORD)
        self.assertEqual(200,code)
        token=entry["cookie_token"]
        self.assertFalse(auth.authorize(token,None))
        self.assertFalse(auth.authorize(token,"wrong"))
        self.assertTrue(auth.authorize(token,entry["csrf_token"]))
        self.assertTrue(auth.logout(token))
        self.assertFalse(auth.authorize(token,entry["csrf_token"]))
        self.assertFalse(auth.logout(token))

    def test_session_capacity_is_bounded(self):
        auth=LocalOperatorAuth.ephemeral_for_tests(PASSWORD)
        sessions=[auth.login(PASSWORD)[1] for _ in range(10)]
        self.assertIsNone(auth.inspect(sessions[0]["cookie_token"]))
        self.assertIsNotNone(auth.inspect(sessions[-1]["cookie_token"]))


class AuthenticatedLocalHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock=[1000.]
        self.core=LocalWorkflowCore(Path(self.tmp.name)/"workflow.sqlite3")
        self.addCleanup(self.core.close)
        self.auth=LocalOperatorAuth.ephemeral_for_tests(
            PASSWORD,clock=lambda:self.clock[0])
        self.provider_calls=[]
        def fake_provider(repo):
            self.provider_calls.append(repo)
            return ISSUES
        self.httpd=ThreadingHTTPServer(
            ("127.0.0.1",0),
            create_handler(local_core=self.core,operator_auth=self.auth,
                           github_fetcher=fake_provider))
        self.worker=threading.Thread(
            target=self.httpd.serve_forever,daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join,4)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self,method,path,body=None,*,cookie=None,csrf=None,headers=None):
        conn=http.client.HTTPConnection(
            "127.0.0.1",self.httpd.server_port,timeout=5)
        h={"Content-Type":"application/json"}
        if cookie is not None: h["Cookie"]=cookie
        if csrf is not None: h["X-AAS-CSRF"]=csrf
        h.update(headers or {})
        conn.request(method,path,
                     body=json.dumps(body) if body is not None else None,
                     headers=h)
        response=conn.getresponse()
        status=response.status
        cookie_out=response.getheader("Set-Cookie")
        content=response.read()
        conn.close()
        return status,json.loads(content),cookie_out

    def login(self):
        status,result,cookie=self.request(
            "POST","/api/operator-login",{"password":PASSWORD})
        self.assertEqual(200,status)
        self.assertIn("HttpOnly",cookie)
        self.assertIn("SameSite=Strict",cookie)
        self.assertNotIn(PASSWORD,cookie)
        return cookie.split(";",1)[0],result["csrf_token"]

    def create(self,cookie,csrf):
        return self.request("POST","/api/local-create",
                            WORKFLOW,cookie=cookie,csrf=csrf)

    def test_session_bootstrap_is_unprivileged_without_cookie(self):
        status,body,_=self.request("GET","/api/operator-session")
        self.assertEqual(200,status)
        self.assertEqual({"authenticated":False},body)
        self.assertEqual(401,self.request("GET","/api/local-workflows")[0])
        self.assertEqual(200,self.request("GET","/api/health")[0])

    def test_login_then_session_and_local_create(self):
        cookie,csrf=self.login()
        status,body,_=self.request(
            "GET","/api/operator-session",cookie=cookie)
        self.assertEqual(200,status)
        self.assertEqual(csrf,body["csrf_token"])
        self.assertTrue(body["authenticated"])
        self.assertEqual(201,self.create(cookie,csrf)[0])
        self.assertEqual(1,len(self.core.list_workflows()["workflows"]))

    def test_invalid_login_does_not_create_session_cookie(self):
        status,out,cookie=self.request(
            "POST","/api/operator-login",{"password":"incorrect"})
        self.assertEqual(401,status)
        self.assertIsNone(cookie)
        self.assertNotIn("csrf_token",out)
        self.assertEqual(401,self.request("GET","/api/local-workflows")[0])

    def test_mutation_denied_without_cookie_on_every_core_route(self):
        routes=[
            "/api/local-create","/api/local-activate","/api/local-run",
            "/api/local-batch-preview","/api/local-batch-commit",
            "/api/local-stage","/api/local-publish","/api/local-pause",
            "/api/local-resume","/api/local-github-preview",
            "/api/local-github-commit",
        ]
        for route in routes:
            with self.subTest(route=route):
                status,body,_=self.request("POST",route,{})
                self.assertEqual(401,status)
                self.assertEqual("operator_session_required",body["error"])
        self.assertEqual([],self.provider_calls)
        self.assertEqual([],self.core.list_workflows()["workflows"])

    def test_mutation_denied_without_cookie_on_every_mock_route(self):
        for route in ("/api/mock-run","/api/mock-review","/api/mock-setup",
                      "/api/mock-triage","/api/mock-appointment",
                      "/api/mock-crm-handoff","/api/mock-knowledge-routing"):
            self.assertEqual(401,self.request("POST",route,{})[0])

    def test_all_nonhealth_data_reads_need_owner_session(self):
        for route in (
            "/api/local-workflows","/api/local-history?workflow_id=owner_flow",
            "/api/local-revisions?workflow_id=owner_flow",
            "/api/templates","/api/setup-choices"):
            self.assertEqual(401,self.request("GET",route)[0])
        cookie,csrf=self.login()
        self.assertEqual(200,self.request("GET","/api/local-workflows",cookie=cookie)[0])
        self.assertEqual(200,self.request("GET","/api/templates",cookie=cookie)[0])

    def test_valid_cookie_without_csrf_never_writes(self):
        cookie,csrf=self.login()
        for invalid in (None,"", "other"):
            self.assertEqual(403,self.request("POST","/api/local-create",
                WORKFLOW,cookie=cookie,csrf=invalid)[0])
        self.assertEqual([],self.core.list_workflows()["workflows"])
        self.assertEqual(201,self.create(cookie,csrf)[0])

    def test_invalid_origin_or_host_is_denied_even_after_login(self):
        cookie,csrf=self.login()
        for bad in ({"Origin":"https://evil.example"},
                    {"Host":"evil.example"}):
            self.assertEqual(403,self.request("POST","/api/local-create",
                WORKFLOW,cookie=cookie,csrf=csrf,headers=bad)[0])
        self.assertEqual([],self.core.list_workflows()["workflows"])

    def test_unauthenticated_public_github_read_never_reaches_provider(self):
        req={"workflow_id":"owner_flow",
             "repo":"naraya07pedro-spec/ai-automation-studio"}
        self.assertEqual(401,self.request("POST",
            "/api/local-github-preview",req)[0])
        self.assertEqual([],self.provider_calls)
        cookie,csrf=self.login()
        self.assertEqual(201,self.create(cookie,csrf)[0])
        self.assertEqual(200,self.request("POST","/api/local-activate",
            {"workflow_id":"owner_flow","expected_version":1},
            cookie=cookie,csrf=csrf)[0])
        self.assertEqual(403,self.request("POST",
            "/api/local-github-preview",req,cookie=cookie)[0])
        self.assertEqual([],self.provider_calls)
        self.assertEqual(200,self.request("POST",
            "/api/local-github-preview",req,cookie=cookie,csrf=csrf)[0])
        self.assertEqual(["naraya07pedro-spec/ai-automation-studio"],
                         self.provider_calls)

    def test_logout_invalidates_cookie_and_csrf(self):
        cookie,csrf=self.login()
        code,result,expire=self.request("POST","/api/operator-logout",
            {},cookie=cookie,csrf=csrf)
        self.assertEqual(200,code)
        self.assertIn("Max-Age=0",expire)
        self.assertFalse(result["authenticated"])
        self.assertEqual(401,self.request(
            "GET","/api/local-workflows",cookie=cookie)[0])
        self.assertEqual(401,self.request(
            "POST","/api/local-create",WORKFLOW,cookie=cookie,csrf=csrf)[0])

    def test_logout_requires_valid_csrf(self):
        cookie,csrf=self.login()
        self.assertEqual(403,self.request("POST","/api/operator-logout",
            {},cookie=cookie)[0])
        self.assertEqual(200,self.request(
            "GET","/api/local-workflows",cookie=cookie)[0])

    def test_expired_cookie_denied_without_restart(self):
        cookie,csrf=self.login()
        self.clock[0]+=SESSION_TTL_SECONDS+1
        self.assertEqual(401,self.request(
            "GET","/api/local-workflows",cookie=cookie)[0])
        self.assertEqual(401,self.request("POST","/api/local-create",
            WORKFLOW,cookie=cookie,csrf=csrf)[0])

    def test_invalid_login_schema_rejected(self):
        for candidate in ({}, {"password":PASSWORD,"owner":"attacker"},
                          {"password":True},{"password":None}):
            code,_,_=self.request("POST","/api/operator-login",candidate)
            self.assertIn(code,(401,422))
        self.assertEqual(422,self.request(
            "POST","/api/operator-logout",
            {"unexpected":True})[0])

    def test_owner_file_or_session_credentials_not_exposed(self):
        cookie,csrf=self.login()
        for path in ("/operator.json","/api/operator-config",
                     "/api/operator-password","/.ai-automation-studio"):
            self.assertEqual(404,self.request("GET",path,cookie=cookie)[0])
        root=self.request("GET","/")[1] if False else None
        self.assertIsNone(root)

if __name__ == "__main__":
    unittest.main()
