"""Stage 121: real Stage 114-117 encrypted ledgers, synthetic Gmail provider.

Exercises cross-ledger signed Gmail identity, no remote write, authentic owner
HTTP and replay safety. Does not contact n8n/Gmail in a real owner account.
"""
import hashlib
import hmac
import http.cookiejar
import importlib.util
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, HTTPCookieProcessor, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"app"))
sys.path.insert(0, str(ROOT/"tests"))
sys.path.insert(0, str(ROOT/"tools"))

import test_n8n_execution_verifier as stage117
from local_gmail_sent_evidence import GmailSentEvidence, GmailSentEvidenceError
from local_signed_gmail_artifact import (
    SignedGmailArtifactBinding, SignedGmailArtifactError, signing_message)
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_workflow_core import LocalWorkflowCore
from local_server import create_handler

PLAN = stage117.PLAN
GMAIL_ID = "gmail_message_stage_121"
RECIPIENT = "owner-test@example.org"
SUBJECT = "Synthetic confirmation event"


class StubGmail:
    def __init__(self):
        self.calls=[]
    def sent_message_metadata(self, mid):
        self.calls.append(mid)
        return {"id":mid,"sent":True,
                "to":"Owner Test <"+RECIPIENT+">","subject":SUBJECT}


HAS_CRYPTO = importlib.util.find_spec('cryptography') is not None


