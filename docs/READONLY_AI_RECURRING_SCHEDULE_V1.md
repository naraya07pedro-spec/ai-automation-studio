# Local Ollama read-only recurring AI schedules · V1 · 2026-11-06

## Core capability delivered

This stage bridges the previous deterministic recurring rule scheduler with a real, **separately scheduled LOCAL model inference** engine. The owner can schedule a fixed prompt + one installed Ollama model at intervals of 1–1440 minutes, 1–168 hours, or daily, weekly, monthly in UTC / Asia/Jakarta. The actual answer and allowed read-only tool names are saved in a durable local SQLite ledger for owner review. No generic ChatGPT plugin scheduling, n8n webhook output, Gmail, arbitrary websites, shell execution, file writes, CRM mutation or Google account access is authorized.

It is an opt-in **read-only local AI task runner**, not a general-purpose autonomous agent or a cloud model provider integration. The underlying existing local_ollama_agent.run_agent uses only a calculator and UTC clock tool, a maximum of four model turns, fixed loopback Ollama HTTP endpoint with no redirects, and no external business effects.

## Explicit owner setup on Windows

1. Install Ollama and a compatible model **separately**. AI Automation Studio does not automatically install/download models. A CPU-only 8 GB Windows computer may be slow, unable to hold the chosen model in RAM, or time out; test the model manually before scheduling.
2. From the reviewed repository run:

       git pull
       .\START_AI_STUDIO.cmd --mode local-ai --enable-scheduled-local-ai

   Use mode full instead of local-ai only if you deliberately want the other already-permitted public read integrations in manual plans; scheduling still does NOT inherit or execute these integrations. An offline mode cannot enable model schedules.
3. Log in at http://127.0.0.1:8765/mission and open **12 · Scheduled Local AI**. Select a *locally installed* Ollama model and enter a short, repeatable prompt without tokens/passwords. Choose the cadence, timezone, maximum inference attempts (1–30) and permission lifetime (1–90 days). Confirm with the current Studio owner password. Creating a task **never runs inference immediately**; the local worker runs at its first due time.
4. See the worker status, past saved model outputs and actual per-run outcome in section 12. Pause/resume is owner/password/CSRF-gated with an optimistic control epoch. A pause stops future due claims; an already running model request cannot be forcibly recalled by this API. Automatic inference stops at the total attempt cap or expiration.
5. Current prompt and output data are stored in PLAINTEXT local SQLite under the current operating-system account at ~/.ai-automation-studio/readonly-ai-schedules.sqlite3. On POSIX the directory is 0700 and file 0600; Windows inherited ACLs need owner validation. Do not include private secrets or highly sensitive user data in scheduled prompts. This release does not encrypt scheduled prompts or outputs.

## Isolation, safety and failure semantics

- A separate background thread handles local AI tasks; the deterministic rule scheduler, n8n managed connector dispatch and HTTP application remain logically separate. The model gate and shared durable LocalModelSafetyState are the SAME as interactive local Ollama: a manual chat inference and a scheduled inference cannot run concurrently.
- Before model network dispatch, the shared safety ledger must atomically enter IN_FLIGHT. Each scheduled slot is claimed in SQLite before the Ollama POST and increments one of the finite attempts. The model reply is validated for mode LOCAL_MODEL, no external dispatch, and only calculate/utc_now tools. It persists only the bounded text answer + tool-name list, not arbitrary writes.
- After timeout / uncertain transport or crash during inference, no blind replay is attempted. A model uncertainty latches the existing operator safety quarantine. The task moves to PAUSED_UNCERTAIN, and its old run is tagged OUTCOME_UNCERTAIN at startup if the worker died while in flight. An owner must inspect/acknowledge the shared checkpoint and explicitly resume if appropriate.
- Definite model error, invalid tool result, or unavailable model pauses the task as PAUSED_ERROR to prevent infinite retry/battery drain. Manual resume starts from a new future due slot, not old failed input.
- The engine considers at most one due slot per worker scan, with no old-event catchup floods. Results survive a process restart. Capacity is twelve schedules and at most thirty stored outputs per task, all bounded and read-only. A task reaching its limit becomes EXHAUSTED; an expired task becomes EXPIRED.
- This does NOT guarantee continuous availability, successful model execution, web freshness, accurate answers, or external task completion. Results may be inaccurate or stale: this scheduler intentionally does not browse new websites.
- Crucially the original deterministic scheduler can still operate when Ollama is unavailable, since the AI worker runs on a separate thread.

## Private Tencent VPS option

The existing private systemd unit has an additional optional --enable-scheduled-local-ai switch. It **requires** local-ai or full mode, which enables the existing local Ollama server-side code; the VPS must separately have a genuinely reachable local Ollama daemon and a compatible installed model. Do not assume the Tencent free tier has enough RAM/CPU to host that model. Keep the Studio port bound to 127.0.0.1 and use its SSH tunnel. No app instance is deployed or started by this PR.

The default offline private VPS unit remains deterministic-only. The project still has **no verified Tencent live instance uptime, actual owner n8n receipt, Google consent or downloaded Ollama model**.

## Evidence

Sources: app/local_readonly_agent_scheduler.py, app/local_ollama_agent.py, app/local_model_safety.py, app/local_server.py, app/launch_workbench.py, deploy/private_vps_service.py, web/mission.html, tests/test_readonly_agent_scheduler.py, tests/test_readonly_agent_scheduler_http.py, .github/workflows/readonly-ai-scheduler.yml.

Focused CI uses disposable SQLite and a deterministic injected runner. It verifies the exact real scheduling/state logic, but **does not run a real model**. Real acceptance must show installed-model readiness, one task due at an actual clock slot, a saved non-fixture Ollama response, and safe restart handling on the owner's machine/VPS.
