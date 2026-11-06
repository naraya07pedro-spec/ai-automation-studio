"""No simulated local Ollama checks may be labelled as LIVE evidence."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from verify_live_ai_schedule import (
    LiveAcceptanceError, run_disposable_schedule, live_acceptance,
    save_redacted_evidence, main)


class Clock:
    def __init__(self):
        self.value=datetime(2026,11,6,9,0,tzinfo=timezone.utc)
    def __call__(self):
        return self.value
    def advance(self,**kwargs):
        self.value+=timedelta(**kwargs)


class LiveAcceptanceContractTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock()
        self.calls=[]
        def fake_runner(prompt,model):
            self.calls.append((prompt,model))
            return {"answer":"A real slot was processed by an INJECTED fixture.",
                    "mode":"LOCAL_MODEL","tools_used":["utc_now"],
                    "external_dispatch_count":0}
        self.fake=fake_runner

    def synthetic(self,runner=None):
        def due(engine,created):
            self.clock.advance(minutes=1)
            engine.run_due()
        return run_disposable_schedule("fixture:3b",
            runner=runner or self.fake,clock=self.clock,
            deadline_seconds=8,after_start=due)

    def test_synthetic_success_never_advertises_real_inference_or_tencent(self):
        result=self.synthetic()
        self.assertEqual(result["status"],"SYNTHETIC_DISPOSABLE_SCHEDULER_PASSED")
        self.assertEqual(result["inference_source"],"CALLER_INJECTED_NOT_LIVE_EVIDENCE")
        self.assertEqual(result["attempted_runs"],1)
        self.assertTrue(result["sqlite_reopen_verified"])
        self.assertTrue(result["no_replay_on_reopen"])
        self.assertEqual(result["external_dispatch_count"],0)
        self.assertEqual(len(self.calls),1)
        self.assertNotIn("LIVE_LOCAL_OLLAMA_SCHEDULE_ACCEPTANCE_PASSED",
                         json.dumps(result))
        self.assertNotIn("tencent_instance_identity_verified",result)

    def test_synthetic_failure_is_not_promoted_to_success(self):
        def bad(p,m):
            return {"answer":"Sent email to everyone","mode":"LOCAL_MODEL",
                    "tools_used":["send_email"],"external_dispatch_count":1}
        with self.assertRaisesRegex(LiveAcceptanceError,"not_completed"):
            self.synthetic(bad)

    def test_synthetic_ollama_timeout_is_not_marked_success(self):
        from local_ollama_agent import AgentError
        def fails(p,m):
            raise AgentError("local_model_timeout")
        with self.assertRaisesRegex(LiveAcceptanceError,"not_completed_paused_uncertain"):
            self.synthetic(fails)

    def test_live_entrypoint_refuses_running_studio_before_model_lookup(self):
        with patch("verify_live_ai_schedule.studio_port_in_use",return_value=True), \
             patch("verify_live_ai_schedule.installed_models") as models:
            with self.assertRaisesRegex(LiveAcceptanceError,"studio_port_8765_in_use"):
                live_acceptance("fixture:3b")
            models.assert_not_called()

    def test_live_entrypoint_requires_exact_installed_model(self):
        with patch("verify_live_ai_schedule.studio_port_in_use",return_value=False), \
             patch("verify_live_ai_schedule.installed_models",return_value=["one:1"]):
            with self.assertRaisesRegex(LiveAcceptanceError,"model_not_installed"):
                live_acceptance("fixture:3b")

    def test_no_fake_success_fallback_when_ollama_missing(self):
        out=io.StringIO()
        with patch("verify_live_ai_schedule.studio_port_in_use",return_value=False), \
             patch("verify_live_ai_schedule.installed_models",
                   side_effect=__import__("local_ollama_agent").AgentError("local_model_unavailable")), \
             contextlib.redirect_stdout(out):
            status=main(["--model","fixture:3b"])
        self.assertEqual(status,2)
        report=json.loads(out.getvalue())
        self.assertEqual(report["status"],"NOT_VERIFIED")
        self.assertFalse(report["live_model_verified"])
        self.assertFalse(report["tencent_instance_identity_verified"])

    def test_model_identifier_validation(self):
        for bad in ("../../escape", "", "a'*", "example:"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(LiveAcceptanceError,"invalid_model_name"):
                    live_acceptance(bad)

    def test_explicit_report_save_is_outside_checkout_create_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("verify_live_ai_schedule.Path.home",return_value=Path(tmp)):
                report={"task_id":"a"*24,
                        "status":"LIVE_LOCAL_OLLAMA_SCHEDULE_ACCEPTANCE_PASSED",
                        "inference_source":"REAL_LOCAL_OLLAMA_127_0_0_1_11434",
                        "answer_sha256":"f"*64}
                self.assertTrue(save_redacted_evidence(report))
                path=Path(tmp)/".ai-automation-studio"/"live-acceptance"/("live-local-ollama-"+ "a"*24+".json")
                self.assertTrue(path.is_file())
                self.assertEqual(json.loads(path.read_text()),report)
                with self.assertRaisesRegex(LiveAcceptanceError,"cannot_create_unique"):
                    save_redacted_evidence(report)
                if os.name=="posix":
                    self.assertEqual(path.stat().st_mode & 0o777,0o600)
                    self.assertEqual(path.parent.stat().st_mode & 0o777,0o700)

    def test_no_arbitrary_path_or_symlinked_report_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            root=base/".ai-automation-studio"
            root.mkdir()
            ext=base/"untrusted"
            ext.mkdir()
            (root/"live-acceptance").symlink_to(ext,target_is_directory=True)
            with patch("verify_live_ai_schedule.Path.home",return_value=base):
                with self.assertRaisesRegex(LiveAcceptanceError,"unsafe_evidence_directory"):
                    save_redacted_evidence({
                        "status":"LIVE_LOCAL_OLLAMA_SCHEDULE_ACCEPTANCE_PASSED",
                        "inference_source":"REAL_LOCAL_OLLAMA_127_0_0_1_11434",
                        "task_id":"a"*24})
            self.assertEqual(list(ext.iterdir()),[])


    def test_evidence_saver_refuses_synthetic_report_even_if_owner_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("verify_live_ai_schedule.Path.home",return_value=Path(tmp)):
                with self.assertRaisesRegex(LiveAcceptanceError,
                                             "only_genuine_live_reports"):
                    save_redacted_evidence({
                        "status":"SYNTHETIC_DISPOSABLE_SCHEDULER_PASSED",
                        "inference_source":"CALLER_INJECTED_NOT_LIVE_EVIDENCE",
                        "task_id":"a"*24})
                self.assertFalse((Path(tmp)/".ai-automation-studio").exists())

    def test_evidence_saver_blocks_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("verify_live_ai_schedule.Path.home",return_value=Path(tmp)):
                with self.assertRaisesRegex(LiveAcceptanceError,
                                             "only_genuine_live_reports"):
                    save_redacted_evidence({
                        "status":"LIVE_LOCAL_OLLAMA_SCHEDULE_ACCEPTANCE_PASSED",
                        "inference_source":"REAL_LOCAL_OLLAMA_127_0_0_1_11434",
                        "task_id":"../../unauthorized"})
                self.assertFalse((Path(tmp)/".ai-automation-studio").exists())
