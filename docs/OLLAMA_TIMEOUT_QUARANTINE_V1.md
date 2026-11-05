# Slice 63 · Ollama timeout quarantine and explicit operator recovery

AI Automation Studio is local-first and single-owner. **An Ollama request timeout is only a client-side observation.** It does not prove the Ollama server has stopped computing. Previously, the shared one-in-flight request gate was released after a timeout, allowing another user click to dispatch a second model request even if the first was still consuming RAM/CPU.

## Changed behavior

- An observed `local_model_timeout` from local model chat, model probe, single-/multi-document Q&A, device acceptance, plan creation, or an Ollama-powered approved task-plan step causes a server-process **quarantine latch**.
- Subsequent model-inference requests receive **HTTP 423** and `local_ollama_timeout_unresolved`, with **no dispatch**. Normal concurrent in-flight requests still receive **HTTP 429** `local_agent_busy`.
- Authenticated, query-free `GET /api/local-agent-timeout-status` returns only the latch state and a reminder that backend completion is **not verified**. This GET invokes no model.
- Non-inference administrative operations such as clearing local chat history or approving/cancelling an existing plan remain permitted, subject to their existing authentication, CSRF, approval and storage rules. An approved task-plan **execution** is not permitted while quarantined.
- The browser provides **Check timeout safety state (no inference)** and an explicit **I inspected Ollama; re-enable inference** control. The latter asks for confirmation, then sends owner/CSRF-protected `POST /api/local-agent-timeout-acknowledge` with precisely `{"acknowledge":"OLLAMA_INSPECTED"}`. The API denies malformed, unauthenticated or cross-origin acknowledgements. It refuses acknowledgement while another gated operation is in flight.
- Successful acknowledgement simply clears the in-memory latch and returns `backend_stopped_verified: false`, `model_calls: 0`. It **does not kill, cancel, inspect or query** Ollama, and is not hardware acceptance. No inference is automatically retried or restarted.

## Recovery sequence on a Windows PC

1. If a request times out, stop submitting model calls. The workbench should show the blocked state, not silently retry.
2. Inspect the Ollama desktop/service and whether the previous inference is still consuming resources. Do not infer that a socket timeout terminated the model.
3. Decide whether it is safe to start a new request. If uncertain, avoid re-enabling inference until the backend state is understood. Reboot/restart is not a guaranteed cancellation mechanism.
4. Only after operator inspection, confirm **I inspected Ollama; re-enable inference**. This is an acknowledgement, not an automatic recovery or an assurance that Ollama is idle.
5. Run **one** explicit model acceptance request if appropriate and inspect source verification, observed latency and system-wide available RAM. Actual device evidence remains tracked in [Issue #68](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68).

For API clients with the same existing owner session and CSRF:
```http
GET /api/local-agent-timeout-status
```
The state response is intentionally minimal and does not expose process lists, filesystem paths or private files. The acknowledgment requires the normal authenticated JSON POST flow and a deliberate owner decision.

## Limits and trust boundaries

**Slice 64 update (2026-11-05):** The earlier process-only limitation below has been superseded by an on-disk SQLite checkpoint. A timeout is persisted as `UNCERTAIN`; when restarted, any prior `IN_FLIGHT` request is also converted to `UNCERTAIN`, and new workbench model calls stay blocked until explicit owner acknowledgment. See [durable crash recovery](OLLAMA_CRASH_RECOVERY_V1.md). This is not a distributed lock or proof of backend termination. The latch does not prevent unrelated programs from calling Ollama directly. It is a protective throttle for this workbench, not process termination, OS memory accounting, inference cancellation or system-wide workload coordination.

Python/HTTP/Chromium CI can verify the state machine using injected timeout fixtures and real browser clicks, but cannot verify actual in-flight Ollama work on the owner's PC. No Ollama installation, model download, cloud API, filesystem scanning or external write is introduced.
