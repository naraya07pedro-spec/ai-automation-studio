"""Stage 124: strict nine-layer local Gmail recovery audit.

The underlying Stage 118 fixture records a real local completed plan,
workflow, connector HMAC signed delivery and synthetic n8n API success.
The new Stage 120/121/122 rows are synthetic saved assertions, then audited
by the actual offline read-only code before/after genuine AES-GCM restore.
No real provider account, network, user password or VPS.
"""
from contextlib import closing, nullcontext
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
sys.path.insert(0,str(ROOT/"tests"))
import offline_owner_backup as backup
import owner_acceptance_audit as acceptance
import owner_encrypted_recovery_drill as drill
import test_owner_acceptance_audit as stage118

MESSAGE_ID="gmail-provider-synthetic-124"
RECIPIENT="owner@example.org"
SUBJECT="Stage124 synthetic proof"
GMAIL_NODE="Send Gmail"
P1="2026-11-08T01:00:00+00:00"
P2="2026-11-08T02:00:00+00:00"
P3="2026-11-08T03:00:00+00:00"
PASS="stage124-strong-fixture-passphrase"


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


@unittest.skipUnless(importlib.util.find_spec("cryptography") is not None,
                     "Dedicated Stage124 workflow installs optional AES-GCM")
class FullRestoredGmailChainTests(unittest.TestCase):
    def setUp(self):
        self.fixture=stage118.OfflineEvidenceAcceptance(
            methodName="test_complete_authentic_local_chain_and_zero_network")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.owner=self.fixture.home
        self.root=self.owner.parent
        self.dbfile=self.owner/"agent-connector-handoffs.sqlite3"
        self.expected={
            "expected_host":stage118.N8N_HOST,
            "expected_workflow":stage118.N8N_WID,
            "expected_node":stage118.N8N_NODE,
            "expected_gmail_node":GMAIL_NODE,
        }
        self.insert_strict_rows()

    def insert_strict_rows(self):
        gmail_sha=_sha(MESSAGE_ID)
        recipient_sha=_sha(RECIPIENT)
        subject_sha=_sha(SUBJECT)
        msg="\n".join(("AAS-GMAIL-ARTIFACT-BINDING-V1",stage118.PID,
            stage118.CID,stage118.WID,stage118.EID,self.fixture.did,
            stage118.N8N_ID,gmail_sha,recipient_sha,subject_sha))
        signed_message="\n".join(("AAS-MANUAL-HANDOFF-N8N-FINAL-RECEIPT-V1",
            stage118.CID,stage118.WID,stage118.EID,self.fixture.did,
            "COMPLETED",stage118.N8N_ID))
        with closing(sqlite3.connect(self.dbfile)) as db:
            with db:
                db.execute("""CREATE TABLE signed_gmail_artifact_bindings(
                    plan_id TEXT PRIMARY KEY,connector_id TEXT NOT NULL,
                    delivery_id TEXT NOT NULL,execution_id TEXT NOT NULL,
                    signed_message_sha256 TEXT NOT NULL,gmail_id_sha256 TEXT NOT NULL,
                    recipient_sha256 TEXT NOT NULL,subject_sha256 TEXT NOT NULL,
                    artifact_message_sha256 TEXT NOT NULL,recorded_utc TEXT NOT NULL)""")
                db.execute("""CREATE TABLE n8n_gmail_output_witnesses(
                    plan_id TEXT PRIMARY KEY,execution_id TEXT NOT NULL,
                    delivery_id TEXT NOT NULL,pinned_send_node_sha256 TEXT NOT NULL,
                    signed_gmail_id_sha256 TEXT NOT NULL,status TEXT NOT NULL,
                    checked_at_utc TEXT NOT NULL)""")
                db.execute("""CREATE TABLE gmail_sent_evidence(
                    plan_id TEXT PRIMARY KEY,gmail_message_sha256 TEXT NOT NULL,
                    expected_to_sha256 TEXT NOT NULL,
                    expected_subject_sha256 TEXT NOT NULL,
                    n8n_execution_sha256 TEXT NOT NULL,status TEXT NOT NULL,
                    gmail_sent_label_seen INTEGER NOT NULL,
                    gmail_artifact_sha256 TEXT NOT NULL,
                    claimed_at_utc TEXT NOT NULL)""")
                db.execute("""INSERT INTO signed_gmail_artifact_bindings VALUES(
                    ?,?,?,?,?,?,?,?,?,?)""", (
                    stage118.PID,stage118.CID,self.fixture.did,
                    stage118.N8N_ID,_sha(signed_message),gmail_sha,
                    recipient_sha,subject_sha,_sha(msg),P1))
                db.execute("""INSERT INTO n8n_gmail_output_witnesses VALUES(
                    ?,?,?,?,?,?,?)""", (
                    stage118.PID,stage118.N8N_ID,self.fixture.did,
                    _sha(GMAIL_NODE),gmail_sha,
                    "PROVIDER_SEND_NODE_ID_CORRELATED",P2))
                db.execute("""INSERT INTO gmail_sent_evidence VALUES(
                    ?,?,?,?,?,?,?,?,?)""",(
                    stage118.PID,gmail_sha,recipient_sha,subject_sha,
                    _sha(stage118.N8N_ID+":"+self.fixture.did),
                    "SENT_ARTIFACT_MATCHED",1,
                    _sha(MESSAGE_ID+"\n"+RECIPIENT+"\n"+SUBJECT),P3))

    def audit(self,root=None,**pins):
        args={**self.expected,**pins}
        return acceptance.audit(root or self.owner,stage118.PID,
            require_gmail_sent_chain=True,**args)

    def change(self,table,col,value):
        assert table in ("signed_gmail_artifact_bindings",
                         "n8n_gmail_output_witnesses","gmail_sent_evidence")
        with closing(sqlite3.connect(self.dbfile)) as db:
            with db:
                db.execute("UPDATE "+table+" SET "+col+"=? WHERE plan_id=?",
                           (value,stage118.PID))

    def test_strict_nine_layers_are_consistent_no_remote_reads(self):
        x=self.audit()
        self.assertEqual(x["status"],"LOCAL_GMAIL_EVIDENCE_CHAIN_CONSISTENT")
        self.assertEqual(len(x["checked_layers"]),9)
        self.assertTrue(x["strict_saved_gmail_chain_audited"])
        self.assertTrue(x["owner_gmail_node_pin_checked"])
        self.assertFalse(x["hmac_secret_reverified_offline"])
        self.assertFalse(x["live_provider_queried_now"])
        self.assertFalse(x["business_effect_verified"])
        self.assertEqual(x["remote_reads"],0)
        self.assertEqual(x["external_writes"],0)
        self.assertEqual(self.fixture.get_calls,1)
        self.assertEqual(self.fixture.external_posts,1)
        for raw in (MESSAGE_ID,RECIPIENT,SUBJECT,stage118.SIGN_KEY):
            self.assertNotIn(raw,json.dumps(x))

    def test_legacy_six_layers_unchanged_without_opt_in(self):
        x=acceptance.audit(self.owner,stage118.PID,
            expected_host=stage118.N8N_HOST,
            expected_workflow=stage118.N8N_WID,
            expected_node=stage118.N8N_NODE)
        self.assertEqual(x["status"],"LOCAL_EVIDENCE_CHAIN_CONSISTENT")
        self.assertEqual(len(x["checked_layers"]),6)
        self.assertFalse(x["strict_saved_gmail_chain_audited"])

    def test_strict_requires_all_four_owner_pins(self):
        for field in self.expected:
            pins=dict(self.expected)
            del pins[field]
            # Use explicit None to emulate missing pin.
            pins[field]=None
            with self.subTest(field=field),self.assertRaisesRegex(
                    acceptance.AcceptanceError,"four_owner_pins"):
                self.audit(**pins)
        with self.assertRaisesRegex(acceptance.AcceptanceError,
                                    "gmail_node_pin_requires_strict_audit"):
            acceptance.audit(self.owner,stage118.PID,
                             expected_gmail_node=GMAIL_NODE)

    def test_owner_gmail_node_pin_mismatch_is_rejected(self):
        with self.assertRaisesRegex(acceptance.AcceptanceError,
                                    "n8n_gmail_send_node_not_owner_pinned"):
            self.audit(expected_gmail_node="Other Gmail")
        with self.assertRaisesRegex(acceptance.AcceptanceError,
                                    "invalid_pinned_gmail_send_node"):
            self.audit(expected_gmail_node=stage118.N8N_NODE)

    def test_tampered_saved_claim_fields_are_detected(self):
        for col,val,err in (
            ("connector_id","different","signed_gmail_chain_identity_mismatch"),
            ("delivery_id","f"*32,"signed_gmail_chain_identity_mismatch"),
            ("execution_id","99999","signed_gmail_chain_identity_mismatch"),
            ("signed_message_sha256","0"*64,"signed_gmail_chain_identity_mismatch"),
            ("gmail_id_sha256","0"*64,"signed_gmail_claim_digest_mismatch"),
            ("recipient_sha256","0"*64,"signed_gmail_claim_digest_mismatch"),
            ("subject_sha256","0"*64,"signed_gmail_claim_digest_mismatch"),
            ("artifact_message_sha256","0"*64,"signed_gmail_claim_digest_mismatch"),
            ("recorded_utc","2026-invalid","signed_gmail_recorded_time_invalid"),
        ):
            with self.subTest(col=col):
                original=self.field("signed_gmail_artifact_bindings",col)
                self.change("signed_gmail_artifact_bindings",col,val)
                with self.assertRaisesRegex(acceptance.AcceptanceError,err):
                    self.audit()
                self.change("signed_gmail_artifact_bindings",col,original)

    def field(self,table,col):
        with closing(sqlite3.connect(self.dbfile)) as db:
            return db.execute("SELECT "+col+" FROM "+table+" WHERE plan_id=?",
                              (stage118.PID,)).fetchone()[0]

    def test_tampered_output_fields_or_attempt_replay_are_rejected(self):
        for col,val,err in (
            ("execution_id","99999","n8n_gmail_output_execution_or_delivery_mismatch"),
            ("delivery_id","f"*32,"n8n_gmail_output_execution_or_delivery_mismatch"),
            ("pinned_send_node_sha256","0"*64,"n8n_gmail_send_node_not_owner_pinned"),
            ("signed_gmail_id_sha256","0"*64,"n8n_gmail_output_identity_mismatch"),
            ("status","LOOKUP_UNCERTAIN","n8n_gmail_output_not_correlated"),
            ("checked_at_utc","2026-10-01T00:00:00+00:00","n8n_gmail_output_order_invalid"),
        ):
            with self.subTest(col=col):
                original=self.field("n8n_gmail_output_witnesses",col)
                self.change("n8n_gmail_output_witnesses",col,val)
                with self.assertRaisesRegex(acceptance.AcceptanceError,err):
                    self.audit()
                self.change("n8n_gmail_output_witnesses",col,original)

    def test_tampered_gmail_sent_or_unsent_fields_are_rejected(self):
        for col,val,err in (
            ("gmail_message_sha256","0"*64,"gmail_sent_identity_mismatch"),
            ("expected_to_sha256","0"*64,"gmail_sent_identity_mismatch"),
            ("expected_subject_sha256","0"*64,"gmail_sent_identity_mismatch"),
            ("n8n_execution_sha256","0"*64,"gmail_sent_execution_delivery_mismatch"),
            ("status","NOT_CORRELATED","gmail_sent_not_independently_matched"),
            ("gmail_sent_label_seen",0,"gmail_sent_not_independently_matched"),
            ("gmail_artifact_sha256","bad","gmail_sent_not_independently_matched"),
            ("claimed_at_utc","2026-11-08T01:30:00+00:00","gmail_sent_order_invalid"),
        ):
            with self.subTest(col=col):
                original=self.field("gmail_sent_evidence",col)
                self.change("gmail_sent_evidence",col,val)
                with self.assertRaisesRegex(acceptance.AcceptanceError,err):
                    self.audit()
                self.change("gmail_sent_evidence",col,original)

    def test_missing_any_strict_table_fails_closed_without_upgrading_six_ledger(self):
        with closing(sqlite3.connect(self.dbfile)) as db:
            with db:
                db.execute("DROP TABLE gmail_sent_evidence")
        with self.assertRaisesRegex(acceptance.AcceptanceError,
                                    "gmail_sent_evidence_schema_or_query_invalid"):
            self.audit()
        self.assertEqual(acceptance.audit(
            self.owner,stage118.PID)["status"],"LOCAL_EVIDENCE_CHAIN_CONSISTENT")

    def test_failed_original_provider_status_blocks_strict_chain(self):
        with closing(sqlite3.connect(self.dbfile)) as db:
            with db:
                db.execute("""UPDATE n8n_provider_execution_checks SET
                    status='EXECUTION_FAILED_CORRELATED',
                    provider_execution_status='error',
                    provider_finished=0 WHERE plan_id=?""",(stage118.PID,))
                db.execute("""UPDATE signed_manual_handoff_outcomes SET
                    claimed_outcome='FAILED' WHERE plan_id=?""",(stage118.PID,))
        with self.assertRaises(acceptance.AcceptanceError):
            self.audit()

    def test_encrypted_secondary_restore_preserves_nine_layer_identity(self):
        primary=self.root/"recovery-124.aasbak"
        secondary=self.root/"recovery-copy-124.aasbak"
        restored=self.root/"restore-nine-layers"
        with patch.object(backup,"_offline_guard",side_effect=nullcontext):
            result=drill.drill(
                self.owner,primary,secondary,PASS,
                restore_directory=restored,plaintext_consent=True,
                plan_id=stage118.PID,
                require_gmail_sent_chain=True,
                expected_host=stage118.N8N_HOST,
                expected_workflow=stage118.N8N_WID,
                expected_node=stage118.N8N_NODE,
                expected_gmail_node=GMAIL_NODE)
        self.assertEqual(result["status"],
                         "RESTORED_NINE_LAYER_GMAIL_EVIDENCE_CONSISTENT")
        self.assertEqual(result["local_ledger_layers_checked"],9)
        self.assertTrue(result["strict_saved_gmail_chain_audited"])
        self.assertTrue(result["semantic_six_ledger_audit_passed"])
        self.assertTrue(result["plaintext_restored"])
        self.assertEqual(primary.read_bytes(),secondary.read_bytes())
        self.assertEqual(self.audit(restored)["status"],
                         "LOCAL_GMAIL_EVIDENCE_CHAIN_CONSISTENT")

    def test_strict_restore_preflight_requires_pins_before_writing_backup(self):
        first=self.root/"missing-pins.aasbak"
        second=self.root/"missing-pins-copy.aasbak"
        with self.assertRaisesRegex(drill.RecoveryDrillError,
                                    "strict_gmail_audit_requires_plan_and_four_pins"):
            drill.drill(self.owner,first,second,PASS,
                        restore_directory=self.root/"tmp-restore",
                        plaintext_consent=True,plan_id=stage118.PID,
                        require_gmail_sent_chain=True)
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())

    def test_offline_cli_reports_nine_layers_without_leaking_identity(self):
        with patch("sys.stdout",new_callable=io.StringIO) as output:
            rc=acceptance.main([
                "--owner-root",str(self.owner),
                "--plan-id",stage118.PID,
                "--expected-n8n-host",stage118.N8N_HOST,
                "--expected-n8n-workflow",stage118.N8N_WID,
                "--expected-n8n-node",stage118.N8N_NODE,
                "--expected-n8n-gmail-node",GMAIL_NODE,
                "--require-gmail-sent-chain"])
        self.assertEqual(rc,0)
        report=json.loads(output.getvalue())
        self.assertEqual(report["status"],"LOCAL_GMAIL_EVIDENCE_CHAIN_CONSISTENT")
        for raw in (MESSAGE_ID,RECIPIENT,SUBJECT,stage118.SIGN_KEY,str(self.owner)):
            self.assertNotIn(raw,output.getvalue())


if __name__=="__main__":
    unittest.main()
