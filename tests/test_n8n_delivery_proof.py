"""Genuine SQLite + AES-GCM managed-autopilot receipt reconciliation contract.

Only fixture transport/HMAC credentials; no live n8n or Tencent interactions.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_connector_vault import generate_master_key
from local_connector_manager import LocalConnectorManager
from local_recurring_scheduler import LocalRecurringScheduler
from local_managed_scheduled_webhook import ManagedScheduledWebhookAutopilot
from local_n8n_delivery_proof import (
    SignedN8NDeliveryProof, N8NProofError, PROOF_DOMAIN, signing_message)
from local_workflow_core import LocalWorkflowCore

HAS_CRYPTO=importlib.util.find_spec("cryptography") is not None
SECRET="n8n-fixture-unique-strong-signing-secret-2026!"


class Clock:
    def __init__(self):
        self.now=datetime(2026,11,6,9,0,tzinfo=timezone.utc)
    def __call__(self):
        return self.now
    def advance(self,**kwargs):
        self.now+=timedelta(**kwargs)


@unittest.skipUnless(HAS_CRYPTO,"Dedicated receipt CI installs actual cryptography")
class N8NSignedDeliveryProofTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name)
        self.clock=Clock()
        self.core=LocalWorkflowCore(self.base/"flow.sqlite3")
        code,_=self.core.create({
            "workflow_id":"proof_flow","display_name":"Proof Flow",
            "service_filter":"automation","min_budget_tier":2,
            "followup_policy":"review_required"})
        self.assertEqual(code,201)
        self.core.activate({"workflow_id":"proof_flow","expected_version":1})
        self.scheduler=LocalRecurringScheduler(
            self.core,self.base/"schedules.sqlite3",clock=self.clock)
        self.schedule=self.scheduler.create({
            "workflow_id":"proof_flow","cadence":"minutes",
            "timezone":"Asia/Jakarta","service_code":"automation",
            "budget_tier":2,"timeline":"soon","every":1})
        self.sent=[]
        def send(url,payload,did,secret):
            self.sent.append((url,payload,did,secret))
            return 202
        self.manager=LocalConnectorManager(
            self.core,self.base/"managed-vault.json",
            sender=send,clock=self.clock,vault_key=generate_master_key())
        self.manager.add({"connector_id":"n8n_primary","label":"n8n Proof",
            "url":"https://example.org/webhook/n8n-secret-path",
            "signing_secret":SECRET})
        self.manager.add({"connector_id":"n8n_secondary","label":"Other",
            "url":"https://example.org/webhook/other-private-path",
            "signing_secret":"separate-other-long-n8n-signing-secret-2026"})
        self.folder=self.base/"managed-scheduled-webhook-ledgers"
        self.autopilot=ManagedScheduledWebhookAutopilot(
            self.scheduler,self.manager,None,self.folder,clock=self.clock)
        self.proofs=SignedN8NDeliveryProof(self.autopilot)

    def tearDown(self):
        self.autopilot.close()
        self.manager.close()
        self.scheduler.close()
        self.core.close()
        self.tmp.cleanup()

    def arm(self,cid="n8n_primary"):
        req={"connector_id":cid,"schedule_id":self.schedule["schedule_id"],
             "max_sends":3,"expires_days":7}
        p=self.autopilot.preview(req)
        return self.autopilot.arm({**req,"preview_token":p["preview_token"]})

    def dispatch(self,cid="n8n_primary"):
        self.arm(cid)
        self.clock.advance(minutes=1)
        self.scheduler.run_due()
        self.assertEqual(self.autopilot.dispatch_due(),1)
        payload=self.sent[-1][1]
        return payload["delivery_id"]

    def sign(self, did, cid="n8n_primary",outcome="COMPLETED",execution="90123",
             secret=SECRET, sid=None):
        sid=sid or self.schedule["schedule_id"]
        message=signing_message(cid,sid,did,outcome,execution)
        proof=hmac.new(secret.encode("utf-8"),
                       message.encode("ascii"),hashlib.sha256).hexdigest()
        return {"connector_id":cid,"schedule_id":sid,"delivery_id":did,
                "outcome":outcome,"execution_id":execution,
                "proof_hmac_sha256":proof}

    def test_exact_hmac_message_domain_and_no_network_on_import(self):
        did=self.dispatch()
        self.assertTrue(signing_message("n8n_primary",self.schedule["schedule_id"],
                did,"COMPLETED","90123").startswith(PROOF_DOMAIN+"\n"))
        before=len(self.sent)
        result=self.proofs.reconcile(self.sign(did))
        self.assertEqual(result["result"],"RECORDED")
        self.assertTrue(result["signature_valid"])
        self.assertFalse(result["independently_verified_remote"])
        self.assertEqual(result["local_dispatch_status_unchanged"],"REMOTE_ACCEPTED")
        self.assertEqual(len(self.sent),before)
        self.assertNotIn(SECRET,json.dumps(result))
        self.assertNotIn(self.sign(did)["proof_hmac_sha256"],json.dumps(result))
        self.assertEqual(self.proofs.list()["records"][0]["delivery_id"],did)

    def test_rejected_invalid_signature_without_ledger_write(self):
        did=self.dispatch()
        data=self.sign(did)
        data["proof_hmac_sha256"]="0"*64
        with self.assertRaisesRegex(N8NProofError,"signature_invalid"):
            self.proofs.reconcile(data)
        self.assertEqual(self.proofs.list()["records"],[])

    def test_cross_connector_replay_is_refused_even_if_hmac_valid_for_other(self):
        did=self.dispatch()
        other=self.sign(did,cid="n8n_secondary",
                        secret="separate-other-long-n8n-signing-secret-2026")
        with self.assertRaisesRegex(N8NProofError,"local_delivery_ledger_not_found"):
            self.proofs.reconcile(other)
        self.assertEqual(self.proofs.list()["records"],[])

    def test_nonexistent_delivery_rejected_even_with_valid_signature(self):
        self.arm()
        with self.assertRaisesRegex(N8NProofError,"local_delivery_attempt_not_found"):
            self.proofs.reconcile(self.sign("d"*32))

    def test_signature_bound_to_outcome_execution_id_schedule(self):
        did=self.dispatch()
        for key,value in [("outcome","FAILED"),
                          ("execution_id","99999"),
                          ("schedule_id","not-a-schedule")]:
            with self.subTest(key=key):
                changed=self.sign(did)
                changed[key]=value
                with self.assertRaises(N8NProofError):
                    self.proofs.reconcile(changed)
        self.assertEqual(self.proofs.list()["records"],[])

    def test_idempotent_duplicate_does_not_overwrite_existing_proof(self):
        did=self.dispatch()
        signed=self.sign(did)
        first=self.proofs.reconcile(signed)
        second=self.proofs.reconcile(signed)
        self.assertEqual(first["verified_at_utc"],second["verified_at_utc"])
        self.assertEqual(second["result"],"ALREADY_VERIFIED")
        self.assertEqual(len(self.proofs.list()["records"]),1)
        with self.assertRaisesRegex(N8NProofError,"conflicting_delivery_proof"):
            self.proofs.reconcile(self.sign(did,outcome="FAILED"))
        self.assertEqual(len(self.proofs.list()["records"]),1)

    def test_negative_provider_assertion_is_preserved(self):
        did=self.dispatch()
        result=self.proofs.reconcile(self.sign(did,outcome="FAILED"))
        self.assertEqual(result["provider_asserted_outcome"],"FAILED")
        self.assertEqual(result["local_dispatch_status_unchanged"],"REMOTE_ACCEPTED")
        self.assertEqual(len(self.sent),1)

    def test_restart_preserves_imported_proof_without_retriggering_send(self):
        did=self.dispatch()
        self.proofs.reconcile(self.sign(did))
        self.autopilot.close()
        self.autopilot=ManagedScheduledWebhookAutopilot(
            self.scheduler,self.manager,None,self.folder,clock=self.clock)
        self.proofs=SignedN8NDeliveryProof(self.autopilot)
        self.assertEqual(self.proofs.list()["records"][0]["delivery_id"],did)
        self.assertEqual(self.autopilot.dispatch_due(),0)
        self.assertEqual(len(self.sent),1)

    def test_uncertain_delivery_can_receive_proof_without_auto_resuming(self):
        self.manager._sender=lambda *args: (_ for _ in ()).throw(TimeoutError("fixture"))
        self.arm()
        self.clock.advance(minutes=1)
        self.scheduler.run_due()
        self.assertEqual(self.autopilot.dispatch_due(),1)
        policy=self.autopilot._get("n8n_primary",require_active=True)
        with policy.lock:
            did=policy.db.execute("""SELECT delivery_id
                FROM scheduled_webhook_attempts""").fetchone()[0]
        result=self.proofs.reconcile(self.sign(did))
        self.assertEqual(result["local_dispatch_status_unchanged"],"OUTCOME_UNCERTAIN")
        self.assertEqual(self.autopilot.get if False else policy.get(
            self.schedule["schedule_id"])["status"],"PAUSED_UNCERTAIN")
        self.assertEqual(self.autopilot.dispatch_due(),0)

    def test_disallow_reconcile_before_post_is_finalized(self):
        self.arm()
        policy=self.autopilot._get("n8n_primary",require_active=True)
        did="e"*32
        with policy.lock,policy.db:
            policy.db.execute("""INSERT INTO scheduled_webhook_attempts(
              schedule_id,slot_utc,delivery_id,status,payload_sha256,claimed_utc)
              VALUES(?,?,?,'IN_FLIGHT',?,?)""",
              (self.schedule["schedule_id"],
               "2026-11-06T09:01:00+00:00",did,"a"*64,
               "2026-11-06T09:01:00+00:00"))
        with self.assertRaisesRegex(N8NProofError,"not_finalized"):
            self.proofs.reconcile(self.sign(did))

    def test_revoked_credential_cannot_authorize_new_proof(self):
        did=self.dispatch()
        self.manager.revoke("n8n_primary")
        with self.assertRaises(N8NProofError):
            self.proofs.reconcile(self.sign(did))
        self.assertEqual(self.proofs.list()["records"],[])

    def test_proofs_still_visible_after_revoke_if_verified_before(self):
        did=self.dispatch()
        self.proofs.reconcile(self.sign(did))
        self.manager.revoke("n8n_primary")
        self.assertEqual(self.proofs.list()["records"][0]["delivery_id"],did)

    def test_empty_and_short_secrets_are_not_accepted_for_reconciliation(self):
        self.manager.add({"connector_id":"short_secret","label":"Short Secret",
            "url":"https://example.org/webhook/short",
            "signing_secret":"abc"})
        self.arm("short_secret")
        self.clock.advance(minutes=1)
        self.scheduler.run_due()
        self.autopilot.dispatch_due()
        did=[x[1]["delivery_id"] for x in self.sent
             if x[1]["connector_id"]=="short_secret"][0]
        with self.assertRaisesRegex(N8NProofError,"strong_connector_hmac_secret_required"):
            self.proofs.reconcile(self.sign(did,cid="short_secret",secret="abc"))

    def test_invalid_fields_and_no_untrusted_file_paths(self):
        for bad in [
            {},
            {"connector_id":"../../escape","schedule_id":"foo",
             "delivery_id":"0"*32,"outcome":"COMPLETED",
             "execution_id":"42","proof_hmac_sha256":"0"*64},
            {"connector_id":"n8n_primary","schedule_id":"foo",
             "delivery_id":"0"*32,"outcome":"COMPLETED",
             "execution_id":"42","proof_hmac_sha256":"0"*64},
        ]:
            with self.assertRaises(N8NProofError):
                self.proofs.reconcile(bad)
        self.assertEqual(self.proofs.list()["records"],[])
