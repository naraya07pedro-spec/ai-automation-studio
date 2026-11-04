# Slice 48 · Authenticated browser access to optional local AI agent

This is an **open-source, local-first AI agent prototype**, not a subscription SaaS. The real Ollama-backed agent introduced in Slice 47 can now be used from the existing workbench browser while the localhost operator is authenticated.

## Run

Provision local owner if necessary:

```powershell
python app/local_server.py --init-owner
```

Start explicitly with model integration enabled:

```powershell
python app/local_server.py --enable-local-agent
```

Install and start Ollama independently, then download a tool-capable model to your computer. Visit `http://127.0.0.1:8765`, sign in, and use **Ask your local AI agent**. Enter the name of a model already installed in Ollama. Without `--enable-local-agent`, the browser panel remains disabled and the server denies model invocations even to signed-in users.

The local model HTTP request goes to fixed `http://127.0.0.1:11434/api/chat`. No API key or cloud billing is required. The CPU/RAM and disk burden of the model depend on the selected model and machine. A browser success requires a running compatible Ollama model, which CI has NOT provided or verified.

## Security contract

- `GET /api/local-agent-capabilities` requires a logged-in operator session. No prompt, transcript or secrets are returned.
- `POST /api/local-agent-run` requires the existing session cookie, same-origin restrictions and anti-CSRF token. JSON input is exact `{"model":"installed-model","prompt":"question"}`; normal model/prompt validation is performed by the underlying bounded agent before model traffic.
- This endpoint is disabled by default; the startup flag is an **operator opt-in**. It uses one server-local semaphore: a second overlapping request is denied with 429 rather than starting another model job. This is not an OS-wide mutex across separate server processes.
- Only `calculate` and `utc_now` can run from model-selected tools; all actions affecting files, shell, GitHub, CRM and email are absent. No persistent chat memory; the UI clears prior response when the local session locks, including after password rotation.
- Existing `MAX_BODY`, host/origin, single-owner CSRF and local-only network server constraints continue to apply. Local HTTP is not suitable for public exposure; never port-forward it.
- The browser displays AI-generated answers as text, never interpreted HTML. Model-generated text can be wrong or misleading and should not be trusted as verified facts.

## Tests and honest boundaries

`tests/test_local_agent_browser_http.py` checks authentication, CSRF, disabled-by-default server, exact request fields, concurrency and revoked sessions using injected read-only model fixtures. `tests_browser/test_real_browser.py` adds a Chromium acceptance through a disposable fixture that mimics only the **response protocol** and checks result display plus logout cleanup. The fixture does not contact Ollama or any cloud.

**Still not delivered:** conversation history and long-term memory, tool permissions beyond two read-only helpers, workflow agent graph, policy-governed real application connectors, real-model compatibility testing and runtime isolation from malicious software. Those must not be described as completed.
