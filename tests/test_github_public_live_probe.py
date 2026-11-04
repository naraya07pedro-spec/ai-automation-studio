"""Offline tests for the real-network smoke runner's evidence semantics.

Only the dedicated GitHub Actions job performs an actual internet GET.
These unit tests prove the probe does not mistake an empty source for fully
verified labels, echo provider text or persist customer data.
"""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "probe_public_github_live.py"
spec = importlib.util.spec_from_file_location("live_probe", SCRIPT)
live_probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live_probe)

LABELS = [
    {"name": "aas:service:automation"},
    {"name": "aas:budget:3"},
    {"name": "aas:timeline:soon"},
]
VALID = {"number": 102, "state": "open", "labels": LABELS,
         "title": "do not log this title",
         "body": "secret email someone@example.com",
         "user": {"login": "never-log-identity"}}


class LiveProbeEvidenceTests(unittest.TestCase):
    def test_valid_live_response_with_matching_labels_only_verifies_mapping(self):
        code, result = live_probe.probe_public_github(
            "octocat/Hello-World", fetcher=lambda _: [VALID])
        self.assertEqual(0, code)
        self.assertEqual("LIVE_TRANSPORT_VERIFIED", result["status"])
        self.assertEqual("VERIFIED_WITH_LIVE_LABELS", result["label_mapping"])
        self.assertEqual("NOT_ATTEMPTED", result["temporary_import"])
        self.assertEqual(0, result["external_dispatch_count"])
        self.assertEqual(1, result["eligible_first_page"])

    def test_matching_labels_explicit_ephemeral_import_and_history(self):
        code, result = live_probe.probe_public_github(
            "octocat/Hello-World", fetcher=lambda _: [VALID],
            check_ephemeral_import=True)
        self.assertEqual(0, code)
        self.assertEqual("VERIFIED_IN_MEMORY_ONLY", result["temporary_import"])
        self.assertEqual(1, result["temporarily_written_event_count"])

    def test_missing_labels_returns_transport_only(self):
        code, result = live_probe.probe_public_github(
            "octocat/Hello-World",
            fetcher=lambda _: [{"number": 1, "state": "open", "labels": []}])
        self.assertEqual(0, code)
        self.assertEqual("NO_ELIGIBLE_ISSUES_ON_FIRST_PAGE",
                         result["label_mapping"])
        self.assertNotIn("VERIFIED_WITH_LIVE_LABELS", str(result))

    def test_empty_successful_source_verifies_only_transport(self):
        code, result = live_probe.probe_public_github(
            "octocat/Hello-World", fetcher=lambda _: [])
        self.assertEqual(0, code)
        self.assertEqual(0, result["scanned_first_page"])
        self.assertEqual("NO_ELIGIBLE_ISSUES_ON_FIRST_PAGE",
                         result["label_mapping"])

    def test_required_eligible_fails_on_empty_source(self):
        code, result = live_probe.probe_public_github(
            "octocat/Hello-World", fetcher=lambda _: [],
            require_eligible=True)
        self.assertEqual(3, code)
        self.assertEqual("LIVE_MAPPING_NOT_VERIFIED", result["status"])

    def test_ephemeral_import_request_fails_when_zero_eligible(self):
        code, result = live_probe.probe_public_github(
            "octocat/Hello-World", fetcher=lambda _: [],
            check_ephemeral_import=True)
        self.assertEqual(3, code)
        self.assertEqual("NOT_ATTEMPTED", result["temporary_import"])

    def test_invalid_repo_rejected_before_fetch(self):
        calls=[]
        code, result = live_probe.probe_public_github(
            "https://evil.example/whatever", fetcher=lambda r: calls.append(r))
        self.assertEqual(2, code)
        self.assertEqual("NOT_CHECKED", result["live_transport"])
        self.assertEqual([], calls)

    def test_provider_failure_redacted_and_not_verified(self):
        from github_public_issue_intake import PublicGitHubFetchError
        def broken(_):
            raise PublicGitHubFetchError("github_connection_unavailable")
        code, result = live_probe.probe_public_github(
            "octocat/Hello-World", fetcher=broken)
        self.assertEqual(1, code)
        self.assertEqual("NOT_VERIFIED", result["live_transport"])
        self.assertEqual("LIVE_READ_FAILED", result["status"])

    def test_provider_bogus_content_exception_redacted(self):
        def dangerous(_):
            raise ValueError("SUPER_SECRET_FROM_PROVIDER")
        code, result = live_probe.probe_public_github(
            "octocat/Hello-World", fetcher=dangerous)
        self.assertEqual(1, code)
        self.assertNotIn("SUPER_SECRET_FROM_PROVIDER", str(result))

    def test_results_do_not_echo_title_body_or_username(self):
        code, summary = live_probe.probe_public_github(
            "octocat/Hello-World", fetcher=lambda _: [VALID],
            check_ephemeral_import=True)
        self.assertEqual(0, code)
        for forbidden in ("someone@example.com", "never-log-identity",
                          "do not log this title", "secret email"):
            self.assertNotIn(forbidden, str(summary))

    def test_explicit_expected_fixture_is_found_on_the_provider_page(self):
        code, proof = live_probe.probe_public_github(
            "octocat/Hello-World", fetcher=lambda _: [VALID],
            expected_issue_number=102, require_eligible=True)
        self.assertEqual(0, code)
        self.assertEqual("FOUND_WITH_REQUIRED_LIVE_LABELS",
                         proof["expected_fixture"])
        self.assertEqual(102, proof["expected_issue_number"])
        self.assertEqual("NOT_ATTEMPTED", proof["temporary_import"])

    def test_exact_fixture_is_written_with_decision_parity_in_memory_only(self):
        code, proof = live_probe.probe_public_github(
            "octocat/Hello-World", fetcher=lambda _: [VALID],
            expected_issue_number=102, require_eligible=True,
            check_ephemeral_import=True)
        self.assertEqual(0, code)
        self.assertEqual("COMMITTED_IN_EPHEMERAL_SQLITE",
                         proof["expected_fixture"])
        self.assertTrue(proof["historical_decisions_match_preview"])
        self.assertEqual(1, proof["temporarily_written_event_count"])
        self.assertEqual(0, proof["external_dispatch_count"])

    def test_other_matching_issue_cannot_substitute_for_expected_fixture(self):
        code, proof = live_probe.probe_public_github(
            "octocat/Hello-World", fetcher=lambda _: [VALID],
            expected_issue_number=103, require_eligible=True,
            check_ephemeral_import=True)
        self.assertEqual(5, code)
        self.assertEqual("EXPECTED_FIXTURE_NOT_VERIFIED", proof["status"])
        self.assertEqual("EXPECTED_FIXTURE_NOT_ON_FIRST_PAGE",
                         proof["label_mapping"])
        self.assertEqual("NOT_ATTEMPTED", proof["temporary_import"])
        self.assertNotIn("historical_decisions_match_preview", proof)

    def test_missing_expected_fixture_and_no_eligible_issue_is_a_failure(self):
        code, proof = live_probe.probe_public_github(
            "octocat/Hello-World", fetcher=lambda _: [],
            expected_issue_number=102)
        self.assertEqual(5, code)
        self.assertEqual("EXPECTED_FIXTURE_NOT_VERIFIED", proof["status"])
        self.assertEqual("NOT_ATTEMPTED", proof["temporary_import"])

    def test_invalid_fixture_number_does_not_call_remote_provider(self):
        for invalid in (0, -1, 1000000000, True, "102", 0.5):
            called=[]
            code, proof = live_probe.probe_public_github(
                "octocat/Hello-World",
                expected_issue_number=invalid,
                fetcher=lambda r: called.append(r))
            self.assertEqual(2, code, invalid)
            self.assertEqual("NOT_CHECKED", proof["live_transport"])
            self.assertEqual([], called)

    def test_expected_fixture_is_not_allowed_to_be_pull_request(self):
        code, proof = live_probe.probe_public_github(
            "octocat/Hello-World",
            fetcher=lambda _: [{**VALID, "pull_request": {}}],
            expected_issue_number=102, check_ephemeral_import=True)
        self.assertEqual(5, code)
        self.assertEqual("EXPECTED_FIXTURE_NOT_VERIFIED", proof["status"])

    def test_fixture_with_missing_required_label_is_not_accepted(self):
        code, proof = live_probe.probe_public_github(
            "octocat/Hello-World",
            fetcher=lambda _: [{**VALID, "labels": LABELS[:2]}],
            expected_issue_number=102, check_ephemeral_import=True)
        self.assertEqual(5, code)
        self.assertEqual(0, proof["eligible_first_page"])

    def test_fixture_decision_proof_cannot_contain_source_free_text(self):
        code, proof = live_probe.probe_public_github(
            "octocat/Hello-World",
            fetcher=lambda _: [VALID],
            expected_issue_number=102, check_ephemeral_import=True)
        self.assertEqual(0, code)
        self.assertNotIn("someone@example.com", str(proof))
        self.assertNotIn("never-log-identity", str(proof))
        self.assertNotIn("do not log this title", str(proof))

    def test_pull_request_is_not_eligible_even_with_matching_labels(self):
        code, result = live_probe.probe_public_github(
            "octocat/Hello-World", fetcher=lambda _: [
                {**VALID, "pull_request": {"url": "synthetic"}}])
        self.assertEqual(0, code)
        self.assertEqual(0, result["eligible_first_page"])
        self.assertEqual("NO_ELIGIBLE_ISSUES_ON_FIRST_PAGE",
                         result["label_mapping"])


if __name__ == "__main__":
    unittest.main()
