"""Chromium acceptance: actual owner-gated SQLite local-rule Automation Library.

No model, provider request, webhook, CRM or external effects; existing real
LocalWorkflowCore is exercised through the same API used by the browser.
"""
import os
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
PASSWORD = "ci_only_fixture_password_1234"


class AutomationLibraryBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = subprocess.Popen(
            [sys.executable, "-u", "tests_browser/serve_auth_fixture.py"],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "PYTHONUNBUFFERED": "1"})
        try:
            for _ in range(100):
                if cls.server.poll() is not None:
                    raise RuntimeError("synthetic local fixture exited")
                try:
                    with urlopen(URL + "/api/health", timeout=.5) as response:
                        if response.status == 200:
                            break
                except (URLError, TimeoutError, OSError):
                    time.sleep(.1)
            else:
                raise RuntimeError("synthetic fixture not ready")
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

    def test_reviewed_local_rule_lifecycle_dedupe_and_history(self):
        context = self.browser.new_context(viewport={"width": 1365, "height": 900})
        self.addCleanup(context.close)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(URL + "/automations", wait_until="networkidle")
        expect(page.locator("#gate")).to_be_visible()
        expect(page.locator("#create")).to_be_disabled()
        page.locator("#password").fill(PASSWORD)
        page.locator("#login").click()
        expect(page.locator("#gate")).to_be_hidden()
        expect(page.locator("#workflow-count")).to_contain_text("0 saved")
        expect(page.locator("#workflows")).to_contain_text("No saved workflows")
        page.locator("#new-id").fill("browser_local_rule")
        page.locator("#new-name").fill("Browser Local Rule")
        page.locator("#new-filter").select_option("automation")
        page.locator("#new-budget").select_option("2")
        page.locator("#new-policy").select_option("draft_only")
        page.locator("#create").click()
        expect(page.locator("#create-status")).to_contain_text("Draft browser_local_rule saved")
        expect(page.locator(".automation")).to_have_count(1)
        expect(page.locator("#chosen-status")).to_have_text("DRAFT")
        expect(page.locator("#run")).to_be_disabled()
        page.locator('.filters button[data-filter="consulting"]').click()
        expect(page.locator("#workflows")).to_contain_text("No workflows match")
        page.locator('.filters button[data-filter="automation"]').click()
        expect(page.locator(".automation")).to_have_count(1)
        expect(page.locator("#workflow-details")).to_contain_text("draft_only")
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("#activate").click()
        expect(page.locator("#chosen-status")).to_have_text("ACTIVE")
        expect(page.locator("#run")).to_be_enabled()
        page.locator("#event-id").fill("browser_event_001")
        page.locator("#service").select_option("automation")
        page.locator("#budget").select_option("2")
        page.locator("#timeline").select_option("urgent")
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("#run").click()
        expect(page.locator("#receipt")).to_contain_text("RECORDED_LOCAL")
        expect(page.locator("#receipt")).to_contain_text("QUALIFIED_DRAFT_ONLY")
        expect(page.locator("#history")).to_contain_text("browser_event_001")
        expect(page.locator("#workflow-details")).to_contain_text("Recorded run count")
        # Exactly same ID+input reuses previously stored receipt (not duplicate).
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("#run").click()
        expect(page.locator("#receipt")).to_contain_text("REPLAYED_LOCAL")
        expect(page.locator("#run-status")).to_contain_text("original saved decision")
        # A changed event with the same ID is rejected; inspect history rather than retry.
        page.locator("#service").select_option("consulting")
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("#run").click()
        expect(page.locator("#run-status")).to_contain_text("event_id_payload_conflict")
        expect(page.locator("#history")).to_contain_text("total 1 saved runs")
        result = context.request.get(URL + "/api/local-workflows")
        self.assertEqual(result.status, 200)
        self.assertEqual(result.json()["external_dispatch_count"], 0)
        self.assertEqual(result.json()["workflows"][0]["run_count"], 1)
        artifacts = ROOT / "browser-artifacts"
        artifacts.mkdir(exist_ok=True)
        page.screenshot(path=str(artifacts / "automation-library-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(artifacts / "automation-library-mobile.png"), full_page=True)
        self.assertFalse(errors, "Browser JS errors: " + repr(errors))
        page.locator("#logout").click()
        expect(page.locator("#gate")).to_be_visible()
        expect(page.locator("#create")).to_be_disabled()
        expect(page.locator("#event-id")).to_be_empty()
        self.assertEqual(context.request.get(URL + "/api/local-workflows").status, 401)


if __name__ == "__main__":
    unittest.main()
