"""HTTP integration tests for canned mock draft, redacted timeline and local review."""
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


class LocalDraftReviewHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler())
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, route, payload, headers=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        supplied = {"Content-Type": "application/json"}
        if headers:
            supplied.update(headers)
        client.request("POST", route, json.dumps(payload), supplied)
        response = client.getresponse()
        status = response.status
        data = json.loads(response.read())
        client.close()
        return status, data

    def inquiry(self, **changes):
        return {"event_id": "synthetic-001", "service_requested": "SENSITIVE_SERVICE",
                "budget_range": "Budget mock", "timeline": "Tomorrow", **changes}

    def run_first(self):
        status, data = self.post("/api/mock-run", self.inquiry())
        self.assertEqual(200, status)
        return data

    def test_timeline_is_seven_server_derived_stages(self):
        data = self.run_first()
        stages = [item["stage"] for item in data["timeline"]]
        self.assertEqual(["INTAKE", "CONFIG_VALIDATE", "DEDUP", "QUALIFICATION",
                          "DRAFT", "REVIEW", "EXTERNAL_DISPATCH"], stages)
        self.assertEqual("DISABLED", data["timeline"][-1]["status"])
        self.assertEqual("PREPARED_MOCK_ONLY", data["timeline"][4]["status"])
        self.assertEqual("AWAITING_LOCAL_REVIEW", data["timeline"][5]["status"])

    def test_draft_contains_only_fixed_mock_words(self):
        data = self.run_first()
        offer = data["review_offer"]
        self.assertEqual("AWAITING_LOCAL_REVIEW", offer["status"])
        self.assertEqual("NEVER_SENT", offer["draft"]["delivery"])
        self.assertEqual("NOT_SET", offer["draft"]["recipient"])
        self.assertTrue(offer["draft"]["requires_verified_identity"])
        self.assertNotIn("SENSITIVE_SERVICE", json.dumps(data))

    def test_acknowledge_consumes_token_with_no_send(self):
        token = self.run_first()["review_offer"]["review_token"]
        status, data = self.post("/api/mock-review",
                                 {"review_token": token, "decision": "ACKNOWLEDGE"})
        self.assertEqual(200, status)
        self.assertEqual("ACKNOWLEDGED_LOCAL_ONLY", data["review"]["status"])
        self.assertEqual(0, data["external_dispatch_count"])
        self.assertFalse(data["review"]["event_state_changed"])
        self.assertNotIn(token, json.dumps(data))

    def test_reject_consumes_token_with_no_send(self):
        token = self.run_first()["review_offer"]["review_token"]
        _, data = self.post("/api/mock-review",
                            {"review_token": token, "decision": "REJECT"})
        self.assertEqual("REJECTED_LOCAL_ONLY", data["review"]["status"])
        self.assertEqual(0, data["review"]["external_dispatch_count"])

    def test_token_cannot_be_consumed_twice(self):
        token = self.run_first()["review_offer"]["review_token"]
        self.post("/api/mock-review", {"review_token": token, "decision": "ACKNOWLEDGE"})
        _, repeated = self.post("/api/mock-review",
                                {"review_token": token, "decision": "REJECT"})
        self.assertEqual("TOKEN_NOT_FOUND_OR_CONSUMED", repeated["review"]["status"])

    def test_duplicate_has_no_new_draft_or_review_token(self):
        self.run_first()
        _, repeat = self.post("/api/mock-run", self.inquiry())
        self.assertEqual("SKIPPED_DUPLICATE", repeat["result"]["workflow_status"])
        self.assertEqual("NOT_AVAILABLE", repeat["review_offer"]["status"])
        self.assertIsNone(repeat["review_offer"]["review_token"])
        self.assertEqual("NOT_CREATED", repeat["timeline"][4]["status"])

    def test_conflict_has_no_new_draft(self):
        self.run_first()
        _, changed = self.post("/api/mock-run", self.inquiry(timeline="Changed"))
        self.assertEqual("BLOCKED_CONFLICT", changed["result"]["workflow_status"])
        self.assertIsNone(changed["review_offer"]["draft"])

    def test_unrecognized_review_decision_fails_closed(self):
        token = self.run_first()["review_offer"]["review_token"]
        _, bad = self.post("/api/mock-review",
                           {"review_token": token, "decision": "SEND"})
        self.assertEqual("INVALID_REVIEW_REQUEST", bad["review"]["status"])
        _, valid = self.post("/api/mock-review",
                             {"review_token": token, "decision": "ACKNOWLEDGE"})
        self.assertEqual("ACKNOWLEDGED_LOCAL_ONLY", valid["review"]["status"])

    def test_malformed_review_payload_rejected(self):
        code, result = self.post("/api/mock-review", {"decision": "ACKNOWLEDGE"})
        self.assertEqual(422, code)
        self.assertEqual("invalid_review_request", result["error"])

    def test_foreign_origin_denied_before_processing(self):
        status, out = self.post("/api/mock-run", self.inquiry(),
                                {"Origin": "https://untrusted.example"})
        self.assertEqual(403, status)
        self.assertEqual("same_origin_required", out["error"])

    def test_host_header_rebinding_denied(self):
        status, out = self.post("/api/mock-run", self.inquiry(),
                                {"Host": "evil.example"})
        self.assertEqual(403, status)
        self.assertEqual("local_host_required", out["error"])

    def test_html_has_review_controls_and_stage_rendering(self):
        c = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        c.request("GET", "/")
        reply = c.getresponse()
        html = reply.read().decode()
        c.close()
        self.assertIn('id="timeline"', html)
        self.assertIn('id="draft"', html)
        self.assertIn('id="ack"', html)
        self.assertIn('id="reject"', html)
        self.assertIn('/api/mock-review', html)


if __name__ == "__main__":
    unittest.main()
