"""Stage #116: signed final-step outcome evidence for Stage #114 manual sends.

Uses real temporary SQLite, AES-GCM registry and a real local HTTP server.
No real provider calls, n8n traffic, webhook delivery, user files or secrets.
"""
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
from pathlib import Path
import hashlib
import hmac
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
import local_server
from local_agent_connector_handoff import AgentConnectorHandoff
from local_agent_task_plans import LocalAgentPlanStore
from local_connector_manager import LocalConnectorManager
from local_connector_vault import generate_master_key
from local_handoff_outcome_proof import (
    SignedManualHandoffOutcome, HandoffOutcomeProofError,
    PROOF_DOMAIN, signing_message)
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_workflow_core import LocalWorkflowCore

HAS_CRYPTO=importlib.util.find_spec("cryptography") is not None
PASSWORD="Stage116-Owner-Only-Private-Test-2026!"
PLAN="d"*24
CID="secure_manual"
WID="outcome_flow"
EID="outcome_event_001"
SECRET="Stage116-synthetic-HMAC-secret-32-bytes-minimum-not-real"


@unittest.skipUnless(HAS_CRYPTO,"Dedicated stage CI installs encrypted connector dependency")
class SignedManualOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name)
        self.root=root
        self.core=LocalWorkflowCore(root/"local.sqlite3")
        self.plans=LocalAgentPlanStore(root/"plans.sqlite3")
        self.plans.create({"model":"test-model","goal":"Compute 7 times 8 privately",
                           "steps":[{"tool":"calculate","args":{
                               "a":"7","b":"8","operation":"multiply"}}]},plan_id=PLAN)
        self.plans.approve(PLAN)
        self.plans.advance(PLAN)
        self.assertEqual(self.plans.get(PLAN)["status"],"COMPLETED")
        self.assertEqual(self.core.create({
            "workflow_id":WID,"display_name":"Outcome Flow",
            "service_filter":"automation","min_budget_tier":2,
            "followup_policy":"review_required"})[0],201)
        self.assertEqual(self.core.activate({
            "workflow_id":WID,"expected_version":1})[0],200)
        self.assertEqual(self.core.run({
            "workflow_id":WID,"event_id":EID,
            "service_code":"automation","budget_tier":2,
            "timeline":"soon"})[0],200)
        self.calls=[]
        def sender(url,payload,did,secret):
            self.calls.append((url,payload,did,secret))
            return 202
        self.sender=sender
        self.manager=LocalConnectorManager(
            self.core,root/"vault"/"managed.json",
            sender=sender,vault_key=generate_master_key())
        self.manager.add({"connector_id":CID,"label":"Private manual test",
                          "url":"https://hooks.n8n.cloud/webhook/stage116-fixture",
                          "signing_secret":SECRET})
        self.dbpath=root/"agent-connector-handoffs.sqlite3"
        self.handoff=AgentConnectorHandoff(self.plans,self.core,self.manager,self.dbpath)
        self.proofs=SignedManualHandoffOutcome(self.handoff,self.manager)

    def tearDown(self):
        self.handoff.close()
        self.manager.close()
        self.plans.close()
        self.core.close()
        self.tmp.cleanup()

    def send(self):
        preview=self.handoff.preview({
            "plan_id":PLAN,"connector_id":CID,
            "workflow_id":WID,"event_id":EID})
        result=self.handoff.commit({
            "plan_id":PLAN,"connector_id":CID,
            "workflow_id":WID,"event_id":EID,
            "plan_sha256":preview["plan_sha256"],
            "workflow_sha256":preview["workflow_sha256"],
            "payload_sha256":preview["payload_sha256"],
            "preview_token":preview["preview_token"]})
        self.assertEqual(result["external_write_attempts"],1)
        return result

    def signed(self,delivery_id,outcome="COMPLETED",execution="n8n-102",
               connector_id=CID,workflow_id=WID,event_id=EID,secret=SECRET):
        msg=signing_message(connector_id,workflow_id,event_id,
                            delivery_id,outcome,execution)
        return {
            "plan_id":PLAN,"connector_id":connector_id,
            "workflow_id":workflow_id,"event_id":event_id,
            "delivery_id":delivery_id,"outcome":outcome,
            "execution_id":execution,
            "proof_hmac_sha256":hmac.new(
                secret.encode(),msg.encode("ascii"),hashlib.sha256).hexdigest()}

    def test_valid_signed_completion_binds_dispatch_and_is_not_independent_proof(self):
        receipt=self.send()
        self.assertEqual(receipt["status"],"REMOTE_ACCEPTED")
        before=len(self.calls)
        proof=self.proofs.import_receipt(self.signed(receipt["delivery_id"]))
        self.assertEqual(proof["result"],"RECORDED")
        self.assertEqual(proof["provider_asserted_outcome"],"COMPLETED")
        self.assertEqual(proof["n8n_execution_id"],"n8n-102")
        self.assertEqual(proof["local_dispatch_status_unchanged"],"REMOTE_ACCEPTED")
        self.assertFalse(proof["business_effect_verified"])
        self.assertFalse(proof["independently_verified_remote"])
        self.assertEqual(proof["evidence"],"HMAC_MATCHED_SHARED_SECRET_OWNER_IMPORTED")
        self.assertEqual(proof["external_write_count"],0)
        self.assertEqual(len(self.calls),before)
        self.assertNotIn("Compute 7",json.dumps(self.proofs.list()))
        self.assertNotIn(SECRET,json.dumps(self.proofs.list()))
        self.assertEqual(self.handoff.history()["handoffs"][0]["status"],"REMOTE_ACCEPTED")

    def test_signed_failed_outcome_recorded_without_mutating_dispatch(self):
        did=self.send()["delivery_id"]
        proof=self.proofs.import_receipt(self.signed(did,outcome="FAILED"))
        self.assertEqual(proof["provider_asserted_outcome"],"FAILED")
        self.assertEqual(proof["local_dispatch_status_unchanged"],"REMOTE_ACCEPTED")
        self.assertFalse(proof["business_effect_verified"])
        self.assertEqual(len(self.calls),1)

    def test_tampered_signature_and_output_rejected_without_ledger_change(self):
        did=self.send()["delivery_id"]
        valid=self.signed(did)
        for item in [
            {**valid,"proof_hmac_sha256":"0"*64},
            {**valid,"outcome":"FAILED"},
            {**valid,"execution_id":"other_exec"},
            {**valid,"connector_id":"unrelated"},
        ]:
            with self.subTest(item=item),self.assertRaises(HandoffOutcomeProofError):
                self.proofs.import_receipt(item)
        self.assertEqual(self.proofs.list()["records"],[])
        self.assertEqual(len(self.calls),1)

    def test_no_actual_send_no_attestation(self):
        # A PREVIEW_ONLY handoff cannot receive an outcome.
        p=self.handoff.preview({
            "plan_id":PLAN,"connector_id":CID,
            "workflow_id":WID,"event_id":EID})
        fake=self.signed("a"*32)
        with self.assertRaisesRegex(HandoffOutcomeProofError,"handoff_dispatch_not_claimed"):
            self.proofs.import_receipt(fake)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.proofs.list()["records"],[])

    def test_nonexistent_delivery_and_wrong_event_fail(self):
        did=self.send()["delivery_id"]
        with self.assertRaisesRegex(HandoffOutcomeProofError,
                                    "handoff_delivery_attempt_not_found"):
            self.proofs.import_receipt(self.signed("f"*32))
        with self.assertRaisesRegex(HandoffOutcomeProofError,
                                    "handoff_dispatch_not_claimed"):
            self.proofs.import_receipt(self.signed(did,event_id="different_event"))
        self.assertEqual(self.proofs.list()["records"],[])

    def test_idempotent_exact_reimport_and_conflicting_new_signed_outcome(self):
        did=self.send()["delivery_id"]
        proof=self.signed(did)
        self.assertEqual(self.proofs.import_receipt(proof)["result"],"RECORDED")
        self.assertEqual(self.proofs.import_receipt(proof)["result"],"ALREADY_RECORDED")
        with self.assertRaisesRegex(HandoffOutcomeProofError,
                                    "conflicting_handoff_outcome_proof"):
            self.proofs.import_receipt(self.signed(
                did,outcome="FAILED",execution="n8n-103"))
        self.assertEqual(len(self.proofs.list()["records"]),1)
        self.assertEqual(len(self.calls),1)

    def test_uncertain_transport_may_gain_signed_claim_without_retry(self):
        def timeout(*args):
            self.calls.append("ATTEMPT_WITHOUT_ACK")
            raise TimeoutError("simulated acknowledgement lost")
        # Existing stage-114 manager sender is injected, no network call.
        self.manager._sender=timeout
        row=self.manager._entry(CID)
        self.manager._client(row).sender=timeout
        receipt=self.send()
        self.assertEqual(receipt["status"],"OUTCOME_UNCERTAIN")
        self.assertEqual(self.calls,["ATTEMPT_WITHOUT_ACK"])
        proof=self.proofs.import_receipt(self.signed(receipt["delivery_id"]))
        self.assertEqual(proof["provider_asserted_outcome"],"COMPLETED")
        self.assertEqual(proof["local_dispatch_status_unchanged"],"OUTCOME_UNCERTAIN")
        self.assertEqual(self.calls,["ATTEMPT_WITHOUT_ACK"])
        with self.assertRaises(Exception):
            self.handoff.preview({
                "plan_id":PLAN,"connector_id":CID,
                "workflow_id":WID,"event_id":EID})

    def test_restart_retains_signed_claim_no_auto_send(self):
        did=self.send()["delivery_id"]
        self.proofs.import_receipt(self.signed(did))
        self.handoff.close()
        self.handoff=AgentConnectorHandoff(self.plans,self.core,self.manager,self.dbpath)
        self.proofs=SignedManualHandoffOutcome(self.handoff,self.manager)
        rows=self.proofs.list()["records"]
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["provider_asserted_outcome"],"COMPLETED")
        self.assertEqual(len(self.calls),1)

    def test_secret_revocation_denies_new_import_but_keeps_existing_evidence(self):
        did=self.send()["delivery_id"]
        self.proofs.import_receipt(self.signed(did))
        self.manager.revoke(CID)
        self.assertEqual(len(self.proofs.list()["records"]),1)
        with self.assertRaises(HandoffOutcomeProofError):
            self.proofs.import_receipt(self.signed(did))
        self.assertEqual(len(self.calls),1)

    def test_source_payload_tamper_blocks_even_valid_signature(self):
        did=self.send()["delivery_id"]
        client=self.manager._client(self.manager._entry(CID))
        with client.db:
            client.db.execute("""UPDATE webhook_deliveries SET payload_sha256=?
                WHERE workflow_id=? AND event_id=?""",("e"*64,WID,EID))
        with self.assertRaisesRegex(HandoffOutcomeProofError,
                                    "handoff_delivery_source_mismatch"):
            self.proofs.import_receipt(self.signed(did))
        self.assertEqual(self.proofs.list()["records"],[])

    def test_forged_attempt_status_never_becomes_completion(self):
        did=self.send()["delivery_id"]
        client=self.manager._client(self.manager._entry(CID))
        with client.db:
            client.db.execute("""UPDATE webhook_deliveries SET status='IN_FLIGHT'
                WHERE workflow_id=? AND event_id=?""",(WID,EID))
        with self.assertRaisesRegex(HandoffOutcomeProofError,
                                    "handoff_delivery_attempt_not_finalized"):
            self.proofs.import_receipt(self.signed(did))
        self.assertEqual(len(self.calls),1)

    def test_invalid_fields_short_secret_and_format_rejected(self):
        did=self.send()["delivery_id"]
        signed=self.signed(did)
        for bad in [
            {**signed,"proof_hmac_sha256":"bad"},
            {**signed,"outcome":"SUCCESS"},
            {**signed,"execution_id":"bad\nnewline"},
            {**signed,"untrusted_url":"https://bad.example.com"},
            {**signed,"plan_id":"../other"},
        ]:
            with self.subTest(bad=bad), self.assertRaises(HandoffOutcomeProofError):
                self.proofs.import_receipt(bad)
        self.assertEqual(self.proofs.list()["records"],[])

    def test_real_loopback_http_session_csrf_password_audit_and_no_send(self):
        did=self.send()["delivery_id"]
        LocalOperatorAuth.provision(self.root/"operator.json",PASSWORD)
        owner=LocalOperatorAuth.load(self.root/"operator.json")
        audit=LocalSecurityAudit(self.root/"audit.sqlite3")
        server=ThreadingHTTPServer(("127.0.0.1",0),
            local_server.create_handler(
                local_core=self.core,operator_auth=owner,security_audit=audit,
                local_agent_plan_store=self.plans,
                managed_connectors=self.manager,
                agent_connector_handoff=self.handoff,
                handoff_outcomes=self.proofs))
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        client=build_opener(ProxyHandler({}),HTTPCookieProcessor(CookieJar()))
        base="http://127.0.0.1:"+str(server.server_port)
        csrf=None
        def post(path,data,use_csrf=True):
            headers={"Content-Type":"application/json"}
            if use_csrf and csrf: headers["X-AAS-CSRF"]=csrf
            with client.open(Request(base+path,json.dumps(data).encode("utf-8"),
                method="POST",headers=headers),timeout=7) as resp:
                return json.load(resp)
        try:
            with self.assertRaises(HTTPError) as anon:
                client.open(base+"/api/signed-handoff-outcomes")
            self.assertEqual(anon.exception.code,401)
            csrf=post("/api/operator-login",{"password":PASSWORD})["csrf_token"]
            with client.open(base+"/api/local-agent-capabilities") as resp:
                self.assertTrue(json.load(resp)["signed_handoff_outcome_import"])
            values=self.signed(did)
            with self.assertRaises(HTTPError) as badcsrf:
                post("/api/signed-handoff-outcome-import",
                     {**values,"current_password":PASSWORD},False)
            self.assertEqual(badcsrf.exception.code,403)
            with self.assertRaises(HTTPError) as badpass:
                post("/api/signed-handoff-outcome-import",
                     {**values,"current_password":"bad"})
            self.assertEqual(badpass.exception.code,401)
            self.assertEqual(self.proofs.list()["records"],[])
            with self.assertRaises(HTTPError) as fake:
                post("/api/signed-handoff-outcome-import",
                     {**values,"proof_hmac_sha256":"f"*64,
                      "current_password":PASSWORD})
            self.assertEqual(fake.exception.code,403)
            recorded=post("/api/signed-handoff-outcome-import",
                          {**values,"current_password":PASSWORD})
            self.assertEqual(recorded["result"],"RECORDED")
            with client.open(base+"/api/signed-handoff-outcomes") as resp:
                listed=json.load(resp)
            self.assertEqual(listed["records"][0]["plan_id"],PLAN)
            self.assertFalse(listed["business_effect_verified"])
            self.assertTrue(any(x["action"]=="SIGNED_HANDOFF_OUTCOME_IMPORT"
                                for x in audit.recent(100)))
            self.assertEqual(len(self.calls),1)
        finally:
            server.shutdown();server.server_close();thread.join(5);audit.close()


if __name__=="__main__":
    unittest.main()
