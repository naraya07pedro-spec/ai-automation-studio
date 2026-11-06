"""Actual outbound webhook state machine, single-use owner preview and SSRF gates.

Every transport in this suite is injected; NEVER contact a real n8n endpoint.
"""
from datetime import datetime, timedelta, timezone
import http.client
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_workflow_core import LocalWorkflowCore
from local_outbound_webhook import (
    LocalOutboundWebhook, OutboundError, validate_https_target,
    send_https_once, _resolve_public_ipv4,
)

ENDPOINT = "https://hooks.n8n.cloud/webhook/owner-controlled-example"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)
    def __call__(self):
        return self.now


class OutboundContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.core = LocalWorkflowCore(root / "local.sqlite3")
        code, _ = self.core.create({
            "workflow_id": "connector_studio", "display_name": "Connector Studio",
            "service_filter": "automation", "min_budget_tier": 2,
            "followup_policy": "review_required"})
        self.assertEqual(code, 201)
        code, _ = self.core.activate({"workflow_id": "connector_studio",
                                      "expected_version": 1})
        self.assertEqual(code, 200)
        code, _ = self.core.run({
            "workflow_id": "connector_studio", "event_id": "webhook_evt_001",
            "service_code": "automation", "budget_tier": 2, "timeline": "soon"})
        self.assertEqual(code, 200)
        self.file = root / "outbound.sqlite3"
        self.clock = Clock()
        self.sent = []
        def sender(url,payload,did,secret):
            self.sent.append((url,payload,did,secret))
            return 202
        self.sender = sender
        self.service = LocalOutboundWebhook(
            self.core, self.file, url=ENDPOINT, secret="fixture-signing-secret",
            sender=self.sender, clock=self.clock)

    def tearDown(self):
        self.service.close()
        self.core.close()
        self.tmp.cleanup()

    def preview(self):
        return self.service.preview({"workflow_id": "connector_studio",
                                     "event_id": "webhook_evt_001"})

    @staticmethod
    def confirmed(preview):
        return {"workflow_id": preview["workflow_id"], "event_id": preview["event_id"],
                "preview_token": preview["preview_token"],
                "payload_sha256": preview["payload_sha256"]}

    def test_one_real_callback_after_explicit_prepared_commit(self):
        preview = self.preview()
        self.assertEqual(preview["status"], "PREVIEW_ONLY")
        self.assertEqual(len(self.sent), 0)
        self.assertNotIn("owner-controlled-example", str(preview))
        result = self.service.commit(self.confirmed(preview))
        self.assertEqual(result["status"], "REMOTE_ACCEPTED")
        self.assertEqual(result["http_status"], 202)
        self.assertFalse(result["downstream_success_verified"])
        self.assertEqual(len(self.sent), 1)
        url,payload,did,secret = self.sent[0]
        self.assertEqual(url, ENDPOINT)
        self.assertEqual(payload["decision"], "NEEDS_REVIEW_LOCAL")
        self.assertEqual(payload["event_id"], "webhook_evt_001")
        self.assertEqual(secret, "fixture-signing-secret")
        self.assertEqual(result["delivery_id"], did)
        self.assertEqual(self.service.history()["deliveries"][0]["status"], "REMOTE_ACCEPTED")
        with self.assertRaises(OutboundError):
            self.service.commit(self.confirmed(preview))
        with self.assertRaises(OutboundError):
            self.preview()
        self.assertEqual(len(self.sent), 1)

    def test_unknown_outcome_blocks_replay_even_after_restart(self):
        preview = self.preview()
        def timeout(*args):
            self.sent.append("NETWORK_ATTEMPT")
            raise TimeoutError("lost after remote might accept")
        self.service.sender = timeout
        result = self.service.commit(self.confirmed(preview))
        self.assertEqual(result["status"], "OUTCOME_UNCERTAIN")
        self.assertEqual(result["external_write_attempts"], 1)
        self.service.close()
        self.service = LocalOutboundWebhook(
            self.core, self.file, url=ENDPOINT, secret="fixture-signing-secret",
            sender=self.sender, clock=self.clock)
        with self.assertRaises(OutboundError):
            self.service.commit(self.confirmed(preview))
        with self.assertRaises(OutboundError):
            self.preview()
        self.assertEqual(self.service.history()["deliveries"][0]["status"], "OUTCOME_UNCERTAIN")

    def test_invalid_preview_or_expired_or_changed_destination_never_sends(self):
        old = self.preview()
        fresh = self.preview()
        with self.assertRaises(OutboundError):
            self.service.commit(self.confirmed(old))
        self.clock.now += timedelta(seconds=301)
        with self.assertRaises(OutboundError):
            self.service.commit(self.confirmed(fresh))
        self.assertEqual(self.sent, [])
        more = self.preview()
        self.service.url = "https://other.n8n.cloud/webhook/another"
        with self.assertRaises(OutboundError):
            self.service.commit(self.confirmed(more))
        self.assertEqual(self.sent, [])

    def test_missing_real_saved_event_never_previews(self):
        with self.assertRaises(OutboundError) as e:
            self.service.preview({"workflow_id": "connector_studio",
                                  "event_id": "unrecorded_event"})
        self.assertEqual(e.exception.status, 404)
        self.assertEqual(self.sent, [])

    def test_url_rejects_local_network_and_untrusted_schemes(self):
        bad = (
            "http://example.com/webhook", "https://127.0.0.1/webhook",
            "https://localhost/webhook", "https://internal.local/webhook",
            "https://user:pass@public.example.com/hook",
            "https://public.org:8443/webhook", "https://public.org/",
            "https://public.org/webhook#fragment", "https://public.org/x\nBad: y",
            "https://[::1]/webhook", "https://public.org\\@127.0.0.1/x",
            "https://example.org:99999/webhook",
        )
        for target in bad:
            with self.subTest(target=target):
                with self.assertRaises(OutboundError):
                    validate_https_target(target)
        self.assertEqual(validate_https_target(ENDPOINT)[0], "hooks.n8n.cloud")

    def test_dns_rebinding_protection_blocks_private_resolution(self):
        for addr in ("127.0.0.1", "10.1.1.8", "169.254.169.254",
                     "192.168.0.1", "100.64.0.1", "0.0.0.0"):
            with patch("local_outbound_webhook.socket.getaddrinfo",
                       return_value=[(2,1,6,"",(addr,443))]):
                with self.assertRaises(OutboundError):
                    _resolve_public_ipv4("public.org")

    def test_http_transport_one_post_no_redirect_and_signature(self):
        reqs=[]
        class FakePinned:
            def __init__(self, host, ip):
                self.host,self.ip = host,ip
            def request(self, method, path, body, headers):
                reqs.append((method,path,body,headers))
            def getresponse(self):
                return type("Response", (), {"status": 302})()
            def close(self):
                pass
        with patch("local_outbound_webhook._resolve_public_ipv4",return_value="8.8.8.8"), \
             patch("local_outbound_webhook._PinnedTLS", FakePinned):
            status=send_https_once(ENDPOINT, {"hello":"world"}, "fixed-delivery-id", "secret")
        self.assertEqual(status, 302)
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0][0], "POST")
        self.assertEqual(reqs[0][1], "/webhook/owner-controlled-example")
        self.assertIn("X-AAS-Signature-SHA256", reqs[0][3])
        self.assertEqual(reqs[0][3]["X-AAS-Delivery-ID"], "fixed-delivery-id")

    def test_catalog_no_write_without_explicit_config(self):
        disabled=LocalOutboundWebhook(self.core, ":memory:")
        try:
            data=disabled.catalog()
            self.assertFalse(data["connectors"][0]["configured"])
            with self.assertRaises(OutboundError):
                disabled.preview({"workflow_id": "connector_studio",
                                  "event_id": "webhook_evt_001"})
        finally:
            disabled.close()


if __name__ == "__main__":
    unittest.main()
