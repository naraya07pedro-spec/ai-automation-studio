# Launch the usable local workbench now · 2026-11-06

## What "working now" actually means

This repo contains an **actual Python HTTP application** on `http://127.0.0.1:8765`, with owner login, SQLite-backed rule workflows, optional local Ollama calls, persistent reviewed task plans, bounded local file actions, and separately permissioned public-source reads.

This is **NOT a 24/7 publicly hosted SaaS** and does not implement every one of the 35 product-baseline decisions. It will not accept incoming external webhooks, manage unattended business automations after your PC shuts down, send follow-ups to customers, update live CRMs, or operate arbitrary tools. The existing five synthetic demo workflows are still synthetic.

## Windows: launch it

Prerequisites: Windows with **Python 3.11+** and the repository checked out. A model is **not** required to use the offline rules/workflow section. The local AI sections additionally require **Ollama**, running separately with a compatible installed model. No model is downloaded or tested automatically.

1. Download or update this repo, then open its folder.
2. **Double-click `START_AI_STUDIO.cmd`**. Do **not** run it as Administrator. It uses the `py -3` launcher if available or falls back to `python`.
3. On first run only, set an owner password of **12–128 characters** in the console. Input is hidden; the program does not print or transmit it.
4. Wait for the local web app to open automatically at **http://127.0.0.1:8765**. Sign in using your password.
5. Keep the console open while working. Close it or press **Ctrl+C** to stop the server. A stopped PC has no running local worker.

The default `full` profile enables **the existing feature panels**, NOT unconditional AI access or side effects. External provider reads remain explicitly owner-driven and permissioned. Local AI needs an independently installed, running Ollama model, which has **not been validated on the operator's actual PC**.

Alternative profiles, run from the repo root:

```powershell
# Works without Ollama: deterministic workflow workbench only
.\START_AI_STUDIO.cmd --mode offline

# Local AI and local documents, no public provider read switches
.\START_AI_STUDIO.cmd --mode local-ai

# Default: existing local agent, reviewed plans and separately consented public reads
.\START_AI_STUDIO.cmd --mode full

# OFF by default: stores private chat history as PLAINTEXT local SQLite
.\START_AI_STUDIO.cmd --mode full --enable-history
```

Windows execution policy changes, cloud accounts, paid providers, admin rights, public router port forwarding, extra Python packages, or copy-pasted secrets are **not required** to launch the local workbench.

Data lives in `~/.ai-automation-studio` in the signed-in OS account; the real owner verifier is never overwritten by the launcher. Do not point a public proxy at this HTTP port.

## Actual vs unverified

- The app is **served over real HTTP on loopback**. CI's disposable integration test boots a real localhost server with a freshly generated, synthetic owner file. This does not prove the user's own Windows installation works.
- No actual Ollama compatibility/RAM test was run on the user's PC. That acceptance remains deferred under GitHub issue #68.
- External connector permissions are still narrow, mostly public read-only. They are not the requested open-ended AI agent.
- The server runs in the **foreground, same Python process as the launcher**, reducing memory overhead and making stop/start behavior more predictable.
- If port 8765 is already used, startup **fails** rather than launching a browser for an unknown service.
- If a password has not been provisioned, the launcher invokes the original offline owner-provisioning CLI and will not open the web server unless it succeeds.

## Separate cloud/24-7 deployment gate

Netlify static hosting alone cannot run this Python + local SQLite + loopback Ollama backend. A Supabase project alone is not a drop-in host for it either. **Do not** change the loopback bind address or the server's Host/origin checks and publish it on a VPS as-is.

To become an internet-accessible, always-on application, this repo still needs an explicitly selected dedicated server/service topology, TLS, remote-capable authentication and secure cookies, durable backups, a separately deployed worker/model provider, access control, secret isolation, and end-to-end acceptance of a real business connector. The operator's Tencent VPS ownership, active state and system access have not been verified through this GitHub integration. Do not claim an online URL or successful cloud deployment until the live service responds correctly under those controls.
