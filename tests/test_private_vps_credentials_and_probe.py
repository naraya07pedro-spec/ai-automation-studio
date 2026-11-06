"""Prod-credential and live-probe verification with disposable local fixtures."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app"))
sys.path.insert(0,str(ROOT/"deploy"))

from local_connector_vault import generate_master_key
from local_service_credentials import read_runtime_secret, ServiceCredentialError
import private_vps_service as vps
import provision_private_vps_credential as provisioner
import verify_private_vps_runtime as checker


class RuntimeSecretTests(unittest.TestCase):
    def test_local_windows_style_environment_without_credential_directory(self):
        with patch.dict(os.environ, {"AAS_VAULT_MASTER_KEY": "explicit-local-test"}, clear=True):
            self.assertEqual(read_runtime_secret("AAS_VAULT_MASTER_KEY"), "explicit-local-test")

    def test_unknown_name_not_a_generic_file_read(self):
        with self.assertRaises(ServiceCredentialError):
            read_runtime_secret("GMAIL_REFRESH_TOKEN")

    def test_systemd_file_read_and_trim_only_terminal_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/"aas-vault-key"
            file.write_text("fixture-private-value\n")
            file.chmod(0o400)
            with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY":tmp}, clear=True):
                self.assertEqual(read_runtime_secret("AAS_VAULT_MASTER_KEY"), "fixture-private-value")

    def test_webhook_file_without_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/"aas-webhook-url"
            file.write_text("https://example.org/hooks/fixture")
            file.chmod(0o400)
            with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY":tmp}, clear=True):
                self.assertEqual(read_runtime_secret("AAS_WEBHOOK_URL"), "https://example.org/hooks/fixture")

    def test_world_readable_credentials_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/"aas-vault-key"
            file.write_text("fixture")
            file.chmod(0o644)
            with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY":tmp}, clear=True):
                with self.assertRaisesRegex(ServiceCredentialError, "unsafe_permissions"):
                    read_runtime_secret("AAS_VAULT_MASTER_KEY")

    def test_symlink_credentials_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/"secret-target"
            target.write_text("fixture")
            target.chmod(0o400)
            (Path(tmp)/"aas-vault-key").symlink_to(target)
            with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY":tmp}, clear=True):
                with self.assertRaisesRegex(ServiceCredentialError,"symlink_refused"):
                    read_runtime_secret("AAS_VAULT_MASTER_KEY")

    def test_secret_in_env_and_loadcredential_cannot_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/"aas-vault-key"
            file.write_text("systemd-fixture")
            file.chmod(0o400)
            with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY":tmp,
                                          "AAS_VAULT_MASTER_KEY":"different"}, clear=True):
                with self.assertRaisesRegex(ServiceCredentialError, "ambiguous"):
                    read_runtime_secret("AAS_VAULT_MASTER_KEY")

    def test_multiline_and_oversized_credentials_are_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/"aas-webhook-url"
            with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY":tmp}, clear=True):
                file.write_text("https://example.org\nmalicious")
                file.chmod(0o400)
                with self.assertRaisesRegex(ServiceCredentialError,"invalid_text"):
                    read_runtime_secret("AAS_WEBHOOK_URL")
                file.write_bytes(b"x"*8193)
                with self.assertRaisesRegex(ServiceCredentialError,"unsafe_permissions"):
                    read_runtime_secret("AAS_WEBHOOK_URL")


class VPSFeatureUnitTests(unittest.TestCase):
    def base_args(self):
        return dict(username="ubuntu", home="/home/ubuntu",
                    checkout="/home/ubuntu/ai-automation-studio",
                    python="/usr/bin/python3",mode="offline")

    def test_legacy_offline_unit_unchanged_by_default(self):
        unit=vps.render_unit(**self.base_args())
        self.assertNotIn("LoadCredential=",unit)
        self.assertNotIn("AAS_VAULT_MASTER_KEY=",unit)
        self.assertNotIn("--enable-google-gmail-metadata",unit)
        self.assertIn("Restart=on-failure",unit)

    def test_explicit_three_connector_sources_are_not_literal_secrets(self):
        unit=vps.render_unit(**self.base_args(),encrypted_webhooks=True,
                             gmail_metadata=True,outbound_webhook=True,signed_webhook=True)
        self.assertEqual(unit.count("--enable-google-gmail-metadata"),1)
        self.assertEqual(unit.count("--enable-encrypted-managed-webhooks"),1)
        self.assertEqual(unit.count("--enable-outbound-webhook"),1)
        self.assertEqual(unit.count("LoadCredential=aas-vault-key:"),1)
        self.assertEqual(unit.count("LoadCredential=aas-webhook-url:"),1)
        self.assertEqual(unit.count("LoadCredential=aas-webhook-signing-secret:"),1)
        self.assertNotIn("Environment=AAS_",unit)
        self.assertNotIn("EnvironmentFile=",unit)
        self.assertIn("NoNewPrivileges=true",unit)
        self.assertIn("ReadWritePaths=/home/ubuntu/.ai-automation-studio",unit)

    def test_invalid_signed_webhook_only_config_rejected(self):
        with self.assertRaisesRegex(ValueError,"signed_webhook_requires"):
            vps.render_unit(**self.base_args(),signed_webhook=True)

    def test_root_only_files_validated_with_disposable_current_uid_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)
            home.chmod(0o700)
            key=home/"aas-vault-key"
            url=home/"aas-webhook-url"
            key.write_text(generate_master_key()+"\n")
            url.write_text("https://example.org/fixture")
            key.chmod(0o600)
            url.chmod(0o600)
            result=vps.validate_secret_sources(encrypted_webhooks=True,
                gmail_metadata=True,outbound_webhook=True,
                directory=home,expected_uid=os.getuid())
            self.assertEqual(result,["aas-vault-key","aas-webhook-url"])
            url.chmod(0o644)
            with self.assertRaisesRegex(ValueError,"unsafe"):
                vps.validate_secret_sources(outbound_webhook=True,
                    directory=home,expected_uid=os.getuid())

    def test_invalid_vault_key_and_url_source_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            root.chmod(0o700)
            key=root/"aas-vault-key"
            url=root/"aas-webhook-url"
            key.write_text("not-a-key")
            url.write_text("http://insecure.example/")
            key.chmod(0o600)
            url.chmod(0o600)
            with self.assertRaisesRegex(ValueError,"invalid_vault"):
                vps.validate_secret_sources(gmail_metadata=True,
                    directory=root,expected_uid=os.getuid())
            key.write_text(generate_master_key())
            with self.assertRaisesRegex(ValueError,"invalid_webhook"):
                vps.validate_secret_sources(gmail_metadata=True,
                    outbound_webhook=True,directory=root,expected_uid=os.getuid())

    def test_no_optional_credentials_required_for_safe_offline_mode(self):
        self.assertEqual(vps.validate_secret_sources(),[])


class CredentialProvisionerTests(unittest.TestCase):
    def test_provisioner_refuses_nonroot_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest=Path(tmp)/"private"
            with patch.object(provisioner.os,"geteuid",return_value=1000):
                with self.assertRaisesRegex(provisioner.ProvisionError,"root_required"):
                    provisioner.provision("vault-key",generate_master_key(),directory=dest)
            self.assertFalse(dest.exists())

    def test_provisioner_rejects_insecure_and_unsupported_values(self):
        for name,value in (
            ("unexpected","fixture"),
            ("vault-key","not-a-key"),
            ("webhook-url","http://insecure.example/hook"),
            ("webhook-signing-secret","bad\nline")):
            with self.subTest(name=name):
                with self.assertRaises(provisioner.ProvisionError):
                    provisioner._validate(name,value)

    def test_provisioner_never_accepts_cli_secret_argument(self):
        self.assertNotIn("--value",provisioner.NAMES)
        self.assertEqual(set(provisioner.NAMES),
                         {"vault-key","webhook-url","webhook-signing-secret"})


class LiveProbeContractTests(unittest.TestCase):
    HEADER="sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n"

    def test_parse_ipv4_and_wildcard_port_exactly(self):
        sample=self.HEADER+(
            "0: 0100007F:223D 00000000:0000 0A 0:0 00 0 0 1000 0 1\n"
            "1: 00000000:223D 00000000:0000 0A 0:0 00 0 0 1000 0 2\n"
            "2: 0100007F:223E 00000000:0000 0A 0:0 00 0 0 1000 0 3\n")
        self.assertEqual(checker.parse_listening_addresses(sample),
                         ["0100007F","00000000"])

    def test_fake_listener_only_is_not_live_approval(self):
        with patch.object(checker.sys,"platform","linux"), \
             patch.object(checker.Path,"is_dir",return_value=True), \
             patch.object(checker.subprocess,"run",return_value=SimpleNamespace(returncode=1)), \
             patch.object(checker,"_listen_status",return_value=(True,True)), \
             patch.object(checker,"_http_checks",return_value=(True,True)):
            result=checker.probe()
        self.assertEqual(result["status"],"NOT_VERIFIED")
        self.assertFalse(result["checks"]["systemd_active"])

    def test_all_checks_are_required_for_passing_snapshot(self):
        with patch.object(checker.sys,"platform","linux"), \
             patch.object(checker.Path,"is_dir",return_value=True), \
             patch.object(checker.subprocess,"run",return_value=SimpleNamespace(returncode=0)), \
             patch.object(checker,"_listen_status",return_value=(True,True)), \
             patch.object(checker,"_http_checks",return_value=(True,True)):
            result=checker.probe()
        self.assertEqual(result["status"],"LIVE_PRIVATE_VPS_SNAPSHOT_PASSED")
        self.assertEqual(len(result["checks"]),5)
        self.assertIn("no proof",result["limitations"].lower())

    def test_public_ipv6_or_wildcard_rejected_before_http(self):
        with patch.object(checker,"parse_listening_addresses",
                          side_effect=[["00000000"], ["00000000000000000000000000000000"]]), \
             patch.object(checker.Path,"read_text",return_value="synthetic"):
            listener,local=checker._listen_status()
        self.assertTrue(listener)
        self.assertFalse(local)


if __name__=="__main__":
    unittest.main()
