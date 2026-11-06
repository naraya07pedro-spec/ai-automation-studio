"""Slice 79: consented, no-grant HN public jobstories discovery preview."""
from http.server import ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from local_hn_job_discovery import (
    HNJobDiscovery, HNDiscoveryError, _fetch_job_ids,
    PREVIEW_MAX_ITEMS, LIST_MAX_IDS, LIST_MAX_BYTES)
from local_hn_story_policy import HNStoryPolicy
from local_hn_story_reader import AllowedHNStoryReader
from local_agent_task_plans import LocalAgentPlanStore, PlanError, validate_steps
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_server import create_handler
from local_tool_registry import (
    BATCH_READ_TOOLS, HN_DISCOVERY_PREVIEW, ToolPermissionError,
    registered_tools, require_discovery_preview)

IDS = [50010001, 50010002, 50010003, 50010004, 50010005, 50010006]
TITLE = "SYNTHETIC_PUBLIC_JOB_TITLE"


def fixture_job(job_id):
    if job_id not in IDS:
        raise AssertionError("unapproved network resource")
    return {"id":job_id, "type":"job", "time":1735689600,
            "title": "<b>"+TITLE+"_"+str(job_id)+"</b><script>DROP_SECRETS</script>",
            "text":"Owner does not fetch job body in discovery", "by":"contact_private"}


