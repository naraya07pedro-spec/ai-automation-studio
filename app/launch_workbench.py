"""Safe, foreground, one-command Windows/local entry to the EXISTING workbench.

Does not install AI models, start cloud services, open a LAN port, send
messages, run scheduled jobs, or grant model-selected external writes.
"""
import argparse
from pathlib import Path
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "app" / "local_server.py"
URL = "http://127.0.0.1:8765"
HOST = "127.0.0.1"
PORT = 8765

# These are server feature switches, NOT unattended task grants.
LOCAL_AGENT_FLAGS = (
    "--enable-local-agent",
    "--enable-agent-memory",
    "--enable-agent-plans",
    "--enable-local-documents",
    "--enable-multi-documents",
    "--enable-plan-csv",
    "--enable-plan-artifacts",
    "--enable-plan-actions",
)
EXTERNAL_READ_FLAGS = (
    "--enable-agent-github-read",
    "--enable-hn-story-read",
    "--enable-hn-job-discovery",
    "--enable-greenhouse-evidence",
)


def feature_flags(mode, history=False):
    """Never enable history or remote reads without the corresponding choice."""
    if mode not in ("offline", "local-ai", "full"):
        raise ValueError("invalid_quickstart_mode")
    if mode == "offline" and history:
        raise ValueError("history_requires_local_ai")
    flags = ()
    if mode != "offline":
        flags += LOCAL_AGENT_FLAGS
    if mode == "full":
        flags += EXTERNAL_READ_FLAGS
    if history:
        flags += ("--enable-agent-history",)
    return flags


def _operator_path():
    return Path.home() / ".ai-automation-studio" / "operator.json"


def _port_free():
    """Never attach to, or open a browser for, an unidentified server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, PORT))
        except OSError:
            return False
    return True


def _open_browser_after_ready(stop):
    # Only the fixed local endpoint; a response with the server's local
    # security headers is required before opening a browser.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(60):
        if stop.wait(0.25):
            return
        try:
            with opener.open(URL, timeout=0.6) as reply:
                confirmed = reply.headers.get("X-Frame-Options") == "DENY"
        except urllib.error.HTTPError as exc:
            confirmed = (exc.code in (401, 403) and
                         exc.headers.get("X-Frame-Options") == "DENY")
        except (OSError, ValueError):
            continue
        if confirmed and not stop.is_set():
            webbrowser.open(URL.rstrip("/") + "/mission")
            return


def _serve(flags):
    # In-process server avoids a second long-lived Python interpreter on 8 GB hosts.
    from local_server import main as server_main
    return server_main(list(flags))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Launch the private local AI Automation Studio.")
    parser.add_argument("--mode", choices=("offline", "local-ai", "full"), default="full",
                        help="offline=rules only; local-ai=no public-source tools; full=all available opt-in read tools")
    parser.add_argument("--enable-webhook", action="store_true",
                        help="Explicitly permit owner-confirmed n8n HTTPS webhook, requires AAS_WEBHOOK_URL environment.")
    parser.add_argument("--enable-history", action="store_true",
                        help="Explicitly persist local chat transcript in plaintext SQLite.")
    parser.add_argument("--no-browser", action="store_true",
                        help="Start without automatically opening a local browser.")
    args = parser.parse_args(argv)

    if sys.version_info < (3, 11):
        print("Python 3.11 or newer is required.")
        return 2
    try:
        flags = feature_flags(args.mode, history=args.enable_history)
        if args.enable_webhook:
            flags += ("--enable-outbound-webhook",)
    except ValueError as exc:
        print("Launch blocked: " + str(exc))
        return 2
    if not SERVER.is_file():
        print("Launch blocked: server missing. Run from a complete Git checkout.")
        return 2
    if not _port_free():
        print("Launch blocked: local TCP port 8765 is already occupied.")
        print("Stop the prior app instance before starting a second one.")
        return 2

    if not _operator_path().is_file():
        print("First launch: create a PRIVATE local owner password (12+ characters).")
        print("Nothing will be sent to the cloud.")
        try:
            setup = subprocess.call([sys.executable, str(SERVER), "--init-owner"], cwd=str(ROOT))
        except (OSError, KeyboardInterrupt):
            return 2
        if setup != 0 or not _operator_path().is_file():
            print("Owner setup not completed. Server was not launched.")
            return 2

    print("AI Automation Studio starting at " + URL)
    print("Profile: " + args.mode + " | HTTP: loopback only | webhook: " +
          ("OWNER-APPROVAL REQUIRED" if args.enable_webhook else "disabled"))
    print("This server stays on only while this console stays open. Use Ctrl+C to stop.")
    if args.mode != "offline":
        print("Local AI needs independently installed Ollama and an installed model.")
        print("No automatic model download, compatibility test, or inference is started.")
    if args.enable_history:
        print("WARNING: local chat history is saved as plaintext.")
    if args.mode == "full":
        print("Public provider reads still require explicit actions/permissions in the UI.")

    stop = threading.Event()
    if not args.no_browser:
        threading.Thread(target=_open_browser_after_ready,
                         args=(stop,), daemon=True).start()
    try:
        _serve(flags)
        return 0
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        print("Could not start local server: " + str(exc))
        return 2
    finally:
        stop.set()


if __name__ == "__main__":
    raise SystemExit(main())
