# Owner-pinned paid one-shot model policy v1 · Stage 139

This adds a real **local risk reduction policy**, NOT a provider-side hard-dollar billing cap. Price tables, current model availability, account quota, upstream retries, and actual invoices are not independently verified by local CI.

## Behavior

- Existing paid one-shot API keeps explicit current-owner password, CSRF, per-request `paid_api_consent=true`, immutable request IDs, provider selection, no silent fallback, shared model-safety quarantine and durable receipts.
- Owner may opt in to an exact **provider:model** allowlist and narrow the existing global five-attempt maximum to **1, 2, 3, 4 or 5 attempted requests per UTC day**. The cap is shared by all configured one-shot providers and includes attempted/rejected/uncertain provider operations recorded in the ledger. A disallowed model is refused **before** claim, outbound dispatch, or quota consumption.
- Both allowlist and reduced cap are persisted in the same owner-private `on-demand-remote-ai.sqlite3` ledger. Restart without flags reuses the previously pinned policy; a mismatched startup setting fails closed rather than silently relaxing the restrictions.
- A policy restricted to Anthropic while only Gemini is enabled fails closed. No provider/model choices are automatically invented or switched.
- The browser only offers already-enabled providers and shows any owner-approved models and actual one-shot attempt ceiling. Backend remains authoritative.
- This protects **direct one-shot paid text only**. The separately owner-armed OpenAI scheduled-task subsystem has its own controls and is **not** covered by this new model allowlist.

## Initial owner configuration

The following IDs are only **illustrative syntax**, not a claim that a model remains available, affordable or eligible under any account as of 2026-11-08. Verify exact current provider IDs, region, billing and quota before use.

```powershell
.\START_AI_STUDIO.cmd --mode cloud-only --enable-gemini-on-demand --allow-remote-model "gemini_generate_content:gemini-2.5-flash" --remote-max-daily-attempts 1
```

Other supported prefixes: `openai_responses:`, `anthropic_messages:`, and `gemini_generate_content:`. Repeat `--allow-remote-model PROVIDER:MODEL` to approve multiple exact pairs. The backend rejects malformed, repeated, invalid-provider and unsupported-format entries, and never automatically falls back to an alternate model.

If a policy already exists, the default launcher preserves it even if these flags are omitted. To **change** the existing policy later, STOP the local server and use an independent owner-authenticated offline CLI:

```powershell
python app/owner_remote_policy.py --replace --allow-remote-model "gemini_generate_content:gemini-2.5-flash" --daily-attempts 1
```

The CLI requires the owner's current password, exact typed `REPLACE PAID MODEL POLICY` confirmation, an unused loopback port 8765 (server stopped) and a private SQLite database. It checks for unresolved `IN_FLIGHT` or `OUTCOME_UNCERTAIN` receipts and **refuses** changing policy while their provider billing still needs review. It preserves all prior request receipts, prompt hashes and safety ledgers, logs a redacted audit event and updates policy revision transactionally.

An explicit `--allow-any-models` replaces the allowlist with an unrestricted model choice after owner confirmation. That is less safe and is not the default. If no allowlist was ever pinned, legacy model selection behavior remains unchanged, with the existing owner consent and five-attempts/day cap.

## Not a monetary guarantee

- Neither these bounds nor the `max_output_tokens=512` text request setting ensures a fixed USD/IDR spend. Token pricing and upstream billable usage vary by model and provider; independently verify provider-side budgets, real invoices and hard account limits if offered.
- Request caps are **local process ledger limits**; parallel deployments, other API clients, scheduled AI outside the on-demand ledger and direct provider-console calls are not counted by this ledger.
- Local SQLite owner permissions and audit trails are not tamper-proof against a compromised same-OS user. VPS/Windows, actual API billing and provider availability require owner-owned credentials and live acceptance; no such calls were made in CI.
- The app does not retain provider API keys in policy JSON or expose them through this UI.

## Tests

Focused Python tests validate exact model refusal preflight, cross-provider shared count cap, persisted policy after restart, mismatched-policy startup denial, offline auth/preflight replacement, uncertain receipt denial, and missing/invalid argument rejection. Existing integration, browser and full Python 3.11/3.12/3.13 suites must pass before merge.
