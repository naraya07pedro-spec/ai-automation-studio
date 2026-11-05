# Slice 64 · Crash-safe local Ollama inference checkpoint

**Scope:** prevent restarting the local workbench from silently clearing a safety hold after an Ollama timeout or abrupt application termination mid-request. This is local request-state durability, not proof of Windows hardware compatibility.

## What persists

When `--enable-local-agent` is enabled, the server stores a single SQLite safety state at:

`~/.ai-automation-studio/ollama-safety.sqlite3`

The entire application table is two columns: `id`, `phase`. There are no prompts, answers, model names, file paths, document content, tokens, or personal identifiers inside this table. The database uses SQLite `synchronous=FULL` and `journal_mode=DELETE` to commit its checkpoint **before dispatching** a model-backed HTTP operation. Unix file permission checks reject unsafe paths. Windows file protection relies on host account/file-system permissions; this is not a substitute for OS ACL validation.

## Transition contract

| State | Meaning | Allowed next transition |
|---|---|---|
| `IDLE` | No unfinished workbench model request checkpoint | Model request commits `IN_FLIGHT` before running |
| `IN_FLIGHT` | A model operation was admitted; its completion is not yet recorded | Normal completion commits `IDLE`; observed `local_model_timeout` commits `UNCERTAIN` |
| `UNCERTAIN` | Previous request may still be running or application restarted mid-inference | **Only** owner inspection and manual acknowledgement commits `IDLE` |

On server construction, a pre-existing `IN_FLIGHT` checkpoint is durably changed to `UNCERTAIN`. Restarting repeatedly cannot clear it. `UNCERTAIN` stays latched even if an old request later returns a response; a stale completion does not prove the user has inspected the backend.

All model-backed app endpoints continue to share a single active-request gate. After an unresolved timeout/crash, new model-backed requests return HTTP 423 `local_ollama_timeout_unresolved` without dispatch. Authenticated `GET /api/local-agent-timeout-status` includes `checkpoint_state` (one of the three known values), `timeout_unresolved` and `backend_stopped_verified: false`. No model invocation occurs during status checks. While another model operation is active, manual acknowledgement still receives 429 `local_agent_busy`.

Recovery uses the **existing** explicit operator process: inspect local Ollama, then click `I inspected Ollama; re-enable inference`, confirm, and send CSRF-protected `POST /api/local-agent-timeout-acknowledge` with exactly `{"acknowledge":"OLLAMA_INSPECTED"}`. That acknowledgement is **not** a backend cancellation or completion check; it simply re-arms the workbench. Normal non-inference administration and safe preflight do not dispatch a model.

## Failure behavior and limits

- On invalid/unreadable/corrupt safety SQLite storage, server startup or model dispatch **fails closed**. It must not create a fresh safe checkpoint over the bad file.
- State transitions use bounded, serialized SQLite writes. Concurrent calls from separate server processes are not an intended operating mode; a second instance observing an `IN_FLIGHT` checkpoint conservatively treats it as `UNCERTAIN`.
- If the user manually deletes/replaces the entire safety SQLite file, this limited local safeguard cannot retain the old state. It is not tamper-proof against the OS user.
- The checkpoint tracks the workbench's model-backed requests, not arbitrary third-party Ollama clients or OS processes. It cannot identify whether the inference remained active after socket failure. Unexpected network errors other than the explicitly recognized `local_model_timeout` still have diagnostic uncertainty; future work may expand the classification conservatively based on evidence.
- Crashing during a non-model administrative operation is not treated as model uncertainty; `IN_FLIGHT` is persisted only for model-backed routes.
- No model is downloaded, started, stopped or automatically retried by this feature. No cloud fallback or external writes.
- Python/HTTP/Chromium tests use controlled fixtures and an actual on-disk SQLite database, **not a live Ollama model**. A local Windows device acceptance still requires [Issue #68](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68).

**Manual confidence check (host operator):** Start with a correctly provisioned local owner and `--enable-local-agent --enable-multi-documents`. A real request timeout or interrupted inference should leave the durable status in `UNCERTAIN`; a restart should leave it blocked. Do **not** deliberately crash the device or stress its RAM merely to demonstrate this safeguard. Existing controlled fixture tests cover that path without endangering real files.
