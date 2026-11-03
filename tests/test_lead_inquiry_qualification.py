"""AC04 executable checks, no external side effects."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from lead_inquiry_qualification import classify_inquiry


class QualificationTests(unittest.TestCase):
    def test_ac04_fixture_exact(self):
        data = json.loads((ROOT / "fixtures" / "lead_inquiry_ac01_ac04.json").read_text())
        case = next(c for c in data["cases"] if c["id"] == "AC04")
        actual = classify_inquiry(case["input"]["inquiry"], case["input"]["required_qualification_fields"], mode=case["input"]["mode"])
        self.assertEqual(actual, case["expected"])

    def test_complete_fields_still_require_review(self):
        out = classify_inquiry({"service_requested": "website", "budget_range": "small"}, ["service_requested", "budget_range"])
        self.assertEqual(out["qualification_status"], "READY_FOR_REVIEW")

    def test_blank_field_unknown(self):
        self.assertEqual(classify_inquiry({"budget_range": "  "}, ["budget_range"])["field_assessments"]["budget_range"], "UNKNOWN")

    def test_missing_field_does_not_parse_message(self):
        result = classify_inquiry({"message": "Budget definitely 100 million"}, ["budget_range"])
        self.assertEqual(result["field_assessments"]["budget_range"], "UNKNOWN")

    def test_nonmock_mode_blocked(self):
        self.assertEqual(classify_inquiry({}, ["name"], mode="live")["error"], "mock_only")

    def test_invalid_inquiry_blocked(self):
        self.assertEqual(classify_inquiry(["bad"], ["name"])["error"], "invalid_input")

    def test_empty_fields_blocked(self):
        self.assertEqual(classify_inquiry({}, [])["error"], "invalid_required_fields")

    def test_duplicate_fields_blocked(self):
        self.assertEqual(classify_inquiry({}, ["name", "name"])["error"], "invalid_required_fields")

    def test_complex_field_unknown(self):
        self.assertEqual(classify_inquiry({"name": {"untrusted": "nested"}}, ["name"])["field_assessments"]["name"], "UNKNOWN")

    def test_secret_not_echoed(self):
        secret = "EXAMPLE_SECRET_DO_NOT_RETURN"
        out = classify_inquiry({"service_requested": "website", "api_key": secret}, ["service_requested"])
        self.assertNotIn(secret, json.dumps(out))

    def test_no_external_dispatch(self):
        self.assertEqual(classify_inquiry({"name": "a"}, ["name"])["external_dispatch_count"], 0)

    def test_invalid_names_blocked(self):
        self.assertEqual(classify_inquiry({}, ["../../api_key"])["error"], "invalid_required_fields")


if __name__ == "__main__":
    unittest.main()
