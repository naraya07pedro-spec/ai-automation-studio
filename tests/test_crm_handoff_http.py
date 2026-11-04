"""HTTP and engine acceptance for synthetic CRM handoff, no CRM writes."""
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
from crm_handoff_flow import MockCRMHandoff


class CRMHandoffHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, payload, *, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        conn.request("POST", "/api/mock-crm-handoff", json.dumps(payload), headers)
        resp = conn.getresponse()
        code = resp.status
        data = json.loads(resp.read())
        conn.close()
        return code, data

    @staticmethod
    def sample(**changes):
        payload = {"event_id": "crm-mock-001", "lead_stage": "qualified",
                   "service_code": "automation", "owner_queue": "sales",
                   "mapping_profile": "standard", "review_acknowledged": True}
        payload.update(changes)
        return payload

    def test_standard_mapping_produces_only_synthetic_record_preview(self):
        code, out = self.post(self.sample())
        self.assertEqual(200, code)
        self.assertEqual("SIMULATED_HANDOFF_PREPARED", out["status"])
        self.assertEqual({"crm_lifecycle_stage": "qualified",
                          "crm_service_category": "automation",
                          "crm_owner_queue": "sales"}, out["record_preview"])
        self.assertEqual("standard", out["mapping_profile"])
        self.assertFalse(out["crm_record_created"])
        self.assertEqual(0, out["external_dispatch_count"])

    def test_compact_mapping_changes_field_names_not_real_destination(self):
        _, out = self.post(self.sample(mapping_profile="compact"))
        self.assertEqual({"stage": "qualified", "service": "automation",
                          "queue": "sales"}, out["record_preview"])
        self.assertFalse(out["crm_record_created"])

    def test_no_review_acknowledgement_blocks_record_preparation(self):
        _, out = self.post(self.sample(review_acknowledged=False))
        self.assertEqual("REVIEW_REQUIRED_NO_RECORD", out["status"])
        self.assertIsNone(out["record_preview"])
        self.assertEqual("MOCK_REVIEW_NOT_ACKNOWLEDGED", out["review_gate"])

    def test_missing_review_does_not_reserve_event_id(self):
        self.post(self.sample(review_acknowledged=False))
        _, out = self.post(self.sample())
        self.assertEqual("FIRST_SEEN", out["deduplication"])

    def test_mock_acknowledgement_is_only_a_caller_claim(self):
        _, out = self.post(self.sample())
        self.assertEqual("CALLER_CLAIMED_MOCK_ACK", out["review_gate"])
        self.assertEqual("DISABLED", out["timeline"][-1]["status"])

    def test_duplicate_does_not_generate_second_record(self):
        self.post(self.sample())
        _, out = self.post(self.sample())
        self.assertEqual("SKIPPED_DUPLICATE", out["status"])
        self.assertEqual("DUPLICATE", out["deduplication"])
        self.assertIsNone(out["record_preview"])

    def test_same_id_different_mapping_conflicts(self):
        self.post(self.sample())
        _, out = self.post(self.sample(mapping_profile="compact"))
        self.assertEqual("BLOCKED_CONFLICT", out["status"])
        self.assertEqual("CONFLICT", out["deduplication"])

    def test_same_id_different_service_conflicts(self):
        self.post(self.sample())
        _, out = self.post(self.sample(service_code="consulting"))
        self.assertEqual("BLOCKED_CONFLICT", out["status"])

    def test_invalid_value_cannot_reserve_identifier(self):
        code, out = self.post(self.sample(service_code="secret"))
        self.assertEqual(422, code)
        self.assertEqual("BLOCKED", out["status"])
        _, good = self.post(self.sample())
        self.assertEqual("FIRST_SEEN", good["deduplication"])

    def test_rejects_arbitrary_personal_data_fields(self):
        code, out = self.post(self.sample(email="private@example.test"))
        self.assertEqual(422, code)
        self.assertEqual("BLOCKED", out["status"])
        self.assertNotIn("private@example.test", json.dumps(out))

    def test_rejects_unrecognized_live_crm_mapping(self):
        code, out = self.post(self.sample(mapping_profile="hubspot_live"))
        self.assertEqual(422, code)
        self.assertIsNone(out["record_preview"])

    def test_rejects_nonboolean_review_claim(self):
        code, out = self.post(self.sample(review_acknowledged="true"))
        self.assertEqual(422, code)
        self.assertEqual("BLOCKED", out["status"])

    def test_rejects_bad_identifiers_and_missing_fields(self):
        for value in ("", "x y", "../private", "🔥", "a" * 81):
            with self.subTest(value=value):
                code, out = self.post(self.sample(event_id=value))
                self.assertEqual(422, code)
                self.assertEqual("BLOCKED", out["status"])
        payload = self.sample()
        payload.pop("owner_queue")
        code, _ = self.post(payload)
        self.assertEqual(422, code)

    def test_local_capacity_is_bounded(self):
        engine = MockCRMHandoff(max_events=1)
        self.assertEqual("SIMULATED_HANDOFF_PREPARED",
                         engine.process(self.sample())["status"])
        out = engine.process(self.sample(event_id="another"))
        self.assertEqual("BLOCKED_CAPACITY", out["status"])
        self.assertIsNone(out["record_preview"])

    def test_timeline_redacts_event_identifier(self):
        _, out = self.post(self.sample())
        self.assertEqual(["INTAKE", "FIELD_VALIDATE", "LOCAL_REVIEW_GATE",
                          "DEDUP", "FIELD_MAP", "CRM_WRITE"],
                         [step["stage"] for step in out["timeline"]])
        self.assertEqual("DISABLED", out["timeline"][-1]["status"])
        self.assertNotIn("crm-mock-001", json.dumps(out))

    def test_foreign_origin_is_denied(self):
        code, out = self.post(self.sample(), origin="https://evil.example")
        self.assertEqual(403, code)
        self.assertEqual("same_origin_required", out["error"])

    def test_catalog_five_executable_and_zero_preview(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.request("GET", "/api/templates")
        catalog = json.loads(conn.getresponse().read())
        conn.close()
        self.assertEqual(5, catalog["executable_count"])
        self.assertEqual(0, catalog["preview_count"])
        row = next(x for x in catalog["templates"] if x["id"] == "crm-handoff")
        self.assertEqual("EXECUTABLE_MOCK", row["status"])
        self.assertEqual("MOCK_ONLY", row["mode"])

    def test_ui_exposes_crm_form_and_api(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.request("GET", "/")
        html = conn.getresponse().read().decode()
        conn.close()
        for marker in ('id="crm-form"', 'id="crm-record"',
                       'id="crm-timeline"', '"/api/mock-crm-handoff"'):
            self.assertIn(marker, html)

    def test_independent_from_support_triage_dedup(self):
        self.post(self.sample(event_id="shared-001"))
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.request("POST", "/api/mock-triage", json.dumps({
            "event_id": "shared-001", "category": "billing",
            "severity": "low", "customer_impact": False}),
            {"Content-Type": "application/json"})
        out = json.loads(conn.getresponse().read())
        conn.close()
        self.assertEqual("FIRST_SEEN", out["deduplication"])


if __name__ == "__main__":
    unittest.main()
