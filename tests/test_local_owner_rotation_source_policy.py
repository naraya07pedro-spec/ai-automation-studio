"""Fail-closed single-owner rotation and per-public-repo policy contract.

All provider responses are test fixtures; only the separate live CI workflow
contacts GitHub. HTTP tests run real loopback and SQLite with authenticated
operator sessions plus explicit reauthentication for grants and rotation.
"""
import http.client
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_operator_auth import LocalOperatorAuth, OperatorConfigurationError
from local_source_policy import (
    LocalPublicSourcePolicy, SourcePolicyError, MAX_ALLOWED_REPOS)
from local_server import create_handler
from local_workflow_core import LocalWorkflowCore

PASSWORD = "safely-provisioned-old-password-44"
NEW = "safely-provisioned-new-password-44"
REPO = "naraya07pedro-spec/ai-automation-studio"
FLOW = {"workflow_id": "rotating_flow", "display_name": "Source Policy Rule",
        "service_filter": "any", "min_budget_tier": 1,
        "followup_policy": "review_required"}
ISSUES = [{"number": 47, "state": "open", "labels": [
    {"name": "aas:service:automation"}, {"name": "aas:budget:3"},
    {"name": "aas:timeline:soon"}]}]


class SourcePolicyPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "public-github-sources.json"

    def test_source_policy_default_denies_everything(self):
        policy = LocalPublicSourcePolicy(self.path)
        self.assertEqual([], policy.snapshot()["allowed"])
        self.assertFalse(policy.is_allowed(REPO))
        self.assertEqual(0, policy.version())
        self.assertFalse(self.path.exists())

    def test_grant_persists_and_normalizes_source(self):
        policy = LocalPublicSourcePolicy(self.path)
        code, result = policy.grant("Naraya07pedro-Spec/AI-Automation-Studio")
        self.assertEqual(200, code)
        self.assertEqual([REPO], result["allowed"])
        self.assertTrue(policy.is_allowed(REPO))
        loaded = LocalPublicSourcePolicy(self.path)
        self.assertTrue(loaded.is_allowed(REPO))
        self.assertEqual(policy.version(), loaded.version())
        self.assertFalse(loaded.is_allowed("someone/other-repository"))

    def test_revoke_uses_epoch_and_persists(self):
        policy = LocalPublicSourcePolicy(self.path)
        policy.grant(REPO)
        old_epoch = policy.version()
        code, data = policy.revoke(REPO)
        self.assertEqual(200, code)
        self.assertEqual("PUBLIC_SOURCE_REVOKED", data["status"])
        self.assertFalse(policy.is_allowed(REPO, old_epoch))
        self.assertFalse(LocalPublicSourcePolicy(self.path).is_allowed(REPO))
        policy.grant(REPO)
        self.assertTrue(policy.is_allowed(REPO))
        self.assertFalse(policy.is_allowed(REPO, old_epoch))

    def test_invalid_repo_urls_and_path_traversal_rejected(self):
        policy = LocalPublicSourcePolicy(self.path)
        for bad in ("https://github.com/a/b","a/../../b","a/.","a/..",
                    "a/x?secret=1","user@host/repo","a/%2f","",123,None):
            self.assertEqual(422,policy.grant(bad)[0], bad)
        self.assertEqual([],policy.snapshot()["allowed"])

    def test_regrant_no_revision_change_and_missing_revoke_no_change(self):
        policy = LocalPublicSourcePolicy(self.path)
        policy.grant(REPO)
        epoch = policy.version()
        self.assertEqual(200,policy.grant(REPO)[0])
        self.assertEqual(epoch,policy.version())
        self.assertEqual(404,policy.revoke("user/other")[0])
        self.assertEqual(epoch,policy.version())

    def test_allowlist_limit_16_and_no_silent_eviction(self):
        policy = LocalPublicSourcePolicy(self.path)
        for i in range(MAX_ALLOWED_REPOS):
            self.assertEqual(200,policy.grant(f"owner/repo-{i}")[0])
        self.assertEqual(409,policy.grant(REPO)[0])
        self.assertEqual(MAX_ALLOWED_REPOS,len(policy.snapshot()["allowed"]))

    def test_source_storage_failure_preserves_grants_and_epoch(self):
        policy = LocalPublicSourcePolicy(self.path)
        policy.grant(REPO)
        snapshot = policy.snapshot()
        with patch("local_source_policy.secure_replace_json",
                   side_effect=OSError("disk_full")):
            code, data = policy.revoke(REPO)
        self.assertEqual(503,code)
        self.assertEqual("source_policy_storage_failed",data["error"])
        self.assertEqual(snapshot,policy.snapshot())
        self.assertTrue(LocalPublicSourcePolicy(self.path).is_allowed(REPO))

    def test_corrupt_or_symlinked_file_fails_closed(self):
        self.path.write_text('{"version":1,"allowed":["a/b","a/b"],"epoch":1}')
        with self.assertRaises(SourcePolicyError):
            LocalPublicSourcePolicy(self.path)
        self.path.write_text('{"unexpected":true}')
        with self.assertRaises(SourcePolicyError):
            LocalPublicSourcePolicy(self.path)
        self.path.unlink()
        another = Path(self.tmp.name) / "owner.json"
        another.write_text("{}")
        try:
            self.path.symlink_to(another)
        except (OSError, NotImplementedError):
            self.skipTest("symlink unsupported")
        with self.assertRaises(SourcePolicyError):
            LocalPublicSourcePolicy(self.path)

    def test_source_file_permissions_restrictive_on_posix(self):
        policy = LocalPublicSourcePolicy(self.path)
        policy.grant(REPO)
        if os.name == "posix":
            self.assertEqual(0,stat.S_IMODE(self.path.stat().st_mode) & 0o077)
            self.path.chmod(0o644)
            with self.assertRaises(SourcePolicyError):
                LocalPublicSourcePolicy(self.path)


class OwnerRotationPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"operator.json"
        LocalOperatorAuth.provision(self.path,PASSWORD)
        self.auth=LocalOperatorAuth.load(self.path)

    def test_rotate_requires_verified_old_password(self):
        before=self.path.read_bytes()
        code,data=self.auth.rotate_password("wrong",NEW)
        self.assertEqual(401,code)
        self.assertEqual(before,self.path.read_bytes())
        self.assertEqual(200,self.auth.login(PASSWORD)[0])

    def test_rotation_rejects_invalid_or_reused_new_password(self):
        for value in ("short","bad\nnewpw-1234567",PASSWORD,True,None):
            code,_=self.auth.rotate_password(PASSWORD,value)
            self.assertEqual(422,code,value)
        self.assertEqual(200,self.auth.login(PASSWORD)[0])

    def test_rotation_is_atomic_and_persisted_after_reload(self):
        code,session=self.auth.login(PASSWORD)
        self.assertEqual(200,code)
        status,data=self.auth.rotate_password(PASSWORD,NEW)
        self.assertEqual(200,status)
        self.assertEqual("OWNER_PASSWORD_ROTATED_SESSIONS_REVOKED",data["status"])
        self.assertIsNone(self.auth.inspect(session["cookie_token"]))
        self.assertEqual(401,self.auth.login(PASSWORD)[0])
        self.assertEqual(200,self.auth.login(NEW)[0])
        loaded=LocalOperatorAuth.load(self.path)
        self.assertEqual(200,loaded.login(NEW)[0])
        self.assertEqual(401,loaded.login(PASSWORD)[0])
        self.assertNotIn(NEW,self.path.read_text())
        self.assertNotIn(PASSWORD,self.path.read_text())

    def test_failed_disk_write_keeps_credentials_and_sessions(self):
        status,session=self.auth.login(PASSWORD)
        self.assertEqual(200,status)
        with patch("local_source_policy.secure_replace_json",
                   side_effect=OSError("synthetic_io_failure")):
            code,data=self.auth.rotate_password(PASSWORD,NEW)
        self.assertEqual(503,code)
        self.assertEqual("owner_rotation_storage_failed",data["error"])
        self.assertTrue(self.auth.inspect(session["cookie_token"]))
        self.assertEqual(200,LocalOperatorAuth.load(self.path).login(PASSWORD)[0])
        self.assertEqual(401,self.auth.login(NEW)[0])

    def test_reauthentication_lockout_applies_to_rotation(self):
        clock=[1000.]
        auth=LocalOperatorAuth.load(self.path,clock=lambda:clock[0])
        for _ in range(5):
            self.assertEqual(401,auth.rotate_password("bad",NEW)[0])
        self.assertEqual(429,auth.rotate_password(PASSWORD,NEW)[0])
        clock[0]+=61
        self.assertEqual(200,auth.rotate_password(PASSWORD,NEW)[0])


class OperatorPolicyHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.owner_path=Path(self.tmp.name)/"owner.json"
        self.policy_path=Path(self.tmp.name)/"repos.json"
        LocalOperatorAuth.provision(self.owner_path,PASSWORD)
        self.auth=LocalOperatorAuth.load(self.owner_path)
        self.policy=LocalPublicSourcePolicy(self.policy_path)
        self.core=LocalWorkflowCore(Path(self.tmp.name)/"data.sqlite3")
        self.addCleanup(self.core.close)
        self.provider_calls=[]
        def fetch(repo):
            self.provider_calls.append(repo)
            return ISSUES
        self.httpd=ThreadingHTTPServer(("127.0.0.1",0),
             create_handler(local_core=self.core,operator_auth=self.auth,
                            source_policy=self.policy,github_fetcher=fetch))
        self.worker=threading.Thread(target=self.httpd.serve_forever,daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join,3)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def http(self,method,path,data=None,cookie=None,csrf=None,headers=None):
        connection=http.client.HTTPConnection("127.0.0.1",
                                               self.httpd.server_port,timeout=6)
        hdr={"Content-Type":"application/json"}
        if cookie is not None:hdr["Cookie"]=cookie
        if csrf is not None:hdr["X-AAS-CSRF"]=csrf
        hdr.update(headers or {})
        connection.request(method,path,body=json.dumps(data) if data is not None else None,headers=hdr)
        res=connection.getresponse()
        code=res.status
        c=res.getheader("Set-Cookie")
        content=json.loads(res.read())
        connection.close()
        return code,content,c

    def login(self,password=PASSWORD):
        code,data,cookie=self.http("POST","/api/operator-login",{"password":password})
        self.assertEqual(200,code,data)
        return cookie.split(";",1)[0],data["csrf_token"]

    def setup_active(self,cookie,csrf):
        self.assertEqual(201,self.http("POST","/api/local-create",FLOW,cookie,csrf)[0])
        self.assertEqual(200,self.http("POST","/api/local-activate",{
            "workflow_id":"rotating_flow","expected_version":1},cookie,csrf)[0])

    def preview(self,cookie,csrf):
        return self.http("POST","/api/local-github-preview",{
            "workflow_id":"rotating_flow","repo":REPO},cookie,csrf)

    def grant(self,cookie,csrf,password=PASSWORD):
        return self.http("POST","/api/local-github-source-grant",
            {"repo":REPO,"current_password":password},cookie,csrf)

    def revoke(self,cookie,csrf,password=PASSWORD):
        return self.http("POST","/api/local-github-source-revoke",
            {"repo":REPO,"current_password":password},cookie,csrf)

    def test_source_policy_list_requires_owner_cookie(self):
        self.assertEqual(401,self.http("GET","/api/local-github-sources")[0])
        cookie,csrf=self.login()
        code,data,_=self.http("GET","/api/local-github-sources",cookie=cookie)
        self.assertEqual(200,code)
        self.assertEqual([],data["allowed"])

    def test_source_default_denied_before_any_outbound_read(self):
        cookie,csrf=self.login()
        self.setup_active(cookie,csrf)
        code,data,_=self.preview(cookie,csrf)
        self.assertEqual(403,code)
        self.assertEqual("public_source_not_allowed",data["error"])
        self.assertEqual([],self.provider_calls)

    def test_source_permission_grant_needs_cookie_csrf_and_password(self):
        cookie,csrf=self.login()
        payload={"repo":REPO,"current_password":PASSWORD}
        self.assertEqual(401,self.http("POST","/api/local-github-source-grant",payload)[0])
        self.assertEqual(403,self.http("POST","/api/local-github-source-grant",
                                     payload,cookie=cookie)[0])
        self.assertEqual(401,self.grant(cookie,csrf,"bad-password")[0])
        self.assertFalse(self.policy.is_allowed(REPO))
        code,data,_=self.grant(cookie,csrf)
        self.assertEqual(200,code)
        self.assertTrue(self.policy.is_allowed(REPO))
        self.assertEqual([REPO],data["allowed"])

    def test_approved_source_reads_provider_and_commits_only_with_approval(self):
        cookie,csrf=self.login()
        self.setup_active(cookie,csrf)
        self.grant(cookie,csrf)
        code,preview,_=self.preview(cookie,csrf)
        self.assertEqual(200,code,preview)
        self.assertEqual(1,preview["new_count"])
        self.assertEqual(0,self.core.history("rotating_flow")[1]["total"])
        code,out,_=self.http("POST","/api/local-github-commit",{
            "workflow_id":"rotating_flow","snapshot_id":preview["snapshot_id"]},cookie,csrf)
        self.assertEqual(200,code,out)
        self.assertEqual(1,self.core.history("rotating_flow")[1]["total"])
        self.assertEqual([REPO],self.provider_calls)

    def test_revoke_invalidates_uncommitted_preview(self):
        cookie,csrf=self.login()
        self.setup_active(cookie,csrf)
        self.grant(cookie,csrf)
        preview=self.preview(cookie,csrf)[1]
        self.assertEqual(200,self.revoke(cookie,csrf)[0])
        code,data,_=self.http("POST","/api/local-github-commit",{
            "workflow_id":"rotating_flow","snapshot_id":preview["snapshot_id"]},cookie,csrf)
        self.assertEqual(403,code)
        self.assertEqual("public_source_permission_changed",data["error"])
        self.assertEqual(0,self.core.history("rotating_flow")[1]["total"])

    def test_revoke_and_regrant_cannot_reuse_old_source_preview(self):
        cookie,csrf=self.login()
        self.setup_active(cookie,csrf)
        self.grant(cookie,csrf)
        preview=self.preview(cookie,csrf)[1]
        self.revoke(cookie,csrf)
        self.grant(cookie,csrf)
        code,data,_=self.http("POST","/api/local-github-commit",{
            "workflow_id":"rotating_flow","snapshot_id":preview["snapshot_id"]},cookie,csrf)
        self.assertEqual(403,code)
        self.assertEqual(0,self.core.history("rotating_flow")[1]["total"])
        self.assertEqual(200,self.preview(cookie,csrf)[0])

    def test_new_reads_denied_after_revoke(self):
        cookie,csrf=self.login()
        self.setup_active(cookie,csrf)
        self.grant(cookie,csrf)
        self.revoke(cookie,csrf)
        self.assertEqual(403,self.preview(cookie,csrf)[0])
        self.assertEqual([],self.provider_calls)

    def test_revoke_requires_password_and_keeps_existing_source(self):
        cookie,csrf=self.login()
        self.grant(cookie,csrf)
        self.assertEqual(401,self.revoke(cookie,csrf,"wrong")[0])
        self.assertTrue(self.policy.is_allowed(REPO))
        self.assertEqual(200,self.revoke(cookie,csrf)[0])

    def test_rotated_password_revokes_sessions_and_old_login(self):
        cookie,csrf=self.login()
        self.setup_active(cookie,csrf)
        self.grant(cookie,csrf)
        code,data,expired=self.http("POST","/api/operator-rotate",{
            "current_password":PASSWORD,"new_password":NEW},cookie,csrf)
        self.assertEqual(200,code,data)
        self.assertIn("Max-Age=0",expired)
        self.assertEqual(401,self.http("GET","/api/local-github-sources",
                                       cookie=cookie)[0])
        self.assertEqual(401,self.http("POST","/api/operator-login",
                                  {"password":PASSWORD})[0])
        new_cookie,new_csrf=self.login(NEW)
        self.assertEqual(200,self.http("GET","/api/local-github-sources",
                                       cookie=new_cookie)[0])
        self.assertEqual([REPO],self.policy.snapshot()["allowed"])
        self.assertEqual(200,LocalOperatorAuth.load(self.owner_path).login(NEW)[0])

    def test_rotation_wrong_current_password_leaves_active_session(self):
        cookie,csrf=self.login()
        status,data,_=self.http("POST","/api/operator-rotate",{
            "current_password":"wrong","new_password":NEW},cookie,csrf)
        self.assertEqual(401,status)
        self.assertEqual(200,self.http("GET","/api/local-workflows",cookie=cookie)[0])
        self.assertEqual(200,LocalOperatorAuth.load(self.owner_path).login(PASSWORD)[0])

    def test_rotation_requires_cookie_and_matching_csrf(self):
        cookie,csrf=self.login()
        req={"current_password":PASSWORD,"new_password":NEW}
        self.assertEqual(401,self.http("POST","/api/operator-rotate",req)[0])
        self.assertEqual(403,self.http("POST","/api/operator-rotate",req,cookie)[0])
        self.assertEqual(200,self.http("POST","/api/operator-rotate",req,cookie,csrf)[0])

    def test_rejects_unexpected_source_permission_and_rotation_fields(self):
        cookie,csrf=self.login()
        self.assertEqual(422,self.http("POST","/api/local-github-source-grant",{
            "repo":REPO,"current_password":PASSWORD,"admin":True},cookie,csrf)[0])
        self.assertEqual(422,self.http("POST","/api/operator-rotate",{
            "new_password":NEW},cookie,csrf)[0])
        self.assertFalse(self.policy.is_allowed(REPO))

    def test_source_permission_change_origin_denied(self):
        cookie,csrf=self.login()
        self.assertEqual(403,self.http("POST","/api/local-github-source-grant",
            {"repo":REPO,"current_password":PASSWORD},cookie,csrf,
            headers={"Origin":"https://evil.example"})[0])
        self.assertFalse(self.policy.is_allowed(REPO))


if __name__ == "__main__":
    unittest.main()
