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
    if mode not in ("offline", "local-ai", "full", "cloud-only"):
        raise ValueError("invalid_quickstart_mode")
    if mode in ("offline", "cloud-only") and history:
        raise ValueError("history_requires_local_ai")
    flags = ()
    if mode in ("local-ai", "full"):
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


def _open_browser_after_ready(stop, destination):
    if destination not in ("/workspace", "/mission"):
        raise ValueError("invalid_local_destination")
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
            webbrowser.open(URL.rstrip("/") + destination)
            return


def _serve(flags):
    # In-process server avoids a second long-lived Python interpreter on 8 GB hosts.
    from local_server import main as server_main
    return server_main(list(flags))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Launch the private local AI Automation Studio.")
    parser.add_argument("--mode", choices=("offline", "local-ai", "full", "cloud-only"), default="full",
                        help="offline=rules only; local-ai=Ollama; full=Ollama + opt-in source reads; cloud-only=explicitly enabled paid text provider(s), no Ollama")
    parser.add_argument("--enable-anthropic-on-demand", action="store_true",
                        help="Opt in to billed one-shot Anthropic Messages; requires AAS_ANTHROPIC_API_KEY at private process startup.")
    parser.add_argument("--enable-gemini-on-demand", action="store_true",
                        help="Opt in to billed one-shot Gemini generateContent; requires AAS_GEMINI_API_KEY at private process startup.")
    parser.add_argument("--allow-remote-model", action="append", default=None,
                        metavar="PROVIDER:MODEL",
                        help="Restrict owner-paid one-shot calls to these exact pairs (repeatable); never selects fallback.")
    parser.add_argument("--remote-max-daily-attempts", type=int, default=5,
                        metavar="1..5", help="Local one-shot attempt cap, not a monetary budget.")
    parser.add_argument("--enable-scheduled-openai", action="store_true",
                        help="Explicitly opt in to billed OpenAI Responses text; cloud-only on-demand is supported, recurring local AI needs its own flag and private API key.")
    parser.add_argument("--enable-scheduled-local-ai", action="store_true",
                        help="Owner-armed recurring local AI inference, requires local-ai or full profile.")
    parser.add_argument("--enable-google-gmail-metadata", action="store_true",
                        help="Enable one owner-clicked Google OAuth Gmail metadata-only read; requires encrypted vault grant.")
    parser.add_argument("--enable-managed-autopilot", action="store_true",
                        help="Owner-armed automatic managed webhook delivery; requires encrypted managed webhooks.")
    parser.add_argument("--enable-encrypted-managed-webhooks", action="store_true",
                        help="Use AES-256-GCM encrypted connector vault, requires pip install -r requirements-vault.txt and AAS_VAULT_MASTER_KEY.")
    parser.add_argument("--enable-managed-webhooks", action="store_true",
                        help="Owner-managed multiple n8n/webhook connections; private OS-protected credentials, no automated sends.")
    parser.add_argument("--enable-webhook", action="store_true",
                        help="Explicitly permit owner-confirmed n8n HTTPS webhook, requires AAS_WEBHOOK_URL environment.")
    parser.add_argument("--enable-encrypted-scoped-memory", action="store_true",
                        help="Encrypt project/Agent notes using separately provisioned AAS_SCOPED_MEMORY_KEY and PyCA cryptography; legacy plaintext database will not auto-migrate.")
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
        if args.mode == "cloud-only":
            if args.enable_scheduled_local_ai or not (args.enable_scheduled_openai or args.enable_anthropic_on_demand or args.enable_gemini_on_demand):
                raise ValueError("cloud-only requires at least one explicit paid provider flag and forbids --enable-scheduled-local-ai")
        if args.mode == "offline" and (args.enable_anthropic_on_demand or args.enable_gemini_on_demand):
            raise ValueError("offline mode cannot enable paid cloud providers")
        if not 1 <= args.remote_max_daily_attempts <= 5:
            raise ValueError("remote-max-daily-attempts must be 1..5")
        if (args.allow_remote_model is not None or args.remote_max_daily_attempts != 5) and not (
                args.enable_scheduled_openai or args.enable_anthropic_on_demand
                or args.enable_gemini_on_demand):
            raise ValueError("remote model restrictions require an explicitly enabled paid provider")
        for exact_model in (args.allow_remote_model or []):
            flags += ("--allow-remote-model", exact_model)
        if args.remote_max_daily_attempts != 5:
            flags += ("--remote-max-daily-attempts", str(args.remote_max_daily_attempts))
        if args.enable_anthropic_on_demand:
            flags += ("--enable-anthropic-on-demand",)
        if args.enable_gemini_on_demand:
            flags += ("--enable-gemini-on-demand",)
        if args.enable_encrypted_scoped_memory:
            if args.mode not in ("local-ai", "full"):
                raise ValueError("encrypted scoped memory requires local-ai or full")
            flags += ("--enable-encrypted-scoped-memory",)
        if args.enable_scheduled_local_ai:
            if args.mode in ("offline", "cloud-only"):
                raise ValueError("scheduled local AI requires local-ai or full mode")
            flags += ("--enable-scheduled-local-ai",)
        if args.enable_scheduled_openai:
            if args.mode != "cloud-only" and not args.enable_scheduled_local_ai:
                raise ValueError("scheduled OpenAI in local-ai/full requires --enable-scheduled-local-ai")
            flags += ("--enable-scheduled-openai",)
        if args.enable_webhook:
            flags += ("--enable-outbound-webhook",)
        if args.enable_managed_webhooks and args.enable_encrypted_managed_webhooks:
            raise ValueError("choose legacy plaintext or encrypted managed-webhooks, not both")
        if args.enable_google_gmail_metadata:
            flags += ("--enable-google-gmail-metadata",)
        if args.enable_managed_webhooks:
            flags += ("--enable-managed-webhooks",)
        if args.enable_encrypted_managed_webhooks:
            flags += ("--enable-encrypted-managed-webhooks",)
        if args.enable_managed_autopilot:
            if not args.enable_encrypted_managed_webhooks:
                raise ValueError("managed-autopilot requires encrypted managed webhooks")
            flags += ("--enable-managed-autopilot",)
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
    if args.mode in ("local-ai", "full"):
        print("Local AI needs independently installed Ollama and an installed model.")
        print("No automatic model download, compatibility test, or inference is started.")
    if args.mode == "cloud-only":
        print("Cloud-only mode: no local Ollama required; configured paid provider text calls require one-request consent, owner password and independent billing.")
    if args.enable_history:
        print("WARNING: local chat history is saved as plaintext.")
    if args.mode == "full":
        print("Public provider reads still require explicit actions/permissions in the UI.")
    if args.enable_google_gmail_metadata:
        print("Gmail metadata-only API is opt-in; each read requires owner password. No automated inbox polling or sends.")
    if args.enable_managed_webhooks:
        print("LEGACY managed webhooks: private OS files but NOT ENCRYPTED; prefer the encrypted mode for new connections.")
    if args.enable_anthropic_on_demand:
        print("WARNING: Anthropic Messages one-shot text is separately billable; provider/model permissions and API quota are not verified.")
    if args.enable_gemini_on_demand:
        print("WARNING: Gemini generateContent one-shot text can incur separate charges; no automatic fallback or retries.")
    if args.enable_scheduled_openai:
        print("WARNING: OpenAI Responses scheduled tasks can incur separate API charges, sending prompts to OpenAI; up to 3 requests/7 days per task, manual owner arm required.")
    if args.enable_scheduled_local_ai:
        print("Read-only AI scheduler active: owner-scoped local Ollama tasks, plain SQLite prompts and results.")
    if args.enable_managed_autopilot:
        print("Managed scheduled webhook delivery: explicit per-connector owner grants, off until armed.")
    if args.enable_encrypted_managed_webhooks:
        print("Encrypted managed connections: AES-256-GCM; master key must be backed up separately from ciphertext.")
        print("Encrypted mode uses a SEPARATE registry and does not import existing plaintext credentials.")

    stop = threading.Event()
    if not args.no_browser:
        threading.Thread(target=_open_browser_after_ready,
                         args=(stop, "/mission" if args.mode == "offline" else "/workspace"), daemon=True).start()
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
