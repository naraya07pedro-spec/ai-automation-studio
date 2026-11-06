"""Real AES-256-GCM encrypted registry tests; NO real webhook or key in Git.

The legacy stdlib-only suite skips this module if the optional dependency is
missing. A dedicated CI workflow explicitly installs the pinned dependency
and runs all tests here instead of silently treating a skip as crypto proof.
"""
import base64
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
from local_connector_vault import (
    EncryptedConnectorVault, VaultError, decode_master_key, generate_master_key)
from local_connector_manager import LocalConnectorManager, ManagedConnectorError
from local_workflow_core import LocalWorkflowCore

HAS_CRYPTO=importlib.util.find_spec("cryptography") is not None


@unittest.skipUnless(HAS_CRYPTO,"Optional cryptography missing; dedicated vault CI installs it")
class EncryptedVaultTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.key=generate_master_key()
        self.file=self.root/"owner"/"managed-webhooks-vault.json"
        self.core=LocalWorkflowCore(self.root/"core.sqlite")
        status,_=self.core.create({
            "workflow_id":"encrypted_flow","display_name":"Encrypted Flow",
            "service_filter":"automation","min_budget_tier":2,
            "followup_policy":"review_required"})
        self.assertEqual(status,201)
        self.assertEqual(self.core.activate({
            "workflow_id":"encrypted_flow","expected_version":1})[0],200)
        self.assertEqual(self.core.run({
            "workflow_id":"encrypted_flow","event_id":"encrypted_event_001",
            "service_code":"automation","budget_tier":2,"timeline":"soon"})[0],200)
        self.sent=[]
        def stub(url,payload,did,secret):
            self.sent.append((url,payload,did,secret))
            return 204
        self.sender=stub
        self.manager=LocalConnectorManager(
            self.core,self.file,sender=self.sender,vault_key=self.key)

    def tearDown(self):
        self.manager.close()
        self.core.close()
        self.temp.cleanup()

    def add(self,key="secure_n8n"):
        return self.manager.add({
            "connector_id":key,"label":"Secure n8n",
            "url":"https://hooks.n8n.cloud/webhook/very-secret-token-001",
            "signing_secret":"very-secret-hmac-key"})

    def test_sealed_bytes_never_contain_url_or_secret_and_metadata_is_honest(self):
        self.add()
        raw=self.file.read_text()
        self.assertNotIn("very-secret-token-001",raw)
        self.assertNotIn("very-secret-hmac-key",raw)
        self.assertNotIn("hooks.n8n.cloud",raw)
        envelope=json.loads(raw)
        self.assertEqual(set(envelope),{"version","algorithm","nonce","ciphertext"})
        self.assertEqual(envelope["algorithm"],"AES-256-GCM")
        catalog=self.manager.list()
        self.assertEqual(catalog["storage"],"AES_256_GCM_EXTERNAL_MASTER_KEY")
        self.assertEqual(catalog["connectors"][0]["destination_hostname"],
                         "hooks.n8n.cloud")
        self.assertNotIn("very-secret",json.dumps(catalog))
        self.assertFalse(catalog["model_tool_access"])
        self.assertFalse(catalog["scheduled_dispatch_enabled"])
        self.assertEqual(self.sent,[])

    def test_reopen_with_key_restores_registry_and_no_retry_ledger(self):
        self.add()
        request={"connector_id":"secure_n8n","workflow_id":"encrypted_flow",
                 "event_id":"encrypted_event_001"}
        p=self.manager.preview(request)
        self.assertEqual(p["status"],"PREVIEW_ONLY")
        self.assertEqual(self.sent,[])
        result=self.manager.commit({**request,"preview_token":p["preview_token"],
                                    "payload_sha256":p["payload_sha256"]})
        self.assertEqual(result["status"],"REMOTE_ACCEPTED")
        self.assertEqual(len(self.sent),1)
        self.assertEqual(self.sent[0][3],"very-secret-hmac-key")
        self.manager.close()
        self.manager=LocalConnectorManager(self.core,self.file,sender=self.sender,vault_key=self.key)
        self.assertEqual(self.manager.list()["connectors"][0]["status"],"ACTIVE")
        self.assertEqual(self.manager.history("secure_n8n")["deliveries"][0]["status"],
                         "REMOTE_ACCEPTED")
        with self.assertRaises(Exception):
            self.manager.preview(request)
        self.assertEqual(len(self.sent),1)

    def test_wrong_key_fails_closed_and_never_recreates_plaintext(self):
        self.add()
        before=self.file.read_bytes()
        with self.assertRaises(ManagedConnectorError) as wrong:
            LocalConnectorManager(self.core,self.file,sender=self.sender,
                                  vault_key=generate_master_key())
        self.assertEqual(wrong.exception.status,503)
        self.assertEqual(self.file.read_bytes(),before)
        self.assertNotIn("very-secret",self.file.read_text())
        self.assertEqual(len(self.sent),0)

    def test_ciphertext_tampering_is_detected(self):
        self.add()
        self.manager.close()
        envelope=json.loads(self.file.read_text())
        cipher=bytearray(base64.b64decode(envelope["ciphertext"]))
        cipher[12]^=1
        envelope["ciphertext"]=base64.b64encode(cipher).decode()
        self.file.write_text(json.dumps(envelope))
        with self.assertRaises(ManagedConnectorError):
            LocalConnectorManager(self.core,self.file,sender=self.sender,vault_key=self.key)
        self.manager=LocalConnectorManager.__new__(LocalConnectorManager)
        self.manager._clients={}
        self.manager._lock=__import__("threading").RLock()

    def test_nonce_rotates_after_each_registry_update(self):
        first=json.loads(self.file.read_text())
        self.add()
        second=json.loads(self.file.read_text())
        self.manager.revoke("secure_n8n")
        third=json.loads(self.file.read_text())
        self.assertNotEqual(first["nonce"],second["nonce"])
        self.assertNotEqual(second["nonce"],third["nonce"])
        self.assertNotIn("very-secret",self.file.read_text())
        self.assertEqual(self.manager.list()["connectors"][0]["status"],"REVOKED")

    def test_plaintext_registry_is_never_silently_imported(self):
        old=self.root/"owner"/"managed-webhooks.json"
        legacy=LocalConnectorManager(self.core,old,sender=self.sender)
        try:
            legacy.add({"connector_id":"legacy_n8n","label":"Legacy connection",
                        "url":"https://hooks.n8n.cloud/webhook/plain-legacy-secret",
                        "signing_secret":"legacy-secret"})
        finally:
            legacy.close()
        self.assertEqual(self.manager.list()["connectors"],[])
        self.assertIn("plain-legacy-secret",old.read_text())
        self.assertNotIn("plain-legacy-secret",self.file.read_text())

    def test_invalid_or_missing_master_key_is_rejected_before_io(self):
        for value in ("","this-is-not-a-key", "A"*44,"A"*40+"!"):
            with self.subTest(value=value):
                with self.assertRaises(VaultError):
                    decode_master_key(value)
        self.assertEqual(len(decode_master_key(self.key)),32)
        target=self.root/"missing"/"new-vault.json"
        with self.assertRaises(VaultError):
            EncryptedConnectorVault(target,"invalid-key")
        self.assertFalse(target.exists())

    def test_encrypted_and_legacy_use_distinct_delivery_directories(self):
        self.add()
        self.assertIn("vault-managed-webhook-ledgers",
                      str(self.manager._ledger_path("secure_n8n")))
        legacy=LocalConnectorManager(self.core,self.root/"owner"/"managed-webhooks.json",
                                     sender=self.sender)
        try:
            self.assertIn("managed-webhook-ledgers",
                          str(legacy._ledger_path("secure_n8n")))
            self.assertNotEqual(self.manager._ledger_path("secure_n8n"),
                                legacy._ledger_path("secure_n8n"))
        finally:
            legacy.close()


if __name__=="__main__":
    unittest.main()
