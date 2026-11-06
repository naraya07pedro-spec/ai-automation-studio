"""Slice 81: fail-closed, bounded evidence-only remote job text triage."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from local_remote_job_eligibility import assess_public_job_language
from local_greenhouse_job_evidence import GreenhouseJobEvidence


def check(description="", location="Remote", title="Automation Engineer"):
    return assess_public_job_language(title=title, location=location, description=description)


class RemoteJobEligibilityEvidenceTests(unittest.TestCase):
    def test_generic_remote_is_not_indonesia_eligibility(self):
        out = check("Remote opportunity with flexible hours.")
        self.assertEqual("REMOTE_MENTIONED_NOT_VERIFIED", out["remote_mode_signal"])
        self.assertEqual("MANUAL_REVIEW_REQUIRED", out["indonesia_candidate_verdict"])
        self.assertEqual("NO_EXPLICIT_LOCATION_CONCLUSION", out["indonesia_work_location_signal"])
        self.assertTrue(out["source_is_public_employer_claim_not_legally_verified"])

    def test_explicit_us_residence_requirement_conflicts_with_indonesia_location(self):
        out = check("Applicants must be based in the United States. Remote within US.")
        self.assertEqual("POSTING_LOCATION_CONFLICT_FOR_INDONESIA", out["indonesia_candidate_verdict"])
        self.assertEqual("EXPLICIT_OTHER_COUNTRY_LOCATION_LIMIT", out["indonesia_work_location_signal"])
        quote = out["evidence"]["location_restrictions"][0]["quote"]
        self.assertIn("must be based in the United States", quote)

    def test_visa_authorization_alone_is_not_geographic_residence_rejection(self):
        out = check("US-only work authorization required. No visa sponsorship is offered.")
        self.assertEqual("MANUAL_REVIEW_REQUIRED", out["indonesia_candidate_verdict"])
        self.assertEqual("NO_EXPLICIT_LOCATION_CONCLUSION", out["indonesia_work_location_signal"])
        self.assertEqual("FOREIGN_AUTHORIZATION_OR_SPONSORSHIP_TERM_FOUND",
                         out["foreign_work_authorization_signal"])
        self.assertTrue(out["evidence"]["foreign_work_authorization"])

    def test_explicit_us_or_indonesia_alternative_is_not_an_exclusion(self):
        out = check("Applicants must be based in the United States or Indonesia.")
        self.assertEqual("MANUAL_REVIEW_REQUIRED", out["indonesia_candidate_verdict"])
        self.assertEqual([], out["evidence"]["location_restrictions"])

    def test_explicit_remote_us_only_hyphen_is_a_location_conflict(self):
        out = check("Remote US-only. We do not recruit from other locations.")
        self.assertEqual("POSTING_LOCATION_CONFLICT_FOR_INDONESIA",
                         out["indonesia_candidate_verdict"])

    def test_indonesia_mentioned_is_not_confirmed_eligible(self):
        out = check("We hire candidates based in Indonesia. Remote work is available.")
        self.assertEqual("INDONESIA_MENTIONED_IN_LOCATION_REQUIREMENT",
                         out["indonesia_work_location_signal"])
        self.assertEqual("MANUAL_REVIEW_REQUIRED", out["indonesia_candidate_verdict"])
        self.assertEqual("NOT_ASSESSED", out["candidate_work_authorization"])

    def test_conflicting_geographic_requirements_are_not_resolved_by_guess(self):
        out = check("Candidates must be based in Canada. We hire candidates based in Indonesia.")
        self.assertEqual("CONFLICTING_LOCATION_TERMS", out["indonesia_work_location_signal"])
        self.assertEqual("MANUAL_REVIEW_REQUIRED", out["indonesia_candidate_verdict"])

    def test_hybrid_remote_conflict_and_seniority_are_reported_not_determined(self):
        out = check("Remote role, hybrid in-office days. At least 4 years of experience required. "
                    "5+ years of experience with REST APIs.")
        self.assertEqual("MIXED_OR_CONFLICTING_MODE_TERMS", out["remote_mode_signal"])
        self.assertEqual("EXPLICIT_YEARS_MENTIONED", out["seniority_signal"])
        self.assertEqual(5, out["seniority_years_mentioned"])
        self.assertEqual("MANUAL_REVIEW_REQUIRED", out["indonesia_candidate_verdict"])

    def test_hidden_html_script_is_not_used_as_evidence(self):
        out = check("<script>Candidates must be based in Canada.</script>"
                    "<p>Fully remote, location is flexible.</p>")
        self.assertFalse(out["evidence"]["location_restrictions"])
        self.assertEqual("MANUAL_REVIEW_REQUIRED", out["indonesia_candidate_verdict"])
        self.assertNotIn("Canada", json.dumps(out))

    def test_location_label_alone_never_implies_legal_eligibility(self):
        out = check("", location="Remote, United States")
        self.assertEqual("MANUAL_REVIEW_REQUIRED", out["indonesia_candidate_verdict"])
        self.assertEqual("REMOTE_MENTIONED_NOT_VERIFIED", out["remote_mode_signal"])
        self.assertFalse(out["evidence"]["location_restrictions"])

    def test_text_limit_explicitly_warns_about_missing_tail(self):
        out = check("An excellent opportunity. " * 4000)
        self.assertTrue(out["description_text_truncated"])
        self.assertLessEqual(out["inspected_description_chars"], 12000)
        self.assertEqual("MANUAL_REVIEW_REQUIRED", out["indonesia_candidate_verdict"])

    def test_bad_inputs_fail_before_interpretation(self):
        for value in (None, 1, True, [], {}):
            with self.subTest(value=str(value)):
                with self.assertRaisesRegex(ValueError, "invalid_public_job_triage_input"):
                    assess_public_job_language(title="Engineer", location="Remote",
                                               description=value)

    def test_existing_greenhouse_preview_has_evidence_but_flags_stay_false(self):
        payload = {
            "id": 111,
            "title": "Integration Engineer",
            "company_name": "Synthetic",
            "location": {"name": "Remote"},
            "content": "<p>Candidates must be based in Canada. 5+ years of experience.</p>",
        }
        out = GreenhouseJobEvidence(fetcher=lambda *_: payload).preview("synthetic", 111)
        triage = out["eligibility_triage"]
        self.assertEqual("POSTING_LOCATION_CONFLICT_FOR_INDONESIA",
                         triage["indonesia_candidate_verdict"])
        self.assertEqual(5, triage["seniority_years_mentioned"])
        self.assertFalse(out["indonesia_eligibility_verified"])
        self.assertFalse(out["remote_work_verified"])
        self.assertFalse(out["accepting_applications_verified"])
        self.assertEqual(1, out["external_read_count"])
        self.assertEqual(0, out["external_write_count"])
        self.assertEqual(0, out["model_calls"])
        self.assertEqual(64, len(out["source_sha256"]))


if __name__ == "__main__":
    unittest.main()
