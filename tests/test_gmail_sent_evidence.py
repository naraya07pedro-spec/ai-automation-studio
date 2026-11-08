"""Stage 120: no real email is sent/read. All responses are synthetic."""
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
import sys
import threading
import tempfile
import http.cookiejar
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, HTTPCookieProcessor, build_opener
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from local_gmail_sent_evidence import GmailSentEvidence, GmailSentEvidenceError
from local_google_gmail import GoogleGmailMetadata, GmailConnectorError, _official_json

PLAN = "a" * 24
TO = "client@example.org"
SUBJECT = "Appointment confirmation"
MID = "email_12345"


class SyntheticGmail:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.result = {"id": MID, "sent": True,
                       "to": "Client <client@example.org>",
                       "subject": SUBJECT}

    def sent_message_metadata(self, mid):
        self.calls.append(mid)
        if self.fail:
            raise GmailConnectorError("google_gmail_request_failed")
        return self.result


class GmailSentEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:",check_same_thread=False)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("""CREATE TABLE agent_connector_handoffs(
            plan_id TEXT PRIMARY KEY)""")
        self.db.execute("INSERT INTO agent_connector_handoffs VALUES(?)",(PLAN,))
        self.db.execute("""CREATE TABLE signed_manual_handoff_outcomes(
            plan_id TEXT PRIMARY KEY, delivery_id TEXT, execution_id TEXT,
            claimed_outcome TEXT)""")
        self.db.execute("""CREATE TABLE n8n_provider_execution_checks(
            plan_id TEXT PRIMARY KEY, delivery_id TEXT, execution_id TEXT,
            status TEXT, provider_finished INTEGER,
            inbound_delivery_correlated INTEGER)""")
        self.db.execute("INSERT INTO signed_manual_handoff_outcomes VALUES(?,?,?,?)",
                        (PLAN,"d"*32,"12345","COMPLETED"))
        self.db.execute("INSERT INTO n8n_provider_execution_checks VALUES(?,?,?,?,?,?)",
                        (PLAN,"d"*32,"12345","EXECUTION_SUCCESS_CORRELATED",1,1))
        self.db.commit()
        self.gmail = SyntheticGmail()
        self.handoff = SimpleNamespace(db=self.db,lock=threading.RLock())
        self.verifier = SimpleNamespace(handoff=self.handoff)
        self.service = GmailSentEvidence(self.verifier,self.gmail)

    def tearDown(self):
        self.db.close()

    def request(self, **overrides):
        data = {"plan_id":PLAN, "gmail_message_id":MID,
                "expected_to":TO, "expected_subject":SUBJECT}
        data.update(overrides)
        return data

    def test_one_realistic_matched_artifact_and_no_raw_values(self):
        result=self.service.verify_one(self.request())
        self.assertEqual(result["status"],"SENT_ARTIFACT_MATCHED")
        self.assertTrue(result["gmail_sent_artifact_verified"])
        self.assertFalse(result["causal_link_to_n8n_verified"])
        self.assertFalse(result["recipient_delivery_verified"])
        self.assertFalse(result["business_effect_verified"])
        self.assertEqual(self.gmail.calls,[MID])
        for text in (TO,SUBJECT,MID):
            self.assertNotIn(text,json.dumps(result))
            self.assertNotIn(text,json.dumps(self.service.history()))
        self.assertEqual(self.service.history()["records"][0]["plan_id"],PLAN)

    def test_replay_is_rejected_without_second_network_call(self):
        self.service.verify_one(self.request())
        with self.assertRaisesRegex(GmailSentEvidenceError,"already_checked"):
            self.service.verify_one(self.request())
        self.assertEqual(self.gmail.calls,[MID])

    def test_wrong_execution_claim_blocks_all_remote_reads(self):
        self.db.execute("UPDATE n8n_provider_execution_checks SET execution_id='999' ")
        with self.assertRaisesRegex(GmailSentEvidenceError,"verified_n8n_success_chain_required"):
            self.service.verify_one(self.request())
        self.assertEqual(self.gmail.calls,[])

    def test_n8n_failed_or_not_finished_blocks(self):
        for sql in [
            "UPDATE n8n_provider_execution_checks SET status='EXECUTION_FAILED_CORRELATED'",
            "UPDATE n8n_provider_execution_checks SET status='EXECUTION_SUCCESS_CORRELATED', provider_finished=0",
            "UPDATE n8n_provider_execution_checks SET provider_finished=1, inbound_delivery_correlated=0",
        ]:
            self.db.execute(sql)
            with self.assertRaises(GmailSentEvidenceError):
                self.service.verify_one(self.request())
        self.assertEqual(self.gmail.calls,[])

    def test_claimed_failed_outcome_blocks(self):
        self.db.execute("UPDATE signed_manual_handoff_outcomes SET claimed_outcome='FAILED'")
        with self.assertRaises(GmailSentEvidenceError):
            self.service.verify_one(self.request())
        self.assertEqual(self.gmail.calls,[])

    def test_subject_mismatch_is_not_a_success(self):
        self.gmail.result["subject"]="Unrelated subject"
        result=self.service.verify_one(self.request())
        self.assertEqual(result["status"],"NOT_CORRELATED")
        self.assertFalse(result["gmail_sent_artifact_verified"])

    def test_recipient_mismatch_is_not_a_success(self):
        self.gmail.result["to"]="other@example.org"
        result=self.service.verify_one(self.request())
        self.assertEqual(result["status"],"NOT_CORRELATED")

    def test_sent_label_missing_is_not_a_success(self):
        self.gmail.result["sent"]=False
        result=self.service.verify_one(self.request())
        self.assertEqual(result["status"],"NOT_CORRELATED")
        self.assertFalse(result["gmail_sent_label_seen"])

    def test_provider_error_quarantines_and_no_retry(self):
        self.gmail.fail=True
        result=self.service.verify_one(self.request())
        self.assertEqual(result["status"],"LOOKUP_UNCERTAIN")
        with self.assertRaises(GmailSentEvidenceError):
            self.service.verify_one(self.request())
        self.assertEqual(self.gmail.calls,[MID])

    def test_restart_quarantines_in_flight(self):
        self.db.execute("INSERT INTO agent_connector_handoffs VALUES(?)",("b"*24,))
        self.db.execute("INSERT INTO gmail_sent_evidence(plan_id,gmail_message_sha256,"
                        "expected_to_sha256,expected_subject_sha256,n8n_execution_sha256,"
                        "status,claimed_at_utc) VALUES(?,?,?,?,?,?,?)",
                        ("b"*24,"a"*64,"b"*64,"c"*64,"d"*64,"IN_FLIGHT",
                         "2026-11-08T00:00:00+00:00"))
        self.db.commit()
        again=GmailSentEvidence(self.verifier,self.gmail)
        rows=again.history()["records"]
        self.assertEqual([x for x in rows if x["plan_id"]=="b"*24][0]["status"],
                         "LOOKUP_UNCERTAIN")

    def test_invalid_message_id_and_recipient_do_not_claim(self):
        for change in [{"gmail_message_id":"../token"},
                       {"expected_to":"foo\nBar@example.org"},
                       {"expected_subject":"abc\rdef"},
                       {"plan_id":"fake"}]:
            with self.assertRaises(GmailSentEvidenceError):
                self.service.verify_one(self.request(**change))
        self.assertEqual(self.db.execute("SELECT count(*) FROM gmail_sent_evidence").fetchone()[0],0)
        self.assertEqual(self.gmail.calls,[])

    def test_duplicate_header_addresses_not_accepted(self):
        self.gmail.result["to"]="a@example.org, client@example.org"
        self.assertEqual(self.service.verify_one(self.request())["status"],"NOT_CORRELATED")

    def test_real_loopback_http_auth_csrf_password_consent_and_replay(self):
        import local_server
        from local_operator_auth import LocalOperatorAuth
        from local_security_audit import LocalSecurityAudit
        from local_workflow_core import LocalWorkflowCore

        with tempfile.TemporaryDirectory(prefix="stage120-http-") as td:
            base=Path(td)
            password="StudioStage120Owner-Passphrase-2026!"
            operator=LocalOperatorAuth.provision(base/"operator.json",password)
            if operator is None:
                operator=LocalOperatorAuth.load(base/"operator.json")
            audit=LocalSecurityAudit(base/"audit.sqlite3")
            core=LocalWorkflowCore(base/"core.sqlite3")
            server=ThreadingHTTPServer(("127.0.0.1",0),
                local_server.create_handler(local_core=core,
                    operator_auth=operator,security_audit=audit,
                    gmail_sent_evidence=self.service))
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            url="http://127.0.0.1:"+str(server.server_port)
            opener=build_opener(ProxyHandler({}),
                HTTPCookieProcessor(http.cookiejar.CookieJar()))

            def post(path,body,csrf=None):
                h={"Content-Type":"application/json"}
                if csrf is not None:
                    h["X-AAS-CSRF"]=csrf
                req=Request(url+path,json.dumps(body).encode(),headers=h,method="POST")
                with opener.open(req,timeout=5) as response:
                    return json.load(response)

            body={**self.request(),"current_password":password,
                  "consent":"VERIFY_GMAIL_SENT_ARTIFACT_ONCE"}
            try:
                with self.assertRaises(HTTPError) as unauth:
                    opener.open(url+"/api/gmail-sent-evidence-history",timeout=5)
                self.assertEqual(unauth.exception.code,401)
                csrf=post("/api/operator-login",{"password":password})["csrf_token"]
                with self.assertRaises(HTTPError) as missing_csrf:
                    post("/api/gmail-sent-evidence",body)
                self.assertEqual(missing_csrf.exception.code,403)
                with self.assertRaises(HTTPError) as wrong_consent:
                    post("/api/gmail-sent-evidence",{**body,"consent":"YES"},csrf)
                self.assertEqual(wrong_consent.exception.code,422)
                with self.assertRaises(HTTPError) as wrong_password:
                    post("/api/gmail-sent-evidence",
                         {**body,"current_password":"wrong"},csrf)
                self.assertEqual(wrong_password.exception.code,401)
                self.assertEqual(self.gmail.calls,[])
                result=post("/api/gmail-sent-evidence",body,csrf)
                self.assertEqual(result["status"],"SENT_ARTIFACT_MATCHED")
                self.assertFalse(result["business_effect_verified"])
                self.assertEqual(self.gmail.calls,[MID])
                with self.assertRaises(HTTPError) as repeat:
                    post("/api/gmail-sent-evidence",body,csrf)
                self.assertEqual(repeat.exception.code,409)
                with opener.open(url+"/api/gmail-sent-evidence-history",
                                 timeout=5) as response:
                    history=json.load(response)
                self.assertEqual(history["records"][0]["status"],
                                 "SENT_ARTIFACT_MATCHED")
                logged=audit.recent()
                self.assertTrue(any(x["action"]=="GMAIL_SENT_EVIDENCE_VERIFY"
                                    and x["outcome"]=="SUCCESS" for x in logged))
                for forbidden in (MID,TO,SUBJECT,password):
                    self.assertNotIn(forbidden,json.dumps(history))
                    self.assertNotIn(forbidden,json.dumps(logged))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                audit.close()
                core.close()

    def test_daily_limit_checks_before_network(self):
        for i in range(5):
            self.db.execute("INSERT INTO agent_connector_handoffs VALUES(?)",(str(i)*24,))
            self.db.execute("""INSERT INTO gmail_sent_evidence
                (plan_id,gmail_message_sha256,expected_to_sha256,
                 expected_subject_sha256,n8n_execution_sha256,status,claimed_at_utc)
                 VALUES(?,?,?,?,?,?,datetime('now'))""",
                            (str(i)*24,"a"*64,"b"*64,"c"*64,"d"*64,
                             "NOT_CORRELATED"))
        self.db.commit()
        with self.assertRaisesRegex(GmailSentEvidenceError,"daily_limit"):
            self.service.verify_one(self.request())
        self.assertEqual(self.gmail.calls,[])


class FixedGmailEndpointTests(unittest.TestCase):
    def _get(self, response, mid=MID):
        obj=object.__new__(GoogleGmailMetadata)
        obj._access_token=lambda: "synthetic"
        obj._get=lambda url, access: response
        return obj.sent_message_metadata(mid)

    def test_no_body_and_sent_label_required(self):
        response={"id":MID,"labelIds":["SENT"],"payload":{"headers":[
            {"name":"To","value":TO},{"name":"Subject","value":SUBJECT}]},
            "snippet":"private content should be ignored"}
        got=self._get(response)
        self.assertEqual(got,{"id":MID,"sent":True,"to":TO,"subject":SUBJECT})
        self.assertNotIn("private content",str(got))

    def test_duplicate_subject_refused(self):
        response={"id":MID,"labelIds":["SENT"],"payload":{"headers":[
            {"name":"Subject","value":SUBJECT},{"name":"Subject","value":"different"}]}}
        with self.assertRaises(GmailConnectorError):
            self._get(response)

    def test_trash_label_does_not_count_as_sent(self):
        response={"id":MID,"labelIds":["SENT","TRASH"],"payload":{"headers":[]}}
        self.assertFalse(self._get(response)["sent"])

    def test_wrong_google_message_identity_refused(self):
        response={"id":"wrong","labelIds":["SENT"],"payload":{"headers":[]}}
        with self.assertRaises(GmailConnectorError):
            self._get(response)

    def test_path_injection_rejected_before_token(self):
        with self.assertRaisesRegex(GmailConnectorError,"message_id_invalid"):
            self._get({},mid="../access")

    def test_outbound_url_allowlist_rejects_arbitrary_host(self):
        with self.assertRaisesRegex(GmailConnectorError,"google_endpoint_not_allowed"):
            _official_json("https://example.org/gmail/v1/users/me/messages/hi")


if __name__ == "__main__":
    unittest.main()