class JobDiscoveryUnits(unittest.TestCase):
    def test_exact_five_metadata_zero_grants_zero_writes_no_model(self):
        seen=[]
        policy=HNStoryPolicy()
        def listed():
            seen.append(("list",))
            return list(IDS)
        def item(i):
            seen.append(("item",i))
            return fixture_job(i)
        out=HNJobDiscovery(list_fetcher=listed,item_fetcher=item).preview()
        self.assertEqual("HACKER_NEWS_PUBLIC_JOBSTORIES_PREVIEW_V1",out["source"])
        self.assertEqual(5,len(out["jobs"]))
        self.assertEqual(6,out["external_read_count"])
        self.assertEqual(6,len(seen))
        self.assertEqual(IDS[:5],[x["story_id"] for x in out["jobs"]])
        self.assertEqual(6,out["feed_ids_returned"])
        self.assertEqual(5,out["ids_considered"])
        self.assertEqual(0,out["auto_grant_count"])
        self.assertEqual(0,out["external_write_count"])
        self.assertEqual(0,out["local_file_write_count"])
        self.assertEqual(0,out["model_calls"])
        self.assertTrue(out["requires_separate_owner_grant"])
        self.assertFalse(out["job_active_verified"])
        self.assertFalse(out["indonesia_eligibility_verified"])
        self.assertNotIn("DROP_SECRETS",json.dumps(out))
        self.assertNotIn("Owner does not fetch job body",json.dumps(out))
        self.assertNotIn("contact_private",json.dumps(out))
        self.assertFalse(policy.snapshot()["allowed_story_ids"])
        self.assertEqual(0,policy.version())
        self.assertEqual(64,len(out["source_sha256"]))
        self.assertTrue(all(x["owner_story_grant"] is False for x in out["jobs"]))

    def test_preview_provenance_is_stable_across_timestamps(self):
        adapter=HNJobDiscovery(list_fetcher=lambda:list(IDS),
                               item_fetcher=fixture_job)
        first=adapter.preview()
        second=adapter.preview()
        self.assertEqual(first["source_sha256"],second["source_sha256"])
        self.assertEqual(first["jobs"],second["jobs"])

    def test_preview_rejects_bad_lists_duplicates_bool_and_oversize(self):
        invalid=[
            None, {}, [], [True], ["50010001"], [0], [-1], [1000000000],
            IDS+[50010001], [1]*201, list(range(1,LIST_MAX_IDS+2))
        ]
        for data in invalid:
            with self.subTest(data=str(data)[:60]):
                with self.assertRaisesRegex(HNDiscoveryError,"hn_discovery_provider_invalid"):
                    HNJobDiscovery(list_fetcher=lambda d=data:d,
                                   item_fetcher=fixture_job).preview()

    def test_preview_rejects_mismatched_types_and_untrusted_items(self):
        for item in (
            {"id":IDS[0],"type":"story","title":"nonjob"},
            {"id":IDS[0],"type":"job","title":False},
            {"id":True,"type":"job","title":"fake"},
            {"id":999,"type":"job","title":"fake"},
            ["not","a","dict"],
        ):
            with self.subTest(item=item):
                with self.assertRaisesRegex(
                        HNDiscoveryError,"hn_discovery_provider_invalid"):
                    HNJobDiscovery(list_fetcher=lambda:list(IDS),
                                   item_fetcher=lambda _,x=item:x).preview()

    def test_deleted_and_empty_titles_skipped_not_granted(self):
        def item(i):
            obj=fixture_job(i)
            if i==IDS[0]:obj["deleted"]=True
            if i==IDS[1]:obj["title"]="<script>BAD</script>"
            return obj
        out=HNJobDiscovery(list_fetcher=lambda:list(IDS),
                           item_fetcher=item).preview()
        self.assertEqual(3,len(out["jobs"]))
        self.assertEqual(6,out["external_read_count"])
        self.assertEqual(5,out["ids_considered"])
        self.assertEqual(0,out["auto_grant_count"])

    def test_network_failure_fails_closed_without_leaking_messages(self):
        def bad(_):
            raise RuntimeError("SECRET_CREDENTIAL_IN_EXCEPTION")
        with self.assertRaisesRegex(HNDiscoveryError,"hn_discovery_item_unavailable") as found:
            HNJobDiscovery(list_fetcher=lambda:list(IDS),
                           item_fetcher=bad).preview()
        self.assertNotIn("SECRET_CREDENTIAL",str(found.exception))
        with self.assertRaisesRegex(HNDiscoveryError,
                                    "hn_discovery_provider_unavailable"):
            HNJobDiscovery(list_fetcher=lambda:(_ for _ in ()).throw(
                RuntimeError("PRIVATE"))).preview()

    def test_list_http_only_exact_official_path_tls_no_redirects(self):
        calls=[]
        class Response:
            def __init__(self,status=200,raw=None,content_type="application/json"):
                self.status=status
                self.raw=json.dumps(IDS).encode() if raw is None else raw
                self.content_type=content_type
            def getheader(self,key,default=""):
                return self.content_type if key=="Content-Type" else default
            def read(self,size):
                calls.append(("read_bound",size))
                return self.raw[:size]
        class Connection:
            def __init__(self,host,timeout,context):
                calls.append(("connect",host,timeout,bool(context)))
            def request(self,method,path,headers):
                calls.append(("request",method,path,headers))
            def getresponse(self):return Response()
            def close(self):calls.append(("close",))
        with patch("local_hn_job_discovery.http.client.HTTPSConnection",Connection):
            self.assertEqual(IDS,_fetch_job_ids())
        self.assertEqual("hacker-news.firebaseio.com",calls[0][1])
        self.assertEqual("GET",calls[1][1])
        self.assertEqual("/v0/jobstories.json",calls[1][2])
        self.assertNotIn("Authorization",calls[1][3])
        self.assertEqual(LIST_MAX_BYTES+1,calls[2][1])
        for invalid in (
            Response(status=302),
            Response(status=429),
            Response(status=200,raw=b"{}"),
            Response(status=200,raw=b"{}"*5000),
            Response(status=200,content_type="text/html"),
        ):
            with self.subTest(status=invalid.status):
                class Failing(Connection):
                    def getresponse(self):return invalid
                with patch("local_hn_job_discovery.http.client.HTTPSConnection",Failing):
                    if invalid.status==200 and invalid.raw==b"{}":
                        self.assertEqual({},_fetch_job_ids())
                    else:
                        with self.assertRaises(HNDiscoveryError):
                            _fetch_job_ids()

    def test_discovery_registry_is_not_a_model_or_batch_tool(self):
        registry=registered_tools(planning=True,hn=True,hn_discovery=True)
        item=next(v for v in registry if v["name"]==HN_DISCOVERY_PREVIEW)
        self.assertEqual(12,len(registry))
        self.assertEqual("PUBLIC_READ",item["effect"])
        self.assertTrue(item["enabled"])
        self.assertFalse(item["model_plannable"])
        self.assertFalse(item["batch_allowed"])
        self.assertTrue(item["owner_confirmation_required"])
        self.assertNotIn(HN_DISCOVERY_PREVIEW,BATCH_READ_TOOLS)
        self.assertEqual(HN_DISCOVERY_PREVIEW,
                         require_discovery_preview(HN_DISCOVERY_PREVIEW,enabled=True))
        with self.assertRaisesRegex(ToolPermissionError,"hn_discovery_permission_denied"):
            require_discovery_preview(HN_DISCOVERY_PREVIEW,enabled=False)
        with self.assertRaisesRegex(ToolPermissionError,"hn_discovery_permission_denied"):
            require_discovery_preview("hn_public_story",enabled=True)
        with self.assertRaisesRegex(PlanError,"tool_not_allowed"):
            validate_steps([{"tool":HN_DISCOVERY_PREVIEW,"args":{}}])


