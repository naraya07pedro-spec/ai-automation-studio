# Three-provider reviewed cloud plans (backend + private Mission Control)

This extends the existing private, owner-approved one-shot cloud plan bridge from
OpenAI Responses only to **explicitly chosen OpenAI Responses, Anthropic Messages,
or Gemini generateContent**. It uses the same existing provider adapters and
one-shot paid-request ledger, not a new autonomous tool executor.

## What actually works in code

- Launch the existing cloud-only mode with one or more explicit provider flags,
  such as `--enable-anthropic-on-demand` or `--enable-gemini-on-demand`,
  plus a separately provisioned genuine API credential. OpenAI remains available
  with `--enable-scheduled-openai`. Do not put secrets in chat, URLs or Git.
- In private `/mission`, sections 13 and 14 show only actually enabled provider
  options; owner chooses the exact provider/model, enters the password and gives
  fresh approval for **one potentially billable** provider request.
- Section 14's cloud text response is *parsed*, not executed. Server permits
  only 1–3 `calculate` / `utc_now` steps and stores an unapproved local plan.
  Review, approve and execute each local step separately in section 02.
- `POST /api/cloud-ai-plan-create` accepts previous legacy JSON without
  `provider` (defaults to `openai_responses`) or an exact
  `provider: "anthropic_messages" | "gemini_generate_content"`.
  `POST /api/cloud-ai-plan-recover` accepts the same optional provider.
  Recovery verifies **the original provider, model, prompt SHA-256, request ID,
  and completed receipt**, and makes **no new paid call**.
- Original exact cloud model ID is preserved in the plan's existing `model`
  field. Names with hyphens are now accepted by the stored-plan validator,
  while the provider adapter still enforces its own stricter model ID regex.
  The provider is recorded in the original one-shot receipt ledger and in the
  plan creation/recovery response. For later provenance review, combine the
  plan ID (first 24 characters of request ID) with the matching private receipt.
- The original shared 1–5 attempts per UTC day / 200 total ledger caps,
  offline owner-pinned allowlist, model-safety checkpoint, no retries after
  uncertain outcomes and no automatic cross-provider fallback remain in force.
  The cap is **not** a guaranteed USD budget.

## Security and failure modes

- Provider choice is **trusted owner input**, never inferred or silently selected
  by model text. Extra request keys, invalid provider/model, missing per-request
  paid consent, absent owner password or CSRF, unavailable provider and blocked
  model allowlist all fail closed before provider dispatch.
- Models cannot grant new tools, read Gmail, send messages, invoke web/shell,
  transfer stored context, or obtain autonomous cross-Agent privileges.
- Interrupted paid requests may have incurred charges. **Do not press Propose
  again**; inspect the exact original receipt/provider usage. A missing or
  uncertain completed receipt cannot be recovered as an approved plan.
- The original one-shot ledger keeps prompt hashes, model/provider, reply and
  reported tokens. The plan store keeps goal, proposed steps and actual local
  results in plaintext SQLite, so use non-sensitive goals and owner-controlled
  storage. The plan API on its own does not include a persisted provider column;
  provider provenance must be checked against the bound paid receipt.

## Regression evidence and release gate

`tests/test_multi_provider_cloud_plan.py` covers provider-only HTTP bootstrap,
CSRF/session/password approval, two provider-specific real SQLite plan executions,
strict receipt provider and goal binding, invalid requests, owner allowlist,
model-originated disallowed tools and legacy OpenAI compatibility. The existing
`test_cloud_plan_bridge.py` and `test_multi_provider_on_demand.py` remain in
the CI matrix. Tests use synthetic provider fixtures, **never real billable API
calls**. CI must pass before merge.

This does **not** complete all product decisions D01–D35, verify real provider
billing, ensure a hard monetary cap or prove device/VPS readiness.
