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
            [sys.executable, "-u", "app/local_server.py"],
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
        self.assertEqual(5, self.page.locator(".template-card").count())
        self.assertEqual(5, self.page.locator(".mode-tag").filter(
            has_text="EXECUTABLE_MOCK").count())
        # The local server runs across test methods. Isolate each synthetic
        # event ID by test identity, while preserving same-ID replay *within*
        # each test to verify deduplication.
        suffix = hashlib.sha256(self._testMethodName.encode("ascii")).hexdigest()[:12]
        for selector in ("#event_id", "#ticket-id", "#appointment-id",
                         "#crm-id", "#knowledge-id"):
            self.page.locator(selector).fill("e2e_" + suffix)

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
        self.assertEqual(7, self.page.locator("#timeline .stage").count())
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
        overflow = self.page.evaluate(
            "() => document.documentElement.scrollWidth > window.innerWidth + 1")
        self.assertFalse(overflow, "mobile document horizontal overflow")


if __name__ == "__main__":
    unittest.main()
