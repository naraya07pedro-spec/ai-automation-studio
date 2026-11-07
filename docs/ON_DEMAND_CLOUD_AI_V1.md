# Stage #112 · On-demand OpenAI text inference without VPS or Ollama

## Why this feature matters

Stage #108 could run scheduled OpenAI text requests without Ollama, but did not expose an **immediate, interactive cloud question** in Mission Control. An owner still had to create a recurring task and wait for a due slot to use remote AI. Stage #112 adds a separate **one-shot, owner-confirmed text request** while retaining the local deterministic rule scheduler and all existing feature flags.

**No VPS is needed.** On the Windows machine holding the Studio checkout, the owner may start the existing `cloud-only` mode. Actual requests need an independently provisioned **OpenAI API key**, a supported model, provider billing, and explicit authorization. ChatGPT Plus does not include OpenAI API credit.

## Windows startup

After reviewing and updating the merged Studio checkout, provision the existing private owner identity if needed (one-time) using the documented `--init-owner` command. Provide `AAS_OPENAI_API_KEY` to the local process using a secure external secret mechanism. Do not paste keys into chat, scripts, GitHub, browser forms or task prompts.

    .\START_AI_STUDIO.cmd --mode cloud-only --enable-scheduled-openai

Open `http://127.0.0.1:8765/mission`, authenticate with the owner password, and use **section 13, Ask Cloud AI Now**. Enter a model ID enabled in the actual API account, a short non-sensitive prompt, tick separate paid-API consent, and reenter the owner password for **each** call. Review the explicit confirmation dialog. No OpenAI request occurs from startup, login or opening this panel. Each approved click may invoke **exactly one** Responses API POST.

No Ollama model installation, local inference model, email, Gmail, n8n, shell, external data fetch, MCP, tool execution, or background job is needed to ask a single text question. This is **not** a general autonomous agent and cannot take action on other applications.

## Request correctness and cost safety

- The server requires an active private owner session, CSRF, **current owner password**, exact fields, and `paid_api_consent` of literal Boolean `true`. Omission, string `"true"`, and numeric `1` are denied.
- Each attempt has a fresh 32-character lowercase hex `request_id` generated with secure browser random bytes. A durable SQLite ledger claims this ID *before* any provider call. The same ID is never sent twice, even if an HTTP response is lost or the process restarts.
- At most **5 attempted requests per UTC day** and **200 total persisted attempt IDs** are allowed in this initial local ledger. These are request-count restrictions, **not a guaranteed USD cost ceiling**. On reaching capacity, new requests are refused; the app does not silently delete receipts. Revisit capacity/retention policy before extending usage.
- The existing single, durable model-safety checkpoint and shared gate also protect scheduled inference. Two concurrent model requests are refused instead of racing. A timeout/network uncertainty latches `UNCERTAIN` and blocks later model calls pending separate owner review.
- The provider adapter is the existing fixed HTTPS OpenAI Responses endpoint, with no proxy/redirect, no model tools, `store=false`, no automatic retry, maximum 512 output tokens and bounded response size.
- Results are kept only in the owner's local SQLite ledger `on-demand-remote-ai.sqlite3`. Stored data: request ID, timestamp, selected model, SHA-256 of prompt, status, reply text if completed, and token counts. The raw prompt, API key and owner password are **not** saved in this new ledger. Local replies **are plaintext**, and the prompt still leaves the machine at inference time.

## Interruption and verification

If the browser times out or disconnects, **do not click Send again just to see whether it worked**. Use *Check last receipt* or the list of previous requests. The authenticated read-only route `GET /api/read-only-ai-ask-receipt?request_id=...` returns existing state and triggers zero model calls. A missing receipt does **not** prove a request was never sent if the durable checkpoint was interrupted before ledger insertion.

State distinctions:

- `COMPLETED`: local code observed a provider-shaped completed result and stored response+token values. Independently verify provider account usage/billing and check answer factuality.
- `FAILED_DEFINITE`: adapter received a definite provider rejection, without auto-retry.
- `OUTCOME_UNCERTAIN`: provider may have accepted a billable request or local storage failed. The model checkpoint stays quarantined until manual review. Existing scheduled AI safety-acknowledgement in section 12 requires current owner password and a separate confirmation; acknowledgment does not automatically reissue the original ID.

Failures can occur **after** the provider charged. A count-based cap and reported token usage cannot guarantee a dollar ceiling. A malicious process with full access to the same local OS account may still access plaintext state or invoke the locally provisioned provider key.

## Evidence and testing boundaries

`tests/test_on_demand_remote_ai.py` verifies true loopback HTTP authentication, CSRF, owner password and consent; exactly-one runner invocation, duplicate replay rejection, durable readback, no stored plaintext prompt, process-death state transition, uncertain provider quarantine, finite rate caps, rejection handling and disabled-mode denial. All model invocations inside CI are injected networkless fixtures, not OpenAI calls. Python 3.11/3.12/3.13 and existing Python/browser regressions are merge gates.

`LIVE_ON_DEMAND_OPENAI_VERIFIED=FALSE` until the owner personally submits one benign approved prompt from Mission Control, inspects the actual reply and independently reconciles API usage/billing in the official provider console. Tencent VPS, 24-hour uptime, model availability and genuine API charges remain **UNVERIFIED** by GitHub CI.
