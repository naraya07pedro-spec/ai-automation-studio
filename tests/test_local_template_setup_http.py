"""HTTP acceptance of template availability and guided setup, mock-only."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_server import create_handler
sys.path.insert(0, str(ROOT / "prototype"))
from lead_inquiry_template_catalog import list_templates, default_setup, validate_guided_setup


class LocalTemplateSetupHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler())
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, route, payload=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        body = json.dumps(payload) if payload is not None else None
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        conn.request(method, route, body, h)
        response = conn.getresponse()
        code = response.status
        raw = response.read()
        conn.close()
        return code, json.loads(raw) if route != "/" else raw.decode()

    def sample(self, **changes):
        return {"event_id": "fixture-a", "service_requested": "Mock automation",
                "budget_range": "Mock budget", "timeline": "Next month", **changes}

    def test_catalog_two_executable_three_preview(self):
        status, catalog = self.request("GET", "/api/templates")
        self.assertEqual(200, status)
        self.assertEqual(2, catalog["executable_count"])
        self.assertEqual(3, catalog["preview_count"])
        self.assertEqual(5, len(catalog["templates"]))
        self.assertEqual(["EXECUTABLE_MOCK", "PREVIEW_NOT_EXECUTABLE", "EXECUTABLE_MOCK"] + ["PREVIEW_NOT_EXECUTABLE"] * 2,
                         [v["status"] for v in catalog["templates"]])

    def test_catalog_returns_detached_metadata(self):
        catalog = list_templates()
        catalog["templates"][0]["status"] = "PREVIEW_NOT_EXECUTABLE"
        self.assertEqual("EXECUTABLE_MOCK", list_templates()["templates"][0]["status"])

    def test_guided_choices_are_server_defined(self):
        status, data = self.request("GET", "/api/setup-choices")
        self.assertEqual(200, status)
        self.assertEqual("lead-inquiry", data["template_id"])
        self.assertEqual(["mock_inbox", "mock_web_form"], data["fields"]["source"])
        self.assertEqual(["mock_crm", "mock_sheet"], data["fields"]["destination"])

    def test_default_guided_setup_validated(self):
        status, result = self.request("POST", "/api/mock-setup", default_setup())
        self.assertEqual(200, status)
        self.assertEqual("DRAFT_VALID", result["setup_status"])
        self.assertEqual([], result["errors"])
        self.assertEqual(0, result["external_dispatch_count"])

    def test_setup_does_not_echo_fields_or_secret(self):
        bad = default_setup()
        bad["setup"]["source"] = "MY_PRIVATE_SECRET"
        status, result = self.request("POST", "/api/mock-setup", bad)
        self.assertEqual(422, status)
        self.assertEqual("BLOCKED", result["setup_status"])
        self.assertNotIn("MY_PRIVATE_SECRET", json.dumps(result))

    def test_preview_template_setup_cannot_be_validated(self):
        bad = default_setup()
        bad["template_id"] = "crm-handoff"
        status, result = self.request("POST", "/api/mock-setup", bad)
        self.assertEqual(422, status)
        self.assertIn("template_not_executable", result["errors"])

    def test_preview_template_cannot_be_run(self):
        bad = self.sample(**default_setup())
        bad["template_id"] = "knowledge-routing"
        status, result = self.request("POST", "/api/mock-run", bad)
        self.assertEqual(422, status)
        self.assertEqual("invalid_mock_setup", result["error"])

    def test_valid_config_runs_real_mock_engine(self):
        payload = self.sample(**default_setup())
        status, out = self.request("POST", "/api/mock-run", payload)
        self.assertEqual(200, status)
        self.assertEqual("DRAFT_VALID", out["setup_status"])
        self.assertEqual("READY_FOR_REVIEW", out["result"]["workflow_status"])
        self.assertEqual(0, out["external_dispatch_count"])

    def test_alternative_mock_adapter_is_allowlisted(self):
        selection = default_setup()
        selection["setup"]["source"] = "mock_web_form"
        selection["setup"]["destination"] = "mock_sheet"
        selection["setup"]["timezone"] = "UTC"
        status, out = self.request("POST", "/api/mock-run", self.sample(**selection))
        self.assertEqual(200, status)
        self.assertEqual("FIRST_SEEN", out["result"]["deduplication"]["status"])

    def test_draft_only_makes_draft_without_review_token(self):
        selection = default_setup()
        selection["setup"]["follow_up_policy"] = "draft_only"
        status, out = self.request("POST", "/api/mock-run", self.sample(**selection))
        self.assertEqual(200, status)
        self.assertEqual("DRAFT_ONLY_NO_REVIEW", out["review_offer"]["status"])
        self.assertIsNone(out["review_offer"]["review_token"])
        self.assertEqual("NEVER_SENT", out["review_offer"]["draft"]["delivery"])
        self.assertEqual("DRAFT_ONLY_NO_REVIEW", out["timeline"][5]["status"])

    def test_approval_policy_keeps_local_token(self):
        status, out = self.request("POST", "/api/mock-run",
                                   self.sample(**default_setup()))
        self.assertEqual(200, status)
        self.assertEqual("AWAITING_LOCAL_REVIEW", out["review_offer"]["status"])
        self.assertIsInstance(out["review_offer"]["review_token"], str)

    def test_bogus_ai_mode_and_live_destination_rejected(self):
        for field, value in (("ai_mode", "openai"), ("destination", "live_crm"),
                             ("source", "http://external.example")):
            with self.subTest(field=field):
                selection = default_setup()
                selection["setup"][field] = value
                status, out = self.request("POST", "/api/mock-run",
                                           self.sample(**selection))
                self.assertEqual(422, status)
                self.assertEqual("invalid_mock_setup", out["error"])

    def test_budget_boundaries_and_boolean_budget(self):
        for invalid in (-1, 1000001, True, 0.5):
            with self.subTest(amount=invalid):
                selection = default_setup()
                selection["setup"]["monthly_budget_idr"] = invalid
                self.assertEqual("BLOCKED", validate_guided_setup(selection)["status"])
        selection = default_setup()
        selection["setup"]["monthly_budget_idr"] = 1000000
        self.assertEqual("DRAFT_VALID", validate_guided_setup(selection)["status"])

    def test_unknown_setup_keys_rejected(self):
        selection = default_setup()
        selection["setup"]["api_key"] = "SECRET_VALUE"
        status, out = self.request("POST", "/api/mock-setup", selection)
        self.assertEqual(422, status)
        self.assertNotIn("SECRET_VALUE", json.dumps(out))

    def test_setup_shape_rejected_before_event_reservation(self):
        payload = self.sample(**default_setup())
        payload["setup"] = None
        status, out = self.request("POST", "/api/mock-run", payload)
        self.assertEqual(422, status)
        self.assertEqual("invalid_mock_setup", out["error"])
        payload["setup"] = default_setup()["setup"]
        status, out = self.request("POST", "/api/mock-run", payload)
        self.assertEqual(200, status)
        self.assertEqual("FIRST_SEEN", out["result"]["deduplication"]["status"])

    def test_invalid_origin_blocks_guided_setup(self):
        status, out = self.request("POST", "/api/mock-setup", default_setup(),
                                   headers={"Origin": "https://evil.example"})
        self.assertEqual(403, status)
        self.assertEqual("same_origin_required", out["error"])

    def test_html_contains_gallery_and_setup_gate(self):
        status, html = self.request("GET", "/")
        self.assertEqual(200, status)
        for marker in ('id="gallery"', 'id="setup-form"', 'id="validate-setup"',
                       '/api/templates', '/api/mock-setup', 'id="run" type="submit" disabled'):
            self.assertIn(marker, html)


if __name__ == "__main__":
    unittest.main()
