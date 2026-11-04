"""HTTP and engine checks for metadata-only knowledge/document routing mock."""
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
from knowledge_document_routing_flow import MockKnowledgeRouting


class KnowledgeRoutingHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler())
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join, 3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, payload, *, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        conn.request("POST", "/api/mock-knowledge-routing", json.dumps(payload), headers)
        reply = conn.getresponse()
        code = reply.status
        data = json.loads(reply.read())
        conn.close()
        return code, data

    @staticmethod
    def sample(**changes):
        value = {"event_id": "doc-synthetic-001", "document_kind": "technical_guide",
                 "classification": "public", "destination": "mock_knowledge_base",
                 "review_acknowledged": False}
        value.update(changes)
        return value

    def test_public_guide_prepares_metadata_only_preview(self):
        status, out = self.post(self.sample())
        self.assertEqual(200, status)
        self.assertEqual("ROUTING_PREVIEW_PREPARED", out["status"])
        self.assertEqual({"document_kind": "technical_guide",
                          "classification": "public",
                          "mock_destination": "mock_knowledge_base"},
                         out["routing_preview"])
        self.assertEqual("FIRST_SEEN", out["deduplication"])
        self.assertFalse(out["document_uploaded"])
        self.assertFalse(out["document_transferred"])
        self.assertEqual(0, out["external_dispatch_count"])

    def test_all_categories_have_fixed_route(self):
        for index, (kind, destination) in enumerate((
            ("policy", "mock_knowledge_base"),
            ("invoice", "mock_finance_queue"),
            ("technical_guide", "mock_knowledge_base"),
            ("contract", "mock_legal_queue"),
        )):
            with self.subTest(kind=kind):
                _, out = self.post(self.sample(event_id=f"doc-{index}",
                        document_kind=kind, destination=destination))
                self.assertEqual(destination, out["destination"])
                self.assertEqual("ROUTING_PREVIEW_PREPARED", out["status"])

    def test_restricted_document_is_always_blocked_even_if_mock_review_true(self):
        for claimed in (True, False):
            with self.subTest(claimed=claimed):
                _, out = self.post(self.sample(classification="restricted",
                                               review_acknowledged=claimed))
                self.assertEqual("RESTRICTED_NO_ROUTE", out["status"])
                self.assertIsNone(out["routing_preview"])
                self.assertEqual("NOT_ASSIGNED", out["destination"])

    def test_restricted_event_does_not_reserve_identifier(self):
        self.post(self.sample(classification="restricted"))
        _, out = self.post(self.sample())
        self.assertEqual("FIRST_SEEN", out["deduplication"])

    def test_internal_without_review_claim_has_no_route(self):
        _, out = self.post(self.sample(classification="internal"))
        self.assertEqual("REVIEW_REQUIRED_NO_ROUTE", out["status"])
        self.assertIsNone(out["routing_preview"])

    def test_internal_claimed_review_is_not_verified_approval(self):
        _, out = self.post(self.sample(classification="internal",
                                       review_acknowledged=True))
        self.assertEqual("ROUTING_PREVIEW_PREPARED", out["status"])
        self.assertEqual("POLICY_CHECK_PASSED_MOCK", out["policy_decision"])
        self.assertFalse(out["document_transferred"])

    def test_destination_mismatch_blocks_even_with_ack(self):
        _, out = self.post(self.sample(document_kind="invoice",
                                       destination="mock_legal_queue",
                                       review_acknowledged=True))
        self.assertEqual("DESTINATION_NOT_ALLOWED", out["status"])
        self.assertIsNone(out["routing_preview"])

    def test_destination_mismatch_does_not_reserve_id(self):
        self.post(self.sample(destination="mock_finance_queue"))
        _, out = self.post(self.sample())
        self.assertEqual("FIRST_SEEN", out["deduplication"])

    def test_duplicate_prepares_no_second_preview(self):
        self.post(self.sample())
        _, out = self.post(self.sample())
        self.assertEqual("SKIPPED_DUPLICATE", out["status"])
        self.assertEqual("DUPLICATE", out["deduplication"])
        self.assertIsNone(out["routing_preview"])

    def test_conflicting_id_is_blocked(self):
        self.post(self.sample())
        _, out = self.post(self.sample(document_kind="policy"))
        self.assertEqual("BLOCKED_CONFLICT", out["status"])
        self.assertEqual("CONFLICT", out["deduplication"])

    def test_invalid_or_extra_fields_are_refused_without_echo(self):
        for payload in (self.sample(file_bytes="confidential material"),
                        self.sample(credential="abc123"),
                        self.sample(document_kind="private_text"),
                        self.sample(destination="https://evil.example"),
                        self.sample(classification="secret"),
                        self.sample(review_acknowledged="false")):
            with self.subTest(keys=list(payload)):
                status, out = self.post(payload)
                self.assertEqual(422, status)
                self.assertEqual("BLOCKED", out["status"])
                self.assertNotIn("confidential material", json.dumps(out))
                self.assertNotIn("abc123", json.dumps(out))

    def test_invalid_payload_does_not_consume_id(self):
        self.post(self.sample(document_kind="unknown"))
        _, good = self.post(self.sample())
        self.assertEqual("FIRST_SEEN", good["deduplication"])

    def test_invalid_event_ids_are_rejected(self):
        for bad in ("", "has spaces", "../../secrets", "🔥", "x" * 81, True):
            with self.subTest(bad=bad):
                status, out = self.post(self.sample(event_id=bad))
                self.assertEqual(422, status)
                self.assertEqual("BLOCKED", out["status"])

    def test_capacity_fail_closed(self):
        flow = MockKnowledgeRouting(max_events=1)
        self.assertEqual("ROUTING_PREVIEW_PREPARED",
                         flow.process(self.sample())["status"])
        out = flow.process(self.sample(event_id="other"))
        self.assertEqual("BLOCKED_CAPACITY", out["status"])
        self.assertIsNone(out["routing_preview"])

    def test_timeline_six_stages_external_dispatch_disabled(self):
        _, out = self.post(self.sample())
        self.assertEqual(["SYNTHETIC_INTAKE", "FIELD_VALIDATE",
                          "CLASSIFICATION_POLICY", "DEDUP", "ROUTE_PREVIEW",
                          "EXTERNAL_DISPATCH"],
                         [v["stage"] for v in out["timeline"]])
        self.assertEqual("DISABLED", out["timeline"][-1]["status"])

    def test_response_redacts_synthetic_event_id(self):
        _, out = self.post(self.sample())
        self.assertNotIn("doc-synthetic-001", json.dumps(out))

    def test_foreign_origin_is_rejected(self):
        status, out = self.post(self.sample(), origin="https://evil.example")
        self.assertEqual(403, status)
        self.assertEqual("same_origin_required", out["error"])

    def test_catalog_five_executable_zero_preview(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.request("GET", "/api/templates")
        reply = conn.getresponse()
        data = json.loads(reply.read())
        conn.close()
        self.assertEqual(5, data["executable_count"])
        self.assertEqual(0, data["preview_count"])
        self.assertTrue(all(t["status"] == "EXECUTABLE_MOCK"
                            for t in data["templates"]))
        self.assertEqual("MOCK_ONLY", next(
            t for t in data["templates"] if t["id"] == "knowledge-routing")["mode"])

    def test_ui_contains_real_knowledge_form_and_route_inspection(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        conn.request("GET", "/")
        html = conn.getresponse().read().decode()
        conn.close()
        for expected in ('id="knowledge-form"', 'id="knowledge-panel"',
                         'id="knowledge-preview"', 'id="knowledge-timeline"',
                         '"/api/mock-knowledge-routing"'):
            self.assertIn(expected, html)

    def test_dedup_is_independent_of_crm(self):
        self.post(self.sample(event_id="shared-doc"))
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        payload = {"event_id": "shared-doc", "lead_stage": "qualified",
                   "service_code": "automation", "owner_queue": "sales",
                   "mapping_profile": "standard", "review_acknowledged": True}
        conn.request("POST", "/api/mock-crm-handoff", json.dumps(payload),
                     {"Content-Type": "application/json"})
        reply = conn.getresponse()
        out = json.loads(reply.read())
        conn.close()
        self.assertEqual("FIRST_SEEN", out["deduplication"])


if __name__ == "__main__":
    unittest.main()
