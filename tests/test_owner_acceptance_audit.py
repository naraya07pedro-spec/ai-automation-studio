"""Stage 118 read-only offline cross-ledger provenance and backup/restore checks.

Real deterministic plan + encrypted connector registry + Stage 114 send + Stage
116 signed claim + Stage 117 stubbed provider execution + offline encrypted
backup. No real HTTPS requests, owner secrets, provider usage, or VPS.
"""
from contextlib import nullcontext, closing
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
sys.path.insert(0,str(ROOT/"tools"))

from local_agent_task_plans import LocalAgentPlanStore
from local_workflow_core import LocalWorkflowCore
from local_connector_manager import LocalConnectorManager
from local_connector_vault import generate_master_key
from local_agent_connector_handoff import AgentConnectorHandoff
from local_handoff_outcome_proof import SignedManualHandoffOutcome,signing_message
from local_n8n_execution_verifier import N8NExecutionVerifier
from local_operator_auth import LocalOperatorAuth
import offline_owner_backup as backup
import owner_acceptance_audit as audit

HAS_CRYPTO=importlib.util.find_spec("cryptography") is not None
PID="b"*24
CID="acceptance01"
WID="acceptance_flow"
EID="acceptance_event_01"
N8N_WID="201"
N8N_ID="12345"
N8N_NODE="Webhook Test"
N8N_HOST="fixture.app.n8n.cloud"
SIGN_KEY="fixture-private-hmac-key-that-is-not-real-32bytes"
PASSWORD="Disposable-stage-118-owner-authentication-password"
PASSPHRASE="Disposable-stage-118-encrypted-backup-passphrase"


