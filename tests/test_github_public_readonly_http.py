"""Public GitHub read-only connector contract and local snapshot acceptance.

All integration tests inject a fake public GitHub response into the real
local HTTP handler. CI does NOT contact GitHub or claim a live provider pass.
"""
from concurrent.futures import ThreadPoolExecutor
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import github_public_issue_intake as connector
from github_public_issue_intake import PublicGitHubFetchError
from local_server import create_handler
from local_workflow_core import LocalWorkflowCore

CREATE = {
    "workflow_id": "github_wf",
    "display_name": "Public Issue Rules",
    "service_filter": "automation",
    "min_budget_tier": 2,
    "followup_policy": "review_required",
}
FIRST_LABELS = [
    {"name": "aas:service:automation"},
    {"name": "aas:budget:3"},
    {"name": "aas:timeline:soon"},
]
SECOND_LABELS = [
    {"name": "aas:service:integration"},
    {"name": "aas:budget:1"},
    {"name": "aas:timeline:flexible"},
]


def public_issues():
    return [
        {"number": 101, "state": "open", "labels": FIRST_LABELS,
         "title": "private_title_just_a_fixture",
         "body": "private_body_just_a_fixture",
         "user": {"login": "private_account_just_a_fixture"}},
        {"number": 102, "state": "open", "labels": SECOND_LABELS},
        {"number": 103, "state": "open", "pull_request": {"url": "ignored"},
         "labels": FIRST_LABELS},
        {"number": 104, "state": "open", "labels": [{"name": "bug"}]},
    ]


class GitHubReadOnlyLocalHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "local.sqlite3"
        self.core = LocalWorkflowCore(self.file)
        self.addCleanup(self.core.close)
        self.clock = [1000.0]
        self.issues = public_issues()
        self.calls = []
        self.provider_error = None

        def fake_fetch(repo):
            self.calls.append(repo)
            if self.provider_error is not None:
                raise PublicGitHubFetchError(self.provider_error)
            return self.issues

        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                          create_handler(
                                            local_core=self.core,
                                            github_fetcher=fake_fetch,
                                            github_clock=lambda: self.clock[0]))
        self.server_thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self.server_thread.join, 4)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def http(self, method, url, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port,
                                          timeout=6)
        data = json.dumps(body) if body is not None else None
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        conn.request(method, url, body=data, headers=h)
        response = conn.getresponse()
        code, payload = response.status, response.read()
        conn.close()
        return code, json.loads(payload)

    def ready(self, wid="github_wf"):
        code, out = self.http("POST", "/api/local-create",
                              {**CREATE, "workflow_id": wid})
        self.assertEqual(201, code, out)
        code, out = self.http("POST", "/api/local-activate", {
            "workflow_id": wid, "expected_version": 1})
        self.assertEqual(200, code, out)

    def preview(self, repo="ExampleOrg/PublicRepo", wid="github_wf"):
        return self.http("POST", "/api/local-github-preview",
                         {"workflow_id": wid, "repo": repo})

    def commit(self, preview, wid="github_wf", **changes):
        request = {"workflow_id": wid,
                   "snapshot_id": preview["snapshot_id"]}
        request.update(changes)
        return self.http("POST", "/api/local-github-commit", request)

    def count(self, wid="github_wf"):
        return self.http("GET", "/api/local-history?workflow_id=" + wid)[1]["total"]

    def test_read_only_preview_maps_only_number_and_three_categories(self):
        self.ready()
        code, out = self.preview()
        self.assertEqual(200, code)
        self.assertEqual("PUBLIC_GITHUB_PREVIEW_READY", out["status"])
        self.assertEqual("PUBLIC_GITHUB_ISSUES_READ_ONLY", out["source"])
        self.assertEqual((4, 2, 2, 0), (out["scanned"], out["ignored_count"],
                                        out["new_count"], out["replay_count"]))
        self.assertEqual("NEEDS_REVIEW_LOCAL", out["rows"][0]["decision"])
        self.assertEqual("SERVICE_MISMATCH", out["rows"][1]["reason"])
        self.assertTrue(out["rows"][0]["event_id"].startswith("gh_"))
        self.assertEqual(["ExampleOrg/PublicRepo"], self.calls)
        self.assertEqual(0, self.count())
        rendered = json.dumps(out)
        for secret in ("private_title", "private_body", "private_account"):
            self.assertNotIn(secret, rendered)

    def test_explicit_local_commit_is_only_write_and_survives_restart(self):
        self.ready()
        snapshot = self.preview()[1]
        self.assertEqual(0, self.count())
        code, result = self.commit(snapshot)
        self.assertEqual(200, code)
        self.assertEqual("PUBLIC_GITHUB_IMPORT_COMMITTED_LOCAL", result["status"])
        self.assertEqual((2, 0, 2), (result["new_count"],
                                     result["replay_count"], result["saved_total"]))
        self.assertEqual(0, result["external_dispatch_count"])
        another = LocalWorkflowCore(self.file)
        self.addCleanup(another.close)
        code, history = another.history("github_wf")
        self.assertEqual(200, code)
        self.assertEqual(2, history["total"])
        self.assertNotIn("private_title", json.dumps(history))
        self.assertNotIn("private_account", json.dumps(history))
        self.assertNotIn("body", json.dumps(history))

    def test_second_preview_returns_replays_not_duplicate_writes(self):
        self.ready()
        self.commit(self.preview()[1])
        code, out = self.preview()
        self.assertEqual(200, code)
        self.assertEqual((0, 2), (out["new_count"], out["replay_count"]))
        self.commit(out)
        self.assertEqual(2, self.count())

    def test_preview_token_one_time_even_after_success(self):
        self.ready()
        old = self.preview()[1]
        self.commit(old)
        code, result = self.commit(old)
        self.assertEqual(409, code)
        self.assertEqual("github_snapshot_not_found", result["error"])
        self.assertEqual(2, self.count())

    def test_invalid_repo_and_nonpublic_url_never_fetch(self):
        self.ready()
        for bad in ("https://api.github.com/repos/a/b",
                    "example.com/a/b", "a/b/other", "a/../other",
                    "a/.", "a/..", "a/b?x=1",
                    "a@evil.com/repo", "user:token@foo/repo",
                    "a/%2f", "a/b#fragment", "a/😸", "", 9, None):
            code, result = self.preview(bad)
            self.assertEqual(422, code, (bad, result))
            self.assertEqual("invalid_public_repo", result["error"])
        self.assertEqual([], self.calls)

    def test_unknown_and_draft_workflow_never_contact_github(self):
        self.assertEqual(404, self.preview()[0])
        self.http("POST", "/api/local-create", CREATE)
        self.assertEqual(409, self.preview()[0])
        self.assertEqual([], self.calls)

    def test_paused_workflow_never_contact_github(self):
        self.ready()
        workflow = self.http("GET", "/api/local-workflows")[1]["workflows"][0]
        self.http("POST", "/api/local-pause", {
            "workflow_id": "github_wf", "expected_version": 1,
            "expected_epoch": workflow["control_epoch"]})
        self.assertEqual("workflow_not_active", self.preview()[1]["error"])
        self.assertEqual([], self.calls)

    def test_no_qualifying_labels_returns_no_snapshot(self):
        self.ready()
        self.issues = [{"number": 18, "labels": [{"name": "bug"}]}]
        code, out = self.preview()
        self.assertEqual(200, code)
        self.assertEqual("NO_MATCHING_PUBLIC_ISSUES", out["status"])
        self.assertEqual(1, out["ignored_count"])
        self.assertNotIn("snapshot_id", out)
        self.assertEqual(0, self.count())

    def test_conflicting_labels_or_partial_labels_skipped(self):
        self.ready()
        self.issues = [
            {"number": 1, "labels": FIRST_LABELS + [
                {"name": "aas:budget:2"}]},
            {"number": 2, "labels": FIRST_LABELS[:2]},
            {"number": 3, "labels": FIRST_LABELS},
        ]
        result = self.preview()[1]
        self.assertEqual(1, result["new_count"])
        self.assertEqual(2, result["ignored_count"])

    def test_pull_requests_are_excluded_not_misrepresented_as_issues(self):
        self.ready()
        self.issues = [{"number": 44, "labels": FIRST_LABELS,
                        "pull_request": {"url": "anything"}}]
        result = self.preview()[1]
        self.assertEqual("NO_MATCHING_PUBLIC_ISSUES", result["status"])
        self.assertEqual(0, self.count())

    def test_invalid_number_labels_and_duplicate_issue_numbers_skipped(self):
        self.ready()
        self.issues = [
            {"number": True, "labels": FIRST_LABELS},
            {"number": 0, "labels": FIRST_LABELS},
            {"number": 101, "labels": FIRST_LABELS},
            {"number": 101, "labels": FIRST_LABELS},
            {"number": 102, "labels": "not-list"},
            {"number": 103, "labels": FIRST_LABELS},
        ]
        result = self.preview()[1]
        self.assertEqual(2, result["new_count"])
        self.assertEqual(4, result["ignored_count"])

    def test_provider_rate_limit_does_not_echo_http_payload(self):
        self.ready()
        self.provider_error = "github_rate_limited_or_forbidden"
        code, result = self.preview()
        self.assertEqual(502, code)
        self.assertEqual("github_rate_limited_or_forbidden", result["error"])
        self.assertNotIn("snapshot_id", result)
        self.assertEqual(0, self.count())

    def test_unknown_provider_exception_is_redacted(self):
        self.ready()
        # An unexpected exception can be noisy in provider internals;
        # the app response must never reflect its message.
        def noisy(_):
            raise ValueError("token=sensitive-secret")
        from github_public_issue_intake import PublicGitHubReadOnlyIntake
        from local_csv_batch import LocalCSVBatchIntake
        other = PublicGitHubReadOnlyIntake(self.core, LocalCSVBatchIntake(self.core),
                                         fetcher=noisy, clock=lambda: 1000)
        code, result = other.preview({
            "workflow_id": "github_wf", "repo": "ExampleOrg/PublicRepo"})
        self.assertEqual(502, code)
        self.assertEqual("github_connection_unavailable", result["error"])
        self.assertNotIn("sensitive-secret", json.dumps(result))

    def test_provider_shape_limit_fails_closed(self):
        self.ready()
        self.issues = public_issues() * 4
        code, out = self.preview()
        self.assertEqual((502, "invalid_provider_shape"),
                         (code, out["error"]))
        self.assertEqual(0, self.count())

    def test_expired_snapshot_fails_without_write(self):
        self.ready()
        old = self.preview()[1]
        self.clock[0] += 301
        code, result = self.commit(old)
        self.assertEqual(409, code)
        self.assertEqual("github_snapshot_not_found", result["error"])
        self.assertEqual(0, self.count())

    def test_mismatched_workflow_snapshot_is_rejected_not_consumed(self):
        self.ready()
        self.ready("other_wf")
        old = self.preview()[1]
        code, out = self.commit(old, wid="other_wf")
        self.assertEqual(409, code)
        self.assertEqual("github_snapshot_not_found", out["error"])
        self.assertEqual(0, self.count())
        self.assertEqual(200, self.commit(old)[0])
        self.assertEqual(2, self.count())

    def test_tampered_snapshot_id_and_commit_fields_fail(self):
        self.ready()
        preview = self.preview()[1]
        for sid in ("x", True, None, "x" * 32):
            code, out = self.commit(preview, snapshot_id=sid)
            self.assertIn(code, (409, 422))
            self.assertEqual(0, out["external_dispatch_count"])
        self.assertEqual(422, self.http("POST", "/api/local-github-commit",
                 {"workflow_id":"github_wf","snapshot_id":preview["snapshot_id"],
                  "csv_text":"bad"})[0])
        self.assertEqual(200, self.commit(preview)[0])

    def test_manual_new_event_invalidates_remote_snapshot(self):
        self.ready()
        preview = self.preview()[1]
        self.http("POST", "/api/local-run", {
            "workflow_id":"github_wf","event_id":"other_local_001",
            "service_code":"automation","budget_tier":2,"timeline":"soon"})
        code, out = self.commit(preview)
        self.assertEqual(409, code)
        self.assertEqual("stale_batch_preview", out["error"])
        self.assertEqual(1, self.count())

    def test_pause_resume_invalidates_remote_snapshot_and_does_not_reuse(self):
        self.ready()
        preview = self.preview()[1]
        epoch = self.http("GET", "/api/local-workflows")[1]["workflows"][0]["control_epoch"]
        self.http("POST", "/api/local-pause", {
            "workflow_id":"github_wf","expected_version":1,"expected_epoch":epoch})
        self.assertEqual("workflow_not_active", self.commit(preview)[1]["error"])
        self.http("POST", "/api/local-resume", {
            "workflow_id":"github_wf","expected_version":1,"expected_epoch":epoch+1})
        self.assertEqual("github_snapshot_not_found", self.commit(preview)[1]["error"])
        self.assertEqual(0, self.count())

    def test_publish_new_rules_rejects_previous_remote_snapshot(self):
        self.ready()
        preview = self.preview()[1]
        self.http("POST", "/api/local-stage", {
            "workflow_id":"github_wf","expected_version":1,
            "service_filter":"integration","min_budget_tier":1,
            "followup_policy":"draft_only"})
        self.http("POST", "/api/local-publish", {
            "workflow_id":"github_wf","expected_version":1,"draft_version":2})
        code, result = self.commit(preview)
        self.assertEqual(409, code)
        self.assertEqual("workflow_version_conflict", result["error"])
        self.assertEqual(0, self.count())

    def test_two_public_repos_use_disjoint_local_event_ids(self):
        self.ready()
        first = self.preview(repo="ExampleOrg/PublicRepo")[1]
        self.commit(first)
        second = self.preview(repo="AnotherOrg/AnotherRepo")[1]
        self.assertEqual(2, second["new_count"])
        self.commit(second)
        self.assertEqual(4, self.count())
        history = self.http("GET",
            "/api/local-history?workflow_id=github_wf")[1]
        ids = {row["event_id"] for row in history["runs"]}
        self.assertEqual(4, len(ids))

    def test_manual_categorical_duplicate_conflict_is_fail_closed(self):
        self.ready()
        first = self.preview()[1]
        self.commit(first)
        self.issues = [{"number": 101, "labels": SECOND_LABELS}]
        code, result = self.preview()
        self.assertEqual(409, code)
        self.assertEqual("event_id_payload_conflict", result["error"])
        self.assertEqual(2, self.count())

    def test_host_and_origin_checks_for_both_new_routes(self):
        self.ready()
        for route in ("/api/local-github-preview", "/api/local-github-commit"):
            for header in ({"Origin":"https://evil.example"},
                           {"Host":"evil.example"}):
                code, _ = self.http("POST", route, {}, header)
                self.assertEqual(403, code)
        self.assertEqual([], self.calls)

    def test_unknown_http_source_write_route_is_not_available(self):
        self.ready()
        code, _ = self.http("POST", "/api/local-github-write", {})
        self.assertEqual(404, code)
        self.assertEqual([], self.calls)

    def test_concurrent_approved_snapshot_commit_is_single_use(self):
        self.ready()
        snap = self.preview()[1]
        with ThreadPoolExecutor(max_workers=2) as pool:
            status = sorted(pool.map(lambda _: self.commit(snap)[0], range(2)))
        self.assertEqual([200, 409], status)
        self.assertEqual(2, self.count())

    def test_http_get_uses_fixed_api_github_host_no_auth_or_redirect(self):
        fake_conn = Mock()
        response = Mock(status=200)
        response.getheader.return_value = "application/json; charset=utf-8"
        response.read.return_value = json.dumps(public_issues()).encode("utf-8")
        fake_conn.getresponse.return_value = response
        with patch.object(connector.http.client, "HTTPSConnection",
                          return_value=fake_conn) as ctor:
            issues = connector._public_issues_http("ExampleOrg/PublicRepo")
        self.assertEqual(4, len(issues))
        self.assertEqual("api.github.com", ctor.call_args.args[0])
        self.assertEqual(443, ctor.call_args.kwargs["port"])
        self.assertEqual(5, ctor.call_args.kwargs["timeout"])
        args, kw = fake_conn.request.call_args
        self.assertEqual("GET", args[0])
        self.assertEqual(
            "/repos/ExampleOrg/PublicRepo/issues?state=open&per_page=15&page=1",
            args[1])
        self.assertNotIn("Authorization", kw["headers"])
        self.assertIn("X-GitHub-Api-Version", kw["headers"])
        self.assertTrue(fake_conn.close.called)

    def test_http_redirect_is_blocked_without_second_destination(self):
        fake_conn = Mock()
        fake_conn.getresponse.return_value = Mock(status=302)
        with patch.object(connector.http.client, "HTTPSConnection",
                          return_value=fake_conn) as ctor:
            with self.assertRaises(PublicGitHubFetchError) as caught:
                connector._public_issues_http("ExampleOrg/PublicRepo")
        self.assertEqual("github_redirect_blocked", caught.exception.code)
        self.assertEqual(1, ctor.call_count)
        self.assertTrue(fake_conn.close.called)

    def test_http_oversize_response_rejected_without_persistence(self):
        fake_conn = Mock()
        fake_resp = Mock(status=200)
        fake_resp.getheader.return_value = "application/json"
        fake_resp.read.return_value = b"x" * (connector._MAX_BYTES + 1)
        fake_conn.getresponse.return_value = fake_resp
        with patch.object(connector.http.client, "HTTPSConnection",
                          return_value=fake_conn):
            with self.assertRaises(PublicGitHubFetchError) as caught:
                connector._public_issues_http("ExampleOrg/PublicRepo")
        self.assertEqual("provider_response_too_large", caught.exception.code)

    def test_http_malformed_json_rejected(self):
        fake_conn = Mock()
        fake_resp = Mock(status=200)
        fake_resp.getheader.return_value = "application/json"
        fake_resp.read.return_value = b"{bad content"
        fake_conn.getresponse.return_value = fake_resp
        with patch.object(connector.http.client, "HTTPSConnection",
                          return_value=fake_conn):
            with self.assertRaises(PublicGitHubFetchError) as caught:
                connector._public_issues_http("ExampleOrg/PublicRepo")
        self.assertEqual("invalid_provider_json", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