class HNDiscoveryHTTP(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home=Path(tmp.name)
        self.policy=HNStoryPolicy(self.home/"hn-grants.json")
        self.audit=LocalSecurityAudit(self.home/"audit.sqlite3")
        self.addCleanup(self.audit.close)
        self.reader=AllowedHNStoryReader(self.policy,fetcher=fixture_job)
        self.store=LocalAgentPlanStore(self.home/"plans.sqlite3",
                                       hn_reader=self.reader)
        self.addCleanup(self.store.close)
        self.auth=LocalOperatorAuth.ephemeral_for_tests(
            "only_for_fixture_hn_preview_password_123")
        self.calls=0
        def item(i):
            self.calls+=1
            return fixture_job(i)
        self.adapter=HNJobDiscovery(
            list_fetcher=lambda:list(IDS),item_fetcher=item)
        self.server=ThreadingHTTPServer(("127.0.0.1",0),create_handler(
            operator_auth=self.auth,security_audit=self.audit,
            hn_policy=self.policy,hn_job_discovery=self.adapter,
            local_agent_runner=lambda *args:None,
            local_agent_planner=lambda *args,**kwargs:None,
            local_agent_plan_store=self.store))
        worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        worker.start()
        self.addCleanup(worker.join,4)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        _,session=self.auth.login("only_for_fixture_hn_preview_password_123")
        self.cookie="aas_session="+session["cookie_token"]
        self.csrf=session["csrf_token"]

    def request(self,path,body=None,*,method="POST",auth=True,csrf=True,origin=None):
        conn=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=12)
        headers={}
        if auth:headers["Cookie"]=self.cookie
        if csrf:headers["X-AAS-CSRF"]=self.csrf
        if origin:headers["Origin"]=origin
        if method=="POST":
            headers["Content-Type"]="application/json"
            data=json.dumps(body).encode()
        else:
            data=None
        conn.request(method,path,data,headers)
        res=conn.getresponse()
        out=res.status,json.loads(res.read())
        conn.close()
        return out

    def test_exact_http_schema_auth_origin_csrf_and_no_grants(self):
        path="/api/local-hn-jobstories-preview"
        before=self.policy.snapshot()
        self.assertEqual(401,self.request(path,{},auth=False)[0])
        self.assertEqual(403,self.request(path,{},csrf=False)[0])
        self.assertEqual(403,self.request(path,{},origin="https://evil.invalid")[0])
        self.assertEqual(422,self.request(path,{"story_id":IDS[0]})[0])
        self.assertEqual(422,self.request(path,[]) [0])
        self.assertEqual(0,self.calls)
        code,out=self.request(path,{})
        self.assertEqual(200,code)
        self.assertEqual(5,len(out["jobs"]))
        self.assertEqual(5,self.calls)
        self.assertEqual(before,self.policy.snapshot())
        self.assertFalse((self.home/"hn-grants.json").exists())
        self.assertEqual(0,self.store.db.execute(
            "SELECT COUNT(*) FROM agent_task_plans").fetchone()[0])
        records=self.audit.recent(10)
        actions=[x for x in records if x["action"]=="HN_JOB_DISCOVERY"]
        self.assertEqual(["SUCCESS","ATTEMPT"],[x["outcome"] for x in actions])
        self.assertTrue(all(x["source_fingerprint"]=="" for x in actions))
        code,caps=self.request("/api/local-agent-capabilities",method="GET")
        self.assertEqual(200,code)
        self.assertTrue(caps["hn_job_discovery"])
        item=next(t for t in caps["tool_registry"]
                  if t["name"]==HN_DISCOVERY_PREVIEW)
        self.assertTrue(item["enabled"])
        self.assertFalse(item["model_plannable"])

    def test_disabled_adapter_denies_without_network_or_grants(self):
        with tempfile.TemporaryDirectory() as tmp:
            disabled=ThreadingHTTPServer(("127.0.0.1",0),create_handler(
                operator_auth=self.auth,security_audit=self.audit,
                hn_policy=self.policy,
                local_agent_runner=lambda *_:None,
                local_agent_planner=lambda *args,**kwargs:None,
                local_agent_plan_store=self.store))
            thread=threading.Thread(target=disabled.serve_forever,daemon=True)
            thread.start()
            try:
                conn=http.client.HTTPConnection(
                    "127.0.0.1",disabled.server_port,timeout=8)
                conn.request("POST","/api/local-hn-jobstories-preview",
                             b"{}",{"Cookie":self.cookie,"X-AAS-CSRF":self.csrf,
                                    "Content-Type":"application/json"})
                res=conn.getresponse()
                self.assertEqual(403,res.status)
                self.assertEqual("hn_discovery_permission_denied",
                                 json.loads(res.read())["error"])
                conn.close()
                self.assertEqual(0,self.calls)
            finally:
                disabled.shutdown()
                disabled.server_close()
                thread.join(timeout=4)

    def test_provider_failure_audited_and_no_permission_changes(self):
        old=self.adapter._list_fetcher
        self.adapter._list_fetcher=lambda:{"bad":"shape"}
        try:
            code,result=self.request("/api/local-hn-jobstories-preview",{})
            self.assertEqual(502,code)
            self.assertEqual("hn_discovery_provider_invalid",result["error"])
            self.assertFalse(self.policy.snapshot()["allowed_story_ids"])
            self.assertEqual(0,self.policy.version())
            events=self.audit.recent(5)
            self.assertEqual("FAILED",events[0]["outcome"])
        finally:
            self.adapter._list_fetcher=old


if __name__=="__main__":
    unittest.main()
