# Stage #127 · Explicit multi-provider on-demand AI, shared safety ledger

Status: implementation under PR review. No paid calls or owner-PC inference have been verified by this change.

## Substantive new backend capability

The existing owner-only on-demand cloud text endpoint now supports THREE provider families without silently changing provider:
- `openai_responses`: preexisting official OpenAI Responses API. It remains the default for legacy requests without a `provider` field.
- `anthropic_messages`: explicit official Anthropic Messages API, fixed HTTPS host `api.anthropic.com` and `/v1/messages`.
- `gemini_generate_content`: explicit official Google Gemini generateContent API, fixed HTTPS host `generativelanguage.googleapis.com` and exact model path.

Both new adapters are one-shot HTTPS POSTs with no redirects, proxies, tool declarations, MCP, browsing, email, filesystem writes, background mode, function calls, streaming, automatic retry or cross-provider fallback. Output is limited to 512 requested tokens, 6,000 returned text characters and 64 KiB response bytes. Only a completed single text answer with valid token counts is accepted; unexpected shapes, truncation, network timeout and interrupted responses mark the result uncertain. HTTP 400/401/402/403/404/422/429 are definite provider rejections; other failures are uncertain.

The on-demand SQLite ledger now stores exact `provider` along with the original request ID, model, prompt SHA-256, status, optional text answer, usage token counts and timestamps. Existing SQLite rows are migrated to `openai_responses` without changing the historic IDs or their result states. The exact receipt identity cannot be reused for a different provider. The preexisting OpenAI-only signed/bound plan import rejects receipts from Anthropic/Gemini, preventing accidental cross-provider provenance substitution. The entire feature still shares ONE crash-aware durable model-safety checkpoint, one per-request idempotency claim, one global five-attempts-per-UTC-day cap and 200 total retained attempts. An unresolved provider attempt quarantines subsequent model calls across all providers until a separate owner review. **Neither a request count nor a token ceiling is a hard USD spend guarantee.** The provider's own invoices remain authoritative.

## Activation (for a reviewed local checkout)

Both new provider connectors are OFF unless explicitly enabled by the owner when launching the backend. The OpenAI on-demand path remains enabled by the existing `--enable-scheduled-openai` flag and separately provisioned `AAS_OPENAI_API_KEY`. New opt-in flags:

```powershell
python app/local_server.py --enable-anthropic-on-demand
python app/local_server.py --enable-gemini-on-demand
# Or use both flags to enable both providers; no key is copied to GitHub or UI.
```

Provision `AAS_ANTHROPIC_API_KEY` or `AAS_GEMINI_API_KEY` in a private process-start secret mechanism BEFORE launching. Missing/invalid keys block startup; never paste live credentials into an issue, chat, HTTP body, source file or screenshot. To enable OpenAI simultaneously, also supply its existing flag and key. The existing Windows launcher currently exposes cloud-only OpenAI and has NOT been updated to add new UI provider selectors; this stage adds a backend API contract, and the new UI is a separate future task.

Authenticate the single owner on localhost. The existing endpoint `POST /api/read-only-ai-ask` requires owner session, CSRF, current owner password, literal `paid_api_consent: true`, a unique lowercase 32-character hex `request_id`, model and short prompt. Supply `provider` explicitly as one of the three exact names. Omitting it preserves OpenAI behavior only; there is never an automatic fallback if OpenAI is unavailable. Example schematic JSON keys (not credentials):

```json
{"request_id":"0123456789abcdef0123456789abcdef","provider":"gemini_generate_content","model":"gemini-2.5-flash","prompt":"Explain database indexes","paid_api_consent":true,"current_password":"<owner password entered privately>"}
```

This is an illustrative request contract; not a live invoice or guarantee of an activated account. Provider/model availability, pricing and quotas are independently controlled by the provider. The authenticated `GET /api/local-agent-capabilities` now returns `remote_on_demand_providers` with the configured subset, and `GET /api/read-only-ai-ask-history` includes provider names in sanitized history. Do not infer a key's provider-side validity from local startup alone.

## Proof boundaries and next work

Automated tests validate fixed endpoint/payload/headers, provider response/usage parsing, dangerous tool-like content rejection, exact owner/CSRF/paid consent, SQLite migration, cross-provider idempotency and daily cap, failure quarantine and OpenAI receipt legacy compatibility. They use injected network fixtures, real SQLite and localhost HTTP, **not** live Anthropic/Gemini/OpenAI billing. Real owner-provider acceptance remains explicitly deferred, as does VPS. There is NO adaptive model quality scoring, automatic routing, live price table, hard currency-budget enforcement, universal paid subscription pooling, multi-agent orchestration or new automation templates. Approval UX for the new providers and provider-specific model compatibility must be verified before calling this production-ready.

Reference API docs checked 2026-11-08: Anthropic [Messages](https://platform.claude.com/docs/en/api/messages/create); Google [generateContent](https://ai.google.dev/api/generate-content); existing OpenAI adapter and `docs/ON_DEMAND_CLOUD_AI_V1.md`.
