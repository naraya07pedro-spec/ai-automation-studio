"""Headless Chromium end-to-end smoke of the real local UI and Python server.

CI-only optional dependency: playwright==1.56.0 plus Chromium browser.
Unlike source/HTTP tests these click real controls and execute browser JS.
Synthetic data only; no third-party hosts or provider connectors.
"""
import os
import hashlib
from pathlib import Path
import subprocess
import sys
import time
import unittest
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8765"


class RealBrowserWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = subprocess.Popen(
            [sys.executable, "-u", "tests_browser/serve_auth_fixture.py"],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "PYTHONUNBUFFERED": "1"})
        try:
            for _ in range(80):
                if cls.server.poll() is not None:
                    raise RuntimeError("local demo server exited before readiness")
                try:
                    with urlopen(URL + "/api/health", timeout=0.5) as response:
                        if response.status == 200:
                            break
                except (URLError, TimeoutError, OSError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("local demo server readiness timeout")
            cls.playwright = sync_playwright().start()
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception:
            cls.server.terminate()
            cls.server.wait(timeout=5)
            raise

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "browser"):
            cls.browser.close()
        if hasattr(cls, "playwright"):
            cls.playwright.stop()
        cls.server.terminate()
        try:
            cls.server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.server.kill()
            cls.server.wait(timeout=5)

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        self.page = self.context.new_page()
        self.page.goto(URL, wait_until="networkidle")
        self.addCleanup(self.context.close)
        expect(self.page.locator("#operator-gate")).to_be_visible()
        self.page.locator("#operator-password").fill("ci_only_fixture_password_1234")
        self.page.locator("#operator-login-button").click()
        expect(self.page.locator("#operator-gate")).to_be_hidden()
        expect(self.page.locator("#operator-logout")).to_be_visible()
        expect(self.page.locator(".template-card")).to_have_count(5)
        self.assertEqual(5, self.page.locator(".mode-tag").filter(
            has_text="EXECUTABLE_MOCK").count())
        # The local server runs across test methods. Isolate each synthetic
        # event ID by test identity, while preserving same-ID replay *within*
        # each test to verify deduplication.
        suffix = hashlib.sha256(self._testMethodName.encode("ascii")).hexdigest()[:12]
        for selector in ("#event_id", "#ticket-id", "#appointment-id",
                         "#crm-id", "#knowledge-id"):
            self.page.locator(selector).fill("e2e_" + suffix)

    def wait_for_gate(self, name):
        # Playwright.wait_for_function internally evaluates a string, which
        # this app's intentionally strict CSP rejects (no 'unsafe-eval').
        # DevTools evaluation here does not weaken the served CSP.
        for _ in range(100):
            if self.page.evaluate("(key) => typeof window[key] === 'function'", name):
                return
            time.sleep(0.05)
        self.fail("timed out waiting for actual network response gate")

    def test_host_preflight_reads_only_installed_catalog_and_clears_on_logout(self):
        expect(self.page.locator("#device-preflight-run")).to_be_enabled()
        expect(self.page.locator("#device-preflight-result")).to_contain_text(
            "No host preflight yet")
        self.page.locator("#device-preflight-run").click()
        expect(self.page.locator("#device-preflight-status")).to_contain_text(
            "Preflight completed")
        report=self.page.locator("#device-preflight-result")
        expect(report).to_contain_text("PREFLIGHT ONLY")
        expect(report).to_contain_text("MODEL_SELECTION_REQUIRED")
        expect(report).to_contain_text("fixture:model")
        expect(report).to_contain_text("System RAM available: 1664 MB")
        expect(report).to_contain_text("No model inference attempted")
        self.page.locator("#operator-logout").click()
        expect(self.page.locator("#device-preflight-run")).to_be_disabled()
        expect(self.page.locator("#device-preflight-result")).to_contain_text(
            "Session locked")

    def test_device_acceptance_timeout_shows_actionable_local_diagnosis(self):
        self.page.locator("#local-agent-model").fill("fixture:timeout")
        expect(self.page.locator("#device-acceptance-run")).to_be_enabled()
        self.page.locator("#device-acceptance-run").click()
        expect(self.page.locator("#device-acceptance-status")).to_contain_text(
            "Acceptance BLOCKED: local_model_timeout")
        expect(self.page.locator("#device-acceptance-result")).to_contain_text(
            "exceeded 90 seconds")
        expect(self.page.locator("#device-acceptance-result")).not_to_contain_text(
            "REAL LOCAL OLLAMA ACCEPTANCE: PASSED")
        expect(self.page.locator("#local-timeout-state")).to_contain_text("QUARANTINED")
        expect(self.page.locator("#local-timeout-ack")).to_be_enabled()
        self.page.locator("#local-agent-model").fill("fixture:model")
        expect(self.page.locator("#device-acceptance-run")).to_be_disabled()
        expect(self.page.locator("#device-acceptance-result")).to_contain_text(
            "No result for this selected model")
        # Operator explicitly acknowledges after inspecting Ollama; tests
        # accept the browser confirmation, never silently rearm the backend.
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator("#local-timeout-ack").click()
        expect(self.page.locator("#local-timeout-state")).to_contain_text(
            "No unresolved local timeout")
        expect(self.page.locator("#device-acceptance-run")).to_be_enabled()

    def test_device_acceptance_fixture_is_not_misrepresented_as_real_ollama(self):
        expect(self.page.locator("#device-acceptance-run")).to_be_disabled()
        self.page.locator("#local-agent-model").fill("fixture:model")
        expect(self.page.locator("#device-acceptance-run")).to_be_enabled()
        self.page.locator("#device-acceptance-run").click()
        expect(self.page.locator("#device-acceptance-status")).to_contain_text(
            "NOT hardware acceptance")
        expect(self.page.locator("#device-acceptance-result")).to_contain_text(
            "SYNTHETIC FIXTURE: NOT REAL OLLAMA")
        expect(self.page.locator("#device-acceptance-result")).to_contain_text(
            "INJECTED_TEST_TRANSPORT")
        expect(self.page.locator("#device-acceptance-result")).to_contain_text(
            "schedule.md")
        expect(self.page.locator("#device-acceptance-result")).to_contain_text(
            "18:30 WIB")
        self.page.locator("#operator-logout").click()
        expect(self.page.locator("#device-acceptance-run")).to_be_disabled()
        expect(self.page.locator("#device-acceptance-result")).to_contain_text(
            "Session locked")

    def test_selected_multi_documents_cite_correct_filename_and_clear_on_logout(self):
        expect(self.page.locator("#local-multidoc-files")).to_be_enabled()
        self.page.locator("#local-agent-model").fill("fixture:model")
        self.page.locator("#local-multidoc-files").set_input_files([
            {"name":"policy.txt","mimeType":"text/plain",
             "buffer":b"Operations approval is required for all renewals."},
            {"name":"schedule.md","mimeType":"text/markdown",
             "buffer":b"Maintenance review meeting starts at 18:30 WIB every Thursday."}
        ])
        self.page.locator("#local-multidoc-question").fill(
            "What time does the maintenance review meeting start?")
        expect(self.page.locator("#local-multidoc-analyze")).to_be_enabled()
        self.page.locator("#local-multidoc-analyze").click()
        expect(self.page.locator("#local-multidoc-status")).to_contain_text(
            "quote(s) verified")
        expect(self.page.locator("#local-multidoc-result")).to_contain_text(
            "schedule.md [F2C1]")
        expect(self.page.locator("#local-multidoc-result")).to_contain_text(
            "meeting starts at 18:30 WIB")
        expect(self.page.locator("#local-multidoc-result")).to_contain_text(
            "DETERMINISTIC_LEXICAL_OVERLAP_V2")
        expect(self.page.locator("#local-multidoc-result")).to_contain_text(
            "Why these passages were selected")
        expect(self.page.locator("#local-multidoc-result")).to_contain_text(
            "Source preview:")
        expect(self.page.locator("#local-multidoc-result")).to_contain_text(
            "matched words:")
        self.page.locator("#operator-logout").click()
        expect(self.page.locator("#operator-gate")).to_be_visible()
        expect(self.page.locator("#local-multidoc-files")).to_be_disabled()
        expect(self.page.locator("#local-multidoc-analyze")).to_be_disabled()
        expect(self.page.locator("#local-multidoc-result")).to_contain_text(
            "Session locked")
        self.assertEqual("",self.page.locator("#local-multidoc-question").input_value())

    def test_selected_text_file_analysis_verified_quote_and_logout_clears(self):
        expect(self.page.locator("#local-document-file")).to_be_enabled()
        self.page.locator("#local-agent-model").fill("fixture:model")
        self.page.locator("#local-document-file").set_input_files({
            "name":"notes.txt", "mimeType":"text/plain",
            "buffer":b"The service desk opens at 09:00 on weekdays.\\n"
        })
        self.page.locator("#local-document-question").fill(
            "When does the desk open?")
        expect(self.page.locator("#local-document-analyze")).to_be_enabled()
        self.page.locator("#local-document-analyze").click()
        expect(self.page.locator("#local-document-status")).to_contain_text(
            "exact-match source citation")
        expect(self.page.locator("#local-document-result")).to_contain_text(
            "The desk opens at 09:00.")
        expect(self.page.locator("#local-document-result")).to_contain_text(
            "[S1] chars")
        expect(self.page.locator("#local-document-result")).to_contain_text(
            "opens at 09:00")
        self.page.locator("#operator-logout").click()
        expect(self.page.locator("#operator-gate")).to_be_visible()
        expect(self.page.locator("#local-document-file")).to_be_disabled()
        expect(self.page.locator("#local-document-analyze")).to_be_disabled()
        expect(self.page.locator("#local-document-result")).to_contain_text(
            "Session locked")
        self.assertEqual("",self.page.locator("#local-document-question").input_value())

    def test_local_ollama_analysis_has_source_citations_and_explicit_approval(self):
        self.page.locator("#local-agent-model").fill("fixture:model")
        self.page.locator("#agent-plan-goal").fill(
            "Analyze open GitHub issues from Octocat/Hello-World and suggest review questions")
        self.page.locator("#agent-plan-propose").click()
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "github_issue_analysis")
        expect(self.page.locator("#agent-plan-next")).to_be_disabled()
        expect(self.page.locator("#agent-plan-analysis-report")).to_contain_text(
            "No AI analysis yet")
        self.page.locator("#agent-plan-approve").click()
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "APPROVED")
        expect(self.page.locator("#agent-plan-analysis-report")).to_contain_text(
            "No AI analysis yet")
        self.page.locator("#agent-plan-next").click()
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "COMPLETED")
        expect(self.page.locator("#agent-plan-analysis-report")).to_contain_text(
            "OLLAMA_BOUNDED_UNVERIFIED_INTERPRETATION")
        expect(self.page.locator("#agent-plan-analysis-report")).to_contain_text(
            "AI observations are UNVERIFIED")
        expect(self.page.locator("#agent-plan-analysis-report")).to_contain_text(
            "Could the existing bug label warrant a reproduction check")
        expect(self.page.locator("#agent-plan-analysis-report")).to_contain_text(
            "https://github.com/Octocat/Hello-World/issues/42")
        self.page.locator("#agent-plan-delete").click()
        expect(self.page.locator("#agent-plan-analysis-report")).to_contain_text(
            "No AI analysis yet")

    def test_agent_label_based_github_issue_triage_report(self):
        self.page.locator("#local-agent-model").fill("fixture:model")
        self.page.locator("#agent-plan-goal").fill(
            "Triage open GitHub issues from Octocat/Hello-World using labels")
        self.page.locator("#agent-plan-propose").click()
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "github_issue_triage")
        expect(self.page.locator("#agent-plan-next")).to_be_disabled()
        expect(self.page.locator("#agent-plan-triage-report")).to_contain_text(
            "No triage report yet")
        self.page.locator("#agent-plan-approve").click()
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "APPROVED")
        expect(self.page.locator("#agent-plan-triage-report")).to_contain_text(
            "No triage report yet")
        self.page.locator("#agent-plan-next").click()
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "COMPLETED")
        expect(self.page.locator("#agent-plan-triage-report")).to_contain_text(
            "DETERMINISTIC_LABEL_RULES_V1")
        expect(self.page.locator("#agent-plan-triage-report")).to_contain_text(
            "flagged for review: 1")
        expect(self.page.locator("#agent-plan-triage-report")).to_contain_text(
            "https://github.com/Octocat/Hello-World/issues/42")
        expect(self.page.locator("#agent-plan-triage-report")).to_contain_text(
            "priority:high")
        self.page.locator("#agent-plan-delete").click()
        expect(self.page.locator("#agent-plan-triage-report")).to_contain_text(
            "No triage report yet")

    def test_agent_approved_github_public_issues_read_with_provenance(self):
        expect(self.page.locator("#agent-plan-github-status")).to_contain_text(
            "GitHub read-only tool enabled")
        self.page.locator("#local-agent-model").fill("fixture:model")
        self.page.locator("#agent-plan-goal").fill(
            "List open GitHub issues from Octocat/Hello-World")
        self.page.locator("#agent-plan-propose").click()
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "github_public_issues")
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "Octocat/Hello-World")
        expect(self.page.locator("#agent-plan-next")).to_be_disabled()
        self.assertNotIn("Synthetic GitHub issue with human-readable title",
                         self.page.locator("#agent-plan-details").inner_text())
        self.page.locator("#agent-plan-approve").click()
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "APPROVED")
        self.assertNotIn("GITHUB_PUBLIC_ISSUES_READ_ONLY",
                         self.page.locator("#agent-plan-details").inner_text())
        self.page.locator("#agent-plan-next").click()
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "COMPLETED")
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "GITHUB_PUBLIC_ISSUES_READ_ONLY")
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "Synthetic GitHub issue with human-readable title")
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "https://github.com/Octocat/Hello-World/issues/42")
        self.page.locator("#agent-plan-delete").click()
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "No selected plan")

    def test_agent_plan_requires_review_and_one_click_per_step(self):
        expect(self.page.locator("#agent-plan-propose")).to_be_enabled()
        self.page.locator("#local-agent-model").fill("fixture:model")
        self.page.locator("#agent-plan-goal").fill(
            "Synthetic: multiply 7 by 8 and read UTC")
        self.page.locator("#agent-plan-propose").click()
        expect(self.page.locator("#agent-plan-status")).to_contain_text(
            "Review every tool")
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "PREPARED")
        self.assertIn("calculate",self.page.locator("#agent-plan-details").inner_text())
        expect(self.page.locator("#agent-plan-next")).to_be_disabled()
        expect(self.page.locator("#agent-plan-approve")).to_be_enabled()
        self.page.locator("#agent-plan-approve").click()
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "APPROVED")
        expect(self.page.locator("#agent-plan-next")).to_be_enabled()
        self.assertNotIn('"output": "56"',
                         self.page.locator("#agent-plan-details").inner_text())
        self.page.locator("#agent-plan-next").click()
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "NEXT_READY")
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            '"output": "56"')
        self.assertEqual(1,
            self.page.locator("#agent-plan-details").inner_text().count('"output":'))
        self.page.locator("#agent-plan-next").click()
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "COMPLETED")
        expect(self.page.locator("#agent-plan-next")).to_be_disabled()
        self.page.locator("#agent-plan-delete").click()
        expect(self.page.locator("#agent-plan-details")).to_contain_text(
            "No selected plan")
        self.page.locator("#agent-plan-list").click()
        expect(self.page.locator("#agent-plan-list-output")).to_contain_text(
            "No plans saved")

    def test_operator_model_discovery_and_explicit_tool_probe(self):
        expect(self.page.locator("#local-agent-find-models")).to_be_enabled()
        expect(self.page.locator("#local-agent-test-model")).to_be_enabled()
        self.page.locator("#local-agent-find-models").click()
        expect(self.page.locator("#local-agent-readiness-status")).to_contain_text(
            "Ollama reachable")
        self.page.locator("#local-agent-model").fill("fixture:model")
        self.page.locator("#local-agent-test-model").click()
        expect(self.page.locator("#local-agent-readiness-status")).to_contain_text(
            "calculator tool call was observed")
        self.page.locator("#operator-logout").click()
        expect(self.page.locator("#operator-gate")).to_be_visible()
        expect(self.page.locator("#local-agent-find-models")).to_be_disabled()
        expect(self.page.locator("#local-agent-test-model")).to_be_disabled()
        expect(self.page.locator("#local-agent-readiness-status")).to_contain_text(
            "Session locked")

    def test_agent_conversations_are_isolated_and_selectively_deleted(self):
        expect(self.page.locator("#local-agent-conversation")).to_be_enabled()
        self.page.locator("#local-agent-model").fill("fixture:model")
        self.page.locator("#local-agent-remember").check()
        self.page.locator("#local-agent-conversation").fill("work-50")
        self.page.locator("#local-agent-prompt").fill("WORK50 synthetic prompt")
        self.page.locator("#local-agent-send").click()
        expect(self.page.locator("#local-agent-status")).to_contain_text("Tools: calculate")
        self.page.locator("#local-agent-conversation").fill("personal-50")
        self.page.locator("#local-agent-prompt").fill("PERSONAL50 synthetic prompt")
        self.page.locator("#local-agent-send").click()
        expect(self.page.locator("#local-agent-status")).to_contain_text("Tools: calculate")
        self.page.locator("#local-agent-conversation").fill("work-50")
        self.page.locator("#local-agent-history-refresh").click()
        expect(self.page.locator("#local-agent-history-display")).to_contain_text(
            "WORK50 synthetic prompt")
        self.assertNotIn("PERSONAL50",self.page.locator(
            "#local-agent-history-display").inner_text())
        self.page.locator("#local-agent-history-clear").click()
        expect(self.page.locator("#local-agent-history-display")).to_contain_text(
            "No saved turns")
        self.page.locator("#local-agent-conversation").fill("personal-50")
        self.page.locator("#local-agent-history-refresh").click()
        expect(self.page.locator("#local-agent-history-display")).to_contain_text(
            "PERSONAL50 synthetic prompt")
        self.page.locator("#local-agent-history-clear").click()

    def test_agent_history_opt_in_visible_and_deletable(self):
        expect(self.page.locator("#local-agent-remember")).to_be_enabled()
        self.page.locator("#local-agent-history-clear").click()
        expect(self.page.locator("#local-agent-history-display")).to_contain_text(
            "No saved turns")
        self.page.locator("#local-agent-model").fill("fixture:model")
        self.page.locator("#local-agent-prompt").fill("Remember this synthetic calculation")
        self.page.locator("#local-agent-remember").check()
        self.page.locator("#local-agent-send").click()
        expect(self.page.locator("#local-agent-status")).to_contain_text("Tools: calculate")
        self.page.locator("#local-agent-history-refresh").click()
        expect(self.page.locator("#local-agent-history-display")).to_contain_text(
            "Remember this synthetic calculation")
        self.page.locator("#local-agent-history-clear").click()
        expect(self.page.locator("#local-agent-history-display")).to_contain_text(
            "No saved turns")

    def test_authenticated_local_agent_browser_panel(self):
        expect(self.page.locator("#local-agent-panel")).to_be_visible()
        expect(self.page.locator("#local-agent-send")).to_be_enabled()
        self.page.locator("#local-agent-model").fill("fixture:model")
        self.page.locator("#local-agent-prompt").fill("calculate 4+5")
        self.page.locator("#local-agent-send").click()
        expect(self.page.locator("#local-agent-output")).to_contain_text(
            "Fixture answer: 13")
        expect(self.page.locator("#local-agent-status")).to_contain_text(
            "Tools: calculate")
        self.page.locator("#operator-logout").click()
        expect(self.page.locator("#operator-gate")).to_be_visible()
        expect(self.page.locator("#local-agent-output")).to_contain_text(
            "previous answer cleared")

    def test_gallery_navigation_and_no_external_links(self):
        (ROOT / "browser-artifacts").mkdir(exist_ok=True)
        self.page.screenshot(path=str(ROOT / "browser-artifacts" / "desktop-gallery.png"),
                             full_page=True)
        cards = self.page.locator(".template-card")
        self.assertEqual(5, cards.count())
        for index, target in enumerate((
            "#setup-panel", "#crm-panel", "#triage-panel",
            "#appointment-panel", "#knowledge-panel")):
            cards.nth(index).get_by_role("button", name="Configure mock").click()
            self.assertTrue(self.page.locator(target).is_visible())
        self.assertEqual(0, self.page.locator("a[href^='https://']").count())

    def test_lead_setup_run_trace_review_and_duplicate(self):
        self.assertTrue(self.page.locator("#run").is_disabled())
        self.page.locator("#validate-setup").click()
        expect(self.page.locator("#setup-status")).to_contain_text("DRAFT_VALID")
        self.assertTrue(self.page.locator("#run").is_enabled())
        self.page.locator("#run").click()
        expect(self.page.locator("#workflow")).to_contain_text("READY_FOR_REVIEW")
        self.assertEqual(7, self.page.locator("#lead-timeline .stage").count())
        self.assertIn("NEVER_SENT", self.page.locator("#draft").inner_text())
        self.page.locator("#ack").click()
        expect(self.page.locator("#review-status")).to_contain_text("ACKNOWLEDGED_LOCAL_ONLY")
        self.page.locator("#run").click()
        expect(self.page.locator("#workflow")).to_contain_text("SKIPPED_DUPLICATE")
        self.assertTrue(self.page.locator("#ack").is_disabled())

    def test_setup_change_invalidates_run(self):
        self.page.locator("#validate-setup").click()
        expect(self.page.locator("#run")).to_be_enabled()
        self.page.locator("#source").select_option("mock_web_form")
        self.assertTrue(self.page.locator("#run").is_disabled())
        self.assertIn("Validate again", self.page.locator("#setup-status").inner_text())

    def test_stale_setup_validation_response_cannot_reenable_run(self):
        # Delay the REAL server response rather than replacing it with a stub.
        self.page.evaluate("""() => {
            const nativeFetch = window.fetch.bind(window);
            window.fetch = async (url, options) => {
                const response = await nativeFetch(url, options);
                if (url === "/api/mock-setup") {
                    await new Promise(resolve => { window.__releaseSetup = resolve; });
                }
                return response;
            };
        }""")
        self.page.locator("#validate-setup").click()
        self.wait_for_gate("__releaseSetup")
        self.page.locator("#source").select_option("mock_web_form")
        self.assertTrue(self.page.locator("#run").is_disabled())
        self.page.evaluate("window.__releaseSetup()")
        self.page.wait_for_timeout(150)
        self.assertTrue(self.page.locator("#run").is_disabled())
        self.assertIn("Validate again", self.page.locator("#setup-status").inner_text())
        self.assertTrue(self.page.locator("#ack").is_disabled())

    def test_inflight_lead_run_cannot_reenable_after_setup_edit(self):
        self.page.locator("#validate-setup").click()
        expect(self.page.locator("#run")).to_be_enabled()
        # The HTTP request still reaches the local engine. Only response
        # delivery is delayed to exercise the UI's stale-result boundary.
        self.page.evaluate("""() => {
            const nativeFetch = window.fetch.bind(window);
            window.fetch = async (url, options) => {
                const response = await nativeFetch(url, options);
                if (url === "/api/mock-run") {
                    await new Promise(resolve => { window.__releaseRun = resolve; });
                }
                return response;
            };
        }""")
        self.page.locator("#run").click()
        self.wait_for_gate("__releaseRun")
        self.page.locator("#destination").select_option("mock_sheet")
        self.assertTrue(self.page.locator("#run").is_disabled())
        self.page.evaluate("window.__releaseRun()")
        expect(self.page.locator("#status")).to_contain_text("STALE RESPONSE IGNORED")
        self.assertTrue(self.page.locator("#run").is_disabled())
        self.assertTrue(self.page.locator("#ack").is_disabled())
        self.assertIn("stale", self.page.locator("#draft").inner_text().lower())

    def test_support_triage_real_browser_duplicate(self):
        self.page.locator("#triage-run").click()
        expect(self.page.locator("#triage-state")).to_contain_text("ROUTED_MOCK_ONLY")
        self.assertEqual("MOCK_TECHNICAL", self.page.locator("#triage-queue").inner_text())
        self.assertEqual(6, self.page.locator("#triage-timeline .stage").count())
        self.page.locator("#triage-run").click()
        expect(self.page.locator("#triage-state")).to_contain_text("SKIPPED_DUPLICATE")
        self.assertEqual("0", self.page.locator("#triage-dispatch").inner_text())

    def test_appointment_plan_in_browser(self):
        self.page.locator("#appointment-run").click()
        expect(self.page.locator("#appointment-state")).to_contain_text("PLANNED_MOCK_ONLY")
        self.assertEqual("WITHIN_24_HOURS", self.page.locator("#appointment-window").inner_text())
        self.assertEqual("0", self.page.locator("#appointment-sent").inner_text())
        self.assertEqual(6, self.page.locator("#appointment-timeline .stage").count())

    def test_crm_review_gate_and_record_mapping_in_browser(self):
        self.page.locator("#crm-run").click()
        expect(self.page.locator("#crm-state")).to_contain_text("REVIEW_REQUIRED_NO_RECORD")
        self.assertIn("No record", self.page.locator("#crm-record").inner_text())
        self.page.locator("#crm-review").select_option("true")
        self.page.locator("#crm-run").click()
        expect(self.page.locator("#crm-state")).to_contain_text("SIMULATED_HANDOFF_PREPARED")
        self.assertIn("crm_lifecycle_stage", self.page.locator("#crm-record").inner_text())
        self.assertEqual("0", self.page.locator("#crm-created").inner_text())

    def test_knowledge_restricted_blocks_then_public_prepares(self):
        self.page.locator("#knowledge-classification").select_option("restricted")
        self.page.locator("#knowledge-run").click()
        expect(self.page.locator("#knowledge-state")).to_contain_text("RESTRICTED_NO_ROUTE")
        self.assertIn("No routing preview", self.page.locator("#knowledge-preview").inner_text())
        self.page.locator("#knowledge-classification").select_option("public")
        self.page.locator("#knowledge-run").click()
        expect(self.page.locator("#knowledge-state")).to_contain_text("ROUTING_PREVIEW_PREPARED")
        self.assertIn("mock_knowledge_base", self.page.locator("#knowledge-preview").inner_text())
        self.assertEqual("0", self.page.locator("#knowledge-transferred").inner_text())

    def assert_old_response_does_not_overwrite_new_run(
            self, endpoint, button, select, choice, event_id, state, expected):
        """Delay a REAL HTTP response, edit inputs, then run a new event.

        The old completion must not overwrite either the revised inputs
        or the newer result, even when the old request completed first.
        """
        self.page.evaluate("""(endpoint) => {
            const nativeFetch = window.fetch.bind(window);
            let holdFirst = true;
            window.fetch = async (url, options) => {
                const response = await nativeFetch(url, options);
                if (url === endpoint && holdFirst) {
                    holdFirst = false;
                    await new Promise(resolve => { window.__releaseOther = resolve; });
                }
                return response;
            };
        }""", endpoint)
        self.page.locator(button).click()
        self.wait_for_gate("__releaseOther")
        self.page.locator(select).select_option(choice)
        self.assertTrue(self.page.locator(button).is_enabled())
        self.assertIn("STALE", self.page.locator(state).inner_text())
        self.page.locator(event_id).fill(
            "revised_" + hashlib.sha256(
                self._testMethodName.encode("ascii")).hexdigest()[:12])
        self.page.locator(button).click()
        expect(self.page.locator(state)).to_contain_text(expected)
        self.page.evaluate("window.__releaseOther()")
        self.page.wait_for_timeout(200)
        self.assertIn(expected, self.page.locator(state).inner_text())
        self.assertTrue(self.page.locator(button).is_enabled())

    def test_triage_delayed_previous_response_cannot_override_new_run(self):
        self.assert_old_response_does_not_overwrite_new_run(
            "/api/mock-triage", "#triage-run", "#ticket-category",
            "billing", "#ticket-id", "#triage-state", "ROUTED_MOCK_ONLY")
        self.assertEqual("0", self.page.locator("#triage-dispatch").inner_text())

    def test_appointment_delayed_previous_response_cannot_override_new_run(self):
        self.assert_old_response_does_not_overwrite_new_run(
            "/api/mock-appointment", "#appointment-run",
            "#appointment-channel", "mock_sms", "#appointment-id",
            "#appointment-state", "PLANNED_MOCK_ONLY")
        self.assertEqual("0", self.page.locator("#appointment-sent").inner_text())

    def test_crm_delayed_previous_response_cannot_override_new_run(self):
        self.assert_old_response_does_not_overwrite_new_run(
            "/api/mock-crm-handoff", "#crm-run", "#crm-review",
            "true", "#crm-id", "#crm-state", "SIMULATED_HANDOFF_PREPARED")
        self.assertEqual("0", self.page.locator("#crm-created").inner_text())
        self.assertIn("crm_lifecycle_stage",
                      self.page.locator("#crm-record").inner_text())

    def test_knowledge_delayed_previous_response_cannot_override_new_run(self):
        self.assert_old_response_does_not_overwrite_new_run(
            "/api/mock-knowledge-routing", "#knowledge-run",
            "#knowledge-classification", "restricted",
            "#knowledge-id", "#knowledge-state", "RESTRICTED_NO_ROUTE")
        self.assertEqual("0",
                         self.page.locator("#knowledge-transferred").inner_text())
        self.assertIn("No routing preview",
                      self.page.locator("#knowledge-preview").inner_text())

    def create_functional_local_workflow(self):
        wid = "wf_" + hashlib.sha256(
            self._testMethodName.encode("ascii")).hexdigest()[:12]
        self.page.locator("#local-id").fill(wid)
        self.page.locator("#local-create").click()
        expect(self.page.locator("#local-create-status")).to_contain_text(
            "CREATED_DRAFT")
        expect(self.page.locator("#local-activate")).to_be_enabled()
        self.page.locator("#local-activate").click()
        expect(self.page.locator("#local-activation-status")).to_contain_text(
            "ACTIVE_LOCAL")
        expect(self.page.locator("#local-run")).to_be_enabled()
        return wid

    def test_functional_local_workflow_create_activate_run_and_browser_reload(self):
        wid = self.create_functional_local_workflow()
        self.page.locator("#local-event").fill("local_event_001")
        self.page.locator("#local-run").click()
        expect(self.page.locator("#local-run-status")).to_contain_text(
            "NEEDS_REVIEW_LOCAL")
        expect(self.page.locator("#local-history")).to_contain_text(
            "1 saved events")
        self.assertIn('"external_dispatch_count": 0',
                      self.page.locator("#local-result").inner_text())
        self.page.reload(wait_until="networkidle")
        expect(self.page.locator("#local-workflows")).to_contain_text(wid)
        expect(self.page.locator("#local-history")).to_contain_text(
            "local_event_001")
        expect(self.page.locator("#local-run")).to_be_enabled()

    def test_functional_local_workflow_idempotency_and_payload_conflict(self):
        self.create_functional_local_workflow()
        self.page.locator("#local-event").fill("local_event_002")
        self.page.locator("#local-run").click()
        expect(self.page.locator("#local-run-status")).to_contain_text(
            "RECORDED_LOCAL")
        self.page.locator("#local-run").click()
        expect(self.page.locator("#local-run-status")).to_contain_text(
            "REPLAYED_LOCAL")
        self.page.locator("#local-budget").select_option("1")
        self.page.locator("#local-run").click()
        expect(self.page.locator("#local-run-status")).to_contain_text(
            "event_id_payload_conflict")
        expect(self.page.locator("#local-history")).to_contain_text(
            "1 saved events")

    def test_local_csv_file_preview_commit_and_replay_in_real_browser(self):
        wid = self.create_functional_local_workflow()
        csv_bytes = (b"event_id,service_code,budget_tier,timeline\\n"
                     b"browser_101,automation,3,soon\\n"
                     b"browser_102,integration,1,flexible\\n").replace(b"\\n", b"\n")
        self.page.locator("#local-batch-file").set_input_files({
            "name": "synthetic-events.csv", "mimeType": "text/csv", "buffer": csv_bytes})
        expect(self.page.locator("#local-batch-preview")).to_be_enabled()
        self.assertTrue(self.page.locator("#local-batch-commit").is_disabled())
        self.page.locator("#local-batch-preview").click()
        expect(self.page.locator("#local-batch-status")).to_contain_text("PREVIEW READY")
        expect(self.page.locator("#local-batch-commit")).to_be_enabled()
        self.assertIn("browser_101", self.page.locator("#local-batch-result").inner_text())
        self.assertIn("0 saved events", self.page.locator("#local-history").inner_text())
        self.page.locator("#local-batch-commit").click()
        expect(self.page.locator("#local-batch-status")).to_contain_text(
            "BATCH_COMMITTED_LOCAL")
        expect(self.page.locator("#local-history")).to_contain_text("2 saved events")
        self.page.reload(wait_until="networkidle")
        expect(self.page.locator("#local-workflows")).to_contain_text(wid)
        expect(self.page.locator("#local-history")).to_contain_text("browser_101")

    def test_local_csv_stale_preview_rejects_commit_after_manual_event(self):
        self.create_functional_local_workflow()
        csv_bytes = (b"event_id,service_code,budget_tier,timeline\\n"
                     b"stale_101,automation,3,soon\\n").replace(b"\\n", b"\n")
        self.page.locator("#local-batch-file").set_input_files({
            "name": "stale.csv", "mimeType": "text/csv", "buffer": csv_bytes})
        self.page.locator("#local-batch-preview").click()
        expect(self.page.locator("#local-batch-status")).to_contain_text("PREVIEW READY")
        self.page.locator("#local-event").fill("manual_after_preview")
        self.page.locator("#local-run").click()
        expect(self.page.locator("#local-run-status")).to_contain_text(
            "RECORDED_LOCAL")
        self.page.locator("#local-batch-commit").click()
        expect(self.page.locator("#local-batch-status")).to_contain_text(
            "stale_batch_preview")
        self.page.locator("#local-refresh-history").click()
        expect(self.page.locator("#local-history")).to_contain_text("1 saved events")
        self.assertTrue(self.page.locator("#local-batch-commit").is_disabled())

    def test_local_csv_bad_columns_rejected_without_saving(self):
        self.create_functional_local_workflow()
        csv_bytes = (b"event_id,email,service_code,budget_tier,timeline\\n"
                     b"invalid_01,someone@example.com,automation,3,soon\\n").replace(
                         b"\\n", b"\n")
        self.page.locator("#local-batch-file").set_input_files({
            "name": "unsafe.csv", "mimeType": "text/csv", "buffer": csv_bytes})
        self.page.locator("#local-batch-preview").click()
        expect(self.page.locator("#local-batch-status")).to_contain_text(
            "invalid_csv_content")
        self.assertTrue(self.page.locator("#local-batch-commit").is_disabled())
        expect(self.page.locator("#local-history")).to_contain_text("0 saved events")
        self.assertNotIn("someone@example.com",
                         self.page.locator("#local-batch-result").inner_text())

    def test_publish_new_rules_keeps_prior_local_results_versioned(self):
        self.create_functional_local_workflow()
        self.page.locator("#local-event").fill("version_old_001")
        self.page.locator("#local-run").click()
        expect(self.page.locator("#local-history")).to_contain_text(
            "version_old_001")
        self.page.locator("#local-stage-service").select_option("integration")
        self.page.locator("#local-stage-budget").select_option("3")
        self.page.locator("#local-stage").click()
        expect(self.page.locator("#local-control-status")).to_contain_text(
            "REVISION_DRAFTED")
        expect(self.page.locator("#local-publish")).to_be_enabled()
        expect(self.page.locator("#local-revisions")).to_contain_text('"DRAFT"')
        self.page.locator("#local-publish").click()
        expect(self.page.locator("#local-control-status")).to_contain_text(
            "REVISION_PUBLISHED_LOCAL")
        expect(self.page.locator("#local-workflows")).to_contain_text("V2")
        self.page.locator("#local-event").fill("version_new_002")
        self.page.locator("#local-run").click()
        expect(self.page.locator("#local-run-status")).to_contain_text(
            "NOT_QUALIFIED_LOCAL")
        expect(self.page.locator("#local-history")).to_contain_text(
            '"workflow_version": 1')
        expect(self.page.locator("#local-history")).to_contain_text(
            '"workflow_version": 2')
        self.page.reload(wait_until="networkidle")
        expect(self.page.locator("#local-revisions")).to_contain_text("ARCHIVED")
        expect(self.page.locator("#local-history")).to_contain_text(
            "version_old_001")

    def test_pause_blocks_manual_and_csv_then_resume_enables(self):
        self.create_functional_local_workflow()
        self.page.locator("#local-batch-file").set_input_files({
            "name": "pause-demo.csv", "mimeType": "text/csv",
            "buffer": b"event_id,service_code,budget_tier,timeline\n"
                      b"paused_001,automation,3,soon\n"})
        self.page.locator("#local-batch-preview").click()
        expect(self.page.locator("#local-batch-status")).to_contain_text(
            "PREVIEW READY")
        self.page.locator("#local-pause").click()
        expect(self.page.locator("#local-control-status")).to_contain_text(
            "PAUSED_LOCAL")
        self.assertTrue(self.page.locator("#local-run").is_disabled())
        self.assertTrue(self.page.locator("#local-batch-preview").is_disabled())
        self.assertTrue(self.page.locator("#local-batch-commit").is_disabled())
        self.page.reload(wait_until="networkidle")
        expect(self.page.locator("#local-control-choice")).to_have_count(1)
        expect(self.page.locator("#local-resume")).to_be_enabled()
        self.assertTrue(self.page.locator("#local-run").is_disabled())
        self.page.locator("#local-resume").click()
        expect(self.page.locator("#local-control-status")).to_contain_text(
            "RESUMED_LOCAL")
        expect(self.page.locator("#local-run")).to_be_enabled()
        # A reload deliberately does NOT retain the selected file bytes.
        self.assertTrue(self.page.locator("#local-batch-preview").is_disabled())
        self.page.locator("#local-batch-file").set_input_files({
            "name": "fresh-after-resume.csv", "mimeType": "text/csv",
            "buffer": b"event_id,service_code,budget_tier,timeline\n"
                      b"resumed_001,automation,3,soon\n"})
        expect(self.page.locator("#local-batch-preview")).to_be_enabled()

    def test_publish_staged_revision_while_paused_keeps_stop_state(self):
        self.create_functional_local_workflow()
        self.page.locator("#local-pause").click()
        expect(self.page.locator("#local-control-status")).to_contain_text(
            "PAUSED_LOCAL")
        self.page.locator("#local-stage-policy").select_option("draft_only")
        self.page.locator("#local-stage").click()
        expect(self.page.locator("#local-control-status")).to_contain_text(
            "REVISION_DRAFTED")
        self.page.locator("#local-publish").click()
        expect(self.page.locator("#local-control-status")).to_contain_text(
            "REVISION_PUBLISHED_LOCAL")
        expect(self.page.locator("#local-resume")).to_be_enabled()
        self.assertTrue(self.page.locator("#local-run").is_disabled())
        self.page.locator("#local-resume").click()
        expect(self.page.locator("#local-run")).to_be_enabled()

    def test_public_github_read_ui_requires_active_workflow_and_valid_repo(self):
        self.assertTrue(self.page.locator("#public-github-preview").is_disabled())
        self.create_functional_local_workflow()
        self.assertTrue(self.page.locator("#public-github-preview").is_disabled())
        self.page.locator("#public-github-repo").fill("Octocat/Hello-World")
        expect(self.page.locator("#public-github-preview")).to_be_enabled()
        self.assertTrue(self.page.locator("#public-github-commit").is_disabled())
        self.page.locator("#public-github-repo").fill("https://evil.example/secret")
        self.assertTrue(self.page.locator("#public-github-preview").is_disabled())
        self.assertTrue(self.page.locator("#public-github-commit").is_disabled())
        self.assertIn("No current source snapshot",
                      self.page.locator("#public-github-result").inner_text())

    def test_public_github_read_ui_disabled_during_local_pause(self):
        self.create_functional_local_workflow()
        self.page.locator("#public-github-repo").fill("Octocat/Hello-World")
        expect(self.page.locator("#public-github-preview")).to_be_enabled()
        self.page.locator("#local-pause").click()
        expect(self.page.locator("#local-control-status")).to_contain_text(
            "PAUSED_LOCAL")
        self.assertTrue(self.page.locator("#public-github-preview").is_disabled())
        self.assertTrue(self.page.locator("#public-github-commit").is_disabled())
        self.page.locator("#local-resume").click()
        expect(self.page.locator("#local-control-status")).to_contain_text(
            "RESUMED_LOCAL")
        expect(self.page.locator("#public-github-preview")).to_be_enabled()
        # No real GitHub request is made by either browser gate test.

    def test_operator_session_restored_on_reload_without_local_storage(self):
        self.page.reload(wait_until="networkidle")
        expect(self.page.locator("#operator-gate")).to_be_hidden()
        expect(self.page.locator("#operator-logout")).to_be_visible()
        # Browser cannot read the HttpOnly credential; only cookie transport.
        self.assertEqual([],self.page.evaluate("""() => [...document.cookie.split(';')]
            .filter(x => x.includes('aas_session'))"""))
        self.assertTrue(self.page.evaluate("""() => {
            return !sessionStorage.getItem('aas_session')
                && !localStorage.getItem('aas_session'); }"""))

    def test_operator_logout_blocks_api_until_fresh_login(self):
        self.page.locator("#operator-logout").click()
        expect(self.page.locator("#operator-gate")).to_be_visible()
        denied=self.page.evaluate("""async () => {
            const r=await fetch('/api/local-workflows');return r.status;}""")
        self.assertEqual(401,denied)
        self.page.locator("#operator-password").fill("wrong_ci_password")
        self.page.locator("#operator-login-button").click()
        expect(self.page.locator("#operator-login-status")).to_contain_text("Login rejected")
        expect(self.page.locator("#operator-gate")).to_be_visible()
        self.page.locator("#operator-password").fill("ci_only_fixture_password_1234")
        self.page.locator("#operator-login-button").click()
        expect(self.page.locator("#operator-gate")).to_be_hidden()

    def test_missing_csrf_rejected_even_with_valid_session(self):
        result=self.page.evaluate("""async () => {
          const r=await fetch('/api/local-create',{
           method:'POST',headers:{'Content-Type':'application/json'},
           body:JSON.stringify({workflow_id:'csrf_browser',
            display_name:'CSRF Browser Test',service_filter:'any',
            min_budget_tier:1,followup_policy:'draft_only'})});
          return {status:r.status,body:await r.json()};}""")
        self.assertEqual(403,result["status"])
        self.assertEqual("operator_csrf_required",result["body"]["error"])
        self.assertFalse(self.page.locator("#operator-gate").is_visible())

    def test_source_permissions_browser_requires_reauthentication_and_revocation(self):
        self.create_functional_local_workflow()
        self.page.locator("#public-github-repo").fill("exampleorg/acceptance-repo")
        expect(self.page.locator("#public-github-preview")).to_be_disabled()
        self.page.locator("#source-permission-repo").fill("exampleorg/acceptance-repo")
        self.page.locator("#source-permission-password").fill("wrong-passphrase")
        self.page.locator("#source-permission-grant").click()
        expect(self.page.locator("#source-permission-status")).to_contain_text(
            "invalid_operator_credentials")
        expect(self.page.locator("#public-github-preview")).to_be_disabled()
        self.page.locator("#source-permission-password").fill(
            "ci_only_fixture_password_1234")
        self.page.locator("#source-permission-grant").click()
        expect(self.page.locator("#source-permission-list")).to_contain_text(
            "exampleorg/acceptance-repo")
        expect(self.page.locator("#public-github-preview")).to_be_enabled()
        self.page.locator("#source-permission-password").fill(
            "ci_only_fixture_password_1234")
        self.page.locator("#source-permission-revoke").click()
        expect(self.page.locator("#source-permission-list")).not_to_contain_text(
            "exampleorg/acceptance-repo")
        expect(self.page.locator("#public-github-preview")).to_be_disabled()

    def test_zz_owner_rotation_revokes_browser_session_and_accepts_new_password(self):
        old="ci_only_fixture_password_1234"
        replacement="ci_only_fixture_password_ROTATED_44"
        self.page.locator("#rotate-old-password").fill(old)
        self.page.locator("#rotate-new-password").fill(replacement)
        self.page.locator("#rotate-password-button").click()
        expect(self.page.locator("#operator-gate")).to_be_visible()
        status=self.page.evaluate("""async () => {
            const r=await fetch('/api/local-workflows');return r.status;}""")
        self.assertEqual(401,status)
        self.page.locator("#operator-password").fill(old)
        self.page.locator("#operator-login-button").click()
        expect(self.page.locator("#operator-login-status")).to_contain_text(
            "invalid_operator_credentials")
        self.page.locator("#operator-password").fill(replacement)
        self.page.locator("#operator-login-button").click()
        expect(self.page.locator("#operator-gate")).to_be_hidden()
        # Restore the fixture password to avoid retaining an altered test
        # credential in the shared CI process.
        self.page.locator("#rotate-old-password").fill(replacement)
        self.page.locator("#rotate-new-password").fill(old)
        self.page.locator("#rotate-password-button").click()
        expect(self.page.locator("#operator-gate")).to_be_visible()
        self.page.locator("#operator-password").fill(old)
        self.page.locator("#operator-login-button").click()
        expect(self.page.locator("#operator-gate")).to_be_hidden()

    def test_authenticated_security_audit_is_visible_and_redacted(self):
        self.page.locator("#operator-audit-refresh").click()
        expect(self.page.locator("#operator-audit-list")).to_contain_text(
            "OPERATOR_LOGIN")
        raw=self.page.locator("#operator-audit-list").inner_text()
        self.assertNotIn("ci_only_fixture_password_1234",raw)
        self.assertNotIn("aas_session",raw)
        self.assertNotIn("csrf_token",raw)
        self.assertNotIn("recovery_code",raw)
        self.assertIn('"source_fingerprint"',raw)

    def test_mobile_375px_five_cards_and_workflows(self):
        self.page.set_viewport_size({"width": 375, "height": 812})
        self.assertEqual(5, self.page.locator(".template-card").count())
        self.page.locator(".template-card").last.get_by_role(
            "button", name="Configure mock").click()
        self.assertTrue(self.page.locator("#knowledge-form").is_visible())
        self.page.locator("#knowledge-run").click()
        expect(self.page.locator("#knowledge-state")).to_contain_text("ROUTING_PREVIEW_PREPARED")
        (ROOT / "browser-artifacts").mkdir(exist_ok=True)
        self.page.screenshot(path=str(ROOT / "browser-artifacts" / "mobile-375px.png"),
                             full_page=True)
        # Outer page should not require horizontal scrolling at phone width.
        overflow = self.page.evaluate("""() => ({
            page: document.documentElement.scrollWidth,
            viewport: window.innerWidth,
            culprits: [...document.querySelectorAll('body *')]
              .filter(el => el.getBoundingClientRect().right > window.innerWidth + 1)
              .slice(0, 12).map(el => ({
                tag: el.tagName, id: el.id, className: String(el.className).slice(0, 60),
                right: Math.round(el.getBoundingClientRect().right)
              }))
        })""")
        self.assertLessEqual(
            overflow["page"], overflow["viewport"] + 1,
            "mobile horizontal overflow: " + str(overflow))


if __name__ == "__main__":
    unittest.main()
