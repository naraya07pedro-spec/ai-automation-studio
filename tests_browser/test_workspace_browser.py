"""Chromium contract for the separate Agent Workspace (synthetic backend only)."""
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
URL="http://127.0.0.1:8765"


class WorkspaceBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=subprocess.Popen(
            [sys.executable, "-u", "tests_browser/serve_auth_fixture.py"],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(80):
                if cls.server.poll() is not None:
                    raise RuntimeError("synthetic fixture exited")
                try:
                    with urlopen(URL+"/api/health",timeout=.5) as r:
                        if r.status==200:break
                except (URLError, TimeoutError, OSError):
                    time.sleep(.1)
            else:raise RuntimeError("fixture not ready")
            cls.playwright=sync_playwright().start()
            cls.browser=cls.playwright.chromium.launch(headless=True)
        except Exception:
            cls.server.terminate();cls.server.wait(timeout=5)
            raise

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls,"browser"):cls.browser.close()
        if hasattr(cls,"playwright"):cls.playwright.stop()
        cls.server.terminate()
        try:cls.server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.server.kill();cls.server.wait(timeout=5)

    def login(self, page):
        page.goto(URL+"/workspace",wait_until="networkidle")
        expect(page.locator("#gate")).to_be_visible()
        page.locator("#password").fill("ci_only_fixture_password_1234")
        page.locator("#login").click()
        expect(page.locator("#gate")).to_be_hidden()
        expect(page.locator("#model")).to_have_value("fixture:model")

    def test_scoped_agent_lifecycle_revokes_bound_plan(self):
        context=self.browser.new_context(viewport={"width":1280,"height":900},accept_downloads=True)
        self.addCleanup(context.close)
        page=context.new_page()
        problems=[];page.on("pageerror",lambda e:problems.append(str(e)))
        self.login(page)
        page.locator("#nav-agents").click()
        page.locator("#new-agent").click()
        expect(page.locator("#agent-dialog")).to_be_visible()
        page.locator("#new-id").fill("workspace_browser_agent")
        page.locator("#new-name").fill("Research Browser")
        page.locator("#new-desc").fill("Synthetic testing with real persisted policy")
        page.locator("#new-password").fill("ci_only_fixture_password_1234")
        page.locator("#save-agent").click()
        expect(page.locator("#agent-dialog")).to_be_hidden()
        expect(page.locator("#active-agent-badge")).to_have_text("Research Browser")
        page.locator("#project").fill("workspace_browser")
        page.locator("#project").dispatch_event("change")
        page.locator("#new-note").click()
        page.locator("#note-id").fill("context_browser")
        page.locator("#note-text").fill("Owner-curated memory for a bounded task.")
        page.locator("#note-password").fill("ci_only_fixture_password_1234")
        page.locator("#save-note").click()
        expect(page.locator("#note-dialog")).to_be_hidden()
        expect(page.locator("#memories")).to_contain_text("context_browser")
        page.locator("#memories input[value=context_browser]").check()
        page.locator("#task").fill("Calculate 7 multiplied by 8")
        page.locator("#submit-task").click()
        expect(page.locator("#plan-box")).to_be_visible()
        expect(page.locator("#plan-status-label")).to_have_text("PREPARED")
        page.locator("#approve-plan").click()
        expect(page.locator("#plan-status-label")).to_have_text("APPROVED")
        page.locator(".note-row .note-control").filter(has_text="Edit").click()
        expect(page.locator("#note-dialog")).to_be_visible()
        page.locator("#note-text").fill("Updated owner-curated context, invalidates previous plans.")
        page.locator("#note-password").fill("ci_only_fixture_password_1234")
        page.locator("#save-note").click()
        expect(page.locator("#note-dialog")).to_be_hidden()
        page.once("dialog",lambda dialog:dialog.accept())
        page.locator("#next-step").click()
        expect(page.locator("#status")).to_contain_text("not confirmed")
        page.locator("#nav-agents").click()
        page.locator(".agent-card button").filter(has_text="Edit / disable").click()
        page.locator("#new-name").fill("Research Browser Revised")
        page.locator("#new-password").fill("ci_only_fixture_password_1234")
        page.locator("#save-agent").click()
        expect(page.locator("#agent-dialog")).to_be_hidden()
        expect(page.locator("#active-agent-badge")).to_have_text("Research Browser Revised")
        page.locator("#nav-agents").click()
        page.locator(".agent-card button").filter(has_text="Edit / disable").click()
        page.locator("#new-password").fill("ci_only_fixture_password_1234")
        page.once("dialog",lambda dialog:dialog.accept())
        page.locator("#disable-agent").click()
        expect(page.locator("#agent-dialog")).to_be_hidden()
        expect(page.locator("#agent-select")).to_have_value("")
        expect(page.locator("#agent-grid")).to_contain_text("disabled")
        self.assertFalse(problems)

    def test_explicit_cloud_consent_and_readonly_receipt(self):
        context=self.browser.new_context(viewport={"width":1100,"height":900})
        self.addCleanup(context.close)
        page=context.new_page()
        problems=[];page.on("pageerror",lambda e:problems.append(str(e)))
        self.login(page)
        page.locator("#mode-cloud").click()
        expect(page.locator("#cloud-config")).to_be_visible()
        expect(page.locator("#submit-task")).to_be_disabled()
        page.locator("#cloud-provider").select_option("openai_responses")
        page.locator("#cloud-model").fill("gpt-5.1")
        page.locator("#cloud-owner").fill("ci_only_fixture_password_1234")
        page.locator("#cloud-consent").check()
        expect(page.locator("#submit-task")).to_be_enabled()
        page.locator("#task").fill("Synthetic cloud text only. No actual provider billing.")
        page.once("dialog",lambda dialog:dialog.accept())
        page.locator("#submit-task").click()
        expect(page.locator("#cloud-result")).to_contain_text("COMPLETED")
        expect(page.locator(".message.assistant").last).to_contain_text("Synthetic cloud response")
        receipt=page.locator("#cloud-receipt-id").input_value()
        self.assertRegex(receipt,r"^[0-9a-f]{32}$")
        expect(page.locator("#cloud-consent")).not_to_be_checked()
        expect(page.locator("#cloud-owner")).to_be_empty()
        page.locator("#cloud-receipt-check").click()
        expect(page.locator("#status")).to_contain_text("Read-only receipt inspected")
        page.locator("#logout").click()
        expect(page.locator("#gate")).to_be_visible()
        expect(page.locator("#cloud-receipt-id")).to_be_empty()
        self.assertFalse(problems)

    def test_owner_unlock_ask_review_execute_logout_and_mobile(self):
        context=self.browser.new_context(viewport={"width":1440,"height":900})
        self.addCleanup(context.close)
        page=context.new_page()
        errors=[]
        page.on("pageerror",lambda err:errors.append(str(err)))
        page.goto(URL+"/workspace",wait_until="networkidle")
        expect(page.locator("#gate")).to_be_visible()
        expect(page.locator("#submit-task")).to_be_disabled()
        page.locator("#password").fill("ci_only_fixture_password_1234")
        page.locator("#login").click()
        expect(page.locator("#gate")).to_be_hidden()
        expect(page.locator("#model")).to_have_value("fixture:model")
        expect(page.locator("#submit-task")).to_be_enabled()
        page.locator("#task").fill("Calculate a safe deterministic result")
        page.locator("#submit-task").click()
        expect(page.locator("#plan-box")).to_be_visible()
        expect(page.locator("#plan-steps .step")).to_have_count(2)
        expect(page.locator("#plan-status-label")).to_have_text("PREPARED")
        page.locator("#approve-plan").click()
        expect(page.locator("#plan-status-label")).to_have_text("APPROVED")
        page.once("dialog",lambda dialog:dialog.accept())
        page.locator("#next-step").click()
        expect(page.locator("#plan-status-label")).to_have_text("NEXT_READY")
        expect(page.locator("#plan-results")).to_contain_text("56")
        page.once("dialog",lambda dialog:dialog.accept())
        page.locator("#next-step").click()
        expect(page.locator("#plan-status-label")).to_have_text("COMPLETED")
        page.locator("#nav-agents").click()
        expect(page.locator("#agents-view")).to_be_visible()
        expect(page.locator("#agent-grid")).to_contain_text("No matching Agent Profiles")
        page.locator("#nav-workspace").click()
        page.locator("#mode-ask").click()
        page.locator("#task").fill("Ask the synthetic local responder")
        page.locator("#submit-task").click()
        expect(page.locator(".message.assistant").last).to_contain_text("Fixture answer")
        artifacts=ROOT/"browser-artifacts"
        artifacts.mkdir(exist_ok=True)
        page.screenshot(path=str(artifacts/"agent-workspace-desktop.png"),full_page=True)
        page.set_viewport_size({"width":390,"height":844})
        page.screenshot(path=str(artifacts/"agent-workspace-mobile.png"),full_page=True)
        self.assertFalse(errors,"Browser JS errors: "+repr(errors))
        page.locator("#logout").click()
        expect(page.locator("#gate")).to_be_visible()
        expect(page.locator("#submit-task")).to_be_disabled()


if __name__=="__main__":
    unittest.main()
