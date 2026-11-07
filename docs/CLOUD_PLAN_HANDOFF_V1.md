# Stage #113 · Cloud AI proposal → owner-reviewed local tool plan

## Functional outcome, without VPS or Ollama

Stage #112 enabled a one-shot, paid OpenAI text answer but not a task with separately reviewed tool execution. Stage #113 connects that **already-bounded paid answer and receipt ledger** to the **existing durable LocalAgentPlanStore and real local read-only tool executor**.

In private Mission Control, new **section 14 · Cloud AI → Reviewed Local Plan** takes a goal, selected API model, per-request paid consent, and owner password. The cloud model proposes a compact JSON plan (one to three steps) through the existing tool-free OpenAI Responses adapter. The server treats all model text as untrusted, independently validates every tool and argument, and accepts only `calculate` and `utc_now` steps. Proposed steps are saved as `PREPARED`, **not executed**. The owner inspects exact steps in section 02, separately chooses **Approve**, and then **Execute next step**. This uses the existing durable local plan store and real deterministic tools, not fabricated results.

There is **no arbitrary browser, file access, shell, Gmail send, CRM mutation, n8n webhook, external MCP tool, cloud model tool-call, multi-model router, or unsupervised business action**. The feature does not accomplish goals that cannot be expressed using the permitted calculator/time tools. A model returning a disallowed plan or an empty array is rejected.

## How to start on the owner Windows PC

Update the reviewed merged repository, provision the local operator identity first if needed, and supply a genuine OpenAI API key only through the existing external private credential mechanism. Do not paste keys or passwords into chat, repository files, web URLs or logs.

    .\START_AI_STUDIO.cmd --mode cloud-only --enable-scheduled-openai

Open `http://127.0.0.1:8765/mission`, sign in, and scroll to **section 14**. Enter a short deterministic goal such as "Add 17 and 25, then show the current UTC time", an exact model ID allowed by the API account, check one paid request consent, enter the current operator password and confirm the request. The OpenAI API has a separate billing account from ChatGPT subscriptions.

When a plan is saved, Mission Control shows the exact steps. Inspect them in **section 02**, choose **Approve**, then execute manually. Completion and step outputs are persisted across Studio process restart. The agent cannot execute merely because OpenAI returned JSON.

For a disconnected response, do not click paid Propose again. Review the **existing request ID** (also listed in section 13 receipts), reenter the original *exact* goal, model and request ID, and click **Recover proposal, NO AI call**. The server checks a stored SHA-256 digest of the original paid prompt and model before trusting the receipt. Recovery is a local-only operation and makes **zero** new OpenAI calls. It cannot recover a request that never produced a completed persisted response; a remote outcome may remain uncertain.

## Built-in safety and consistency

- Every paid proposal inherits the stage #112 single-request idempotency ledger, 5 attempt/UTC-day cap, 200-total capacity, shared durable model checkpoint, zero automatic retries and model tools disabled by the provider adapter. These are request-count bounds, **not** a USD spending limit.
- Existing plan IDs for cloud proposals are derived from the first 24 hex characters of the 32-hex paid request IDs. The server binds them to the stored request and checks goal, model and exact steps before returning an existing plan. A collision or different content fails closed. Repeated recovery does not create another plan or run tools.
- A completed response may still contain invalid or unsafe plan JSON. The model request and any charge cannot be undone. Invalid instructions never gain tool execution permission; original receipt remains available for diagnosis. No automatic retry of a failed planning request.
- Only an authenticated owner session and CSRF plus the current owner password may request a proposal or recovery. Approved local steps use the existing authenticated plan controls and a shared inference safety gate.
- The local plan SQLite stores the goal, model, steps, results and audit events in plaintext. The one-shot remote ledger separately stores a prompt hash, answer and provider-reported token usage. Do not enter secrets or sensitive business/customer data unless the processing boundary is appropriate.
- Plaintext local tool execution is not evidence of provider account availability, provider charges, correct model reasoning, email/CRM delivery, or 24/7 uptime. No real OpenAI key or VPS access is exercised in CI.

## Verification and remaining work

`tests/test_cloud_plan_bridge.py` uses an actual authenticated loopback HTTP server, a strictly fake paid model result, actual SQLite stores and real calculator/UTC execution. Tests cover missing consent, owner-password/CSRF denial, plan approval boundary, disallowed model tools, request idempotency, prompt/model hash binding, no-charge recovery, plan-state persistence and a disabled-mode gate.

The dedicated CI workflow runs Python 3.11, 3.12 and 3.13 plus existing cloud-only, paid request, plan-store tests and Mission Control JS syntax. The broader regression and Chromium smoke workflows must also pass before merging.

**Evidence boundary:** `LIVE_CLOUD_PLAN_MODEL_VERIFIED=FALSE` until an owner runs one benign paid plan through a real API account, confirms actual provider usage, reviews the resulting proposal, and manually executes the real local steps on their own machine. **VPS remains intentionally out of scope**.

The next real capability gap after this stage is **approved integration actions** with read-after-write verification and exact permissions. Do not confuse a calculator/time-only plan with a universal autonomous agent.
