"""Disposable actual HTTP smoke for the one-click local entrypoint.

This runs only a synthetic offline owner on a disposable CI host.
It is NOT an Ollama or the real operator's Windows acceptance test.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_operator_auth import LocalOperatorAuth


class LocalQuickstartHTTPTests(unittest.TestCase):
    def test_real_loopback_http_offline_mode_with_disposable_owner(self):
        with tempfile.TemporaryDirectory(prefix="aas-quickstart-") as home:
            owner = Path(home) / ".ai-automation-studio" / "operator.json"
            LocalOperatorAuth.provision(owner, "SyntheticOfflineOwnerPass2026!")
            env = dict(os.environ)
            env["HOME"] = home
            env["USERPROFILE"] = home
            env["PYTHONUNBUFFERED"] = "1"
            args = [sys.executable, str(ROOT / "app" / "launch_workbench.py"),
                    "--mode", "offline", "--no-browser"]
            proc = subprocess.Popen(
                args, cwd=str(ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            seen = False
            output = ""
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        output = proc.stdout.read()
                        break
                    try:
                        with opener.open("http://127.0.0.1:8765", timeout=1) as response:
                            html = response.read(200000).decode("utf-8")
                            if (response.status == 200
                                    and response.headers.get("X-Frame-Options") == "DENY"
                                    and "AI Automation Studio" in html):
                                seen = True
                                break
                    except (OSError, urllib.error.HTTPError):
                        pass
                    time.sleep(0.2)
                self.assertTrue(seen, "Disposable local HTTP app did not respond: " + output[:2000])
            finally:
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.communicate(timeout=7)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate(timeout=7)


if __name__ == "__main__":
    unittest.main()
