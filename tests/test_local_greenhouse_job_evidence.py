"""Slice 80: exact owner-clicked, public Greenhouse ATS evidence, no writes."""
from http.server import ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_greenhouse_job_evidence import (
    GreenhouseJobEvidence, GreenhouseEvidenceError, _get_public_job,
    validate_greenhouse_target, MAX_BYTES)
from local_tool_registry import (
    GREENHOUSE_EVIDENCE, BATCH_READ_TOOLS, registered_tools,
    require_discovery_preview, ToolPermissionError)
from local_agent_task_plans import LocalAgentPlanStore, PlanError, validate_steps
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_server import create_handler

BOARD="examplecorp"
JOB=1234567
HN_ID=77712301
SOURCE={
    "id":JOB,"title":"<p>SYNTHETIC Integration Engineer</p><script>SECRET</script>",
    "company_name":"Example Corp",
    "location":{"name":"Anywhere in United States"},
    "content":"<p>US-only work authorization required. SYNTHETIC JOB.</p>"
              "<script>SENSITIVE_HTML</script>",
    "first_published":"2026-10-01T12:00:00Z",
    "updated_at":"2026-10-03T12:00:00Z",
    "application_deadline":None,
    "absolute_url":"https://evil.invalid/exfiltrate?secret=1",
    "questions":[{"name":"Never echo application questions"}],
}

