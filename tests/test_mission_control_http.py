"""One disposable end-to-end smoke for the actual Mission Control HTTP route.

No live model, external services, private files or comprehensive test marathon.
"""
import http.cookiejar
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_operator_auth import LocalOperatorAuth

BASE = "http://127.0.0.1:8765"
PASSWORD = "DisposableStudioSmokeOwner2026!"


class MissionControlSmoke(unittest.TestCase):
    def test_actual_http_page_login_and_preexisting_auth_boundary(self):
        with tempfile.TemporaryDirectory(prefix="aas-mission-") as temp:
            home = Path(temp)
            owner = home / ".ai-automation-studio" / "operator.json"
            LocalOperatorAuth.provision(owner, PASSWORD)
            env = dict(os.environ)
            env.update(HOME=str(home), USERPROFILE=str(home), PYTHONUNBUFFERED="1")
            proc = subprocess.Popen(
                [sys.executable, str(ROOT / "app" / "local_server.py")],
                cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
            try:
                ready = False
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        break
                    try:
                        with opener.open(BASE + "/mission", timeout=1) as response:
                            page = response.read().decode("utf-8")
                            ready = response.status == 200
                            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
                            self.assertIn("Mission Control", page)
                            self.assertIn("/api/local-agent-plan-create", page)
                            self.assertIn("/api/local-agent-run", page)
                            self.assertIn("operator-login", page)
                            break
                    except (OSError, URLError, TimeoutError):
                        time.sleep(0.2)
                self.assertTrue(ready, "Mission Control did not start on the real loopback HTTP server")
                with self.assertRaises(HTTPError) as denied:
                    opener.open(BASE + "/api/local-agent-plans", timeout=2)
                self.assertEqual(denied.exception.code, 401)

                req = Request(
                    BASE + "/api/operator-login",
                    json.dumps({"password": PASSWORD}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST")
                with opener.open(req, timeout=4) as response:
                    data = json.load(response)
                self.assertTrue(data["authenticated"])
                self.assertTrue(data["csrf_token"])
                with opener.open(BASE + "/api/local-agent-capabilities", timeout=2) as response:
                    capabilities = json.load(response)
                self.assertFalse(capabilities["enabled"])
                self.assertFalse(capabilities["planning"])
            finally:
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=7)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=7)


if __name__ == "__main__":
    unittest.main()