@unittest.skipUnless(HAS_CRYPTO, 'Dedicated Stage 121 CI installs optional cryptography')
class Stage121SignedBindingTests(unittest.TestCase):
    def setUp(self):
        self.previous=stage117.Stage117VerifierTests()
        self.previous.setUp()
        self.assertEqual(
            self.previous.verifier.verify_one({"plan_id":PLAN})["status"],
            "EXECUTION_SUCCESS_CORRELATED")
        self.binding=SignedGmailArtifactBinding(
            self.previous.verifier,self.previous.manager)
        self.gmail=StubGmail()
        self.evidence=GmailSentEvidence(self.previous.verifier,self.gmail,
                              signed_artifact_binding=self.binding)
        self.db=self.previous.handoff.db

    def tearDown(self):
        self.previous.tearDown()

    def claim(self, **overrides):
        row=self.db.execute("""SELECT plan_id,connector_id,workflow_id,
               event_id,delivery_id,execution_id FROM signed_manual_handoff_outcomes
               WHERE plan_id=?""",(PLAN,)).fetchone()
        fields=("plan_id","connector_id","workflow_id","event_id",
                "delivery_id","execution_id")
        data=dict(zip(fields,row))
        data.update({"gmail_id_sha256":hashlib.sha256(GMAIL_ID.encode()).hexdigest(),
                     "recipient_sha256":hashlib.sha256(RECIPIENT.encode()).hexdigest(),
                     "subject_sha256":hashlib.sha256(SUBJECT.encode()).hexdigest()})
        data.update(overrides)
        msg=signing_message(*(data[k] for k in (
            "plan_id","connector_id","workflow_id","event_id",
            "delivery_id","execution_id","gmail_id_sha256",
            "recipient_sha256","subject_sha256")))
        data["proof_hmac_sha256"]=hmac.new(
            stage117.SIGN_KEY.encode(),msg.encode("ascii"),
            hashlib.sha256).hexdigest()
        return data

    def verify(self, **kw):
        values={"plan_id":PLAN,"gmail_message_id":GMAIL_ID,
                "expected_to":RECIPIENT,"expected_subject":SUBJECT}
        values.update(kw)
        return self.evidence.verify_one(values)

    def test_unsigned_blocked_before_gmail_token_or_get(self):
        with self.assertRaisesRegex(GmailSentEvidenceError,"signed_gmail_binding_required"):
            self.verify()
        self.assertEqual(self.gmail.calls,[])
        self.assertEqual(self.evidence.history()["records"],[])

    def test_signed_and_provider_correlated_then_independent_gmail_match(self):
        claim=self.claim()
        record=self.binding.import_claim(claim)
        self.assertEqual(record["result"],"RECORDED")
        self.assertTrue(record["signature_valid_at_import"])
        result=self.verify()
        self.assertEqual(result["status"],"SENT_ARTIFACT_MATCHED")
        self.assertEqual(self.gmail.calls,[GMAIL_ID])
        self.assertFalse(result["business_effect_verified"])
        self.assertFalse(result["causal_link_to_n8n_verified"])
        self.assertFalse(result["recipient_delivery_verified"])
        for raw in (GMAIL_ID,RECIPIENT,SUBJECT,stage117.SIGN_KEY):
            self.assertNotIn(raw,json.dumps(self.binding.history()))
            self.assertNotIn(raw,json.dumps(self.evidence.history()))

    def test_owner_cannot_swap_message_recipient_or_subject(self):
        for key, value in [
            ("gmail_message_id","otherid"),
            ("expected_to","other@example.org"),
            ("expected_subject","Other subject")]:
            self.binding.import_claim(self.claim())
            with self.subTest(key=key), self.assertRaisesRegex(
                    GmailSentEvidenceError,"signed_gmail_binding_mismatch"):
                self.verify(**{key:value})
        self.assertEqual(self.gmail.calls,[])
        self.assertEqual(self.evidence.history()["records"],[])

    def test_forged_signature_rejected_before_gmail(self):
        data=self.claim()
        data["proof_hmac_sha256"]="0"*64
        with self.assertRaisesRegex(SignedGmailArtifactError,
                                    "gmail_binding_signature_invalid"):
            self.binding.import_claim(data)
        self.assertEqual(self.binding.history()["records"],[])
        self.assertEqual(self.gmail.calls,[])

    def test_swapped_delivery_and_execution_are_rejected(self):
        for key,value in [("delivery_id","f"*32),
                          ("execution_id","999"),
                          ("connector_id","another")]:
            with self.subTest(key=key), self.assertRaisesRegex(
                    SignedGmailArtifactError,"gmail_binding_chain_mismatch"):
                self.binding.import_claim(self.claim(**{key:value}))
        self.assertEqual(self.binding.history()["records"],[])

    def test_existing_provider_status_tampering_blocks(self):
        self.db.execute("""UPDATE n8n_provider_execution_checks
                           SET status='DELIVERY_NOT_CORRELATED' WHERE plan_id=?""",
                        (PLAN,))
        with self.assertRaisesRegex(SignedGmailArtifactError,"verified_n8n_chain_required"):
            self.binding.import_claim(self.claim())
        self.assertEqual(self.gmail.calls,[])

    def test_n8n_provider_pin_and_success_metadata_swaps_block_import(self):
        values=[("provider_workflow_id","different-workflow"),
                ("pinned_node_sha256","0"*64),
                ("provider_hostname","attacker.example.org"),
                ("provider_execution_status","error")]
        for field, tampered in values:
            original=self.db.execute(
                "SELECT "+field+" FROM n8n_provider_execution_checks WHERE plan_id=?",
                (PLAN,)).fetchone()[0]
            self.db.execute(
                "UPDATE n8n_provider_execution_checks SET "+field+"=? WHERE plan_id=?",
                (tampered,PLAN))
            with self.subTest(field=field), self.assertRaisesRegex(
                    SignedGmailArtifactError,"verified_n8n_chain_required"):
                self.binding.import_claim(self.claim())
            self.db.execute(
                "UPDATE n8n_provider_execution_checks SET "+field+"=? WHERE plan_id=?",
                (original,PLAN))
        self.assertEqual(self.binding.history()["records"],[])
        self.assertEqual(self.gmail.calls,[])

    def test_signature_scope_domain_and_immutable_reimport(self):
        data=self.claim()
        self.assertEqual(self.binding.import_claim(data)["result"],"RECORDED")
        self.assertEqual(self.binding.import_claim(data)["result"],"ALREADY_RECORDED")
        altered=self.claim(gmail_id_sha256="0"*64)
        with self.assertRaisesRegex(SignedGmailArtifactError,"gmail_binding_conflict"):
            self.binding.import_claim(altered)
        self.assertEqual(len(self.binding.history()["records"]),1)

    def test_bound_import_never_calls_gmail_then_one_shot_gmail(self):
        self.binding.import_claim(self.claim())
        self.assertEqual(self.gmail.calls,[])
        self.assertEqual(self.verify()["status"],"SENT_ARTIFACT_MATCHED")
        with self.assertRaisesRegex(GmailSentEvidenceError,"already_checked"):
            self.verify()
        self.assertEqual(self.gmail.calls,[GMAIL_ID])

    def test_legacy_unsigned_gmail_check_cannot_be_retroactively_signed(self):
        legacy=GmailSentEvidence(self.previous.verifier,self.gmail)
        result=legacy.verify_one({
            "plan_id":PLAN, "gmail_message_id":GMAIL_ID,
            "expected_to":RECIPIENT, "expected_subject":SUBJECT})
        self.assertEqual(result["status"],"SENT_ARTIFACT_MATCHED")
        with self.assertRaisesRegex(SignedGmailArtifactError,
                                    "prior_gmail_check_cannot_be_retroactively_bound"):
            self.binding.import_claim(self.claim())
        self.assertEqual(self.binding.history()["records"],[])

    def test_sqlite_reopen_preserves_bound_claim(self):
        self.binding.import_claim(self.claim())
        again=SignedGmailArtifactBinding(self.previous.verifier,self.previous.manager)
        self.assertTrue(again.require_match(PLAN,GMAIL_ID,RECIPIENT,SUBJECT))
        self.assertEqual(len(again.history()["records"]),1)

    def test_owner_http_enforces_auth_csrf_password_consent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            password="Stage121-Owner-Only-HMAC-2026!"
            auth=LocalOperatorAuth.provision(root/"operator.json",password)
            if auth is None:
                auth=LocalOperatorAuth.load(root/"operator.json")
            audit=LocalSecurityAudit(root/"audit.sqlite3")
            core=LocalWorkflowCore(root/"core.sqlite3")
            server=ThreadingHTTPServer(("127.0.0.1",0),
                create_handler(local_core=core, operator_auth=auth,
                   security_audit=audit,
                   signed_gmail_binding=self.binding,
                   gmail_sent_evidence=self.evidence))
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            origin="http://127.0.0.1:"+str(server.server_port)
            opener=build_opener(ProxyHandler({}),
                       HTTPCookieProcessor(http.cookiejar.CookieJar()))
            def post(path,body,csrf=None):
                headers={"Content-Type":"application/json"}
                if csrf is not None: headers["X-AAS-CSRF"]=csrf
                req=Request(origin+path,json.dumps(body).encode(),
                            headers=headers,method="POST")
                with opener.open(req,timeout=5) as response:
                    return json.load(response)
            claim=self.claim()
            body={**claim,"current_password":password,
                  "consent":"IMPORT_SIGNED_GMAIL_ARTIFACT_ONCE"}
            try:
                with self.assertRaises(HTTPError) as no_login:
                    opener.open(origin+"/api/signed-gmail-artifact-bindings")
                self.assertEqual(no_login.exception.code,401)
                csrf=post("/api/operator-login",{"password":password})["csrf_token"]
                with self.assertRaises(HTTPError) as no_csrf:
                    post("/api/signed-gmail-artifact-import",body)
                self.assertEqual(no_csrf.exception.code,403)
                with self.assertRaises(HTTPError) as no_consent:
                    post("/api/signed-gmail-artifact-import",
                         {**body,"consent":"YES"},csrf)
                self.assertEqual(no_consent.exception.code,422)
                with self.assertRaises(HTTPError) as wrong_pwd:
                    post("/api/signed-gmail-artifact-import",
                         {**body,"current_password":"wrong"},csrf)
                self.assertEqual(wrong_pwd.exception.code,401)
                self.assertEqual(self.binding.history()["records"],[])
                result=post("/api/signed-gmail-artifact-import",body,csrf)
                self.assertEqual(result["result"],"RECORDED")
                with opener.open(origin+"/api/signed-gmail-artifact-bindings") as response:
                    history=json.load(response)
                self.assertEqual(len(history["records"]),1)
                self.assertEqual(self.gmail.calls,[])
                self.assertTrue(any(event["action"]=="SIGNED_GMAIL_ARTIFACT_IMPORT"
                                    and event["outcome"]=="SUCCESS"
                                    for event in audit.recent()))
                for secret in (GMAIL_ID,RECIPIENT,SUBJECT,stage117.SIGN_KEY,password):
                    self.assertNotIn(secret,json.dumps(history))
                    self.assertNotIn(secret,json.dumps(audit.recent()))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                core.close()
                audit.close()