class GreenhouseUnitTests(unittest.TestCase):
    def test_exact_official_record_and_no_unsupported_eligibility_claim(self):
        seen=[]
        def fetch(board,ident):
            seen.append((board,ident))
            return dict(SOURCE)
        out=GreenhouseJobEvidence(fetcher=fetch).preview(BOARD,JOB,HN_ID)
        self.assertEqual([(BOARD,JOB)],seen)
        self.assertEqual("GREENHOUSE_OFFICIAL_PUBLIC_JOB_BOARD_V1",out["source"])
        self.assertEqual("SYNTHETIC Integration Engineer",out["title"])
        self.assertEqual("Anywhere in United States",out["location_label"])
        self.assertIn("US-only work authorization required",out["description_excerpt"])
        self.assertFalse(out["indonesia_eligibility_verified"])
        self.assertFalse(out["remote_work_verified"])
        self.assertFalse(out["accepting_applications_verified"])
        self.assertFalse(out["hn_to_ats_match_verified"])
        self.assertFalse(out["employer_identity_independently_verified"])
        self.assertEqual(HN_ID,out["hn_story_id_supplied_by_owner"])
        self.assertTrue(out["public_job_post_observed"])
        self.assertEqual("https://job-boards.greenhouse.io/examplecorp/jobs/1234567",
                         out["canonical_job_url"])
        self.assertEqual(1,out["external_read_count"])
        self.assertEqual(0,out["external_write_count"])
        self.assertEqual(0,out["model_calls"])
        self.assertFalse(out["applicant_data_sent"])
        self.assertEqual(64,len(out["source_sha256"]))
        self.assertTrue(out["checked_at_utc"].endswith("+00:00"))
        text=json.dumps(out)
        self.assertNotIn("evil.invalid",text)
        self.assertNotIn("SENSITIVE_HTML",text)
        self.assertNotIn("Never echo",text)
        self.assertNotIn("SECRET",text)

    def test_owner_claim_of_cross_source_relation_does_not_verify_match(self):
        no_link=GreenhouseJobEvidence(fetcher=lambda *_: dict(SOURCE)).preview(BOARD,JOB)
        self.assertIsNone(no_link["hn_story_id_supplied_by_owner"])
        self.assertFalse(no_link["hn_to_ats_match_verified"])
        owner_link=GreenhouseJobEvidence(fetcher=lambda *_: dict(SOURCE)).preview(BOARD,JOB,HN_ID)
        self.assertEqual(HN_ID,owner_link["hn_story_id_supplied_by_owner"])
        self.assertFalse(owner_link["hn_to_ats_match_verified"])

    def test_invalid_ats_target_blocks_before_network(self):
        invalid=[
            ("https://evil.invalid",JOB,None),
            ("../admin",JOB,None),
            ("two/boards",JOB,None),
            ("UPPER",JOB,None),
            ("internal",JOB,None),
            (" board ",JOB,None),
            ("a"*65,JOB,None),
            ("",JOB,None),
            (1,JOB,None),
            (BOARD,"123",None),
            (BOARD,0,None),
            (BOARD,True,None),
            (BOARD,1000000000,None),
            (BOARD,JOB,"77712301"),
            (BOARD,JOB,True),
            (BOARD,JOB,0),
        ]
        for board,job,hn in invalid:
            with self.subTest(board=str(board),job=job,hn=hn):
                with self.assertRaisesRegex(GreenhouseEvidenceError,
                                             "invalid_greenhouse_target"):
                    GreenhouseJobEvidence(fetcher=lambda *_:self.fail(
                        "invalid input reached network")).preview(board,job,hn)

    def test_bad_job_payload_and_provider_exception_rejected(self):
        variants=[
            None, [], {},
            {**SOURCE,"id":JOB+1},
            {**SOURCE,"id":True},
            {**SOURCE,"title":False},
            {**SOURCE,"title":"  "},
            {**SOURCE,"location":"Remote"},
        ]
        for item in variants:
            with self.subTest(item=str(item)[:80]):
                with self.assertRaisesRegex(GreenhouseEvidenceError,
                                             "greenhouse_provider_shape_invalid"):
                    GreenhouseJobEvidence(fetcher=lambda *_ ,x=item:x).preview(BOARD,JOB)
        with self.assertRaisesRegex(GreenhouseEvidenceError,"greenhouse_provider_unavailable") as found:
            GreenhouseJobEvidence(fetcher=lambda *_: (_ for _ in ()).throw(
                RuntimeError("PRIVATE_TOKEN_DO_NOT_LEAK"))).preview(BOARD,JOB)
        self.assertNotIn("PRIVATE_TOKEN_DO_NOT_LEAK",str(found.exception))

    def test_stable_source_fingerprint_without_fetch_timestamp(self):
        adapter=GreenhouseJobEvidence(fetcher=lambda *_:dict(SOURCE))
        a=adapter.preview(BOARD,JOB)
        b=adapter.preview(BOARD,JOB)
        self.assertEqual(a["source_sha256"],b["source_sha256"])
        self.assertEqual(a["first_published_utc"],"2026-10-01T12:00:00+00:00")
        self.assertEqual(a["updated_at_utc"],"2026-10-03T12:00:00+00:00")

    def test_fixed_https_host_exact_path_and_no_applicant_data(self):
        events=[]
        class Response:
            status=200
            def getheader(self,key,default=""):
                return "application/json" if key=="Content-Type" else default
            def read(self,limit):
                events.append(("read",limit))
                return json.dumps(SOURCE).encode()[:limit]
        class HTTPS:
            def __init__(self,host,timeout,context):
                events.append(("connect",host,timeout,bool(context)))
            def request(self,method,path,headers):
                events.append(("request",method,path,headers))
            def getresponse(self):return Response()
            def close(self):events.append(("close",))
        with patch("local_greenhouse_job_evidence.http.client.HTTPSConnection",HTTPS):
            out=_get_public_job(BOARD,JOB)
        self.assertEqual(JOB,out["id"])
        self.assertEqual("boards-api.greenhouse.io",events[0][1])
        self.assertEqual("GET",events[1][1])
        self.assertEqual("/v1/boards/examplecorp/jobs/1234567",events[1][2])
        self.assertNotIn("Authorization",events[1][3])
        self.assertEqual(("read",MAX_BYTES+1),events[2])

    def test_404_410_redirect_and_oversized_content_block(self):
        class FailResponse:
            def __init__(self,code,payload=b"{}",ctype="application/json"):
                self.status=code;self.payload=payload;self.ctype=ctype
            def getheader(self,key,default=""):
                return self.ctype if key=="Content-Type" else default
            def read(self,n):return self.payload[:n]
        class HTTPS:
            response=FailResponse(404)
            def __init__(self,*args,**kwargs):pass
            def request(self,*args,**kwargs):pass
            def getresponse(self):return self.response
            def close(self):pass
        with patch("local_greenhouse_job_evidence.http.client.HTTPSConnection",HTTPS):
            for code in [404,410]:
                HTTPS.response=FailResponse(code)
                with self.assertRaisesRegex(GreenhouseEvidenceError,
                                            "greenhouse_job_not_published_or_not_found"):
                    _get_public_job(BOARD,JOB)
            for code in [301,302,403,429,503]:
                HTTPS.response=FailResponse(code)
                with self.assertRaisesRegex(GreenhouseEvidenceError,
                                            "greenhouse_provider_unavailable"):
                    _get_public_job(BOARD,JOB)
            HTTPS.response=FailResponse(200,b"x"*(MAX_BYTES+1))
            with self.assertRaisesRegex(GreenhouseEvidenceError,
                                        "greenhouse_response_size_rejected"):
                _get_public_job(BOARD,JOB)
            HTTPS.response=FailResponse(200,b"{}",ctype="text/html")
            with self.assertRaisesRegex(GreenhouseEvidenceError,
                                        "greenhouse_provider_shape_invalid"):
                _get_public_job(BOARD,JOB)

    def test_registry_never_model_or_batch_executes_ats(self):
        entries=registered_tools(planning=True,greenhouse=True)
        entry=next(x for x in entries if x["name"]==GREENHOUSE_EVIDENCE)
        self.assertEqual(13,len(entries))
        self.assertTrue(entry["enabled"])
        self.assertEqual("PUBLIC_READ",entry["effect"])
        self.assertFalse(entry["model_plannable"])
        self.assertFalse(entry["batch_allowed"])
        self.assertTrue(entry["owner_confirmation_required"])
        self.assertNotIn(GREENHOUSE_EVIDENCE,BATCH_READ_TOOLS)
        self.assertEqual(GREENHOUSE_EVIDENCE,require_discovery_preview(
            GREENHOUSE_EVIDENCE,enabled=True))
        with self.assertRaisesRegex(ToolPermissionError,"hn_discovery_permission_denied"):
            require_discovery_preview(GREENHOUSE_EVIDENCE,enabled=False)
        with self.assertRaisesRegex(PlanError,"tool_not_allowed"):
            validate_steps([{"tool":GREENHOUSE_EVIDENCE,
                             "args":{"board_token":BOARD,"job_id":JOB}}])


class GreenhouseHTTPTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home=Path(temp.name)
        self.store=LocalAgentPlanStore(self.home/"plans.sqlite3")
        self.addCleanup(self.store.close)
        self.audit=LocalSecurityAudit(self.home/"audit.sqlite3")
        self.addCleanup(self.audit.close)
        self.auth=LocalOperatorAuth.ephemeral_for_tests(
            "fixture-only-local-ats-password-12345")
        self.calls=0
        def fetch(board,job):
            self.calls+=1
            return dict(SOURCE)
        self.evidence=GreenhouseJobEvidence(fetcher=fetch)
        self.server=ThreadingHTTPServer(("127.0.0.1",0),create_handler(
            operator_auth=self.auth,security_audit=self.audit,
            local_agent_runner=lambda *_:None,
            local_agent_planner=lambda *_args,**_kwargs:None,
            local_agent_plan_store=self.store,
            greenhouse_evidence=self.evidence))
        thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(thread.join,4)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        _,session=self.auth.login("fixture-only-local-ats-password-12345")
        self.cookie="aas_session="+session["cookie_token"]
        self.csrf=session["csrf_token"]

    def request(self,path,body=None,method="POST",auth=True,csrf=True,origin=None):
        conn=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=12)
        headers={}
        if auth:headers["Cookie"]=self.cookie
        if csrf:headers["X-AAS-CSRF"]=self.csrf
        if origin is not None:headers["Origin"]=origin
        if method=="POST":
            headers["Content-Type"]="application/json"
            payload=json.dumps(body).encode()
        else:payload=None
        conn.request(method,path,payload,headers)
        response=conn.getresponse()
        value=response.status,json.loads(response.read())
        conn.close()
        return value

    def test_exact_http_auth_csrf_origin_one_get_audit_without_grant(self):
        route="/api/local-greenhouse-job-preview"
        body={"board_token":BOARD,"job_id":JOB}
        self.assertEqual(401,self.request(route,body,auth=False)[0])
        self.assertEqual(403,self.request(route,body,csrf=False)[0])
        self.assertEqual(403,self.request(route,body,origin="https://evil.invalid")[0])
        self.assertEqual(422,self.request(route,{**body,"url":"https://evil"})[0])
        self.assertEqual(422,self.request(route,{**body,"content":"write"})[0])
        self.assertEqual(422,self.request(route,{"board_token":"../x","job_id":JOB})[0])
        self.assertEqual(0,self.calls)
        status,data=self.request(route,{**body,"hn_story_id":HN_ID})
        self.assertEqual(200,status)
        self.assertEqual(1,self.calls)
        self.assertTrue(data["public_job_post_observed"])
        self.assertFalse(data["indonesia_eligibility_verified"])
        self.assertEqual(HN_ID,data["hn_story_id_supplied_by_owner"])
        self.assertFalse(data["hn_to_ats_match_verified"])
        self.assertEqual(0,self.store.db.execute(
            "SELECT COUNT(*) FROM agent_task_plans").fetchone()[0])
        self.assertEqual(0,len(list(self.home.glob("*.json"))))
        records=[x for x in self.audit.recent(10)
                 if x["action"]=="GREENHOUSE_JOB_CHECK"]
        self.assertEqual(["SUCCESS","ATTEMPT"],[x["outcome"] for x in records])
        self.assertTrue(all(x["source_fingerprint"]=="" for x in records))
        status,caps=self.request("/api/local-agent-capabilities",method="GET")
        self.assertEqual(200,status)
        self.assertTrue(caps["greenhouse_evidence"])

    def test_disabled_gate_rejects_without_provider_call(self):
        disabled=ThreadingHTTPServer(("127.0.0.1",0),create_handler(
            operator_auth=self.auth,security_audit=self.audit,
            local_agent_runner=lambda *_:None,
            local_agent_planner=lambda *_a,**_kw:None,
            local_agent_plan_store=self.store))
        worker=threading.Thread(target=disabled.serve_forever,daemon=True)
        worker.start()
        try:
            con=http.client.HTTPConnection("127.0.0.1",disabled.server_port,timeout=8)
            con.request("POST","/api/local-greenhouse-job-preview",
                        json.dumps({"board_token":BOARD,"job_id":JOB}).encode(),
                        {"Cookie":self.cookie,"X-AAS-CSRF":self.csrf,
                         "Content-Type":"application/json"})
            resp=con.getresponse()
            self.assertEqual(403,resp.status)
            self.assertEqual("greenhouse_evidence_disabled",
                             json.loads(resp.read())["error"])
            con.close()
            self.assertEqual(0,self.calls)
        finally:
            disabled.shutdown();disabled.server_close();worker.join(4)


if __name__=="__main__":
    unittest.main()
