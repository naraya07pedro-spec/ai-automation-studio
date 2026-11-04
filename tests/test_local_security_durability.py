"""Operator security audit + post-rename durability failure adversarial tests.

All tests are local, use synthetic data and deliberately inject filesystem
errors. No public/private GitHub account mutation or external provider call.
"""
import http.client
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
import local_source_policy as policy_module
from local_source_policy import (
    LocalPublicSourcePolicy, PostReplaceDurabilityError,
    secure_replace_json)
from local_operator_auth import LocalOperatorAuth
from local_security_audit import (
    LocalSecurityAudit, SecurityAuditError)
from local_workflow_core import LocalWorkflowCore
from local_server import create_handler

PASSWORD="synthetic_owner_password_46"
NEW_PASSWORD="synthetic_owner_new_password_46"
REPO="naraya07pedro-spec/ai-automation-studio"
FLOW={"workflow_id":"audit_flow","display_name":"Audit Test",
      "service_filter":"any","min_budget_tier":1,
      "followup_policy":"review_required"}


def fail_second_fsync():
    real_fsync=os.fsync
    calls=[0]
    def fsync(fd):
        calls[0]+=1
        if calls[0]==2:
            raise OSError("injected postrename directory fsync failure")
        return real_fsync(fd)
    return fsync


class PostRenameDurabilityTests(unittest.TestCase):
    def setUp(self):
        if os.name != "posix":
            self.skipTest("directory fsync is POSIX-specific")
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.owner_path=Path(self.tmp.name)/"owner.json"
        LocalOperatorAuth.provision(self.owner_path,PASSWORD)
        self.policy_path=Path(self.tmp.name)/"policy.json"

    def test_pre_rename_sync_failure_preserves_old_file(self):
        before=self.owner_path.read_bytes()
        with patch("local_source_policy.os.fsync",side_effect=OSError("before")):
            with self.assertRaises(OSError):
                secure_replace_json(self.owner_path,{"version":11})
        self.assertEqual(before,self.owner_path.read_bytes())
        self.assertEqual([],list(self.owner_path.parent.glob(".*.tmp")))

    def test_directory_sync_failure_has_distinct_post_replace_exception(self):
        before=self.owner_path.read_bytes()
        with patch("local_source_policy.os.fsync",side_effect=fail_second_fsync()):
            with self.assertRaises(PostReplaceDurabilityError):
                secure_replace_json(self.owner_path,{"revision":46})
        self.assertNotEqual(before,self.owner_path.read_bytes())
        self.assertEqual({"revision":46},json.loads(self.owner_path.read_text()))

    def test_owner_rotation_post_rename_failure_revokes_session_and_requires_restart(self):
        owner=LocalOperatorAuth.load(self.owner_path)
        session=owner.login(PASSWORD)[1]
        with patch("local_source_policy.os.fsync",side_effect=fail_second_fsync()):
            status,result=owner.rotate_password(PASSWORD,NEW_PASSWORD)
        self.assertEqual(503,status)
        self.assertEqual("operator_restart_required",result["error"])
        self.assertIsNone(owner.inspect(session["cookie_token"]))
        self.assertEqual(503,owner.login(PASSWORD)[0])
        self.assertEqual(503,owner.login(NEW_PASSWORD)[0])
        reloaded=LocalOperatorAuth.load(self.owner_path)
        self.assertEqual(200,reloaded.login(NEW_PASSWORD)[0])
        self.assertEqual(401,reloaded.login(PASSWORD)[0])

    def test_recovery_enrollment_post_rename_failure_does_not_emit_code(self):
        owner=LocalOperatorAuth.load(self.owner_path)
        session=owner.login(PASSWORD)[1]
        with patch("local_source_policy.os.fsync",side_effect=fail_second_fsync()):
            status,result=owner.setup_recovery(PASSWORD)
        self.assertEqual(503,status)
        self.assertNotIn("recovery_code",result)
        self.assertEqual("operator_restart_required",result["error"])
        self.assertIsNone(owner.inspect(session["cookie_token"]))
        loaded=LocalOperatorAuth.load(self.owner_path)
        self.assertIsNotNone(loaded._recovery_hash)
        self.assertEqual(200,loaded.login(PASSWORD)[0])

    def test_consumed_recovery_post_rename_failure_forces_reload(self):
        owner=LocalOperatorAuth.load(self.owner_path)
        code=owner.setup_recovery(PASSWORD)[1]["recovery_code"]
        with patch("local_source_policy.os.fsync",side_effect=fail_second_fsync()):
            status,result=owner.recover_with_code(code,NEW_PASSWORD)
        self.assertEqual(503,status)
        self.assertEqual("operator_restart_required",result["error"])
        self.assertEqual(503,owner.login(PASSWORD)[0])
        reloaded=LocalOperatorAuth.load(self.owner_path)
        self.assertEqual(200,reloaded.login(NEW_PASSWORD)[0])
        self.assertEqual(403,reloaded.recover_with_code(code,PASSWORD)[0])

    def test_grant_post_rename_failure_never_authorizes_stale_memory(self):
        policy=LocalPublicSourcePolicy(self.policy_path)
        with patch("local_source_policy.os.fsync",side_effect=fail_second_fsync()):
            status,result=policy.grant(REPO)
        self.assertEqual(503,status)
        self.assertEqual("source_policy_restart_required",result["error"])
        self.assertFalse(policy.is_allowed(REPO))
        self.assertEqual([],policy.snapshot()["allowed"])
        self.assertTrue(policy.snapshot()["storage_recovery_required"])
        self.assertEqual(503,policy.grant(REPO)[0])
        self.assertTrue(LocalPublicSourcePolicy(self.policy_path).is_allowed(REPO))

    def test_revoke_post_rename_failure_denies_all_sources_until_restart(self):
        policy=LocalPublicSourcePolicy(self.policy_path)
        self.assertEqual(200,policy.grant(REPO)[0])
        epoch=policy.version()
        with patch("local_source_policy.os.fsync",side_effect=fail_second_fsync()):
            status,data=policy.revoke(REPO)
        self.assertEqual(503,status)
        self.assertFalse(policy.is_allowed(REPO,epoch))
        self.assertFalse(policy.is_allowed(REPO))
        self.assertEqual(503,policy.revoke(REPO)[0])
        self.assertFalse(LocalPublicSourcePolicy(self.policy_path).is_allowed(REPO))


class SecurityAuditStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"security-audit.sqlite3"
        self.audit=LocalSecurityAudit(self.path)
        self.addCleanup(self.audit.close)

    def test_record_only_allowlisted_event_and_outcome(self):
        for invalid in ("ARBITRARY_TEXT","PASSWORD_SECRET",None,""):
            with self.assertRaises(ValueError):
                self.audit.record(invalid,"SUCCESS")
        with self.assertRaises(ValueError):
            self.audit.record("OPERATOR_LOGIN","PASSWORD=SECRET")
        self.assertEqual([],self.audit.recent())

    def test_repository_name_is_fingerprinted_and_not_stored_raw(self):
        self.audit.record("PUBLIC_SOURCE_GRANT","ATTEMPT",REPO)
        record=self.audit.recent()[0]
        self.assertEqual(16,len(record["source_fingerprint"]))
        self.assertEqual("PUBLIC_SOURCE_GRANT",record["action"])
        self.assertNotIn(REPO,self.path.read_bytes().decode("latin1"))
        self.assertNotIn(PASSWORD,self.path.read_bytes().decode("latin1"))

    def test_case_insensitive_source_fingerprints_are_stable(self):
        self.audit.record("PUBLIC_SOURCE_GRANT","SUCCESS",REPO)
        self.audit.record("PUBLIC_SOURCE_REVOKE","SUCCESS",REPO.upper())
        rows=self.audit.recent()
        self.assertEqual(rows[0]["source_fingerprint"],rows[1]["source_fingerprint"])

    def test_redacted_ledger_survives_restart_and_has_monotonic_order(self):
        self.audit.record("OPERATOR_LOGIN","SUCCESS")
        self.audit.record("OWNER_ROTATE","ATTEMPT")
        self.audit.record("OWNER_ROTATE","SUCCESS")
        rows=self.audit.recent()
        self.assertEqual([3,2,1],[row["seq"] for row in rows])
        other=LocalSecurityAudit(self.path)
        try:
            self.assertEqual(rows,other.recent())
        finally:
            other.close()

    def test_unauthorized_audit_limits_rejected(self):
        for invalid in (0,-1,101,True,None,"40"):
            with self.assertRaises(ValueError):
                self.audit.recent(invalid)

    def test_ledger_retention_is_bounded_with_explicit_old_record_pruning(self):
        with patch.object(sys.modules["local_security_audit"],"MAX_EVENTS",4), \
             patch.object(sys.modules["local_security_audit"],"TRIM_COUNT",2):
            for i in range(7):
                self.audit.record("OPERATOR_LOGIN",
                                  "SUCCESS" if i%2 else "DENIED")
        result=self.audit.recent(100)
        self.assertLessEqual(len(result),4)
        self.assertEqual(7,result[0]["seq"])

    def test_sqlite_write_failure_blocks_further_recording(self):
        self.audit._db.close()
        with self.assertRaises(SecurityAuditError):
            self.audit.record("OPERATOR_LOGIN","SUCCESS")
        self.assertFalse(self.audit.ready())
        with self.assertRaises(SecurityAuditError):
            self.audit.recent()

    def test_file_permissions_restricted_on_posix(self):
        if os.name=="posix":
            import stat
            self.assertEqual(0,stat.S_IMODE(self.path.stat().st_mode) & 0o077)

    def test_insecure_existing_ledger_does_not_open_on_posix(self):
        if os.name!="posix":
            self.skipTest("POSIX-only file permission gate")
        self.path.chmod(0o644)
        with self.assertRaises(SecurityAuditError):
            LocalSecurityAudit(self.path)


class AuditedOperatorHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home=Path(self.tmp.name)
        owner_path=home/"operator.json"
        LocalOperatorAuth.provision(owner_path,PASSWORD)
        self.owner=LocalOperatorAuth.load(owner_path)
        self.audit=LocalSecurityAudit(home/"security-audit.sqlite3")
        self.addCleanup(self.audit.close)
        self.policy=LocalPublicSourcePolicy(home/"public-github-sources.json")
        self.core=LocalWorkflowCore(home/"workflows.sqlite3")
        self.addCleanup(self.core.close)
        self.calls=[]
        def fake_provider(repo):
            self.calls.append(repo)
            return []
        self.server=ThreadingHTTPServer(("127.0.0.1",0),
            create_handler(local_core=self.core,operator_auth=self.owner,
                           source_policy=self.policy,security_audit=self.audit,
                           github_fetcher=fake_provider))
        thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(thread.join,3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def http(self,method,path,body=None,cookie=None,csrf=None):
        conn=http.client.HTTPConnection("127.0.0.1",
                                        self.server.server_port,timeout=5)
        headers={"Content-Type":"application/json"}
        if cookie is not None:headers["Cookie"]=cookie
        if csrf is not None:headers["X-AAS-CSRF"]=csrf
        conn.request(method,path,body=json.dumps(body) if body is not None else None,
                     headers=headers)
        response=conn.getresponse()
        status=response.status
        c=response.getheader("Set-Cookie")
        data=json.loads(response.read())
        conn.close()
        return status,data,c

    def login(self):
        status,data,cookie=self.http("POST","/api/operator-login",
                                     {"password":PASSWORD})
        self.assertEqual(200,status)
        return cookie.split(";",1)[0],data["csrf_token"]

    def test_audit_read_requires_session_and_exposes_no_secrets(self):
        self.assertEqual(401,self.http("GET","/api/operator-audit")[0])
        cookie,csrf=self.login()
        status,body,_=self.http("GET","/api/operator-audit?limit=5",cookie=cookie)
        self.assertEqual(200,status)
        self.assertEqual("OPERATOR_LOGIN",body["events"][0]["action"])
        self.assertEqual("SUCCESS",body["events"][0]["outcome"])
        self.assertNotIn(PASSWORD,str(body))
        self.assertNotIn(csrf,str(body))
        self.assertNotIn(cookie,str(body))

    def test_bad_login_is_recorded_only_as_denied(self):
        self.assertEqual(401,self.http("POST","/api/operator-login",
                                       {"password":"malicioussecret"})[0])
        rows=self.audit.recent()
        self.assertEqual("OPERATOR_LOGIN",rows[0]["action"])
        self.assertEqual("DENIED",rows[0]["outcome"])
        self.assertNotIn("malicioussecret",str(rows))

    def test_owner_rotation_has_attempt_and_result_and_revokes_session(self):
        cookie,csrf=self.login()
        status,_,_=self.http("POST","/api/operator-rotate",{
            "current_password":PASSWORD,"new_password":NEW_PASSWORD},
            cookie,csrf)
        self.assertEqual(200,status)
        events=self.audit.recent()
        self.assertEqual(["SUCCESS","ATTEMPT"],
                         [row["outcome"] for row in events[:2]])
        self.assertEqual(["OWNER_ROTATE","OWNER_ROTATE"],
                         [row["action"] for row in events[:2]])
        self.assertEqual(401,self.http("GET","/api/local-workflows",cookie=cookie)[0])

    def test_source_grant_and_revoke_logged_without_plain_repo(self):
        cookie,csrf=self.login()
        for verb in ("grant","revoke"):
            status,data,_=self.http("POST","/api/local-github-source-"+verb,{
                "repo":REPO,"current_password":PASSWORD},cookie,csrf)
            self.assertEqual(200,status,data)
        rows=self.audit.recent()
        self.assertEqual(["PUBLIC_SOURCE_REVOKE","PUBLIC_SOURCE_REVOKE",
                          "PUBLIC_SOURCE_GRANT","PUBLIC_SOURCE_GRANT"],
                         [row["action"] for row in rows[:4]])
        self.assertNotIn(REPO,str(rows))
        self.assertTrue(all(len(row["source_fingerprint"])==16
                            for row in rows[:4]))

    def test_wrong_source_password_records_denied_not_granted(self):
        cookie,csrf=self.login()
        status,_,_=self.http("POST","/api/local-github-source-grant",{
            "repo":REPO,"current_password":"wrong"},cookie,csrf)
        self.assertEqual(401,status)
        self.assertFalse(self.policy.is_allowed(REPO))
        self.assertEqual("DENIED",self.audit.recent()[0]["outcome"])

    def test_audit_storage_unavailable_blocks_login_and_sensitive_mutations(self):
        cookie,csrf=self.login()
        self.audit._blocked=True
        status,body,_=self.http("POST","/api/local-github-source-grant",{
            "repo":REPO,"current_password":PASSWORD},cookie,csrf)
        self.assertEqual(503,status)
        self.assertFalse(self.policy.is_allowed(REPO))
        self.assertEqual(503,self.http("POST","/api/operator-login",{
            "password":PASSWORD})[0])
        self.assertEqual(503,self.http("GET","/api/operator-audit",cookie=cookie)[0])
        # A previously valid operator is still allowed to lock/logout.
        self.assertEqual(200,self.http("POST","/api/operator-logout",
                                       {},cookie,csrf)[0])

    def test_audit_prewrite_failure_denies_grant_without_provider_fetch(self):
        cookie,csrf=self.login()
        with patch.object(self.audit,"record",
                          side_effect=SecurityAuditError("synthetic_audit_failure")):
            status,body,_=self.http("POST","/api/local-github-source-grant",{
                "repo":REPO,"current_password":PASSWORD},cookie,csrf)
        self.assertEqual(503,status)
        self.assertFalse(self.policy.is_allowed(REPO))
        self.assertEqual([],self.calls)

    def test_audit_read_rejects_unknown_query_keys_and_invalid_limits(self):
        cookie,csrf=self.login()
        self.assertEqual(422,self.http("GET","/api/operator-audit?limit=0",cookie=cookie)[0])
        self.assertEqual(422,self.http("GET","/api/operator-audit?limit=999",cookie=cookie)[0])
        self.assertEqual(422,self.http("GET","/api/operator-audit?secret=1",cookie=cookie)[0])

    def test_source_denied_before_external_fetch_when_unapproved(self):
        cookie,csrf=self.login()
        self.assertEqual(201,self.http("POST","/api/local-create",
                                      FLOW,cookie,csrf)[0])
        self.assertEqual(200,self.http("POST","/api/local-activate",{
            "workflow_id":"audit_flow","expected_version":1},cookie,csrf)[0])
        self.assertEqual(403,self.http("POST","/api/local-github-preview",{
            "workflow_id":"audit_flow","repo":REPO},cookie,csrf)[0])
        self.assertEqual([],self.calls)


if __name__=="__main__":
    unittest.main()
