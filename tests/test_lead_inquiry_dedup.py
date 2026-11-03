"""Unit tests for AC03 in-memory mock event deduplication."""
import concurrent.futures
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from lead_inquiry_dedup import InMemoryDeduplicator  # noqa: E402


class DedupTests(unittest.TestCase):
    def test_ac03_fixture_same_event_once(self):
        fixture = json.loads((ROOT / "fixtures" / "lead_inquiry_ac01_ac04.json").read_text())
        case = next(c for c in fixture["cases"] if c["id"] == "AC03")
        seen = InMemoryDeduplicator()
        result = [seen.register(case["input"]["source"], e["source_event_id"],
                                {"inquiry_id": e["inquiry_id"]}) for e in case["input"]["events"]]
        self.assertEqual(["FIRST_SEEN", "DUPLICATE"], [r["status"] for r in result])
        self.assertEqual(case["expected"]["distinct_logical_events"], sum(r["status"] == "FIRST_SEEN" for r in result))
        self.assertEqual(case["expected"]["duplicate_event_count"], sum(r["status"] == "DUPLICATE" for r in result))
        self.assertTrue(all(r["external_dispatch_count"] == case["expected"]["external_dispatch_count"] for r in result))
        self.assertEqual(1, case["expected"]["maximum_authorized_dispatches_per_logical_event"])
        self.assertEqual("DUPLICATE_RECORDED", case["expected"]["deduplication_status"])

    def test_conflicting_payload_for_same_id_is_not_accepted(self):
        d = InMemoryDeduplicator()
        self.assertEqual("FIRST_SEEN", d.register("mock_inbox", "id", {"budget": 1})["status"])
        self.assertEqual("CONFLICT", d.register("mock_inbox", "id", {"budget": 2})["status"])

    def test_distinct_events(self):
        d = InMemoryDeduplicator()
        self.assertEqual("FIRST_SEEN", d.register("mock_inbox", "a", {})["status"])
        self.assertEqual("FIRST_SEEN", d.register("mock_inbox", "b", {})["status"])

    def test_distinct_sources_do_not_collide(self):
        d = InMemoryDeduplicator()
        self.assertEqual("FIRST_SEEN", d.register("mock_a", "x", {})["status"])
        self.assertEqual("FIRST_SEEN", d.register("mock_b", "x", {})["status"])

    def test_invalid_event_id_rejected_without_reservation(self):
        d = InMemoryDeduplicator()
        self.assertEqual("INVALID_ID", d.register("mock_a", "  ", {})["status"])
        self.assertEqual("FIRST_SEEN", d.register("mock_a", "valid", {})["status"])

    def test_non_mock_sources_rejected(self):
        d = InMemoryDeduplicator()
        self.assertEqual("MOCK_ONLY", d.register("gmail", "x", {})["status"])

    def test_capacity_exhaustion_fails_closed_without_eviction(self):
        d = InMemoryDeduplicator(max_events=1)
        self.assertEqual("FIRST_SEEN", d.register("mock_a", "a", {})["status"])
        self.assertEqual("CAPACITY_EXHAUSTED", d.register("mock_a", "b", {})["status"])
        self.assertEqual("DUPLICATE", d.register("mock_a", "a", {})["status"])

    def test_bad_payload_rejected(self):
        d = InMemoryDeduplicator()
        self.assertEqual("INVALID_PAYLOAD", d.register("mock_a", "a", [1])["status"])
        self.assertEqual("INVALID_PAYLOAD", d.register("mock_a", "a", {"x": object()})["status"])

    def test_canonical_json_order_is_irrelevant(self):
        d = InMemoryDeduplicator()
        self.assertEqual("FIRST_SEEN", d.register("mock_a", "x", {"a": 1, "b": 2})["status"])
        self.assertEqual("DUPLICATE", d.register("mock_a", "x", {"b": 2, "a": 1})["status"])

    def test_concurrent_same_event_cannot_be_reserved_twice(self):
        d = InMemoryDeduplicator()
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: d.register("mock_a", "same", {"x": 1}), range(100)))
        self.assertEqual(1, sum(x["status"] == "FIRST_SEEN" for x in results))
        self.assertEqual(99, sum(x["status"] == "DUPLICATE" for x in results))

    def test_new_instance_has_no_previous_state(self):
        a, b = InMemoryDeduplicator(), InMemoryDeduplicator()
        self.assertEqual("FIRST_SEEN", a.register("mock_a", "x", {})["status"])
        self.assertEqual("FIRST_SEEN", b.register("mock_a", "x", {})["status"])

    def test_invalid_capacity(self):
        for x in (0, -1, True, "2"):
            with self.subTest(x=x), self.assertRaises(ValueError):
                InMemoryDeduplicator(x)


if __name__ == "__main__":
    unittest.main()
