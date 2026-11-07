# Bounded remote AI scheduling · OpenAI Responses V1 · 7 Nov 2026

## Why this is a core capability

PR #103 added genuine bounded local Ollama recurring inference, but small VPS instances may lack CPU/RAM for an installed local model. PR #104–#106 delivered on-host acceptance and reboot provenance checks. This stage adds an **optional real remote text-model inference path** inside the same owner-authorized scheduled AI engine, without giving the model tools or arbitrary external actions. Remote provider integration does not magically deploy the Tencent instance.

**Actual API:** one fixed `https://api.openai.com/v1/responses` POST using the official OpenAI Responses schema. Input is exactly one owner-approved prompt; `tools=[]`, `tool_choice=none`, `store=false`, `background=false`, `max_output_tokens=512`, no redirects, no HTTP proxy, TLS verified, no retries. The result must be a completed assistant text response, with bounded text and input/output token counts. **No web search/MCP/shell/email/n8n/CRM tools are granted**, and no new business dispatch occurs. `store=false` is a provider request setting, **not** a guarantee about all vendor data retention or privacy policies.

## Explicit opt-in and pricing

All default/legacy startup paths remain LOCAL-only; no API request is made during startup, credential provisioning, schedule creation, CI or dry-run. Enabling requires (1) choosing the new `--enable-scheduled-openai` flag in addition to `--enable-scheduled-local-ai`, (2) separately provisioning a real OpenAI API key, (3) choosing provider `OpenAI Responses API` in Mission Control section 12, (4) an explicit paid-use acknowledgement, and (5) current owner password + CSRF. No provider-enabled option appears without startup opt-in.

OpenAI API billing is **separate from ChatGPT Plus/Pro**. A local cap limits a remote schedule to **1–3 total invocation attempts and 1–7 days' permission**, with a maximum 512 output tokens per attempt. This is **not a dollar limit**: token input, pricing tier, provider-side charges, taxes, other provider callers, and provider billing/quota settings are external. Configure provider-native monthly budget/limit alerts and spending controls. The model must exist and be authorized by the user's API account; the app does not download any model or guarantee a particular model ID is provisioned.

Cloud prompts leave the owner's machine and are sent to OpenAI; do not schedule sensitive personal data, credentials or proprietary material without a separate privacy/legal review. Saved prompts/answers and token usage live in **plaintext local SQLite** protected only by the local OS account; there is no KMS-at-rest encryption for scheduled results. Never embed a key in a prompt or pass it in the model field.

## Windows manual setup

Provision your own API key **outside ChatGPT and this chat** using an owner-controlled secure credential mechanism. For a one-session Windows PowerShell test, set process-scoped `AAS_OPENAI_API_KEY` privately and clear it afterward; process environment variables have exposure risks, so avoid storing a permanent key in scripts, GitHub files, public terminal history or chat.

From the reviewed checkout:

    git pull
    .\START_AI_STUDIO.cmd --mode local-ai --enable-scheduled-local-ai --enable-scheduled-openai

Open `http://127.0.0.1:8765/mission`, log in, go to **12 · Scheduled AI**, select OpenAI Responses API, choose one model **already enabled in your API account**, enter a short prompt, at most three attempts and seven days, explicitly acknowledge paid use and enter your fresh owner password. Schedule creation itself sends **zero** API requests; one remote request is attempted at the next due slot. The result and provider token usage are shown in the task history. The remote provider sends no n8n or email messages.

Do not supply the API key to Mission Control, the browser's model input, a schedule, GitHub, or this chat. The operator password is a separate credential.

## Linux/Tencent private service setup

First, independently verify the Tencent instance's real existence, RAM, CPU, OS, public SSH access and VM identity. The repo does not connect to Tencent or create/bill cloud infrastructure. For an **initial installation only**, use the existing nonroot app user, existing private localhost-only service and root-owned LoadCredential conventions.

Provision the API key through the new hidden interactive prompt on the server (not a plaintext CLI argument):

    sudo python3 deploy/provision_private_vps_credential.py --name openai-api-key

This writes root-only `/etc/ai-automation-studio/aas-openai-api-key` (owner root, 0600), with fixed-name systemd `LoadCredential`. The application reads the allowlisted runtime credential via `AAS_OPENAI_API_KEY` name; the key never enters the unit's command line or owner-readable SQLite.

Preview a **new** unit first (substitute the actual dedicated nonroot user and checkout):

    sudo python3 deploy/private_vps_service.py --user YOUR_NONROOT_USER --checkout /absolute/checkout --mode local-ai --enable-scheduled-local-ai --enable-scheduled-openai

Only for a verified first-time installation, explicitly add `--install`. **Do not run the installer against an existing service expecting an upgrade**: it intentionally refuses to overwrite existing units. Review a controlled admin maintenance change, backup and daemon-reload/restart separately if a systemd unit already exists. No Tencent VM was accessed while writing/testing this feature. Keep Studio bound to 127.0.0.1:8765 via SSH tunnel; do not expose the UI to the public Internet.

## Recovery, security and migration

- Existing task databases migrate in place by adding `provider` with legacy default `local_ollama` and optional `usage_json`; old rows remain attributed to local Ollama and are not silently given paid permission.
- Disabling the remote provider at next launch pauses all ACTIVE remote tasks as `PAUSED_ERROR`, with a new control epoch. A remote task cannot be resumed without an enabled paid provider and explicit owner reauthorization.
- The app records at most one remote Responses request per due task slot, uses the existing shared model gate, durable pre-send SQLite claim and expiration/quota, and never automatically retries. Definite HTTP 4xx rejections pause; ambiguous HTTP/network/timeout/invalid paid responses result in `PAUSED_UNCERTAIN` plus existing model-safety quarantine. Owner must inspect/acknowledge, then explicitly resume; uncertain usage/billing is not assumed to be zero.
- A success records only bounded output text and provider-supplied input/output token counts, not estimated dollars or exact invoice amounts. No raw API token, response headers or provider error body is persisted/logged. `external_dispatch_count=0` means zero **business tool dispatches**, not that no paid HTTP call occurred.
- Remote API is **not** a locally hosted agent tool environment and does not have tool calling, web grounding, MCP, integrations or multi-provider failover. Those capabilities need separate owner-scoped permission and safety design. This first provider integration deliberately does not silently substitute a different paid provider after quota, outage, or model failure.
- Default API budget alerts, account model access, regional policy, real billing, rate limits, key validity and actual remote inference remain owner-side acceptance gaps.

## Evidence

Sources: `app/openai_readonly_responses.py`, `app/local_readonly_agent_scheduler.py`, `app/local_server.py`, `app/local_service_credentials.py`, `app/launch_workbench.py`, `deploy/private_vps_service.py`, `deploy/provision_private_vps_credential.py`, `web/mission.html`, `tests/test_bounded_remote_openai_scheduler.py`, and `.github/workflows/bounded-remote-model-schedules.yml`.

GitHub CI uses **injected HTTP transports and disposable SQLite**. A passing CI build means only that the bounded design behaves under simulation: it is **not a live OpenAI API charge, a real model response, actual Tencent deployment, signed business completion, or 24/7 verification**. Record live acceptance only after an owner-run authorized call with observed OpenAI usage and local task history.