class OwnerSignedGmailImportCLITests(unittest.TestCase):
    def setUp(self):
        from owner_signed_gmail_import import SignedGmailImportClient
        self.client=object.__new__(SignedGmailImportClient)
        self.calls=[]
        self.client._login=lambda password:self.calls.append(("login",None))
        self.client._logout=lambda:self.calls.append(("logout",None))
        self.stored=False
        def request(path, values=None):
            self.calls.append((path,values))
            if path=="/api/n8n-execution-check-history":
                return {"records":[{"plan_id":PLAN,"status":"EXECUTION_SUCCESS_CORRELATED",
                                    "inbound_delivery_correlated":True}]}
            if path=="/api/signed-gmail-artifact-bindings":
                return {"records":[{"plan_id":PLAN}] if self.stored else []}
            if path=="/api/signed-gmail-artifact-import":
                return {"result":"RECORDED","plan_id":PLAN,
                        "signature_valid_at_import":True,
                        "business_effect_verified":False,
                        "external_write_count":0}
            raise AssertionError("unexpected endpoint")
        self.client._request=request

    def fixture(self):
        data={
            "plan_id":PLAN, "connector_id":"n8n_manual",
            "workflow_id":"receipt_flow", "event_id":"receipt_event_001",
            "delivery_id":"d"*32, "execution_id":"12345",
            "gmail_id_sha256":"a"*64, "recipient_sha256":"b"*64,
            "subject_sha256":"c"*64, "proof_hmac_sha256":"e"*64,
        }
        return json.dumps(data)

    def test_inspect_only_reads_existing_local_histories(self):
        report=self.client.execute(mode="inspect",plan_id=PLAN,password="p")
        self.assertEqual(report["status"],"READY_FOR_OWNER_SIGNED_GMAIL_IMPORT")
        self.assertFalse(any(path=="/api/signed-gmail-artifact-import"
                             for path,_ in self.calls))

    def test_import_requires_typed_confirmation_before_post(self):
        from owner_live_n8n_acceptance import OwnerLiveAcceptanceError
        with self.assertRaises(OwnerLiveAcceptanceError):
            self.client.execute(mode="import",plan_id=PLAN,password="p",
                confirmation="YES",claim_json=self.fixture())
        self.assertFalse(any(path=="/api/signed-gmail-artifact-import"
                             for path,_ in self.calls))

    def test_import_is_exactly_one_local_post_and_no_raw_report(self):
        result=self.client.execute(
            mode="import",plan_id=PLAN,password="p",
            confirmation="IMPORT_SIGNED_GMAIL_ARTIFACT_ONCE",
            claim_json=self.fixture())
        self.assertEqual(result["status"],"SIGNED_GMAIL_ARTIFACT_BOUND")
        self.assertFalse(result["n8n_causation_verified"])
        self.assertEqual(sum(path=="/api/signed-gmail-artifact-import"
                             for path,_ in self.calls),1)
        self.assertNotIn("n8n_manual",json.dumps(result))

    def test_import_lost_local_ack_is_uncertain_no_repeat(self):
        from owner_live_n8n_acceptance import OwnerLiveAcceptanceError
        original=self.client._request
        def fail(path, values=None):
            if path=="/api/signed-gmail-artifact-import":
                self.calls.append((path,values))
                raise OwnerLiveAcceptanceError("response_missing")
            return original(path,values)
        self.client._request=fail
        result=self.client.execute(
            mode="import",plan_id=PLAN,password="p",
            confirmation="IMPORT_SIGNED_GMAIL_ARTIFACT_ONCE",
            claim_json=self.fixture())
        self.assertEqual(result["status"],
                         "LOCAL_IMPORT_RESPONSE_UNCERTAIN_CHECK_HISTORY_NO_RETRY")
        self.assertEqual(sum(path=="/api/signed-gmail-artifact-import"
                             for path,_ in self.calls),1)

    def test_import_rejects_extra_fields_before_post(self):
        from owner_live_n8n_acceptance import OwnerLiveAcceptanceError
        data=json.loads(self.fixture());data["secret"]="hidden"
        with self.assertRaisesRegex(OwnerLiveAcceptanceError,
                                    "invalid_signed_claim_fields"):
            self.client.execute(
                mode="import",plan_id=PLAN,password="p",
                confirmation="IMPORT_SIGNED_GMAIL_ARTIFACT_ONCE",
                claim_json=json.dumps(data))
        self.assertFalse(any(path=="/api/signed-gmail-artifact-import"
                             for path,_ in self.calls))


