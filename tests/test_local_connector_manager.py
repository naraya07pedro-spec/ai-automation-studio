"""Focused tests of real managed HTTPS connector registrations and send ledgers.

All external sends are injected callbacks, never live network.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_workflow_core import LocalWorkflowCore
from local_connector_manager import LocalConnectorManager, ManagedConnectorError
from local_outbound_webhook import OutboundError


class ManagedConnectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)
        self.core=LocalWorkflowCore(self.path/"core.sqlite")
        code,_=self.core.create({"workflow_id":"managed_flow","display_name":"Managed Flow",
            "service_filter":"automation","min_budget_tier":2,
            "followup_policy":"review_required"})
        self.assertEqual(code,201)
        self.assertEqual(self.core.activate({"workflow_id":"managed_flow","expected_version":1})[0],200)
        self.assertEqual(self.core.run({"workflow_id":"managed_flow","event_id":"registered_evt_001",
            "service_code":"automation","budget_tier":2,"timeline":"soon"})[0],200)
        self.sent=[]
        def sender(url,payload,did,secret):
            self.sent.append((url,payload,did,secret))
            return 202
        self.sender=sender
        self.registry_file=self.path/"owner"/"managed-webhooks.json"
        self.manager=LocalConnectorManager(self.core,self.registry_file,sender=self.sender)

    def tearDown(self):
        self.manager.close()
        self.core.close()
        self.tmp.cleanup()

    def add(self,name="one",destination=None):
        dest=destination or "https://hooks.n8n.cloud/webhook/"+name+"-private"
        return self.manager.add({
            "connector_id":name+"hook","label":"Destination "+name,
            "url":dest,"signing_secret":"secret-"+name})

    def preview(self,key="onehook"):
        return self.manager.preview({"connector_id":key,
            "workflow_id":"managed_flow","event_id":"registered_evt_001"})

    def commit(self,p,key="onehook"):
        return self.manager.commit({"connector_id":key,
            "workflow_id":"managed_flow","event_id":"registered_evt_001",
            "preview_token":p["preview_token"],
            "payload_sha256":p["payload_sha256"]})

    def test_register_multiple_real_connections_and_redact_secrets(self):
        self.add("one");self.add("two")
        catalog=self.manager.list()
        self.assertEqual(len(catalog["connectors"]),2)
        self.assertEqual(catalog["storage"],"OS_PERMISSION_PROTECTED_NOT_ENCRYPTED")
        self.assertFalse(catalog["scheduled_dispatch_enabled"])
        self.assertNotIn("/webhook/",json.dumps(catalog))
        self.assertNotIn("secret-one",json.dumps(catalog))
        self.assertNotIn("secret-two",json.dumps(catalog))
        self.assertEqual(self.sent,[])
        raw=self.registry_file.read_text()
        self.assertIn("secret-one",raw)  # Explicitly NOT encrypted at rest!
        self.assertNotIn("secret-one",json.dumps(catalog))

    def test_independent_previews_and_one_real_send_per_connection(self):
        self.add("one");self.add("two")
        p1=self.preview("onehook")
        p2=self.preview("twohook")
        self.assertEqual(self.sent,[])
        result1=self.commit(p1,"onehook")
        result2=self.commit(p2,"twohook")
        self.assertEqual(result1["status"],"REMOTE_ACCEPTED")
        self.assertEqual(result2["status"],"REMOTE_ACCEPTED")
        self.assertEqual(len(self.sent),2)
        self.assertNotEqual(self.sent[0][0],self.sent[1][0])
        self.assertEqual(self.sent[0][3],"secret-one")
        self.assertEqual(self.sent[1][3],"secret-two")
        with self.assertRaises(OutboundError):
            self.commit(p1,"onehook")
        with self.assertRaises(OutboundError):
            self.commit(p2,"twohook")
        self.assertEqual(len(self.sent),2)
        self.assertEqual(len(self.manager.history("onehook")["deliveries"]),1)
        self.assertEqual(len(self.manager.history("twohook")["deliveries"]),1)

    def test_connector_id_binding_prevents_cross_destination_token_replay(self):
        self.add("one");self.add("two")
        p1=self.preview("onehook");self.preview("twohook")
        with self.assertRaises(OutboundError):
            self.commit(p1,"twohook")
        self.assertEqual(self.sent,[])
        self.assertEqual(self.commit(p1,"onehook")["status"],"REMOTE_ACCEPTED")

    def test_revocation_erases_file_credentials_and_denies_new_actions(self):
        self.add("one");self.add("two")
        p=self.preview("onehook")
        revoked=self.manager.revoke("onehook")
        self.assertEqual(revoked["status"],"REVOKED")
        self.assertNotIn("secret-one",self.registry_file.read_text())
        self.assertNotIn("/webhook/one-private",self.registry_file.read_text())
        self.assertIn("secret-two",self.registry_file.read_text())
        with self.assertRaises(ManagedConnectorError):
            self.commit(p,"onehook")
        with self.assertRaises(ManagedConnectorError):
            self.preview("onehook")
        with self.assertRaises(ManagedConnectorError):
            self.add("one")
        self.assertEqual(self.sent,[])
        self.assertEqual(self.manager.history("onehook")["connector_status"],"REVOKED")

    def test_restart_preserves_separate_no_retry_ledgers_and_revoke_state(self):
        self.add("one");self.add("two")
        p=self.preview()
        self.commit(p)
        self.manager.revoke("twohook")
        self.manager.close()
        self.manager=LocalConnectorManager(self.core,self.registry_file,sender=self.sender)
        self.assertEqual(len(self.manager.list()["connectors"]),2)
        self.assertEqual(self.manager.history("onehook")["deliveries"][0]["status"],
                         "REMOTE_ACCEPTED")
        with self.assertRaises(OutboundError):
            self.preview()
        self.assertEqual(self.manager.list()["connectors"][1]["status"],"REVOKED")
        self.assertEqual(len(self.sent),1)

    def test_limits_and_invalid_destination_fail_without_network(self):
        for bad in ("http://n8n.com/hook","https://127.0.0.1/hook",
                    "https://example.local/hook","https://service.example.org:8080/hook"):
            with self.subTest(bad=bad):
                with self.assertRaises(ManagedConnectorError):
                    self.manager.add({"connector_id":"testhook","label":"N8n",
                        "url":bad,"signing_secret":""})
        for name in ("one","two","tri","four"):
            self.add(name)
        with self.assertRaises(ManagedConnectorError):
            self.add("five")
        self.assertEqual(self.sent,[])

    def test_unrecorded_local_event_cannot_be_sent(self):
        self.add()
        with self.assertRaises(OutboundError):
            self.manager.preview({"connector_id":"onehook","workflow_id":"managed_flow",
                "event_id":"missing_evt"})
        self.assertEqual(self.sent,[])

    def test_default_not_a_model_tool_and_never_schedules_itself(self):
        self.add()
        status=self.manager.list()
        self.assertFalse(status["model_tool_access"])
        self.assertFalse(status["scheduled_dispatch_enabled"])
        self.assertFalse(status["connectors"][0]["supports_scheduled_send"])
        self.assertEqual(self.sent,[])


if __name__=="__main__":
    unittest.main()
