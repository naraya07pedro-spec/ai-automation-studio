"""Slice 61: offline local-host diagnostics, explicit inference and safe evidence."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_ollama_agent import AgentError
from local_document_analysis import LocalDocumentError
from local_windows_preflight import preflight,diagnose

RAM={"total_ram_mb":8192,"available_ram_mb":1700,"memory_load_pct":79}


class WindowsHostPreflightTests(unittest.TestCase):
    def test_harmless_precheck_requires_no_model_or_tool_inference(self):
        with patch("local_windows_preflight.smoke") as mock:
            result=preflight(catalog=lambda:["fixture:model"],memory=lambda:RAM,
                             system="Windows")
        mock.assert_not_called()
        self.assertEqual("MODEL_SELECTION_REQUIRED",result["status"])
        self.assertEqual(["fixture:model"],result["installed_models"])
        self.assertEqual(1,result["installed_model_count"])
        self.assertEqual("Windows",result["host_os"])
        self.assertFalse(result["live_model_inference_attempted"])
        self.assertFalse(result["application_files_read"])
        self.assertFalse(result["history_saved"])
        self.assertEqual(0,result["external_write_count"])
        self.assertEqual(8192,result["system_ram"]["total_ram_mb"])
        self.assertEqual(1700,result["system_ram"]["available_ram_mb"])
        self.assertNotIn("C:\\",json.dumps(result))
        self.assertNotIn("password",json.dumps(result).lower())

    def test_unavailable_ollama_yields_an_action_instead_of_fake_models(self):
        def down():raise AgentError("local_model_unavailable")
        result=preflight("fixture:model",catalog=down,memory=lambda:RAM,
                         system="Windows")
        self.assertEqual("OLLAMA_NOT_READY",result["status"])
        self.assertEqual("local_model_unavailable",result["error"])
        self.assertFalse(result["ollama_reachable"])
        self.assertFalse(result["live_model_inference_attempted"])
        self.assertEqual([],result["installed_models"])
        self.assertIn("127.0.0.1:11434",result["next_action"])

    def test_empty_or_missing_model_does_not_attempt_live_inference(self):
        no_models=diagnose("fixture:model",run_smoke=True,catalog=lambda:[],
                           memory=lambda:RAM,system="Windows")
        self.assertEqual("NO_MODELS_INSTALLED",no_models["status"])
        self.assertEqual("NOT_ATTEMPTED",no_models["smoke_status"])
        missing=diagnose("other:model",run_smoke=True,
                         catalog=lambda:["fixture:model"],memory=lambda:RAM,
                         system="Windows")
        self.assertEqual("MODEL_NOT_INSTALLED",missing["status"])
        self.assertEqual("NOT_ATTEMPTED",missing["smoke_status"])
        self.assertFalse(missing["live_model_inference_attempted"])

    def test_low_available_ram_warning_is_not_model_fit_prediction(self):
        low={**RAM,"available_ram_mb":650}
        report=preflight("fixture:model",catalog=lambda:["fixture:model"],
                         memory=lambda:low,system="Windows")
        self.assertEqual("READY_FOR_EXPLICIT_SMOKE",report["status"])
        self.assertTrue(report["selected_model_installed"])
        self.assertIn("snapshot",report["memory_note"])
        self.assertIn("not a model-specific",report["resource_warning"])
        self.assertFalse(report["live_model_inference_attempted"])

    def test_catalog_is_validated_even_if_test_callback_supplies_it(self):
        for raw in (None,{},["../secret"],["fixture:model"]*65,["bad name"]):
            with self.subTest(raw=str(raw)[:30]):
                report=preflight(catalog=lambda raw=raw:raw,memory=lambda:RAM)
                self.assertEqual("OLLAMA_NOT_READY",report["status"])
                self.assertEqual("invalid_model_catalog",report["error"])
        with self.assertRaisesRegex(AgentError,"invalid_model_name"):
            preflight("../../bad",catalog=lambda:[],memory=lambda:RAM)

    def test_explicit_synthetic_callback_never_claims_real_windows_result(self):
        seen=[]
        def fake(model):
            seen.append(model)
            return {"status":"SYNTHETIC_MODEL_SMOKE_PASSED",
                    "inference_source":"INJECTED_TEST_TRANSPORT",
                    "model":model,"source_evidence_verified":True,
                    "elapsed_ms":12,"history_saved":False,
                    "external_write_count":0,"citations":[]}
        report=diagnose("fixture:model",run_smoke=True,
                        catalog=lambda:["fixture:model"],memory=lambda:RAM,
                        system="Windows",live_smoke=fake)
        self.assertEqual(["fixture:model"],seen)
        self.assertEqual("SYNTHETIC_DEVICE_SMOKE_PASSED",report["status"])
        self.assertFalse(report["live_model_inference_attempted"])
        self.assertEqual("INJECTED_TEST_TRANSPORT",report["inference_source"])
        self.assertEqual(0,report["external_write_count"])

    def test_failed_live_smoke_is_reported_as_attempted(self):
        def timed_out(model):raise AgentError("local_model_timeout")
        report=diagnose("fixture:model",run_smoke=True,
                        catalog=lambda:["fixture:model"],memory=lambda:RAM,
                        system="Windows",live_smoke=timed_out)
        self.assertEqual("DEVICE_SMOKE_BLOCKED",report["status"])
        self.assertEqual("BLOCKED",report["smoke_status"])
        self.assertEqual("local_model_timeout",report["error"])
        self.assertFalse(report["live_model_inference_attempted"])
        self.assertIn("Do not retry",report["next_action"])

    def test_numeric_evidence_failure_preserves_precise_device_diagnostic(self):
        def unsupported(model):
            raise LocalDocumentError("multi_document_numeric_evidence_missing")
        result=diagnose("fixture:model",run_smoke=True,
                        catalog=lambda:["fixture:model"],memory=lambda:RAM,
                        system="Windows",live_smoke=unsupported)
        self.assertEqual("DEVICE_SMOKE_BLOCKED",result["status"])
        self.assertEqual("multi_document_numeric_evidence_missing",result["error"])
        self.assertIn("number or time",result["next_action"])
        self.assertNotEqual("local_model_unavailable",result["error"])

    def test_uncertain_connection_loss_has_safe_actionable_diagnostic(self):
        def interrupted(model):
            raise LocalDocumentError("local_model_transport_uncertain")
        result=diagnose("fixture:model",run_smoke=True,
                        catalog=lambda:["fixture:model"],memory=lambda:RAM,
                        system="Windows",live_smoke=interrupted)
        self.assertEqual("DEVICE_SMOKE_BLOCKED",result["status"])
        self.assertEqual("local_model_transport_uncertain",result["error"])
        self.assertIn("Inspect the backend",result["next_action"])
        self.assertNotEqual("REAL_DEVICE_SMOKE_PASSED",result["status"])

    def test_cli_without_run_smoke_never_invokes_model(self):
        import local_windows_preflight as pre
        with patch.object(pre,"preflight",return_value={
                "status":"MODEL_SELECTION_REQUIRED"}) as read, patch.object(
                pre,"smoke") as actual, patch("builtins.print"):
            self.assertEqual(0,pre.main([]))
        read.assert_called_once()
        actual.assert_not_called()
        with self.assertRaises(SystemExit) as error:
            pre.main(["--run-smoke"])
        self.assertNotEqual(0,error.exception.code)


if __name__=="__main__":
    unittest.main()
