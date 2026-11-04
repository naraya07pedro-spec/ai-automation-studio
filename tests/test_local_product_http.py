"""Real HTTP smoke tests of the loopback-only mock product UI and API."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_server import create_handler, MAX_BODY


class LocalProductHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, payload=None, *, content_type="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Content-Type": content_type}
        if isinstance(payload, dict):
            payload = json.dumps(payload).encode()
        conn.request(method, path, body=payload, headers=headers)
        reply = conn.getresponse()
        data = reply.read()
        status, headers = reply.status, reply.getheaders()
        conn.close()
        return status, dict(headers), data

    def event(self, **overrides):
        return {"event_id": "demo-001", "service_requested": "Automation",
                "budget_range": "$1k-5k", "timeline": "30 days", **overrides}

    def test_home_serves_real_interactive_app(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(200, status)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"Lead Inquiry Qualification", body)
        self.assertIn(b'fetch("/api/mock-run"', body)
        self.assertIn(b"INSPECT ENGINEERING", body)

    def test_first_valid_event_returns_ready_for_review(self):
        status, _, body = self.request("POST", "/api/mock-run", self.event())
        data = json.loads(body)
        self.assertEqual(200, status)
        self.assertEqual("MOCK_ONLY", data["mode"])
        self.assertEqual("READY_FOR_REVIEW", data["result"]["workflow_status"])
        self.assertEqual("FIRST_SEEN", data["result"]["deduplication"]["status"])
        self.assertEqual(0, data["external_dispatch_count"])

    def test_repeat_event_is_not_processed_twice(self):
        first = json.loads(self.request("POST", "/api/mock-run", self.event())[2])
        duplicate = json.loads(self.request("POST", "/api/mock-run", self.event())[2])
        self.assertEqual("FIRST_SEEN", first["result"]["deduplication"]["status"])
        self.assertEqual("SKIPPED_DUPLICATE", duplicate["result"]["workflow_status"])

    def test_changed_payload_with_same_id_is_conflict(self):
        self.request("POST", "/api/mock-run", self.event())
        modified = json.loads(self.request(
            "POST", "/api/mock-run", self.event(timeline="different"))[2])
        self.assertEqual("BLOCKED_CONFLICT", modified["result"]["workflow_status"])

    def test_missing_required_field_is_rejected(self):
        payload = self.event()
        del payload["timeline"]
        status, _, body = self.request("POST", "/api/mock-run", payload)
        self.assertEqual(422, status)
        self.assertEqual("invalid_synthetic_fields", json.loads(body)["error"])

    def test_blank_field_is_rejected(self):
        status, _, _ = self.request("POST", "/api/mock-run", self.event(timeline=" "))
        self.assertEqual(422, status)

    def test_unknown_field_is_rejected_not_logged(self):
        status, _, body = self.request("POST", "/api/mock-run",
                                      self.event(secret="DO_NOT_LEAK"))
        self.assertEqual(422, status)
        self.assertNotIn(b"DO_NOT_LEAK", body)

    def test_input_larger_than_limit_rejected(self):
        status, _, _ = self.request("POST", "/api/mock-run",
                                     b"x" * (MAX_BODY + 1))
        self.assertEqual(413, status)

    def test_non_json_content_type_rejected(self):
        status, _, _ = self.request("POST", "/api/mock-run",
                                     b"x", content_type="text/plain")
        self.assertEqual(415, status)

    def test_bad_json_is_redacted(self):
        status, _, body = self.request("POST", "/api/mock-run",
                                      b'{"secret":"DO_NOT_LEAK"')
        self.assertEqual(400, status)
        self.assertNotIn(b"DO_NOT_LEAK", body)

    def test_health_is_explicitly_mock_only(self):
        status, _, body = self.request("GET", "/api/health")
        self.assertEqual(200, status)
        self.assertEqual({"status": "MOCK_ONLY", "external_dispatch_count": 0},
                         json.loads(body))

    def test_arbitrary_paths_do_not_serve_files(self):
        status, _, _ = self.request("GET", "/prototype/lead_inquiry_flow.py")
        self.assertEqual(404, status)

    def test_security_headers_and_no_store(self):
        _, headers, _ = self.request("GET", "/")
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertEqual("nosniff", headers["X-Content-Type-Options"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_api_does_not_echo_input_text_or_credentials(self):
        _, _, body = self.request("POST", "/api/mock-run",
                                  self.event(service_requested="SENSITIVE_SYNTHETIC"))
        self.assertNotIn(b"SENSITIVE_SYNTHETIC", body)
        self.assertNotIn(b"source_event_id", body)

    def test_wrong_route_does_not_dispatch(self):
        status, _, body = self.request("POST", "/api/send", self.event())
        self.assertEqual(404, status)
        self.assertNotIn(b"event_id", body)


if __name__ == "__main__":
    unittest.main()
