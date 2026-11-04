"""HTTP acceptance for third executable mock appointment follow-up workflow."""
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
from appointment_followup_flow import MockAppointmentFollowup


class AppointmentFollowupHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler())
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, value, *, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        conn.request("POST", "/api/mock-appointment", json.dumps(value), headers)
        response = conn.getresponse()
        status = response.status
        content = json.loads(response.read())
        conn.close()
        return status, content

    @staticmethod
    def sample(**changes):
        result = {"event_id": "appt-mock-001",
                  "as_of": "2026-11-04T09:00:00+07:00",
                  "appointment_at": "2026-11-05T09:00:00+07:00",
                  "channel": "mock_email"}
        result.update(changes)
        return result

    def test_24_hour_boundary_creates_mock_reminder_plan(self):
        status, out = self.post(self.sample())
        self.assertEqual(200, status)
        self.assertEqual("PLANNED_MOCK_ONLY", out["status"])
        self.assertEqual("WITHIN_24_HOURS", out["window"])
        self.assertEqual("PREPARED_REMINDER_MOCK_ONLY", out["followup_plan"])
        self.assertEqual("FIRST_SEEN", out["deduplication"])
        self.assertEqual(0, out["external_dispatch_count"])
        self.assertIs(out["message_sent"], False)
        self.assertIs(out["calendar_event_created"], False)

    def test_future_appointment_produces_deferred_draft_only(self):
        _, out = self.post(self.sample(appointment_at="2026-11-11T09:00:00+07:00"))
        self.assertEqual("UPCOMING_30_DAYS", out["window"])
        self.assertEqual("DEFERRED_DRAFT_MOCK_ONLY", out["followup_plan"])

    def test_past_appointment_requires_manual_review_without_send(self):
        _, out = self.post(self.sample(appointment_at="2026-11-03T09:00:00+07:00"))
        self.assertEqual("PAST_APPOINTMENT", out["window"])
        self.assertEqual("MANUAL_REVIEW_NO_SEND", out["followup_plan"])

    def test_zero_delta_still_simulates_no_send(self):
        _, out = self.post(self.sample(appointment_at="2026-11-04T09:00:00+07:00"))
        self.assertEqual("WITHIN_24_HOURS", out["window"])
        self.assertIs(out["message_sent"], False)

    def test_cross_timezone_reference_is_compared_as_instant(self):
        _, out = self.post(self.sample(
            as_of="2026-11-04T02:00:00Z",
            appointment_at="2026-11-05T09:00:00+07:00"))
        self.assertEqual("WITHIN_24_HOURS", out["window"])

    def test_30_day_boundary_is_allowed(self):
        _, out = self.post(self.sample(appointment_at="2026-12-04T09:00:00+07:00"))
        self.assertEqual("PLANNED_MOCK_ONLY", out["status"])
        self.assertEqual("UPCOMING_30_DAYS", out["window"])

    def test_more_than_30_days_blocks_before_reserving_id(self):
        code, out = self.post(self.sample(appointment_at="2026-12-05T09:00:00+07:00"))
        self.assertEqual(422, code)
        self.assertEqual("BLOCKED", out["status"])
        _, valid = self.post(self.sample())
        self.assertEqual("FIRST_SEEN", valid["deduplication"])

    def test_more_than_seven_days_past_is_blocked(self):
        code, out = self.post(self.sample(appointment_at="2026-10-26T09:00:00+07:00"))
        self.assertEqual(422, code)
        self.assertEqual("BLOCKED", out["status"])

    def test_same_request_is_duplicate_with_no_plan(self):
        self.post(self.sample())
        _, repeated = self.post(self.sample())
        self.assertEqual("SKIPPED_DUPLICATE", repeated["status"])
        self.assertEqual("DUPLICATE", repeated["deduplication"])
        self.assertEqual("NOT_PREPARED", repeated["followup_plan"])

    def test_same_id_changed_appointment_is_conflict(self):
        self.post(self.sample())
        _, changed = self.post(self.sample(appointment_at="2026-11-06T09:00:00+07:00"))
        self.assertEqual("BLOCKED_CONFLICT", changed["status"])
        self.assertEqual("CONFLICT", changed["deduplication"])

    def test_same_instant_different_offset_is_duplicate(self):
        self.post(self.sample())
        _, out = self.post(self.sample(
            as_of="2026-11-04T02:00:00Z",
            appointment_at="2026-11-05T02:00:00Z"))
        self.assertEqual("SKIPPED_DUPLICATE", out["status"])

    def test_invalid_timestamp_or_missing_timezone_rejected(self):
        for value in ("2026-11-04T09:00:00", "2026-13-04T09:00:00+07:00",
                      "yesterday", "2026-11-04T09:00:00+15:00"):
            with self.subTest(value=value):
                status, out = self.post(self.sample(as_of=value))
                self.assertEqual(422, status)
                self.assertEqual("BLOCKED", out["status"])

    def test_input_without_fixed_fields_or_with_extra_content_rejected(self):
        samples = [dict(self.sample(), message="customer secret"), {"event_id": "appt"}]
        for value in samples:
            with self.subTest(keys=list(value)):
                status, out = self.post(value)
                self.assertEqual(422, status)
                self.assertEqual("BLOCKED", out["status"])
                self.assertNotIn("customer secret", json.dumps(out))

    def test_invalid_id_and_live_channel_rejected(self):
        for patch in ({"event_id": "../secret"}, {"event_id": "a b"},
                      {"channel": "real_email"}, {"channel": 123}, {"event_id": True}):
            with self.subTest(patch=patch):
                status, out = self.post(self.sample(**patch))
                self.assertEqual(422, status)
                self.assertEqual("BLOCKED", out["status"])

    def test_local_capacity_is_bounded(self):
        engine = MockAppointmentFollowup(max_events=1)
        self.assertEqual("PLANNED_MOCK_ONLY", engine.process(self.sample())["status"])
        self.assertEqual("BLOCKED_CAPACITY", engine.process(
            self.sample(event_id="appt-2"))["status"])

    def test_wrong_origin_is_denied(self):
        status, out = self.post(self.sample(), origin="https://untrusted.example")
        self.assertEqual(403, status)
        self.assertEqual("same_origin_required", out["error"])

    def test_six_stage_timeline_and_no_sensitive_identifiers(self):
        _, out = self.post(self.sample())
        self.assertEqual(["INTAKE", "DATE_VALIDATE", "DEDUP", "WINDOW_ASSESS",
                          "DRAFT_PLAN", "EXTERNAL_DISPATCH"],
                         [stage["stage"] for stage in out["timeline"]])
        self.assertEqual("DISABLED", out["timeline"][-1]["status"])
        self.assertNotIn("appt-mock-001", json.dumps(out))
        self.assertNotIn("2026-11-05", json.dumps(out))

    def test_gallery_marks_appointment_as_third_executable(self):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        client.request("GET", "/api/templates")
        response = client.getresponse()
        catalog = json.loads(response.read())
        client.close()
        self.assertEqual(3, catalog["executable_count"])
        self.assertEqual(2, catalog["preview_count"])
        row = next(x for x in catalog["templates"] if x["id"] == "appointment-followup")
        self.assertEqual("EXECUTABLE_MOCK", row["status"])

    def test_independent_event_register_from_support_triage(self):
        self.post(self.sample(event_id="shared-001"))
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        client.request("POST", "/api/mock-triage", json.dumps({
            "event_id": "shared-001", "category": "access",
            "severity": "low", "customer_impact": False}),
            {"Content-Type": "application/json"})
        response = client.getresponse()
        out = json.loads(response.read())
        client.close()
        self.assertEqual(200, response.status)
        self.assertEqual("FIRST_SEEN", out["deduplication"])

    def test_html_exposes_appointment_form_and_real_api(self):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        client.request("GET", "/")
        response = client.getresponse()
        html = response.read().decode()
        client.close()
        for text in ('id="appointment-form"', 'id="appointment-timeline"',
                     '"/api/mock-appointment"', 'id="appointment-panel"'):
            self.assertIn(text, html)


if __name__ == "__main__":
    unittest.main()
