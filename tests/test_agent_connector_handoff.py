"""Stage #114 acceptance: real local SQLite/HTTP, encrypted vault, fake HTTPS transport.

Never calls a live model, n8n, Gmail, browser, or Tencent. No real secrets.
"""
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
from pathlib import Path
import http.cookiejar
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import local_server
from local_agent_connector_handoff import (
    AgentConnectorHandoff, AgentConnectorHandoffError)
from local_agent_task_plans import LocalAgentPlanStore
from local_connector_manager import LocalConnectorManager
from local_connector_vault import generate_master_key
from local_operator_auth import LocalOperatorAuth
from local_security_audit import LocalSecurityAudit
from local_workflow_core import LocalWorkflowCore

HAS_CRYPTO = importlib.util.find_spec("cryptography") is not None
PLAN_ID = "c" * 24
PASS = "Stage114-Owner-Password-Private-2026!"
GOAL = "Calculate 17 plus 25. No business action authorized."
WORKFLOW = "handoff_flow"
EVENT = "handoff_event_001"
CONNECTOR = "securehook"


@unittest.skipUnless(HAS_CRYPTO, "Crypto optional: dedicated stage CI installs pinned vault dependency")
class Stage114Contract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.core = LocalWorkflowCore(self.root / "local.sqlite3")
        self.plans = LocalAgentPlanStore(self.root / "plans.sqlite3")
        proposed = {"goal": GOAL, "model": "test-model", "steps": [
            {"tool": "calculate", "args": {
                "a": "17", "b": "25", "operation": "add"}}]}
        created = self.plans.create(proposed, plan_id=PLAN_ID)
        self.assertEqual(created["status"], "PREPARED")
        self.plans.approve(PLAN_ID)
        self.plans.advance(PLAN_ID)
        self.assertEqual(self.plans.get(PLAN_ID)["status"], "COMPLETED")
        self.assertEqual(self.core.create({
            "workflow_id": WORKFLOW, "display_name": "Handoff Flow",
            "service_filter": "automation", "min_budget_tier": 2,
            "followup_policy": "review_required"})[0], 201)
        self.assertEqual(self.core.activate({
            "workflow_id": WORKFLOW, "expected_version": 1})[0], 200)
        self.assertEqual(self.core.run({
            "workflow_id": WORKFLOW, "event_id": EVENT,
            "service_code": "automation", "budget_tier": 2,
            "timeline": "soon"})[0], 200)
        self.sent = []
        def sender(url, payload, delivery_id, secret):
            self.sent.append((url, payload, delivery_id, secret))
            return 202
        self.sender = sender
        self.key = generate_master_key()
        self.vault_file = self.root / "vault" / "connectors.json"
        self.manager = LocalConnectorManager(
            self.core, self.vault_file, sender=self.sender, vault_key=self.key)
        self.manager.add({
            "connector_id": CONNECTOR, "label": "Encrypted Handoff",
            "url": "https://hooks.n8n.cloud/webhook/never-real-stage114",
            "signing_secret": "fixture-private-not-real"})
        self.db_file = self.root / "handoffs.sqlite3"
        self.bridge = AgentConnectorHandoff(
            self.plans, self.core, self.manager, self.db_file)

    def tearDown(self):
        self.bridge.close()
        self.manager.close()
        self.plans.close()
        self.core.close()
        self.temp.cleanup()

    def proposal(self, **change):
        return {"plan_id": PLAN_ID, "connector_id": CONNECTOR,
                "workflow_id": WORKFLOW, "event_id": EVENT, **change}

    def preview(self):
        return self.bridge.preview(self.proposal())

    def commit(self, p, **change):
        return self.bridge.commit({
            **self.proposal(), "plan_sha256": p["plan_sha256"],
            "workflow_sha256": p["workflow_sha256"],
            "payload_sha256": p["payload_sha256"],
            "preview_token": p["preview_token"], **change})

    def test_real_one_shot_handoff_and_private_plan_content_never_leaves(self):
        p = self.preview()
        self.assertEqual(p["status"], "PREVIEW_ONLY")
        self.assertEqual(self.sent, [])
        self.assertFalse(p["plan_results_sent"])
        self.assertNotIn(GOAL, json.dumps(p))
        self.assertNotIn("never-real-stage114", json.dumps(p))
        receipt = self.commit(p)
        self.assertEqual(receipt["status"], "REMOTE_ACCEPTED")
        self.assertEqual(receipt["external_write_attempts"], 1)
        self.assertFalse(receipt["downstream_success_verified"])
        self.assertEqual(len(self.sent), 1)
        url, payload, delivery_id, secret = self.sent[0]
        self.assertEqual(payload["event_id"], EVENT)
        self.assertEqual(payload["schema"], "aas.local_workflow_receipt.v1")
        self.assertNotIn(GOAL, json.dumps(payload))
        self.assertEqual(self.bridge.history()["handoffs"][0]["status"],
                         "REMOTE_ACCEPTED")
        with self.assertRaises(AgentConnectorHandoffError):
            self.commit(p)
        with self.assertRaises(AgentConnectorHandoffError):
            self.preview()
        self.assertEqual(len(self.sent), 1)

    def test_incomplete_plan_is_never_eligible(self):
        with self.plans.db:
            self.plans.db.execute(
                "UPDATE agent_task_plans SET status='PREPARED' WHERE plan_id=?",
                (PLAN_ID,))
        with self.assertRaisesRegex(AgentConnectorHandoffError,
                                    "handoff_plan_not_completed"):
            self.preview()
        self.assertEqual(self.sent, [])

    def test_source_changes_after_preview_block_dispatch(self):
        p = self.preview()
        with self.plans.db:
            self.plans.db.execute(
                "UPDATE agent_task_plans SET goal='changed plan' WHERE plan_id=?",
                (PLAN_ID,))
        with self.assertRaisesRegex(AgentConnectorHandoffError,
                                    "handoff_source_or_preview_changed"):
            self.commit(p)
        self.assertEqual(self.sent, [])

    def test_stale_preview_token_and_cross_destination_are_rejected(self):
        old = self.preview()
        fresh = self.preview()
        with self.assertRaisesRegex(AgentConnectorHandoffError,
                                    "handoff_source_or_preview_changed"):
            self.commit(old)
        with self.assertRaises(AgentConnectorHandoffError):
            self.commit(fresh, connector_id="otherhook")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.commit(fresh)["status"], "REMOTE_ACCEPTED")
        self.assertEqual(len(self.sent), 1)

    def test_reopen_local_ledger_never_replays_external_send(self):
        p = self.preview()
        self.commit(p)
        self.bridge.close()
        self.bridge = AgentConnectorHandoff(
            self.plans, self.core, self.manager, self.db_file)
        self.assertEqual(self.bridge.history()["handoffs"][0]["status"],
                         "REMOTE_ACCEPTED")
        with self.assertRaises(AgentConnectorHandoffError):
            self.commit(p)
        self.assertEqual(len(self.sent), 1)

    def test_network_unknown_does_not_retry_even_after_restart(self):
        def timeout(*_):
            self.sent.append("ONE_NETWORK_ATTEMPT")
            raise TimeoutError("simulated lost acknowledgement")
        self.manager._client(self.manager._entry(CONNECTOR)).sender = timeout
        p = self.preview()
        result = self.commit(p)
        self.assertEqual(result["status"], "OUTCOME_UNCERTAIN")
        self.bridge.close()
        self.bridge = AgentConnectorHandoff(
            self.plans, self.core, self.manager, self.db_file)
        with self.assertRaises(AgentConnectorHandoffError):
            self.commit(p)
        self.assertEqual(self.sent, ["ONE_NETWORK_ATTEMPT"])

    def test_separate_plan_cannot_claim_same_event(self):
        p = self.preview()
        self.plans.create({
            "goal": "independent", "model": "test-model",
            "steps": [{"tool": "utc_now", "args": {}}]}, plan_id="d"*24)
        self.plans.approve("d"*24)
        self.plans.advance("d"*24)
        with self.assertRaisesRegex(AgentConnectorHandoffError,
                                    "handoff_event_already_bound"):
            self.bridge.preview(self.proposal(plan_id="d"*24))
        self.assertEqual(self.sent, [])
        self.commit(p)

    def test_invalid_fields_and_plaintext_manager_fail_closed(self):
        with self.assertRaises(AgentConnectorHandoffError):
            self.bridge.preview({**self.proposal(), "ai_grants_write": True})
        with self.assertRaises(AgentConnectorHandoffError):
            self.bridge.preview(self.proposal(plan_id="../unsafe"))
        plaintext = LocalConnectorManager(
            self.core, self.root / "plaintext" / "unsafe.json",
            sender=self.sender)
        try:
            with self.assertRaisesRegex(AgentConnectorHandoffError,
                                        "encrypted_managed_connector"):
                AgentConnectorHandoff(self.plans, self.core, plaintext)
        finally:
            plaintext.close()

    def test_real_loopback_http_auth_csrf_password_and_history(self):
        LocalOperatorAuth.provision(self.root / "owner.json", PASS)
        owner = LocalOperatorAuth.load(self.root / "owner.json")
        audit = LocalSecurityAudit(self.root / "audit.sqlite3")
        server = ThreadingHTTPServer(("127.0.0.1", 0),
            local_server.create_handler(
                local_core=self.core, local_agent_plan_store=self.plans,
                operator_auth=owner, security_audit=audit,
                managed_connectors=self.manager,
                agent_connector_handoff=self.bridge))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = "http://127.0.0.1:" + str(server.server_port)
        opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
        csrf = None
        def post(route, data, use_csrf=True):
            headers = {"Content-Type": "application/json"}
            if csrf and use_csrf:
                headers["X-AAS-CSRF"] = csrf
            with opener.open(Request(url + route,
                json.dumps(data).encode("utf-8"), method="POST",
                headers=headers), timeout=7) as resp:
                return json.load(resp)
        try:
            with self.assertRaises(HTTPError) as anon:
                opener.open(url + "/api/agent-connector-handoffs", timeout=7)
            self.assertEqual(anon.exception.code, 401)
            csrf = post("/api/operator-login", {"password": PASS})["csrf_token"]
            with self.assertRaises(HTTPError) as denied:
                post("/api/agent-connector-handoff-preview",
                     self.proposal(), use_csrf=False)
            self.assertEqual(denied.exception.code, 403)
            p = post("/api/agent-connector-handoff-preview", self.proposal())
            self.assertEqual(p["status"], "PREVIEW_ONLY")
            confirmed = {
                **self.proposal(), "plan_sha256": p["plan_sha256"],
                "workflow_sha256": p["workflow_sha256"],
                "payload_sha256": p["payload_sha256"],
                "preview_token": p["preview_token"]}
            with self.assertRaises(HTTPError) as bad:
                post("/api/agent-connector-handoff-commit",
                     {**confirmed, "current_password": "bad"})
            self.assertEqual(bad.exception.code, 401)
            self.assertEqual(self.sent, [])
            r = post("/api/agent-connector-handoff-commit",
                     {**confirmed, "current_password": PASS})
            self.assertEqual(r["status"], "REMOTE_ACCEPTED")
            self.assertEqual(len(self.sent), 1)
            with opener.open(url + "/api/agent-connector-handoffs",
                             timeout=7) as response:
                history = json.load(response)
            self.assertEqual(history["handoffs"][0]["status"], "REMOTE_ACCEPTED")
            self.assertTrue(any(e["action"] == "AGENT_CONNECTOR_HANDOFF"
                                for e in audit.recent(40)))
        finally:
            server.shutdown()
            server.server_close()
            worker.join(5)
            audit.close()


if __name__ == "__main__":
    unittest.main()
