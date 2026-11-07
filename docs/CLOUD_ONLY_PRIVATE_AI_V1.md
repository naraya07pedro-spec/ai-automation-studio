# Real cloud-only scheduled AI without Ollama · Stage #108 · 7 Nov 2026

## Objective and exact difference from #107

PR #107 added optional bounded OpenAI Responses API inference to the *existing local Ollama* scheduler, which still required \`--enable-local-agent\` and \`--enable-scheduled-local-ai\`. Thus the VPS previously had to launch the local Ollama feature stack even when all chosen AI tasks were remote. A low-RAM VPS should **not have to install, download, list or launch Ollama** to schedule paid text-only cloud model calls.

This change adds an explicit \`cloud-only\` launcher/systemd deployment profile. Its entire inference capability is **owner-authenticated, owner-approved scheduled OpenAI text tasks**, with no interactive Ollama chat or other locally installed LLM requirement. The model is accessed only at an owner-armed future due slot. The deterministic local rule scheduler and private Mission Control continue to operate without a model.

**Never conflate code/CI with a real deployment.** No Tencent Cloud VM, actual OpenAI API key, provider invoice, live model response, physical reboot or 24-hour uptime was accessed to build this mode.

## What is actually enabled and disabled

| Scope | cloud-only | local-ai / full with opt-in |
| --- | --- | --- |
| Start private loopback HTTP 127.0.0.1:8765 | Yes | Yes |
| Persistent local rule scheduler | Yes | Yes |
| Scheduled OpenAI Responses text inference | Yes, only with \`--enable-scheduled-openai\` and key | Only with existing two explicit flags + key |
| Ollama process, model install, model API or model listing required | **No** | Only when separately using local Ollama features |
| Local Ollama chat, memory/plans/tools | **Disabled** | Separately opt-in per mode |
| Create or resume an Ollama task | **Denied** | Allowed only when explicitly enabled |
| Existing active Ollama tasks when switching to cloud-only | Automatically moved to **PAUSED_ERROR** with incremented control epoch; their provider is never changed | Normal local lifecycle |
| Existing OpenAI tasks when disabling remote feature | Automatically paused, not retried or switched to Ollama | Same |
| OpenAI requests before owner creates a schedule | **Zero** | Zero |
| Business sends through model tools | **Never** | Never for these scheduler tasks |

Cloud-only still imports ordinary Python app modules and starts deterministic rule scheduling, so this is **not a guarantee of zero CPU/RAM consumption**. It removes the need for an Ollama model process, not the application's Python interpreter, SQLite, or remote provider networking.

## Windows owner startup

1. Obtain an API key from your separately managed OpenAI API account. API billing is **independent from ChatGPT subscription billing** and may incur costs. Do not paste keys in chat, Git, browser input fields, task prompts, shell scripts or logs.
2. In a private and temporary shell process, provide \`AAS_OPENAI_API_KEY\` to the Studio process using your own secure secret method. Avoid hardcoding it into PowerShell history or checked-in files. Existing secure credential-handling rules still apply.
3. In the reviewed checkout, after verifying changes:

       git pull
       .\START_AI_STUDIO.cmd --mode cloud-only --enable-scheduled-openai

4. Enter the private owner password at \`http://127.0.0.1:8765/mission\`. The scheduled AI section should show **OpenAI Responses API available** and **Ollama unavailable**. The local chat pane remains disabled, by design. This mode does not launch, probe, download or install an Ollama model.
5. Choose a model ID that your **actual API account supports**, enter a short non-sensitive prompt, set \`max_runs=1\`, \`expires_days=1\`, a 1-minute cadence (Asia/Jakarta), tick the explicit paid billing consent checkbox, confirm the warning and enter the current owner password. The **server also rejects any HTTP request** that omits the literal Boolean \`paid_api_consent=true\` for a remote task, even with a valid owner session/password. Creation does not call the model.
6. On the first due slot, inspect the **actual** result in Mission Control's task history: completed response, real reported input/output token usage and unchanged external business dispatch count. Independently compare to provider usage/billing on your account before any broader scheduling. Never infer billing from tokens alone.

If the API model is not enabled, credentials are invalid, quota is reached, or transport is uncertain, the scheduler pauses and requires review rather than repeatedly attempting paid requests. A network timeout may already have incurred a charge; do not assume zero cost or manually spam retries.

## Small Linux VPS / Tencent systemd mode

First independently confirm a real running Linux instance, legitimate SSH host key, OS account and resource capacity. No SSH, Tencent console, install or paid API access is performed by the GitHub code changes.

For a **new** reviewed private Studio installation with a dedicated nonroot service account, set a private owner password and provision one root-owned API key through the existing hidden prompt (not a CLI argument):

    sudo python3 deploy/provision_private_vps_credential.py --name openai-api-key

Preview the systemd service, substituting the real owner nonroot user and absolute checkout path:

    sudo python3 deploy/private_vps_service.py --user YOUR_NONROOT_USER --checkout /absolute/checkout --mode cloud-only --enable-scheduled-openai

Expected preview: \`LoadCredential=aas-openai-api-key:/etc/ai-automation-studio/aas-openai-api-key\` and \`ExecStart=.../app/local_server.py --enable-scheduled-openai\`, **without** \`--enable-local-agent\` or \`--enable-scheduled-local-ai\`. Private port remains loopback-only. The installer stays preview-only without \`--install\`; adding \`--install\` is for an independently reviewed first-time installation only. Existing units are protected from overwrite. For an existing systemd deployment, use an operator-controlled backup, audited unit change, \`daemon-reload\` and restart procedure: this release does **not** silently modify a running service.

Never expose Studio port 8765 publicly. Access it through a private SSH local tunnel. After login, create a **single, short, one-attempt** task and verify the genuine provider-reported token usage and bill from the user-controlled provider console. Provider-side budget alerts and spending controls are recommended; a maximum of 3 requests/7 days is **not** a USD ceiling.

## Safety properties and migration

- Cloud-only launch is **not** automatic from a generic offline invocation; owner must explicitly select both \`--mode cloud-only\` and \`--enable-scheduled-openai\`. Existing \`local-ai\` and \`full\` paths retain their original local model flags. The standalone server can run \`--enable-scheduled-openai\` without Ollama.
- The AI scheduler uses the same durable \`ollama-safety.sqlite3\` checkpoint filename for both local and remote calls. That preexisting file name is legacy; its purpose in this mode is to persist **remote uncertain network/charge outcomes**, NOT to start Ollama. The shared gate is created even if interactive local agent is disabled.
- Cloud-only mode blocks implicit or explicit \`local_ollama\` task creation, refuses local resume, and automatically pauses older active local tasks. Existing task prompt, provider and history stay unchanged. It never converts existing tasks to paid cloud requests.
- The server requires \`paid_api_consent\` **exactly Boolean true** for every newly created \`openai_responses\` task. \`"true"\`, \`1\`, \`false\` and omission are rejected. Local tasks must not carry that field. Owner password and CSRF validation still apply independently.
- Existing OpenAI adapter restrictions remain: one fixed HTTPS Responses endpoint, no arbitrary redirects or environment proxies, no tools/MCP/web/shell/CRM/Gmail/n8n model actions, a maximum of 3 owner-authorized attempts per task and 7-day expiry, no automatic retry after uncertainty, 512 maximum output tokens, bounded persisted results, and provider token accounting. API key is not exposed through HTTP, logs, prompts or SQLite.
- Prompts leave the owner host whenever a real due remote slot executes; plaintext local SQLite still holds task prompts and responses. Owners must avoid personal secrets and sensitive data unless their privacy requirements explicitly permit the provider's processing.

## Evidence boundary

GitHub CI tests actually instantiate the Python private server with a stubbed network listener, separately exercise **real localhost HTTP authentication** against a disposable owner account, run the due-slot schedule with a networkless remote-runner fixture, and test the service unit rendering. It explicitly fails if the server tries to invoke or list Ollama. This **does not** test paid API availability, bill, real Tencent resources, unattended 24/7 operation, or successful external business dispatch. The exact tested head SHA, test totals and merged PR are recorded separately in \`docs/PROGRESS.md\` after CI verification.
