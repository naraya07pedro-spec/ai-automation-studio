"""Offline paid model-policy change is owner-explicit, durable and non-destructive."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_on_demand_remote_ai import LocalOnDemandRemoteAI, OnDemandRemoteAIError
from owner_remote_policy import replace_saved_policy,RemotePolicyChangeBlocked


class RemoteModelPolicyAdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file=Path(self.tmp.name)/"one-shot.sqlite3"
        self.runner=lambda prompt,model: {
            "mode":"REMOTE_OPENAI_READ_ONLY",
            "answer":"test","tools_used":[],
            "external_dispatch_count":0,
            "usage":{"input_tokens":10,"output_tokens":2}}
        db=LocalOnDemandRemoteAI(self.file,runner=self.runner,
            model_allowlist=["openai_responses:gpt-5"],daily_attempt_limit=1)
        db.close()

    def test_offline_only_owner_approval_can_replace_persisted_policy(self):
        with self.assertRaisesRegex(RemotePolicyChangeBlocked,"owner_review_required"):
            replace_saved_policy(self.file,models=["openai_responses:gpt-6"],
                                 daily_limit=2)
        with self.assertRaisesRegex(OnDemandRemoteAIError,"offline_owner_review"):
            LocalOnDemandRemoteAI(self.file,runner=self.runner,
                 model_allowlist=["openai_responses:gpt-6"],daily_attempt_limit=2)
        status=replace_saved_policy(self.file,models=["openai_responses:gpt-6"],
                                    daily_limit=2,acknowledged=True)
        self.assertEqual(status["status"],"LOCAL_REMOTE_POLICY_REPLACED_OFFLINE")
        self.assertEqual(status["revision"],2)
        self.assertEqual(status["maximum_attempts_per_utc_day"],2)
        self.assertFalse(status["history_deleted"]==True)
        db=LocalOnDemandRemoteAI(self.file,runner=self.runner)
        self.assertEqual(db.daily_attempt_limit,2)
        self.assertEqual(db.approved_models(),[
            {"provider":"openai_responses","model":"gpt-6"}])
        db.close()
        # Daily cap is preserved if omitted on further deliberate reconfiguration.
        status=replace_saved_policy(self.file,models=["openai_responses:gpt-5"],
                                    acknowledged=True)
        self.assertEqual(status["maximum_attempts_per_utc_day"],2)

    def test_unresolved_paid_receipt_blocks_any_policy_change(self):
        with sqlite3.connect(self.file) as db:
            db.execute("""INSERT INTO remote_asks(request_id,prompt_sha256,
              model,status,created_utc,provider) VALUES(?,?,?,?,?,?)""",
              ("a"*32,"0"*64,"gpt-5","OUTCOME_UNCERTAIN",
               "2026-11-08T00:00:00+00:00","openai_responses"))
        with self.assertRaisesRegex(RemotePolicyChangeBlocked,"ambiguous_paid_receipts"):
            replace_saved_policy(self.file,models=["openai_responses:gpt-6"],
                                 acknowledged=True)
        with sqlite3.connect(self.file) as db:
            old=db.execute("SELECT status FROM remote_asks").fetchone()[0]
            policy=db.execute("SELECT revision,daily_attempt_limit FROM remote_one_shot_policy").fetchone()
        self.assertEqual(old,"OUTCOME_UNCERTAIN")
        self.assertEqual(policy,(1,1))

    def test_exact_offline_remove_allowlist_requires_explicit_review(self):
        status=replace_saved_policy(self.file,models=None,daily_limit=1,
                                    acknowledged=True)
        self.assertIsNone(status["approved_models"])
        db=LocalOnDemandRemoteAI(self.file,runner=self.runner)
        self.assertIsNone(db.model_allowlist)
        self.assertEqual(db.daily_attempt_limit,1)
        db.close()

    def test_invalid_daily_cap_fails_before_any_database_write(self):
        for cap in [0,6,True,"2"]:
            with self.subTest(cap=cap),self.assertRaisesRegex(
                 RemotePolicyChangeBlocked,"invalid_daily_cap"):
                replace_saved_policy(self.file,models=["openai_responses:gpt-5"],
                                     daily_limit=cap,acknowledged=True)
        with sqlite3.connect(self.file) as db:
            self.assertEqual(db.execute(
                "SELECT revision FROM remote_one_shot_policy").fetchone()[0],1)


if __name__=="__main__":
    unittest.main()
