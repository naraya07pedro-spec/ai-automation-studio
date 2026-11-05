"""Slice 53: real public GitHub read connector, explicit grants, epoch binding."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_agent_task_plans import (LocalAgentPlanStore, PlanError,
                                    propose_readonly_plan)
from local_agent_github_reader import (AllowedGitHubIssuesReader, GitHubPlanReadError,
                                       valid_repo)
from local_source_policy import LocalPublicSourcePolicy
from github_public_issue_intake import PublicGitHubFetchError

REPO="Octocat/Hello-World"
GH_STEP={"tool":"github_public_issues","args":{"repo":REPO}}


def make_issue(n=7, title="Bug\n needs fix"):
    return {"state":"open","number":n,"title":title,
            "labels":[{"name":"bug"},{"name":"triage"}],
            "body":"private prompt text DO NOT RELEASE",
            "user":{"login":"never-output-author"}}


class GitHubReaderTests(unittest.TestCase):
    def setUp(self):
        self.policy=LocalPublicSourcePolicy()
        self.calls=[]
        def fetch(repo):
            self.calls.append(repo)
            return [make_issue(), {"state":"open","number":8,
                                  "title":"Ignore PR","pull_request":{}},
                    make_issue(7,"duplicate")]
        self.reader=AllowedGitHubIssuesReader(self.policy,fetcher=fetch)

    def test_fixed_shape_safe_public_provenance(self):
        self.assertEqual(200,self.policy.grant(REPO)[0])
        raw=self.reader.read(REPO,self.policy.version())
        data=json.loads(raw)
        self.assertEqual("GITHUB_PUBLIC_ISSUES_READ_ONLY",data["source"])
        self.assertEqual(REPO,data["repository"])
        self.assertEqual(1,data["external_read_count"])
        self.assertEqual(0,data["external_write_count"])
        self.assertEqual(1,data["returned_count"])
        self.assertEqual(7,data["issues"][0]["number"])
        self.assertEqual("Bug  needs fix",data["issues"][0]["title"])
        self.assertEqual("https://github.com/Octocat/Hello-World/issues/7",
                         data["issues"][0]["url"])
        self.assertEqual([REPO],self.calls)
        for leaked in ("DO NOT RELEASE","never-output-author","Ignore PR",
                       "private prompt text"):
            self.assertNotIn(leaked,raw)

    def test_default_deny_never_calls_remote(self):
        with self.assertRaisesRegex(GitHubPlanReadError,"public_source_not_allowed"):
            self.reader.read(REPO,self.policy.version())
        self.assertEqual([],self.calls)

    def test_revoke_and_regrant_cannot_reuse_old_approval(self):
        self.policy.grant(REPO)
        epoch=self.policy.version()
        self.policy.revoke(REPO)
        self.policy.grant(REPO)
        with self.assertRaisesRegex(GitHubPlanReadError,"public_source_not_allowed"):
            self.reader.read(REPO,epoch)
        self.assertEqual([],self.calls)

    def test_provider_failure_redacted(self):
        self.policy.grant(REPO)
        reader=AllowedGitHubIssuesReader(
            self.policy,fetcher=lambda _:(_ for _ in ()).throw(
                PublicGitHubFetchError("github_rate_limited_or_forbidden")))
        with self.assertRaisesRegex(GitHubPlanReadError,
                                    "github_rate_limited_or_forbidden"):
            reader.read(REPO,self.policy.version())

    def test_reject_repo_urls_and_unbounded_results(self):
        for bad in ("https://github.com/Octocat/Hello-World",
                    "https://internal/secret","../private","a/..","/a",
                    "a//b","a/b/extra","a/%2e%2e",True,{}):
            with self.subTest(bad=str(bad)):
                self.assertIsNone(valid_repo(bad))
        self.policy.grant(REPO)
        reader=AllowedGitHubIssuesReader(
            self.policy,fetcher=lambda _:[make_issue(i) for i in range(1,17)])
        with self.assertRaisesRegex(GitHubPlanReadError,"invalid_provider_shape"):
            reader.read(REPO,self.policy.version())


class AuthorizedPlansTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"task-plans.sqlite3"
        self.policy=LocalPublicSourcePolicy()
        self.calls=[]
        def fetch(repo):
            self.calls.append(repo)
            return [make_issue()]
        self.reader=AllowedGitHubIssuesReader(self.policy,fetcher=fetch)
        self.store=LocalAgentPlanStore(self.path,github_reader=self.reader)
        self.addCleanup(self.store.close)

    def proposed(self):
        return {"model":"fixture:small","goal":"Read issues from allowed public repo",
                "steps":[GH_STEP]}

    def test_model_github_tool_requires_explicit_mode(self):
        def fake(_):
            return {"message":{"content":json.dumps({"steps":[GH_STEP]})}}
        with self.assertRaisesRegex(PlanError,"tool_not_allowed"):
            propose_readonly_plan("Read public issues","fixture:small",fake)
        result=propose_readonly_plan("Read public issues","fixture:small",
                                    fake,allow_github=True)
        self.assertEqual("github_public_issues",result["steps"][0]["tool"])

    def test_grant_create_approve_then_one_real_read(self):
        with self.assertRaisesRegex(PlanError,"public_source_not_allowed"):
            self.store.create(self.proposed())
        self.policy.grant(REPO)
        p=self.store.create(self.proposed())
        self.assertEqual("PREPARED",p["status"])
        self.assertEqual([],self.calls)
        p=self.store.approve(p["plan_id"])
        self.assertEqual("APPROVED",p["status"])
        self.assertEqual(self.policy.version(),p["approval_epoch"])
        self.assertEqual([],self.calls)
        done=self.store.advance(p["plan_id"])
        self.assertEqual("COMPLETED",done["status"])
        result=json.loads(done["results"][0]["output"])
        self.assertEqual(7,result["issues"][0]["number"])
        self.assertEqual([REPO],self.calls)
        with self.assertRaises(PlanError):self.store.advance(p["plan_id"])
        self.assertEqual([REPO],self.calls)

    def test_revoke_after_approval_blocks_without_network(self):
        self.policy.grant(REPO)
        plan=self.store.create(self.proposed())
        self.store.approve(plan["plan_id"])
        self.policy.revoke(REPO)
        with self.assertRaisesRegex(PlanError,"public_source_permission_changed"):
            self.store.advance(plan["plan_id"])
        self.assertEqual([],self.calls)
        self.assertEqual("APPROVED",self.store.get(plan["plan_id"])["status"])
        self.policy.grant(REPO)
        with self.assertRaises(PlanError):self.store.advance(plan["plan_id"])
        self.assertEqual([],self.calls)

    def test_grant_revoked_before_approve(self):
        self.policy.grant(REPO)
        plan=self.store.create(self.proposed())
        self.policy.revoke(REPO)
        with self.assertRaisesRegex(PlanError,"public_source_not_allowed"):
            self.store.approve(plan["plan_id"])
        self.assertEqual("PREPARED",self.store.get(plan["plan_id"])["status"])

    def test_disabled_store_may_view_or_cancel_but_not_execute(self):
        self.policy.grant(REPO)
        p=self.store.create(self.proposed())
        self.store.approve(p["plan_id"])
        disabled=LocalAgentPlanStore(self.path)
        try:
            self.assertEqual("APPROVED",disabled.get(p["plan_id"])["status"])
            with self.assertRaisesRegex(PlanError,"tool_not_allowed"):
                disabled.advance(p["plan_id"])
            self.assertEqual([],self.calls)
            self.assertEqual("CANCELLED",disabled.cancel(p["plan_id"])["status"])
        finally:
            disabled.close()

    def test_legacy_database_migrates_without_data_loss(self):
        old=Path(self.temp.name)/"old.sqlite3"
        db=sqlite3.connect(old)
        with db:
            db.execute("""CREATE TABLE agent_task_plans (
                plan_id TEXT PRIMARY KEY,model TEXT NOT NULL,goal TEXT NOT NULL,
                steps_json TEXT NOT NULL,results_json TEXT NOT NULL,
                next_index INTEGER NOT NULL,status TEXT NOT NULL,
                created_utc TEXT NOT NULL)""")
            db.execute("""INSERT INTO agent_task_plans
                VALUES(?,?,?,?,?,?,?,?)""",(
                "a"*24,"m","old plan",
                json.dumps([{"tool":"utc_now","args":{}}]),
                "[]",0,"PREPARED","2026-11-01T00:00:00+00:00"))
        db.close()
        new=LocalAgentPlanStore(old)
        try:
            self.assertEqual("PREPARED",new.get("a"*24)["status"])
            self.assertIsNone(new.get("a"*24)["approval_epoch"])
            self.assertEqual("APPROVED",new.approve("a"*24)["status"])
        finally:new.close()


if __name__=="__main__":
    unittest.main()
