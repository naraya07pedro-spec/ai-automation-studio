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
            env.update(HOME=str(home), USERPROFILE=str(home), PYTHONUNBUFFERED="1",
                       AAS_WEBHOOK_URL="https://demo.n8n.cloud/webhook/disposable-smoke")
            proc = subprocess.Popen(
                [sys.executable, str(ROOT / "app" / "local_server.py"),
                 "--enable-outbound-webhook"],
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

                # Actual scheduler HTTP integration, no model or simulated service.
                with opener.open(BASE + "/api/local-schedules", timeout=2) as response:
                    sched = json.load(response)
                self.assertTrue(sched["worker_running"])
                self.assertEqual(sched["schedules"], [])

                def post(path, data):
                    req = Request(BASE + path,
                                  json.dumps(data).encode("utf-8"),
                                  headers={"Content-Type": "application/json",
                                           "X-AAS-CSRF": login_csrf},
                                  method="POST")
                    with opener.open(req, timeout=4) as response:
                        return response.status, json.load(response)

                login_csrf = data["csrf_token"]
                status, made = post("/api/local-create", {
                    "workflow_id": "scheduled_studio", "display_name": "Scheduled Studio",
                    "service_filter": "automation", "min_budget_tier": 2,
                    "followup_policy": "review_required"})
                self.assertEqual(status, 201)
                self.assertEqual(post("/api/local-activate", {
                    "workflow_id": made["workflow_id"], "expected_version": 1})[0], 200)
                status, created = post("/api/local-schedule-create", {
                    "workflow_id": "scheduled_studio", "cadence": "minutes",
                    "timezone": "Asia/Jakarta", "service_code": "automation",
                    "budget_tier": 2, "timeline": "soon", "every": 1})
                self.assertEqual(status, 201)
                self.assertEqual(created["status"], "ACTIVE")
                with opener.open(BASE + "/api/local-schedules", timeout=2) as response:
                    self.assertEqual(len(json.load(response)["schedules"]), 1)
                status, paused = post("/api/local-schedule-pause", {
                    "schedule_id": created["schedule_id"], "expected_epoch": 0})
                self.assertEqual(status, 200)
                self.assertEqual(paused["status"], "PAUSED")

                # Real configured connector, no network send.
                with opener.open(BASE + "/api/connector-catalog", timeout=2) as response:
                    catalog = json.load(response)
                self.assertTrue(catalog["connectors"][0]["configured"])
                self.assertEqual(catalog["webhook_destination_hostname"], "demo.n8n.cloud")
                status, receipt = post("/api/local-run", {
                    "workflow_id": "scheduled_studio", "event_id": "connector_smoke_001",
                    "service_code": "automation", "budget_tier": 2, "timeline": "soon"})
                self.assertEqual(status, 200)
                self.assertEqual(receipt["status"], "RECORDED_LOCAL")
                status, preview = post("/api/outbound-webhook-preview", {
                    "workflow_id": "scheduled_studio", "event_id": "connector_smoke_001"})
                self.assertEqual(status, 200)
                self.assertEqual(preview["status"], "PREVIEW_ONLY")
                self.assertEqual(preview["external_write_count"], 0)
                with self.assertRaises(HTTPError) as rejected:
                    post("/api/outbound-webhook-commit", {
                        "workflow_id": "scheduled_studio",
                        "event_id": "connector_smoke_001",
                        "preview_token": preview["preview_token"],
                        "payload_sha256": preview["payload_sha256"],
                        "current_password": "wrong-owner-password"})
                self.assertIn(rejected.exception.code, (401, 403, 429))
                with opener.open(BASE + "/api/outbound-history", timeout=2) as response:
                    ledger = json.load(response)
                self.assertEqual(ledger["deliveries"][0]["status"], "PREVIEWED")
                # Never attempt a network send in this CI smoke.
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