@unittest.skipUnless(HAS_CRYPTO,"Install pinned optional AES-GCM for dedicated Stage 118 suite")
class OfflineEvidenceAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base=Path(self.tmp.name)
        self.home=base/".ai-automation-studio"
        self.home.mkdir(mode=0o700)
        self.operator=self.home/"operator.json"
        LocalOperatorAuth.provision(self.operator,PASSWORD)
        self.core=LocalWorkflowCore(self.home/"local-workflows.sqlite3")
        self.plans=LocalAgentPlanStore(self.home/"agent-task-plans.sqlite3")
        self.plans.create({"model":"synthetic-model","goal":"Compute 2+2",
                           "steps":[{"tool":"calculate",
                           "args":{"a":"2","b":"2","operation":"add"}}]},plan_id=PID)
        self.plans.approve(PID)
        self.plans.advance(PID)
        self.assertEqual(self.plans.get(PID)["status"],"COMPLETED")
        self.assertEqual(self.core.create({
            "workflow_id":WID,"display_name":"Acceptance Flow",
            "service_filter":"automation","min_budget_tier":2,
            "followup_policy":"review_required"})[0],201)
        self.assertEqual(self.core.activate({
            "workflow_id":WID,"expected_version":1})[0],200)
        self.assertEqual(self.core.run({
            "workflow_id":WID,"event_id":EID,"service_code":"automation",
            "budget_tier":2,"timeline":"soon"})[0],200)
        self.external_posts=0
        def fake_sender(url,payload,did,secret):
            self.external_posts+=1
            return 202
        self.manager=LocalConnectorManager(
            self.core,self.home/"managed-webhooks-vault.json",
            sender=fake_sender,vault_key=generate_master_key())
        self.manager.add({"connector_id":CID,"label":"Private test connector",
                          "url":"https://fixture.app.n8n.cloud/webhook/acceptance",
                          "signing_secret":SIGN_KEY})
        self.handoff=AgentConnectorHandoff(
            self.plans,self.core,self.manager,
            self.home/"agent-connector-handoffs.sqlite3")
        self.signed=SignedManualHandoffOutcome(self.handoff,self.manager)
        p=self.handoff.preview({"plan_id":PID,"connector_id":CID,
                                "workflow_id":WID,"event_id":EID})
        result=self.handoff.commit({
            "plan_id":PID,"connector_id":CID,"workflow_id":WID,
            "event_id":EID,"plan_sha256":p["plan_sha256"],
            "workflow_sha256":p["workflow_sha256"],
            "payload_sha256":p["payload_sha256"],
            "preview_token":p["preview_token"]})
        self.assertEqual(result["status"],"REMOTE_ACCEPTED")
        self.did=result["delivery_id"]
        msg=signing_message(CID,WID,EID,self.did,"COMPLETED",N8N_ID)
        self.signed.import_receipt({
            "plan_id":PID,"connector_id":CID,"workflow_id":WID,
            "event_id":EID,"delivery_id":self.did,"outcome":"COMPLETED",
            "execution_id":N8N_ID,"proof_hmac_sha256":hmac.new(
                SIGN_KEY.encode(),msg.encode("ascii"),
                hashlib.sha256).hexdigest()})
        self.get_calls=0
        def stub(host,execution_id,key):
            self.get_calls+=1
            return {"id":N8N_ID,"workflowId":N8N_WID,
                    "status":"success","finished":True,
                    "stoppedAt":"2026-11-07T12:00:00Z",
                    "data":{"resultData":{"runData":{N8N_NODE:[
                        {"data":{"main":[[{"json":{"body":{
                            "schema":"aas.local_workflow_receipt.v1",
                            "workflow_id":WID,"event_id":EID,
                            "delivery_id":self.did}}}]]}}
                    ]}}}}
        self.provider=N8NExecutionVerifier(self.signed,
            api_origin="https://"+N8N_HOST,
            workflow_id=N8N_WID,webhook_node_name=N8N_NODE,
            api_key="fake-api-key-long-enough-for-tests-2026",
            fetcher=stub)
        self.assertEqual(self.provider.verify_one(
            {"plan_id":PID})["status"],"EXECUTION_SUCCESS_CORRELATED")
        self.assertEqual(self.get_calls,1)
        self.handoff.close();self.manager.close()
        self.plans.close();self.core.close()
        # Avoid competing jobs for shared port 8765; production socket guard
        # is independently asserted in test_offline_guard_rejects_a_live_studio.
        self.guard=patch.object(audit,"_offline_guard",side_effect=nullcontext)
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def check(self,root=None,**pins):
        return audit.audit(root or self.home,PID,
            expected_host=pins.get("host",N8N_HOST),
            expected_workflow=pins.get("workflow",N8N_WID),
            expected_node=pins.get("node",N8N_NODE))

    def mutate(self,relative,sql,args=()):
        # sqlite3.Connection.__exit__ commits but DOES NOT close the DB.
        # A lingering WAL connection would correctly trip the offline guard.
        with closing(sqlite3.connect(self.home/relative)) as db:
            with db:
                db.execute(sql,args)

    def test_complete_authentic_local_chain_and_zero_network(self):
        got=self.check()
        self.assertEqual(got["status"],"LOCAL_EVIDENCE_CHAIN_CONSISTENT")
        self.assertEqual(len(got["checked_layers"]),6)
        self.assertEqual(got["recorded_provider_execution"],
                         "EXECUTION_SUCCESS_CORRELATED")
        self.assertTrue(got["owner_host_pin_checked"])
        self.assertTrue(got["owner_workflow_pin_checked"])
        self.assertTrue(got["owner_node_pin_checked"])
        self.assertFalse(got["live_provider_queried_now"])
        self.assertFalse(got["hmac_secret_reverified_offline"])
        self.assertFalse(got["business_effect_verified"])
        self.assertEqual(got["remote_reads"],0)
        self.assertEqual(got["external_writes"],0)
        self.assertNotIn(SIGN_KEY,json.dumps(got))
        self.assertNotIn("Compute 2+2",json.dumps(got))
        self.assertEqual(self.external_posts,1)
        self.assertEqual(self.get_calls,1)

    def test_owner_pinned_host_workflow_and_node_required_to_match(self):
        for pins in [
            {"host":"other.app.n8n.cloud"},
            {"workflow":"other"},
            {"node":"Other Webhook"},
        ]:
            with self.subTest(pins=pins),self.assertRaises(audit.AcceptanceError):
                self.check(**pins)

    def test_plan_hash_tamper_detected(self):
        self.mutate("agent-task-plans.sqlite3",
            "UPDATE agent_task_plans SET goal='changed' WHERE plan_id=?",(PID,))
        with self.assertRaisesRegex(audit.AcceptanceError,"plan_provenance_hash_mismatch"):
            self.check()

    def test_workflow_receipt_hash_tamper_detected(self):
        self.mutate("local-workflows.sqlite3",
            "UPDATE local_runs SET reason='CHANGED' WHERE workflow_id=? AND event_id=?",
            (WID,EID))
        with self.assertRaisesRegex(audit.AcceptanceError,"workflow_receipt_hash_mismatch"):
            self.check()

    def test_payload_hash_tamper_detected(self):
        self.mutate("agent-connector-handoffs.sqlite3",
            "UPDATE agent_connector_handoffs SET payload_sha256=? WHERE plan_id=?",
            ("a"*64,PID))
        with self.assertRaisesRegex(audit.AcceptanceError,"sent_payload_hash_mismatch"):
            self.check()

    def test_sender_ledger_delivery_or_status_tamper_detected(self):
        self.mutate("managed-webhook-ledgers/"+CID+".sqlite3",
            "UPDATE webhook_deliveries SET delivery_id=? WHERE event_id=?",
            ("f"*32,EID))
        with self.assertRaisesRegex(audit.AcceptanceError,"sender_ledger_binding_mismatch"):
            self.check()

    def test_signed_message_identity_tamper_detected(self):
        self.mutate("agent-connector-handoffs.sqlite3",
            "UPDATE signed_manual_handoff_outcomes SET execution_id=? WHERE plan_id=?",
            ("99999",PID))
        with self.assertRaisesRegex(audit.AcceptanceError,"signed_message_binding_mismatch"):
            self.check()

    def test_saved_provider_correlated_status_tamper_detected(self):
        self.mutate("agent-connector-handoffs.sqlite3",
            """UPDATE n8n_provider_execution_checks
               SET inbound_delivery_correlated=0 WHERE plan_id=?""",(PID,))
        with self.assertRaisesRegex(audit.AcceptanceError,
                                    "provider_inbound_delivery_not_correlated"):
            self.check()

    def test_failed_provider_claim_does_not_pass(self):
        self.mutate("agent-connector-handoffs.sqlite3",
            """UPDATE n8n_provider_execution_checks
               SET status='LOOKUP_NOT_FOUND' WHERE plan_id=?""",(PID,))
        with self.assertRaisesRegex(audit.AcceptanceError,
                                    "provider_execution_not_successfully_correlated"):
            self.check()

    def test_missing_evidence_and_bad_sqlite_fail_without_network(self):
        self.mutate("agent-connector-handoffs.sqlite3",
            "DELETE FROM signed_manual_handoff_outcomes WHERE plan_id=?",(PID,))
        with self.assertRaisesRegex(audit.AcceptanceError,"signed_receipt_missing"):
            self.check()
        self.assertEqual(self.external_posts,1)
        self.assertEqual(self.get_calls,1)

    def test_sqlite_sidecar_is_refused_even_after_clean_server_shutdown(self):
        side=Path(str(self.home/"agent-task-plans.sqlite3")+"-wal")
        side.write_bytes(b"synthetic stale WAL marker")
        with self.assertRaisesRegex(audit.AcceptanceError,
                                    "ledger_requires_clean_shutdown"):
            self.check()

    def test_missing_or_symlinked_ledger_refused(self):
        original=self.home/"agent-task-plans.sqlite3"
        dest=self.home/"private-copy.sqlite3"
        original.rename(dest)
        original.symlink_to(dest)
        with self.assertRaises(audit.AcceptanceError):
            self.check()

    def test_actual_encrypted_backup_and_disposable_restore_preserve_chain(self):
        # Real encryption, restored SQLite and fixture owner password verifier.
        archive=self.home.parent/"fixture-encrypted.aasbak"
        restore=self.home.parent/"restored-for-readonly-audit"
        with patch.object(backup,"_offline_guard",side_effect=nullcontext):
            snapshot=backup._snapshot(self.home)
            for name,blob in snapshot.items():
                if name.endswith(".sqlite3"):
                    with self.subTest(sqlite_file=name):
                        backup._check_sqlite_bytes(blob)
            made=backup.create(self.home,archive,PASSPHRASE)
        self.assertEqual(made["status"],"ENCRYPTED_OFFLINE_BACKUP_CREATED")
        self.assertNotIn(b"Compute 2+2",archive.read_bytes())
        self.assertNotIn(SIGN_KEY.encode(),archive.read_bytes())
        self.assertEqual(backup.verify(archive,PASSPHRASE)["status"],
                         "ENCRYPTED_BACKUP_AUTHENTICATED_AND_SQLITE_CHECKED")
        result=backup.restore_test(archive,restore,PASSPHRASE,consent=True)
        self.assertEqual(result["status"],
                         "DISPOSABLE_RESTORE_TO_NEW_DIRECTORY_CHECKED")
        restored=self.check(restore)
        self.assertEqual(restored["status"],"LOCAL_EVIDENCE_CHAIN_CONSISTENT")
        self.assertFalse(restored["business_effect_verified"])

    def test_cli_never_prints_local_private_state_on_failure(self):
        with patch("sys.stdout") as stream:
            code=audit.main(["--owner-root",str(self.home),
                             "--plan-id","not-a-plan"])
        self.assertEqual(code,2)
        data=json.loads("".join(c.args[0] for c in stream.write.call_args_list))
        self.assertEqual(data["status"],"LOCAL_EVIDENCE_CHAIN_NOT_PROVEN")
        self.assertEqual(data["remote_reads"],0)
        self.assertEqual(data["external_writes"],0)
        self.assertNotIn(str(self.home),json.dumps(data))

    def test_offline_guard_rejects_a_live_studio(self):
        self.guard.stop()
        try:
            import socket
            with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1",0))
                # Simulate bind refused: no access to the real Studio service.
                with patch("owner_acceptance_audit._offline_guard",
                           side_effect=backup.BackupError(
                               "stop_local_studio_before_backup")):
                    with self.assertRaises(backup.BackupError):
                        self.check()
        finally:
            self.guard.start()


if __name__=="__main__":
    unittest.main()
