"""Real, isolated unit tests of AC01/AC02 behavior and fail-closed edges."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from lead_inquiry_validation import validate_configuration  # noqa: E402

FIXTURES = ROOT / "fixtures" / "lead_inquiry_ac01_ac04.json"


class LeadInquiryValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cases = json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"]
        cls.fixtures = {item["id"]: item for item in cases}

    def test_ac01_valid_configuration_matches_fixture(self):
        case = self.fixtures["AC01"]
        actual = validate_configuration(case["input"]["configuration"], mode=case["input"]["mode"])
        self.assertEqual(case["expected"], actual)

    def test_ac02_missing_mapping_matches_fixture(self):
        case = self.fixtures["AC02"]
        actual = validate_configuration(case["input"]["configuration"], mode=case["input"]["mode"])
        for key in ("activation_status", "validation_errors", "external_dispatch_count"):
            self.assertEqual(case["expected"][key], actual[key])
        self.assertFalse(case["expected"]["secrets_in_output"])

    def test_live_mode_rejected_without_dispatch(self):
        config = self.fixtures["AC01"]["input"]["configuration"]
        self.assertEqual({"activation_status": "BLOCKED", "validation_errors": ["mock_only"], "external_dispatch_count": 0}, validate_configuration(config, mode="live"))

    def test_invalid_configuration_type_is_rejected(self):
        result = validate_configuration(["not a mapping"])
        self.assertEqual("BLOCKED", result["activation_status"])
        self.assertEqual(["configuration_invalid"], result["validation_errors"])

    def test_invalid_mapping_is_rejected(self):
        config = copy.deepcopy(self.fixtures["AC01"]["input"]["configuration"])
        config["mapping"]["inquiry_id"] = "  "
        self.assertIn("mapping_invalid", validate_configuration(config)["validation_errors"])

    def test_live_connector_value_cannot_activate(self):
        config = copy.deepcopy(self.fixtures["AC01"]["input"]["configuration"])
        config["destination"] = "real_crm"
        self.assertIn("destination_unsupported", validate_configuration(config)["validation_errors"])

    def test_secret_cannot_leak_in_results(self):
        config = copy.deepcopy(self.fixtures["AC01"]["input"]["configuration"])
        config["api_key"] = "SUPER_SECRET_NEVER_RETURN_THIS"
        result = validate_configuration(config)
        self.assertNotIn(config["api_key"], json.dumps(result))
        self.assertEqual("DRAFT_VALID", result["activation_status"])

    def test_negative_budget_is_rejected(self):
        config = copy.deepcopy(self.fixtures["AC01"]["input"]["configuration"])
        config["monthly_budget_idr"] = -1
        self.assertIn("budget_invalid", validate_configuration(config)["validation_errors"])


if __name__ == "__main__":
    unittest.main()
