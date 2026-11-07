"""Stage #110: no silent root action, no billable API, no existing-unit overwrite."""
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))
import activate_private_cloud as activation
import verify_private_vps_runtime
import verify_private_cloud_ai_runtime


def preflight(needs_owner=False):
    return {
        "status": "READY_TO_APPLY_WITH_OWNER_CONSENT",
        "checks": {"linux_systemd": True},
        "owner_initialization_needed": needs_owner,
        "user": "studio",
    }


class GuidedCloudActivation(unittest.TestCase):
    def test_unapproved_never_runs_commands(self):
        with patch.object(activation, "inspect", return_value=preflight()), \
             patch.object(activation, "_run_interactive") as execute:
            result = activation.apply()
        self.assertEqual(result["status"], "OWNER_CONFIRMATION_REQUIRED")
        self.assertEqual(result["mutations"], 0)
        execute.assert_not_called()

    def test_blocked_preflight_never_prompts_or_changes_state(self):
        with patch.object(activation, "inspect", return_value={
                 "status": "BLOCKED", "checks": {"linux_systemd": False},
             }), patch.object(activation, "_run_interactive") as execute:
            result = activation.apply(confirm=activation.CONFIRMATION)
        self.assertEqual(result["status"], "BLOCKED")
        execute.assert_not_called()

    def test_existing_unit_or_port_refuses_before_sudo(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(activation, "inspect", return_value=preflight()), \
             patch.object(activation, "_owner_status", return_value=(True, False)), \
             patch.object(activation.pwd, "getpwnam", return_value=types.SimpleNamespace(pw_dir=tmp)), \
             patch.object(activation, "_run_interactive") as execute:
            service = Path(tmp) / "existing-unit"
            service.touch()
            with patch.object(activation, "UNIT_FILE", service), \
                 patch.object(activation, "_is_port_free", return_value=True):
                result = activation.apply(confirm=activation.CONFIRMATION)
            self.assertEqual(result["reason"], "service_or_port_changed")
            execute.assert_not_called()
            service.unlink()
            with patch.object(activation, "UNIT_FILE", service), \
                 patch.object(activation, "_is_port_free", return_value=False):
                result = activation.apply(confirm=activation.CONFIRMATION)
            self.assertEqual(result["reason"], "service_or_port_changed")
            execute.assert_not_called()

    def test_owner_setup_failure_never_reaches_sudo(self):
        with patch.object(activation, "inspect", return_value=preflight(True)), \
             patch.object(activation, "_run_interactive", return_value=False) as run:
            result = activation.apply(confirm=activation.CONFIRMATION)
        self.assertEqual(result["reason"], "owner_setup_failed")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][-1], "--init-owner")

    def test_successful_new_install_runs_only_existing_fixed_scripts(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(activation, "inspect", return_value=preflight()), \
             patch.object(activation, "_owner_status", return_value=(True, False)), \
             patch.object(activation.pwd, "getpwnam", return_value=types.SimpleNamespace(pw_dir=tmp)), \
             patch.object(activation, "UNIT_FILE", Path(tmp) / "absent-unit"), \
             patch.object(activation, "_is_port_free", return_value=True), \
             patch.object(activation, "_quiet", return_value=subprocess.CompletedProcess([], 1, "", "")), \
             patch.object(activation, "_run_interactive", return_value=True) as run, \
             patch.object(verify_private_vps_runtime, "probe", return_value={
                 "status": "LIVE_PRIVATE_VPS_SNAPSHOT_PASSED",
             }), patch.object(verify_private_cloud_ai_runtime, "_check_cloud_process", return_value={
                 "nonroot_service_identity": True,
                 "live_cloud_only_server_process": True,
                 "root_owned_credential_declared_in_unit": True,
             }):
            result = activation.apply(confirm=activation.CONFIRMATION)
        self.assertEqual(result["status"],
                         "PRIVATE_CLOUD_SERVER_BOOT_VERIFIED_NO_MODEL_REQUEST")
        self.assertFalse(result["paid_model_request_executed_by_this_tool"])
        self.assertFalse(result["vps_identity_verified"])
        self.assertFalse(result["openai_provider_account_verified"])
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 4)
        self.assertEqual(commands[0], ["sudo", "-v"])
        self.assertEqual(commands[1][-2:], ["--name", "openai-api-key"])
        self.assertEqual(commands[2][-2:], ["--mode", "cloud-only", "--enable-scheduled-openai"][-2:])
        self.assertEqual(commands[3][-1], "--install")
        self.assertTrue(all("git" not in cmd[0] for cmd in commands))
        self.assertTrue(all("curl" not in cmd for cmd in commands))

    def test_existing_key_not_overwritten_or_reprompted(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(activation, "inspect", return_value=preflight()), \
             patch.object(activation, "_owner_status", return_value=(True, False)), \
             patch.object(activation.pwd, "getpwnam", return_value=types.SimpleNamespace(pw_dir=tmp)), \
             patch.object(activation, "UNIT_FILE", Path(tmp) / "absent-unit"), \
             patch.object(activation, "_is_port_free", return_value=True), \
             patch.object(activation, "_quiet", return_value=subprocess.CompletedProcess([], 0, "", "")), \
             patch.object(activation, "_run_interactive", return_value=True) as run, \
             patch.object(verify_private_vps_runtime, "probe", return_value={
                 "status": "NOT_VERIFIED",
             }), patch.object(verify_private_cloud_ai_runtime, "_check_cloud_process", return_value={}):
            result = activation.apply(confirm=activation.CONFIRMATION)
        self.assertEqual(result["status"], "DEPLOYMENT_INCOMPLETE_REVIEW_SYSTEMD")
        commands = [c.args[0] for c in run.call_args_list]
        self.assertEqual(len(commands), 3)
        self.assertFalse(any("provision_private_vps_credential.py" in " ".join(cmd)
                             for cmd in commands))
        self.assertEqual(commands[-1][-1], "--install")

    def test_inspector_does_not_print_username_and_never_calls_sudo(self):
        report = {**preflight(), "limitations": "No live proof"}
        self.assertNotIn("user", activation._safe_public_report(report))
        self.assertEqual(activation._safe_public_report(report)["status"],
                         "READY_TO_APPLY_WITH_OWNER_CONSENT")

    def test_sudo_python_rejects_user_owned_interpreter(self):
        with tempfile.TemporaryDirectory() as tmp:
            user_python = Path(tmp) / "python3"
            user_python.write_text("fixture")
            user_python.chmod(0o755)
            with patch.object(activation.sys, "executable", str(user_python)):
                self.assertFalse(activation._trusted_python_for_sudo())

    def test_absent_owner_store_is_initializable_but_insecure_store_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secure, needed = activation._owner_status(root, root.stat().st_uid)
            self.assertTrue(secure)
            self.assertTrue(needed)
            owner_dir = root / ".ai-automation-studio"
            owner_dir.mkdir(mode=0o777)
            owner_dir.chmod(0o777)
            secure, needed = activation._owner_status(root, root.stat().st_uid)
            self.assertFalse(secure)
            self.assertTrue(needed)
            owner_dir.chmod(0o700)
            (owner_dir / "operator.json").write_text("fixture")
            (owner_dir / "operator.json").chmod(0o600)
            secure, needed = activation._owner_status(root, root.stat().st_uid)
            self.assertTrue(secure)
            self.assertFalse(needed)


if __name__ == "__main__":
    unittest.main()
