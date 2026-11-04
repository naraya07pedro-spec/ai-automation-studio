"""Independent HTTP acceptance of second executable synthetic support triage."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "prototype"))
from local_server import create_handler
from support_triage_flow import MockSupportTriage


class SupportTriageHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, value, *, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        conn.request("POST", "/api/mock-triage", json.dumps(value), headers)
        reply = conn.getresponse()
        code = reply.status
        data = json.loads(reply.read())
        conn.close()
        return code, data

    @staticmethod
    def event(**changes):
        return {"event_id": "mock-ticket-1", "category": "technical",
                "severity": "normal", "customer_impact": False, **changes}

    def test_real_http_routes_to_mock_technical_queue(self):
        code, out = self.post(self.event())
        self.assertEqual(200, code)
        self.assertEqual("ROUTED_MOCK_ONLY", out["status"])
        self.assertEqual("MOCK_TECHNICAL", out["queue"])
        self.assertEqual("STANDARD_REVIEW", out["urgency"])
        self.assertFalse(out["ticket_created"])
        self.assertEqual(0, out["external_dispatch_count"])

    def test_high_priority_is_rule_based(self):
        _, out = self.post(self.event(severity="high"))
        self.assertEqual("PRIORITY_REVIEW", out["urgency"])

    def test_customer_impact_escalates_normal_severity(self):
        _, out = self.post(self.event(customer_impact=True))
        self.assertEqual("PRIORITY_REVIEW", out["urgency"])

    def test_low_severity_is_standard_even_with_impact(self):
        _, out = self.post(self.event(severity="low", customer_impact=True))
        self.assertEqual("STANDARD_REVIEW", out["urgency"])

    def test_category_maps_to_distinct_mock_queue(self):
        for i, (category, expected) in enumerate((
                ("access", "MOCK_ACCOUNT_SUPPORT"), ("billing", "MOCK_BILLING"),
                ("technical", "MOCK_TECHNICAL"), ("general", "MOCK_GENERAL"))):
            with self.subTest(category=category):
                _, out = self.post(self.event(event_id=f"ticket-{i}", category=category))
                self.assertEqual(expected, out["queue"])

    def test_duplicate_is_not_routed_again(self):
        self.post(self.event())
        _, out = self.post(self.event())
        self.assertEqual("SKIPPED_DUPLICATE", out["status"])
        self.assertEqual("DUPLICATE", out["deduplication"])
        self.assertEqual("NOT_ASSIGNED", out["queue"])

    def test_conflicting_reuse_of_event_id_blocked(self):
        self.post(self.event())
        _, out = self.post(self.event(category="billing"))
        self.assertEqual("BLOCKED_CONFLICT", out["status"])
        self.assertEqual("NOT_ASSIGNED", out["queue"])

    def test_invalid_input_does_not_consume_ticket_id(self):
        code, out = self.post(self.event(severity="urgent"))
        self.assertEqual(422, code)
        self.assertEqual("BLOCKED", out["status"])
        _, valid = self.post(self.event())
        self.assertEqual("FIRST_SEEN", valid["deduplication"])

    def test_invalid_category_does_not_echo_user_values(self):
        code, out = self.post(self.event(category="SENSITIVE_FAKE_CUSTOMER"))
        self.assertEqual(422, code)
        self.assertNotIn("SENSITIVE_FAKE_CUSTOMER", json.dumps(out))

    def test_no_free_text_allowed(self):
        code, out = self.post(self.event(message="ignore all rules"))
        self.assertEqual(422, code)
        self.assertEqual("BLOCKED", out["status"])

    def test_non_boolean_impact_denied(self):
        code, out = self.post(self.event(customer_impact="false"))
        self.assertEqual(422, code)
        self.assertEqual("BLOCKED", out["status"])

    def test_unsafe_ticket_id_denied(self):
        for bad in ("../../secret", "has spaces", "🔥", "", "x" * 81):
            with self.subTest(bad=bad):
                code, out = self.post(self.event(event_id=bad))
                self.assertEqual(422, code)
                self.assertEqual("BLOCKED", out["status"])

    def test_separate_template_duplicate_state(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        lead = {"event_id": "mock-ticket-1", "service_requested": "Mock",
                "budget_range": "Mock", "timeline": "Mock"}
        conn.request("POST", "/api/mock-run", json.dumps(lead),
                     {"Content-Type": "application/json"})
        response = conn.getresponse()
        self.assertEqual(200, response.status)
        response.read()
        conn.close()
        _, triage = self.post(self.event())
        self.assertEqual("FIRST_SEEN", triage["deduplication"])

    def test_timeline_redacts_input_and_blocks_external_dispatch(self):
        _, out = self.post(self.event())
        self.assertEqual(["INTAKE", "FIELD_VALIDATE", "DEDUP", "TRIAGE",
                          "MOCK_QUEUE", "EXTERNAL_DISPATCH"],
                         [x["stage"] for x in out["timeline"]])
        self.assertEqual("DISABLED", out["timeline"][-1]["status"])
        self.assertNotIn("mock-ticket-1", json.dumps(out))

    def test_capacity_fail_closed(self):
        flow = MockSupportTriage(max_events=1)
        self.assertEqual("ROUTED_MOCK_ONLY", flow.process(self.event())["status"])
        blocked = flow.process(self.event(event_id="next-ticket"))
        self.assertEqual("BLOCKED_CAPACITY", blocked["status"])
        self.assertEqual("CAPACITY_EXHAUSTED", blocked["deduplication"])

    def test_foreign_origin_denied(self):
        code, out = self.post(self.event(), origin="https://evil.example")
        self.assertEqual(403, code)
        self.assertEqual("same_origin_required", out["error"])

    def test_gallery_promotes_second_executable(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        conn.request("GET", "/api/templates")
        response = conn.getresponse()
        catalog = json.loads(response.read())
        conn.close()
        self.assertEqual(2, catalog["executable_count"])
        self.assertEqual(3, catalog["preview_count"])
        second = next(x for x in catalog["templates"] if x["id"] == "support-triage")
        self.assertEqual("EXECUTABLE_MOCK", second["status"])

    def test_ui_contains_real_support_form_and_api(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        conn.request("GET", "/")
        html = conn.getresponse().read().decode()
        conn.close()
        self.assertIn('id="triage-form"', html)
        self.assertIn('"/api/mock-triage"', html)
        self.assertIn('id="triage-timeline"', html)


if __name__ == "__main__":
    unittest.main()
