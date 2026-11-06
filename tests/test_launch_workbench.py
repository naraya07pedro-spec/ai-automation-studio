"""Release-entrypoint contract: no provider writes, hidden cloud, or fake readiness."""
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app"))
import launch_workbench as quickstart


class QuickstartTests(unittest.TestCase):
    def test_offline_profile_needs_no_model_or_remote_permissions(self):
        self.assertEqual(quickstart.feature_flags("offline"), ())

    def test_local_agent_profile_is_opt_in_without_remote_read(self):
        flags = quickstart.feature_flags("local-ai")
        self.assertIn("--enable-agent-plans", flags)
        self.assertIn("--enable-plan-actions", flags)
        self.assertNotIn("--enable-hn-story-read", flags)
        self.assertNotIn("--enable-agent-github-read", flags)
        self.assertNotIn("--enable-agent-history", flags)

    def test_full_profile_exposes_existing_tools_without_background_grant(self):
        flags = quickstart.feature_flags("full")
        for flag in quickstart.LOCAL_AGENT_FLAGS + quickstart.EXTERNAL_READ_FLAGS:
            self.assertEqual(flags.count(flag), 1)
        self.assertNotIn("--enable-agent-history", flags)
        self.assertFalse(any("send" in s or "public-host" in s or "shell" in s for s in flags))
        self.assertEqual((quickstart.HOST, quickstart.PORT), ("127.0.0.1", 8765))

    def test_plaintext_history_requires_explicit_opt_in(self):
        self.assertNotIn("--enable-agent-history", quickstart.feature_flags("full"))
        self.assertIn("--enable-agent-history", quickstart.feature_flags("local-ai", history=True))
        with self.assertRaisesRegex(ValueError, "history_requires_local_ai"):
            quickstart.feature_flags("offline", history=True)

    def test_invalid_profile_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_quickstart_mode"):
            quickstart.feature_flags("cloud")

    def test_missing_owner_setup_failure_never_starts_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = pathlib.Path(tmp) / "operator.json"
            with patch.object(quickstart, "_operator_path", return_value=missing), \
                 patch.object(quickstart, "_port_free", return_value=True), \
                 patch.object(quickstart.subprocess, "call", return_value=1) as launch:
                self.assertEqual(quickstart.main(["--mode", "offline", "--no-browser"]), 2)
                self.assertEqual(launch.call_count, 1)
                self.assertEqual(launch.call_args.args[0][-1], "--init-owner")

    def test_occupied_port_never_provisions_or_starts(self):
        with patch.object(quickstart, "_port_free", return_value=False), \
             patch.object(quickstart.subprocess, "call") as launch:
            self.assertEqual(quickstart.main(["--mode", "full", "--no-browser"]), 2)
            launch.assert_not_called()

    def test_existing_owner_starts_one_foreground_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner = pathlib.Path(tmp) / "operator.json"
            owner.write_text("fixture only", encoding="utf-8")
            with patch.object(quickstart, "_operator_path", return_value=owner), \
                 patch.object(quickstart, "_port_free", return_value=True), \
                 patch.object(quickstart, "_serve") as launch, \
             patch.object(quickstart.subprocess, "call") as setup:
                self.assertEqual(quickstart.main(["--mode", "full", "--no-browser"]), 0)
                self.assertEqual(launch.call_count, 1)
                setup.assert_not_called()
                flags = launch.call_args.args[0]
                self.assertIn("--enable-local-agent", flags)
                self.assertNotIn("--enable-agent-history", flags)


if __name__ == "__main__":
    unittest.main()
