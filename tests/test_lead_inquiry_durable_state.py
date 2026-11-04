"""Durable mock event store tests: restart, conflicts, transaction races, fail-closed."""
import concurrent.futures
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from lead_inquiry_durable_state import DurableMockEventStore


class DurableMockStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "mock_events.sqlite"
        self.store = DurableMockEventStore(self.path)

    def reserve(self, event_id="event-1", payload=None, source="mock_inbox"):
        return self.store.reserve(source, event_id, {} if payload is None else payload)["status"]

    def test_initial_reservation_is_pending(self):
        self.assertEqual("RESERVED_PENDING", self.reserve())
        self.assertEqual("PENDING", self.store.state("mock_inbox", "event-1"))

    def test_pending_survives_reopen_and_requires_reconciliation(self):
        self.reserve()
        restarted = DurableMockEventStore(self.path)
        self.assertEqual("PENDING", restarted.state("mock_inbox", "event-1"))
        self.assertEqual("NEEDS_RECONCILIATION", restarted.reserve("mock_inbox", "event-1", {})["status"])

    def test_completed_survives_restart(self):
        self.reserve()
        self.assertEqual("MOCK_COMPLETED", self.store.mark_mock_completed("mock_inbox", "event-1")["status"])
        restarted = DurableMockEventStore(self.path)
        self.assertEqual("COMPLETED", restarted.state("mock_inbox", "event-1"))
        self.assertEqual("DUPLICATE", restarted.reserve("mock_inbox", "event-1", {})["status"])

    def test_conflicting_payload_is_not_accepted(self):
        self.reserve(payload={"a": 1})
        self.assertEqual("CONFLICT", self.reserve(payload={"a": 2}))

    def test_duplicate_keys_scoped_by_source(self):
        self.assertEqual("RESERVED_PENDING", self.reserve(source="mock_a"))
        self.assertEqual("RESERVED_PENDING", self.reserve(source="mock_b"))

    def test_missing_completion_cannot_create_record(self):
        self.assertEqual("NOT_FOUND", self.store.mark_mock_completed("mock_inbox", "missing")["status"])

    def test_repeated_completion_is_idempotent(self):
        self.reserve()
        self.store.mark_mock_completed("mock_inbox", "event-1")
        self.assertEqual("ALREADY_COMPLETED", self.store.mark_mock_completed("mock_inbox", "event-1")["status"])

    def test_capacity_exhaustion_does_not_evict(self):
        limited = DurableMockEventStore(self.path, max_events=1)
        self.assertEqual("RESERVED_PENDING", limited.reserve("mock_inbox", "a", {})["status"])
        self.assertEqual("CAPACITY_EXHAUSTED", limited.reserve("mock_inbox", "b", {})["status"])
        self.assertEqual("NEEDS_RECONCILIATION", limited.reserve("mock_inbox", "a", {})["status"])

    def test_atomic_concurrent_reservation_from_distinct_instances(self):
        def submit(_):
            instance = DurableMockEventStore(self.path)
            return instance.reserve("mock_inbox", "shared", {"same": True})["status"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit, range(40)))
        self.assertEqual(1, results.count("RESERVED_PENDING"))
        self.assertEqual(39, results.count("NEEDS_RECONCILIATION"))

    def test_reject_live_source_and_invalid_id(self):
        self.assertEqual("MOCK_ONLY", self.reserve(source="gmail"))
        self.assertEqual("INVALID_ID", self.reserve(event_id=" "))
        self.assertEqual("INVALID_ID", self.store.mark_mock_completed("gmail", "x")["status"])

    def test_invalid_payload_and_oversize(self):
        self.assertEqual("INVALID_PAYLOAD", self.store.reserve("mock_inbox", "x", ["not a dict"])["status"])
        self.assertEqual("INVALID_PAYLOAD", self.store.reserve("mock_inbox", "x", {"bad": object()})["status"])
        self.assertEqual("PAYLOAD_TOO_LARGE", self.store.reserve("mock_inbox", "x", {"blob": "x" * 262145})["status"])

    def test_canonical_key_order_same_digest(self):
        self.assertEqual("RESERVED_PENDING", self.reserve(payload={"a": 1, "b": 2}))
        self.assertEqual("NEEDS_RECONCILIATION", self.reserve(payload={"b": 2, "a": 1}))

    def test_ac03_fixture_persistent_duplicate(self):
        fixture = json.loads((ROOT / "fixtures" / "lead_inquiry_ac01_ac04.json").read_text())
        case = next(c for c in fixture["cases"] if c["id"] == "AC03")
        events = case["input"]["events"]
        source = case["input"]["source"]
        results = []
        for event in events:
            results.append(self.store.reserve(source, event["source_event_id"],
                                              {"inquiry_id": event["inquiry_id"]})["status"])
        self.assertEqual(["RESERVED_PENDING", "NEEDS_RECONCILIATION"], results)
        self.assertEqual(case["expected"]["distinct_logical_events"], results.count("RESERVED_PENDING"))
        self.assertEqual(case["expected"]["duplicate_event_count"], results.count("NEEDS_RECONCILIATION"))

    def test_zero_external_dispatch_on_all_paths(self):
        results = [
            self.store.reserve("mock_inbox", "a", {}),
            self.store.reserve("mock_inbox", "a", {}),
            self.store.reserve("mock_inbox", "a", {"changed": 1}),
            self.store.mark_mock_completed("mock_inbox", "a"),
            self.store.reserve("mock_inbox", "a", {}),
        ]
        self.assertTrue(all(r["external_dispatch_count"] == 0 for r in results))

    def test_file_path_must_be_persistent(self):
        with self.assertRaises(ValueError):
            DurableMockEventStore(":memory:")
        with self.assertRaises(ValueError):
            DurableMockEventStore(self.tmp.name)


if __name__ == "__main__":
    unittest.main()
