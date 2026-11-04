"""Integrated durable mock flow tests: restart, failure isolation, no auto-replay."""
import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))

from lead_inquiry_flow import MockInquiryFlow
from lead_inquiry_durable_state import DurableMockEventStore


class DurableFlowIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = json.loads((ROOT / "fixtures" / "lead_inquiry_ac01_ac04.json").read_text())
        cls.cases = {c["id"]: c for c in data["cases"]}
        cls.config = cls.cases["AC01"]["input"]["configuration"]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "events.sqlite"
        self.store = DurableMockEventStore(self.path)

    def new_flow(self, store=None):
        return MockInquiryFlow(durable_store=store if store is not None else DurableMockEventStore(self.path))

    @staticmethod
    def event(identity="id-1", **inquiry):
        return {"source_event_id": identity, "inquiry": inquiry}

    def test_complete_inquiry_survives_restart_without_reprocessing(self):
        first = self.new_flow().process(self.config, self.event(service_requested="website"),
                                        ["service_requested"])
        self.assertEqual("READY_FOR_REVIEW", first["workflow_status"])
        self.assertEqual("MOCK_COMPLETED", self.store.state("mock_inbox", "id-1").replace("COMPLETED", "MOCK_COMPLETED"))
        second = self.new_flow().process(self.config, self.event(service_requested="website"),
                                         ["service_requested"])
        self.assertEqual("SKIPPED_DUPLICATE", second["workflow_status"])
        self.assertEqual("DUPLICATE", second["deduplication"]["status"])
        self.assertIsNone(second["qualification"])

    def test_missing_fact_persisted_but_not_inferred(self):
        inquiry = self.cases["AC04"]["input"]["inquiry"]
        first = self.new_flow().process(
            self.config, self.event("ac04", **inquiry),
            self.cases["AC04"]["input"]["required_qualification_fields"]
        )
        self.assertEqual("NEEDS_REVIEW", first["workflow_status"])
        self.assertEqual("UNKNOWN", first["qualification"]["field_assessments"]["budget_range"])
        replay = self.new_flow().process(
            self.config, self.event("ac04", **inquiry),
            self.cases["AC04"]["input"]["required_qualification_fields"]
        )
        self.assertEqual("SKIPPED_DUPLICATE", replay["workflow_status"])

    def test_pending_after_restart_blocks_replay_and_auto_completion(self):
        self.store.reserve("mock_inbox", "pending", {"service_requested": "website"})
        reopened = self.new_flow()
        outcome = reopened.process(self.config, self.event("pending", service_requested="website"),
                                   ["service_requested"])
        self.assertEqual("BLOCKED_RECONCILIATION", outcome["workflow_status"])
        self.assertEqual("NEEDS_RECONCILIATION", outcome["deduplication"]["status"])
        self.assertEqual("PENDING", self.store.state("mock_inbox", "pending"))
        self.assertIsNone(outcome["qualification"])

    def test_conflicting_payload_after_restart_blocked(self):
        self.new_flow().process(self.config, self.event("same", service_requested="website"),
                                ["service_requested"])
        result = self.new_flow().process(self.config, self.event("same", service_requested="marketing"),
                                         ["service_requested"])
        self.assertEqual("BLOCKED_CONFLICT", result["workflow_status"])

    def test_bad_configuration_does_not_reserve_event(self):
        invalid = self.cases["AC02"]["input"]["configuration"]
        blocked = self.new_flow().process(invalid, self.event(service_requested="web"), ["service_requested"])
        self.assertEqual("BLOCKED", blocked["workflow_status"])
        self.assertEqual("NOT_FOUND", self.store.state("mock_inbox", "id-1"))

    def test_bad_required_fields_do_not_reserve_event(self):
        blocked = self.new_flow().process(self.config, self.event(service_requested="web"), ["../../secret"])
        self.assertEqual("BLOCKED", blocked["workflow_status"])
        self.assertEqual("NOT_FOUND", self.store.state("mock_inbox", "id-1"))

    def test_live_mode_rejected_without_persistence(self):
        blocked = self.new_flow().process(self.config, self.event(service_requested="web"),
                                          ["service_requested"], mode="live")
        self.assertEqual("BLOCKED", blocked["workflow_status"])
        self.assertEqual("NOT_FOUND", self.store.state("mock_inbox", "id-1"))

    def test_invalid_event_id_never_reserved(self):
        blocked = self.new_flow().process(self.config, self.event("  ", service_requested="web"),
                                          ["service_requested"])
        self.assertEqual("BLOCKED", blocked["workflow_status"])
        self.assertEqual("INVALID_ID", blocked["deduplication"]["status"])

    def test_no_private_payload_or_secret_in_outputs_and_storage(self):
        secret = "SUPER_PRIVATE_TEST_SENTINEL"
        cfg = copy.deepcopy(self.config)
        cfg["api_key"] = secret
        result = self.new_flow().process(cfg, self.event(service_requested="web", message=secret),
                                         ["service_requested"])
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn(secret.encode(), self.path.read_bytes())

    def test_storage_failure_cannot_fallback_to_memory(self):
        class BrokenStore:
            def reserve(self, *_):
                raise sqlite3.OperationalError("secret/path/database damaged")
            def mark_mock_completed(self, *_):
                raise AssertionError("must not reach completion")
        result = self.new_flow(BrokenStore()).process(
            self.config, self.event(service_requested="web"), ["service_requested"])
        self.assertEqual("BLOCKED", result["workflow_status"])
        self.assertEqual("STORAGE_UNAVAILABLE", result["deduplication"]["status"])
        self.assertNotIn("secret/path", json.dumps(result))

    def test_completion_failure_leaves_uncertain_outcome_blocked(self):
        store = self.store
        class IncompleteStore:
            def reserve(self, source, identity, payload):
                return store.reserve(source, identity, payload)
            def mark_mock_completed(self, *_):
                raise sqlite3.OperationalError("disk inaccessible")
        result = self.new_flow(IncompleteStore()).process(
            self.config, self.event(service_requested="web"), ["service_requested"])
        self.assertEqual("BLOCKED", result["workflow_status"])
        self.assertEqual("STORAGE_UNAVAILABLE", result["deduplication"]["status"])
        self.assertEqual("PENDING", self.store.state("mock_inbox", "id-1"))
        replay = self.new_flow().process(
            self.config, self.event(service_requested="web"), ["service_requested"])
        self.assertEqual("BLOCKED_RECONCILIATION", replay["workflow_status"])

    def test_no_external_dispatch_and_default_flow_unchanged(self):
        durable = self.new_flow().process(
            self.config, self.event(service_requested="web"), ["service_requested"])
        ephemeral = MockInquiryFlow().process(
            self.config, self.event(service_requested="web"), ["service_requested"])
        self.assertEqual(0, durable["external_dispatch_count"])
        self.assertEqual(0, ephemeral["external_dispatch_count"])
        self.assertEqual("READY_FOR_REVIEW", durable["workflow_status"])
        self.assertEqual("FIRST_SEEN", ephemeral["deduplication"]["status"])

    def test_capacity_exhaustion_blocks_without_fallback(self):
        limited = DurableMockEventStore(self.path, max_events=1)
        flow = MockInquiryFlow(durable_store=limited)
        first = flow.process(self.config, self.event("first", service_requested="a"), ["service_requested"])
        second = flow.process(self.config, self.event("second", service_requested="b"), ["service_requested"])
        self.assertEqual("READY_FOR_REVIEW", first["workflow_status"])
        self.assertEqual("BLOCKED", second["workflow_status"])
        self.assertEqual("CAPACITY_EXHAUSTED", second["deduplication"]["status"])

    def test_invalid_store_interface_rejected(self):
        with self.assertRaises(TypeError):
            MockInquiryFlow(durable_store=object())


if __name__ == "__main__":
    unittest.main()
