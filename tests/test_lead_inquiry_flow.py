"""Integrated mock flow tests against the committed synthetic fixtures."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from lead_inquiry_flow import MockInquiryFlow


class IntegratedFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cases = json.loads((ROOT / "fixtures" / "lead_inquiry_ac01_ac04.json").read_text())["cases"]
        cls.cases = {case["id"]: case for case in cases}
        cls.valid_config = cls.cases["AC01"]["input"]["configuration"]

    def event(self, event_id="event-1", **inquiry):
        return {"source_event_id": event_id, "inquiry": inquiry}

    def test_valid_setup_and_complete_inquiry(self):
        result = MockInquiryFlow().process(self.valid_config, self.event(service_requested="website", budget_range="small"),
                                           ["service_requested", "budget_range"])
        self.assertEqual("READY_FOR_REVIEW", result["workflow_status"])
        self.assertEqual("DRAFT_VALID", result["validation"]["activation_status"])
        self.assertEqual("FIRST_SEEN", result["deduplication"]["status"])
        self.assertEqual(0, result["external_dispatch_count"])

    def test_ac02_bad_configuration_blocks_before_reservation(self):
        flow = MockInquiryFlow()
        bad = self.cases["AC02"]["input"]["configuration"]
        event = self.event(service_requested="website")
        blocked = flow.process(bad, event, ["service_requested"])
        self.assertEqual("BLOCKED", blocked["workflow_status"])
        self.assertIsNone(blocked["deduplication"])
        retry = flow.process(self.valid_config, event, ["service_requested"])
        self.assertEqual("FIRST_SEEN", retry["deduplication"]["status"])

    def test_ac03_duplicate_fixture_in_integrated_path(self):
        flow = MockInquiryFlow()
        case = self.cases["AC03"]["input"]
        statuses = []
        for record in case["events"]:
            result = flow.process(self.valid_config,
                self.event(record["source_event_id"], inquiry_id=record["inquiry_id"]),
                ["inquiry_id"])
            statuses.append(result["workflow_status"])
        self.assertEqual(["READY_FOR_REVIEW", "SKIPPED_DUPLICATE"], statuses)

    def test_ac04_missing_field_matches_fixture_expectations(self):
        case = self.cases["AC04"]
        payload = dict(case["input"]["inquiry"])
        event_id = payload.pop("source_event_id")
        result = MockInquiryFlow().process(self.valid_config, self.event(event_id, **payload),
                                            case["input"]["required_qualification_fields"])
        self.assertEqual("NEEDS_REVIEW", result["workflow_status"])
        self.assertEqual(case["expected"]["field_assessments"], result["qualification"]["field_assessments"])
        self.assertEqual([], result["qualification"]["fabricated_verified_fields"])

    def test_conflicting_same_id_is_blocked(self):
        flow = MockInquiryFlow()
        flow.process(self.valid_config, self.event("same", service_requested="website"), ["service_requested"])
        other = flow.process(self.valid_config, self.event("same", service_requested="marketing"), ["service_requested"])
        self.assertEqual("BLOCKED_CONFLICT", other["workflow_status"])
        self.assertIsNone(other["qualification"])

    def test_invalid_field_rules_do_not_reserve_event_id(self):
        flow = MockInquiryFlow()
        event = self.event("same", service_requested="website")
        invalid = flow.process(self.valid_config, event, ["../../secret"])
        self.assertEqual("BLOCKED", invalid["workflow_status"])
        good = flow.process(self.valid_config, event, ["service_requested"])
        self.assertEqual("FIRST_SEEN", good["deduplication"]["status"])

    def test_invalid_event_structure_fails_closed(self):
        flow = MockInquiryFlow()
        for event in (None, [], {}, {"source_event_id": "x", "inquiry": []}):
            with self.subTest(event=event):
                self.assertEqual("BLOCKED", flow.process(self.valid_config, event, ["service_requested"])["workflow_status"])

    def test_nonmock_mode_rejected(self):
        result = MockInquiryFlow().process(self.valid_config, self.event(service_requested="web"),
                                           ["service_requested"], mode="live")
        self.assertEqual("BLOCKED", result["workflow_status"])
        self.assertIsNone(result["deduplication"])

    def test_no_secret_echo_in_output(self):
        secret = "VERY_PRIVATE_TEST_SECRET"
        config = copy.deepcopy(self.valid_config)
        config["api_key"] = secret
        result = MockInquiryFlow().process(config, self.event(service_requested="web", private_note=secret),
                                           ["service_requested"])
        self.assertNotIn(secret, json.dumps(result))

    def test_dedup_capacity_exhausted_blocks_next_event(self):
        flow = MockInquiryFlow(max_events=1)
        flow.process(self.valid_config, self.event("first", service_requested="web"), ["service_requested"])
        result = flow.process(self.valid_config, self.event("second", service_requested="web"), ["service_requested"])
        self.assertEqual("BLOCKED", result["workflow_status"])
        self.assertEqual("CAPACITY_EXHAUSTED", result["deduplication"]["status"])

    def test_no_side_effect_on_all_paths(self):
        flow = MockInquiryFlow()
        results = [
            flow.process(self.valid_config, self.event("1", service_requested="web"), ["service_requested"]),
            flow.process(self.valid_config, self.event("1", service_requested="web"), ["service_requested"]),
            flow.process(self.valid_config, self.event("1", service_requested="other"), ["service_requested"]),
            flow.process({}, self.event("2", service_requested="web"), ["service_requested"]),
        ]
        self.assertTrue(all(x["external_dispatch_count"] == 0 for x in results))


if __name__ == "__main__":
    unittest.main()
