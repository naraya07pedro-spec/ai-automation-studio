"""New Agent Workspace surface: real route, owner login and fail-closed APIs."""
import http.client
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_operator_auth import LocalOperatorAuth
from local_server import create_handler

PASSWORD = "fixture-only-agent-workspace-owner-password!"


class AgentWorkspaceSurfaceTests(unittest.TestCase):
    def setUp(self):
        auth = LocalOperatorAuth.ephemeral_for_tests(PASSWORD)
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), create_handler(operator_auth=auth))
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 4)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path):
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode()
        finally:
            conn.close()

    def test_new_route_is_login_accessible_without_exposing_api(self):
        status, headers, html = self.get("/workspace")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn("<title>Workspace | Personal AI Harness</title>", html)
        self.assertIn('id="login-form"', html)
        self.assertIn('id="gate"', html)
        self.assertIn('id="plan-box"', html)
        self.assertIn('id="agent-grid"', html)
        self.assertIn('id="memories"', html)
        self.assertIn("csrf_token", html)
        # The page itself is public so the owner can sign in; account data is not.
        self.assertEqual(self.get("/api/agent-profiles")[0], 401)
        self.assertEqual(self.get("/api/local-agent-plans")[0], 401)
        self.assertEqual(self.get("/api/agent-scoped-memory?project_id=sample&agent_id=agent")[0], 401)

    def test_delegations_page_is_accessible_only_as_a_login_shell(self):
        code, headers, html = self.get("/delegations")
        self.assertEqual(code, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn("<title>Delegations | Personal AI Harness</title>", html)
        self.assertIn('id="login-form"', html)
        self.assertIn('id="handoff-preview-form"', html)
        self.assertIn('id="reconcile-plan"', html)
        self.assertIn("/api/durable-job-create", html)
        self.assertIn("/api/agent-typed-handoff-preview", html)
        self.assertIn("current_password", html)
        self.assertNotIn("innerHTML", html)
        self.assertNotIn("localStorage", html)
        self.assertEqual(self.get("/api/durable-jobs")[0], 401)
        self.assertEqual(self.get("/api/agent-typed-handoff-receipt?preview_id="+"a"*32)[0], 401)

    def test_existing_mission_unchanged_and_no_unreviewed_action(self):
        old_status, _, old_html = self.get("/mission")
        self.assertEqual(old_status, 200)
        self.assertIn('id="chat-form"', old_html)
        html = self.get("/workspace")[2]
        self.assertIn("/api/agent-profile-create", html)
        self.assertIn("/api/local-agent-plan-create", html)
        self.assertIn('"/api/local-agent-plan-"+name', html)
        self.assertIn("confirm(", html)
        self.assertNotIn("localStorage", html)
        self.assertNotIn("sessionStorage", html)
        self.assertNotIn("innerHTML", html)
        self.assertNotIn("eval(", html)


if __name__ == "__main__":
    unittest.main()
