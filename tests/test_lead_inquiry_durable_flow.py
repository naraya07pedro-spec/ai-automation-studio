"""Integration checks for optional durable mock-event state in MockInquiryFlow."""
import concurrent.futures
import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from durable_mock_state import DurableMockEventStore
from lead_inquiry_flow import MockInquiryFlow


class DurableFlowIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "events.sqlite"
        cases = json.loads((ROOT / "fixtures" / "lead_inquiry_ac01_ac04.json").read_text())["cases"]
        self.config = next(c for c in cases if c["id"] == "AC01")["input"]["configuration"]

    def flow(self):
        return MockInquiryFlow(durable_store=DurableMockEventStore(self.database))

    @staticmethod
    def event(event_id="event-001", **fields):
        return {"source_event_id": event_id, "inquiry": fields}

    def test_first_event_recorded_and_result(self):
        result = self.flow().process(self.config, self.event(service_requested="web"), ["service_requested"])
        self.assertEqual("READY_FOR_REVIEW", result["workflow_status"])
        self.assertEqual("RECORDED", result["deduplication"]["status"])

    def test_new_instance_replays_as_duplicate(self):
        first = self.flow().process(self.config, self.event(service_requested="web"), ["service_requested"])
        again = self.flow().process(self.config, self.event(service_requested="web"), ["service_requested"])
        self.assertEqual("READY_FOR_REVIEW", first["workflow_status"])
        self.assertEqual("SKIPPED_DUPLICATE", again["workflow_status"])
        self.assertIsNone(again["qualification"])
        self.assertEqual("READY_FOR_REVIEW", again["deduplication"]["decision"])

    def test_conflict_after_restart_is_blocked(self):
        self.flow().process(self.config, self.event(service_requested="web"), ["service_requested"])
        r = self.flow().process(self.config, self.event(service_requested="crm"), ["service_requested"])
        self.assertEqual("BLOCKED_CONFLICT", r["workflow_status"])
        self.assertEqual("CONFLICT", r["deduplication"]["status"])

    def test_incomplete_inquiry_decision_survives_restart(self):
        first = self.flow().process(self.config, self.event(service_requested="web", budget_range=None),
                                    ["service_requested", "budget_range"])
        second = self.flow().process(self.config, self.event(service_requested="web", budget_range=None),
                                     ["service_requested", "budget_range"])
        self.assertEqual("NEEDS_REVIEW", first["workflow_status"])
        self.assertEqual("SKIPPED_DUPLICATE", second["workflow_status"])
        self.assertEqual("NEEDS_REVIEW", second["deduplication"]["decision"])

    def test_invalid_config_does_not_reserve_event(self):
        broken = copy.deepcopy(self.config)
        broken["mapping"] = None
        self.assertEqual("BLOCKED", self.flow().process(broken, self.event(service_requested="web"),
                                                       ["service_requested"])["workflow_status"])
        self.assertEqual("RECORDED", self.flow().process(self.config, self.event(service_requested="web"),
                                                        ["service_requested"])["deduplication"]["status"])

    def test_invalid_qualification_does_not_reserve_event(self):
        self.assertEqual("BLOCKED", self.flow().process(self.config, self.event(service_requested="web"),
                                                       ["../invalid"])["workflow_status"])
        result = self.flow().process(self.config, self.event(service_requested="web"),
                                     ["service_requested"])
        self.assertEqual("RECORDED", result["deduplication"]["status"])

    def test_nonmock_mode_cannot_record(self):
        result = self.flow().process(self.config, self.event(service_requested="web"),
                                     ["service_requested"], mode="live")
        self.assertEqual("BLOCKED", result["workflow_status"])
        self.assertEqual("RECORDED", self.flow().process(self.config, self.event(service_requested="web"),
                                                        ["service_requested"])["deduplication"]["status"])

    def test_store_error_fails_closed_no_volatile_fallback(self):
        store = DurableMockEventStore(self.database)
        flow = MockInquiryFlow(durable_store=store)
        with patch.object(store, "record", side_effect=sqlite3.OperationalError("secret internal DB path")):
            result = flow.process(self.config, self.event(service_requested="web"),
                                  ["service_requested"])
        self.assertEqual("STORAGE_UNAVAILABLE", result["workflow_status"])
        self.assertIsNone(result["deduplication"])
        self.assertNotIn("secret internal DB path", str(result))
        self.assertEqual(0, result["external_dispatch_count"])

    def test_unrelated_os_error_fails_closed(self):
        store = DurableMockEventStore(self.database)
        with patch.object(store, "record", side_effect=OSError("private path")):
            result = MockInquiryFlow(durable_store=store).process(
                self.config, self.event(service_requested="web"), ["service_requested"])
        self.assertEqual("STORAGE_UNAVAILABLE", result["workflow_status"])
        self.assertNotIn("private path", str(result))

    def test_100_parallel_new_flow_instances_record_once(self):
        def process(_):
            return self.flow().process(self.config, self.event(service_requested="web"),
                                       ["service_requested"])["workflow_status"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            statuses = list(pool.map(process, range(100)))
        self.assertEqual(1, statuses.count("READY_FOR_REVIEW"))
        self.assertEqual(99, statuses.count("SKIPPED_DUPLICATE"))

    def test_no_private_payload_echo_and_no_dispatch(self):
        secret = "DO_NOT_REVEAL_EXAMPLE_PRIVATE_DATA"
        first = self.flow().process(self.config,
                                    self.event(service_requested="web", private_note=secret),
                                    ["service_requested"])
        replay = self.flow().process(self.config,
                                     self.event(service_requested="web", private_note=secret),
                                     ["service_requested"])
        for result in (first, replay):
            self.assertNotIn(secret, str(result))
            self.assertEqual(0, result["external_dispatch_count"])

    def test_default_in_memory_mode_remains_unchanged(self):
        f = MockInquiryFlow()
        e = self.event(service_requested="web")
        a = f.process(self.config, e, ["service_requested"])
        b = f.process(self.config, e, ["service_requested"])
        self.assertEqual("FIRST_SEEN", a["deduplication"]["status"])
        self.assertEqual("SKIPPED_DUPLICATE", b["workflow_status"])

    def test_wrong_store_type_is_rejected(self):
        with self.assertRaises(TypeError):
            MockInquiryFlow(durable_store=object())


if __name__ == "__main__":
    unittest.main()