class StrictGmailSentCLIPreflightTests(unittest.TestCase):
    def test_unsigned_owner_preflight_blocks_before_gmail(self):
        from owner_gmail_sent_acceptance import OwnerGmailSentClient
        from owner_live_n8n_acceptance import OwnerLiveAcceptanceError
        client=object.__new__(OwnerGmailSentClient)
        observed=[]
        client._login=lambda p:None
        client._logout=lambda:None
        def request(path,values=None):
            observed.append(path)
            if path=="/api/n8n-execution-check-history":
                return {"records":[{"plan_id":PLAN,"status":"EXECUTION_SUCCESS_CORRELATED",
                                    "inbound_delivery_correlated":True}]}
            if path=="/api/local-agent-capabilities":
                return {"gmail_sent_artifact_verification":True,
                        "signed_gmail_artifact_binding_required":True}
            if path=="/api/signed-gmail-artifact-bindings":
                return {"records":[]}
            raise AssertionError("should not request "+path)
        client._request=request
        with self.assertRaisesRegex(OwnerLiveAcceptanceError,
                                    "signed_gmail_binding_preflight_required"):
            client.execute(mode="inspect",plan_id=PLAN,password="p")
        self.assertNotIn("/api/gmail-sent-evidence",observed)

    def test_signed_owner_preflight_allows_exact_plan_only(self):
        from owner_gmail_sent_acceptance import OwnerGmailSentClient
        client=object.__new__(OwnerGmailSentClient)
        observed=[]
        client._login=lambda p:None
        client._logout=lambda:None
        def request(path,values=None):
            observed.append(path)
            if path=="/api/n8n-execution-check-history":
                return {"records":[{"plan_id":PLAN,"status":"EXECUTION_SUCCESS_CORRELATED",
                                    "inbound_delivery_correlated":True}]}
            if path=="/api/local-agent-capabilities":
                return {"gmail_sent_artifact_verification":True,
                        "signed_gmail_artifact_binding_required":True}
            if path=="/api/signed-gmail-artifact-bindings":
                return {"records":[{"plan_id":PLAN}]}
            if path=="/api/gmail-sent-evidence-history":
                return {"records":[]}
            raise AssertionError(path)
        client._request=request
        result=client.execute(mode="inspect",plan_id=PLAN,password="p")
        self.assertEqual(result["status"],"READY_FOR_ONE_GMAIL_SENT_METADATA_READ")
        self.assertNotIn("/api/gmail-sent-evidence",observed)


if __name__ == "__main__":
    unittest.main()
