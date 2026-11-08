"""Real Chromium acceptance of owner-controlled dual-Agent job + typed note transfer.

Synthetic two-Agent fixture; no paid models or external provider calls.
"""
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]
URL="http://127.0.0.1:8765"
PASS="ci_only_fixture_password_1234"


class DelegationsBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=subprocess.Popen(
            [sys.executable,"-u","tests_browser/serve_auth_fixture.py"],
            cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            env={**os.environ,"AAS_FIXTURE_DELEGATIONS":"1","PYTHONUNBUFFERED":"1"})
        try:
            for _ in range(100):
                if cls.server.poll() is not None:raise RuntimeError("fixture exited before readiness")
                try:
                    with urlopen(URL+"/api/health",timeout=.5) as res:
                        if res.status==200:break
                except (URLError,TimeoutError,OSError):time.sleep(.1)
            else:raise RuntimeError("local fixture unresponsive")
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

    def test_owner_approved_queue_and_explicit_typed_handoff(self):
        ctx=self.browser.new_context(viewport={"width":1365,"height":900})
        self.addCleanup(ctx.close)
        page=ctx.new_page()
        errors=[]
        page.on("pageerror",lambda e:errors.append(str(e)))
        page.goto(URL+"/delegations",wait_until="networkidle")
        expect(page.locator("#gate")).to_be_visible()
        expect(page.locator("#create-job")).to_be_disabled()
        page.locator("#password").fill(PASS)
        page.locator("#login").click()
        expect(page.locator("#gate")).to_be_hidden()
        expect(page.locator("#cap-note")).to_contain_text("Two-Agent delegation: enabled")
        expect(page.locator("#cap-note")).to_contain_text("Typed result transfer: enabled")
        expect(page.locator("#create-status")).to_contain_text("2 eligible approved")
        page.locator("#job-id").fill("browser_delegation")
        page.locator("#job-label").fill("Research to review")
        page.locator("#source-plan").select_option("a"*24)
        page.locator("#target-plan").select_option("b"*24)
        expect(page.locator("#pair-preview")).to_contain_text("research_browser → review_browser")
        page.locator("#create-password").fill(PASS)
        page.once("dialog",lambda d:d.accept())
        page.locator("#create-job").click()
        expect(page.locator("#job-status")).to_have_text("DRAFT")
        expect(page.locator("#job-detail")).to_contain_text("research_browser")
        page.locator("#job-password").fill(PASS)
        page.once("dialog",lambda d:d.accept())
        page.locator("#job-arm").click()
        expect(page.locator("#job-status")).to_have_text("ACTIVE")
        page.locator("#job-password").fill(PASS)
        page.once("dialog",lambda d:d.accept())
        page.locator("#job-next").click()
        expect(page.locator("#job-detail")).to_contain_text("COMPLETED")
        expect(page.locator("#job-detail")).to_contain_text("WAITING")
        # Source completion barrier does not execute target or transfer raw data.
        page.locator("#handoff-note").fill("browser_result")
        page.locator("#handoff-password").fill(PASS)
        page.locator("#handoff-preview").click()
        expect(page.locator("#handoff-output")).to_contain_text("DECIMAL_NUMBER")
        expect(page.locator("#handoff-output")).to_contain_text("Value: 56")
        expect(page.locator("#handoff-output")).to_contain_text("PREVIEW ONLY")
        page.locator("#handoff-password").fill(PASS)
        page.once("dialog",lambda d:d.accept())
        page.locator("#handoff-commit").click()
        expect(page.locator("#handoff-output")).to_contain_text("COMMITTED")
        receipt_id=page.locator("#handoff-receipt-id").input_value()
        self.assertRegex(receipt_id,r"^[0-9a-f]{32}$")
        page.locator("#handoff-inspect").click()
        expect(page.locator("#handoff-output")).to_contain_text("COMMITTED")
        evidence=ctx.request.get(URL+"/api/agent-scoped-memory?project_id=browser_project&agent_id=review_browser")
        self.assertEqual(evidence.status,200)
        note=next(n for n in evidence.json()["notes"] if n["note_id"]=="browser_result")
        self.assertEqual(note["source_kind"],"OWNER_IMPORTED_UNVERIFIED")
        self.assertIn("DECIMAL_NUMBER = 56",note["text"])
        # No target execution before separate owner tick.
        queue=ctx.request.get(URL+"/api/durable-jobs").json()["jobs"]
        self.assertEqual(queue[0]["nodes"][1]["status"],"WAITING")
        page.locator("#job-password").fill(PASS)
        page.once("dialog",lambda d:d.accept())
        page.locator("#job-next").click()
        expect(page.locator("#job-status")).to_have_text("COMPLETED")
        artifacts=ROOT/"browser-artifacts";artifacts.mkdir(exist_ok=True)
        page.screenshot(path=str(artifacts/"delegations-desktop.png"),full_page=True)
        page.set_viewport_size({"width":390,"height":844})
        page.screenshot(path=str(artifacts/"delegations-mobile.png"),full_page=True)
        self.assertFalse(errors)
        page.locator("#logout").click()
        expect(page.locator("#gate")).to_be_visible()
        expect(page.locator("#handoff-receipt-id")).to_be_empty()
        expect(page.locator("#create-job")).to_be_disabled()


if __name__=="__main__":
    unittest.main()
