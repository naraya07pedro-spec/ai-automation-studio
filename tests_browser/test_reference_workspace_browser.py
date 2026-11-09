"""Playwright acceptance for reference-inspired live Agent Workspace layout.

The fixture is synthetic: assertions prove actual UI/backend wiring, not live
Ollama, provider billing, Gmail, Tencent uptime, or real off-device backups.
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

ROOT=Path(__file__).resolve().parents[1]
URL="http://127.0.0.1:8765"
PASSWORD="ci_only_fixture_password_1234"


class ReferenceWorkspaceBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=subprocess.Popen(
            [sys.executable,"-u","tests_browser/serve_auth_fixture.py"],
            cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            env={**os.environ,"PYTHONUNBUFFERED":"1"})
        try:
            for _ in range(100):
                if cls.server.poll() is not None:
                    raise RuntimeError("fixture server exited")
                try:
                    with urlopen(URL+"/api/health",timeout=.5) as response:
                        if response.status==200:break
                except (URLError,TimeoutError,OSError):
                    time.sleep(.1)
            else:
                raise RuntimeError("fixture server unavailable")
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

    def setup_page(self,width=1500,height=960):
        context=self.browser.new_context(viewport={"width":width,"height":height})
        self.addCleanup(context.close)
        page=context.new_page()
        failures=[]
        page.on("pageerror",lambda error:failures.append(str(error)))
        page.goto(URL+"/workspace",wait_until="networkidle")
        return page,context,failures

    def login(self,page):
        expect(page.locator("#gate")).to_be_visible()
        page.locator("#password").fill(PASSWORD)
        page.locator("#login").click()
        expect(page.locator("#gate")).to_be_hidden()

    def test_desktop_reference_panels_and_owner_actions_are_functional(self):
        page,context,failures=self.setup_page()
        expect(page.get_by_role("heading",name="Agent Workspace")).to_be_visible()
        expect(page.locator(".studio-context")).to_be_visible()
        expect(page.locator(".studio-work-column")).to_be_visible()
        expect(page.locator(".studio-right")).to_be_visible()
        expect(page.locator("#studio-module-grid .studio-module")).to_have_count(8)
        self.assertEqual(context.request.get(URL+"/assets/studio-workspace.css").status,200)
        self.assertEqual(context.request.get(URL+"/assets/studio-workspace.js").status,200)
        # Unauthenticated UI must never invent a running job or backend proof.
        expect(page.locator("#studio-system-status")).to_contain_text("Terkunci")
        self.login(page)
        expect(page.locator("#studio-system-status")).to_contain_text("Terautentikasi")
        expect(page.locator("#studio-current-jobs")).not_to_be_empty()
        page.locator("#studio-action-task").click()
        expect(page.locator("#task")).to_be_focused()
        page.locator("#studio-mode-ask").click()
        expect(page.locator("#mode-ask")).to_have_attribute("aria-pressed","true")
        page.locator("#studio-mode-plan").click()
        expect(page.locator("#mode-plan")).to_have_attribute("aria-pressed","true")
        page.locator("#studio-global-search").fill("research")
        page.locator("#studio-global-search").press("Enter")
        expect(page.locator("#agents-view")).to_be_visible()
        expect(page.locator("#agent-search")).to_have_value("research")
        page.locator("#nav-workspace").click()
        expect(page.locator("#workspace-view")).to_be_visible()
        page.locator("#studio-action-agent").click()
        expect(page.locator("#agents-view")).to_be_visible()
        if page.locator("#agent-dialog").is_visible():
            page.locator("#close-agent").click()
        page.locator("#logout").click()
        expect(page.locator("#gate")).to_be_visible()
        expect(page.locator("#studio-system-status")).to_contain_text("Terkunci")
        self.assertEqual(failures,[])

    def test_mobile_workspace_is_accessible_without_invented_runtime_success(self):
        page,context,failures=self.setup_page(390,844)
        self.login(page)
        expect(page.locator(".rail")).to_have_css("position","fixed")
        expect(page.locator("#studio-module-grid .studio-module")).to_have_count(8)
        expect(page.locator(".studio-work-column")).to_be_visible()
        # On mobile the conversation must appear before configuration/side panels.
        positions=page.evaluate("""() => ({
            work: document.querySelector('.studio-work-column').getBoundingClientRect().top,
            context: document.querySelector('.studio-context').getBoundingClientRect().top,
            overflow: document.documentElement.scrollWidth > innerWidth + 2
        })""")
        self.assertLess(positions["work"],positions["context"])
        self.assertFalse(positions["overflow"])
        page.locator("#studio-action-task").click()
        expect(page.locator("#task")).to_be_focused()
        artifacts=ROOT/"browser-artifacts"
        artifacts.mkdir(exist_ok=True)
        page.screenshot(path=str(artifacts/"workspace-reference-mobile.png"),full_page=True)
        self.assertEqual(failures,[])


    def test_related_module_pages_match_selected_light_theme(self):
        page,context,failures=self.setup_page(1200,900)
        self.login(page)
        for route in ("/automations","/delegations","/jobs","/mission","/handoff"):
            with self.subTest(route=route):
                page.goto(URL+route,wait_until="domcontentloaded")
                nav=page.locator(".studio-module-nav")
                expect(nav).to_be_visible()
                expect(nav.locator('a[aria-current="page"]')).to_have_count(1)
                expect(nav.locator('a[href="/workspace"]')).to_be_visible()
                self.assertEqual(page.evaluate(
                    "getComputedStyle(document.documentElement).colorScheme"),"light")
                self.assertFalse(page.evaluate(
                    "document.documentElement.scrollWidth > innerWidth + 2"))
        self.assertEqual(context.request.get(URL+"/assets/studio-pages.css").status,200)
        self.assertEqual(failures,[])


if __name__=="__main__":
    unittest.main()
