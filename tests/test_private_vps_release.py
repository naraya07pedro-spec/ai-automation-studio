"""Private VPS deployment and SSH tunnel contracts (no real cloud required)."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))
sys.path.insert(0, str(ROOT / "tools"))

import private_vps_service as vps
import connect_private_vps as client


class SystemdReleaseTests(unittest.TestCase):
    def test_offline_unit_preserves_old_loopback_server(self):
        unit = vps.render_unit(
            username="ubuntu", home="/home/ubuntu",
            checkout="/home/ubuntu/ai-automation-studio",
            python="/usr/bin/python3", mode="offline")
        self.assertIn("User=ubuntu\n", unit)
        self.assertIn("Environment=HOME=/home/ubuntu\n", unit)
        self.assertIn("WorkingDirectory=/home/ubuntu/ai-automation-studio\n", unit)
        self.assertIn("ExecStart=/usr/bin/python3 /home/ubuntu/ai-automation-studio/app/local_server.py\n", unit)
        self.assertIn("ReadWritePaths=/home/ubuntu/.ai-automation-studio\n", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertNotIn("--enable-local-agent", unit)
        self.assertNotIn("0.0.0.0", unit)
        self.assertNotIn("--enable-agent-history", unit)

    def test_full_unit_exposes_only_preexisting_option_flags(self):
        unit = vps.render_unit(
            username="ubuntu", home="/home/ubuntu",
            checkout="/home/ubuntu/ai-automation-studio",
            python="/usr/bin/python3", mode="full")
        for flag in (
            "--enable-local-agent", "--enable-agent-plans",
            "--enable-agent-github-read", "--enable-hn-story-read",
            "--enable-greenhouse-evidence", "--enable-plan-actions"):
            self.assertEqual(unit.count(flag), 1)
        self.assertNotIn("--enable-agent-history", unit)
        self.assertNotIn("--enable-public", unit)
        self.assertNotIn("shell", unit)

    def test_path_and_user_input_cannot_inject_unit_directive(self):
        common = {
            "username": "ubuntu", "home": "/home/ubuntu",
            "checkout": "/home/ubuntu/ai-automation-studio",
            "python": "/usr/bin/python3", "mode": "offline"}
        for change in (
            {"username": "root"}, {"username": "ubuntu\nUser=root"},
            {"home": "/home/ubuntu\nEnvironment=X"},
            {"checkout": "/tmp/a b"},
            {"checkout": "/home/ubuntu/../root"},
            {"python": "python3"}, {"python": "/usr/bin/python3%p"},
            {"mode": "raw"}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    vps.render_unit(**(common | change))

    def test_systemd_install_requires_explicit_root_and_rejects_existing_unit(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "ai-automation-studio.service"
            with patch.object(vps, "UNIT_FILE", path), \
                 patch.object(vps.os, "geteuid", return_value=1000):
                with self.assertRaisesRegex(ValueError, "sudo_required"):
                    vps.install_unit("[Unit]\n")
                self.assertFalse(path.exists())
            path.write_text("old-unit-do-not-overwrite", encoding="utf-8")
            with patch.object(vps, "UNIT_FILE", path), \
                 patch.object(vps.os, "geteuid", return_value=0), \
                 patch.object(vps.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "existing_service"):
                    vps.install_unit("new")
                self.assertEqual(path.read_text(encoding="utf-8"), "old-unit-do-not-overwrite")
                run.assert_not_called()

    def test_install_uses_daemon_reload_enable_and_healthy_check(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "ai-automation-studio.service"
            with patch.object(vps, "UNIT_FILE", path), \
                 patch.object(vps.os, "geteuid", return_value=0), \
                 patch.object(vps.subprocess, "run") as run:
                vps.install_unit("[Unit]\nDescription=fixture\n")
                self.assertTrue(path.is_file())
                self.assertEqual(
                    [call.args[0] for call in run.call_args_list],
                    [["systemctl", "daemon-reload"],
                     ["systemctl", "enable", "--now", vps.UNIT_NAME],
                     ["systemctl", "is-active", "--quiet", vps.UNIT_NAME]])
                self.assertTrue(all(call.kwargs["check"] for call in run.call_args_list))


class PrivateSSHTests(unittest.TestCase):
    def test_ssh_destination_validation(self):
        for target in ("ubuntu@203.0.113.7", "aas@my-vps.example.com"):
            self.assertTrue(client.valid_destination(target))
        for target in (
            "-oProxyCommand=evil", "root;rm@server", "ubuntu@host && echo",
            "u@host\nProxyCommand=bad", "ubuntu@-host", "ubuntu@host..example",
            "ubuntu@host;curl", "", "username-only", "ubuntu@[::1]"):
            with self.subTest(target=target):
                self.assertFalse(client.valid_destination(target))

    def test_ssh_args_require_host_key_check_and_fixed_local_port(self):
        argv = client.ssh_args("ubuntu@203.0.113.7", ssh="ssh")
        self.assertEqual(argv[0], "ssh")
        self.assertEqual(argv[-1], "ubuntu@203.0.113.7")
        self.assertIn("127.0.0.1:8765:127.0.0.1:8765", argv)
        self.assertIn("StrictHostKeyChecking=ask", argv)
        self.assertIn("ExitOnForwardFailure=yes", argv)
        self.assertNotIn("StrictHostKeyChecking=no", argv)
        self.assertNotIn("-R", argv)
        self.assertNotIn("0.0.0.0", argv)

    def test_local_port_collision_blocks_ssh_without_spawning(self):
        with patch.object(client, "local_port_free", return_value=False), \
             patch.object(client.subprocess, "Popen") as spawn:
            self.assertEqual(
                client.main(["--destination", "ubuntu@203.0.113.7", "--no-browser"]), 2)
            spawn.assert_not_called()

    def test_invalid_destination_blocks_ssh_without_spawning(self):
        with patch.object(client.subprocess, "Popen") as spawn:
            self.assertEqual(
                client.main(["--destination", "ubuntu@host;bad", "--no-browser"]), 2)
            spawn.assert_not_called()

    def test_ssh_exit_before_http_does_not_claim_success(self):
        process = SimpleNamespace(returncode=255, poll=lambda: 255)
        with patch.object(client, "local_port_free", return_value=True), \
             patch.object(client.shutil, "which", return_value="/usr/bin/ssh"), \
             patch.object(client.subprocess, "Popen", return_value=process), \
             patch.object(client.webbrowser, "open") as browser:
            self.assertEqual(
                client.main(["--destination", "ubuntu@203.0.113.7"]), 255)
            browser.assert_not_called()

    def test_browser_readiness_uses_fixed_loopback_and_denial_header(self):
        self.assertEqual(client.URL, "http://127.0.0.1:8765")
        self.assertEqual(client.PORT, 8765)


if __name__ == "__main__":
    unittest.main()
